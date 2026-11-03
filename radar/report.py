"""Rapport de sortie (phase 3) : meilleures zones par jour disponible, carte zoomée, email HTML + PDF.

Contenu : pour chaque jour disponible de J à J+horizon (week-end, férié, soir de semaine si la lumière le
permet), les `top_n` meilleures zones par famille (cellules 250 m séparées d'au moins `min_separation_km`),
avec habitat, trajet, et rappels de sécurité/réglementation. Image : fond Plan IGN + score du meilleur jour.
Envoi SMTP seulement avec --send et les variables d'environnement (jamais d'adresse dans le dépôt).
"""
from __future__ import annotations

import html as H
import io
import logging
import math
import os
import smtplib
import urllib.request
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import numpy as np

from radar import calendar_fr as C
from radar.config import load_env, resolve
from radar.mapgen import COLORS
from radar.score import classify

log = logging.getLogger("radar.report")
IGN_TILE = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
            "&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png")
ATTRIBUTIONS = ("Météo : Open-Meteo.com (CC BY 4.0). Forêt : © IGN BD Forêt V2. Géologie : © BRGM BD Charm-50. "
                "Relief : © ESA/Copernicus GLO-30. Trajets : © OpenStreetMap contributors, OSRM. "
                "Zones protégées : PatriNat (OFB/MNHN), Géoplateforme IGN. Fond de carte : © IGN Plan IGN.")
WARNINGS = [
    "Indice de POUSSE, jamais de comestibilité : ne consommez rien sans identification certaine (pharmacien, "
    "société mycologique). En cas de doute, ne pas consommer.",
    "Indice non calibré : seuils et poids d'habitat sont des hypothèses tant que le journal compte moins de 20 sorties.",
    "Vérifier la vigilance Météo-France avant de partir (orages, pluie-inondation) : https://vigilance.meteofrance.fr",
    "Respecter les propriétés privées, les arrêtés municipaux et le règlement des zones protégées (cœur du Parc "
    "national des Cévennes : cueillette réglementée).",
]


# --------------------------------------------------------------------------- sélection des zones

def pick_zones(score: np.ndarray, x: np.ndarray, y: np.ndarray, top_n: int, sep_m: float,
               mask: np.ndarray | None = None) -> list[int]:
    """Indices des meilleures cellules, deux à deux distantes d'au moins sep_m (glouton)."""
    ok = np.isfinite(score) & (score >= 0)
    if mask is not None:
        ok &= mask
    cand = np.flatnonzero(ok)
    cand = cand[np.argsort(score[cand])[::-1]]
    chosen: list[int] = []
    for i in cand:
        if all(math.hypot(x[i] - x[j], y[i] - y[j]) >= sep_m for j in chosen):
            chosen.append(int(i))
            if len(chosen) == top_n:
                break
    return chosen


def describe_cell(res: dict, i: int) -> dict:
    t, meta = res["t"], res["meta"]
    fp, gp = t["forest_pct"][i], t["geol_pct"][i]
    fk, gk = int(np.argmax(fp)), int(np.argmax(gp))
    d = {"lat": float(t["lat"][i]), "lon": float(t["lon"][i]),
         "alt": None if np.isnan(t["alt"][i]) else int(round(float(t["alt"][i]))),
         "foret": f"{meta['forest_classes'][fk].replace('_', ' ')} {int(fp[fk])} %",
         "sol": f"{meta['geol_classes'][gk]} {int(gp[gk])} %",
         "trajet": None, "reglemente": False}
    if "access" in res:
        a, am = res["access"], res["access_meta"]
        d["trajet"] = int(round(float(a["travel_min"][i])))
        d["reglemente"] = bool((a["protected"][i, np.array(am["factors"]) >= 1] > 0).any())
    return d


