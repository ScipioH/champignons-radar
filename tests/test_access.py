"""Tests accessibilité (phase 2c) : réseau simulé, rasterisation sur grille synthétique."""
import numpy as np
import pytest

from radar import access as A
from radar.config import load_config, load_env
from radar.habitat import final_score
from radar.terrain import make_grid

CFG = load_config()


def test_origine_centre_par_defaut_et_domicile_par_env(monkeypatch):
    monkeypatch.setattr(A, "load_env", lambda: None)
    monkeypatch.delenv("RADAR_HOME_LAT", raising=False)
    monkeypatch.delenv("RADAR_HOME_LON", raising=False)
    assert A.origin(CFG) == (CFG["center"]["lat"], CFG["center"]["lon"], False)
    monkeypatch.setenv("RADAR_HOME_LAT", "43.7")
    monkeypatch.setenv("RADAR_HOME_LON", "3.8")
    assert A.origin(CFG) == (43.7, 3.8, True)


def test_load_env_n_ecrase_pas(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("# commentaire\nRADAR_TEST_A=1\nRADAR_TEST_B = 'deux'\n", encoding="utf-8")
    monkeypatch.setenv("RADAR_TEST_A", "deja")
    monkeypatch.delenv("RADAR_TEST_B", raising=False)
    load_env(p)
    import os
    assert os.environ["RADAR_TEST_A"] == "deja" and os.environ["RADAR_TEST_B"] == "deux"


def test_osrm_lots_et_non_routable():
    acfg = dict(CFG["access"], batch=2, pause_seconds=0)
    urls = []

    def fake(url):
        urls.append(url)
        n = url.split("/driving/")[1].split("?")[0].count(";")
        return {"code": "Ok", "durations": [[0] + [600.0] * (n - 1) + [None]],
                "destinations": [{"distance": 0}] + [{"distance": 100.0}] * n}
    dur, snap = A.osrm_minutes((3.87, 43.6), [(3.8, 43.7), (3.9, 43.8), (4.0, 43.9)], acfg, get=fake)
    assert len(urls) == 2 and "sources=0" in urls[0]
    assert dur[0] == pytest.approx(10) and np.isnan(dur[1]) and np.isnan(dur[2])
    assert snap.tolist() == pytest.approx([100.0] * 3)


def test_osrm_erreur_remontee():
    with pytest.raises(RuntimeError, match="NoRoute"):
        A.osrm_minutes((3.87, 43.6), [(3.8, 43.7)], dict(CFG["access"], pause_seconds=0),
                       get=lambda u: {"code": "NoRoute", "message": "x"})


def test_temps_marche_ajoute():
    assert A.travel_minutes(np.array([30.0]), np.array([2000.0]), 4.0)[0] == pytest.approx(60.0)


def test_facteur_acces_perimetre_et_zones():
    travel = np.array([30.0, 59.0, 61.0, np.nan, 30.0, 30.0])
    shares = np.array([[0, 0], [0, 0], [0, 0], [0, 0], [1.0, 0], [0, 1.0]], dtype=float)
    f = A.access_factor(travel, shares, np.array([0.0, 1.0]), 60)
    assert f.tolist() == pytest.approx([1, 1, -1, -1, 0, 1])     # réserve exclue, cœur de parc signalé seulement
    s = final_score(np.ones(6), np.ones(6), 1.0, CFG["score"], f)
    assert s[2] == -1 and s[3] == -1 and s[4] == 0 and s[0] == pytest.approx(100)


def test_parts_zones_protegees_rasterisees():
    g = make_grid(43.6108, 3.8767, 1000, 250, 50)
    xmin, ymin, xmax, ymax = g.bbox()
    half = {"type": "Polygon", "coordinates": [[(xmin, ymin), (xmin, ymax), ((xmin + xmax) / 2, ymax),
                                                ((xmin + xmax) / 2, ymin), (xmin, ymin)]]}
    feats = {"rnn": [{"geometry": half, "properties": {"zone": "x"}}],
             "pn": [{"geometry": half, "properties": {"zone": "Adhesion"}}]}      # filtrée par 'where'
    layers = {"rnn": {"factor": 0.0}, "pn": {"factor": 1.0, "where": {"zone": "Cœur"}}}
    rows, cols = np.array([0, 0]), np.array([0, g.ncols - 1])
    sh, names = A.protected_shares(g, (rows, cols), layers, feats)
    assert names == ["rnn", "pn"]
    assert sh[0].tolist() == [1.0, 0.0] and sh[1].tolist() == [0.0, 0.0]
