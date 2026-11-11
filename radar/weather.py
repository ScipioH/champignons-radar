"""Récupération météo Open-Meteo (historique + prévisions), requêtes groupées + cache disque.

Respect des quotas : lots de batch_size points, pause entre lots, retry avec backoff sur 429,
compteur journalier de points interrogés (max_points_per_day).
"""
from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from radar.config import resolve

log = logging.getLogger("radar.weather")
# et0_fao_evapotranspiration : évapotranspiration de référence (mm/j), pour le bilan d'eau du sol (score.soil_ratio)
DAILY_VARS = "precipitation_sum,temperature_2m_min,temperature_2m_mean,et0_fao_evapotranspiration"
# change quand DAILY_VARS change : un cache ancien n'a pas les mêmes variables
VARS_TAG = "v2"


class QuotaExceeded(RuntimeError):
    pass


def _key(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def _http_get_json(url: str, max_retries: int) -> object:
    delay = 5.0
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "champignons-radar/0.1 (usage personnel)"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # 429 (quota/minute) et 5xx (serveur) : passagers, on retente ; 4xx : erreur de requête, on remonte
            if (e.code == 429 or e.code >= 500) and attempt < max_retries:
                log.warning("HTTP %d reçu, nouvelle tentative dans %.0fs", e.code, delay)
                time.sleep(delay)
                delay *= 2
                continue
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Open-Meteo HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                # réessayer n'y change rien. Cause constatée le 2026-11-11 : antivirus qui intercepte le HTTPS
                # (certificats ré-signés « Avast Web/Mail Shield ») et présente par moments un certificat expiré
                raise RuntimeError(
                    f"certificat HTTPS refusé ({e.reason.verify_message}). Cause probable : un antivirus qui "
                    "analyse les connexions HTTPS (ex. Avast, Agent Web) ; exclure *.open-meteo.com de cette "
                    "analyse, ou vérifier l'heure du PC.") from e
            if attempt < max_retries:
                log.warning("réseau : %s ; nouvelle tentative dans %.0fs", e, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"Open-Meteo injoignable après {max_retries + 1} essais : {e}") from e
        except (TimeoutError, ConnectionError) as e:
            # coupure réseau / délai dépassé (constaté en CI : « handshake operation timed out »)
            if attempt < max_retries:
                log.warning("réseau : %s ; nouvelle tentative dans %.0fs", e, delay)
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"Open-Meteo injoignable après {max_retries + 1} essais : {e}") from e
    raise RuntimeError("Open-Meteo : échec après retries")


def build_url(points: list[tuple[float, float]], wcfg: dict) -> str:
    params = {
        "latitude": ",".join(f"{p[0]:.4f}" for p in points),
        "longitude": ",".join(f"{p[1]:.4f}" for p in points),
        "daily": DAILY_VARS,
        "timezone": "Europe/Paris",
    }
    if wcfg.get("start_date"):  # période datée (API historical-forecast, contrôle des pluies)
        params["start_date"], params["end_date"] = wcfg["start_date"], wcfg["end_date"]
    else:
        params["past_days"], params["forecast_days"] = wcfg["past_days"], wcfg["forecast_days"]
    if wcfg.get("model"):
        params["models"] = wcfg["model"]  # ex. meteofrance_arome_france_hd ; absent = best_match
    return wcfg["api_url"] + "?" + urllib.parse.urlencode(params, safe=",")


def _parse(entry: dict) -> dict:
    d = entry["daily"]
    return {
        "elevation": entry.get("elevation"),
        "time": d["time"],
        "precip": d["precipitation_sum"],
        "tmin": d["temperature_2m_min"],
        "tmean": d["temperature_2m_mean"],
        "et0": d.get("et0_fao_evapotranspiration"),  # absent des anciens caches : repli dans le score
    }


def _quota_file(cache_dir: Path) -> Path:
    return cache_dir / "quota.json"


def _load_quota(cache_dir: Path) -> dict:
    f = _quota_file(cache_dir)
    today = date.today().isoformat()
    if f.exists():
        q = json.loads(f.read_text(encoding="utf-8"))
        if q.get("date") == today:
            return q
    return {"date": today, "points": 0}


def _cache_prefix(wcfg: dict) -> str:
    tag = f"{wcfg['model']}_" if wcfg.get("model") else ""
    # la fenêtre demandée fait partie de la clé : sinon un changement de past_days relirait des séries
    # de mauvaise longueur et décalerait tous les indices
    if wcfg.get("start_date"):
        span = f"s{wcfg['start_date']}e{wcfg['end_date']}_"
    else:
        span = f"p{wcfg['past_days']}f{wcfg['forecast_days']}_"
    return f"weather_{tag}{span}"


