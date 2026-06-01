#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from lib.brew import fetch_formulae
from lib.common import CACHE_DIR, ensure_root, read_json, stable_hash, write_json
from lib.casks import fetch_cask_index, fetch_supported_casks, write_cask_cache
from lib.executables import build_executable_index, write_executable_index


def seeded_formulae(refresh: bool) -> list[dict]:
    output = CACHE_DIR / "brew" / "formulae.json"
    if not refresh and output.exists():
        payload = read_json(output)
        formulae = payload.get("formulae") if isinstance(payload, dict) else None
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
    cask_index = fetch_cask_index(refresh=args.refresh)
    casks = fetch_supported_casks(cask_index, refresh=args.refresh)
    write_json(CACHE_DIR / "brew" / "formulae.json", {
        "schema": 1,
        "source": "https://formulae.brew.sh/api/formula.json",
        "source_hash": stable_hash(formulae),
        "formulae": formulae,
    })
    write_cask_cache(casks)
    executables = build_executable_index(formulae, refresh=args.refresh, fetch_manifests=args.fetch_manifests, limit=args.manifest_limit)
    write_executable_index(executables)
    print(json.dumps({"ok": True, "formulae": len(formulae), "casks": len(casks), "executable_packages": len(executables)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
