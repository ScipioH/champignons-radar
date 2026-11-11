"""Foncier : part de chaque cellule en terrain public, privé, abords bâtis (maisons, jardins) ou non cadastré.

Sources (Licence Ouverte 2.0, téléchargées une fois dans data/raw/proprietes) :
- parcelles et bâtiments du cadastre (Etalab, GeoJSON par département, une entité par ligne) ;
- fichiers des parcelles des personnes morales (DGFiP) : propriétaire public si « groupe personne » ∈
  public_groups (État, région, département, commune, établissement public). Les parcelles des particuliers ne
  sont pas publiées : une parcelle absente de ce fichier est donc privée (ou à une société).
Chaîne : rasterisation à `fine_m` (10 m) sur l'emprise de la grille, puis :
- « abords bâtis » = tout pixel à moins de `built_buffer_m` d'un bâtiment (maison + jardin, hameau, village),
  quel que soit le propriétaire ;
- sinon public / privé selon la parcelle, ou non cadastré (routes, cours d'eau : domaine public).
Facteur de score = part publique + part non cadastrée + private_factor × part privée (abords bâtis : 0).
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import math
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin

from radar.config import resolve
from radar.terrain import L93, GridSpec, blocks

log = logging.getLogger("radar.land")
UNCAD, PUBLIC, PRIVATE, BUILT = 0, 1, 2, 3
CLASSES = {"public": PUBLIC, "prive": PRIVATE, "bati": BUILT, "non_cadastre": UNCAD}


# --------------------------------------------------------------------------- identifiants

def parcel_id(dep: str, commune: str, prefixe: str, section: str, numero: str) -> str:
    """Identifiant cadastral à 14 caractères, au format Etalab (ex. 340050000A0351).

    `commune` = code commune à 3 chiffres (sans le département)."""
    pre = (prefixe or "").strip() or "000"
    return f"{dep[:2]}{commune.strip().zfill(3)[-3:]}{pre.zfill(3)}{section.strip().rjust(2, '0')}{numero.strip().zfill(4)}"


def public_ids(zip_paths: list[Path], deps: list[str], groups: set[str]) -> tuple[set[str], Counter]:
    """Identifiants des parcelles dont un propriétaire est public, lus dans les fichiers DGFiP (CSV « ; »)."""
    ids: set[str] = set()
    stats: Counter = Counter()
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                stem = Path(name).stem
                if not name.lower().endswith(".csv") or not any(stem.endswith(f"_{d}0") or stem.endswith(f"_{d}")
                                                                for d in deps):
                    continue
                with z.open(name) as fh:
                    rows = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""), delimiter=";")
                    header = [h.strip().lower() for h in next(rows)]
                    col = {k: _find(header, *v) for k, v in COLUMNS.items()}
                    for r in rows:
                        if len(r) < len(header):
                            continue
                        g = r[col["groupe"]].strip()[:1]
                        # forme juridique 7xxx = personne morale de droit public (collectivité, section de
                        # commune, établissement public administratif), parfois classée en groupe 0
                        public = g in groups or r[col["forme"]].strip().startswith("7")
                        stats[f"groupe {g}{' public' if public else ''}"] += 1
                        if not public:
                            continue
                        dep = r[col["dep"]].strip().zfill(2)
                        ids.add(parcel_id(dep, r[col["commune"]], r[col["prefixe"]], r[col["section"]],
                                          r[col["numero"]]))
    return ids, stats


# en-têtes DGFiP (libellés longs, variables selon les millésimes) : on cherche des mots-clés
COLUMNS = {
    "dep": ("département",),
    "commune": ("code commune", "commune"),
    "prefixe": ("préfixe",),
    "section": ("section",),
    "numero": ("n° plan", "numéro de plan", "plan"),
    "groupe": ("groupe",),
    "forme": ("forme juridique",),   # la 1re trouvée est la forme complète (code INSEE à 4 chiffres)
}


def _find(header: list[str], *keys: str) -> int:
    for k in keys:
        for i, h in enumerate(header):
            if k in h:
                return i
    raise RuntimeError(f"colonne introuvable ({keys}) dans l'en-tête DGFiP : {header}")


# --------------------------------------------------------------------------- géométries (flux GeoJSON)

def iter_features(path: Path, bbox_wgs: tuple[float, float, float, float]):
    """Entités d'un GeoJSON Etalab gzippé (une par ligne), gardées si leur 1er sommet est dans bbox_wgs."""
    w, s, e, n = bbox_wgs
    dec = json.JSONDecoder()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith('{"type":"Feature"'):
                continue
            f, _ = dec.raw_decode(line)   # 1er objet de la ligne ; ignore la « , » ou le « ]} » final
            g = f.get("geometry") or {}
            c = g.get("coordinates")
            if g.get("type") not in ("Polygon", "MultiPolygon") or not c:
                continue
            p = c[0][0] if g["type"] == "Polygon" else c[0][0][0]
            if w <= p[0] <= e and s <= p[1] <= n:
                yield f


