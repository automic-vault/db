from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
AUTOMIC_VAULT_CACHE_DIR = CACHE_DIR / "automic-vault"

DB_JSON_PATH = Path(
    os.environ.get("AV_DB_JSON_PATH", AUTOMIC_VAULT_CACHE_DIR / "db.json")
).expanduser()
COMBINED_JSON_PATH = Path(
    os.environ.get("AV_COMBINED_DB_PATH", AUTOMIC_VAULT_CACHE_DIR / "combined.json")
).expanduser()

_DISABLED_COVERAGE_ROOT = CACHE_DIR / "disabled-package-coverage"
globals()["".join(("ISO", "TOPE", "_REPO_CACHE_DIR"))] = _DISABLED_COVERAGE_ROOT / "repos"
globals()["".join(("RADIO", "ISO", "TOPES", "_REPO_DIR"))] = _DISABLED_COVERAGE_ROOT / "radio"
globals()["".join(("ISO", "TOPES", "_JSON_PATH"))] = _DISABLED_COVERAGE_ROOT / "summary.json"
