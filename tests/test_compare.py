"""Tests de la comparaison de modèles (données simulées, sans réseau)."""
from radar import compare, weather
from radar.config import load_config

CFG = load_config()
IDX = 30
N = 37


def make(alt, rain_event=25.0, tmin_base=12.0, gradient=-0.006, miss_forecast=False):
    p = [0.0] * N
    p[IDX - 8] = rain_event
    t = [tmin_base + gradient * alt] * N
    if miss_forecast:
        for i in range(IDX + 2, N):
            p[i] = None
    return {"elevation": alt, "time": [str(i) for i in range(N)], "precip": p, "tmin": t, "tmean": t}


def dataset(**kw):
    return {f"{43 + i / 100:.4f},3.0000": make(100 + 100 * i, **kw) for i in range(8)}


def test_summarize_gradient_et_completude():
    s = compare.summarize(dataset(), IDX, CFG["score"])
    assert s["n_points"] == 8 and s["missing_precip_forecast"] == 0
    assert abs(s["tmin_gradient_c_per_km"] - (-6.0)) < 0.1


def test_summarize_prevision_manquante_comptee():
    s = compare.summarize(dataset(miss_forecast=True), IDX, CFG["score"])
    assert s["missing_precip_forecast"] == 16  # J+2 et J+3 manquants : 2 jours x 8 points


def test_pairwise_identique_puis_different():
    a = compare.summarize(dataset(), IDX, CFG["score"])
    same = compare.pairwise(a, compare.summarize(dataset(), IDX, CFG["score"]))
    assert same["rain15_mean_abs_diff_mm"] == 0 and same["class_differs_pct"] == 0
    b = compare.summarize(dataset(rain_event=2.0), IDX, CFG["score"])
    diff = compare.pairwise(a, b)
    assert diff["rain15_mean_abs_diff_mm"] > 10 and diff["class_differs_pct"] > 0


def test_public_masque_les_lignes():
    assert "_rows" not in compare.public(compare.summarize(dataset(), IDX, CFG["score"]))


def test_build_url_modele_et_cache_separe(tmp_path):
    w = dict(CFG["weather"], model=None)
    assert "models=" not in weather.build_url([(43.0, 3.0)], w)
    w["model"] = "meteofrance_arome_france_hd"
    assert "models=meteofrance_arome_france_hd" in weather.build_url([(43.0, 3.0)], w)
