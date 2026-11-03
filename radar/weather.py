"""Récupération météo Open-Meteo (historique + prévisions), requêtes groupées + cache disque.

Respect des quotas : lots de batch_size points, pause entre lots, retry avec backoff sur 429,
compteur journalier de points interrogés (max_points_per_day).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from radar.config import resolve

log = logging.getLogger("radar.weather")
DAILY_VARS = "precipitation_sum,temperature_2m_min,temperature_2m_mean"


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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
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
        "past_days": wcfg["past_days"],
        "forecast_days": wcfg["forecast_days"],
        "timezone": "Europe/Paris",
    }
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


def fetch_all(points: list[tuple[float, float]], wcfg: dict) -> dict[str, dict]:
    """Retourne {"lat,lon": {elevation,time,precip,tmin,tmean}} ; cache valable pour la journée."""
    cache_dir = resolve(wcfg["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{wcfg['model']}_" if wcfg.get("model") else ""
    # la fenêtre demandée fait partie de la clé : sinon un changement de past_days relirait des séries
    # de mauvaise longueur et décalerait tous les indices
    span = f"p{wcfg['past_days']}f{wcfg['forecast_days']}_"
    cache_file = cache_dir / f"weather_{tag}{span}{date.today().isoformat()}.json"
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
