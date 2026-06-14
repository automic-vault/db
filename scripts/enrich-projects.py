#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_PATH))

from scripts.bootstrap.lib.common import CACHE_DIR, ROOT, ensure_root, git_commit_if_changed, read_json, write_json
from scripts.bootstrap.lib.render import render_combined_tree
from scripts.enrichment import (
    apply_results,
    load_projects,
    prompt_text,
    review_input,
    run_id,
    select_projects,
    today_iso,
    update_observed_state,
    validation_error_summary,
    validation_rejection_count,
    validate_codex_payload_partial,
    write_run_artifacts,
)


SCHEMA_PATH = ROOT / "schemas" / "codex-project-enrichment-output.schema.json"
DEFAULT_CODEX_TIMEOUT_SECONDS = 15 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review and apply curated project enrichment with Codex.")
    parser.add_argument("--mode", choices=["replace", "new", "review-stale-updated"], required=True)
    parser.add_argument("--limit", type=int, default=0, help="Limit projects sent for review.")
    parser.add_argument("--batch-size", type=int, default=10, help="Projects to send to Codex per batch.")
    parser.add_argument("--force", action="store_true", help="Re-run Codex even when a valid batch checkpoint exists.")
    parser.add_argument("--run-id", help="Resume or write artifacts under a specific enrichment run id.")
    parser.add_argument("--dry-run", action="store_true", help="Build cache/input artifacts without invoking Codex or editing YAML.")
    parser.add_argument("--commit-after-batch", action="store_true", help="Commit tracked YAML changes after each applied batch.")
    parser.add_argument("--provider", default="brew", choices=["brew"], help="Project provider to enrich.")
    parser.add_argument("--confidence-threshold", choices=["high", "medium", "low"], default="medium")
    return parser.parse_args()


def read_codex_output(path: Path) -> object:
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def codex_timeout_seconds() -> float | None:
    raw = os.environ.get("AVDB_CODEX_TIMEOUT_SECONDS")
    if raw is None or raw.strip() == "":
        return DEFAULT_CODEX_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as err:
        raise SystemExit("AVDB_CODEX_TIMEOUT_SECONDS must be a number") from err
    if timeout < 0:
        raise SystemExit("AVDB_CODEX_TIMEOUT_SECONDS must be non-negative")
    return timeout or None


def write_output_schema(output_schema_path: Path, expected_ids: set[str]) -> None:
    schema = read_json(SCHEMA_PATH, default={})
    if not isinstance(schema, dict):
        raise SystemExit(f"{SCHEMA_PATH} must contain a JSON object")
    results_schema = schema.get("properties", {}).get("results", {})
    if not isinstance(results_schema, dict):
        raise SystemExit(f"{SCHEMA_PATH} must define properties.results")
    results_schema["minItems"] = len(expected_ids)
    results_schema["maxItems"] = len(expected_ids)
    item_schema = results_schema.get("items", {})
    if isinstance(item_schema, dict):
        id_schema = item_schema.get("properties", {}).get("id", {})
        if isinstance(id_schema, dict):
            id_schema["enum"] = sorted(expected_ids)
    write_json(output_schema_path, schema)


def invoke_codex(prompt_path: Path, output_path: Path, output_schema_path: Path) -> None:
    prompt = prompt_path.read_text(encoding="utf-8")
    command = [
        "codex",
        "--search",
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(output_schema_path),
        "-C",
        str(ROOT),
        "-o",
        str(output_path),
        prompt,
    ]
    timeout = codex_timeout_seconds()
    try:
        subprocess.run(command, cwd=ROOT, check=True, timeout=timeout)
    except subprocess.TimeoutExpired as err:
        seconds = int(timeout) if timeout is not None else 0
        raise SystemExit(f"Codex enrichment timed out after {seconds}s for {prompt_path}") from err


def batches(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size < 1:
        raise SystemExit("--batch-size must be at least 1")
    return [items[index : index + size] for index in range(0, len(items), size)]


def empty_summary(reviewed: int = 0) -> dict[str, int]:
    return {"reviewed": reviewed, "changed": 0, "rejected": 0, "skipped_low_confidence": 0, "no_op": 0}


def add_summary(target: dict[str, int], source: dict[str, int]) -> None:
    for key in ("changed", "rejected", "skipped_low_confidence", "no_op"):
        target[key] += source.get(key, 0)


def load_valid_checkpoint(
    normalized_path: Path,
    expected_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]] | None:
    if not normalized_path.exists():
        return None
    payload = read_json(normalized_path, default={})
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    normalized, errors, invalid = validate_codex_payload_partial({"results": results}, expected_ids)
    if errors:
        return None
    return normalized, errors, invalid


