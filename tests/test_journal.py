"""Tests du journal de sorties (phase 4) : base temporaire, réseau simulé, serveur local."""
import json
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta

import numpy as np
import pytest

from radar import journal as J
from radar import journal_app as A
from radar.config import load_config
from radar.terrain import load_terrain_cfg

CFG = load_config()
TODAY = date(2026, 11, 4)


def fake_snap(cfg, d, lat, lon, famille):
    s = 80.0 if lat > 43.7 else 20.0
    from radar.score import classify
    return {"meteo": 70, "habitat": 0.5, "score": s, "classe": classify(s, cfg["score"])}


@pytest.fixture
def con(tmp_path):
    with J.session(tmp_path / "j.db") as c:
        yield c


def entry(**kw):
    e = {"date": "2026-11-01", "lat": 43.98, "lon": 3.71, "famille": "cepes", "resultat": 2, "note": "test"}
    e.update(kw)
    return e


def test_validation():
    fams = ["cepes"]
    with pytest.raises(ValueError, match="futur"):
        J.validate(entry(date="2026-11-05"), fams, TODAY)
    with pytest.raises(ValueError, match="France"):
        J.validate(entry(lat=10), fams, TODAY)
    with pytest.raises(ValueError, match="famille"):
        J.validate(entry(famille="truffes"), fams, TODAY)
    with pytest.raises(ValueError, match="0 à 3"):
        J.validate(entry(resultat=5), fams, TODAY)
    assert J.validate(entry(note="x" * 900), fams, TODAY)["note"] == "x" * 500


def test_ajout_avec_instantane_et_echec_reseau(con):
    r = J.add(con, CFG, entry(), snap=fake_snap)
    assert r["score"] == 80 and r["classe"] == "très bon" and r["erreur"] is None

    def boom(*a):
        raise OSError("réseau coupé")
    r2 = J.add(con, CFG, entry(lat=43.5, resultat=0), snap=boom)
    assert r2["score"] is None and "réseau" in r2["erreur"]
    assert len(J.rows(con)) == 2                                   # la sortie est gardée
    assert J.recompute(con, CFG, snap=fake_snap) == 1
    assert all(r["score"] is not None for r in J.rows(con))


def test_evaluation_non_calibree_puis_par_classe(con):
    for i in range(5):
        J.add(con, CFG, entry(lat=43.8, resultat=2 + (i % 2)), snap=fake_snap)    # prédit très bon, trouvé
        J.add(con, CFG, entry(lat=43.5, resultat=0), snap=fake_snap)              # prédit faible, bredouille
    ev = J.evaluate(con, CFG)
    assert ev["n"] == 10 and not ev["calibrable"]
    assert ev["par_classe"]["très bon"]["trouve"] == 1.0 and ev["par_classe"]["faible"]["trouve"] == 0.0
    assert ev["spearman"] > 0.8
    txt = J.evaluation_text(ev)
    assert "NON calibré" in txt and "encore 10" in txt


def test_spearman_cas_limites():
    assert J._spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])) is None
    assert J._spearman(np.array([1.0, 1.0, 1.0]), np.array([0.0, 1.0, 2.0])) is None
    assert J._spearman(np.array([1.0, 2.0, 3.0, 4.0]), np.array([3.0, 2.0, 1.0, 0.0])) == pytest.approx(-1.0)


def test_url_meteo_recente_ou_archive():
    u = J.weather_url(43.6, 3.9, TODAY - timedelta(days=35), TODAY + timedelta(days=3), TODAY)
    assert u.startswith("https://api.open-meteo.com/v1/forecast") and "start_date=2026-09-30" in u
    assert "models=" not in u
    u = J.weather_url(43.6, 3.9, date(2025, 9, 1), date(2025, 10, 10), TODAY, "meteofrance_seamless")
    assert u.startswith(J.HISTORICAL_API) and "end_date=2025-10-10" in u and "models=meteofrance_seamless" in u
    # sortie d'il y a 4 jours : historique de 44 j -> archives, tronquées à la veille de la veille
    u = J.weather_url(43.6, 3.9, TODAY - timedelta(days=44), TODAY - timedelta(days=1), TODAY)
    assert u.startswith(J.HISTORICAL_API) and "end_date=2026-11-02" in u


