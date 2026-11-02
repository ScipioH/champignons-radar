"""Prétraitement du terrain (phase 2a) : grille 250 m figée avec essences, géologie et relief.

Chaîne : polygones BD Forêt et BD Charm-50 rasterisés à `fine_m` (50 m) en Lambert 93, MNT Copernicus
reprojeté sur la même grille, puis agrégation par blocs en cellules de `terrain.spacing_m` (250 m) :
  - forêt et géologie : POURCENTAGE de chaque classe dans la cellule (pas seulement la majorité) ;
  - relief : altitude moyenne, pente moyenne, exposition (vecteur moyen pondéré par la pente).
La couverture de la géologie (départements 30 + 34) sert de masque de couverture : hors couverture, on
ne sait rien (≠ « hors forêt »). Mer = non couvert et altitude ≤ seuil.

Sortie : data/processed/terrain_250m.npz + terrain_meta.json + terrain_report.txt (régénérables, non versionnés).
"""
from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
import warnings
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import shapefile
import yaml
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin

from radar.config import ROOT, resolve

log = logging.getLogger("radar.terrain")
L93 = "EPSG:2154"
NODATA8 = 255
NODATA16 = 65535


# --------------------------------------------------------------------------- config & texte

def load_terrain_cfg(path: Path | None = None) -> dict:
    with open(path or ROOT / "config" / "terrain.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def normalize(s: str | None) -> str:
    """Minuscules, sans accents, apostrophes unifiées (BD Forêt stocke parfois U+0092)."""
    s = (s or "").replace("\x92", "'").replace("’", "'")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def forest_class(code_tfv: str | None, mapping: dict[str, str]) -> str:
    return mapping.get((code_tfv or "").strip(), "inconnu")


def classify_geology(notation: str | None, descr: str | None, rules: dict) -> str:
    """Règles NOTATION prioritaires, sinon mot-clé le plus tôt cité dans DESCR, sinon 'autre'."""
    n = (notation or "").strip()
    for r in rules["notation"]:
        if re.match(r["pattern"], n):
            return r["class"]
    text = normalize(descr)
    best: tuple[int, str] | None = None
    for cls, kws in rules["keywords"].items():
        for kw in kws:
            m = re.search(r"(?<![a-z])" + re.escape(normalize(kw)), text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), cls)
    cls = best[1] if best else "autre"
    ov = rules.get("age_override")
    if ov and cls in ov["from"] and any(normalize(w) in text for w in ov["if_any"]):
        cls = ov["to"]
    return cls


# --------------------------------------------------------------------------- grille

@dataclass
class GridSpec:
    xmin: float
    ymax: float
    fine_m: float
    factor: int
    nrows: int          # cellules (grossières)
    ncols: int
    x0: float           # centre en Lambert 93
    y0: float
    radius_m: float

    @property
    def cell_m(self) -> float:
        return self.fine_m * self.factor

    @property
    def fine_shape(self) -> tuple[int, int]:
        return self.nrows * self.factor, self.ncols * self.factor

    @property
    def fine_transform(self):
        return from_origin(self.xmin, self.ymax, self.fine_m, self.fine_m)

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.xmin + (np.arange(self.ncols) + 0.5) * self.cell_m
        ys = self.ymax - (np.arange(self.nrows) + 0.5) * self.cell_m
        return np.meshgrid(xs, ys)

    def bbox(self) -> tuple[float, float, float, float]:
        return self.xmin, self.ymax - self.nrows * self.cell_m, self.xmin + self.ncols * self.cell_m, self.ymax


def make_grid(lat0: float, lon0: float, radius_m: float, cell_m: float, fine_m: float) -> GridSpec:
    if cell_m <= 0 or fine_m <= 0 or abs(cell_m / fine_m - round(cell_m / fine_m)) > 1e-9:
        raise ValueError("cell_m doit être un multiple entier de fine_m")
    x0, y0 = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(lon0, lat0)
    xmin = math.floor((x0 - radius_m) / cell_m) * cell_m
    xmax = math.ceil((x0 + radius_m) / cell_m) * cell_m
    ymin = math.floor((y0 - radius_m) / cell_m) * cell_m
    ymax = math.ceil((y0 + radius_m) / cell_m) * cell_m
    return GridSpec(xmin, ymax, fine_m, int(round(cell_m / fine_m)),
                    int(round((ymax - ymin) / cell_m)), int(round((xmax - xmin) / cell_m)), x0, y0, radius_m)


# --------------------------------------------------------------------------- agrégation (pur numpy)

