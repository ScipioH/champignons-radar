"""Tests du prétraitement terrain : fonctions pures + chaîne complète sur fichiers synthétiques (sans réseau)."""
import math

import numpy as np
import pytest
import rasterio
import shapefile
from rasterio.transform import from_origin

from radar import terrain as T
from radar.config import load_config

TCFG = T.load_terrain_cfg()
GEO = TCFG["geology"]


# ----------------------------------------------------------------- reclassement

def test_normalize_apostrophe_et_accents():
    assert T.normalize("Forêt fermée de pin d\x92Alep") == "foret fermee de pin d'alep"


def test_forest_class_codes_reels_et_inconnu():
    m = TCFG["forest"]["code_tfv"]
    assert T.forest_class("FF1-10-10", m) == "chataignier"
    assert T.forest_class("LA4", m) == "lande"          # NC = lande, pas « inconnu »
    assert T.forest_class("FF0", m) == "sans_couvert"   # NR = sans couvert arboré
    assert T.forest_class("ZZ9", m) == "inconnu"
    assert set(m.values()) <= set(TCFG["forest"]["classes"])


@pytest.mark.parametrize("nota,descr,attendu", [
    ("Fz", "Alluvions argilo-sableuses à graviers et galets", "superficiel"),
    ("Fy", "Blocs, galets et graviers de calcaires siliceux, de grès", "superficiel"),  # notation prioritaire
    ("hydro", "Lacs, étangs, cours d'eau", "eau"),
    ("X", "Dépôts anthropiques indifférenciés", "anthropique"),
    ("t7", "Calcaire, dolomie, grès et argiles (Rhétien)", "calcaire"),
    ("t5-6", "Argiles bariolées, niveaux gréso-dolomitiques, gypse", "detritique"),  # 1er cité = argiles
    ("Mγ2-3La", "Granite migmatitique à cordiérite de Laouzas", "acide"),
    ("ζa1", "Gneiss plagioclasiques à biotite", "acide"),
    ("β", "Coulées basaltiques de l'Escandorgue", "basique"),
    ("e4-5P", "Calcaire blanc lacustre à planorbes de Montpellier", "calcaire"),
    ("m3-4m", "Molasse sableuse marine, marnes bleues", "detritique"),
    ("e6", "Formation indéterminée", "autre"),
    ("e6Aco", "Bancs conglomératiques à galets siliceux", "detritique"),  # 'e' minuscule = étage, pas éboulis
    # Corrections issues du diagnostic du 2026-11-03
    ("kGF", "Série flyschoïde gréso-feldspathique (Cambrien)", "acide"),       # grès paléozoïque -> sol acide
    ("k-o(a)", "Série gréso-pélitique (Cambro-ordovicien)", "acide"),
    ("r1", "Pélites rouges et grès (Permien)", "detritique"),                  # Permien exclu de l'ajustement
    ("îãdif", "Microgranites (Carbonifère supérieur) diffus", "acide"),        # mot-clé composé
    ("g1b-2br", "Brèche de faille, brèche des Matelles (Oligocène)", "calcaire"),  # hypothèse régionale
    ("Mv", "Formation littorale à galets (Pléistocène moyen)", "superficiel"),
    ("Ûáo", "Porphyres andésitiques de Gabian", "basique"),
    ("l3-4S", "Formations sporadiques d'entroquites, calcarénites fossilifères", "calcaire"),
])
def test_classify_geology(nota, descr, attendu):
    assert T.classify_geology(nota, descr, GEO) == attendu


def test_mot_cle_en_debut_de_mot_seulement():
    # 'gres' ne doit pas matcher l'intérieur de « progressive » ; 'argile' doit matcher « argileux »
    assert T.classify_geology("z", "Transition progressive", GEO) == "autre"
    assert T.classify_geology("z", "Niveaux argileux", GEO) == "detritique"


# ----------------------------------------------------------------- grille & agrégation

def test_make_grid_alignee_et_couvrante():
    g = T.make_grid(43.6108, 3.8767, 60_000, 250, 50)
    assert g.factor == 5 and g.xmin % 250 == 0 and g.ymax % 250 == 0
    xmin, ymin, xmax, ymax = g.bbox()
    assert xmin <= g.x0 - 60_000 and xmax >= g.x0 + 60_000 and ymin <= g.y0 - 60_000 and ymax >= g.y0 + 60_000
    with pytest.raises(ValueError):
        T.make_grid(43.6, 3.9, 1000, 250, 60)


def test_block_percent_et_couverture():
    codes = np.array([[1, 1, 2, 2], [1, 255, 2, 2]], dtype=np.uint8)
    pct, cov = T.block_percent(codes, 2, 3, 255)
    assert pct[0, 0].tolist() == [0, 100, 0] and cov[0, 0] == 75   # pourcentage parmi les pixels couverts
    assert pct[0, 1].tolist() == [0, 0, 100] and cov[0, 1] == 100


def test_block_nanmean_bloc_vide():
    a = np.array([[1, 3, np.nan, np.nan], [5, 7, np.nan, np.nan]], dtype=np.float32)
    m = T.block_nanmean(a, 2)
    assert m[0, 0] == 4 and np.isnan(m[0, 1])


