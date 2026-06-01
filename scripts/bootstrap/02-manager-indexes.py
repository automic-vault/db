#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from lib.common import CACHE_DIR, ensure_root, read_json, write_json
from lib.managers import build_manager_indexes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch package-manager indexes used for install-line matching.")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached package-manager index sources.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_root()
    output = CACHE_DIR / "pkg-manager-indexes.json.gz"
    if not args.refresh and output.exists():
        indexes = read_json(output)
    else:
        indexes = build_manager_indexes(refresh=args.refresh)
    write_json(CACHE_DIR / "pkg-manager-indexes.json.gz", indexes)
    print(json.dumps({"ok": True, "managers": len(indexes.get("managers") or {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
