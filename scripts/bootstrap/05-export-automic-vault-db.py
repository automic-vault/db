#!/usr/bin/env -S uv run --python 3.10
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lib.authority import AUTOMIC_VAULT_DB_PATH, write_automic_vault_db
from lib.common import ensure_root


def pulse_coverage(items: dict[str, object]) -> dict[str, int]:
    total = len(items)
    last_updated_at = sum(
        1
        for metadata in items.values()
        if (
            isinstance(metadata, dict)
            and isinstance(metadata.get("last_updated_at"), str)
            and metadata["last_updated_at"]
        )
    )
    pulse_kind = sum(
        1
        for metadata in items.values()
        if isinstance(metadata, dict) and isinstance(metadata.get("pulse_kind"), str) and metadata["pulse_kind"]
    )
    return {
        "total": total,
        "last_updated_at": last_updated_at,
        "pulse_kind": pulse_kind,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the public pkg.so db.json export.")
    parser.add_argument("--output", type=Path, default=AUTOMIC_VAULT_DB_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_root()
    db = write_automic_vault_db(args.output)
    pulse = {
        "formulas": pulse_coverage(db["formulas"]),
        "casks": pulse_coverage(db["casks"]),
        "npms": pulse_coverage(db["npms"]),
        "crates": pulse_coverage(db["crates"]),
    }
    for source, coverage in pulse.items():
        if coverage["total"] and not coverage["last_updated_at"]:
            print(
                f"Warning: no {source} pulse last_updated_at metadata exported",
                file=sys.stderr,
            )
    print(json.dumps({
        "ok": True,
        "path": str(args.output),
        "entries": len(db["entries"]),
        "formulas": len(db["formulas"]),
        "pulse": pulse,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
