#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

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


def main() -> int:
    ensure_root()
    db = write_automic_vault_db()
    pulse = {
        "formulas": pulse_coverage(db["formulas"]),
        "casks": pulse_coverage(db["casks"]),
        "npms": pulse_coverage(db["npms"]),
    }
    for source, coverage in pulse.items():
        if coverage["total"] and not coverage["last_updated_at"]:
            print(
                f"Warning: no {source} pulse last_updated_at metadata exported",
                file=sys.stderr,
            )
    print(json.dumps({
        "ok": True,
        "path": str(AUTOMIC_VAULT_DB_PATH),
        "entries": len(db["entries"]),
        "formulas": len(db["formulas"]),
        "pulse": pulse,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
