#!/usr/bin/env python3
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PKG_HUBS_PATH = Path("data/pkg-hubs.json")
PKG_TAXONOMY_PATH = Path("data/pkg-taxonomy.json")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


@lru_cache(maxsize=1)
def load_pkg_hub_data() -> dict[str, Any]:
    data = read_json(PKG_HUBS_PATH, {"schema": 1, "hubs": {}})
    hubs = data.get("hubs")
    if not isinstance(hubs, dict):
        raise ValueError(f"{PKG_HUBS_PATH} must contain a hubs object")
    return data


def load_pkg_hubs() -> dict[str, dict[str, Any]]:
    hubs = load_pkg_hub_data().get("hubs") or {}
    return {str(slug): hub for slug, hub in hubs.items() if isinstance(hub, dict)}


def graph_hub_definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for slug, hub in load_pkg_hubs().items():
        definitions[slug] = {
            "label": str(hub.get("label") or hub.get("title") or slug),
            "kicker": str(hub.get("kicker") or "package hub"),
            "description": str(hub.get("description") or "Generated package hub."),
            "terms": tuple(_clean_list(hub.get("terms"))),
            "names": tuple(_clean_list(hub.get("names"))),
            "providers": tuple(_clean_list(hub.get("providers"))),
            "categories": tuple(_clean_list(hub.get("categories"))),
            "categoryPaths": tuple(_clean_list(hub.get("categoryPaths"))),
            "tags": tuple(_clean_list(hub.get("tags"))),
            "group": str(hub.get("group") or "topical"),
            "priority": int(hub.get("priority") or 100),
            "riskHub": bool(hub.get("riskHub")),
            "reason": str(hub.get("reason") or hub.get("description") or "Matched package hub metadata."),
        }
    return definitions


@lru_cache(maxsize=1)
def load_pkg_taxonomy_data() -> dict[str, Any]:
    data = read_json(PKG_TAXONOMY_PATH, {"schema": 1, "packages": {}})
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise ValueError(f"{PKG_TAXONOMY_PATH} must contain a packages object")
    return data


@lru_cache(maxsize=1)
def load_pkg_taxonomy_index() -> dict[str, dict[str, Any]]:
    packages = load_pkg_taxonomy_data().get("packages") or {}
    index: dict[str, dict[str, Any]] = {}
    for package_key, entry in packages.items():
        if not isinstance(package_key, str) or not isinstance(entry, dict):
            continue
        index[package_key] = entry
        aliases = entry.get("packageManagerAliases")
        if not isinstance(aliases, dict):
            continue
        for provider, name in aliases.items():
            provider_text = str(provider or "").strip()
            name_text = str(name or "").strip()
            if provider_text and name_text:
                index.setdefault(f"{provider_text}:{name_text}", entry)
    return index


def taxonomy_for_package(index: dict[str, dict[str, Any]], provider: str, name: str) -> dict[str, Any]:
    return index.get(f"{provider}:{name}") or {}


def taxonomy_terms(entry: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    category = str(entry.get("category") or "").strip()
    if category:
        terms.add(category)
    for value in _clean_list(entry.get("categoryPath")):
        terms.add(value)
    for value in _clean_list(entry.get("tags")):
        terms.add(value)
    return terms


def taxonomy_brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": entry.get("category") or "",
        "categoryPath": _clean_list(entry.get("categoryPath")),
        "categoryConfidence": entry.get("categoryConfidence") or "",
        "tags": _clean_list(entry.get("tags"))[:16],
    }
