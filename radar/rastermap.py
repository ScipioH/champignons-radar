"""Carte HTML de l'habitat et du score final à 250 m (phase 2b).

Les cellules (grille Lambert 93) sont rééchantillonnées au plus proche sur une grille régulière lat/lon pour
être posées en `imageOverlay` Leaflet. Les valeurs (0-100, 254 = inconnu, 255 = hors zone) sont embarquées
compressées (zlib + base64) et colorisées dans le navigateur ; un clic affiche la valeur de chaque couche.
"""
from __future__ import annotations

import base64
import json
import math
import zlib
from pathlib import Path

import numpy as np
from pyproj import Transformer

from radar.mapgen import COLORS, _safe_json
from radar.terrain import L93

UNKNOWN, OUTSIDE = 254, 255


def encode_values(v: np.ndarray) -> np.ndarray:
    """0-1 -> 0-100 (uint8) ; NaN -> UNKNOWN."""
    out = np.full(v.shape, UNKNOWN, dtype=np.uint8)
    ok = ~np.isnan(v)
    out[ok] = np.round(np.clip(v[ok], 0, 1) * 100).astype(np.uint8)
    return out


def display_grid(t: dict, grid: dict) -> tuple[np.ndarray, list[list[float]]]:
    """Index de cellule (ou -1) pour chaque pixel d'une grille lat/lon couvrant les cellules, et ses bornes."""
    cell = grid["fine_m"] * grid["factor"]
    nrows, ncols = grid["nrows"], grid["ncols"]
    full = np.full((nrows, ncols), -1, dtype=np.int64)
    r = np.floor((grid["ymax"] - t["y"]) / cell).astype(int)
    c = np.floor((t["x"] - grid["xmin"]) / cell).astype(int)
    full[r, c] = np.arange(len(t["x"]))

    lat0 = float(np.mean(t["lat"]))
    dlat = cell / 111_320.0
    dlon = cell / (111_320.0 * math.cos(math.radians(lat0)))
    s, n = float(t["lat"].min()) - dlat, float(t["lat"].max()) + dlat
    w, e = float(t["lon"].min()) - dlon, float(t["lon"].max()) + dlon
    ny, nx = int(math.ceil((n - s) / dlat)), int(math.ceil((e - w) / dlon))
    lats = n - (np.arange(ny) + 0.5) * dlat
    lons = w + (np.arange(nx) + 0.5) * dlon
    LON, LAT = np.meshgrid(lons, lats)
    X, Y = Transformer.from_crs("EPSG:4326", L93, always_xy=True).transform(LON, LAT)
    rr = np.floor((grid["ymax"] - Y) / cell).astype(int)
    cc = np.floor((X - grid["xmin"]) / cell).astype(int)
    inside = (rr >= 0) & (rr < nrows) & (cc >= 0) & (cc < ncols)
    idx = np.full(X.shape, -1, dtype=np.int64)
    idx[inside] = full[rr[inside], cc[inside]]
    return idx, [[n - ny * dlat, w], [n, w + nx * dlon]]


def pack(values: np.ndarray, idx: np.ndarray) -> str:
    px = np.full(idx.shape, OUTSIDE, dtype=np.uint8)
    m = idx >= 0
    px[m] = values[idx[m]]
    return base64.b64encode(zlib.compress(px.tobytes(), 9)).decode("ascii")


TEMPLATE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Champignons Radar</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 html,body,#map{height:100%;margin:0}
 .leaflet-image-layer{image-rendering:pixelated}
 #banner{position:absolute;z-index:1000;top:8px;left:50px;right:8px;background:#fff;border:2px solid #c0392b;
  border-radius:6px;padding:6px 10px;font:13px system-ui,sans-serif;max-width:760px}
 #legend{position:absolute;z-index:1000;bottom:20px;left:10px;background:#fff;padding:8px 10px;border-radius:6px;
  font:12px system-ui,sans-serif;box-shadow:0 1px 4px #0005;max-width:260px}
 #legend i{display:inline-block;width:12px;height:12px;margin-right:6px;vertical-align:middle}
