"""Contrôle de la pluie du modèle (Open-Meteo) par les relevés quotidiens des stations Météo-France.

Question posée : le modèle voit-il les pluies qui déclenchent les poussées, et avec quel biais ? On compare, station
par station, sur les `days` derniers jours :
  - les cumuls (rapport modèle / observé) et la corrélation quotidienne ;
  - les pluies déclenchantes (cumul 2 j >= trigger_mm) : détectées, manquées, inventées (±tolérance) ;
  - l'effet sur le score météo : même calcul avec la pluie observée au lieu de la pluie modèle (température et
    évapotranspiration du modèle dans les deux cas, pour isoler l'effet de la pluie).
Les fichiers Météo-France sont des données non fiables a priori : lus comme du texte CSV, valeurs bornées.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from radar import score as S
from radar import weather as W
from radar.config import resolve
from radar.grid import haversine_km

log = logging.getLogger("radar.observations")
UA = {"User-Agent": "champignons-radar/0.1 (usage personnel)"}
NEEDED = ("NUM_POSTE", "NOM_USUEL", "LAT", "LON", "ALTI", "AAAAMMJJ", "RR", "QRR")
QRR_OK = {"0", "1", "9"}  # 0 protégée, 1 validée, 9 filtrée non encore expertisée ; 2 = douteuse : écartée
BANDS = [(-1e9, 200, "plaine (< 200 m)"), (200, 600, "collines (200-600 m)"), (600, 1e9, "relief (> 600 m)")]


# --------------------------------------------------------------------------- téléchargement

def _host_ok(url: str, ocfg: dict) -> bool:
    u = urllib.parse.urlparse(url)
    return u.scheme == "https" and u.hostname in set(ocfg["allowed_hosts"])


def resolve_url(dep: str, ocfg: dict, get_json=None) -> str:
    """URL du fichier « latest » le plus récent du département, lue dans le catalogue data.gouv."""
    def _get(url: str):
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    try:
        cat = (get_json or _get)(ocfg["catalog_url"])
        pat = re.compile(rf"QUOT_departement_{re.escape(dep)}_periode_(\d{{4}})-(\d{{4}})_RR-T-Vent$")
        best = None
        for res in cat.get("resources", []):
            m = pat.match(str(res.get("title", "")))
            if m and _host_ok(str(res.get("url", "")), ocfg):
                key = (int(m.group(2)), int(m.group(1)))
                if best is None or key > best[0]:
                    best = (key, res["url"])
        if best:
            return best[1]
        log.warning("catalogue data.gouv : fichier du %s introuvable, gabarit de secours", dep)
    except Exception as e:  # noqa: BLE001 - le gabarit reste utilisable
        log.warning("catalogue data.gouv injoignable (%s), gabarit de secours", e)
    return ocfg["url_template"].format(dep=dep)


def download(dep: str, ocfg: dict, get_json=None) -> Path:
    out_dir = resolve(ocfg["dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"Q_{dep}_latest_RR.csv.gz"
    if dst.exists() and time.time() - dst.stat().st_mtime < ocfg["max_age_hours"] * 3600:
        return dst
    url = resolve_url(dep, ocfg, get_json)
    if not _host_ok(url, ocfg):
        raise RuntimeError(f"hôte non autorisé : {url}")
    limit = int(ocfg["max_size_mb"] * 1024 * 1024)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"fichier {dep} plus gros que max_size_mb={ocfg['max_size_mb']} : refusé")
    if data[:2] != b"\x1f\x8b":
        raise RuntimeError(f"fichier {dep} : pas un gzip")
    tmp = dst.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dst)
    log.info("relevés %s téléchargés (%d Ko)", dep, len(data) // 1024)
    return dst


# --------------------------------------------------------------------------- lecture

def _num(s: str, lo: float, hi: float) -> float | None:
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        return None
    return v if lo <= v <= hi and math.isfinite(v) else None


def parse(text: str) -> dict[str, dict]:
    """{num_poste: {nom, lat, lon, alt, rr: {AAAA-MM-JJ: mm}}} ; lignes invalides ou douteuses ignorées."""
    rd = csv.DictReader(io.StringIO(text), delimiter=";")
    missing = [c for c in NEEDED if c not in (rd.fieldnames or [])]
    if missing:
        raise ValueError(f"colonnes absentes du fichier Météo-France : {missing}")
    out: dict[str, dict] = {}
    for row in rd:
        rr = _num(row["RR"] or "", 0.0, 600.0)
        if rr is None or (row["QRR"] or "").strip() not in QRR_OK:
            continue
        d = (row["AAAAMMJJ"] or "").strip()
        if not re.fullmatch(r"\d{8}", d):
            continue
        num = (row["NUM_POSTE"] or "").strip()
        st = out.get(num)
        if st is None:
            lat, lon = _num(row["LAT"] or "", 40, 52), _num(row["LON"] or "", -6, 10)
            if lat is None or lon is None:
                continue
            st = out[num] = {"nom": (row["NOM_USUEL"] or "").strip()[:60], "lat": lat, "lon": lon,
                             "alt": _num(row["ALTI"] or "", -10, 5000), "rr": {}}
        st["rr"][f"{d[:4]}-{d[4:6]}-{d[6:]}"] = rr
    return out


def load_stations(path: Path) -> dict[str, dict]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        return parse(f.read())


# --------------------------------------------------------------------------- comparaison

def trigger_days(p: list[float], trig: float) -> list[int]:
    return [i for i in range(1, len(p)) if p[i] + p[i - 1] >= trig]


def events(days: list[int]) -> list[tuple[int, int]]:
    """Regroupe des jours consécutifs (ou à 1 jour d'écart) en épisodes (début, fin)."""
    ev: list[tuple[int, int]] = []
    for d in days:
        if ev and d - ev[-1][1] <= 2:
            ev[-1] = (ev[-1][0], d)
        else:
            ev.append((d, d))
    return ev


def match_events(obs_ev: list[tuple[int, int]], mod_ev: list[tuple[int, int]], tol: int) -> tuple[int, int, int]:
    """(détectés, manqués, inventés)."""
    def near(a, b):
        return a[0] - tol <= b[1] and b[0] - tol <= a[1]
    hits = sum(any(near(o, m) for m in mod_ev) for o in obs_ev)
    false = sum(not any(near(m, o) for o in obs_ev) for m in mod_ev)
    return hits, len(obs_ev) - hits, false


def compare_station(obs_rr: dict[str, float], model: dict, scfg: dict, ocfg: dict, alt: float | None,
                    only: set[str] | None = None) -> dict | None:
    """Comparaison sur les jours communs. model = série Open-Meteo parsée (weather._parse).
    only : dates autorisées (pour comparer plusieurs modèles exactement sur les mêmes jours)."""
    times = model["time"]
    pm = [S._p(model["precip"], i) for i in range(len(times))]
    po = [obs_rr.get(t) for t in times]
    common = [i for i, v in enumerate(po) if v is not None and model["precip"][i] is not None
              and (only is None or times[i] in only)]
    if len(common) < ocfg["min_days"]:
        return None
    a_o = np.array([po[i] for i in common], float)
    a_m = np.array([pm[i] for i in common], float)
    corr = float(np.corrcoef(a_o, a_m)[0, 1]) if a_o.std() > 0 and a_m.std() > 0 else None

    # séries complètes pour les épisodes et le score : jour sans relevé -> valeur du modèle (signalé par la
    # complétude ; les stations trop lacunaires sont déjà écartées par min_days)
    filled: list[float] = [pm[i] if (v := po[i]) is None else v for i in range(len(times))]
    trig = scfg["pluie"]["trigger_mm"]
    last = max(common)
    o_ev = events([d for d in trigger_days(filled[: last + 1], trig) if d >= min(common) + 1])
    m_ev = events([d for d in trigger_days(pm[: last + 1], trig) if d >= min(common) + 1])
    hits, missed, false = match_events(o_ev, m_ev, ocfg["event_tolerance_days"])

    # effet sur le score météo, jours évaluables : historique complet derrière, prévision (observée) devant
    look = scfg["delai"]["lookback_days"] + 2
    hor = scfg["prevision"]["horizon_days"]
    d_scores = []
    for i in range(max(look, min(common) + look), last - hor + 1):
        so = S.score_point(filled, model["tmin"], i, alt, scfg, model.get("et0"))["score"]
        sm = S.score_point(pm, model["tmin"], i, alt, scfg, model.get("et0"))["score"]
        if so is not None and sm is not None:
            d_scores.append((sm, so))
    return {"n_days": len(common), "sum_obs": float(a_o.sum()), "sum_mod": float(a_m.sum()), "corr": corr,
            "mae": float(np.abs(a_m - a_o).mean()), "hits": hits, "missed": missed, "false": false,
            "scores": d_scores, "completeness": len(common) / (last - min(common) + 1)}


def summarize(rows: list[dict], scfg: dict) -> dict:
    if not rows:
        return {"n": 0}
    so, sm = sum(r["sum_obs"] for r in rows), sum(r["sum_mod"] for r in rows)
    hits, missed, false = (sum(r[k] for r in rows) for k in ("hits", "missed", "false"))
    pairs = [p for r in rows for p in r["scores"]]
    corrs = [r["corr"] for r in rows if r["corr"] is not None]
    out = {"n": len(rows), "ratio": (sm / so) if so > 0 else None, "sum_obs": so, "sum_mod": sm,
           "corr_median": float(np.median(corrs)) if corrs else None,
           "mae": float(np.mean([r["mae"] for r in rows])),
           "hits": hits, "missed": missed, "false": false,
           "pod": hits / (hits + missed) if hits + missed else None,
           "far": false / (hits + false) if hits + false else None, "n_scores": len(pairs)}
    if pairs:
        d = np.array([m - o for m, o in pairs])
        cls = [S.classify(m, scfg) != S.classify(o, scfg) for m, o in pairs]
        out.update({"score_bias": float(d.mean()), "score_mad": float(np.abs(d).mean()),
                    "class_diff": float(np.mean(cls))})
    return out


def _f(v, fmt: str, none: str = "—") -> str:
    return none if v is None else format(v, fmt)


def summary_lines(name: str, s: dict) -> list[str]:
    if not s.get("n"):
        return [f"- {name} : aucune station exploitable"]
    L = [f"- **{name}** ({s['n']} stations) : cumul modèle / observé = {_f(s['ratio'], '.2f')} "
         f"({s['sum_mod']:.0f} / {s['sum_obs']:.0f} mm) ; corrélation quotidienne médiane {_f(s['corr_median'], '.2f')} ; "
         f"écart moyen {s['mae']:.1f} mm/j",
         f"  - pluies déclenchantes : {s['hits']} vues, {s['missed']} manquées, {s['false']} inventées "
         f"(détection {_f(s['pod'], '.0%')}, fausses alertes {_f(s['far'], '.0%')})"]
    if s.get("n_scores"):
        L.append(f"  - effet sur le score météo ({s['n_scores']} jours × stations) : écart moyen "
                 f"{s['score_mad']:.1f} pts, biais {s['score_bias']:+.1f} pts (modèle − observé), "
                 f"classe différente {s['class_diff']:.0%} du temps")
    return L


def verdict(s: dict) -> str:
    if not s.get("n") or s.get("ratio") is None:
        return "pas assez de données pour conclure."
    parts = []
    r = s["ratio"]
    parts.append("le modèle **surestime** la pluie" if r > 1.2 else "le modèle **sous-estime** la pluie" if r < 0.8
                 else "cumuls cohérents (±20 %)")
    if s.get("pod") is not None:
        parts.append(f"{s['pod']:.0%} des pluies déclenchantes vues")
    if s.get("class_diff") is not None:
        parts.append(f"classe météo identique {1 - s['class_diff']:.0%} du temps")
    return " ; ".join(parts) + "."


# --------------------------------------------------------------------------- exécution

def run(cfg: dict, models: list[str]) -> str:
    ocfg, scfg = cfg["observations"], cfg["score"]
    c = cfg["center"]
    stations: dict[str, dict] = {}
    for dep in ocfg["departements"]:
        stations.update(load_stations(download(str(dep), ocfg)))
    near = {k: s for k, s in stations.items()
            if haversine_km(c["lat"], c["lon"], s["lat"], s["lon"]) <= ocfg["radius_km"]}
    # stations sans relevé récent : inutiles pour la fenêtre comparée
    recent_from = (date.today() - timedelta(days=ocfg["days"])).isoformat()
    near = {k: s for k, s in near.items() if any(d >= recent_from for d in s["rr"])}
    if not near:
        raise RuntimeError("aucune station Météo-France récente dans le rayon")
    last_obs = max(d for s in near.values() for d in s["rr"])
    pts = [(round(s["lat"], 4), round(s["lon"], 4)) for s in near.values()]

    # API « historical forecast » : prévisions archivées des modèles, séries complètes sur la période (l'API de
    # prévision, elle, renvoie des valeurs vides au-delà de ~40-57 jours d'historique selon le modèle)
    end = date.fromisoformat(last_obs)
    start = end - timedelta(days=ocfg["days"] - 1)
    datas = {}
    for model in models:
        wcfg = dict(cfg["weather"], api_url=ocfg["model_api_url"], start_date=start.isoformat(),
                    end_date=end.isoformat(), model=None if model == "best_match" else model)
        datas[model] = W.fetch_all(pts, wcfg)

    per_model: dict[str, list[tuple[dict, dict]]] = {m: [] for m in models}
    for (lat, lon), s in zip(pts, near.values()):
        k = f"{lat:.4f},{lon:.4f}"
        # mêmes jours pour tous les modèles : sinon les chiffres ne sont pas comparables entre eux
        ok = set.intersection(*({t for t, v in zip(datas[m][k]["time"], datas[m][k]["precip"]) if v is not None}
                                for m in models))
        for model in models:
            r = compare_station(s["rr"], datas[model][k], scfg, ocfg, s["alt"], only=ok)
            if r:
                per_model[model].append((s, r))

    L = [f"# Contrôle de la pluie modèle par les stations Météo-France — {date.today().isoformat()}",
         "",
         f"{len(near)} stations à moins de {ocfg['radius_km']} km (départements {', '.join(map(str, ocfg['departements']))}), "
         f"relevés jusqu'au {last_obs} ; fenêtre {ocfg['days']} jours. Pluie déclenchante = "
         f"{scfg['pluie']['trigger_mm']} mm en 2 jours, tolérance ±{ocfg['event_tolerance_days']} j.",
         "Modèle « best_match » = celui qu'utilise le score.", ""]
    for model, rows in per_model.items():
        L.append(f"## {model}")
        L += summary_lines("toutes stations", summarize([r for _, r in rows], scfg))
        for lo, hi, label in BANDS:
            sel = [r for s, r in rows if s["alt"] is not None and lo <= s["alt"] < hi]
            L += summary_lines(label, summarize(sel, scfg))
        L += ["", f"Verdict : {verdict(summarize([r for _, r in rows], scfg))}", ""]
    if per_model:
        first = next(iter(per_model.values()))
        worst = sorted(first, key=lambda sr: abs(math.log(max(sr[1]['sum_mod'], 1) / max(sr[1]['sum_obs'], 1))),
                       reverse=True)[:8]
        L += [f"## Stations les plus en écart ({next(iter(per_model))})", "",
              "| Station | Alt. | Observé (mm) | Modèle (mm) | Corr. | Pluies vues / manquées / inventées |",
              "|---|---|---|---|---|---|"]
        for s, r in worst:
            L.append(f"| {s['nom']} | {_f(s['alt'], '.0f')} | {r['sum_obs']:.0f} | {r['sum_mod']:.0f} | "
                     f"{_f(r['corr'], '.2f')} | {r['hits']} / {r['missed']} / {r['false']} |")
    L += ["", "Lecture : un rapport > 1 = le modèle met plus de pluie que la station. Une station ponctuelle ne "
              "représente pas parfaitement une maille de modèle (orage local) : regarder les tendances, pas une station.",
          "Données : © Météo-France (Licence Ouverte 2.0) ; Open-Meteo.com (CC BY 4.0)."]
    text = "\n".join(L)
    out = resolve("data/out/controle_pluie.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    summ = {m: summarize([r for _, r in rows], scfg) for m, rows in per_model.items()}
    resolve("data/out/controle_pluie.json").write_text(
        json.dumps({"date": date.today().isoformat(), "last_obs": last_obs, "models": summ,
                    "generated": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return text
