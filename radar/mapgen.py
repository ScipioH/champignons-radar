"""Génération de la carte HTML statique (Leaflet), données embarquées pour ouverture en local."""
from __future__ import annotations

import json
from pathlib import Path

COLORS = {"faible": "#b0b0b0", "moyen": "#f2c14e", "bon": "#7cb342", "très bon": "#1b7a3d", "indéterminé": "#555555"}

TEMPLATE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Champignons Radar</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 html,body,#map{height:100%;margin:0}
 #banner{position:absolute;z-index:1000;top:8px;left:50px;right:8px;background:#fff;border:2px solid #c0392b;
  border-radius:6px;padding:6px 10px;font:13px system-ui,sans-serif;max-width:720px}
 #legend{position:absolute;z-index:1000;bottom:20px;left:10px;background:#fff;padding:8px 10px;border-radius:6px;
  font:12px system-ui,sans-serif;box-shadow:0 1px 4px #0005}
 #legend i{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:middle}
</style></head><body>
<div id="banner"><b>Indice de POUSSE, pas de comestibilité.</b> __CALIB__ Respecter la vigilance Météo-France,
la période de chasse et la réglementation locale. Échéance : __DATE__.</div>
<div id="map"></div><div id="legend"><b>Classe</b><br>__LEGEND__</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const map = L.map('map').setView([__LAT__, __LON__], 9);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 17, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
const COLORS = __COLORS__;
for (const p of DATA.points) {
  const m = L.circleMarker([p.lat, p.lon], {radius: 7, color: '#222', weight: 0.5,
    fillColor: COLORS[p.classe] || '#555', fillOpacity: 0.85}).addTo(map);
  const c = p.components || {};
  m.bindPopup('<b>' + p.classe + '</b> (score ' + (p.score ?? 'n/a') + ')<br>Altitude : ' + Math.round(p.altitude) +
    ' m<br>Pluie 15 j : ' + p.rain_15d_mm + ' mm<br>Jours depuis pluie déclenchante : ' +
    (p.days_since_trigger ?? 'aucune') + '<br>Nuits de gel (7 j) : ' + p.gel_nights +
    '<br><small>pluie ' + c.pluie + ' · délai ' + c.delai + ' · temp ' + c.temp + ' · sécheresse ' + c.secheresse +
    ' · prévision ' + c.prevision + '</small>');
}
</script></body></html>
"""


def _safe_json(obj) -> str:
    # évite toute fermeture prématurée de balise <script>
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_html(result: dict, center: tuple[float, float], calibrated: bool) -> str:
    legend = "<br>".join(f'<i style="background:{c}"></i>{k}' for k, c in COLORS.items() if k != "indéterminé")
    calib = "" if calibrated else "<b>Indice non calibré</b> (seuils = hypothèses, journal &lt; 20 sorties)."
    html = TEMPLATE
    for k, v in {
        "__DATA__": _safe_json(result),
        "__COLORS__": _safe_json(COLORS),
        "__LAT__": str(center[0]),
        "__LON__": str(center[1]),
        "__DATE__": result["date"],
        "__CALIB__": calib,
        "__LEGEND__": legend,
    }.items():
        html = html.replace(k, v)
    return html


def write_map(result: dict, center: tuple[float, float], calibrated: bool, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(result, center, calibrated), encoding="utf-8")
    return out
