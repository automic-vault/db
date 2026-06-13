from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
AUTOMIC_VAULT_CACHE_DIR = CACHE_DIR / "automic-vault"
ISOTOPE_REPO_CACHE_DIR = Path(
    os.environ.get("AUTOMIC_VAULT_REPO_CACHE", ROOT.parent / "isotopes")
).expanduser()
RADIOISOTOPES_REPO_DIR = Path(
    os.environ.get("AUTOMIC_VAULT_RADIOISOTOPES_REPO", ROOT.parent / "radioisotopes")
).expanduser()

DB_JSON_PATH = Path(
    os.environ.get("AV_DB_JSON_PATH", AUTOMIC_VAULT_CACHE_DIR / "db.json")
).expanduser()
COMBINED_JSON_PATH = Path(
    os.environ.get("AV_COMBINED_DB_PATH", AUTOMIC_VAULT_CACHE_DIR / "combined.json")
).expanduser()
ISOTOPES_JSON_PATH = Path(
    os.environ.get("AV_ISOTOPES_JSON_PATH", AUTOMIC_VAULT_CACHE_DIR / "isotopes.json")
).expanduser()