def to_l93(geoms: list[dict], tr: Transformer) -> list[dict]:
    """Reprojette un lot de polygones WGS84 -> Lambert 93 en un seul appel pyproj."""
    rings = []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            rings.extend(poly)
    if not rings:
        return []
    xy = np.concatenate([np.asarray(r, dtype=np.float64) for r in rings])
    X, Y = tr.transform(xy[:, 0], xy[:, 1])
    out, k = [], 0
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        new = []
        for poly in polys:
            nr = []
            for r in poly:
                m = len(r)
                nr.append(np.column_stack([X[k:k + m], Y[k:k + m]]).tolist())
                k += m
            new.append(nr)
        out.append({"type": "Polygon", "coordinates": new[0]} if g["type"] == "Polygon"
                   else {"type": "MultiPolygon", "coordinates": new})
    return out


def burn(path: Path, out: np.ndarray, transform, bbox_wgs, value_of, chunk: int = 20000) -> Counter:
    """Rasterise les entités du fichier dans `out` ; value_of(feature) -> valeur (ou None pour ignorer)."""
    tr = Transformer.from_crs("EPSG:4326", L93, always_xy=True)
    stats: Counter = Counter()
    batch_g, batch_v = [], []

    def flush():
        if batch_g:
            features.rasterize(zip(to_l93(batch_g, tr), batch_v), out=out, transform=transform)
            batch_g.clear()
            batch_v.clear()
    for f in iter_features(path, bbox_wgs):
        v = value_of(f)
        stats[v] += 1
        if v is None:
            continue
        batch_g.append(f["geometry"])
        batch_v.append(v)
        if len(batch_g) >= chunk:
            flush()
    flush()
    return stats


# --------------------------------------------------------------------------- calcul (pur numpy)

def near_mask(mask: np.ndarray, steps: int) -> np.ndarray:
    """Pixels à au plus `steps` pixels (métrique octogonale) d'un pixel True."""
    reached = mask.astype(bool).copy()
    for k in range(1, steps + 1):
        g = reached.copy()
        g[1:, :] |= reached[:-1, :]
        g[:-1, :] |= reached[1:, :]
        g[:, 1:] |= reached[:, :-1]
        g[:, :-1] |= reached[:, 1:]
        if k % 2:
            g[1:, 1:] |= reached[:-1, :-1]
            g[1:, :-1] |= reached[:-1, 1:]
            g[:-1, 1:] |= reached[1:, :-1]
            g[:-1, :-1] |= reached[1:, 1:]
        reached = g
    return reached


def class_shares(cls: np.ndarray, f: int) -> dict[str, np.ndarray]:
    """Part (0-1) de chaque classe par bloc f × f."""
    b = blocks(cls, f)
    return {k: (b == v).mean(axis=2).astype(np.float32) for k, v in CLASSES.items()}


def land_factor(shares: dict[str, np.ndarray], private_factor: float) -> np.ndarray:
    return np.clip(shares["public"] + shares["non_cadastre"] + private_factor * shares["prive"], 0.0, 1.0)


# --------------------------------------------------------------------------- exécution

