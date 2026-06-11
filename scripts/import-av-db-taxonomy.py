#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_SOURCE = Path(os.environ.get("AV_DB_DIR") or "/Users/mxcl/src/av.db")
OUTPUT_PATH = Path("data/pkg-taxonomy.json")
SCHEMA_VERSION = 1


def scalar(lines: list[str], key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.*)$")
    for line in lines:
        match = pattern.match(line)
        if match:
            value = match.group(1).strip()
            if value == "null":
                return ""
            return value.strip("\"'")
    return ""


def list_block(lines: list[str], key: str, indent: int = 0) -> list[str]:
    start_pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}:\s*$")
    item_pattern = re.compile(rf"^{' ' * (indent + 2)}-\s*(.*)$")
    end_pattern = re.compile(rf"^{' ' * indent}[A-Za-z0-9_-]+:")
    result: list[str] = []
    in_block = False
    for line in lines:
        if not in_block and start_pattern.match(line):
            in_block = True
            continue
        if not in_block:
            continue
        item = item_pattern.match(line)
        if item:
            value = item.group(1).strip().strip("\"'")
            if value and value != ">":
                result.append(value)
            continue
        if end_pattern.match(line) and not item_pattern.match(line):
            break
    return result


def mapping_block(lines: list[str], key: str, indent: int = 0) -> dict[str, str]:
    start_pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}:\s*$")
    item_pattern = re.compile(rf"^{' ' * (indent + 2)}([A-Za-z0-9_.-]+):\s*(.*)$")
    end_pattern = re.compile(rf"^{' ' * indent}[A-Za-z0-9_-]+:")
    result: dict[str, str] = {}
    in_block = False
    for line in lines:
        if not in_block and start_pattern.match(line):
            in_block = True
            continue
        if not in_block:
            continue
        item = item_pattern.match(line)
        if item:
            value = item.group(2).strip().strip("\"'")
            if value and value != "null":
                result[item.group(1)] = value
            continue
        if end_pattern.match(line) and not item_pattern.match(line):
            break
    return result


def provenance_list(lines: list[str], key: str) -> list[str]:
    start_pattern = re.compile(rf"^  {re.escape(key)}:\s*$")
    item_pattern = re.compile(r"^    -\s*(.*)$")
    end_pattern = re.compile(r"^  [A-Za-z0-9_-]+:")
    result: list[str] = []
    in_block = False
    for line in lines:
        if not in_block and start_pattern.match(line):
            in_block = True
            continue
        if not in_block:
            continue
        item = item_pattern.match(line)
        if item:
            value = item.group(1).strip().strip("\"'")
            if value and value != ">":
                result.append(value)
            continue
        if end_pattern.match(line) and not item_pattern.match(line):
            break
    return result


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def source_commit(source: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def source_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(path.parents[1]).as_posix().encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_combined(path: Path) -> dict[str, Any]:
    lines = read_lines(path)
    package_id = scalar(lines, "id")
    if not package_id:
        package_id = f"brew:{path.stem}"
    return {
        "id": package_id,
        "displayName": scalar(lines, "display-name") or path.stem,
        "category": scalar(lines, "category"),
        "tags": sorted(set(list_block(lines, "tags"))),
        "packageManagerAliases": mapping_block(lines, "package-manager"),
    }


def parse_agent(path: Path) -> dict[str, Any]:
    lines = read_lines(path)
    package_id = scalar(lines, "id") or f"brew:{path.stem}"
    return {
        "id": package_id,
        "categoryPath": list_block(lines, "category-path"),
        "categoryConfidence": scalar(lines, "category-confidence"),
        "tagsConfidence": scalar(lines, "tags-confidence"),
        "categorySources": provenance_list(lines, "category-sources"),
        "tagsSources": provenance_list(lines, "tags-sources"),
    }


def build_taxonomy(source: Path) -> dict[str, Any]:
    if not source.exists():
        raise FileNotFoundError(f"missing av.db source: {source}")
    combined_paths = sorted((source / "combined").glob("*.yml"))
    agent_paths = sorted((source / "agents").glob("*.yml"))
    agents = {parse_agent(path)["id"]: parse_agent(path) for path in agent_paths}
    packages: dict[str, dict[str, Any]] = {}
    for path in combined_paths:
        entry = parse_combined(path)
        agent = agents.get(entry["id"]) or {}
        if agent.get("categoryPath"):
            entry["categoryPath"] = agent["categoryPath"]
        else:
            entry["categoryPath"] = [entry["category"]] if entry.get("category") else []
        if agent.get("categoryConfidence"):
            entry["categoryConfidence"] = agent["categoryConfidence"]
        if agent.get("tagsConfidence"):
            entry["tagsConfidence"] = agent["tagsConfidence"]
        if agent.get("categorySources"):
            entry["categorySources"] = agent["categorySources"][:3]
        if agent.get("tagsSources"):
            entry["tagsSources"] = agent["tagsSources"][:3]
        packages[entry["id"]] = entry
    paths = combined_paths + agent_paths
    return {
        "schema": SCHEMA_VERSION,
        "source": str(source),
        "sourceCommit": source_commit(source),
        "sourceHash": source_hash(paths),
        "packageCount": len(packages),
        "packages": packages,
    }


def comparable(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import av.db category and tag curation for package hub generation.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Path to av.db checkout.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Output JSON path. Defaults to {OUTPUT_PATH}.")
    parser.add_argument("--check", action="store_true", help="Validate that the output matches the current source.")
    args = parser.parse_args()
    source = Path(args.source).expanduser()
    output = Path(args.output)
    try:
        expected = build_taxonomy(source)
    except OSError as err:
        print(f"ERROR {err}", file=sys.stderr)
        return 1
    if args.check:
        try:
            current = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            print(f"ERROR unable to read {output}: {err}", file=sys.stderr)
            return 1
        if comparable(current) != comparable(expected):
            print(f"ERROR {output} is stale; run scripts/import-av-db-taxonomy.py", file=sys.stderr)
            return 1
        print(f"OK {output} is current ({expected['packageCount']:,} packages)")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OK wrote {expected['packageCount']:,} package taxonomy entries to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
