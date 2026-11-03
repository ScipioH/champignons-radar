"""Page locale de saisie du journal (http://127.0.0.1:8765). N'écoute que sur la machine.

Protections : écoute sur 127.0.0.1 ; en-tête Host vérifié (contre le DNS rebinding) ; les écritures exigent
l'en-tête X-Radar (une page web tierce ne peut pas l'envoyer sans pré-vérification CORS, que ce serveur refuse).
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from radar import journal as J
from radar.config import load_config
from radar.habitat import load_species
from radar.mapgen import TILES_JS, _safe_json

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Journal de sorties — Champignons Radar</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
 body{margin:0;font:14px system-ui,sans-serif;display:grid;grid-template-columns:minmax(300px,420px) 1fr;height:100vh}
 aside{padding:12px 16px;overflow:auto;border-right:1px solid #ddd}
 #map{height:100vh}
 label{display:block;margin:8px 0 2px;font-weight:600}
 input,select,textarea{width:100%;box-sizing:border-box;padding:6px;font:inherit}
 button{margin-top:10px;padding:8px 14px;font:inherit;cursor:pointer}
 table{border-collapse:collapse;width:100%;font-size:12px;margin-top:8px}
 td,th{border-bottom:1px solid #eee;padding:3px 4px;text-align:left}
 #msg{margin-top:8px;min-height:18px} .err{color:#b00020} .ok{color:#1b7a3d}
 pre{white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px;font-size:12px}
 @media (max-width:800px){body{grid-template-columns:1fr;height:auto}#map{height:50vh}}
</style></head><body>
<aside>
<h2 style="margin:4px 0">Journal de sorties</h2>
<p style="margin:0;color:#555">Cliquez sur la carte à l'endroit de la sortie. Notez aussi les sorties <b>bredouilles</b>.
Données gardées sur ce PC uniquement (<code>data/journal.db</code>).</p>
<label>Date</label><input id="date" type="date">
<label>Lieu</label><input id="pos" placeholder="cliquer sur la carte" readonly>
<label>Famille cherchée</label><select id="famille"></select>
<label>Résultat</label><select id="resultat"></select>
<label>Note (facultatif)</label><textarea id="note" rows="2" maxlength="500"></textarea>
<button id="save">Enregistrer</button><div id="msg"></div>
<h3>Évaluation du modèle</h3><pre id="eval"></pre>
<h3>Sorties</h3><table><thead><tr><th>Date</th><th>Famille</th><th>Résultat</th><th>Score prédit</th><th></th></tr></thead>
<tbody id="rows"></tbody></table>
</aside>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const FAM = __FAM__, RES = __RES__;
const map = L.map('map').setView([__LAT__, __LON__], 9);
__TILES__
L.control.layers(BASES, {}, {position: 'topright'}).addTo(map);
let marker = null, pos = null;
const H = {'Content-Type': 'application/json', 'X-Radar': '1'};
document.getElementById('date').value = new Date().toISOString().slice(0, 10);
for (const [k, v] of Object.entries(FAM)) document.getElementById('famille').add(new Option(v, k));
for (const [k, v] of Object.entries(RES)) document.getElementById('resultat').add(new Option(k + ' — ' + v, k));
map.on('click', e => {
  pos = e.latlng;
  if (marker) marker.setLatLng(pos); else marker = L.marker(pos).addTo(map);
  document.getElementById('pos').value = pos.lat.toFixed(5) + ', ' + pos.lng.toFixed(5);
});
const layer = L.layerGroup().addTo(map);
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function load() {
  const r = await (await fetch('/api/sorties')).json();
  document.getElementById('eval').textContent = r.evaluation;
  layer.clearLayers();
  document.getElementById('rows').innerHTML = r.rows.map(s => {
    L.circleMarker([s.lat, s.lon], {radius: 6, color: '#222', weight: 1, fillOpacity: 0.9,
      fillColor: ['#999', '#f2c14e', '#7cb342', '#1b7a3d'][s.resultat]}).addTo(layer)
      .bindPopup(esc(s.date) + ' — ' + esc(FAM[s.famille]) + '<br>' + esc(RES[s.resultat]) + (s.note ? '<br>' + esc(s.note) : ''));
    const pred = s.score == null ? '<span title="' + esc(s.erreur) + '">—</span>' : esc(s.score) + ' (' + esc(s.classe) + ')';
    return '<tr><td>' + esc(s.date) + '</td><td>' + esc(FAM[s.famille]) + '</td><td>' + esc(RES[s.resultat]) +
      '</td><td>' + pred + '</td><td><a href="#" data-id="' + s.id + '">suppr.</a></td></tr>';
  }).join('');
}
document.getElementById('rows').addEventListener('click', async e => {
  const id = e.target.dataset && e.target.dataset.id;
  if (!id) return;
  e.preventDefault();
  if (e.target.textContent !== 'confirmer ?') { e.target.textContent = 'confirmer ?'; return; }
  await fetch('/api/delete', {method: 'POST', headers: H, body: JSON.stringify({id: +id})});
  load();
});
document.getElementById('save').onclick = async () => {
  const msg = document.getElementById('msg');
  if (!pos) { msg.className = 'err'; msg.textContent = 'Cliquez d\\'abord sur la carte.'; return; }
  msg.className = ''; msg.textContent = 'Calcul du score du modèle pour ce jour…';
  const body = {date: document.getElementById('date').value, lat: pos.lat, lon: pos.lng,
    famille: document.getElementById('famille').value, resultat: +document.getElementById('resultat').value,
    note: document.getElementById('note').value};
  const r = await fetch('/api/sorties', {method: 'POST', headers: H, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) { msg.className = 'err'; msg.textContent = j.error; return; }
  msg.className = 'ok';
  msg.textContent = 'Enregistré. ' + (j.score != null ? 'Le modèle prédisait ' + j.score + ' (' + j.classe + ').'
    : 'Score du modèle non calculé (' + j.erreur + ') : relancer plus tard.');
  document.getElementById('note').value = '';
  load();
};
load();
</script></body></html>
"""


