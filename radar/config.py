"""Chargement de la configuration unique (config/config.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"


def load_config(path: Path | str | None = None) -> dict:
    """Charge le YAML de configuration ; aucune valeur n'est codée en dur ailleurs."""
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve(rel: str) -> Path:
    """Chemin relatif à la racine du projet."""
    return ROOT / rel


def load_env(path: Path | None = None) -> None:
    """Charge le fichier `.env` local (non versionné) dans os.environ, sans écraser les variables déjà définies
    (en CI, ce sont les secrets GitHub). Format : CLE=valeur, une par ligne, # pour les commentaires."""
    import os

    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
