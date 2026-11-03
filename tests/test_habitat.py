"""Tests de l'habitat (phase 2b), du score final et de la carte raster (sans réseau, données synthétiques)."""
import base64
import copy
import zlib

import numpy as np
import pytest

from radar import habitat as H
from radar import rastermap as R
from radar import terrain as T
from radar.config import load_config
from radar.score import classify

SCFG = H.load_species()
TCFG = T.load_terrain_cfg()
FC = TCFG["forest"]["classes"]
GC = TCFG["geology"]["classes"]
CFG = load_config()


def cells(forest: dict, geol: dict, alt=500.0, north=0.0, east=0.0, cov=100) -> dict:
    """Une cellule synthétique : dictionnaires classe -> pourcentage."""
    fp = np.zeros((1, len(FC)), dtype=np.uint8)
    gp = np.zeros((1, len(GC)), dtype=np.uint8)
    for k, v in forest.items():
        fp[0, FC.index(k)] = v
    for k, v in geol.items():
        gp[0, GC.index(k)] = v
    return {"forest_pct": fp, "geol_pct": gp, "alt": np.array([alt]), "north": np.array([north]),
            "east": np.array([east]), "coverage_pct": np.array([cov])}


def hab(fam: str, c: dict, month: int = 11) -> float:
    return float(H.habitat_family(c, SCFG["families"][fam], SCFG, FC, GC, month)[0])


# ----------------------------------------------------------------- config

def test_config_complete_pour_toutes_les_familles():
    for fam in SCFG["families"].values():
        fw = H.forest_weights(fam, SCFG, FC)
        gw = H.geology_weights(fam, SCFG, GC)
        assert fw.shape == (len(FC),) and gw.shape == (len(GC),)
        assert fw[FC.index("inconnu")] == SCFG["forest_unknown_prior"] > 0      # inconnu : jamais 0
        assert gw[GC.index("autre")] == SCFG["geology_unknown_prior"] > 0


def test_classe_sans_poids_refusee():
    fam = copy.deepcopy(SCFG["families"]["cepes"])
    del fam["forest"]["hetre"]
    with pytest.raises(ValueError, match="hetre"):
        H.forest_weights(fam, SCFG, FC)


def test_foret_ouverte_derivee():
    fam = SCFG["families"]["cepes"]
    fw = H.forest_weights(fam, SCFG, FC)
    assert fw[FC.index("feuillus_ouverts")] == pytest.approx(fam["forest"]["feuillus_melanges"] * fam["open_factor"])
    assert fw[FC.index("mixte_ouvert")] == pytest.approx(fam["forest"]["mixte"] * fam["open_factor"])


# ----------------------------------------------------------------- facteurs

def test_altitude_plateau_rampe_et_inconnue():
    a = {"ideal_from": 200, "ideal_to": 1200, "ramp_m": 300, "edge": 0.5}
    f = H.altitude_factor(np.array([500, 200, 50, -200, 1350, 2000, np.nan]), a)
    assert f.tolist() == pytest.approx([1, 1, 0.75, 0.5, 0.75, 0.5, 1])


def test_exposition_faible_et_saisonniere():
    e = SCFG["exposure"]
    n = np.array([0.34, -0.34, 0.0, np.nan])
    f = H.exposure_factor(n * np.cos(np.radians(45)), n * np.sin(np.radians(45)), 10, e)
    assert f[0] == pytest.approx(1 + e["amplitude"]) and f[1] == pytest.approx(1 - e["amplitude"])
    assert f[2] == 1 and f[3] == 1
    assert (H.exposure_factor(n, n, 12, e) == 1).all()     # hors des mois concernés : neutre


def test_saison_voisinage_circulaire():
    s = SCFG["season"]
    assert set(s) == {"in_season", "shoulder", "off_season"}
    assert H.season_factor(11, [9, 10, 11], s) == s["in_season"]
    assert H.season_factor(12, [9, 10, 11], s) == s["shoulder"]
    assert H.season_factor(8, [9, 10, 11], s) == s["shoulder"]
    assert H.season_factor(3, [9, 10, 11], s) == s["off_season"]
    assert H.season_factor(1, [10, 11, 12], s) == s["shoulder"]   # décembre -> janvier


# ----------------------------------------------------------------- habitat : cas métier

def test_chataigneraie_sur_socle_acide_ideale_pour_cepes():
    c = cells({"chataignier": 100}, {"acide": 100})
    assert hab("cepes", c) == pytest.approx(1.0)
    assert hab("lactaires", c) == 0.0


def test_pinede_alep_calcaire_ideale_pour_lactaires():
    c = cells({"pin_alep": 100}, {"calcaire": 100}, alt=100)
    assert hab("lactaires", c) == pytest.approx(1.0)
    assert hab("cepes", c) < 0.1


def test_lande_et_urbain_nuls_mais_pas_inconnus():
    for f in ({"lande": 100}, {"hors_foret": 100}, {"sans_couvert": 100}):
        assert all(hab(k, cells(f, {"calcaire": 100})) == 0 for k in SCFG["families"])


