"""Carte HTML de l'habitat et du score final à 250 m, avec curseur temporel J-7 → J+7.

Les cellules (grille Lambert 93) sont rééchantillonnées au plus proche sur une grille régulière lat/lon pour
être posées en `imageOverlay` Leaflet. Les valeurs (0-100, 254 = inconnu, 255 = hors zone) sont embarquées
compressées (zlib + base64), une par jour pour les couches de score, et colorisées dans le navigateur à la
demande ; un clic affiche la valeur de chaque couche pour le jour affiché.
"""
from __future__ import annotations

import base64
import json
import math
import zlib
from pathlib import Path

import numpy as np
from pyproj import Transformer

from radar.mapgen import COLORS, TILES_JS, _safe_json
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
 #time{position:absolute;z-index:1000;bottom:20px;left:50%;transform:translateX(-50%);background:#fff;
  padding:8px 12px;border-radius:6px;font:13px system-ui,sans-serif;box-shadow:0 1px 4px #0005;
  width:min(520px,calc(100% - 340px));min-width:260px;text-align:center}
 #time .row{display:flex;align-items:center;gap:8px}
 #time input{flex:1}
 #time button{font:16px system-ui;border:1px solid #bbb;background:#f6f6f6;border-radius:4px;cursor:pointer;width:30px}
 #daylabel{font-weight:600}
 #daynote{font-size:11px;color:#555;min-height:14px}
 #daynote.warn{color:#b35900}
