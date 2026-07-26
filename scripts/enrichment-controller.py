#!/usr/bin/env -S uv run --python 3.10
from __future__ import annotations

import argparse
import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "cache" / "enrichment" / "runs"
MODE_ALIASES = {"history-only": "history-missing"}
PROVIDER_ALIASES = {"mixed": "brew"}
CLAIM_FILENAME = "controller-claim.json"
CLAIM_LOCK_FILENAME = ".controller-claim.lock"
DEFAULT_LEASE_SECONDS = 12 * 60 * 60


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def manifest_for_run(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "controller-manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"missing {manifest_path}")
    return read_json(manifest_path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def claim_for_run(run_dir: Path) -> dict[str, Any] | None:
    claim_path = run_dir / CLAIM_FILENAME
    if not claim_path.is_file():
        return None
    try:
        return read_json(claim_path)
    except (json.JSONDecodeError, OSError):
        return None


def claim_is_active(run_dir: Path, *, now: datetime) -> bool:
    claim = claim_for_run(run_dir)
    if claim is None:
        return False
    lease_expires_at = parse_timestamp(claim.get("lease_expires_at"))
    return lease_expires_at is not None and lease_expires_at > now


def run_payload(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    batches = manifest.get("batches") or []
    pending_batches = [batch for batch in batches if str(batch.get("status") or "") == "pending"]
    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "mode": str(manifest.get("mode") or ""),
        "provider": str(manifest.get("provider") or "brew"),
        "batch_size": int(manifest.get("batch_size") or 0),
        "commit_after_batch": bool(manifest.get("commit_after_batch")),
        "selected_count": int(manifest.get("selected_count") or 0),
        "include_missing_curated_fields": bool(manifest.get("include_missing_curated_fields")),
        "pending_batches": len(pending_batches),
        "batch_count": len(batches),
    }


def unresolved_runs(*, now: datetime | None = None) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    if not RUNS_DIR.is_dir():
        return runs
    current_time = now or utc_now()

    for run_dir in sorted(path for path in RUNS_DIR.iterdir() if path.is_dir()):
        manifest_path = run_dir / "controller-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        selected_count = int(manifest.get("selected_count") or 0)
        if selected_count < 1:
            continue
        if (run_dir / "apply-summary.json").is_file():
            continue
        if claim_is_active(run_dir, now=current_time):
            continue
        runs.append(run_payload(run_dir, manifest))
    return runs


@contextmanager
def claim_lock() -> Iterator[None]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNS_DIR / CLAIM_LOCK_FILENAME
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_claim(run_dir: Path, claim: dict[str, Any]) -> None:
    claim_path = run_dir / CLAIM_FILENAME
    claim_path.unlink(missing_ok=True)
    descriptor = os.open(claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, indent=2, sort_keys=True)
        handle.write("\n")


def claim_next(
    *,
    owner: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")
    current_time = now or utc_now()
    with claim_lock():
        runs = unresolved_runs(now=current_time)
        if not runs:
            return None
        run = runs[0]
        run_dir = RUNS_DIR / str(run["run_id"])
        claim = {
            "schema": 1,
            "claim_id": str(uuid.uuid4()),
            "owner": owner,
            "claimed_at": iso_timestamp(current_time),
            "lease_expires_at": iso_timestamp(current_time + timedelta(seconds=lease_seconds)),
        }
        write_claim(run_dir, claim)
        run["claim"] = claim
        return run


def apply_command(run: dict[str, Any]) -> list[str]:
    command = [
        "python3",
        "scripts/enrich-projects.py",
        "--mode",
        MODE_ALIASES.get(str(run["mode"]), str(run["mode"])),
        "--batch-size",
        str(run["batch_size"]),
        "--backend",
        "external",
        "--phase",
        "apply",
        "--provider",
        PROVIDER_ALIASES.get(str(run["provider"]), str(run["provider"])),
        "--run-id",
        str(run["run_id"]),
    ]
    include_missing = bool(run.get("include_missing_curated_fields"))
    if include_missing:
        command.append("--include-missing-curated-fields")
    if bool(run.get("commit_after_batch", not include_missing)):
        command.append("--commit-after-batch")
    return command


def print_run(run: dict[str, Any], *, json_output: bool) -> None:
    payload = dict(run)
    payload["apply_command"] = apply_command(run)
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return
    print(payload["run_id"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect unresolved external enrichment controller runs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pending = subparsers.add_parser("pending", help="List unresolved prepared runs.")
    pending.add_argument("--json", action="store_true", help="Emit JSON objects.")

    next_run = subparsers.add_parser("next-run", help="Print the oldest unresolved prepared run.")
    next_run.add_argument("--json", action="store_true", help="Emit a JSON object.")

    claim_next_run = subparsers.add_parser(
        "claim-next",
        help="Atomically lease and print the oldest available prepared run.",
    )
    claim_next_run.add_argument("--json", action="store_true", help="Emit a JSON object.")
    claim_next_run.add_argument(
        "--owner",
        default="codex-nightly-monolith",
        help="Human-readable owner recorded in the claim.",
    )
    claim_next_run.add_argument(
        "--lease-seconds",
        type=int,
        default=DEFAULT_LEASE_SECONDS,
        help=f"Claim lifetime before recovery is allowed (default: {DEFAULT_LEASE_SECONDS}).",
    )

    show = subparsers.add_parser("show", help="Show one run by id.")
    show.add_argument("run_id", help="Run id under cache/enrichment/runs/")
    show.add_argument("--json", action="store_true", help="Emit a JSON object.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "pending":
        runs = unresolved_runs()
        for run in runs:
            print_run(run, json_output=bool(args.json))
        return 0

    if args.command == "next-run":
        runs = unresolved_runs()
        if not runs:
            return 1
        print_run(runs[0], json_output=bool(args.json))
        return 0

    if args.command == "claim-next":
        if args.lease_seconds < 1:
            raise SystemExit("--lease-seconds must be at least 1")
        run = claim_next(owner=str(args.owner), lease_seconds=int(args.lease_seconds))
        if run is None:
            return 1
        print_run(run, json_output=bool(args.json))
        return 0

    if args.command == "show":
        run_dir = RUNS_DIR / str(args.run_id)
        manifest = manifest_for_run(run_dir)
        batches = manifest.get("batches") or []
        payload = {
            "run_id": run_dir.name,
            "run_dir": str(run_dir.relative_to(ROOT)),
            "mode": str(manifest.get("mode") or ""),
            "provider": str(manifest.get("provider") or "brew"),
            "batch_size": int(manifest.get("batch_size") or 0),
            "commit_after_batch": bool(manifest.get("commit_after_batch")),
            "selected_count": int(manifest.get("selected_count") or 0),
            "include_missing_curated_fields": bool(manifest.get("include_missing_curated_fields")),
            "pending_batches": sum(1 for batch in batches if str(batch.get("status") or "") == "pending"),
            "batch_count": len(batches),
        }
        print_run(payload, json_output=bool(args.json))
        return 0

    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