def test_essence_non_precisee_prior_intermediaire():
    v = hab("cepes", cells({"feuillus_melanges": 100}, {"acide": 100}))
    assert 0.3 <= v <= 0.7


def test_hors_couverture_inconnu():
    c = cells({}, {}, cov=0)
    assert all(np.isnan(hab(k, c)) for k in SCFG["families"])


def test_cellule_mixte_ponderee_par_les_parts():
    c = cells({"chataignier": 50, "lande": 50}, {"acide": 100})
    assert hab("cepes", c) == pytest.approx(0.5)


# ----------------------------------------------------------------- score final

def test_exposant_habitat_un_garrigue_pelee_reste_faible():
    sc = CFG["score"]
    assert sc["exponent_habitat"] == 1.0
    f = H.final_score(np.array([1.0]), np.array([0.1]), 1.0, sc)
    assert f[0] == pytest.approx(10) and classify(f[0], sc) == "faible"


def test_score_final_propage_inconnu_et_saison():
    sc = CFG["score"]
    f = H.final_score(np.array([0.8, 0.8]), np.array([np.nan, 1.0]), 0.5, sc)
    assert np.isnan(f[0]) and f[1] == pytest.approx(100 * 0.8 ** sc["exponent_meteo"] * 0.5)


def test_idw_exact_aux_points_et_borne():
    px, py, pv = np.array([0.0, 10000.0]), np.array([0.0, 0.0]), np.array([0.2, 0.8])
    v = H.idw(px, py, pv, np.array([0.0, 10000.0, 5000.0, 2000.0]), np.zeros(4), k=2)
    assert v[0] == pytest.approx(0.2, abs=1e-6) and v[1] == pytest.approx(0.8, abs=1e-6)
    assert v[2] == pytest.approx(0.5) and 0.2 < v[3] < 0.5


# ----------------------------------------------------------------- critères & carte

def synth_terrain():
    """Grille 1-D de 4 cellules : châtaigneraie acide, pinède calcaire, urbain, hors couverture."""
    rows = [cells({"chataignier": 100}, {"acide": 100}), cells({"pin_alep": 100}, {"calcaire": 100}, alt=50),
            cells({"hors_foret": 100}, {"calcaire": 100}, alt=50), cells({}, {}, cov=0)]
    t = {k: np.concatenate([r[k] for r in rows]) for k in rows[0]}
    t["x"] = np.array([0.0, 250.0, 500.0, 750.0]) + 700_000
    t["y"] = np.full(4, 6_280_000.0 - 125)
    return t


def test_check_criteria_sur_terrain_synthetique():
    t = synth_terrain()
    hb = H.compute_habitat(t, SCFG, FC, GC, 11)
    scfg = copy.deepcopy(SCFG)
    scfg["criteria"]["top_fraction"] = 0.34   # une cellule sur trois connues
    # zones recentrées sur les cellules synthétiques
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(T.L93, "EPSG:4326", always_xy=True).transform(t["x"], t["y"])
    scfg["criteria"]["zones"]["gardiole"].update(lat=lat[1], lon=lon[1], radius_m=100)
    scfg["criteria"]["zones"]["montpellier"].update(lat=lat[2], lon=lon[2], radius_m=100)
    res = {c["id"]: c["ok"] for c in H.check_criteria(t, hb, scfg, FC, GC)}
    assert res == {"a": True, "b": True, "c": True, "d": True}


def test_carte_raster_encodage_et_html(tmp_path):
    t = synth_terrain()
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(T.L93, "EPSG:4326", always_xy=True).transform(t["x"], t["y"])
    t["lat"], t["lon"] = np.asarray(lat), np.asarray(lon)
    grid = {"xmin": 700_000.0, "ymax": 6_280_000.0, "fine_m": 50.0, "factor": 5, "nrows": 1, "ncols": 4}
    idx, bounds = R.display_grid(t, grid)
    assert set(np.unique(idx)) <= {-1, 0, 1, 2, 3} and {0, 1, 2, 3} <= set(np.unique(idx))
    vals = R.encode_values(np.array([1.0, 0.25, 0.0, np.nan]))
    assert vals.tolist() == [100, 25, 0, R.UNKNOWN]
    px = np.frombuffer(zlib.decompress(base64.b64decode(R.pack(vals, idx))), dtype=np.uint8)
    assert px.size == idx.size and R.OUTSIDE in px and R.UNKNOWN in px
    out = R.write(tmp_path / "w" / "index.html", layers={"h": {"label": "Habitat", "kind": "habitat",
                                                                 "values": np.array([1.0, 0.25, 0.0, np.nan])}},
                  idx=idx, bounds=bounds, points=[{"lat": 43.6, "lon": 3.9, "score": 50, "classe": "moyen"}],
                  date_="2026-11-04", center=(43.6, 3.9), classes=CFG["score"]["classes"])
    html = out.read_text(encoding="utf-8")
    assert "pas de comestibilité" in html and "CC BY 4.0" in html and "BD Charm-50" in html and "__" not in html
