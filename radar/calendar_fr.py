"""Calendrier de disponibilité (phase 3) : jours fériés français, coucher du soleil, créneaux de sortie.

Règle (décision du 2026-11-02) : week-ends + jours fériés ; soirs de semaine si, en partant à
`weekday_depart_hour` et après le trajet, il reste au moins `min_forage_minutes` de jour sur place.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def easter(year: int) -> date:
    """Dimanche de Pâques (algorithme grégorien anonyme / Meeus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lv = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lv) // 451
    month = (h + lv - 7 * m + 114) // 31
    day = ((h + lv - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def holidays(year: int) -> dict[date, str]:
    """Jours fériés nationaux (métropole, hors Alsace-Moselle)."""
    p = easter(year)
    return {
        date(year, 1, 1): "Jour de l'an", p + timedelta(days=1): "Lundi de Pâques", date(year, 5, 1): "Fête du travail",
        date(year, 5, 8): "Victoire 1945", p + timedelta(days=39): "Ascension", p + timedelta(days=50): "Lundi de Pentecôte",
        date(year, 7, 14): "Fête nationale", date(year, 8, 15): "Assomption", date(year, 11, 1): "Toussaint",
        date(year, 11, 11): "Armistice", date(year, 12, 25): "Noël",
    }


def sunset(d: date, lat: float, lon: float) -> datetime:
    """Coucher du soleil (heure locale de Paris), équations NOAA ; précision ~1-2 min, suffisante ici."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    minutes_utc = 720 - 4 * (lon - ha) - eqt
    utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(minutes=minutes_utc)
    return utc.astimezone(PARIS)


def day_kind(d: date) -> tuple[str | None, str]:
    """('journee', libellé) pour week-end/férié, ('soir', libellé) en semaine."""
    h = holidays(d.year).get(d)
    if h:
        return "journee", f"férié ({h})"
    if d.weekday() >= 5:
        return "journee", "week-end"
    return "soir", "soir de semaine"


def evening_minutes(d: date, lat: float, lon: float, travel_min: float, depart_hour: float) -> float:
    """Minutes de jour restant sur place un soir de semaine (départ à depart_hour, après le trajet)."""
    dep = datetime(d.year, d.month, d.day, tzinfo=PARIS) + timedelta(hours=depart_hour)
    arrive = dep + timedelta(minutes=float(travel_min))
    return (sunset(d, lat, lon) - arrive).total_seconds() / 60.0


def slot(d: date, lat: float, lon: float, travel_min: float, acfg: dict) -> dict:
    """Créneau d'une zone pour un jour : {ok, kind, label, minutes (jour restant, soirs seulement)}."""
    kind, label = day_kind(d)
    if kind == "journee":
        return {"ok": True, "kind": kind, "label": label, "minutes": None}
    m = evening_minutes(d, lat, lon, travel_min, acfg["weekday_depart_hour"])
    return {"ok": m >= acfg["min_forage_minutes"], "kind": kind, "label": label, "minutes": m}
