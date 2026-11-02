"""Tests du score sur séries simulées (aucun réseau). Critères de fini de la phase 1."""
import pytest

from radar.config import load_config
from radar.score import classify, gel_factor, ramp, score_point

CFG = load_config()["score"]
IDX = 30  # 30 jours passés puis aujourd'hui puis 6 jours de prévision
N = 37


def series(rain: dict[int, float] | None = None, tmin: float = 9.5, frost_last: int = 0, frost_val: float = -2.0):
    """rain: {jours avant aujourd'hui (>0) ou après (<0): mm}. Prévision sèche par défaut."""
    precip = [0.0] * N
    for back, mm in (rain or {}).items():
        assert 0 <= IDX - back < N, "jour hors série simulée"
        precip[IDX - back] = mm
    tm = [tmin] * N
    for k in range(frost_last):
        tm[IDX - k] = frost_val
    return precip, tm


def score(precip, tm, alt=200.0):
    return score_point(precip, tm, IDX, alt, CFG)["score"]


def fixture_2nov():
    # Situation du 2/11/2026 : ~50 mm / 15 j, pluie >= 20 mm il y a 8 j, Tmin ~9,5, 0 gel
    return series({8: 25, 7: 5, 3: 10, 12: 10})


def test_a_fixture_favorable():
    assert score(*fixture_2nov()) >= 70


def test_b_gel_penalise_mais_pas_zero():
    ref = score(*fixture_2nov())
    p, t = series({8: 25, 7: 5, 3: 10, 12: 10}, frost_last=3)
    s = score(p, t)
    assert 0 < s < 0.6 * ref


def test_c_secheresse_longue():
    p, t = series({})
    assert score(p, t) < 30


def test_d_trop_tot_apres_pluie_diluvienne():
    p, t = series({1: 200})
    assert score(p, t) < 40


def test_e_decroissance_apres_20_jours():
    prev = None
    for days in range(14, IDX + 1):  # la série réelle ne couvre que 30 jours passés
        p, t = series({days: 30})
        s = score(p, t)
        if prev is not None:
            assert s <= prev + 1e-9, f"non monotone à {days} j"
        prev = s
    assert prev < score(*series({10: 30}))


def test_plancher_un_facteur_nul_nécrase_pas_tout():
    # pas de pluie déclenchante mais tout le reste idéal : score faible mais > 0
    p, t = series({})
    assert score(p, t) > 5


def test_gel_altitude_plus_severe():
    t = [9.5] * N
    t[IDX] = -1.0
    low, n1 = gel_factor(t, IDX, 200, CFG)
    high, n2 = gel_factor(t, IDX, 1200, CFG)
    assert n1 == n2 == 1 and high < low < 1.0


def test_gel_severe():
    t = [9.5] * N
    t[IDX] = -6.0
    f, _ = gel_factor(t, IDX, 200, CFG)
    assert f == pytest.approx(CFG["gel"]["severe_factor"])


def test_valeurs_manquantes_ne_plantent_pas():
    p, t = fixture_2nov()
    p[IDX - 2] = None
    t = [None] * N  # aucune Tmin : composante ignorée, résultat marqué partiel
    r = score_point(p, t, IDX, 200, CFG)
    assert r["partial"] is True and r["score"] is not None


def test_sans_prevision_composante_ignoree():
    p, t = fixture_2nov()
    r = score_point(p[: IDX + 1], t[: IDX + 1], IDX, 200, CFG)
    assert r["components"]["prevision"] is None and r["partial"]


def test_idx_invalide():
    p, t = fixture_2nov()
    with pytest.raises(ValueError):
        score_point(p, t, 99, 200, CFG)


def test_ramp_et_classes():
    assert ramp(5, 0, 10) == 0.5 and ramp(-1, 0, 10) == 0 and ramp(99, 0, 10) == 1
    assert classify(80, CFG) == "très bon" and classify(60, CFG) == "bon"
    assert classify(40, CFG) == "moyen" and classify(10, CFG) == "faible"


def test_score_borne():
    for rain in ({}, {1: 500}, {8: 40}, {5: 300, 6: 300}):
        p, t = series(rain)
        s = score(p, t)
        assert 0 <= s <= 100
