#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lib.brew import fetch_formulae
from lib.common import CACHE_DIR, ensure_root, read_json, stable_hash, write_json
from lib.executables import build_executable_index, write_executable_index


SOURCE_FORMULA_CACHE = (
    Path.home()
    / "src"
    / "automic-vault"
    / "cache"
    / "brew.sh"
    / "58a55472b0cdf8aa7ae83de7d71d45802697a0a2f41a5c6364973f415eac9995.json"
)


def seeded_formulae(refresh: bool) -> list[dict]:
    output = CACHE_DIR / "brew" / "formulae.json"
    if not refresh and output.exists():
        payload = read_json(output)
        formulae = payload.get("formulae") if isinstance(payload, dict) else None
        if isinstance(formulae, list):
            return [item for item in formulae if isinstance(item, dict)]
    if not refresh and not output.exists() and SOURCE_FORMULA_CACHE.exists():
        payload = read_json(SOURCE_FORMULA_CACHE)
        if isinstance(payload, dict) and "__pkgdb_payload__" in payload:
            formulae = payload["__pkgdb_payload__"]
            if isinstance(formulae, list):
                return [item for item in formulae if isinstance(item, dict)]
    return fetch_formulae(refresh=refresh)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and cache Homebrew formula metadata.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached Homebrew API data.")
    parser.add_argument("--fetch-manifests", action="store_true", help="Fetch GHCR bottle manifests to discover executables.")
    parser.add_argument("--manifest-limit", type=int, default=0, help="Limit manifest fetches for debugging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_root()
    formulae = seeded_formulae(args.refresh)
    write_json(CACHE_DIR / "brew" / "formulae.json", {
        "schema": 1,
        "source": "https://formulae.brew.sh/api/formula.json",
        "source_hash": stable_hash(formulae),
        "formulae": formulae,
    })
    executables = build_executable_index(formulae, refresh=args.refresh, fetch_manifests=args.fetch_manifests, limit=args.manifest_limit)
    write_executable_index(executables)
    print(json.dumps({"ok": True, "formulae": len(formulae), "executable_packages": len(executables)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
