"""Tests calendrier (fériés, coucher du soleil, créneaux) et rapport (sans réseau)."""
from datetime import date

import numpy as np
import pytest

from radar import calendar_fr as C
from radar import report as R
from radar.config import load_config

CFG = load_config()
MTP = (43.6108, 3.8767)


def test_paques_et_feries():
    assert C.easter(2026) == date(2026, 4, 5) and C.easter(2025) == date(2025, 4, 20) and C.easter(2024) == date(2024, 3, 31)
    h = C.holidays(2026)
    assert len(h) == 11 and h[date(2026, 5, 14)] == "Ascension" and h[date(2026, 5, 25)] == "Lundi de Pentecôte"
    assert C.day_kind(date(2026, 11, 11))[0] == "journee" and C.day_kind(date(2026, 11, 7))[0] == "journee"
    assert C.day_kind(date(2026, 11, 5))[0] == "soir"


PARIS_OBS = (48.8566, 2.3522)


@pytest.mark.parametrize("d,hh,mm", [(date(2026, 12, 21), 16, 56), (date(2026, 6, 21), 21, 58)])
def test_coucher_du_soleil_paris_ephemerides(d, hh, mm):
    """Référence : couchers bien établis à Paris aux solstices (16 h 56 / 21 h 58)."""
    s = C.sunset(d, *PARIS_OBS)
    assert abs((s.hour * 60 + s.minute) - (hh * 60 + mm)) <= 3


def test_coucher_montpellier_plus_tard_que_paris_en_hiver():
    # plus au sud : journées d'hiver plus longues, malgré la longitude plus à l'est
    assert C.sunset(date(2026, 12, 21), *MTP) > C.sunset(date(2026, 12, 21), *PARIS_OBS)
    assert C.sunset(date(2026, 6, 21), *MTP) < C.sunset(date(2026, 6, 21), *PARIS_OBS)


def test_creneau_soir_ete_oui_hiver_non():
    av = CFG["availability"]
    ete = C.slot(date(2026, 6, 16), *MTP, 60, av)          # mardi de juin : coucher ~21h30
    hiver = C.slot(date(2026, 11, 5), *MTP, 60, av)        # jeudi de novembre : coucher ~17h20
    assert ete["ok"] and ete["minutes"] > 200
    assert not hiver["ok"] and hiver["minutes"] < 0
    assert C.slot(date(2026, 11, 7), *MTP, 60, av)["ok"]    # samedi : journée


def test_pick_zones_separation_et_exclusions():
    x = np.array([0.0, 100.0, 6000.0, 12000.0, 20000.0])
    y = np.zeros(5)
    s = np.array([90.0, 89.0, 80.0, -1.0, np.nan])         # -1 hors périmètre, NaN inconnu
    assert R.pick_zones(s, x, y, 3, 5000) == [0, 2]
    assert R.pick_zones(s, x, y, 3, 5000, mask=np.array([False, True, True, True, True])) == [1, 2]


def fake_res_weather():
    n = 3
    fc = ["hors_foret", "chataignier"]
    gc = ["acide", "calcaire"]
    t = {"x": np.array([0.0, 10000.0, 20000.0]) + 700000, "y": np.full(n, 6280000.0),
         "lat": np.array([43.6, 43.6, 43.6]), "lon": np.array([3.8, 3.9, 4.0]), "alt": np.array([300.0, np.nan, 50.0]),
         "forest_pct": np.array([[0, 100], [100, 0], [0, 100]]), "geol_pct": np.array([[100, 0], [0, 100], [0, 100]])}
    dates = ["2026-11-04", "2026-11-05", "2026-11-07"]
    fam = {"cepes": {"label": "Cèpes"}}
    final = {"cepes": [np.array([70.0, 10.0, 40.0])] * 3}
    res = {"t": t, "meta": {"forest_classes": fc, "geol_classes": gc}, "scfg": {"families": fam}, "final_days": final,
           "access": {"travel_min": np.array([50.0, 20.0, 30.0]), "protected": np.zeros((n, 1))},
           "access_meta": {"factors": [1.0], "origin_is_home": False}}
    weather = {"date": "2026-11-04", "dates": dates, "offsets": [0, 1, 3],
               "points": [{"timeline": [60, 50, None]}, {"timeline": [40, 30, 20]}]}
    return res, weather


def test_rapport_jours_disponibles_et_contenu():
    res, weather = fake_res_weather()
    rep = R.build(res, weather, CFG)
    # jeudi 5 novembre : soir sans lumière -> retiré ; mercredi 4 = soir aussi -> retiré ; samedi 7 gardé
    assert [d["date"] for d in rep["days"]] == ["2026-11-07"]
    z = rep["days"][0]["families"]["cepes"]["zones"]
    assert [q["score"] for q in z] == [70, 40, 10] and z[0]["foret"] == "chataignier 100 %" and z[0]["trajet"] == 50
    assert rep["meteo_median"]["2026-11-07"] == 20 and rep["hunting"]
    txt = R.to_text(rep, CFG)
    assert "samedi 7 novembre" in txt and "jamais de comestibilité" in txt and "Open-Meteo" in txt and "Chasse" in txt
    html = R.to_html(rep, CFG, with_image=True)
    assert 'src="cid:carte"' in html and "openstreetmap.org/?mlat=43.60000" in html


def test_email_pdf_et_envoi_refuse_sans_smtp(tmp_path, monkeypatch):
    res, weather = fake_res_weather()
    rep = R.build(res, weather, CFG)
    txt = R.to_text(rep, CFG)
    pdf = R.write_pdf(txt, None, tmp_path / "r.pdf")
    assert pdf.read_bytes()[:5] == b"%PDF-"
    png = tmp_path / "c.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    msg = R.make_email(rep, CFG, txt, R.to_html(rep, CFG, True), png, pdf, "dest@example.org", "exp@example.org")
    raw = bytes(msg)
    assert b"multipart/mixed" in raw and b"multipart/related" in raw and b'filename="r.pdf"' in raw
    assert b"Content-ID: <carte>" in raw
    monkeypatch.setattr(R, "load_env", lambda: None)
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="SMTP non configuré"):
        R.send(msg)


def test_pdf_caracteres_hors_winansi():
    assert R._pdf_safe("≤ 60 min → ~3 h") == "<= 60 min -> env. 3 h"
