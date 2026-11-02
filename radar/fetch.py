"""Téléchargement des sources de terrain avec contrôle d'intégrité (phase 2a).

Règles : jamais d'URL non vérifiée (config/sources.yaml), taille et MD5 contrôlés quand le
fournisseur les publie, SHA-256 enregistré dans data/raw/CHECKSUMS.json, écriture atomique
(.part puis renommage), reprise = on saute ce qui est déjà présent et conforme.
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.request
from datetime import date
from pathlib import Path

import yaml

from radar.config import ROOT, resolve

log = logging.getLogger("radar.fetch")
CHUNK = 1 << 20
UA = "champignons-radar/0.1 (usage personnel)"


class IntegrityError(RuntimeError):
    pass


def load_sources(path: Path | None = None) -> dict[str, dict]:
    with open(path or ROOT / "config" / "sources.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def file_digests(path: Path) -> tuple[str, str, int]:
    md5, sha, n = hashlib.md5(), hashlib.sha256(), 0  # noqa: S324 - md5 = contrôle publié par IGN, pas de la sécurité
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            md5.update(chunk)
            sha.update(chunk)
            n += len(chunk)
    return md5.hexdigest(), sha.hexdigest(), n


def verify(path: Path, size: int | None, md5: str | None) -> tuple[str, int]:
    """Lève IntegrityError si taille/MD5 ne correspondent pas ; retourne (sha256, taille)."""
    got_md5, got_sha, got_size = file_digests(path)
    if size is not None and got_size != size:
        raise IntegrityError(f"{path.name}: taille {got_size} != attendue {size}")
    if md5 is not None and got_md5 != md5.lower():
        raise IntegrityError(f"{path.name}: MD5 {got_md5} != attendu {md5}")
    return got_sha, got_size


def _checksums_file(raw_dir: Path) -> Path:
    return raw_dir / "CHECKSUMS.json"


def _load_checksums(raw_dir: Path) -> dict:
    f = _checksums_file(raw_dir)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def download(name: str, spec: dict, raw_dir: Path, opener=urllib.request.urlopen) -> str:
    """Télécharge une source ; retourne 'present' si déjà conforme, sinon 'telecharge'."""
    dest = raw_dir / spec["dest"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    sums = _load_checksums(raw_dir)
    if dest.exists():
        try:
            sha, size = verify(dest, spec.get("size"), spec.get("md5"))
            if sums.get(name, {}).get("sha256") in (None, sha):
                return "present"
            raise IntegrityError(f"{dest.name}: SHA-256 différent de celui enregistré (fichier modifié ?)")
        except IntegrityError:
            log.warning("%s présent mais non conforme : retéléchargement", name)
            dest.unlink()

    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(spec["url"], headers={"User-Agent": UA})
    with opener(req, timeout=120) as resp, open(part, "wb") as out:
        done, last = 0, 0
        while chunk := resp.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            if done - last >= 20 * CHUNK:
                log.info("%s : %d Mo", name, done >> 20)
                last = done
    try:
        sha, size = verify(part, spec.get("size"), spec.get("md5"))
    except IntegrityError:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    sums[name] = {"sha256": sha, "size": size, "url": spec["url"], "downloaded": date.today().isoformat()}
    _checksums_file(raw_dir).write_text(json.dumps(sums, indent=2), encoding="utf-8")
    return "telecharge"


def fetch_all(only: list[str] | None = None, raw_dir: Path | None = None, sources: dict | None = None,
              opener=urllib.request.urlopen) -> dict[str, str]:
    raw_dir = raw_dir or resolve("data/raw")
    sources = sources or load_sources()
    names = only or list(sources)
    unknown = [n for n in names if n not in sources]
    if unknown:
        raise KeyError(f"sources inconnues : {unknown} (disponibles : {list(sources)})")
    return {n: download(n, sources[n], raw_dir, opener) for n in names}