def blocks(a: np.ndarray, f: int) -> np.ndarray:
    nr, nc = a.shape
    if nr % f or nc % f:
        raise ValueError("dimensions non multiples du facteur d'agrégation")
    return a.reshape(nr // f, f, nc // f, f).swapaxes(1, 2).reshape(nr // f, nc // f, f * f)


def block_nanmean(a: np.ndarray, f: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # bloc entièrement vide -> NaN, voulu
        return np.nanmean(blocks(a, f), axis=2).astype(np.float32)


def block_percent(codes: np.ndarray, f: int, n_classes: int, nodata: int) -> tuple[np.ndarray, np.ndarray]:
    """Pourcentage de chaque classe parmi les pixels COUVERTS du bloc, et % de pixels couverts."""
    b = blocks(codes, f)
    covered = (b != nodata).sum(axis=2)
    out = np.zeros(b.shape[:2] + (n_classes,), dtype=np.uint8)
    denom = np.maximum(covered, 1)
    for k in range(n_classes):
        out[..., k] = np.round(100.0 * (b == k).sum(axis=2) / denom).astype(np.uint8)
    cov_pct = np.round(100.0 * covered / (f * f)).astype(np.uint8)
    return out, cov_pct


def slope_aspect(dem: np.ndarray, res: float) -> tuple[np.ndarray, np.ndarray]:
    """Pente (degrés) et exposition (azimut de la ligne de plus grande pente descendante, 0 = N, 90 = E).

    Les lignes du raster vont vers le sud : d(z)/d(nord) = -d(z)/d(ligne).
    """
    dz_drow, dz_dcol = np.gradient(dem.astype(np.float64), res)
    dz_dx, dz_dn = dz_dcol, -dz_drow
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dn)))
    aspect = (np.degrees(np.arctan2(-dz_dx, -dz_dn)) + 360.0) % 360.0
    return slope.astype(np.float32), aspect.astype(np.float32)


def block_exposure(slope: np.ndarray, aspect: np.ndarray, f: int) -> tuple[np.ndarray, np.ndarray]:
    """Composantes nord/est moyennes de l'exposition, pondérées par sin(pente) (un replat ne compte pas)."""
    w = np.sin(np.radians(slope))
    a = np.radians(aspect)
    return block_nanmean(w * np.cos(a), f), block_nanmean(w * np.sin(a), f)


# --------------------------------------------------------------------------- lecture & rasterisation

def iter_shapes(path: Path, encoding: str, bbox: tuple[float, float, float, float]):
    """(géométrie GeoJSON-like, enregistrement dict) des entités recoupant bbox."""
    r = shapefile.Reader(str(path), encoding=encoding)
    names = [f[0] for f in r.fields[1:]]
    xmin, ymin, xmax, ymax = bbox
    for sr in r.iterShapeRecords():
        if sr.shape.shapeType == shapefile.NULL or not sr.shape.points:
            continue  # géométrie vide : ignorée
        b = sr.shape.bbox
        if b[2] < xmin or b[0] > xmax or b[3] < ymin or b[1] > ymax:
            continue
        yield sr.shape.__geo_interface__, dict(zip(names, sr.record))


def rasterize_ids(pairs, g: GridSpec, fill: int, dtype: str) -> np.ndarray:
    pairs = list(pairs)
    if not pairs:
        return np.full(g.fine_shape, fill, dtype=dtype)
    return features.rasterize(pairs, out_shape=g.fine_shape, transform=g.fine_transform, fill=fill, dtype=dtype)


