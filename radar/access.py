"""Accessibilité (phase 2c) : temps de trajet réel et zones protégées. Calcul unique (réseau), résultat figé.

- Temps de trajet : OSRM (voiture) depuis le point de départ vers un échantillon tous les `sample_km`, plus la
  marche du point d'échantillon à la route la plus proche ; interpolé sur les cellules 250 m (IDW, 4 voisins).
  Une cellule au-delà de `availability.max_travel_minutes` est HORS PÉRIMÈTRE (retirée de la carte).
- Zones protégées : couches PatriNat de la Géoplateforme IGN (réserves naturelles, arrêtés de biotope, cœur de
  parc national), rasterisées à 50 m puis part de chaque catégorie par cellule ; facteur de score par catégorie.

Le point de départ peut être le domicile (RADAR_HOME_LAT/LON, .env ou secrets) : il n'est jamais écrit dans les
sorties (seulement un drapeau « domicile oui/non »).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import features

from radar.config import load_env, resolve
from radar.habitat import idw_weights, load_terrain
from radar.terrain import L93, GridSpec, block_nanmean

log = logging.getLogger("radar.access")
UA = {"User-Agent": "champignons-radar/0.1 (usage personnel)"}


def origin(cfg: dict) -> tuple[float, float, bool]:
    """(lat, lon, est_domicile). Domicile lu uniquement dans l'environnement (.env local / secrets CI)."""
    load_env()
    lat, lon = os.environ.get("RADAR_HOME_LAT"), os.environ.get("RADAR_HOME_LON")
    if lat and lon:
        return float(lat), float(lon), True
    return cfg["center"]["lat"], cfg["center"]["lon"], False


def _get_json(url: str, retries: int = 3) -> dict:
    delay = 5.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001 - réseau : on retente puis on remonte l'erreur
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("inaccessible")


# --------------------------------------------------------------------------- temps de trajet

def sample_points(t: dict, grid: GridSpec, step_m: float, x0: float, y0: float) -> tuple[np.ndarray, np.ndarray]:
    """Points tous les step_m (L93) dans le disque, gardés seulement s'il y a une cellule terrestre à < step_m."""
    xs = np.arange(math.floor((x0 - grid.radius_m) / step_m), math.ceil((x0 + grid.radius_m) / step_m) + 1) * step_m
    ys = np.arange(math.floor((y0 - grid.radius_m) / step_m), math.ceil((y0 + grid.radius_m) / step_m) + 1) * step_m
    X, Y = (a.ravel() for a in np.meshgrid(xs, ys))
    keep = np.hypot(X - grid.x0, Y - grid.y0) <= grid.radius_m + step_m
    X, Y = X[keep], Y[keep]
    nn, _ = idw_weights(t["x"], t["y"], X, Y, k=1)
    d = np.hypot(t["x"][nn[:, 0]] - X, t["y"][nn[:, 0]] - Y)
    return X[d < step_m], Y[d < step_m]


def osrm_minutes(o_lonlat: tuple[float, float], dests: list[tuple[float, float]], acfg: dict,
                 get=_get_json) -> tuple[np.ndarray, np.ndarray]:
    """Durée voiture (min) et distance d'accroche à la route (m) depuis l'origine, par lots ; NaN si non routable."""
    dur = np.full(len(dests), np.nan)
    snap = np.full(len(dests), np.nan)
    b = acfg["batch"]
    for n, s in enumerate(range(0, len(dests), b)):
        chunk = dests[s:s + b]
        if n:
            time.sleep(acfg["pause_seconds"])
        coords = ";".join(f"{lo:.5f},{la:.5f}" for lo, la in [o_lonlat, *chunk])
        url = f"{acfg['osrm_url']}/table/v1/driving/{coords}?sources=0&annotations=duration"
        r = get(url)
        if r.get("code") != "Ok":
            raise RuntimeError(f"OSRM : {r.get('code')} {r.get('message', '')}")
        row = r["durations"][0][1:]
        dur[s:s + len(chunk)] = [np.nan if v is None else v / 60.0 for v in row]
        snap[s:s + len(chunk)] = [w.get("distance", np.nan) for w in r["destinations"][1:]]
        log.info("OSRM lot %d : %d destinations", n + 1, len(chunk))
    return dur, snap


def travel_minutes(drive_min: np.ndarray, snap_m: np.ndarray, walk_kmh: float) -> np.ndarray:
    """Voiture + marche jusqu'au point (la marche depuis la route compte aussi)."""
    return drive_min + snap_m / 1000.0 / walk_kmh * 60.0


# --------------------------------------------------------------------------- zones protégées

def wfs_features(acfg: dict, typename: str, bbox: tuple[float, float, float, float], get=_get_json) -> list[dict]:
    q = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": typename,
         "SRSNAME": "EPSG:2154", "BBOX": ",".join(f"{v:.0f}" for v in bbox) + ",EPSG:2154",
         "OUTPUTFORMAT": "application/json", "COUNT": "5000"}
    r = get(acfg["protected"]["wfs_url"] + "?" + urllib.parse.urlencode(q, safe=",:"))
    return r.get("features", [])