def osm_link(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=15/{lat:.5f}/{lon:.5f}"


def build(res: dict, weather: dict, cfg: dict) -> dict:
    """Structure du rapport : jours disponibles et zones par famille."""
    rc, av = cfg["report"], cfg["availability"]
    t, scfg = res["t"], res["scfg"]
    c = cfg["center"]
    travel = res["access"]["travel_min"] if "access" in res else np.full(len(t["x"]), float(av["max_travel_minutes"]))
    days = []
    for i, (o, ds) in enumerate(zip(weather["offsets"], weather["dates"])):
        if o < 0 or o > rc["horizon_days"]:
            continue
        d = date.fromisoformat(ds)
        kind, label = C.day_kind(d)
        mask = None
        info = {"date": ds, "offset": o, "kind": kind, "label": label}
        if kind == "soir":
            # jour restant sur place = (coucher - départ) - trajet ; coucher pris au centre (écart ~1-2 min)
            minutes = C.evening_minutes(d, c["lat"], c["lon"], 0.0, av["weekday_depart_hour"]) - travel
            mask = minutes >= av["min_forage_minutes"]
            info["sunset"] = C.sunset(d, c["lat"], c["lon"]).strftime("%H:%M")
            if not mask.any():
                continue                                     # soir sans lumière suffisante : pas un créneau
        fams = {}
        for k, fam in scfg["families"].items():
            sc = res["final_days"][k][i]
            zs = pick_zones(sc, t["x"], t["y"], rc["top_n"], rc["min_separation_km"] * 1000, mask)
            fams[k] = {"label": fam["label"], "zones": [
                {**describe_cell(res, z), "score": round(float(sc[z])), "classe": classify(float(sc[z]), cfg["score"])}
                for z in zs]}
        info["families"] = fams
        info["best"] = max((z["score"] for f in fams.values() for z in f["zones"]), default=0)
        info["index"] = i
        days.append(info)
    meteo_med = {d: float(np.median([p["timeline"][i] for p in weather["points"] if p["timeline"][i] is not None]))
                 for i, d in enumerate(weather["dates"])}
    hunting = date.fromisoformat(weather["date"]).month in rc["hunting_months"]
    return {"date": weather["date"], "days": days, "meteo_median": meteo_med, "hunting": hunting,
            "origin_is_home": bool(res.get("access_meta", {}).get("origin_is_home"))}


# --------------------------------------------------------------------------- texte / HTML

FR_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]


def fr_date(ds: str) -> str:
    d = date.fromisoformat(ds)
    return f"{FR_DAYS[d.weekday()]} {d.day} {FR_MONTHS[d.month - 1]}"


def zone_line(n: int, z: dict) -> str:
    parts = [f"{n}. {z['score']} ({z['classe']})", f"{z['lat']:.4f}, {z['lon']:.4f}"]
    if z["alt"] is not None:
        parts.append(f"{z['alt']} m")
    parts += [f"forêt : {z['foret']}", f"sol : {z['sol']}"]
    if z["trajet"] is not None:
        parts.append(f"~{z['trajet']} min de trajet")
    if z["reglemente"]:
        parts.append("cœur du Parc national : cueillette réglementée")
    return " · ".join(parts)


def summary_line(rep: dict, cfg: dict) -> str:
    if not rep["days"]:
        return "Aucun créneau disponible sur la période."
    best = max(rep["days"], key=lambda d: d["best"])
    return (f"Meilleur créneau : {fr_date(best['date'])} ({best['label']}), meilleure zone à {best['best']}/100 "
            f"({classify(best['best'], cfg['score'])}).")


