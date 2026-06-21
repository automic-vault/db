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
DEFAULT_HOURLY_ENRICH_LIMIT = int(os.environ.get("AVDB_HOURLY_ENRICH_LIMIT", "10"))
DEFAULT_HOURLY_ENRICH_BATCH_SIZE = int(os.environ.get("AVDB_HOURLY_ENRICH_BATCH_SIZE", "5"))
DEFAULT_HOURLY_ENRICH_TIMEOUT_SECONDS = int(os.environ.get("AVDB_HOURLY_ENRICH_TIMEOUT_SECONDS", "1200"))


def run(command: list[str], *, timeout: int | None = None, allow_failure: bool = False) -> bool:
    print("+", " ".join(command), flush=True)
    try:
        subprocess.run(command, cwd=ROOT, check=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if not allow_failure:
            raise
        print(f"WARN: command timed out after {timeout}s", file=sys.stderr, flush=True)
        return False
    except subprocess.CalledProcessError as err:
        if not allow_failure:
            raise
        print(f"WARN: command failed with exit code {err.returncode}", file=sys.stderr, flush=True)
        return False
    return True


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
    parser.add_argument("--skip-enrichment", action="store_true", help="Skip hourly curated-field enrichment.")
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=DEFAULT_HOURLY_ENRICH_LIMIT,
        help="Maximum projects to enrich for missing curated fields.",
    )
    parser.add_argument(
        "--enrich-batch-size",
        type=int,
        default=DEFAULT_HOURLY_ENRICH_BATCH_SIZE,
        help="Projects to send to Codex per hourly enrichment batch.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    py = sys.executable
    os.chdir(ROOT)

    run([py, "scripts/build.py", "--refresh"])
    if not args.skip_enrichment and args.enrich_limit > 0:
        run(
            [
                py,
                "scripts/enrich-projects.py",
                "--mode",
                "new",
                "--include-missing-curated-fields",
                "--limit",
                str(args.enrich_limit),
                "--batch-size",
                str(args.enrich_batch_size),
            ],
            timeout=DEFAULT_HOURLY_ENRICH_TIMEOUT_SECONDS,
            allow_failure=True,
        )
    if not args.skip_isotopes:
        isotope_command = ["bash", "scripts/build-isotopes.sh"]
        if args.skip_isotope_builds:
            isotope_command.append("--skip-builds")
        run(isotope_command)
    run([py, "scripts/export-automic-vault-db.py"])
    run([py, "scripts/check-automic-vault-db-health.py"])
    run([py, "scripts/publish-public-db.py"])
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
