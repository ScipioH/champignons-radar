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

def idw(px: np.ndarray, py: np.ndarray, pv: np.ndarray, cx: np.ndarray, cy: np.ndarray,
        k: int = 4, power: float = 2.0, chunk: int = 20000) -> np.ndarray:
    """Pondération inverse des distances sur les k points les plus proches (lissage)."""
    k = min(k, len(pv))
    out = np.empty(len(cx))
    for s in range(0, len(cx), chunk):
        dx = cx[s:s + chunk, None] - px[None, :]
        dy = cy[s:s + chunk, None] - py[None, :]
        d = np.hypot(dx, dy)
        nn = np.argpartition(d, k - 1, axis=1)[:, :k]
        dn = np.take_along_axis(d, nn, axis=1)
        w = 1.0 / np.maximum(dn, 1.0) ** power
        out[s:s + chunk] = (w * pv[nn]).sum(axis=1) / w.sum(axis=1)
    return out


def meteo_on_cells(points: list[dict], cx: np.ndarray, cy: np.ndarray) -> np.ndarray:
    """Score météo 0-1 par cellule à partir des points V0 (score 0-100)."""
    pts = [p for p in points if p.get("score") is not None]
    if not pts:
        raise ValueError("aucun point météo exploitable")
    x, y = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(
        [p["lon"] for p in pts], [p["lat"] for p in pts])
    return idw(np.asarray(x), np.asarray(y), np.array([p["score"] for p in pts]) / 100.0, cx, cy)


def final_score(meteo: np.ndarray, habitat: np.ndarray, season: float, scfg_score: dict,
                access: np.ndarray | float = 1.0) -> np.ndarray:
    a, b = scfg_score["exponent_meteo"], scfg_score["exponent_habitat"]
    return 100.0 * np.clip(meteo, 0, 1) ** a * habitat ** b * season * access   # NaN (inconnu) propagé


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


def run(cfg: dict, weather_points: list[dict] | None, month: int | None = None) -> dict:
    scfg = load_species()
    t, meta = load_terrain()
    fclasses, gclasses = meta["forest_classes"], meta["geol_classes"]
    month = month or date.today().month
    hab = compute_habitat(t, scfg, fclasses, gclasses, month)
    crit = check_criteria(t, hab, scfg, fclasses, gclasses)
    res = {"t": t, "meta": meta, "hab": hab, "criteria": crit, "month": month, "scfg": scfg, "final": {}}
    lines = [f"# Habitat & score final — {date.today().isoformat()} (mois {month})", "", "## Habitat par famille"]
    lines += describe(t, hab, scfg, fclasses, month)
    if weather_points is not None:
        meteo = meteo_on_cells(weather_points, t["x"], t["y"])
        res["meteo"] = meteo
        lines += ["", "## Score final (classes sur cellules connues)"]
        for k, h in hab.items():
            fam = scfg["families"][k]
            s = season_factor(month, fam["season_months"], scfg["season"])
            f = final_score(meteo, h, s, cfg["score"])
            res["final"][k] = f
            v = f[~np.isnan(f)]
            b = cfg["score"]["classes"]
            lines.append(f"- {k} : très bon {(v >= b['tres_bon']).mean():.1%}, bon "
                         f"{((v >= b['bon']) & (v < b['tres_bon'])).mean():.1%}, moyen "
                         f"{((v >= b['moyen']) & (v < b['bon'])).mean():.1%}, faible {(v < b['moyen']).mean():.1%}")
    lines += ["", "## Critères de fin 2b (cohérence du code avec les hypothèses, pas validation des hypothèses)"]
    lines += [f"- ({c['id']}) {'OK' if c['ok'] else 'ÉCHEC'} — {c['detail']}" for c in crit]
    res["report"] = "\n".join(lines)
    out = resolve("data/processed")
    out.mkdir(parents=True, exist_ok=True)
    (out / "habitat_report.txt").write_text(res["report"], encoding="utf-8")
    np.savez_compressed(out / "habitat_250m.npz", **{f"habitat_{k}": v.astype(np.float32) for k, v in hab.items()},
                        **{f"final_{k}": v.astype(np.float32) for k, v in res["final"].items()})
    return res
