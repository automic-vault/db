#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "bootstrap"))

from lib.common import AGENTS_DIR, ensure_root, write_text_if_changed  # noqa: E402
from lib.render import geiger_summary_for_package_id, parse_simple_yaml  # noqa: E402
from lib.yaml_writer import yaml_text  # noqa: E402


def expected_record(path: Path) -> dict[str, Any]:
    record = parse_simple_yaml(path.read_text(encoding="utf-8"))
    package_id = record.get("id") or f"brew:{path.stem}"
    geiger = geiger_summary_for_package_id(package_id)
    if geiger:
        record["geiger"] = geiger
    return record


def apply_geiger(check: bool) -> int:
    ensure_root()
    if not AGENTS_DIR.exists():
        raise FileNotFoundError(f"missing agents directory: {AGENTS_DIR}")

    checked = 0
    changed = 0
    stale: list[Path] = []
    for path in sorted(AGENTS_DIR.glob("*.yml")):
        record = expected_record(path)
        if "geiger" not in record:
            continue
        checked += 1
        rendered = yaml_text(record)
        if path.read_text(encoding="utf-8") == rendered:
            continue
        changed += 1
        if check:
            stale.append(path)
        else:
            write_text_if_changed(path, rendered)

    if stale:
        sample = ", ".join(path.relative_to(ROOT).as_posix() for path in stale[:10])
        suffix = "" if len(stale) <= 10 else f", ... and {len(stale) - 10} more"
        print(f"agents geiger data is stale for {len(stale)} files: {sample}{suffix}", file=sys.stderr)
        return 1

    action = "checked" if check else "updated"
    print(f"{action} {checked} agent geiger records; {changed} file(s) {'would change' if check else 'changed'}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy Geiger color/reason data into agents/*.yml.")
    parser.add_argument("--check", action="store_true", help="fail if agents/*.yml is missing current Geiger data")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return apply_geiger(args.check)
    except (OSError, ValueError) as err:
        print(f"failed to apply geiger data: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
