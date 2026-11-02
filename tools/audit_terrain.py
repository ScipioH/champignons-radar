"""Audit reproductible des sources de terrain (phase 2a) : extraction + champs + valeurs réelles.

Usage : python -I tools/audit_terrain.py data/raw
À lancer AVANT d'écrire ou de modifier le reclassement (on audite la structure réelle, on ne la suppose pas).
Les archives sont des données non fiables : extraction dans data/raw/extracted/<nom>/, jamais dans le dépôt.

Encodages constatés (2026-11-02) : BD Forêt = UTF-8 strict (apostrophe stockée en U+0092 à normaliser) ;
géologie BRGM = cp1252 (l'UTF-8 échoue).
"""
from __future__ import annotations

import sys
import zipfile
from collections import Counter
from pathlib import Path

import py7zr
import shapefile

sys.stdout.reconfigure(encoding="utf-8")


def extract_all(raw: Path) -> Path:
    out = raw / "extracted"
    for arc in sorted(raw.rglob("*")):
        if arc.suffix not in (".7z", ".zip") or "extracted" in arc.parts:
            continue
        dest = out / arc.stem
        if dest.exists():
            continue
        dest.mkdir(parents=True)
        if arc.suffix == ".7z":
            with py7zr.SevenZipFile(arc) as z:
                z.extractall(dest)
        else:
            with zipfile.ZipFile(arc) as z:
                z.extractall(dest)
        print("extrait", arc.name)
    return out


def records(shp: Path, encoding: str):
    r = shapefile.Reader(str(shp), encoding=encoding)
    names = [f[0] for f in r.fields[1:]]
    return names, (dict(zip(names, rec)) for rec in r.iterRecords())


def main(raw_dir: str) -> None:
    out = extract_all(Path(raw_dir))
    ess, g11 = Counter(), Counter()
    for shp in out.rglob("FORMATION_VEGETALE.shp"):
        names, recs = records(shp, "utf-8")
        print(f"\n{shp.parent.parent.parent.parent.name}: champs {names}")
        for d in recs:
            ess[d["ESSENCE"]] += 1
            g11[d["TFV_G11"]] += 1
    print("\nESSENCE :", *(f"\n  {v:6d} {k!r}" for k, v in ess.most_common()))
    print("\nTFV_G11 :", *(f"\n  {v:6d} {k!r}" for k, v in g11.most_common()))
    geo = Counter()
    for shp in out.rglob("*_S_FGEOL_2154.shp"):
        _, recs = records(shp, "cp1252")
        for d in recs:
            geo[(d["NOTATION"], d["DESCR"])] += 1
    print(f"\nGEOLOGIE : {len(geo)} formations distinctes ; 40 plus fréquentes :")
    for (nota, descr), v in geo.most_common(40):
        print(f"  {v:5d} {nota!s:10} {(descr or '')[:110]}")


if __name__ == "__main__":
    main(sys.argv[1])
