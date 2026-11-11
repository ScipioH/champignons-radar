"""Tests accès à pied : distance aux voies, pixels difficiles, facteur, WFS par dalles (réseau simulé)."""
import json
from collections import Counter

import numpy as np
import pytest

from radar import foot as F
from radar.terrain import GridSpec

FCFG = {"flat_deg": 15, "steep_deg": 30, "near_m": 50, "near_relief": 0.5, "far_m": 1000, "min_factor": 0.5,
        "count": 3, "wfs_url": "https://x/wfs", "typename": "T"}


def test_distance_octogonale():
    m = np.zeros((41, 41), dtype=bool)
    m[20, 20] = True
    d = F.octagonal_distance(m, 50.0, 1000.0)
    assert d[20, 20] == 0
    assert d[20, 21] == 50 and d[20, 30] == 500          # axe : exact
    diag = d[30, 30]                                       # vraie distance : 10 * 50 * sqrt(2) = 707 m
    assert abs(diag - 707.1) / 707.1 < 0.09
    m2 = np.zeros((5, 60), dtype=bool)
    m2[:, 0] = True
    assert np.isinf(F.octagonal_distance(m2, 50.0, 1000.0)[0, 59])   # au-delà du plafond : inf


def test_pixels_difficiles():
    slope = np.array([[35.0, 35.0, 22.5, 10.0, 10.0, np.nan]])
    dist = np.array([[50.0, 300.0, 300.0, 300.0, 1500.0, 300.0]])
    h = F.hard_pixels(slope, dist, FCFG)
    # raide au bord d'une voie : moitié ; raide loin : 1 ; pente moyenne : rampe ; doux : 0 ; très loin : 1
    assert h[0, :5].tolist() == [0.5, 1.0, 0.5, 0.0, 1.0] and np.isnan(h[0, 5])


def test_facteur_borne_et_nan_neutre():
    f = F.foot_factor(np.array([0.0, 0.5, 1.0, np.nan, 2.0]), 0.5)
    assert f.tolist() == [1.0, 0.75, 0.5, 1.0, 0.5]


def test_dalle_saturee_decoupee():
    calls = []

    def get(url):
        calls.append(url)
        n = 3 if len(calls) == 1 else 1        # 1re réponse = limite atteinte -> 4 sous-dalles
        return {"features": [{"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                              "properties": {"nature": "Sentier"}}] * n}
    out = F.fetch_tile(FCFG, (0, 0, 1000, 1000), get)
    assert len(calls) == 5 and len(out) == 4
    assert "PROPERTYNAME=nature%2Cgeometrie" in calls[0] or "PROPERTYNAME=nature,geometrie" in calls[0]


def test_reponse_invalide():
    with pytest.raises(RuntimeError):
        F.fetch_tile(FCFG, (0, 0, 1, 1), lambda u: {"type": "ExceptionReport"})


def grid():
    return GridSpec(xmin=0.0, ymax=1000.0, fine_m=50.0, factor=5, nrows=4, ncols=4, x0=500.0, y0=500.0,
                    radius_m=400.0)


def test_rasterisation_exclut_autoroutes():
    g = grid()
    out = np.zeros(g.fine_shape, dtype=np.uint8)
    c = Counter()
    feats = [{"geometry": {"type": "LineString", "coordinates": [[0, 975], [1000, 975]]}, "properties": {"nature": "Chemin"}},
             {"geometry": {"type": "LineString", "coordinates": [[0, 25], [1000, 25]]},
              "properties": {"nature": "Type autoroutier"}},
             {"geometry": {"type": "Point", "coordinates": [500, 500]}, "properties": {"nature": "Sentier"}}]
    F.burn_paths(feats, g, out, {"Type autoroutier"}, c)
    assert out[0].all() and not out[-1].any() and out.sum() == 20
    assert c == Counter({"Chemin": 1, "Type autoroutier": 1})


def test_dalles_dans_le_disque():
    g = GridSpec(xmin=0.0, ymax=10000.0, fine_m=50.0, factor=5, nrows=40, ncols=40, x0=5000.0, y0=5000.0,
                 radius_m=2000.0)
    ts = F.tiles(g, 2500.0)
    assert len(ts) == 4                       # les dalles d'angle (loin du disque) sont écartées
    assert all(b[2] > b[0] and b[3] > b[1] for b in ts)


def test_chargement_refuse_autre_grille(tmp_path):
    np.savez_compressed(tmp_path / "foot_250m.npz", hard_share=np.zeros(3), steep_share=np.zeros(3),
                        path_dist_m=np.zeros(3))
    (tmp_path / "foot_meta.json").write_text(json.dumps({"n_cells": 3}), encoding="utf-8")
    assert F.load(3, tmp_path) is not None
    assert F.load(4, tmp_path) is None
