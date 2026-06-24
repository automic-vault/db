#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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
DEFAULT_HOURLY_ENRICH_BATCH_SIZE = int(os.environ.get("AVDB_HOURLY_ENRICH_BATCH_SIZE", "3"))
DEFAULT_HOURLY_ENRICH_PREPARE_TIMEOUT_SECONDS = int(os.environ.get("AVDB_HOURLY_ENRICH_PREPARE_TIMEOUT_SECONDS", "300"))
ENRICHMENT_RUNS_DIR = ROOT / "cache" / "enrichment" / "runs"
PREPARE_OUTPUT_PATTERN = re.compile(
    r"Prepared \d+ projects in \d+ batches under (?P<run_dir>cache/enrichment/runs/[^\s]+)"
)


class EnrichmentHealthError(RuntimeError):
    pass


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


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_prepared_run_dir(stdout: str) -> Path | None:
    match = PREPARE_OUTPUT_PATTERN.search(stdout)
    if not match:
        return None
    return ROOT / match.group("run_dir")


def unresolved_enrichment_run_ids(*, exclude_run_id: str | None = None) -> list[str]:
    if not ENRICHMENT_RUNS_DIR.is_dir():
        return []

    unresolved: list[str] = []
    for manifest_path in sorted(ENRICHMENT_RUNS_DIR.glob("*/controller-manifest.json")):
        run_dir = manifest_path.parent
        run_id = run_dir.name
        if exclude_run_id and run_id == exclude_run_id:
            continue
        manifest = read_json(manifest_path)
        selected_count = int(manifest.get("selected_count") or 0)
        if selected_count < 1:
            continue
        if (run_dir / "apply-summary.json").exists():
            continue
        unresolved.append(run_id)
    return unresolved


def warn_unresolved_hourly_enrichment_backlog(run_ids: list[str]) -> None:
    sample = ", ".join(run_ids[-3:])
    print(
        "WARN: skipping hourly enrichment prepare because "
        f"{len(run_ids)} older prepared run(s) are still unapplied ({sample})",
        file=sys.stderr,
        flush=True,
    )


def assert_hourly_enrichment_progress(run_dir: Path) -> None:
    manifest_path = run_dir / "controller-manifest.json"
    if not manifest_path.is_file():
        raise EnrichmentHealthError(f"hourly enrichment prepared a run without {manifest_path}")

    manifest = read_json(manifest_path)
    selected_count = int(manifest.get("selected_count") or 0)
    if selected_count < 1:
        return

    unresolved_older = unresolved_enrichment_run_ids(exclude_run_id=run_dir.name)
    if not unresolved_older:
        return

    sample = ", ".join(unresolved_older[-3:])
    raise EnrichmentHealthError(
        "hourly enrichment prepared "
        f"{selected_count} project(s) in {run_dir.name}, but {len(unresolved_older)} older prepared run(s) "
        f"are still unapplied ({sample})"
    )


def run_prepare_enrichment(command: list[str], *, timeout: int) -> Path | None:
    print("+", " ".join(command), flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"WARN: command timed out after {timeout}s", file=sys.stderr, flush=True)
        return None
    except subprocess.CalledProcessError as err:
        if err.stdout:
            print(err.stdout, end="", flush=True)
        if err.stderr:
            print(err.stderr, end="", file=sys.stderr, flush=True)
        print(f"WARN: command failed with exit code {err.returncode}", file=sys.stderr, flush=True)
        return None

    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    return parse_prepared_run_dir(result.stdout)


def commit_if_changed(message: str) -> str | None:
    run(["git", "add", "-A", "--", *COMMIT_PATHS])
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *COMMIT_PATHS], cwd=ROOT)
    if diff.returncode == 0:
        return None
    if diff.returncode != 1:
        diff.check_returncode()
    changed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", *COMMIT_PATHS],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    changed_paths = [line for line in changed.stdout.splitlines() if line]
    if not changed_paths:
        return None
    subprocess.run(["git", "commit", "-m", message, "--", *changed_paths], cwd=ROOT, check=True)
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
        unresolved = unresolved_enrichment_run_ids()
        if unresolved:
            warn_unresolved_hourly_enrichment_backlog(unresolved)
        else:
            run_dir = run_prepare_enrichment(
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
                    "--backend",
                    "external",
                    "--phase",
                    "prepare",
                ],
                timeout=DEFAULT_HOURLY_ENRICH_PREPARE_TIMEOUT_SECONDS,
            )
            if run_dir is not None:
                assert_hourly_enrichment_progress(run_dir)
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