def protected_shares(grid: GridSpec, keep_idx: tuple[np.ndarray, np.ndarray], layers: dict,
                     feats: dict[str, list[dict]]) -> tuple[np.ndarray, list[str]]:
    """Part (0-1) de chaque catégorie dans chaque cellule conservée."""
    names = list(layers)
    out = np.zeros((len(keep_idx[0]), len(names)), dtype=np.float32)
    for j, name in enumerate(names):
        where = layers[name].get("where") or {}
        geoms = [(f["geometry"], 1) for f in feats.get(name, [])
                 if f.get("geometry") and all(f["properties"].get(k) == v for k, v in where.items())]
        if not geoms:
            continue
        r = features.rasterize(geoms, out_shape=grid.fine_shape, transform=grid.fine_transform, fill=0, dtype="uint8")
        out[:, j] = block_nanmean(r.astype(np.float32), grid.factor)[keep_idx]
    return out, names


def access_factor(travel_min: np.ndarray, shares: np.ndarray, factors: np.ndarray, max_minutes: float) -> np.ndarray:
    """1 dans le périmètre, -1 hors périmètre (trajet > max ou non routable) ; atténué dans les zones protégées."""
    f = np.prod(1.0 - shares * (1.0 - factors[None, :]), axis=1)
    return np.where(np.isfinite(travel_min) & (travel_min <= max_minutes), f, -1.0)


# --------------------------------------------------------------------------- exécution

def run(cfg: dict) -> str:
    acfg = cfg["access"]
    t, meta = load_terrain()
    grid = GridSpec(**meta["grid"])
    lat0, lon0, is_home = origin(cfg)
    to_l93 = Transformer.from_crs("EPSG:4326", L93, always_xy=True)
    to_wgs = Transformer.from_crs(L93, "EPSG:4326", always_xy=True)
    x0, y0 = to_l93.transform(lon0, lat0)

    sx, sy = sample_points(t, grid, acfg["sample_km"] * 1000.0, x0, y0)
    slon, slat = to_wgs.transform(sx, sy)
    log.info("%d points d'échantillonnage du trajet (pas %s km)", len(sx), acfg["sample_km"])
    drive, snap = osrm_minutes((lon0, lat0), list(zip(np.asarray(slon), np.asarray(slat))), acfg)
    tm = travel_minutes(drive, snap, acfg["walk_kmh"])
    ok = np.isfinite(tm)
    nn, w = idw_weights(sx[ok], sy[ok], t["x"], t["y"])
    travel = (w * tm[ok][nn]).sum(axis=1).astype(np.float32)

    # zones protégées
    rows = np.floor((grid.ymax - t["y"]) / grid.cell_m).astype(int)
    cols = np.floor((t["x"] - grid.xmin) / grid.cell_m).astype(int)
    layers = acfg["protected"]["layers"]
    feats, names_found = {}, {}
    for name, l in layers.items():
        feats[name] = wfs_features(acfg, l["typename"], grid.bbox())
        where = l.get("where") or {}
        names_found[name] = sorted({f["properties"].get("nom_site", "?") for f in feats[name]
                                    if all(f["properties"].get(k) == v for k, v in where.items())})
    shares, cats = protected_shares(grid, (rows, cols), layers, feats)

    out = resolve("data/processed")
    np.savez_compressed(out / "access_250m.npz", travel_min=travel, protected=shares)
    (out / "access_meta.json").write_text(json.dumps({
        "date": date.today().isoformat(), "origin_is_home": is_home, "categories": cats,
        "factors": [layers[c]["factor"] for c in cats], "labels": [layers[c]["label"] for c in cats],
        "sample_km": acfg["sample_km"], "n_samples": int(len(sx)), "n_unroutable": int((~ok).sum()),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    mx = cfg["availability"]["max_travel_minutes"]
    inside = travel <= mx
    lines = [f"# Accessibilité — {date.today().isoformat()}",
             f"Départ : {'domicile (.env)' if is_home else 'centre ' + cfg['center']['name']} ; OSRM voiture + marche "
             f"{acfg['walk_kmh']} km/h depuis la route.",
             f"Échantillon : {len(sx)} points tous les {acfg['sample_km']} km ; non routables : {int((~ok).sum())}.",
             f"Temps de trajet (cellules) : médiane {np.median(travel):.0f} min, max {travel.max():.0f} min.",
             f"Cellules à ≤ {mx} min : {inside.sum()} / {len(travel)} ({inside.mean():.0%}) ; "
             f"hors périmètre : {(~inside).sum()}.",
             "", "## Zones protégées dans l'emprise"]
    for j, c in enumerate(cats):
        lines.append(f"- {layers[c]['label']} (facteur {layers[c]['factor']}) : {(shares[:, j] > 0).sum()} cellules "
                     f"touchées — {', '.join(names_found[c]) or 'aucune'}")
    txt = "\n".join(lines)
    (out / "access_report.txt").write_text(txt, encoding="utf-8")
    return txt


def load(dirpath: Path | None = None) -> tuple[dict, dict] | None:
    d = dirpath or resolve("data/processed")
    if not (d / "access_250m.npz").exists():
        return None
    with np.load(d / "access_250m.npz") as z:
        a = {k: z[k] for k in z.files}
    return a, json.loads((d / "access_meta.json").read_text(encoding="utf-8"))