def validate_and_write_batch(
    codex_output_path: Path,
    normalized_path: Path,
    expected_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = read_codex_output(codex_output_path)
    normalized, errors, invalid = validate_codex_payload_partial(payload, expected_ids)
    error_summary = validation_error_summary(errors)
    write_json(
        normalized_path,
        {"results": normalized, "errors": errors, "error_summary": error_summary, "invalid": invalid},
    )
    return normalized, errors, invalid, error_summary


def print_summary(summary: dict[str, int]) -> None:
    print(
        "Reviewed: {reviewed}\nChanged: {changed}\nRejected: {rejected}\n"
        "Skipped low-confidence: {skipped_low_confidence}\nNo-op: {no_op}".format(**summary)
    )


def main() -> int:
    args = parse_args()
    ensure_root()
    today = today_iso()
    state_path = CACHE_DIR / "enrichment" / "state.json"
    state = read_json(state_path, default={})
    if not isinstance(state, dict):
        raise SystemExit(f"{state_path} must contain a JSON object")

    projects = load_projects(args.provider)
    update_observed_state(state, projects, today)
    selected = select_projects(projects, state, mode=args.mode, today=today)
    if args.limit:
        selected = selected[: args.limit]

    current_run = args.run_id or run_id()
    run_dir = CACHE_DIR / "enrichment" / "runs" / current_run
    run_dir.mkdir(parents=True, exist_ok=True)
    input_payload = review_input(selected)
    input_path = run_dir / "input.json"
    prompt = prompt_text(input_path, len(selected))
    write_run_artifacts(run_dir, input_payload, prompt)

    summary = empty_summary(len(selected))
    all_normalized: list[dict[str, Any]] = []
    all_errors: list[str] = []
    all_invalid: list[dict[str, Any]] = []
    failed_batches = 0
    projects_by_id = {str(record.get("id")): record for record in projects}

    batch_root = run_dir / "batches"
    batch_root.mkdir(parents=True, exist_ok=True)
    for batch_index, batch_projects in enumerate(batches(selected, args.batch_size), start=1):
        batch_dir = batch_root / f"{batch_index:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_input = review_input(batch_projects)
        batch_input_path = batch_dir / "input.json"
        batch_prompt = prompt_text(batch_input_path, len(batch_projects))
        write_run_artifacts(batch_dir, batch_input, batch_prompt)

        batch_summary = empty_summary(len(batch_projects))
        expected_ids = {str(item.get("id")) for item in batch_projects}
        output_schema_path = batch_dir / "output-schema.json"
        normalized_path = batch_dir / "normalized-output.json"
        codex_output_path = batch_dir / "codex-output.json"
        write_output_schema(output_schema_path, expected_ids)

        if args.dry_run:
            write_json(normalized_path, {"results": [], "errors": [], "error_summary": [], "invalid": []})
            write_json(batch_dir / "apply-summary.json", batch_summary)
            continue

        checkpoint = None if args.force else load_valid_checkpoint(normalized_path, expected_ids)
        if checkpoint is not None:
            normalized, errors, invalid = checkpoint
            error_summary = []
            print(f"SKIP batch {batch_index:04d}; valid checkpoint exists")
        else:
            invoke_codex(batch_dir / "prompt.md", codex_output_path, output_schema_path)
            normalized, errors, invalid, error_summary = validate_and_write_batch(codex_output_path, normalized_path, expected_ids)

        batch_summary["rejected"] += validation_rejection_count(expected_ids, normalized)
        all_normalized.extend(normalized)
        all_errors.extend(errors)
        all_invalid.extend(invalid)

        if not normalized:
            failed_batches += 1
            write_json(batch_dir / "apply-summary.json", batch_summary)
            write_json(state_path, state)
            print(f"Batch {batch_index:04d} failed validation; see {normalized_path}", file=sys.stderr)
            if error_summary:
                top_error = error_summary[0]
                print(f"Top validation error: {top_error['error']} ({top_error['count']})", file=sys.stderr)
            summary["rejected"] += batch_summary["rejected"]
            continue

        apply_summary = apply_results(
            projects_by_id,
            state,
            normalized,
            confidence_threshold=args.confidence_threshold,
            today=today,
            dry_run=False,
        )
        add_summary(batch_summary, apply_summary)
        add_summary(summary, batch_summary)
        render_combined_tree()
        write_json(batch_dir / "apply-summary.json", batch_summary)
        write_json(state_path, state)
        if args.commit_after_batch:
            commit = git_commit_if_changed(
                f"nightly: enrich {args.mode} batch {batch_index:04d}",
                ["agents", "combined"],
            )
            print(f"COMMIT batch {batch_index:04d} {commit}" if commit else f"SKIP commit batch {batch_index:04d}; no tracked changes")

        if errors:
            print(
                f"Batch {batch_index:04d} partially failed validation; applied {len(normalized)} valid results; "
                f"see {normalized_path}",
                file=sys.stderr,
            )
            if error_summary:
                top_error = error_summary[0]
                print(f"Top validation error: {top_error['error']} ({top_error['count']})", file=sys.stderr)

    write_json(
        run_dir / "normalized-output.json",
        {
            "results": all_normalized,
            "errors": all_errors,
            "error_summary": validation_error_summary(all_errors),
            "invalid": all_invalid,
        },
    )
    write_json(run_dir / "apply-summary.json", summary)
    write_json(state_path, state)
    print_summary(summary)
    return 1 if failed_batches else 0


if __name__ == "__main__":
    raise SystemExit(main())
