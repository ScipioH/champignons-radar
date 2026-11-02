"""Point d'entrée : python -m radar <commande>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from radar import grid, mapgen, score, weather
from radar.config import load_config, resolve


def cmd_v0(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    c = cfg["center"]
    pts = grid.build_grid(c["lat"], c["lon"], cfg["grid"]["radius_km"], cfg["grid"]["spacing_km"])
    if args.limit:
        pts = pts[: args.limit]
    wcfg = cfg["weather"]
    data = weather.fetch_all(pts, wcfg)

    idx = wcfg["past_days"]
    out_points, skipped_sea, skipped_bad = [], 0, 0
    ref_date = None
    for lat, lon in pts:
        d = data[f"{lat:.4f},{lon:.4f}"]
        alt = d.get("elevation")
        if alt is not None and alt <= 0:
            skipped_sea += 1  # mer / étang : hors sujet pour la cueillette
            continue
        if len(d["precip"]) <= idx:
            skipped_bad += 1
            continue
        ref_date = d["time"][idx]
        r = score.score_point(d["precip"], d["tmin"], idx, alt, cfg["score"])
        if r["score"] is None:
            skipped_bad += 1
            continue
        out_points.append({"lat": lat, "lon": lon, "altitude": alt or 0, **r})

    result = {"date": ref_date, "n_points": len(out_points), "points": out_points}
    out_json = resolve("data/out/v0.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    html = mapgen.write_map(result, (c["lat"], c["lon"]), calibrated=False, out=resolve("web/index.html"))

    counts: dict[str, int] = {}
    for p in out_points:
        counts[p["classe"]] = counts.get(p["classe"], 0) + 1
    print(f"Date de référence : {ref_date}")
    print(f"{len(out_points)} points scorés ({skipped_sea} mer/étang ignorés, {skipped_bad} inexploitables)")
    print("Répartition :", counts)
    print(f"Carte : {html}")
    print("Rappel : indice de pousse non calibré, jamais une validation de comestibilité.")
    return 0


def cmd_check_sources(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    c = cfg["center"]
    try:
        url = weather.build_url([(c["lat"], c["lon"])], cfg["weather"])
        data = weather._http_get_json(url, 1)
        d = weather._parse(data if isinstance(data, dict) else data[0])
        print(f"Open-Meteo OK : {len(d['time'])} jours, altitude {d['elevation']} m")
        return 0
    except Exception as e:  # noqa: BLE001 - on veut afficher toute panne de source
        print(f"Open-Meteo KO : {e}")
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--config", default=None, help="chemin d'un config.yaml alternatif")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("v0", help="météo + score + carte V0")
    p.add_argument("--limit", type=int, default=0, help="limiter le nombre de points (debug)")
    p.set_defaults(func=cmd_v0)
    p = sub.add_parser("check-sources", help="vérifier que les sources externes répondent")
    p.set_defaults(func=cmd_check_sources)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
