"""Point d'entrée : python -m radar <commande>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from radar import grid, mapgen, score, weather
from radar.config import load_config, resolve


def meteo_points(cfg: dict, limit: int = 0) -> dict:
    """Météo + score météo sur la grille 5 km ; écrit data/out/v0.json."""
    c = cfg["center"]
    pts = grid.build_grid(c["lat"], c["lon"], cfg["grid"]["radius_km"], cfg["grid"]["spacing_km"])
    if limit:
        pts = pts[:limit]
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

    result = {"date": ref_date, "n_points": len(out_points), "points": out_points,
              "skipped_sea": skipped_sea, "skipped_bad": skipped_bad}
    out_json = resolve("data/out/v0.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def cmd_v0(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    c = cfg["center"]
    result = meteo_points(cfg, args.limit)
    html = mapgen.write_map(result, (c["lat"], c["lon"]), calibrated=False, out=resolve("web/v0.html"))
    counts: dict[str, int] = {}
    for p in result["points"]:
        counts[p["classe"]] = counts.get(p["classe"], 0) + 1
    print(f"Date de référence : {result['date']}")
    print(f"{result['n_points']} points scorés ({result['skipped_sea']} mer/étang ignorés, "
          f"{result['skipped_bad']} inexploitables)")
    print("Répartition :", counts)
    print(f"Carte météo seule : {html}")
    print("Rappel : indice de pousse non calibré, jamais une validation de comestibilité.")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    from radar import habitat, rastermap

    cfg = load_config(args.config)
    c = cfg["center"]
    result = meteo_points(cfg)
    res = habitat.run(cfg, result["points"], month=args.month or None)
    print(res["report"])
    scfg = res["scfg"]
    layers = {}
    for k, fam in scfg["families"].items():
        layers[f"final_{k}"] = {"label": f"Score — {fam['label']}", "kind": "final", "values": res["final"][k] / 100.0}
    for k, fam in scfg["families"].items():
        layers[f"hab_{k}"] = {"label": f"Habitat — {fam['label']}", "kind": "habitat", "values": res["hab"][k]}
    idx, bounds = rastermap.display_grid(res["t"], res["meta"]["grid"])
    html = rastermap.write(resolve("web/index.html"), layers=layers, idx=idx, bounds=bounds, points=result["points"],
                           date_=result["date"], center=(c["lat"], c["lon"]), classes=cfg["score"]["classes"])
    print(f"\nCarte : {html}")
    return 0 if all(x["ok"] for x in res["criteria"]) else 2


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


def cmd_compare_models(args: argparse.Namespace) -> int:
    from radar import compare

    cfg = load_config(args.config)
    lat0, lon0 = (float(x) for x in args.center.split(","))
    pts = grid.build_grid(lat0, lon0, args.radius, args.spacing)
    if args.limit:
        pts = pts[: args.limit]
    print(f"Zone test : centre {lat0},{lon0} rayon {args.radius} km maille {args.spacing} km -> {len(pts)} points")
    idx = cfg["weather"]["past_days"]
    summaries = {}
    for model in args.models.split(","):
        wcfg = dict(cfg["weather"])
        wcfg["model"] = None if model == "best_match" else model
        data = weather.fetch_all(pts, wcfg)
        summaries[model] = compare.summarize(data, idx, cfg["score"])
        print(f"\n== {model} ==")
        for k, v in compare.public(summaries[model]).items():
            print(f"  {k}: {v}")
    names = list(summaries)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            print(f"\n== écarts {names[i]} vs {names[j]} ==")
            for k, v in compare.pairwise(summaries[names[i]], summaries[names[j]]).items():
                print(f"  {k}: {v}")
    print("\nRappel : sans observations au sol, ces écarts ne disent pas quel modèle est le plus juste.")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from radar import fetch

    res = fetch.fetch_all(only=args.only.split(",") if args.only else None)
    for name, status in res.items():
        print(f"{name}: {status}")
    return 0


def cmd_terrain(args: argparse.Namespace) -> int:
    from radar import terrain

    print(terrain.run(load_config(args.config)))
    print("\nSorties : data/processed/terrain_250m.npz, terrain_meta.json, terrain_report.txt")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--config", default=None, help="chemin d'un config.yaml alternatif")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("v0", help="météo + score + carte V0")
    p.add_argument("--limit", type=int, default=0, help="limiter le nombre de points (debug)")
    p.set_defaults(func=cmd_v0)
    p = sub.add_parser("score", help="météo + habitat 250 m -> score final et carte web/index.html")
    p.add_argument("--month", type=int, default=0, help="mois simulé (saison/exposition), défaut : mois courant")
    p.set_defaults(func=cmd_score)
    p = sub.add_parser("check-sources", help="vérifier que les sources externes répondent")
    p.set_defaults(func=cmd_check_sources)
    p = sub.add_parser("fetch", help="télécharger les sources de terrain vérifiées (config/sources.yaml)")
    p.add_argument("--only", default=None, help="liste de sources séparées par des virgules")
    p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("terrain", help="prétraitement terrain -> grille 250 m (data/processed)")
    p.set_defaults(func=cmd_terrain)
    p = sub.add_parser("compare-models", help="comparer des modèles météo sur une zone test")
    p.add_argument("--models", default="best_match,meteofrance_arome_france_hd")
    p.add_argument("--center", default="43.9667,3.8500", help="lat,lon (défaut : Saint-Hippolyte-du-Fort)")
    p.add_argument("--radius", type=float, default=20)
    p.add_argument("--spacing", type=float, default=2)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_compare_models)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
