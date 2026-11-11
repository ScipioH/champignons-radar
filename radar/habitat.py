"""Score d'habitat par famille (phase 2b) et score final.

habitat(cellule) = forêt × géologie × altitude × exposition, chaque terme dans [0, 1] (exposition : 1 ± amplitude),
où forêt et géologie sont les moyennes des poids de `config/species.yaml` pondérées par la part de chaque classe
dans la cellule. Approximation assumée : forêt et géologie sont combinées au niveau de la cellule (la répartition
conjointe pixel par pixel n'est pas conservée).
Hors couverture (départements ≠ 30/34) : habitat = NaN = « inconnu », jamais 0.

score final = 100 × météo^a × habitat^b × saison × accessibilité (accessibilité = 1 jusqu'à la phase 2c).
La météo (points à 5 km) est ramenée aux cellules par pondération inverse des distances : c'est un LISSAGE,
pas une information plus fine (cf. PLAN.md §7bis).
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import yaml
from pyproj import Transformer

from radar.config import ROOT, resolve
from radar.terrain import L93

TERRAIN_KEYS = ("x", "y", "lat", "lon", "alt", "slope", "north", "east", "forest_pct", "geol_pct", "coverage_pct")


# --------------------------------------------------------------------------- config

def load_species(path: Path | None = None) -> dict:
    with open(path or ROOT / "config" / "species.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def forest_weights(fam: dict, scfg: dict, classes: list[str]) -> np.ndarray:
    """Vecteur de poids aligné sur les classes forestières ; erreur si une classe n'est pas couverte."""
    w = np.zeros(len(classes))
    missing = []
    for i, c in enumerate(classes):
        if c in scfg["forest_zero"]:
            w[i] = 0.0
        elif c == "inconnu":
            w[i] = scfg["forest_unknown_prior"]
        elif c in scfg["open_of"]:
            w[i] = fam["forest"][scfg["open_of"][c]] * fam["open_factor"]
        elif c in fam["forest"]:
            w[i] = fam["forest"][c]
        else:
            missing.append(c)
    if missing:
        raise ValueError(f"classes forestières sans poids : {missing}")
    if not ((w >= 0) & (w <= 1)).all():
        raise ValueError("poids forestiers hors [0, 1]")
    return w


def geology_weights(fam: dict, scfg: dict, classes: list[str]) -> np.ndarray:
    w = np.zeros(len(classes))
    missing = []
    for i, c in enumerate(classes):
        if c in scfg["geology_zero"]:
            w[i] = 0.0
        elif c == "autre":
            w[i] = scfg["geology_unknown_prior"]
        elif c in fam["geology"]:
            w[i] = fam["geology"][c]
        else:
            missing.append(c)
    if missing:
        raise ValueError(f"classes géologiques sans poids : {missing}")
    return w


# --------------------------------------------------------------------------- facteurs (purs)

