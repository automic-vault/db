#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMIT_PATHS = [
    "deterministic",
    "combined",
    "agents",
    "human-override",
    "data/approval-gates",
    "data/pkg-hubs.json",
    "data/pkg-i18n",
    "data/pkg-pages",
    "data/pkg-taxonomy.json",
]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def commit_if_changed(message: str) -> str | None:
    run(["git", "add", "-A", "--", *COMMIT_PATHS])
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *COMMIT_PATHS], cwd=ROOT)
    if diff.returncode == 0:
        return None
    if diff.returncode != 1:
        diff.check_returncode()
    subprocess.run(["git", "commit", "-m", message, "--", *COMMIT_PATHS], cwd=ROOT, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one av.db hourly package database update.")
    parser.add_argument("--no-commit", action="store_true", help="Do not commit stable source changes.")
    parser.add_argument("--skip-isotopes", action="store_true", help="Skip isotope build and summary refresh.")
    parser.add_argument(
        "--skip-isotope-builds",
        action="store_true",
        help="Refresh isotope summary without building or publishing isotope releases.",
    )
    parser.add_argument("--skip-sqlite", action="store_true", help="Skip package SQLite generation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    py = sys.executable
    os.chdir(ROOT)

    run([py, "scripts/build.py", "--refresh"])
    if not args.skip_isotopes:
        isotope_command = ["bash", "scripts/build-isotopes.sh"]
        if args.skip_isotope_builds:
            isotope_command.append("--skip-builds")
        run(isotope_command)
    run([py, "scripts/export-automic-vault-db.py"])
    run([py, "scripts/generate-pkg-page-enrichment.py", "--refresh", "--registry-cache-only"])
    run([py, "scripts/generate-pkg-version-freshness.py"])
    run([py, "scripts/generate-pkg-manager-indexes.py"])
    run([py, "scripts/generate-pkg-cross-ecosystem.py"])
    run([py, "scripts/generate-pkg-graph.py"])
    run([py, "scripts/generate-pkg-graph-curation.py"])
    run([py, "scripts/generate-pkg-graph.py"])
    if not args.skip_sqlite:
        run([py, "scripts/generate-pkg-sqlite.py"])

    if not args.no_commit:
        commit = commit_if_changed("hourly: refresh package database")
        print(f"commit={commit or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