def test_instantane_melange_les_modeles_comme_la_carte():
    calls = []
    n = 45
    days = [(date(2026, 9, 20) + timedelta(days=k)).isoformat() for k in range(n)]

    def get(u):
        calls.append(u)
        mf = "models=meteofrance_seamless" in u
        p = [None if (mf and k >= n - 2) else (1.0 if mf else 9.0) for k in range(n)]
        return {"elevation": 300.0, "daily": {"time": days, "precipitation_sum": p, "temperature_2m_min": [8.0] * n,
                                              "temperature_2m_mean": [11.0] * n,
                                              "et0_fao_evapotranspiration": [1.0] * n}}
    cfg = dict(CFG, weather=dict(CFG["weather"], model="meteofrance_seamless", fallback_model="best_match"))
    w = J.snapshot_weather(cfg, date(2026, 9, 20), date(2026, 11, 3), TODAY, 43.6, 3.9, get)
    assert len(calls) == 2
    assert w["precip"][:3] == [1.0] * 3 and w["precip"][-2:] == [9.0, 9.0] and w["filled_days"] == 2


def test_instantane_modele_sur_donnees_simulees():
    tc = load_terrain_cfg()
    fc, gc = tc["forest"]["classes"], tc["geology"]["classes"]
    from pyproj import Transformer
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform(3.71, 43.98)
    fp = np.zeros((1, len(fc)))
    fp[0, fc.index("chataignier")] = 100
    gp = np.zeros((1, len(gc)))
    gp[0, gc.index("acide")] = 100
    terrain = ({"x": np.array([x]), "y": np.array([y]), "forest_pct": fp, "geol_pct": gp, "alt": np.array([500.0]),
                "north": np.array([0.0]), "east": np.array([0.0]), "coverage_pct": np.array([100])},
               {"forest_classes": fc, "geol_classes": gc})
    d = date(2026, 11, 1)
    days = [(d - timedelta(days=32) + timedelta(days=k)).isoformat() for k in range(36)]
    precip = [0.0] * 36
    precip[days.index("2026-10-22")] = 40.0                    # pluie déclenchante 10 j avant
    fake = {"elevation": 500.0, "daily": {"time": days, "precipitation_sum": precip,
                                         "temperature_2m_min": [9.0] * 36, "temperature_2m_mean": [12.0] * 36}}
    s = J.snapshot(CFG, d, 43.98, 3.71, "cepes", today=TODAY, get=lambda u: fake, terrain=terrain)
    assert s["habitat"] == pytest.approx(1.0) and s["meteo"] > 60 and s["score"] > 60
    far = J.snapshot(CFG, d, 43.5, 3.0, "cepes", today=TODAY, get=lambda u: fake, terrain=terrain)
    assert far["habitat"] is None and far["score"] is None     # hors grille terrain : inconnu


# ----------------------------------------------------------------- serveur local

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "db_path", lambda: tmp_path / "srv.db")
    monkeypatch.setattr(J, "snapshot", fake_snap)
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), A.make_handler(CFG, 0))
    port = srv.server_address[1]
    srv.RequestHandlerClass = A.make_handler(CFG, port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield port
    srv.shutdown()
    srv.server_close()


def call(port, path, body=None, headers=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers=headers or {}, method="GET" if body is None else "POST")
    if host:
        req.add_unredirected_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_serveur_page_ajout_et_protections(server):
    port = server
    code, html = call(port, "/")
    assert code == 200 and "Journal de sorties" in html and "__" not in html
    h = {"Content-Type": "application/json", "X-Radar": "1"}
    code, _ = call(port, "/api/sorties", entry(), headers={"Content-Type": "application/json"})
    assert code == 403                                               # sans X-Radar (requête d'un site tiers)
    code, _ = call(port, "/", host="evil.example:80")
    assert code == 403                                               # DNS rebinding
    code, body = call(port, "/api/sorties", entry(date=str(date.today())), headers=h)
    assert code == 200 and json.loads(body)["score"] is not None
    code, body = call(port, "/api/sorties", entry(resultat=9), headers=h)
    assert code == 400 and "0 à 3" in json.loads(body)["error"]
    code, body = call(port, "/api/sorties")
    data = json.loads(body)
    assert len(data["rows"]) == 1 and "NON calibré" in data["evaluation"]
    code, _ = call(port, "/api/delete", {"id": data["rows"][0]["id"]}, headers=h)
    assert code == 200 and json.loads(call(port, "/api/sorties")[1])["rows"] == []