def to_text(rep: dict, cfg: dict) -> str:
    L = [f"Champignons Radar — rapport du {fr_date(rep['date'])}", "", summary_line(rep, cfg), ""]
    for d in rep["days"]:
        head = f"== {fr_date(d['date'])} (J+{d['offset']}, {d['label']}"
        head += f", coucher {d['sunset']}" if d.get("sunset") else ""
        head += f") — météo médiane {rep['meteo_median'][d['date']]:.0f}/100"
        L.append(head + (" — prévision lointaine, tendance seulement" if d["offset"] > 3 else ""))
        for f in d["families"].values():
            L.append(f"  {f['label']} :")
            L += [f"    {zone_line(n, z)}" for n, z in enumerate(f["zones"], 1)] or ["    aucune zone"]
        L.append("")
    if rep["hunting"]:
        L.append("Chasse : saison ouverte. Battues fréquentes les week-ends et jours fériés : vêtements voyants, "
                 "respecter les panneaux « chasse en cours ».")
    L += ["", "Rappels :"] + [f"- {w}" for w in WARNINGS] + ["", ATTRIBUTIONS]
    return "\n".join(L)


def to_html(rep: dict, cfg: dict, with_image: bool) -> str:
    e = H.escape
    out = [f"<h2>Champignons Radar — {e(fr_date(rep['date']))}</h2>",
           f"<p><b>{e(summary_line(rep, cfg))}</b></p>"]
    if with_image:
        out.append('<p><img src="cid:carte" alt="carte" style="max-width:100%;border:1px solid #ccc"></p>')
    for d in rep["days"]:
        warn = " <i>(prévision lointaine : tendance seulement)</i>" if d["offset"] > 3 else ""
        sun = f", coucher {e(d['sunset'])}" if d.get("sunset") else ""
        out.append(f"<h3>{e(fr_date(d['date']))} — {e(d['label'])}{sun}{warn}</h3>"
                   f"<p>Météo médiane de la zone : {rep['meteo_median'][d['date']]:.0f}/100</p>")
        for f in d["families"].values():
            out.append(f"<p><b>{e(f['label'])}</b></p><ol>")
            for z in f["zones"]:
                col = COLORS.get(z["classe"], "#999")
                extra = [x for x in (f"{z['alt']} m" if z["alt"] is not None else None,
                                     f"forêt : {z['foret']}", f"sol : {z['sol']}",
                                     f"~{z['trajet']} min" if z["trajet"] is not None else None,
                                     "cœur du Parc : cueillette réglementée" if z["reglemente"] else None) if x]
                out.append(f'<li><span style="background:{col};padding:0 6px;border-radius:3px">{z["score"]} '
                           f'{e(z["classe"])}</span> <a href="{osm_link(z["lat"], z["lon"])}">'
                           f'{z["lat"]:.4f}, {z["lon"]:.4f}</a> — {e(" · ".join(extra))}</li>')
            out.append("</ol>")
    if rep["hunting"]:
        out.append("<p><b>Chasse</b> : saison ouverte, battues fréquentes les week-ends et jours fériés. "
                   "Vêtements voyants.</p>")
    out.append("<hr><ul>" + "".join(f"<li><small>{e(w)}</small></li>" for w in WARNINGS) + "</ul>")
    out.append(f"<p><small>{e(ATTRIBUTIONS)}</small></p>")
    return "<html><body style=\"font-family:system-ui,sans-serif;max-width:760px\">" + "\n".join(out) + "</body></html>"


# --------------------------------------------------------------------------- image

def _merc(lon, lat):
    r = 6378137.0
    return r * np.radians(lon), r * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def fetch_basemap(w: float, s: float, e: float, n: float, z: int, get=None):
    """Mosaïque Plan IGN couvrant la boîte ; renvoie (image RGB, étendue mercator) ou None si échec réseau."""
    from PIL import Image

    def _get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "champignons-radar/0.1 (usage personnel)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    get = get or _get
    x0, y0 = (int(v) for v in _tile_xy(w, n, z))
    x1, y1 = (int(v) for v in _tile_xy(e, s, z))
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 64:
        raise ValueError("trop de tuiles")
    img = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), "white")
    try:
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                img.paste(Image.open(io.BytesIO(get(IGN_TILE.format(z=z, x=tx, y=ty)))).convert("RGB"),
                          ((tx - x0) * 256, (ty - y0) * 256))
    except Exception as ex:  # noqa: BLE001 - le rapport reste utile sans fond de carte
        log.warning("fond de carte indisponible : %s", ex)
        return None
    r, n_ = 6378137.0, 2 ** z
    size = 2 * math.pi * r / n_
    ox = -math.pi * r
    return img, (ox + x0 * size, ox + (x1 + 1) * size, math.pi * r - (y1 + 1) * size, math.pi * r - y0 * size)


