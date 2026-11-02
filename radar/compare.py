"""Comparaison de modèles météo sur une même zone (phase 2a). Fonctions pures, sans réseau.

Honnêteté : sans observations au sol, on NE PEUT PAS dire quel modèle est le plus juste.
On mesure seulement (1) la disponibilité/complétude des données, (2) le niveau de détail spatial,
(3) la cohérence physique (gradient thermique avec l'altitude), (4) l'effet sur le score.
"""
from __future__ import annotations

import statistics
from collections import Counter

from radar import score as sc


def _vals(xs):
    return [x for x in xs if x is not None]


def summarize(data: dict[str, dict], idx: int, score_cfg: dict, horizon: int = 3) -> dict:
    """Résumé d'un modèle. data = {"lat,lon": {elevation,time,precip,tmin,...}}."""
    rows = []
    miss_p = miss_t = miss_fc = n_series = 0
    for key, d in data.items():
        p, t = d["precip"], d["tmin"]
        n_series += 1
        miss_p += sum(1 for x in p[: idx + 1] if x is None)
        miss_t += sum(1 for x in t[: idx + 1] if x is None)
        fc = p[idx + 1: idx + 1 + horizon]
        miss_fc += sum(1 for x in fc if x is None) + max(0, horizon - len(fc))
        alt = d.get("elevation")
        r = sc.score_point(p, t, idx, alt, score_cfg)
        tm7 = _vals(t[max(0, idx - 6): idx + 1])
        rows.append({
            "key": key, "alt": alt, "score": r["score"], "classe": r["classe"],
            "rain15": r["rain_15d_mm"], "tmin7": (sum(tm7) / len(tm7)) if tm7 else None,
        })
    scores = [r["score"] for r in rows if r["score"] is not None]
    rain = [r["rain15"] for r in rows]
    out = {
        "n_points": n_series,
        "missing_precip_past": miss_p, "missing_tmin_past": miss_t, "missing_precip_forecast": miss_fc,
        "rain15_mean": round(statistics.fmean(rain), 1) if rain else None,
        "rain15_std": round(statistics.pstdev(rain), 1) if len(rain) > 1 else None,
        "rain15_unique": len({round(x, 1) for x in rain}),
        "score_mean": round(statistics.fmean(scores), 1) if scores else None,
        "score_std": round(statistics.pstdev(scores), 1) if len(scores) > 1 else None,
        "classes": dict(Counter(r["classe"] for r in rows)),
        "_rows": rows,
    }
    pairs = [(r["alt"], r["rain15"]) for r in rows if r["alt"] is not None]
    if len(pairs) > 2 and len({a for a, _ in pairs}) > 1 and len({b for _, b in pairs}) > 1:
        out["corr_rain15_alt"] = round(statistics.correlation([a for a, _ in pairs], [b for _, b in pairs]), 2)
    tp = [(r["alt"], r["tmin7"]) for r in rows if r["alt"] is not None and r["tmin7"] is not None]
    if len(tp) > 2 and len({a for a, _ in tp}) > 1:
        slope = statistics.linear_regression([a for a, _ in tp], [b for _, b in tp]).slope
        out["tmin_gradient_c_per_km"] = round(slope * 1000, 2)
    return out


def pairwise(sa: dict, sb: dict) -> dict:
    """Écarts entre deux résumés calculés sur les MÊMES points."""
    ra = {r["key"]: r for r in sa["_rows"]}
    rb = {r["key"]: r for r in sb["_rows"]}
    keys = sorted(set(ra) & set(rb))
    if not keys:
        return {"n_common": 0}
    dr = [abs(ra[k]["rain15"] - rb[k]["rain15"]) for k in keys]
    ds = [abs(ra[k]["score"] - rb[k]["score"]) for k in keys if ra[k]["score"] is not None and rb[k]["score"] is not None]
    dc = sum(1 for k in keys if ra[k]["classe"] != rb[k]["classe"])
    out = {
        "n_common": len(keys),
        "rain15_mean_abs_diff_mm": round(statistics.fmean(dr), 1),
        "score_mean_abs_diff": round(statistics.fmean(ds), 1) if ds else None,
        "class_differs_pct": round(100 * dc / len(keys), 1),
    }
    xa = [ra[k]["rain15"] for k in keys]
    xb = [rb[k]["rain15"] for k in keys]
    if len(set(xa)) > 1 and len(set(xb)) > 1:
        out["rain15_corr"] = round(statistics.correlation(xa, xb), 2)
    return out


def public(summary: dict) -> dict:
    return {k: v for k, v in summary.items() if not k.startswith("_")}