</style></head><body>
<div id="banner"><b>Indice de POUSSE, pas de comestibilité.</b> <b>Indice non calibré</b> (poids d'habitat et seuils
= hypothèses). Respecter la vigilance Météo-France, la chasse, les propriétés privées et la réglementation locale.
Météo du __DATE__ (maille 5 km, lissée : pas plus précise à 250 m). Habitat 2018 (BD Forêt).<br>
<small>Météo : <a href="https://open-meteo.com/">Open-Meteo.com</a> (CC BY 4.0) · Forêt : IGN BD Forêt V2 ·
Géologie : BRGM BD Charm-50 · Relief : Copernicus GLO-30 © DLR/Airbus (ESA) · Fond : OpenStreetMap</small></div>
<div id="map"></div><div id="legend"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = __DATA__;
const COLORS = __COLORS__;
const map = L.map('map').setView([__LAT__, __LON__], 9);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 17, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
async function inflate(b64) {
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const s = new Blob([bin]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(s).arrayBuffer());
}
function hex(h) { return [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16), parseInt(h.slice(5,7),16)]; }
function colorFinal(v) {
  const b = D.classes;
  if (v >= b.tres_bon) return [...hex(COLORS['très bon']), 210];
  if (v >= b.bon) return [...hex(COLORS['bon']), 200];
  if (v >= b.moyen) return [...hex(COLORS['moyen']), 190];
  return v > 0 ? [...hex(COLORS['faible']), 70] : [0, 0, 0, 0];
}
function colorHab(v) {
  if (v < 5) return [0, 0, 0, 0];
  const t = v / 100;
  return [Math.round(240 - 220*t), Math.round(230 - 120*t), Math.round(140 - 110*t), Math.round(80 + 150*t)];
}
const VALUES = {};
const layers = {};
function render(name, vals) {
  const L_ = D.layers[name];
  const cv = document.createElement('canvas'); cv.width = D.nx; cv.height = D.ny;
  const ctx = cv.getContext('2d'); const img = ctx.createImageData(D.nx, D.ny);
  const f = L_.kind === 'final' ? colorFinal : colorHab;
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    const c = v === 255 ? [0,0,0,0] : v === 254 ? [120,120,120,90] : f(v);
    img.data.set(c, 4*i);
  }
  ctx.putImageData(img, 0, 0);
  return L.imageOverlay(cv.toDataURL(), D.bounds, {opacity: 1});
}
function legend(kind) {
  const el = document.getElementById('legend');
  let h = kind === 'final'
    ? '<b>Score final</b><br>' + ['très bon','bon','moyen','faible'].map(k => '<i style="background:'+COLORS[k]+'"></i>'+k).join('<br>')
    : '<b>Habitat (0-1)</b><br><i style="background:#dce48a"></i>faible<br><i style="background:#14501e"></i>fort';
  h += '<br><i style="background:#787878"></i>inconnu (hors Gard/Hérault)<br><small>Clic : valeurs de la cellule</small>';
  el.innerHTML = h;
}
(async () => {
  for (const name of Object.keys(D.layers)) {
    VALUES[name] = await inflate(D.layers[name].data);
    layers[D.layers[name].label] = render(name, VALUES[name]);
  }
  const first = Object.keys(D.layers)[0];
  layers[D.layers[first].label].addTo(map); legend(D.layers[first].kind);
  const pts = L.layerGroup();
  for (const p of D.points) {
    L.circleMarker([p.lat, p.lon], {radius: 4, color: '#222', weight: 0.5, fillColor: COLORS[p.classe] || '#555',
      fillOpacity: 0.9}).bindPopup('Météo seule : <b>' + p.classe + '</b> (' + p.score + ')<br>Pluie 15 j : ' +
      p.rain_15d_mm + ' mm<br>Jours depuis pluie déclenchante : ' + (p.days_since_trigger ?? 'aucune')).addTo(pts);
  }
  L.control.layers(layers, {'Points météo (5 km)': pts}, {collapsed: false}).addTo(map);
  map.on('baselayerchange', e => { for (const n in D.layers) if (D.layers[n].label === e.name) legend(D.layers[n].kind); });
  map.on('click', e => {
    const [[s, w], [n, ea]] = D.bounds;
    const i = Math.floor((n - e.latlng.lat) / (n - s) * D.ny), j = Math.floor((e.latlng.lng - w) / (ea - w) * D.nx);
    if (i < 0 || j < 0 || i >= D.ny || j >= D.nx) return;
    const rows = Object.keys(D.layers).map(k => {
      const v = VALUES[k][i * D.nx + j];
      return D.layers[k].label + ' : ' + (v === 255 ? 'hors zone' : v === 254 ? 'inconnu' :
        (D.layers[k].kind === 'final' ? v : (v / 100).toFixed(2)));
    });
    L.popup().setLatLng(e.latlng).setContent(rows.join('<br>')).openOn(map);
  });
})();
</script></body></html>
"""


def build_html(layers: dict, idx: np.ndarray, bounds, points: list[dict], date_: str,
               center: tuple[float, float], classes: dict) -> str:
    """layers : nom -> {label, kind ('final'|'habitat'), values (0-1, NaN = inconnu)}."""
    data = {
        "nx": int(idx.shape[1]), "ny": int(idx.shape[0]), "bounds": bounds, "classes": classes,
        "points": [{k: p.get(k) for k in ("lat", "lon", "score", "classe", "rain_15d_mm", "days_since_trigger")}
                   for p in points],
        "layers": {n: {"label": l["label"], "kind": l["kind"], "data": pack(encode_values(l["values"]), idx)}
                   for n, l in layers.items()},
    }
    html = TEMPLATE
    for k, v in {"__DATA__": _safe_json(data), "__COLORS__": json.dumps(COLORS, ensure_ascii=False),
                 "__LAT__": str(center[0]), "__LON__": str(center[1]), "__DATE__": str(date_)}.items():
        html = html.replace(k, v)
    return html


def write(out: Path, **kw) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(**kw), encoding="utf-8")
    return out
