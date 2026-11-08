"""Enchaînement des pluies : prolongation par une pluie modérée, relance et poussée abîmée après une forte pluie."""
import pytest

from radar import score
from radar.config import load_config

SC = load_config()["score"]
N = 60


def serie(pluies: dict[int, float]) -> list[float]:
    p = [0.0] * N
    for k, v in pluies.items():
        p[k] = v
    return p


def test_episodes_regroupes():
    # épisode cévenol sur 3 jours (41,8 / 63,2 / 14,5) = UN épisode, jour le plus arrosé, meilleur cumul 2 j
    ev = score.rain_events(serie({30: 41.8, 31: 63.2, 32: 14.5, 45: 25.0}), 50, SC)
    assert ev == [(31, pytest.approx(105.0)), (45, 25.0)]


def test_pluie_moderee_prolonge_la_poussee():
    p = serie({20: 30.0, 30: 25.0})            # 2e pluie modérée 10 j après la 1re
    cur, prev = score.flushes(score.rain_events(p, 33, SC), SC)
    assert cur == {"anchor": 20, "last": 30} and prev is None
    # 3 j après la 2e pluie : l'ancienne logique repartait de zéro (délai 0) ; la poussée continue
    assert score.delai_flush(cur, 33, SC) == 1.0
    assert score.comp_delai(3, SC) == 0.0
    # le plateau est prolongé : 24 j après le début, mais 14 j après la dernière pluie
    assert score.delai_flush(score.flushes(score.rain_events(p, 44, SC), SC)[0], 44, SC) == 1.0


def test_forte_pluie_relance_et_garde_la_moitie_de_l_ancienne():
    p = serie({20: 30.0, 32: 45.0, 33: 40.0})  # poussée mûre, puis 85 mm en 2 j
    cur, prev = score.flushes(score.rain_events(p, 34, SC), SC)
    assert cur["anchor"] in (32, 33) and prev == {"anchor": 20, "last": 20}
    t = [9.0] * N
    r = score.score_point(p, t, 34, 300, SC)
    assert r["damaged_flush"] is True and r["components"]["delai"] == 0.0
    # score = moitié de ce qu'aurait donné l'ancienne poussée (délai 1), toutes autres composantes égales
    sans_relance = dict(SC, delai=dict(SC["delai"], reset_mm=1e9))
    r_old = score.score_point(p, t, 34, 300, sans_relance)
    assert r["score"] == pytest.approx(SC["delai"]["damaged_factor"] * r_old["score"], abs=0.11)
    # une semaine plus tard, la nouvelle poussée a pris le relais
    r2 = score.score_point(p, t, 42, 300, SC)
    assert r2["damaged_flush"] is False and r2["components"]["delai"] == 1.0


def test_pluie_moderee_apres_poussee_eteinte_relance_sans_poussee_abimee():
    p = serie({5: 30.0, 30: 25.0})             # 25 j sans pluie entre les deux : la 1re poussée est finie
    cur, prev = score.flushes(score.rain_events(p, 32, SC), SC)
    assert cur == {"anchor": 30, "last": 30} and prev is None


def test_sans_pluie_declenchante():
    cur, prev = score.flushes(score.rain_events(serie({10: 5.0}), 40, SC), SC)
    assert cur is None and prev is None and score.delai_flush(None, 40, SC) == 0.0