def render_map(res: dict, rep: dict, cfg: dict, out: Path, idx: np.ndarray, bounds, basemap=True) -> Path | None:
    """Carte zoomée sur les zones du meilleur jour : fond IGN, score max des familles, zones numérotées."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not rep["days"]:
        return None
    day = max(rep["days"], key=lambda d: d["best"])
    zones = [(f, n, z) for f in day["families"].values() for n, z in enumerate(f["zones"], 1)]
    if not zones:
        return None
    lats = [z["lat"] for _, _, z in zones]
    lons = [z["lon"] for _, _, z in zones]
    pad = 0.06
    s, n = min(lats) - pad, max(lats) + pad
    w, e = min(lons) - pad, max(lons) + pad
    span = max(e - w, (n - s) * 1.4, 0.35)
    cx, cy = (w + e) / 2, (s + n) / 2
    w, e, s, n = cx - span / 2, cx + span / 2, cy - span / 2.8, cy + span / 2.8
    z = int(max(8, min(13, math.floor(math.log2(4 * 360 / span)))))

    fig, ax = plt.subplots(figsize=(9, 6.5), dpi=110)
    bm = fetch_basemap(w, s, e, n, z) if basemap else None
    if bm:
        img, ext = bm
        ax.imshow(img, extent=ext, origin="upper", zorder=0)
    # score du jour : max des familles, classes colorées
    sc = np.fmax.reduce([res["final_days"][k][day["index"]] for k in res["final_days"]])  # NaN ignoré, sans alerte
    px = np.full(idx.shape, np.nan)
    m = idx >= 0
    px[m] = sc[idx[m]]
    b = cfg["score"]["classes"]
    rgba = np.zeros(idx.shape + (4,))
    for lo, name, alpha in ((b["moyen"], "moyen", 0.55), (b["bon"], "bon", 0.65), (b["tres_bon"], "très bon", 0.75)):
        h = COLORS[name]
        sel = px >= lo
        rgba[sel] = [int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255, alpha]
    (bs, bw), (bn, be) = bounds
    X0, Y0 = _merc(bw, bs)
    X1, Y1 = _merc(be, bn)
    ax.imshow(rgba, extent=(X0, X1, Y0, Y1), origin="upper", zorder=1, interpolation="nearest")
    # une étiquette par lieu : « C1·G1 » si deux familles tombent sur la même cellule
    labels: dict[tuple[float, float], list[str]] = {}
    for (k, f) in day["families"].items():
        for i_, zz in enumerate(f["zones"], 1):
            labels.setdefault((zz["lon"], zz["lat"]), []).append(f"{k[0].upper()}{i_}")
    for (lon, lat), labs in labels.items():
        mx, my = _merc(lon, lat)
        ax.plot(mx, my, "o", ms=10, mfc="white", mec="black", mew=1.5, zorder=3)
        ax.annotate("·".join(labs), (mx, my), xytext=(7, 5), textcoords="offset points", fontsize=9,
                    weight="bold", zorder=4, bbox={"boxstyle": "round,pad=0.15", "fc": "white", "alpha": 0.85})
    WX, SY = _merc(w, s)
    EX, NY = _merc(e, n)
    ax.set_xlim(WX, EX)
    ax.set_ylim(SY, NY)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"{fr_date(day['date'])} — C = cèpes, L = lactaires, G = girolles ; couleur = score (moyen → très bon)",
                 fontsize=10)
    fig.text(0.01, 0.01, "Fond © IGN Plan IGN · Score : indice de pousse non calibré, pas de comestibilité",
             fontsize=7, color="#444")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


# --------------------------------------------------------------------------- PDF & email

def _pdf_safe(s: str) -> str:
    """Les polices PDF standard (WinAnsi) n'ont pas certains symboles."""
    for a, b in (("≤", "<="), ("≥", ">="), ("→", "->"), ("−", "-"), ("~", "env. ")):
        s = s.replace(a, b)
    return s


