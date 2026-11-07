"""Bilan d'eau du sol (composante sécheresse) et repli sur le cache météo quand Open-Meteo est injoignable."""
import json
from datetime import date

import pytest

from radar import cli, rastermap, score, weather
from radar.config import load_config

CFG = load_config()
SC = CFG["score"]


def test_bilan_eau_seau_borne_et_evaporation():
    c = SC["secheresse"]
    n = 20
    # 100 mm le premier jour : la réserve plafonne à la capacité, puis s'évapore de coef × ET0 par jour
    precip = [100.0] + [0.0] * (n - 1)
    et0 = [2.0] * n
    r0 = score.soil_ratio(precip, et0, 0, SC)
    assert r0 == pytest.approx((c["bucket_mm"] - c["et_coef"] * 2.0) / c["bucket_mm"])
    r10 = score.soil_ratio(precip, et0, 10, SC)
    assert r10 == pytest.approx((c["bucket_mm"] - 11 * c["et_coef"] * 2.0) / c["bucket_mm"])
    # jamais négatif
    assert score.soil_ratio([0.0] * n, [10.0] * n, n - 1, SC) == 0.0


def test_secheresse_depend_de_l_evaporation():
    """Même pluie, 10 jours secs ensuite : sol encore humide en novembre (ET0 ~1), sec en septembre (ET0 ~4)."""
    n = 40
    precip = [0.0] * n
    precip[25] = precip[26] = 20.0
    trig = score.find_trigger(precip, 36, SC)
    novembre = score.comp_secheresse(precip, 36, trig, SC, [1.0] * n)
    septembre = score.comp_secheresse(precip, 36, trig, SC, [4.0] * n)
    ancien = score.comp_secheresse(precip, 36, trig, SC, None)
    assert novembre == 1.0
    # 40 mm − 10 j × 0,7 × 4 mm = 6,4 mm restants (16 %) : presque sec
    assert septembre == pytest.approx(0.4 + 0.6 * (0.16 - 0.10) / 0.40)
    assert ancien == SC["secheresse"]["min_value"]  # ancienne mesure : 0 mm depuis la pluie, quelle que soit la saison


def test_secheresse_repli_sans_et0_ou_et0_lacunaire():
    n = 30
    precip = [5.0] * n
    assert score.soil_ratio(precip, None, 20, SC) is None
    assert score.soil_ratio(precip, [None] * n, 20, SC) is None
    r = score.score_point(precip, [10.0] * n, 20, 100, SC)
    assert r["soil_ratio"] is None and r["score"] is not None


def _entry(n):
    return {"elevation": 100.0, "time": [f"2026-10-{i + 1:02d}" for i in range(n)], "precip": [3.0] * n,
            "tmin": [9.0] * n, "tmean": [12.0] * n, "et0": [1.0] * n}


def test_repli_cache_le_plus_recent(tmp_path):
    w = dict(CFG["weather"], cache_dir=str(tmp_path))
    pts = [(43.1, 3.2), (43.2, 3.3)]
    keys = [weather._key(*p) for p in pts]
    pre = weather._cache_prefix(w)
    (tmp_path / f"{pre}2026-11-01.json").write_text(json.dumps({k: _entry(5) for k in keys}))
    (tmp_path / f"{pre}v2_2026-11-03.json").write_text(json.dumps({k: _entry(6) for k in keys}))
    (tmp_path / f"{pre}v2_2026-11-04.json").write_text(json.dumps({keys[0]: _entry(7)}))  # incomplet
    (tmp_path / f"weather_meteofrance_{pre[8:]}v2_2026-11-05.json").write_text("{}")      # autre modèle
    data, day = weather.latest_cache(pts, w)
    assert day == "2026-11-03" and len(data[keys[0]]["time"]) == 6
    with pytest.raises(RuntimeError, match="aucune météo"):
        weather.latest_cache([(44.0, 4.0)], w)


def test_meteo_points_bascule_sur_le_cache(monkeypatch):
    def panne(pts, wcfg):
        raise RuntimeError("Open-Meteo injoignable après 5 essais : timed out")
    n = CFG["weather"]["past_days"] + CFG["weather"]["forecast_days"]
    fake = {"elevation": 100.0, "time": [f"d{i}" for i in range(n)], "precip": [2.0] * n, "tmin": [9.0] * n,
            "tmean": [12.0] * n, "et0": [1.0] * n}
    monkeypatch.setattr(weather, "fetch_all", panne)
    monkeypatch.setattr(weather, "latest_blend", lambda pts, w: ({weather._key(*p): fake for p in pts}, "2026-11-03"))
    monkeypatch.setattr(cli, "resolve", lambda p: __import__("pathlib").Path(__import__("tempfile").gettempdir()) / "v0_test.json")
    res = cli.meteo_points(CFG, limit=3)
    assert res["stale"] == "2026-11-03" and res["n_points"] > 0


def test_quota_ne_bascule_pas_sur_le_cache(monkeypatch):
    def quota(pts, wcfg):
        raise weather.QuotaExceeded("garde-fou")
    monkeypatch.setattr(weather, "fetch_all", quota)
    monkeypatch.setattr(weather, "latest_blend", lambda *a: pytest.fail("pas de repli sur un dépassement de quota"))
    with pytest.raises(weather.QuotaExceeded):
        cli.meteo_points(CFG, limit=3)


def test_avertissement_carte_echappe():
    import numpy as np
    idx = np.zeros((2, 2), dtype=np.int64)
    days = [{"offset": 0, "date": str(date(2026, 11, 7))}]
    html = rastermap.build_html({}, idx, [[0, 0], [1, 1]], [], days, (43.6, 3.9), SC["classes"],
                                warning="météo du <b>3</b>")
    assert "météo du &lt;b&gt;3&lt;/b&gt;" in html and "__WARN__" not in html
    assert "__WARN__" not in rastermap.build_html({}, idx, [[0, 0], [1, 1]], [], days, (43.6, 3.9), SC["classes"])
