#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "cache" / "enrichment" / "runs"


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


def unresolved_runs() -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    if not RUNS_DIR.is_dir():
        return runs

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
        batches = manifest.get("batches") or []
        pending_batches = [batch for batch in batches if str(batch.get("status") or "") == "pending"]
        runs.append(
            {
                "run_id": run_dir.name,
                "run_dir": str(run_dir.relative_to(ROOT)),
                "mode": str(manifest.get("mode") or ""),
                "provider": str(manifest.get("provider") or "brew"),
                "batch_size": int(manifest.get("batch_size") or 0),
                "selected_count": selected_count,
                "include_missing_curated_fields": bool(manifest.get("include_missing_curated_fields")),
                "pending_batches": len(pending_batches),
                "batch_count": len(batches),
            }
        )
    return runs


def apply_command(run: dict[str, Any]) -> list[str]:
    command = [
        "python3",
        "scripts/enrich-projects.py",
        "--mode",
        str(run["mode"]),
        "--batch-size",
        str(run["batch_size"]),
        "--backend",
        "external",
        "--phase",
        "apply",
        "--provider",
        str(run["provider"]),
        "--run-id",
        str(run["run_id"]),
    ]
    if bool(run.get("include_missing_curated_fields")):
        command.append("--include-missing-curated-fields")
    else:
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

    show = subparsers.add_parser("show", help="Show one run by id.")
    show.add_argument("run_id", help="Run id under cache/enrichment/runs/")
    show.add_argument("--json", action="store_true", help="Emit a JSON object.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runs = unresolved_runs()

    if args.command == "pending":
        for run in runs:
            print_run(run, json_output=bool(args.json))
        return 0

    if args.command == "next-run":
        if not runs:
            return 1
        print_run(runs[0], json_output=bool(args.json))
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