def test_pente_exposition_plans():
    rows, cols = np.mgrid[0:20, 0:20].astype(float)
    sud = 1000 - 5.0 * rows                      # altitude décroît vers le sud (lignes croissantes) : pente 10 %
    s, a = T.slope_aspect(sud, 50)
    assert np.allclose(s[5:15, 5:15], math.degrees(math.atan(0.1)), atol=1e-4)
    assert np.allclose(a[5:15, 5:15], 180, atol=1e-4)
    est = 1000 - 5.0 * cols                      # décroît vers l'est
    _, a2 = T.slope_aspect(est, 50)
    assert np.allclose(a2[5:15, 5:15], 90, atol=1e-4)
    n, e = T.block_exposure(*T.slope_aspect(sud, 50), 5)
    assert n[1, 1] < -0.09 and abs(e[1, 1]) < 1e-6


# ----------------------------------------------------------------- chaîne complète synthétique

def _poly(w, x0, y0, x1, y1):
    w.poly([[(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]])  # sens horaire = anneau extérieur


def synth_inputs(tmp_path, g):
    xmin, ymin, xmax, ymax = g.bbox()
    xm, ym = (xmin + xmax) / 2, (ymin + ymax) / 2
    fw = shapefile.Writer(str(tmp_path / "FORMATION_VEGETALE"), shapeType=shapefile.POLYGON, encoding="utf-8")
    for name, size in (("ID", 24), ("CODE_TFV", 12), ("TFV", 68), ("TFV_G11", 35), ("ESSENCE", 27)):
        fw.field(name, "C", size)
    _poly(fw, xmin, ymin, xm, ymax)                       # moitié ouest : châtaignier
    fw.record("1", "FF1-10-10", "Forêt fermée de châtaignier pur", "Forêt fermée feuillus", "Châtaignier")
    _poly(fw, xm, ym, xmax, ymax)                         # quart nord-est : code non prévu
    fw.record("2", "ZZ9", "?", "?", "?")
    fw.close()
    gw = shapefile.Writer(str(tmp_path / "G_S_FGEOL_2154"), shapeType=shapefile.POLYGON, encoding="cp1252")
    gw.field("NOTATION", "C", 50)
    gw.field("DESCR", "C", 254)
    _poly(gw, xmin, ym, xmax, ymax)                       # géologie seulement au nord -> sud hors couverture
    gw.record("k1", "Schistes et micaschistes à séricite")
    gw.close()
    # MNT plan incliné vers le sud, en Lambert 93, débordant de l'emprise
    res = 25.0
    nr, nc = int((ymax - ymin) / res) + 40, int((xmax - xmin) / res) + 40
    yy = np.arange(nr)[:, None] * np.ones((1, nc))
    dem = (500.0 - 0.1 * res * yy).astype(np.float32)
    tif = tmp_path / "dem.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=nr, width=nc, count=1, dtype="float32", crs=T.L93,
                       transform=from_origin(xmin - 20 * res, ymax + 20 * res, res, res), nodata=-9999) as dst:
        dst.write(dem, 1)
    return T.Inputs([tmp_path / "FORMATION_VEGETALE.shp"], [tmp_path / "G_S_FGEOL_2154.shp"], [tif])


def test_build_bout_en_bout(tmp_path):
    cfg = load_config()
    cfg["grid"]["radius_km"] = 1.0
    c = cfg["center"]
    g = T.make_grid(c["lat"], c["lon"], 1000, cfg["terrain"]["spacing_m"], TCFG["fine_m"])
    res = T.build(cfg, TCFG, synth_inputs(tmp_path, g))
    fc, gc = res["forest_classes"], res["geol_classes"]
    north = res["coverage_pct"] == 100
    south = res["coverage_pct"] == 0
    assert north.any() and south.any()
    west = res["x"] < g.x0 - 300
    nw = north & west
    assert (res["forest_pct"][nw, fc.index("chataignier")] == 100).all()
    assert (res["geol_pct"][north, gc.index("acide")] == 100).all()
    # Sud hors couverture géologique : conservé (altitude > 1 m) mais aucune info forêt/géologie
    assert (res["forest_pct"][south].sum(axis=1) == 0).all() and (res["geol_pct"][south].sum(axis=1) == 0).all()
    assert res["unknown_codes"] == ["ZZ9"]
    # Relief : pente ~5,7°, exposition sud (composante nord négative)
    assert np.allclose(res["slope"][north], math.degrees(math.atan(0.1)), atol=0.2)
    assert (res["north"][north] < -0.09).all()
    # Rapport et sauvegarde
    txt = T.report(res, {"checkpoints": [{"name": "centre", "lat": c["lat"], "lon": c["lon"], "attente": "?"}]})
    assert "ZZ9" in txt and "centre" in txt
    npz = T.save(res, tmp_path / "out", {})
    with np.load(npz) as d:
        assert d["forest_pct"].shape == (len(res["x"]), len(fc))