def write_pdf(text: str, png: Path | None, out: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.4 * cm, bottomMargin=1.4 * cm, title="Champignons Radar")
    flow = []
    lines = _pdf_safe(text).splitlines()
    flow.append(Paragraph(H.escape(lines[0]), st["Title"]))
    if png:
        flow += [Image(str(png), width=17.5 * cm, height=17.5 * cm * 6.5 / 9), Spacer(1, 0.3 * cm)]
    zone = ParagraphStyle("zone", parent=st["BodyText"], leftIndent=0.6 * cm, fontSize=9, leading=11)
    for ln in lines[1:]:
        if not ln.strip():
            flow.append(Spacer(1, 0.2 * cm))
        elif ln.startswith("=="):
            flow.append(Paragraph(H.escape(ln.strip("= ")), st["Heading3"]))
        elif ln.startswith("    "):
            flow.append(Paragraph(H.escape(ln.strip()), zone))
        elif ln.startswith("  "):
            flow.append(Paragraph("<b>" + H.escape(ln.strip()) + "</b>", st["BodyText"]))
        else:
            flow.append(Paragraph(H.escape(ln), st["BodyText"]))
    doc.build(flow)
    return out


def make_email(rep: dict, cfg: dict, text: str, html: str, png: Path | None, pdf: Path, to: str, sender: str):
    msg = EmailMessage()
    best = summary_line(rep, cfg)
    msg["Subject"] = f"Champignons Radar — {fr_date(rep['date'])} — {best[:80]}"
    msg["From"], msg["To"] = sender, to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if png:
        body = msg.get_body(("html",))
        assert isinstance(body, EmailMessage)
        body.add_related(png.read_bytes(), maintype="image", subtype="png", cid="<carte>")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    return msg


def send(msg: EmailMessage) -> None:
    load_env()
    host, user, pwd = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not (host and user and pwd and msg["To"]):
        raise RuntimeError("SMTP non configuré : définir SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_TO "
                           "(fichier .env local ou secrets GitHub)")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


def run(res: dict, weather: dict, cfg: dict, do_send: bool, basemap: bool = True) -> dict:
    from radar import rastermap

    rep = build(res, weather, cfg)
    out = resolve(f"data/out/report/{rep['date']}")
    out.mkdir(parents=True, exist_ok=True)
    idx, bounds = rastermap.display_grid(res["t"], res["meta"]["grid"])
    png = render_map(res, rep, cfg, out / "carte.png", idx, bounds, basemap=basemap)
    text = to_text(rep, cfg)
    html = to_html(rep, cfg, png is not None)
    (out / "rapport.txt").write_text(text, encoding="utf-8")
    (out / "email.html").write_text(html.replace("cid:carte", "carte.png"), encoding="utf-8")
    pdf = write_pdf(text, png, out / f"champignons-radar-{rep['date']}.pdf")
    load_env()
    to = os.environ.get("REPORT_TO", "")
    sender = os.environ.get("REPORT_FROM") or os.environ.get("SMTP_USER", "")
    msg = make_email(rep, cfg, text, html, png, pdf, to, sender)
    (out / "email.eml").write_bytes(bytes(msg))
    sent = False
    if do_send:
        send(msg)
        sent = True
    return {"dir": out, "text": text, "sent": sent, "report": rep}