def weighted_share(pct: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Moyenne des poids pondérée par les pourcentages ; NaN si la cellule n'a aucun pourcentage."""
    pct = pct.astype(np.float64)
    tot = pct.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(tot > 0, (pct @ w) / tot, np.nan)


def altitude_factor(alt: np.ndarray, a: dict) -> np.ndarray:
    """1 sur [ideal_from, ideal_to], rampe linéaire sur ramp_m jusqu'à `edge` au-delà ; altitude inconnue -> 1."""
    alt = np.asarray(alt, dtype=np.float64)
    lo = np.clip((alt - (a["ideal_from"] - a["ramp_m"])) / a["ramp_m"], 0, 1)
    hi = np.clip(((a["ideal_to"] + a["ramp_m"]) - alt) / a["ramp_m"], 0, 1)
    f = a["edge"] + (1 - a["edge"]) * np.minimum(lo, hi)
    return np.where(np.isnan(alt), 1.0, f)


def exposure_factor(north: np.ndarray, east: np.ndarray, month: int, e: dict) -> np.ndarray:
    """1 ± amplitude selon l'orientation vers `direction_deg` (versant frais), seulement les mois indiqués."""
    if month not in e["months"]:
        return np.ones_like(np.asarray(north, dtype=np.float64))
    d = math.radians(e["direction_deg"])
    proj = (np.asarray(north) * math.cos(d) + np.asarray(east) * math.sin(d)) / e["ref_sin_slope"]
    return np.where(np.isnan(proj), 1.0, 1.0 + e["amplitude"] * np.clip(proj, -1, 1))


def season_factor(month: int, months: list[int], s: dict) -> float:
    if month in months:
        return s["in_season"]
    adj = {(m % 12) + 1 for m in months} | {((m - 2) % 12) + 1 for m in months}
    return s["shoulder"] if month in adj else s["off_season"]


def habitat_family(t: dict, fam: dict, scfg: dict, fclasses: list[str], gclasses: list[str], month: int) -> np.ndarray:
    fw = forest_weights(fam, scfg, fclasses)
    gw = geology_weights(fam, scfg, gclasses)
    h = (weighted_share(t["forest_pct"], fw) * weighted_share(t["geol_pct"], gw)
         * altitude_factor(t["alt"], fam["altitude"]) * exposure_factor(t["north"], t["east"], month, scfg["exposure"]))
    h = np.clip(h, 0.0, 1.0)
    return np.where(np.asarray(t["coverage_pct"]) == 0, np.nan, h)


def compute_habitat(t: dict, scfg: dict, fclasses: list[str], gclasses: list[str], month: int) -> dict[str, np.ndarray]:
    return {k: habitat_family(t, f, scfg, fclasses, gclasses, month) for k, f in scfg["families"].items()}


# --------------------------------------------------------------------------- météo -> cellules, score final

def idw_weights(px: np.ndarray, py: np.ndarray, cx: np.ndarray, cy: np.ndarray,
                k: int = 4, power: float = 2.0, chunk: int = 20000) -> tuple[np.ndarray, np.ndarray]:
    """Voisins (k plus proches) et poids normalisés de pondération inverse des distances. Calculés une fois,
    réutilisés pour chaque jour du curseur."""
    k = min(k, len(px))
    nn = np.empty((len(cx), k), dtype=np.int64)
    w = np.empty((len(cx), k))
    for s in range(0, len(cx), chunk):
        d = np.hypot(cx[s:s + chunk, None] - px[None, :], cy[s:s + chunk, None] - py[None, :])
        n_ = np.argpartition(d, k - 1, axis=1)[:, :k]
        w_ = 1.0 / np.maximum(np.take_along_axis(d, n_, axis=1), 1.0) ** power
        nn[s:s + chunk], w[s:s + chunk] = n_, w_ / w_.sum(axis=1, keepdims=True)
    return nn, w


def idw(px: np.ndarray, py: np.ndarray, pv: np.ndarray, cx: np.ndarray, cy: np.ndarray, k: int = 4) -> np.ndarray:
    """Pondération inverse des distances sur les k points les plus proches (lissage)."""
    nn, w = idw_weights(px, py, cx, cy, k)
    return (w * pv[nn]).sum(axis=1)


class MeteoInterp:
    """Ramène des valeurs portées par les points météo (5 km) sur les cellules 250 m."""

    def __init__(self, points: list[dict], cx: np.ndarray, cy: np.ndarray):
        self.points = [p for p in points if p.get("score") is not None]
        if not self.points:
            raise ValueError("aucun point météo exploitable")
        x, y = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(
            [p["lon"] for p in self.points], [p["lat"] for p in self.points])
        self.nn, self.w = idw_weights(np.asarray(x), np.asarray(y), cx, cy)

    def __call__(self, values: list[float | None]) -> np.ndarray:
        """values (0-100, une par point retenu) -> score 0-1 par cellule ; point sans valeur ignoré."""
        v = np.array([np.nan if x is None else x for x in values], dtype=np.float64) / 100.0
        vv = v[self.nn]
        w = np.where(np.isnan(vv), 0.0, self.w)
        with np.errstate(invalid="ignore", divide="ignore"):
            return (w * np.nan_to_num(vv)).sum(axis=1) / w.sum(axis=1)

    def day(self, i: int | None = None) -> np.ndarray:
        """Jour i du curseur (index dans 'timeline') ; None = score du jour J."""
        return self([p["score"] if i is None else p["timeline"][i] for p in self.points])


def meteo_on_cells(points: list[dict], cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
    """Score météo 0-1 par cellule à partir des points V0 (score 0-100)."""
    return MeteoInterp(points, cx, cy).day()


def final_score(meteo: np.ndarray, habitat: np.ndarray, season: float, scfg_score: dict,
                access: np.ndarray | float = 1.0) -> np.ndarray:
    """access : facteur 0-1, ou < 0 = hors périmètre de trajet -> score -1 (retiré de la carte)."""
    a, b = scfg_score["exponent_meteo"], scfg_score["exponent_habitat"]
    acc = np.asarray(access, dtype=np.float64)
    s = 100.0 * np.clip(meteo, 0, 1) ** a * habitat ** b * season * np.clip(acc, 0, 1)  # NaN (inconnu) propagé
    return np.where(acc < 0, -1.0, s)


# --------------------------------------------------------------------------- critères de fin 2b

def _zone(t: dict, z: dict) -> np.ndarray:
    x, y = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(z["lon"], z["lat"])
    return np.hypot(t["x"] - x, t["y"] - y) <= z["radius_m"]


def check_criteria(t: dict, hab: dict[str, np.ndarray], scfg: dict, fclasses: list[str], gclasses: list[str]) -> list[dict]:
    c = scfg["criteria"]
    out = []
    # (a) meilleures cellules cèpes : socle acide + châtaignier/chênes/hêtre majoritaires
    h = hab["cepes"]
    known = ~np.isnan(h)
    thr = np.quantile(h[known], 1 - c["top_fraction"])
    top = known & (h >= thr)
    fp = t["forest_pct"][top].astype(float)
    gp = t["geol_pct"][top].astype(float)
    good = [fclasses.index(k) for k in c["good_forest_cepes"]]
    acid = gp[:, gclasses.index("acide")].sum() / max(gp.sum(), 1)
    goodf = fp[:, good].sum() / max(fp.sum(), 1)
    out.append({"id": "a", "ok": bool(acid > 0.5 and goodf > 0.5),
                "detail": f"top {c['top_fraction']:.0%} cèpes ({int(top.sum())} cellules, habitat ≥ {thr:.2f}) : "
                          f"sol acide {acid:.0%}, châtaignier/chênes/hêtre {goodf:.0%} (attendu > 50 % chacun)"})
    # (b) Gardiole : lactaires > cèpes
    z = _zone(t, c["zones"]["gardiole"]) & known
    lac, cep = np.nanmean(hab["lactaires"][z]), np.nanmean(hab["cepes"][z])
    out.append({"id": "b", "ok": bool(lac > cep),
                "detail": f"Gardiole ({int(z.sum())} cellules) : lactaires {lac:.2f} vs cèpes {cep:.2f}"})
    # (c) Montpellier centre : habitat quasi nul
    zm = c["zones"]["montpellier"]
    z = _zone(t, zm) & known
    means = {k: float(np.nanmean(v[z])) for k, v in hab.items()}
    out.append({"id": "c", "ok": bool(z.any() and max(means.values()) <= zm["max_mean"]),
                "detail": f"Montpellier centre ({int(z.sum())} cellules) : "
                          + ", ".join(f"{k} {v:.3f}" for k, v in means.items()) + f" (attendu ≤ {zm['max_mean']})"})
    # (d) hors couverture = inconnu, pas 0
    unc = np.asarray(t["coverage_pct"]) == 0
    ok = all(np.isnan(v[unc]).all() and not np.isnan(v[~unc]).any() for v in hab.values())
    out.append({"id": "d", "ok": bool(ok), "detail": f"{int(unc.sum())} cellules hors couverture, toutes « inconnu » (NaN) ;"
                                                      " aucune cellule couverte sans valeur"})
    return out


# --------------------------------------------------------------------------- exécution

def load_terrain(dirpath: Path | None = None) -> tuple[dict, dict]:
    d = dirpath or resolve("data/processed")
    npz, meta = d / "terrain_250m.npz", d / "terrain_meta.json"
    if not npz.exists():
        raise FileNotFoundError("terrain absent : lancer `python -m radar terrain`")
    with np.load(npz) as z:
        t = {k: z[k] for k in TERRAIN_KEYS}
    return t, json.loads(meta.read_text(encoding="utf-8"))


def describe(t: dict, hab: dict, scfg: dict, fclasses: list[str], month: int) -> list[str]:
    lines = []
    for k, h in hab.items():
        known = ~np.isnan(h)
        v = h[known]
        fam = scfg["families"][k]
        s = season_factor(month, fam["season_months"], scfg["season"])
        lines.append(f"- {fam['label']} : saison ×{s} ; habitat moyen {v.mean():.3f}, "
                     f"> 0,5 : {(v > 0.5).mean():.1%} des cellules, > 0,8 : {(v > 0.8).mean():.1%}")
    return lines


def final_days(t: dict, weather: dict, scfg: dict, fclasses: list[str], gclasses: list[str], cfg: dict,
               month_override: int | None = None, access: np.ndarray | float = 1.0) -> dict[str, list[np.ndarray]]:
    """Score final de chaque famille pour chaque jour du curseur (saison et exposition du mois de CE jour)."""
    interp = MeteoInterp(weather["points"], t["x"], t["y"])
    hab_by_month: dict[int, dict[str, np.ndarray]] = {}
    out: dict[str, list[np.ndarray]] = {k: [] for k in scfg["families"]}
    for i, d in enumerate(weather["dates"]):
        m = month_override or int(d[5:7])
        if m not in hab_by_month:
            hab_by_month[m] = compute_habitat(t, scfg, fclasses, gclasses, m)
        meteo = interp.day(i)
        for k, fam in scfg["families"].items():
            s = season_factor(m, fam["season_months"], scfg["season"])
            out[k].append(final_score(meteo, hab_by_month[m][k], s, cfg["score"], access))
    return out


def run(cfg: dict, weather: dict | None, month: int | None = None) -> dict:
    """weather : résultat de cli.meteo_points (points + dates du curseur), ou None pour l'habitat seul."""
    scfg = load_species()
    t, meta = load_terrain()
    fclasses, gclasses = meta["forest_classes"], meta["geol_classes"]
    month_override = month
    month = month or date.today().month
    hab = compute_habitat(t, scfg, fclasses, gclasses, month)
    crit = check_criteria(t, hab, scfg, fclasses, gclasses)
    res: dict = {"t": t, "meta": meta, "hab": hab, "criteria": crit, "month": month, "scfg": scfg, "final": {}}
    lines = [f"# Habitat & score final — {date.today().isoformat()} (mois {month})", "", "## Habitat par famille"]
    lines += describe(t, hab, scfg, fclasses, month)
    # Accessibilité (phase 2c) : facteur par cellule, -1 = hors périmètre de trajet
    from radar import access as A
    acc_data = A.load()
    acc: np.ndarray | float = 1.0
    if acc_data is None:
        lines += ["", "⚠️ Accessibilité non calculée (`python -m radar access`) : périmètre = disque à vol d'oiseau."]
    else:
        a, am = acc_data
        acc = A.access_factor(a["travel_min"], a["protected"], np.array(am["factors"]),
                              cfg["availability"]["max_travel_minutes"])
        res["access"], res["access_meta"] = a, am
        res["access_factor"] = acc
        mx = cfg["availability"]["max_travel_minutes"]
        bad = int(((acc >= 0) & ~(a["travel_min"] <= mx)).sum())
        crit.append({"id": "2c", "ok": bad == 0,
                     "detail": f"{int((acc < 0).sum())} cellules hors périmètre (> {mx} min) retirées ; "
                               f"cellules affichées au-delà du périmètre : {bad}"})
    # Accès à pied : pente forte loin d'une voie -> score atténué (jamais exclu)
    from radar import foot as F
    foot_data = F.load(len(t["x"]))
    if foot_data is None:
        lines += ["", "⚠️ Accès à pied non calculé (`python -m radar pied`) : pente et chemins non pris en compte."]
    else:
        fd, fm = foot_data
        ff = F.foot_factor(fd["hard_share"], cfg["foot"]["min_factor"])
        base = np.broadcast_to(np.asarray(acc, dtype=np.float64), ff.shape)
        acc = np.where(base >= 0, base * ff, base)
        res["foot"], res["foot_meta"] = fd, fm
        res["access_factor"] = acc
        lines += ["", f"Accès à pied : facteur médian {np.median(ff):.2f}, cellules atténuées de plus de 25 % : "
                      f"{(ff < 0.75).mean():.1%}."]
    # Foncier : abords bâtis retirés, terrain privé non bâti atténué, public et non cadastré inchangés.
    # Le facteur foncier est multiplicatif et ≤ 1 : score = score_sans_foncier × facteur, exactement (la carte
    # recalcule ainsi le score pour un autre poids du privé, sans tout réembarquer).
    acc_base = acc
    lf = None
    from radar import land as Ld
    land_data = Ld.load(len(t["x"]))
    if land_data is None:
        lines += ["⚠️ Foncier non calculé (`python -m radar foncier`) : jardins et terrains privés non pris en compte."]
    else:
        ls, lm = land_data
        lf = Ld.land_factor(ls, cfg["land"]["private_factor"])
        base = np.broadcast_to(np.asarray(acc, dtype=np.float64), lf.shape)
        acc = np.where(base >= 0, base * lf, base)
        res["land"], res["land_meta"] = ls, lm
        res["access_factor"] = acc
        lines += [f"Foncier : facteur médian {np.median(lf):.2f} (privé × {cfg['land']['private_factor']}, abords "
                  f"bâtis × 0) ; cellules majoritairement publiques : {(ls['public'] > 0.5).mean():.1%}."]
    if weather is not None:
        weather_points = weather["points"]
        meteo = meteo_on_cells(weather_points, t["x"], t["y"])
        res["meteo"] = meteo
        if weather.get("dates"):
            base = final_days(t, weather, scfg, fclasses, gclasses, cfg, month_override, acc_base)
            if lf is None:
                res["final_days"] = base
            else:
                res["final_days_base"] = base
                res["final_days"] = {k: [np.where(v >= 0, v * lf, v) for v in days] for k, days in base.items()}
            res["dates"], res["offsets"] = weather["dates"], weather["offsets"]
        lines += ["", "## Score final (classes sur cellules connues et dans le périmètre)"]
        for k, h in hab.items():
            fam = scfg["families"][k]
            s = season_factor(month, fam["season_months"], scfg["season"])
            f = final_score(meteo, h, s, cfg["score"], acc)
            res["final"][k] = f
            v = f[~np.isnan(f) & (f >= 0)]
            b = cfg["score"]["classes"]
            lines.append(f"- {k} : très bon {(v >= b['tres_bon']).mean():.1%}, bon "
                         f"{((v >= b['bon']) & (v < b['tres_bon'])).mean():.1%}, moyen "
                         f"{((v >= b['moyen']) & (v < b['bon'])).mean():.1%}, faible {(v < b['moyen']).mean():.1%}")
    lines += ["", "## Critères de fin 2b/2c (cohérence du code avec les hypothèses, pas validation des hypothèses)"]
    lines += [f"- ({c['id']}) {'OK' if c['ok'] else 'ÉCHEC'} — {c['detail']}" for c in crit]
    res["report"] = "\n".join(lines)
    out = resolve("data/processed")
    out.mkdir(parents=True, exist_ok=True)
    (out / "habitat_report.txt").write_text(res["report"], encoding="utf-8")
    np.savez_compressed(out / "habitat_250m.npz", **{f"habitat_{k}": v.astype(np.float32) for k, v in hab.items()},
                        **{f"final_{k}": v.astype(np.float32) for k, v in res["final"].items()})
    return res