def page(cfg: dict) -> str:
    fams = {k: f["label"] for k, f in load_species()["families"].items()}
    html = PAGE
    for k, v in {"__TILES__": TILES_JS, "__FAM__": _safe_json(fams), "__RES__": _safe_json(J.RESULTATS),
                 "__LAT__": str(cfg["center"]["lat"]), "__LON__": str(cfg["center"]["lon"])}.items():
        html = html.replace(k, v)
    return html


def make_handler(cfg: dict, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # pas de journal console des coordonnées
            pass

        def _send(self, code: int, body: str, ctype: str = "application/json; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self._send(403, json.dumps({"error": "hôte refusé"}))
                return False
            return True

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return
            if self.path == "/":
                return self._send(200, page(cfg), "text/html; charset=utf-8")
            if self.path == "/api/sorties":
                with lock, J.session() as con:
                    body = {"rows": J.rows(con), "evaluation": J.evaluation_text(J.evaluate(con, cfg))}
                return self._send(200, json.dumps(body, ensure_ascii=False))
            self._send(404, json.dumps({"error": "introuvable"}))

        def do_POST(self):  # noqa: N802
            # lire le corps AVANT de répondre, même pour refuser : sinon Windows coupe la connexion (RST)
            # et le client reçoit une erreur réseau au lieu du 403/400
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if not 0 <= n <= 10_000:
                self.close_connection = True
                return self._send(413, json.dumps({"error": "requête invalide ou trop grande"}))
            raw = self.rfile.read(n)
            if not self._host_ok():
                return
            if self.headers.get("X-Radar") != "1":
                return self._send(403, json.dumps({"error": "en-tête manquant"}))
            try:
                data = json.loads(raw or b"{}")
                with lock, J.session() as con:
                    if self.path == "/api/sorties":
                        out = J.add(con, cfg, data)
                    elif self.path == "/api/delete":
                        J.delete(con, int(data["id"]))
                        out = {"ok": True}
                    else:
                        return self._send(404, json.dumps({"error": "introuvable"}))
                self._send(200, json.dumps(out, ensure_ascii=False))
            except (ValueError, KeyError, TypeError) as e:
                self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))

        def do_OPTIONS(self):  # noqa: N802 - pré-vérification CORS refusée
            self._send(403, json.dumps({"error": "refusé"}))

    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    cfg = load_config()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Journal ouvert sur {url} — fermer cette fenêtre pour arrêter.")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