def dem_on_grid(tifs: list[Path], g: GridSpec) -> np.ndarray:
    import rasterio
    from rasterio.warp import Resampling, reproject

    dst = np.full(g.fine_shape, np.nan, dtype=np.float32)
    for p in tifs:
        with rasterio.open(p) as src:
            tmp = np.full(g.fine_shape, np.nan, dtype=np.float32)
            reproject(source=rasterio.band(src, 1), destination=tmp, src_transform=src.transform, src_crs=src.crs,
                      src_nodata=src.nodata, dst_transform=g.fine_transform, dst_crs=L93, dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
        m = np.isnan(dst) & ~np.isnan(tmp)
        dst[m] = tmp[m]
    return dst


# --------------------------------------------------------------------------- chaîne complète

@dataclass
class Inputs:
    forest_shps: list[Path]
    geology_shps: list[Path]
    dem_tifs: list[Path]


def discover_inputs(raw_dir: Path) -> Inputs:
    ext = raw_dir / "extracted"
    inp = Inputs(sorted(ext.rglob("FORMATION_VEGETALE.shp")), sorted(ext.rglob("*_S_FGEOL_2154.shp")),
                 sorted((raw_dir / "dem").glob("*.tif")))
    missing = [k for k, v in asdict(inp).items() if not v]
    if missing:
        raise FileNotFoundError(f"entrées manquantes : {missing} — lancer `python -m radar fetch` puis l'extraction")
    return inp


def build(cfg: dict, tcfg: dict, inp: Inputs) -> dict:
    c = cfg["center"]
    g = make_grid(c["lat"], c["lon"], cfg["grid"]["radius_km"] * 1000, cfg["terrain"]["spacing_m"], tcfg["fine_m"])
    bbox = g.bbox()
    log.info("grille : %d x %d cellules de %.0f m (fin : %s à %.0f m)", g.nrows, g.ncols, g.cell_m, g.fine_shape, g.fine_m)

    # Forêt : rasterisation de l'index CODE_TFV, puis table de correspondance -> classe
    fclasses = tcfg["forest"]["classes"]
    fidx = {k: i for i, k in enumerate(fclasses)}
    codes: dict[str, int] = {}
    pairs = []
    for shp in inp.forest_shps:
        for geom, rec in iter_shapes(shp, "utf-8", bbox):
            code = (rec["CODE_TFV"] or "").strip()
            pairs.append((geom, codes.setdefault(code, len(codes) + 1)))  # 0 = pas de polygone
    code_raster = rasterize_ids(pairs, g, 0, "uint8")
    lut = np.zeros(256, dtype=np.uint8)  # 0 -> hors_foret
    unknown_codes = []
    for code, i in codes.items():
        cls = forest_class(code, tcfg["forest"]["code_tfv"])
        if cls == "inconnu":
            unknown_codes.append(code)
        lut[i] = fidx[cls]
    forest = lut[code_raster]
    del code_raster, pairs

    # Géologie : rasterisation de l'index de formation (uint16), puis classe ; sert aussi de masque de couverture
    gclasses = tcfg["geology"]["classes"]
    gidx = {k: i for i, k in enumerate(gclasses)}
    formations: dict[tuple[str, str], int] = {}
    pairs = []
    for shp in inp.geology_shps:
        for geom, rec in iter_shapes(shp, "cp1252", bbox):
            key = ((rec["NOTATION"] or "").strip(), (rec["DESCR"] or "").strip())
            pairs.append((geom, formations.setdefault(key, len(formations))))
    form_raster = rasterize_ids(pairs, g, NODATA16, "uint16")
    del pairs
    glut = np.full(NODATA16 + 1, NODATA8, dtype=np.uint8)
    form_class = {}
    for (nota, descr), i in formations.items():
        form_class[i] = classify_geology(nota, descr, tcfg["geology"])
        glut[i] = gidx[form_class[i]]
    geol = glut[form_raster]
    covered = geol != NODATA8
    forest[~covered] = NODATA8

    # Relief
    dem = dem_on_grid(inp.dem_tifs, g)
    slope, aspect = slope_aspect(np.nan_to_num(dem, nan=0.0), g.fine_m)
    slope[np.isnan(dem)] = np.nan
    aspect[np.isnan(dem)] = np.nan

    f = g.factor
    forest_pct, cov_pct = block_percent(forest, f, len(fclasses), NODATA8)
    geol_pct, _ = block_percent(geol, f, len(gclasses), NODATA8)
    alt = block_nanmean(dem, f)
    slope_m = block_nanmean(slope, f)
    north, east = block_exposure(slope, aspect, f)

    X, Y = g.centers()
    in_disk = np.hypot(X - g.x0, Y - g.y0) <= g.radius_m
    sea = (cov_pct == 0) & ~(alt > cfg_sea_alt(tcfg))
    keep = in_disk & ~sea
    lon, lat = Transformer.from_crs(L93, "EPSG:4326", always_xy=True).transform(X[keep], Y[keep])

    # Rapport de couverture géologique : surface par formation non classée
    fine_area_km2 = (g.fine_m ** 2) / 1e6
    counts = np.bincount(form_raster[form_raster != NODATA16].ravel(), minlength=len(formations))
    inv = {i: k for k, i in formations.items()}
    unclassified = sorted(((counts[i] * fine_area_km2, inv[i]) for i, cl in form_class.items() if cl == "autre"),
                          reverse=True)
    geol_area = {cl: 0.0 for cl in gclasses}
    for i, cl in form_class.items():
        geol_area[cl] += counts[i] * fine_area_km2

    return {
        "grid": g, "keep": keep, "x": X[keep], "y": Y[keep], "lat": np.asarray(lat), "lon": np.asarray(lon),
        "alt": alt[keep], "slope": slope_m[keep], "north": north[keep], "east": east[keep],
        "forest_pct": forest_pct[keep], "geol_pct": geol_pct[keep], "coverage_pct": cov_pct[keep],
        "forest_classes": fclasses, "geol_classes": gclasses, "unknown_codes": unknown_codes,
        "geol_area_km2": geol_area, "geol_unclassified": unclassified[:20],
        "n_formations": len(formations), "n_in_disk": int(in_disk.sum()), "n_sea": int((in_disk & sea).sum()),
    }


def cfg_sea_alt(tcfg: dict) -> float:
    return float(tcfg.get("sea_max_alt_m", 1.0))


def describe_area(res: dict, lat: float, lon: float, radius_m: float, top: int = 4) -> str:
    """Bilan moyen des cellules dans un rayon (plus représentatif qu'une cellule isolée)."""
    x, y = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(lon, lat)
    sel = np.hypot(res["x"] - x, res["y"] - y) <= radius_m
    if not sel.any():
        return "aucune cellule dans le rayon"

    def topk(pct, names):
        order = np.argsort(pct)[::-1][:top]
        return ", ".join(f"{names[k]} {pct[k]:.0f}%" for k in order if pct[k] >= 0.5) or "—"
    return (f"{int(sel.sum())} cellules, alt {np.nanmin(res['alt'][sel]):.0f}–{np.nanmax(res['alt'][sel]):.0f} m, "
            f"pente moy. {np.nanmean(res['slope'][sel]):.1f}° | "
            f"forêt : {topk(res['forest_pct'][sel].mean(axis=0), res['forest_classes'])} | "
            f"géologie : {topk(res['geol_pct'][sel].mean(axis=0), res['geol_classes'])}")


def report(res: dict, tcfg: dict) -> str:
    g: GridSpec = res["grid"]
    cell_km2 = (g.cell_m ** 2) / 1e6
    n = len(res["x"])
    lines = [f"# Rapport terrain — {date.today().isoformat()}",
             f"Cellules de {g.cell_m:.0f} m dans le disque de {g.radius_m / 1000:.0f} km : {res['n_in_disk']} "
             f"(dont {res['n_sea']} mer écartées) -> {n} cellules conservées.",
             f"Hors couverture (départements autres que 30/34) : {(res['coverage_pct'] == 0).sum()} cellules ; "
             f"couverture partielle : {((res['coverage_pct'] > 0) & (res['coverage_pct'] < 100)).sum()}.",
             f"Altitude (cellules couvertes) : min {np.nanmin(res['alt']):.0f} m, médiane {np.nanmedian(res['alt']):.0f} m, "
             f"max {np.nanmax(res['alt']):.0f} m ; altitudes manquantes : {int(np.isnan(res['alt']).sum())}.",
             "", "## Forêt (surface équivalente, km²)"]
    area = res["forest_pct"].astype(np.float64).T @ (res["coverage_pct"] / 100.0) * cell_km2 / 100.0
    for k in np.argsort(area)[::-1]:
        if area[k] > 0:
            lines.append(f"  {res['forest_classes'][k]:22} {area[k]:8.1f}")
    lines.append(f"Codes CODE_TFV non prévus : {res['unknown_codes'] or 'aucun'}")
    lines += ["", f"## Géologie ({res['n_formations']} formations dans l'emprise) — surface par classe (km², emprise rectangulaire)"]
    tot = sum(res["geol_area_km2"].values()) or 1.0
    for cl, a in sorted(res["geol_area_km2"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {cl:12} {a:8.1f}  ({100 * a / tot:4.1f} %)")
    lines.append("Formations non classées les plus étendues (km²) :")
    for a, (nota, descr) in res["geol_unclassified"][:12]:
        lines.append(f"  {a:6.1f}  {nota:10} {descr[:100]}")
    r = tcfg.get("checkpoint_radius_m", 3000)
    lines += ["", f"## Points de contrôle (rayon {r / 1000:.0f} km ; à juger à l'œil, pas des tests)"]
    for cp in tcfg.get("checkpoints", []):
        lines.append(f"- {cp['name']} (attendu : {cp['attente']})\n    {describe_area(res, cp['lat'], cp['lon'], r)}")
    return "\n".join(lines)


def save(res: dict, out_dir: Path, sources_checksums: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    npz = out_dir / "terrain_250m.npz"
    np.savez_compressed(npz, **{k: res[k] for k in ("x", "y", "lat", "lon", "alt", "slope", "north", "east",
                                                    "forest_pct", "geol_pct", "coverage_pct")})
    meta = {"date": date.today().isoformat(), "grid": asdict(res["grid"]), "forest_classes": res["forest_classes"],
            "geol_classes": res["geol_classes"], "n_cells": int(len(res["x"])), "crs": L93,
            "sources_sha256": {k: v.get("sha256") for k, v in sources_checksums.items()}}
    (out_dir / "terrain_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return npz


def run(cfg: dict) -> str:
    from radar import fetch

    raw = resolve("data/raw")
    fetch.extract_all(raw)
    tcfg = load_terrain_cfg()
    res = build(cfg, tcfg, discover_inputs(raw))
    sums = json.loads((raw / "CHECKSUMS.json").read_text(encoding="utf-8")) if (raw / "CHECKSUMS.json").exists() else {}
    out = resolve("data/processed")
    save(res, out, sums)
    txt = report(res, tcfg)
    (out / "terrain_report.txt").write_text(txt, encoding="utf-8")
    return txt