</style></head><body>
<div id="banner"><b>Indice de POUSSE, pas de comestibilité.</b> <b>Indice non calibré</b> (poids d'habitat et seuils
= hypothèses). Respecter la vigilance Météo-France, la chasse, les propriétés privées et la réglementation locale.
Calcul du __DATE__ ; météo en maille 5 km, lissée (pas plus précise à 250 m). Habitat 2018 (BD Forêt).<br>
<small>Météo : <a href="https://open-meteo.com/">Open-Meteo.com</a> (CC BY 4.0) · Forêt : IGN BD Forêt V2 ·
Géologie : BRGM BD Charm-50 · Relief : Copernicus GLO-30 © DLR/Airbus (ESA) · Fond : IGN Géoplateforme / OpenTopoMap</small></div>
<div id="map"></div><div id="legend"></div>
<div id="time"><div class="row"><button id="prev" title="jour précédent">‹</button>
<input id="slider" type="range" step="1"><button id="next" title="jour suivant">›</button></div>
<div id="daylabel"></div><div id="daynote"></div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = __DATA__;
const COLORS = __COLORS__;
const map = L.map('map').setView([__LAT__, __LON__], 9);
__TILES__
L.control.layers(BASES, {}, {position: 'bottomright'}).addTo(map);
const TODAY = D.days.findIndex(d => d.offset === 0);
let day = TODAY;
async function inflate(b64) {
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const s = new Blob([bin]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(s).arrayBuffer());
}
function hex(h) { return [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16), parseInt(h.slice(5,7),16)]; }
function classOf(v) {
  const b = D.classes;
  return v >= b.tres_bon ? 'très bon' : v >= b.bon ? 'bon' : v >= b.moyen ? 'moyen' : 'faible';
}
function colorFinal(v) {
  const k = classOf(v), a = {'très bon': 210, 'bon': 200, 'moyen': 190, 'faible': 70}[k];
  return k === 'faible' && v === 0 ? [0, 0, 0, 0] : [...hex(COLORS[k]), a];
}
function colorHab(v) {
  if (v < 5) return [0, 0, 0, 0];
  const t = v / 100;
  return [Math.round(240 - 220*t), Math.round(230 - 120*t), Math.round(140 - 110*t), Math.round(80 + 150*t)];
}
const dayOf = name => D.layers[name].data.length > 1 ? day : 0;   // l'habitat ne dépend pas du jour
const VALUES = {}, URLS = {};
async function values(name, d) {
  const key = name + '|' + d;
  if (!VALUES[key]) VALUES[key] = await inflate(D.layers[name].data[d]);
  return VALUES[key];
}
async function url(name, d) {
  const key = name + '|' + d;
  if (URLS[key]) return URLS[key];
  const vals = await values(name, d);
  const cv = document.createElement('canvas'); cv.width = D.nx; cv.height = D.ny;
  const ctx = cv.getContext('2d'); const img = ctx.createImageData(D.nx, D.ny);
  const f = D.layers[name].kind === 'final' ? colorFinal : colorHab;
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    img.data.set(v === 255 ? [0,0,0,0] : v === 254 ? [120,120,120,90] : f(v), 4*i);
  }
  ctx.putImageData(img, 0, 0);
  return URLS[key] = cv.toDataURL();
}
function legend(kind) {
  let h = kind === 'final'
    ? '<b>Score final</b><br>' + ['très bon','bon','moyen','faible'].map(k => '<i style="background:'+COLORS[k]+'"></i>'+k).join('<br>')
    : '<b>Habitat (0-1)</b> <small>(indépendant du jour)</small><br><i style="background:#dce48a"></i>faible<br><i style="background:#14501e"></i>fort';
  h += '<br><i style="background:#787878"></i>inconnu (hors Gard/Hérault)<br><small>Clic : valeurs de la cellule</small>';
  document.getElementById('legend').innerHTML = h;
}
const FMT = new Intl.DateTimeFormat('fr-FR', {weekday: 'long', day: 'numeric', month: 'long'});
function dayText(d) {
  const o = D.days[d].offset, date = new Date(D.days[d].date + 'T12:00:00');
  const j = o === 0 ? "aujourd'hui" : 'J' + (o > 0 ? '+' : '−') + Math.abs(o);
  return j + ' — ' + FMT.format(date);
}
function dayNote(d) {
  const o = D.days[d].offset;
  if (o < 0) return ['Rétrospectif : météo reconstituée par le modèle, pas des mesures.', false];
  if (o === 0) return ['', false];
  if (o <= 3) return ['Prévision : fiabilité correcte, décroissante.', false];
  return ['Prévision lointaine : tendance seulement, à revérifier les jours suivants.', true];
}
const overlays = {}, markers = [];
let active = null;
async function refresh() {
  document.getElementById('slider').value = day;
  document.getElementById('daylabel').textContent = dayText(day);
  const [note, warn] = dayNote(day), el = document.getElementById('daynote');
  el.textContent = note; el.className = warn ? 'warn' : '';
  for (const name in overlays) if (map.hasLayer(overlays[name])) overlays[name].setUrl(await url(name, dayOf(name)));
  for (const [m, p] of markers) {
    const v = p.timeline ? p.timeline[day] : p.score;
    m.setStyle({fillColor: v == null ? '#555' : COLORS[classOf(v)]});
    m.setPopupContent('Météo seule (' + dayText(day) + ') : <b>' + (v == null ? 'n/a' : classOf(v) + '</b> (' + v + ')'));
  }
}
(async () => {
  const s = document.getElementById('slider');
  s.min = 0; s.max = D.days.length - 1;
  s.addEventListener('input', () => { day = +s.value; refresh(); });
  document.getElementById('prev').onclick = () => { if (day > 0) { day--; refresh(); } };
  document.getElementById('next').onclick = () => { if (day < D.days.length - 1) { day++; refresh(); } };
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' && day > 0) { day--; refresh(); }
    if (e.key === 'ArrowRight' && day < D.days.length - 1) { day++; refresh(); }
  });
  const byLabel = {};
  for (const name of Object.keys(D.layers)) {
    overlays[name] = L.imageOverlay(await url(name, dayOf(name)), D.bounds);
    byLabel[D.layers[name].label] = overlays[name];
  }
  const first = Object.keys(D.layers)[0];
  overlays[first].addTo(map); legend(D.layers[first].kind);
  const pts = L.layerGroup();
  for (const p of D.points) {
    const m = L.circleMarker([p.lat, p.lon], {radius: 4, color: '#222', weight: 0.5, fillOpacity: 0.9}).bindPopup('');
    markers.push([m, p]); m.addTo(pts);
  }
  L.control.layers(byLabel, {'Points météo (5 km)': pts}, {collapsed: false}).addTo(map);
  map.on('baselayerchange', async e => {
    for (const n in D.layers) if (D.layers[n].label === e.name) {
      legend(D.layers[n].kind); overlays[n].setUrl(await url(n, dayOf(n)));
    }
  });
  map.on('click', async e => {
    const [[s_, w], [n, ea]] = D.bounds;
    const i = Math.floor((n - e.latlng.lat) / (n - s_) * D.ny), j = Math.floor((e.latlng.lng - w) / (ea - w) * D.nx);
    if (i < 0 || j < 0 || i >= D.ny || j >= D.nx) return;
    const rows = ['<b>' + dayText(day) + '</b>'];
    for (const k of Object.keys(D.layers)) {
      const v = (await values(k, dayOf(k)))[i * D.nx + j];
      rows.push(D.layers[k].label + ' : ' + (v === 255 ? 'hors zone' : v === 254 ? 'inconnu' :
        (D.layers[k].kind === 'final' ? v + ' (' + classOf(v) + ')' : (v / 100).toFixed(2))));
    }
    L.popup().setLatLng(e.latlng).setContent(rows.join('<br>')).openOn(map);
  });
  refresh();
})();
</script></body></html>
"""


def build_html(layers: dict, idx: np.ndarray, bounds, points: list[dict], days: list[dict],
               center: tuple[float, float], classes: dict) -> str:
    """layers : nom -> {label, kind ('final'|'habitat'), values : liste de tableaux 0-1 (NaN = inconnu)}.

    Les couches 'final' ont une valeur par jour de `days` ({offset, date}) ; l'habitat, une seule.
    """
    for n, l in layers.items():
        if len(l["values"]) not in (1, len(days)):
            raise ValueError(f"couche {n} : {len(l['values'])} jours pour un curseur de {len(days)}")
    today = next(d["date"] for d in days if d["offset"] == 0)
    data = {
        "nx": int(idx.shape[1]), "ny": int(idx.shape[0]), "bounds": bounds, "classes": classes, "days": days,
        "points": [{k: p.get(k) for k in ("lat", "lon", "score", "timeline")} for p in points],
        "layers": {n: {"label": l["label"], "kind": l["kind"],
                       "data": [pack(encode_values(v), idx) for v in l["values"]]}
                   for n, l in layers.items()},
    }
    html = TEMPLATE
    for k, v in {"__TILES__": TILES_JS, "__DATA__": _safe_json(data),
                 "__COLORS__": json.dumps(COLORS, ensure_ascii=False),
                 "__LAT__": str(center[0]), "__LON__": str(center[1]), "__DATE__": str(today)}.items():
        html = html.replace(k, v)
    return html


def write(out: Path, **kw) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(**kw), encoding="utf-8")
    return out
