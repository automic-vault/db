#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from avdb_paths import DB_JSON_PATH
except ModuleNotFoundError:
    from scripts.avdb_paths import DB_JSON_PATH


PULSE_SOURCES = ("formulas", "casks", "npms")
PULSE_KIND_REQUIRED_SOURCES = {"formulas", "casks"}


class HealthCheckFailed(Exception):
    pass


def populated_metadata(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, dict):
        return {}
    return {
        key: value
        for key, value in items.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def pulse_coverage(db: dict[str, Any]) -> dict[str, dict[str, int]]:
    coverage: dict[str, dict[str, int]] = {}
    for source in PULSE_SOURCES:
        items = populated_metadata(db.get(source))
        coverage[source] = {
            "total": len(items),
            "last_updated_at": sum(
                1
                for metadata in items.values()
                if isinstance(metadata.get("last_updated_at"), str) and metadata["last_updated_at"]
            ),
            "pulse_kind": sum(
                1
                for metadata in items.values()
                if isinstance(metadata.get("pulse_kind"), str) and metadata["pulse_kind"]
            ),
        }
    return coverage


def check_pulse_health(db: dict[str, Any]) -> dict[str, dict[str, int]]:
    coverage = pulse_coverage(db)
    failures = []
    for source, counts in coverage.items():
        if counts["total"] and not counts["last_updated_at"]:
            failures.append(f"{source}: no last_updated_at values across {counts['total']} entries")
        if (
            source in PULSE_KIND_REQUIRED_SOURCES
            and counts["last_updated_at"]
            and not counts["pulse_kind"]
        ):
            failures.append(f"{source}: no pulse_kind values across {counts['last_updated_at']} pulse entries")
    if failures:
        raise HealthCheckFailed("; ".join(failures))
    return coverage


def read_db(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise HealthCheckFailed(f"{path} does not exist") from err
    except json.JSONDecodeError as err:
        raise HealthCheckFailed(f"{path} is not valid JSON: {err}") from err
    if not isinstance(payload, dict):
        raise HealthCheckFailed(f"{path} must contain a JSON object")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check generated Automic Vault DB health.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_JSON_PATH,
        help=f"Automic Vault db.json path. Defaults to {DB_JSON_PATH}.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        coverage = check_pulse_health(read_db(args.db))
    except HealthCheckFailed as err:
        print(json.dumps({"ok": False, "error": str(err)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "pulse": coverage}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
