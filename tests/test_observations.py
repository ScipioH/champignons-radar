"""Contrôle de la pluie modèle par les stations Météo-France (données synthétiques, sans réseau)."""
import pytest

from radar import observations as O
from radar.config import load_config

CFG = load_config()
OC, SC = CFG["observations"], CFG["score"]
HEADER = "NUM_POSTE;NOM_USUEL;LAT;LON;ALTI;AAAAMMJJ;RR;QRR;TN;QTN"


def test_lecture_csv_filtre_qualite_et_bornes():
    text = "\n".join([
        HEADER,
        "34001001;STATION A;43.6;3.8;50;20261001;12.5;1;8;1",
        "34001001;STATION A;43.6;3.8;50;20261002;3;2;8;1",        # douteuse : écartée
        "34001001;STATION A;43.6;3.8;50;20261003;9999;1;8;1",     # hors bornes : écartée
        "34001001;STATION A;43.6;3.8;50;20261004;;1;8;1",         # vide : écartée
        "34001001;STATION A;43.6;3.8;50;2026-10-05;1;1;8;1",      # date mal formée : écartée
        "30002002;<script>B;44.0;3.9;700;20261001;0.0;9;8;1",
        "30003003;HORS FRANCE;10;3.9;700;20261001;1.0;1;8;1",     # coordonnées absurdes : écartée
    ])
    st = O.parse(text)
    assert set(st) == {"34001001", "30002002"}
    assert st["34001001"]["rr"] == {"2026-10-01": 12.5}
    assert st["30002002"]["alt"] == 700.0


def test_lecture_refuse_colonnes_manquantes():
    with pytest.raises(ValueError, match="colonnes"):
        O.parse("NUM_POSTE;LAT;LON\n1;2;3")


def test_episodes_et_appariement():
    p = [0, 0, 15, 10, 0, 0, 0, 0, 30, 0, 0]
    days = O.trigger_days(p, 20)
    assert days == [3, 8, 9]
    ev = O.events(days)
    assert ev == [(3, 3), (8, 9)]
    # modèle : 1er épisode décalé d'un jour (vu avec tolérance 1), 2e manqué, un épisode inventé
    hits, missed, false = O.match_events(ev, [(4, 4), (20, 20)], tol=1)
    assert (hits, missed, false) == (1, 1, 1)
    assert O.match_events(ev, [(5, 5)], tol=1) == (0, 2, 1)


def _model(precip):
    n = len(precip)
    return {"time": [f"2026-{8 + i // 31:02d}-{i % 31 + 1:02d}" for i in range(n)], "precip": list(precip),
            "tmin": [9.0] * n, "et0": [1.5] * n}


def test_comparaison_modele_identique_aux_stations():
    precip = ([0.0] * 10 + [25.0, 10.0] + [0.0] * 8) * 4
    m = _model(precip)
    obs = {t: v for t, v in zip(m["time"], precip)}
    r = O.compare_station(obs, m, SC, OC, 300)
    assert r["sum_obs"] == pytest.approx(r["sum_mod"]) and r["mae"] == 0
    assert r["missed"] == 0 and r["false"] == 0 and r["hits"] >= 3
    s = O.summarize([r], SC)
    assert s["ratio"] == pytest.approx(1.0) and s["score_mad"] == 0 and s["class_diff"] == 0
    assert "cohérents" in O.verdict(s)


def test_comparaison_modele_qui_sous_estime():
    precip = ([0.0] * 10 + [25.0, 10.0] + [0.0] * 8) * 4
    m = _model([v * 0.4 for v in precip])          # le modèle ne voit que 40 % de la pluie
    obs = {t: v for t, v in zip(m["time"], precip)}
    s = O.summarize([O.compare_station(obs, m, SC, OC, 300)], SC)
    assert s["ratio"] == pytest.approx(0.4)
    assert s["pod"] == 0 and s["missed"] >= 3     # 14 mm en 2 j : aucune pluie déclenchante vue
    assert s["score_bias"] < 0                    # le score météo du modèle est trop bas
    assert "sous-estime" in O.verdict(s)


def test_station_trop_lacunaire_ignoree():
    m = _model([1.0] * 60)
    obs = {t: 1.0 for t in m["time"][:10]}
    assert O.compare_station(obs, m, SC, OC, 100) is None


def test_url_lue_dans_le_catalogue_et_hote_controle():
    base = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/QUOT/"
    cat = {"resources": [
        {"title": "QUOT_departement_34_periode_2025-2026_RR-T-Vent", "url": base + "Q_34_latest-2025-2026.csv.gz"},
        {"title": "QUOT_departement_34_periode_2026-2027_RR-T-Vent", "url": base + "Q_34_latest-2026-2027.csv.gz"},
        {"title": "QUOT_departement_34_periode_2027-2028_RR-T-Vent", "url": "https://pirate.example/x.csv.gz"},
        {"title": "QUOT_departement_34_periode_2026-2027_autres-parametres", "url": base + "autres.csv.gz"},
        {"title": "QUOT_departement_340_periode_2026-2027_RR-T-Vent", "url": base + "Q_340.csv.gz"},
    ]}
    assert O.resolve_url("34", OC, lambda u: cat).endswith("Q_34_latest-2026-2027.csv.gz")
    # catalogue en panne : gabarit de secours
    def panne(u):
        raise OSError("down")
    assert O.resolve_url("30", OC, panne) == OC["url_template"].format(dep="30")
    assert not O._host_ok("http://meteofrance.s3.sbg.io.cloud.ovh.net/x", OC)   # pas https
