"""Génération de la grille de points autour du centre (rayon haversine)."""
from __future__ import annotations

import math

EARTH_KM = 6371.0088
KM_PER_DEG_LAT = 111.19


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def build_grid(lat0: float, lon0: float, radius_km: float, spacing_km: float) -> list[tuple[float, float]]:
    """Points régulièrement espacés (~spacing_km) dans le disque de rayon radius_km.

    Le rayon à vol d'oiseau est une approximation déclarée du "<= 1 h de route"
    (remplacée par un vrai temps de trajet en phase 2c).
    """
    if spacing_km <= 0 or radius_km <= 0:
        raise ValueError("radius_km et spacing_km doivent être > 0")
    dlat = spacing_km / KM_PER_DEG_LAT
    dlon = spacing_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat0)))
    n_lat = int(radius_km / spacing_km) + 1
    n_lon = int(radius_km / spacing_km * 1.5) + 1  # marge : dlon varie légèrement avec la latitude
    points = []
    for i in range(-n_lat, n_lat + 1):
        for j in range(-n_lon, n_lon + 1):
            lat = lat0 + i * dlat
            lon = lon0 + j * dlon
            if haversine_km(lat0, lon0, lat, lon) <= radius_km:
                points.append((round(lat, 4), round(lon, 4)))
    return points
