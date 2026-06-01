from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from .common import CACHE_DIR, COMBINED_DIR, write_json
from .casks import read_cask_authority
from .executables import executable_entries_from_index, executable_index_from_project_yaml
from .render import parse_simple_yaml, read_formula_cache


DB_SCHEMA_VERSION = 7
AUTOMIC_VAULT_CACHE_DIR = CACHE_DIR / "automic-vault"
AUTOMIC_VAULT_DB_PATH = AUTOMIC_VAULT_CACHE_DIR / "db.json"


def formula_lookup(formulae: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for formula in formulae:
        name = formula.get("name")
        if isinstance(name, str) and name:
            result[name] = formula
    return result


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def formula_metadata_from_record(record: dict[str, Any], formula: dict[str, Any] | None) -> dict[str, Any]:
    docs = string_list(record.get("docs"))
    repo = str(record.get("repo") or "")
    metadata = {
        "summary": str(record.get("description") or ""),
        "homepage": str(record.get("homepage") or ""),
        "repo": repo,
        "repository": repo,
        "docs": docs,
        "upstreamDocs": docs[0] if docs else "",
        "category": str(record.get("category") or ""),
        "aliases": string_list((formula or {}).get("aliases")),
        "oldnames": string_list((formula or {}).get("oldnames")),
    }
    return {key: value for key, value in metadata.items() if value or key == "summary"}


def formula_metadata_from_project_yaml(root: Path = COMBINED_DIR, formulae: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    formulae_by_name = formula_lookup(formulae if formulae is not None else read_formula_cache())
    result: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return result
    for path in sorted(root.glob("*.yml")):
        record = parse_simple_yaml(path.read_text(encoding="utf-8"))
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier.startswith("brew:"):
            continue
        name = identifier.split(":", 1)[1]
        if name:
            result[name] = formula_metadata_from_record(record, formulae_by_name.get(name))
    return result


def build_automic_vault_db(root: Path = COMBINED_DIR, formulae: list[dict[str, Any]] | None = None, generated_at: str | None = None) -> dict[str, Any]:
    executable_index = executable_index_from_project_yaml(root)
    cask_entries, casks = read_cask_authority()
    entries = executable_entries_from_index(executable_index)
    entries.update(cask_entries)
    timestamp = generated_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "schema": DB_SCHEMA_VERSION,
        "generated_at": timestamp,
        "entries": dict(sorted(entries.items())),
        "formulas": formula_metadata_from_project_yaml(root, formulae),
        "casks": casks,
        "npms": {},
    }


def write_automic_vault_db(path: Path = AUTOMIC_VAULT_DB_PATH) -> dict[str, Any]:
    db = build_automic_vault_db()
    write_json(path, db)
    return db
