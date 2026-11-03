"""Journal de sorties et calibration (phase 4).

Chaque sortie (date, lieu, famille, résultat) est enregistrée dans `data/journal.db` (SQLite, NON versionné :
ce sont vos coins) avec l'instantané du modèle pour ce jour et ce lieu (score météo, habitat, score final,
version des réglages). L'évaluation compare les résultats aux classes prédites ; tant que le journal compte
moins de `calibration.min_sorties` sorties, aucune recalibration n'est proposée (« indice non calibré »).

Saisie : `python -m radar journal` ouvre une page locale (127.0.0.1 uniquement) avec carte cliquable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from radar import habitat as Hb
from radar import score as S
from radar import weather as W
from radar.config import ROOT, resolve

RESULTATS = {0: "rien", 1: "quelques-uns", 2: "bonne cueillette", 3: "excellente"}
SCHEMA = """CREATE TABLE IF NOT EXISTS sorties (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, famille TEXT NOT NULL,
  resultat INTEGER NOT NULL CHECK (resultat BETWEEN 0 AND 3), note TEXT DEFAULT '',
  meteo REAL, habitat REAL, score REAL, classe TEXT, modele TEXT, erreur TEXT,
  created_at TEXT NOT NULL)"""


def db_path() -> Path:
    return resolve("data/journal.db")


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    return con


@contextmanager
def session(path: Path | None = None):
    """Connexion fermée en sortie (le `with` natif de sqlite3 valide mais ne ferme pas)."""
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def model_version() -> str:
    """Empreinte des réglages du modèle : une recalibration change l'empreinte (comparaisons honnêtes)."""
    h = hashlib.sha256()
    for f in ("config/config.yaml", "config/species.yaml", "config/terrain.yaml"):
        h.update((ROOT / f).read_bytes())
    return h.hexdigest()[:10]


# --------------------------------------------------------------------------- instantané du modèle

def weather_url(lat: float, lon: float, start: date, end: date, today: date) -> str:
    """API de prévision (passé récent + prévision) si la période est récente, sinon archive (réanalyse)."""
    recent = (today - start).days <= 85
    base = "https://api.open-meteo.com/v1/forecast" if recent else "https://archive-api.open-meteo.com/v1/archive"
    if not recent:
        end = min(end, today - timedelta(days=2))
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "daily": W.DAILY_VARS, "timezone": "Europe/Paris",
         "start_date": start.isoformat(), "end_date": end.isoformat()}
    return base + "?" + urllib.parse.urlencode(q, safe=",")


def snapshot(cfg: dict, d: date, lat: float, lon: float, famille: str, today: date | None = None,
             get=None, terrain=None) -> dict:
    """Score du modèle à cette date et ce lieu (sans le facteur trajet : on évalue la pousse, pas l'accès)."""
    today = today or date.today()
    if d > today + timedelta(days=7):
        raise ValueError("date trop lointaine")
    lookback = cfg["score"]["delai"]["lookback_days"] + 2
    horizon = cfg["score"]["prevision"]["horizon_days"]
    start, end = d - timedelta(days=lookback), d + timedelta(days=horizon)
    data = (get or (lambda u: W._http_get_json(u, 3)))(weather_url(lat, lon, start, end, today))
    w = W._parse(data)
    idx = w["time"].index(d.isoformat())
    r = S.score_point(w["precip"], w["tmin"], idx, w["elevation"], cfg["score"])
    if r["score"] is None:
        raise ValueError("météo inexploitable")
    scfg = Hb.load_species()
    t, meta = terrain or Hb.load_terrain()
    from pyproj import Transformer
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True).transform(lon, lat)
    i = int(np.argmin(np.hypot(t["x"] - x, t["y"] - y)))
    if np.hypot(t["x"][i] - x, t["y"][i] - y) > 300:
        hab = float("nan")                                   # hors de la grille terrain
    else:
        one = {k: t[k][i:i + 1] for k in ("forest_pct", "geol_pct", "alt", "north", "east", "coverage_pct")}
        hab = float(Hb.habitat_family(one, scfg["families"][famille], scfg, meta["forest_classes"],
                                      meta["geol_classes"], d.month)[0])
    season = Hb.season_factor(d.month, scfg["families"][famille]["season_months"], scfg["season"])
    final = float(Hb.final_score(np.array([r["score"] / 100.0]), np.array([hab]), season, cfg["score"])[0])
    return {"meteo": r["score"], "habitat": None if np.isnan(hab) else round(hab, 3),
            "score": None if np.isnan(final) else round(final, 1),
            "classe": None if np.isnan(final) else S.classify(final, cfg["score"])}


# --------------------------------------------------------------------------- saisie

