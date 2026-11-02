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