def run(cfg: dict) -> str:
    from radar.habitat import load_terrain

    lcfg = cfg["land"]
    raw = resolve(lcfg["dir"])
    t, meta = load_terrain()
    g = GridSpec(**meta["grid"])
    fm = float(lcfg["fine_m"])
    f = int(round(g.cell_m / fm))
    if abs(f * fm - g.cell_m) > 1e-6:
        raise ValueError("land.fine_m doit diviser la taille de cellule")
    shape = (g.nrows * f, g.ncols * f)
    transform = from_origin(g.xmin, g.ymax, fm, fm)
    xmin, ymin, xmax, ymax = g.bbox()
    to_wgs = Transformer.from_crs(L93, "EPSG:4326", always_xy=True)
    lons, lats = to_wgs.transform([xmin, xmax, xmin, xmax], [ymin, ymin, ymax, ymax])
    bbox_wgs = (min(lons) - 0.01, min(lats) - 0.01, max(lons) + 0.01, max(lats) + 0.01)

    zips = sorted(raw.glob(lcfg["owners_glob"]))
    if not zips:
        raise FileNotFoundError(f"fichiers DGFiP absents ({raw / lcfg['owners_glob']})")
    pub, owner_stats = public_ids(zips, lcfg["departements"], set(lcfg["public_groups"]))
    log.info("%d parcelles à propriétaire public (%s)", len(pub), dict(owner_stats))

    cls = np.full(shape, UNCAD, dtype=np.uint8)
    pstats: Counter = Counter()
    for d in lcfg["departements"]:
        p = raw / f"cadastre-{d}-parcelles.json.gz"
        pstats += burn(p, cls, transform, bbox_wgs, lambda ft: PUBLIC if ft.get("id") in pub else PRIVATE)
        log.info("parcelles %s : %s", d, dict(pstats))
    built = np.zeros(shape, dtype=np.uint8)
    bstats: Counter = Counter()
    for d in lcfg["departements"]:
        bstats += burn(raw / f"cadastre-{d}-batiments.json.gz", built, transform, bbox_wgs, lambda ft: 1)
    steps = int(math.ceil(lcfg["built_buffer_m"] / fm))
    cls[near_mask(built > 0, steps)] = BUILT
    del built

    sh = class_shares(cls, f)
    rows = np.floor((g.ymax - t["y"]) / g.cell_m).astype(int)
    cols = np.floor((t["x"] - g.xmin) / g.cell_m).astype(int)
    cell = {k: v[rows, cols] for k, v in sh.items()}
    out = resolve("data/processed")
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "land_250m.npz", **cell)
    (out / "land_meta.json").write_text(json.dumps({
        "date": date.today().isoformat(), "n_cells": int(len(t["x"])),
        "params": {k: lcfg[k] for k in ("fine_m", "built_buffer_m", "public_groups")},
        "n_public_parcels": len(pub), "parcels": {str(k): v for k, v in pstats.items()},
        "buildings": int(sum(bstats.values())), "owner_rows": dict(owner_stats),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    known = t["coverage_pct"] > 0
    lines = [f"# Foncier — {date.today().isoformat()}",
             f"Parcelles à propriétaire public (DGFiP, groupes {', '.join(lcfg['public_groups'])}) : {len(pub)}.",
             f"Bâtiments : {sum(bstats.values())} ; abords bâtis = moins de {lcfg['built_buffer_m']} m d'un bâtiment.",
             "Part de la surface (cellules couvertes) : " + ", ".join(
                 f"{k} {float(cell[k][known].mean()):.1%}" for k in CLASSES)]
    txt = "\n".join(lines)
    (out / "land_report.txt").write_text(txt, encoding="utf-8")
    return txt


def load(n_cells: int, dirpath: Path | None = None) -> tuple[dict, dict] | None:
    d = dirpath or resolve("data/processed")
    if not (d / "land_250m.npz").exists():
        return None
    meta = json.loads((d / "land_meta.json").read_text(encoding="utf-8"))
    if meta.get("n_cells") != n_cells:
        log.warning("foncier calculé sur une autre grille : ignoré (relancer `python -m radar foncier`)")
        return None
    with np.load(d / "land_250m.npz") as z:
        return {k: z[k] for k in z.files}, meta