def validate(entry: dict, families: list[str], today: date | None = None) -> dict:
    today = today or date.today()
    d = date.fromisoformat(str(entry["date"]))
    if d > today:
        raise ValueError("une sortie ne peut pas être dans le futur")
    lat, lon = float(entry["lat"]), float(entry["lon"])
    if not (41 <= lat <= 52 and -6 <= lon <= 10):
        raise ValueError("coordonnées hors de France")
    if entry["famille"] not in families:
        raise ValueError(f"famille inconnue : {entry['famille']}")
    res = int(entry["resultat"])
    if res not in RESULTATS:
        raise ValueError("résultat attendu : 0 à 3")
    return {"date": d, "lat": lat, "lon": lon, "famille": entry["famille"], "resultat": res,
            "note": str(entry.get("note", ""))[:500]}


def add(con: sqlite3.Connection, cfg: dict, entry: dict, snap=None) -> dict:
    snap = snap or snapshot          # résolu à l'appel (remplaçable dans les tests)
    e = validate(entry, list(Hb.load_species()["families"]))
    try:
        s, err = snap(cfg, e["date"], e["lat"], e["lon"], e["famille"]), None
    except Exception as ex:  # noqa: BLE001 - la sortie est gardée même sans instantané (recalculable)
        s, err = {"meteo": None, "habitat": None, "score": None, "classe": None}, str(ex)[:200]
    cur = con.execute(
        "INSERT INTO sorties (date, lat, lon, famille, resultat, note, meteo, habitat, score, classe, modele, erreur,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (e["date"].isoformat(), e["lat"], e["lon"], e["famille"], e["resultat"], e["note"], s["meteo"], s["habitat"],
         s["score"], s["classe"], model_version(), err, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return {"id": cur.lastrowid, **s, "erreur": err}


def delete(con: sqlite3.Connection, sid: int) -> None:
    con.execute("DELETE FROM sorties WHERE id = ?", (int(sid),))
    con.commit()


def rows(con: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM sorties ORDER BY date DESC, id DESC")]


def recompute(con: sqlite3.Connection, cfg: dict, snap=None) -> int:
    """Recalcule l'instantané des sorties sans score (échec réseau) ou d'une ancienne version du modèle."""
    snap = snap or snapshot
    n, ver = 0, model_version()
    for r in rows(con):
        if r["score"] is not None and r["modele"] == ver:
            continue
        try:
            s = snap(cfg, date.fromisoformat(r["date"]), r["lat"], r["lon"], r["famille"])
        except Exception:  # noqa: BLE001
            continue
        con.execute("UPDATE sorties SET meteo=?, habitat=?, score=?, classe=?, modele=?, erreur=NULL WHERE id=?",
                    (s["meteo"], s["habitat"], s["score"], s["classe"], ver, r["id"]))
        n += 1
    con.commit()
    return n


# --------------------------------------------------------------------------- évaluation

def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.all(a == a[0]) or np.all(b == b[0]):
        return None

    def rank(v):
        order = np.argsort(v, kind="mergesort")
        r = np.empty(len(v))
        r[order] = np.arange(len(v))
        for x in np.unique(v):                       # rangs moyens pour les ex aequo
            m = v == x
            r[m] = r[m].mean()
        return r
    return float(np.corrcoef(rank(a), rank(b))[0, 1])


def evaluate(con: sqlite3.Connection, cfg: dict) -> dict:
    rs = [r for r in rows(con) if r["score"] is not None]
    n_all = len(rows(con))
    need = cfg["calibration"]["min_sorties"]
    by_class: dict[str, dict] = {}
    for k in ("faible", "moyen", "bon", "très bon"):
        sel = [r for r in rs if r["classe"] == k]
        if sel:
            by_class[k] = {"n": len(sel), "trouve": sum(r["resultat"] >= 1 for r in sel) / len(sel),
                           "resultat_moyen": float(np.mean([r["resultat"] for r in sel]))}
    rho = _spearman(np.array([r["score"] for r in rs], float), np.array([r["resultat"] for r in rs], float))
    return {"n": n_all, "n_scores": len(rs), "min": need, "calibrable": len(rs) >= need, "par_classe": by_class,
            "spearman": rho}


def evaluation_text(ev: dict) -> str:
    L = [f"Sorties : {ev['n']} (dont {ev['n_scores']} avec instantané du modèle) — seuil de calibration : {ev['min']}."]
    if not ev["calibrable"]:
        L.append(f"Indice NON calibré : encore {ev['min'] - ev['n_scores']} sortie(s) avant de pouvoir juger le modèle. "
                 "Notez aussi les sorties bredouilles : elles comptent autant.")
    for k, v in ev["par_classe"].items():
        L.append(f"  classe prédite « {k} » : {v['n']} sortie(s), trouvé dans {v['trouve']:.0%}, "
                 f"résultat moyen {v['resultat_moyen']:.1f}/3")
    if ev["spearman"] is not None:
        L.append(f"Corrélation de rang score ↔ résultat : {ev['spearman']:+.2f} "
                 "(> 0 : le modèle classe dans le bon sens ; proche de 0 : il ne discrimine pas).")
    return "\n".join(L)


def export_json(con: sqlite3.Connection) -> str:
    return json.dumps(rows(con), ensure_ascii=False, indent=1)