def latest_cache(points: list[tuple[float, float]], wcfg: dict) -> tuple[dict[str, dict], str]:
    """Repli quand Open-Meteo est injoignable : le cache le plus récent (même modèle, même fenêtre) qui contient
    tous les points. Retourne (données, date du cache). Ses séries sont centrées sur SA date, pas sur aujourd'hui."""
    cache_dir = resolve(wcfg["cache_dir"])
    prefix = _cache_prefix(wcfg)
    found = []
    for f in cache_dir.glob(prefix + "*.json"):
        m = re.fullmatch(r"(?:v\d+_)?(\d{4}-\d{2}-\d{2})", f.stem[len(prefix):])
        if m:
            found.append((m.group(1), f))
    for day, f in sorted(found, reverse=True):
        cache = json.loads(f.read_text(encoding="utf-8"))
        if all(_key(*p) in cache for p in points):
            return {_key(*p): cache[_key(*p)] for p in points}, day
    raise RuntimeError("Open-Meteo injoignable et aucune météo en cache pour ces points")


SERIES = ("precip", "tmin", "tmean", "et0")


def fallback_cfg(wcfg: dict) -> dict | None:
    """Config du modèle de complément (fallback_model), ou None s'il n'y a pas de mélange."""
    fb = wcfg.get("fallback_model")
    if not wcfg.get("model") or not fb:
        return None
    return dict(wcfg, model=None if fb == "best_match" else fb)


def merge_series(primary: dict, fallback: dict) -> dict:
    """Jour par jour : valeur du modèle principal, sinon celle du complément (ex. AROME/ARPEGE jusqu'à J+3,
    puis best_match). Les deux séries doivent couvrir les mêmes dates."""
    if primary["time"] != fallback["time"]:
        raise RuntimeError("séries météo de dates différentes : mélange impossible")
    out = dict(primary)
    out["elevation"] = primary.get("elevation") if primary.get("elevation") is not None else fallback.get("elevation")
    for k in SERIES:
        p, f = primary.get(k), fallback.get(k)
        if p is None or f is None:
            out[k] = p if p is not None else f
            continue
        out[k] = [a if a is not None else b for a, b in zip(p, f)]
    out["filled_days"] = sum(a is None for a in primary["precip"])
    return out


def fetch_blend(points: list[tuple[float, float]], wcfg: dict) -> dict[str, dict]:
    """fetch_all du modèle principal, complété par fallback_model là où il n'a pas de valeur."""
    fb = fallback_cfg(wcfg)
    prim = fetch_all(points, wcfg)
    if fb is None:
        return prim
    sec = fetch_all(points, fb)
    return {k: merge_series(prim[k], sec[k]) for k in prim}


def latest_blend(points: list[tuple[float, float]], wcfg: dict) -> tuple[dict[str, dict], str]:
    """Repli hors ligne pour fetch_blend : dernier cache du modèle principal, complété par le cache du complément
    du même jour s'il existe."""
    prim, day = latest_cache(points, wcfg)
    fb = fallback_cfg(wcfg)
    if fb is None:
        return prim, day
    try:
        sec, day2 = latest_cache(points, fb)
    except RuntimeError:
        return prim, day
    if day2 != day:
        return prim, day
    return {k: merge_series(prim[k], sec[k]) for k in prim}, day


def fetch_all(points: list[tuple[float, float]], wcfg: dict) -> dict[str, dict]:
    """Retourne {"lat,lon": {elevation,time,precip,tmin,tmean,et0}} ; cache valable pour la journée."""
    cache_dir = resolve(wcfg["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{_cache_prefix(wcfg)}{VARS_TAG}_{date.today().isoformat()}.json"
    cache: dict[str, dict] = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}

    todo = [p for p in points if _key(*p) not in cache]
    log.info("%d points demandés, %d en cache, %d à télécharger", len(points), len(points) - len(todo), len(todo))
    quota = _load_quota(cache_dir)
    if quota["points"] + len(todo) > wcfg["max_points_per_day"]:
        raise QuotaExceeded(
            f"Garde-fou quota : {quota['points']} points déjà interrogés aujourd'hui + {len(todo)} "
            f"> max_points_per_day={wcfg['max_points_per_day']}. Ajuster la config en connaissance de cause."
        )

    bs = wcfg["batch_size"]
    for n, start in enumerate(range(0, len(todo), bs)):
        batch = todo[start:start + bs]
        if n:
            time.sleep(wcfg["pause_seconds"])
        data = _http_get_json(build_url(batch, wcfg), wcfg["max_retries"])
        entries = data if isinstance(data, list) else [data]
        if len(entries) != len(batch):
            raise RuntimeError(f"Réponse inattendue : {len(entries)} entrées pour {len(batch)} points")
        for p, e in zip(batch, entries):
            cache[_key(*p)] = _parse(e)
        quota["points"] += len(batch)
        # sauvegarde incrémentale : une interruption ne perd pas les lots déjà obtenus
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
        _quota_file(cache_dir).write_text(json.dumps(quota), encoding="utf-8")
        log.info("lot %d OK (%d points)", n + 1, len(batch))
    return {_key(*p): cache[_key(*p)] for p in points}
