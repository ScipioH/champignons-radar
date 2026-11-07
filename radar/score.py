"""Score météo de pousse (V0). Fonctions pures, sans réseau, testables sur séries simulées.

Rappel : indicateur de probabilité de POUSSE, jamais de comestibilité.
Tous les seuils viennent de la config et sont des hypothèses non calibrées.
"""
from __future__ import annotations

import math


def ramp(x: float, x0: float, x1: float, y0: float = 0.0, y1: float = 1.0) -> float:
    """Interpolation linéaire bornée entre (x0,y0) et (x1,y1)."""
    if x1 == x0:
        return y1 if x >= x1 else y0
    t = (x - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return y0 + t * (y1 - y0)


def _p(precip: list, i: int) -> float:
    v = precip[i]
    return 0.0 if v is None else float(v)


def find_trigger(precip: list, idx: int, cfg: dict) -> int | None:
    """Indice du jour de la pluie déclenchante la plus récente (cumul 2 j >= seuil), ou None."""
    trig = cfg["pluie"]["trigger_mm"]
    lookback = cfg["delai"]["lookback_days"]
    for i in range(idx, max(-1, idx - lookback - 1), -1):
        prev = _p(precip, i - 1) if i >= 1 else 0.0
        if _p(precip, i) + prev >= trig:
            # le jour d'événement est celui qui porte le plus de pluie dans la paire
            return i if _p(precip, i) >= prev else i - 1
    return None


def comp_pluie(precip: list, idx: int, cfg: dict) -> float:
    c = cfg["pluie"]
    lo = max(0, idx - c["lookback_days"] + 1)
    best = 0.0
    for i in range(lo, idx + 1):
        prev = _p(precip, i - 1) if i >= 1 else 0.0
        best = max(best, _p(precip, i) + prev)
    if best >= c["plateau_to_mm"]:
        return ramp(best, c["plateau_to_mm"], c["saturation_mm"], 1.0, c["saturation_value"])
    return ramp(best, c["zero_below_mm"], c["plateau_from_mm"])


def comp_delai(days_since: int | None, cfg: dict) -> float:
    if days_since is None:
        return 0.0
    c = cfg["delai"]
    if days_since <= c["plateau_from"]:
        return ramp(days_since, c["zero_before"], c["plateau_from"])
    if days_since <= c["plateau_to"]:
        return 1.0
    return ramp(days_since, c["plateau_to"], c["zero_after"], 1.0, 0.0)


def comp_temp(tmin: list, idx: int, cfg: dict) -> float | None:
    c = cfg["temp"]
    vals = [v for v in tmin[max(0, idx - c["window_days"] + 1): idx + 1] if v is not None]
    if not vals:
        return None
    return ramp(sum(vals) / len(vals), c["zero_tmin"], c["ideal_tmin"])


def soil_ratio(precip: list, et0: list | None, idx: int, cfg: dict) -> float | None:
    """Réserve d'eau de surface (0-1) au jour idx : bilan quotidien « seau » depuis le début de la série.

    W = min(W + pluie, capacité) − coef × ET0, borné à 0. Départ à init_ratio × capacité ; l'état initial est
    oublié après quelques semaines (pluies et évaporation). None si l'ET0 manque (repli sur l'ancienne mesure).
    """
    c = cfg["secheresse"]
    if not et0 or len(et0) <= idx:
        return None
    vals = et0[: idx + 1]
    if sum(v is None for v in vals) > 0.1 * len(vals):
        return None
    cap = float(c["bucket_mm"])
    w = c["init_ratio"] * cap
    for i in range(idx + 1):
        e = et0[i] if et0[i] is not None else 0.0
        w = max(0.0, min(cap, w + _p(precip, i)) - c["et_coef"] * float(e))
    return w / cap


def comp_secheresse(precip: list, idx: int, trig_idx: int | None, cfg: dict, et0: list | None = None) -> float:
    """Le sol est-il resté humide ? Bilan d'eau (pluie − évaporation) si l'ET0 est disponible, sinon repli :
    pluie moyenne par jour depuis la pluie déclenchante."""
    c = cfg["secheresse"]
    r = soil_ratio(precip, et0, idx, cfg)
    if r is not None:
        return ramp(r, c["dry_ratio"], c["wet_ratio"], c["min_value"], 1.0)
    if trig_idx is None:
        return c["min_value"]
    days = max(1, idx - trig_idx)
    rain_since = sum(_p(precip, i) for i in range(trig_idx + 1, idx + 1))
    return ramp(rain_since / days, 0.0, c["ok_mm_per_day"], c["min_value"], 1.0)


def comp_prevision(precip: list, idx: int, cfg: dict) -> float | None:
    c = cfg["prevision"]
    hi = min(len(precip), idx + 1 + c["horizon_days"])
    if hi <= idx + 1:
        return None  # pas de prévision disponible : composante ignorée
    total = sum(_p(precip, i) for i in range(idx + 1, hi))
    if total < c["ok_from_mm"]:
        return ramp(total, 0.0, c["ok_from_mm"], c["dry_value"], 1.0)
    if total <= c["ok_to_mm"]:
        return 1.0
    return ramp(total, c["ok_to_mm"], c["wet_mm"], 1.0, c["wet_value"])


def gel_factor(tmin: list, idx: int, altitude: float | None, cfg: dict) -> tuple[float, int]:
    """Facteur doux (jamais 0 sec) selon nuits de gel, sévérité et altitude."""
    c = cfg["gel"]
    vals = [v for v in tmin[max(0, idx - c["window_days"] + 1): idx + 1] if v is not None]
    nights = sum(1 for v in vals if v <= c["frost_tmin"])
    if nights == 0:
        return 1.0, 0
    mult = c["altitude_multiplier"] if (altitude or 0) > c["altitude_threshold_m"] else 1.0
    f = max(c["floor"], 1.0 - c["penalty_per_night"] * nights * mult)
    if vals and min(vals) <= c["severe_tmin"]:
        f = min(f, c["severe_factor"])
    return f, nights


def classify(score100: float, cfg: dict) -> str:
    b = cfg["classes"]
    if score100 >= b["tres_bon"]:
        return "très bon"
    if score100 >= b["bon"]:
        return "bon"
    if score100 >= b["moyen"]:
        return "moyen"
    return "faible"


def score_point(precip: list, tmin: list, idx: int, altitude: float | None, cfg: dict,
                et0: list | None = None) -> dict:
    """Score météo 0-100 pour le jour d'indice idx. Les jours > idx sont des prévisions.
    et0 : évapotranspiration quotidienne (mm), pour le bilan d'eau ; facultative (repli si absente).

    Moyenne géométrique pondérée avec plancher (un facteur nul n'écrase pas tout),
    multipliée par un facteur gel doux. Les composantes indisponibles (None) sont
    ignorées et les poids renormalisés (résultat marqué partial=True).
    """
    if not 0 <= idx < len(precip):
        raise ValueError("idx hors série")
    trig_idx = find_trigger(precip, idx, cfg)
    days_since = None if trig_idx is None else idx - trig_idx
    comps = {
        "pluie": comp_pluie(precip, idx, cfg),
        "delai": comp_delai(days_since, cfg),
        "temp": comp_temp(tmin, idx, cfg),
        "secheresse": comp_secheresse(precip, idx, trig_idx, cfg, et0),
        "prevision": comp_prevision(precip, idx, cfg),
    }
    floor = cfg["floor"]
    wsum = 0.0
    acc = 0.0
    for name, w in cfg["weights"].items():
        v = comps.get(name)
        if v is None:
            continue
        wsum += w
        acc += w * math.log(max(v, floor))
    if wsum == 0:
        return {"score": None, "classe": "indéterminé", "partial": True, "components": comps}
    meteo = math.exp(acc / wsum)
    gf, nights = gel_factor(tmin, idx, altitude, cfg)
    score = round(100 * meteo * gf, 1)
    return {
        "score": score,
        "classe": classify(score, cfg),
        "partial": any(v is None for v in comps.values()),
        "components": {k: (None if v is None else round(v, 3)) for k, v in comps.items()},
        "gel_factor": round(gf, 3),
        "gel_nights": nights,
        "days_since_trigger": days_since,
        "rain_15d_mm": round(sum(_p(precip, i) for i in range(max(0, idx - 14), idx + 1)), 1),
        "soil_ratio": None if (sr := soil_ratio(precip, et0, idx, cfg)) is None else round(sr, 3),
    }
