"""Tests grille, météo (réseau simulé), carte."""
import json

import pytest

from radar import mapgen, weather
from radar.config import load_config
from radar.grid import build_grid, haversine_km

CFG = load_config()
C = CFG["center"]


def test_grille_dans_le_rayon_et_centre_inclus():
    pts = build_grid(C["lat"], C["lon"], 60, 5)
    assert all(haversine_km(C["lat"], C["lon"], la, lo) <= 60.0001 for la, lo in pts)
    # aire attendue ~ pi*60^2/25 = 452 points
    assert 400 < len(pts) < 500
    assert min(haversine_km(C["lat"], C["lon"], la, lo) for la, lo in pts) < 1.0
    assert len(pts) == len(set(pts))


def test_grille_parametres_invalides():
    with pytest.raises(ValueError):
        build_grid(43, 3, 0, 5)
    with pytest.raises(ValueError):
        build_grid(43, 3, 60, 0)


def fake_entry(i):
    n = 37
    return {"elevation": 100.0 + i,
            "daily": {"time": [f"d{k}" for k in range(n)], "precipitation_sum": [0.0] * n,
                      "temperature_2m_min": [9.0] * n, "temperature_2m_mean": [12.0] * n}}


@pytest.fixture
def wcfg(tmp_path):
    w = dict(CFG["weather"])
    w["cache_dir"] = str(tmp_path)  # chemin absolu : resolve() le conserve
    w["pause_seconds"] = 0
    w["batch_size"] = 3
    return w


def test_fetch_lots_et_cache(monkeypatch, wcfg):
    calls = []

    def fake_get(url, retries):
        calls.append(url)
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return [fake_entry(i) for i in range(n)] if n > 1 else fake_entry(0)

    monkeypatch.setattr(weather, "_http_get_json", fake_get)
    pts = [(43.0 + i / 10, 3.0) for i in range(7)]
    out = weather.fetch_all(pts, wcfg)
    assert len(out) == 7 and len(calls) == 3  # 3 + 3 + 1
    weather.fetch_all(pts, wcfg)
    assert len(calls) == 3  # tout vient du cache
    wcfg["past_days"] += 10
    weather.fetch_all(pts, wcfg)
    assert len(calls) == 6  # autre fenêtre demandée : le cache du jour ne doit pas être réutilisé


def test_quota_garde_fou(monkeypatch, wcfg):
    wcfg["max_points_per_day"] = 2
    monkeypatch.setattr(weather, "_http_get_json", lambda u, r: pytest.fail("ne doit pas appeler le réseau"))
    with pytest.raises(weather.QuotaExceeded):
        weather.fetch_all([(43.0, 3.0), (43.1, 3.0), (43.2, 3.0)], wcfg)


def test_retente_sur_coupure_reseau_et_5xx(monkeypatch):
    import io
    import urllib.error
    calls = []

    def flaky(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("_ssl.c:1015: The handshake operation timed out")
        if len(calls) == 2:
            raise urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b""))
        return io.BytesIO(b'{"ok": true}')
    monkeypatch.setattr(weather.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(weather.time, "sleep", lambda s: None)
    assert weather._http_get_json("https://x", 3) == {"ok": True} and len(calls) == 3
    calls.clear()
    monkeypatch.setattr(weather.urllib.request, "urlopen",
                        lambda r, timeout: (_ for _ in ()).throw(urllib.error.URLError("down")))
    with pytest.raises(RuntimeError, match="injoignable"):
        weather._http_get_json("https://x", 2)


def test_certificat_refuse_echoue_tout_de_suite(monkeypatch):
    import ssl
    import urllib.error
    calls = []
    err = ssl.SSLCertVerificationError(1, "certificate verify failed")
    err.verify_message = "certificate has expired"

    def refuse(req, timeout):
        calls.append(1)
        raise urllib.error.URLError(err)
    monkeypatch.setattr(weather.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(weather.time, "sleep", lambda s: pytest.fail("pas d'attente sur un certificat refusé"))
    with pytest.raises(RuntimeError, match="certificate has expired.*antivirus"):
        weather._http_get_json("https://x", 4)
    assert len(calls) == 1


def test_reponse_incoherente(monkeypatch, wcfg):
    monkeypatch.setattr(weather, "_http_get_json", lambda u, r: [fake_entry(0)])
    with pytest.raises(RuntimeError):
        weather.fetch_all([(43.0, 3.0), (43.1, 3.0)], wcfg)


def test_build_url_groupee():
    url = weather.build_url([(43.1, 3.2), (43.3, 3.4)], CFG["weather"])
    assert "latitude=43.1000,43.3000" in url and "longitude=3.2000,3.4000" in url
    w = CFG["weather"]
    assert f"past_days={w['past_days']}" in url and f"forecast_days={w['forecast_days']}" in url
    # le curseur J-7..J+7 doit tenir dans la série : 30 j d'historique avant J-7, 3 j de prévision après J+7
    tl = CFG["timeline"]
    assert w["past_days"] - tl["days_before"] >= CFG["score"]["delai"]["lookback_days"]
    assert w["forecast_days"] - 1 - tl["days_after"] >= CFG["score"]["prevision"]["horizon_days"]


def test_carte_html_echappe_et_avertit(tmp_path):
    res = {"date": "2026-11-02", "n_points": 1, "points": [
        {"lat": 43.6, "lon": 3.9, "altitude": 50, "score": 80, "classe": "très bon", "components": {},
         "rain_15d_mm": 10, "days_since_trigger": 8, "gel_nights": 0, "x": "</script><b>"}]}
    out = mapgen.write_map(res, (43.6, 3.9), calibrated=False, out=tmp_path / "m" / "index.html")
    html = out.read_text(encoding="utf-8")
    assert "comestibilité" in html and "non calibré" in html
    data_part = html.split("const DATA = ")[1].split(";\nconst map")[0]
    assert "</script>" not in data_part
    json.loads(data_part.replace("<\\/", "</"))
