"""Tests foncier : identifiants cadastraux, fichier DGFiP (synthétique), flux GeoJSON, classes et facteur."""
import gzip
import json
import zipfile

import numpy as np
import pytest
from pyproj import Transformer
from rasterio.transform import from_origin

from radar import land as L


def test_identifiant_format_etalab():
    assert L.parcel_id("34", "005", "", " A", "351") == "340050000A0351"
    assert L.parcel_id("30", "189", "012", "AB", "0007") == "30189012AB0007"   # commune absorbée (préfixe)


HEADER = ("Département;Code Direction;Code Commune;Nom de la commune;Préfixe;Section;N° plan;N° voirie;"
          "Indice de répétition;Code voie MAJIC;Code voie Rivoli;Nature voie;Nom voie;Contenance;SUF;Nature culture;"
          "Contenance;Code droit - par;N° Majic - par;N° SIREN - par;Groupe personne - par;Forme juridique - par;"
          "Forme juridique abrégée - par;Dénomination - par")


def row(commune, section, plan, groupe, forme):
    return (f"34;0;{commune};X;; {section};{plan};;;;;;;100;A;B - Bois;100;P - Propriétaire;P1;1;"
            f"{groupe};{forme};ABR;NOM")


def make_zip(tmp_path):
    lines = [HEADER, row("005", "A", "0351", "4 - Commune", "7210"),
             row("005", "A", "0352", "0 - Personnes morales non remarquables", "6534"),        # GFA : privé
             row("005", "B", "0010", "0 - Personnes morales non remarquables", "7229"),        # collectivité
             row("006", "C", "0001", "9 - Établissements publics ou organismes associés", "7389")]
    p = tmp_path / "pm_test.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("dossier/PM_26_NB_340.csv", "\n".join(lines).encode("utf-8"))
        z.writestr("dossier/PM_26_NB_330.csv", "\n".join([HEADER, row("001", "A", "1", "4 - Commune", "7210")]))
    return p


def test_parcelles_publiques(tmp_path):
    ids, stats = L.public_ids([make_zip(tmp_path)], ["34"], {"1", "2", "3", "4", "9"})
    assert ids == {"340050000A0351", "340050000B0010", "340060000C0001"}   # 33 ignoré, GFA privé
    assert stats["groupe 0"] == 1 and stats["groupe 0 public"] == 1


def gz_geojson(tmp_path, name, feats):
    p = tmp_path / name
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection","features":[\n')
        fh.write(",\n".join(json.dumps(f, separators=(",", ":")) for f in feats))
        fh.write("]}\n")   # comme Etalab : la fin de collection est collée à la dernière entité
    return p


def square(lon, lat, d=0.001, pid=None):
    f = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat],
         [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}, "properties": {}}
    if pid:
        f["id"] = pid
    return f


def test_flux_filtre_emprise(tmp_path):
    p = gz_geojson(tmp_path, "c.json.gz", [square(3.8, 43.6, pid="a"), square(5.0, 45.0, pid="b")])
    got = [f["id"] for f in L.iter_features(p, (3.0, 43.0, 4.5, 44.5))]
    assert got == ["a"]


def test_reprojection_par_lot():
    tr = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    g1, g2 = square(3.8, 43.6)["geometry"], {"type": "MultiPolygon", "coordinates": [square(3.9, 43.7)["geometry"]["coordinates"]]}
    out = L.to_l93([g1, g2], tr)
    x, y = tr.transform(3.8, 43.6)
    assert out[0]["type"] == "Polygon" and out[0]["coordinates"][0][0] == pytest.approx([x, y])
    assert out[1]["type"] == "MultiPolygon" and len(out[1]["coordinates"][0][0]) == 5


def test_rasterisation_public_prive(tmp_path):
    tr = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    x0, y0 = tr.transform(3.8, 43.6)
    transform = from_origin(x0 - 50, y0 + 300, 10.0, 10.0)
    out = np.zeros((40, 40), dtype=np.uint8)
    p = gz_geojson(tmp_path, "p.json.gz", [square(3.8, 43.6, 0.002, "pub"), square(3.803, 43.6, 0.002, "priv")])
    st = L.burn(p, out, transform, (3.0, 43.0, 4.5, 44.5), lambda f: L.PUBLIC if f["id"] == "pub" else L.PRIVATE)
    assert st[L.PUBLIC] == 1 and st[L.PRIVATE] == 1
    assert (out == L.PUBLIC).sum() > 100 and (out == L.PRIVATE).sum() > 100


def test_abords_bati_et_facteur():
    cls = np.full((10, 10), L.PRIVATE, dtype=np.uint8)
    cls[:, :5] = L.PUBLIC
    b = np.zeros((10, 10), dtype=bool)
    b[0, 9] = True
    cls[L.near_mask(b, 2)] = L.BUILT
    assert cls[0, 7] == L.BUILT and cls[2, 9] == L.BUILT and cls[5, 9] == L.PRIVATE
    sh = L.class_shares(cls, 5)
    assert sh["public"][0, 0] == 1.0 and sh["bati"][0, 1] > 0
    f = L.land_factor(sh, 0.3)
    assert f[0, 0] == 1.0 and f[1, 1] == pytest.approx(0.3) and f[0, 1] < 0.3


def test_facteur_non_cadastre_neutre():
    sh = {"public": np.array([0.0]), "prive": np.array([0.0]), "bati": np.array([0.0]), "non_cadastre": np.array([1.0])}
    assert L.land_factor(sh, 0.0)[0] == 1.0
