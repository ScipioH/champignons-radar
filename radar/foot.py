"""Accès à pied : part de chaque cellule à la fois raide ET loin d'une voie (route, chemin, sentier).

- Pente : MNT Copernicus reprojeté à 50 m sur la grille de terrain.py (pente lissée par la maille).
- Voies : tronçons BD TOPO® (IGN, WFS Géoplateforme) hors autoroutes et bacs, téléchargés par dalles et
  rasterisés à 50 m ; distance à la voie la plus proche en métrique octogonale (au plus ~9 % sous l'euclidienne).
- Difficulté du pixel (0-1) : rampe de pente de flat_deg à steep_deg ; réduite de moitié au bord immédiat d'une
  voie (on y prospecte une bande) ; 1 à plus de far_m de toute voie. Le réseau IGN est très dense (chemins
  compris) : une exemption complète « près d'un chemin » annulait presque toute pénalité (constat 2026-11-11).
- Facteur de score de la cellule : 1 − (1 − min_factor) × difficulté moyenne (jamais d'exclusion).

Calcul unique (`python -m radar pied`) ; voies rasterisées en cache dans data/raw/paths (données publiques).
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from rasterio import features

from radar.config import resolve
from radar.terrain import GridSpec, block_nanmean, dem_on_grid, slope_aspect

log = logging.getLogger("radar.foot")
LINE_TYPES = ("LineString", "MultiLineString")


# --------------------------------------------------------------------------- voies (WFS par dalles)

def tiles(grid: GridSpec, step_m: float) -> list[tuple[float, float, float, float]]:
    """Dalles carrées couvrant l'emprise, gardées si elles recoupent le disque d'étude."""
    xmin, ymin, xmax, ymax = grid.bbox()
    out = []
    for x in np.arange(xmin, xmax, step_m):
        for y in np.arange(ymin, ymax, step_m):
            b = (float(x), float(y), float(min(x + step_m, xmax)), float(min(y + step_m, ymax)))
            dx = max(b[0] - grid.x0, 0.0, grid.x0 - b[2])
            dy = max(b[1] - grid.y0, 0.0, grid.y0 - b[3])
            if math.hypot(dx, dy) <= grid.radius_m + grid.cell_m:
                out.append(b)
    return out


def fetch_tile(fcfg: dict, bbox: tuple[float, float, float, float], get, depth: int = 0) -> list[dict]:
    """Entités de la dalle ; si la réponse atteint la limite `count`, la dalle est coupée en quatre."""
    q = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": fcfg["typename"],
         "SRSNAME": "EPSG:2154", "BBOX": ",".join(f"{v:.0f}" for v in bbox) + ",EPSG:2154",
         "OUTPUTFORMAT": "application/json", "COUNT": str(fcfg["count"]), "PROPERTYNAME": "nature,geometrie"}
    r = get(fcfg["wfs_url"] + "?" + urllib.parse.urlencode(q, safe=",:"))
    feats = r.get("features") if isinstance(r, dict) else None
    if not isinstance(feats, list):
        raise RuntimeError("WFS voies : réponse sans liste « features »")
    if len(feats) < fcfg["count"]:
        return feats
    if depth >= 4:
        raise RuntimeError(f"WFS voies : dalle {bbox} encore saturée après 4 découpes")
    xm, ym = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    out: list[dict] = []
    for b in ((bbox[0], bbox[1], xm, ym), (xm, bbox[1], bbox[2], ym), (bbox[0], ym, xm, bbox[3]), (xm, ym, bbox[2], bbox[3])):
        out += fetch_tile(fcfg, b, get, depth + 1)
    return out


def burn_paths(feats: list[dict], grid: GridSpec, out: np.ndarray, exclude: set[str], counts: Counter) -> None:
    """Rasterise (tous pixels touchés) les tronçons retenus dans `out` (uint8, 1 = voie)."""
    shapes = []
    for f in feats:
        g, nat = f.get("geometry"), (f.get("properties") or {}).get("nature")
        if not g or g.get("type") not in LINE_TYPES:
            continue
        counts[str(nat)] += 1
        if nat in exclude:
            continue
        shapes.append((g, 1))
    if shapes:
        features.rasterize(shapes, out=out, transform=grid.fine_transform, all_touched=True)


def paths_raster(grid: GridSpec, fcfg: dict, get, refresh: bool = False) -> tuple[np.ndarray, dict]:
    """Raster 50 m des voies (cache data/raw/paths) et métadonnées du téléchargement."""
    d = resolve(fcfg["cache_dir"])
    npz, meta_f = d / "paths_50m.npz", d / "paths_meta.json"
    if npz.exists() and meta_f.exists() and not refresh:
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        if meta.get("grid") == grid.__dict__ and meta.get("exclude") == sorted(fcfg["exclude_natures"]):
            with np.load(npz) as z:
                return z["paths"], meta
        log.info("cache des voies obsolète (grille ou natures exclues changées) : nouveau téléchargement")
    out = np.zeros(grid.fine_shape, dtype=np.uint8)
    counts: Counter = Counter()
    ts = tiles(grid, fcfg["tile_km"] * 1000.0)
    for n, b in enumerate(ts):
        if n:
            time.sleep(fcfg["pause_seconds"])
        burn_paths(fetch_tile(fcfg, b, get), grid, out, set(fcfg["exclude_natures"]), counts)
        if n % 10 == 0:
            log.info("voies : dalle %d / %d", n + 1, len(ts))
    meta = {"date": date.today().isoformat(), "grid": grid.__dict__, "exclude": sorted(fcfg["exclude_natures"]),
            "typename": fcfg["typename"], "n_tiles": len(ts), "natures": dict(counts.most_common())}
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(npz, paths=out)
    meta_f.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return out, meta


# --------------------------------------------------------------------------- calcul (pur numpy)

def octagonal_distance(mask: np.ndarray, step_m: float, max_m: float) -> np.ndarray:
    """Distance (m) au pixel True le plus proche, par dilatations alternées 8- et 4-voisins ; inf au-delà de max_m."""
    d = np.where(mask, 0.0, np.inf).astype(np.float32)
    reached = mask.astype(bool).copy()
    for k in range(1, int(math.ceil(max_m / step_m)) + 1):
        g = reached.copy()
        g[1:, :] |= reached[:-1, :]
        g[:-1, :] |= reached[1:, :]
        g[:, 1:] |= reached[:, :-1]
        g[:, :-1] |= reached[:, 1:]
        if k % 2:  # diagonales un pas sur deux : métrique octogonale
            g[1:, 1:] |= reached[:-1, :-1]
            g[1:, :-1] |= reached[:-1, 1:]
            g[:-1, 1:] |= reached[1:, :-1]
            g[:-1, :-1] |= reached[1:, 1:]
        d[g & ~reached] = k * step_m
        reached = g
    return d


def hard_pixels(slope: np.ndarray, dist: np.ndarray, fcfg: dict) -> np.ndarray:
    """Difficulté 0-1 du pixel : rampe de pente flat_deg -> steep_deg, réduite (× near_relief) au bord d'une voie
    (≤ near_m), 1 au-delà de far_m de toute voie ; NaN = pente inconnue (mer, hors MNT)."""
    ramp = np.clip((np.nan_to_num(slope) - fcfg["flat_deg"]) / (fcfg["steep_deg"] - fcfg["flat_deg"]), 0.0, 1.0)
    diff = ramp * np.where(dist <= fcfg["near_m"], fcfg["near_relief"], 1.0)
    diff = np.maximum(diff, (dist > fcfg["far_m"]).astype(np.float64))
    return np.where(np.isnan(slope), np.nan, diff).astype(np.float32)


def foot_factor(hard_share: np.ndarray, min_factor: float) -> np.ndarray:
    """Multiplicateur du score : 1 (aucune part difficile) à min_factor (cellule entièrement difficile) ; NaN -> 1."""
    h = np.nan_to_num(np.asarray(hard_share, dtype=np.float64), nan=0.0)
    return 1.0 - (1.0 - min_factor) * np.clip(h, 0.0, 1.0)


# --------------------------------------------------------------------------- exécution

def run(cfg: dict, get=None, refresh: bool = False) -> str:
    from radar.access import _get_json
    from radar.habitat import load_terrain

    fcfg = cfg["foot"]
    t, meta = load_terrain()
    grid = GridSpec(**meta["grid"])
    paths, pmeta = paths_raster(grid, fcfg, get or _get_json, refresh)

    tifs = sorted((resolve("data/raw") / "dem").glob("*.tif"))
    if not tifs:
        raise FileNotFoundError("MNT absent (data/raw/dem) : lancer `python -m radar fetch`")
    dem = dem_on_grid(tifs, grid)
    slope, _ = slope_aspect(np.nan_to_num(dem, nan=0.0), grid.fine_m)
    slope[np.isnan(dem)] = np.nan
    del dem
    cap = 2.0 * fcfg["far_m"]
    dist = octagonal_distance(paths > 0, grid.fine_m, cap)
    hard = hard_pixels(slope, dist, fcfg)
    steep = np.where(np.isnan(slope), np.nan, (slope > fcfg["flat_deg"]).astype(np.float32))

    f = grid.factor
    rows = np.floor((grid.ymax - t["y"]) / grid.cell_m).astype(int)
    cols = np.floor((t["x"] - grid.xmin) / grid.cell_m).astype(int)
    hard_share = block_nanmean(hard, f)[rows, cols]
    steep_share = block_nanmean(steep, f)[rows, cols]
    path_dist = block_nanmean(np.minimum(dist, cap), f)[rows, cols]

    out = resolve("data/processed")
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "foot_250m.npz", hard_share=hard_share, steep_share=steep_share, path_dist_m=path_dist)
    (out / "foot_meta.json").write_text(json.dumps({
        "date": date.today().isoformat(), "n_cells": int(len(t["x"])),
        "params": {k: fcfg[k] for k in ("flat_deg", "steep_deg", "near_m", "near_relief", "far_m", "min_factor")},
        "paths_date": pmeta["date"], "natures": pmeta["natures"],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    fac = foot_factor(hard_share, fcfg["min_factor"])
    known = ~np.isnan(hard_share)
    hs = hard_share[known]
    lines = [f"# Accès à pied — {date.today().isoformat()}",
             f"Voies BD TOPO (dalles : {pmeta['n_tiles']}, téléchargées le {pmeta['date']}) : "
             + ", ".join(f"{k} {v}" for k, v in pmeta["natures"].items()),
             f"Exclues : {', '.join(pmeta['exclude']) or 'aucune'}.",
             f"Règle : difficulté = rampe de pente {fcfg['flat_deg']}° -> {fcfg['steep_deg']}°, × {fcfg['near_relief']} à "
             f"≤ {fcfg['near_m']} m d'une voie, 1 à plus de {fcfg['far_m']} m ; facteur = 1 − "
             f"{1 - fcfg['min_factor']:.2f} × difficulté moyenne.",
             f"Pixels 50 m sur une voie : {(paths > 0).mean():.1%} ; pente > {fcfg['flat_deg']}° : {np.nanmean(steep):.1%}.",
             f"Cellules : difficulté moyenne {hs.mean():.1%} ; > 25 % : {(hs > 0.25).mean():.1%} ; "
             f"> 50 % : {(hs > 0.5).mean():.1%}.",
             f"Facteur : médiane {np.median(fac[known]):.2f}, minimum {fac[known].min():.2f}.",
             f"Distance moyenne à une voie (cellule) : médiane {np.nanmedian(path_dist):.0f} m, "
             f"95e centile {np.nanpercentile(path_dist, 95):.0f} m."]
    txt = "\n".join(lines)
    (out / "foot_report.txt").write_text(txt, encoding="utf-8")
    return txt


def load(n_cells: int, dirpath: Path | None = None) -> tuple[dict, dict] | None:
    """Données d'accès à pied si présentes ET calculées sur la grille actuelle (sinon None)."""
    d = dirpath or resolve("data/processed")
    if not (d / "foot_250m.npz").exists():
        return None
    meta = json.loads((d / "foot_meta.json").read_text(encoding="utf-8"))
    if meta.get("n_cells") != n_cells:
        log.warning("accès à pied calculé sur une autre grille : ignoré (relancer `python -m radar pied`)")
        return None
    with np.load(d / "foot_250m.npz") as z:
        return {k: z[k] for k in z.files}, meta
