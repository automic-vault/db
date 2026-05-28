#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_PATH))

from scripts.bootstrap.lib.common import CACHE_DIR, ROOT, ensure_root, read_json, write_json
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
    validate_codex_payload_partial,
    write_run_artifacts,
)


SCHEMA_PATH = ROOT / "schemas" / "codex-project-enrichment-output.schema.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review and apply curated project enrichment with Codex.")
    parser.add_argument("--mode", choices=["replace", "new", "review-stale-updated"], required=True)
    parser.add_argument("--limit", type=int, default=0, help="Limit projects sent for review.")
    parser.add_argument("--dry-run", action="store_true", help="Build cache/input artifacts without invoking Codex or editing YAML.")
    parser.add_argument("--provider", default="brew", choices=["brew"], help="Project provider to enrich.")
    parser.add_argument("--confidence-threshold", choices=["high", "medium", "low"], default="medium")
    return parser.parse_args()


def read_codex_output(path: Path) -> object:
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


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
    subprocess.run(
        [
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
        ],
        cwd=ROOT,
        check=True,
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

    current_run = run_id()
    run_dir = CACHE_DIR / "enrichment" / "runs" / current_run
    run_dir.mkdir(parents=True, exist_ok=True)
    input_payload = review_input(selected)
    input_path = run_dir / "input.json"
    prompt = prompt_text(input_path, len(selected))
    write_run_artifacts(run_dir, input_payload, prompt)

    summary = {"reviewed": len(selected), "changed": 0, "rejected": 0, "skipped_low_confidence": 0, "no_op": 0}
    if selected and not args.dry_run:
        codex_output_path = run_dir / "codex-output.json"
        expected_ids = {str(item.get("id")) for item in selected}
        output_schema_path = run_dir / "output-schema.json"
        write_output_schema(output_schema_path, expected_ids)
        invoke_codex(run_dir / "prompt.md", codex_output_path, output_schema_path)
        payload = read_codex_output(codex_output_path)
        normalized, errors, invalid = validate_codex_payload_partial(payload, expected_ids)
        write_json(run_dir / "normalized-output.json", {"results": normalized, "errors": errors, "invalid": invalid})
        if errors:
            summary["rejected"] += len(errors)
            write_json(run_dir / "apply-summary.json", summary)
            write_json(state_path, state)
            print(
                "Reviewed: {reviewed}\nChanged: {changed}\nRejected: {rejected}\n"
                "Skipped low-confidence: {skipped_low_confidence}\nNo-op: {no_op}".format(**summary)
            )
            print(f"Codex output failed validation; see {run_dir / 'normalized-output.json'}", file=sys.stderr)
            return 1
        projects_by_id = {str(record.get("id")): record for record in projects}
        apply_summary = apply_results(
            projects_by_id,
            state,
            normalized,
            confidence_threshold=args.confidence_threshold,
            today=today,
            dry_run=False,
        )
        for key in ("changed", "rejected", "skipped_low_confidence", "no_op"):
            summary[key] += apply_summary.get(key, 0)
        render_combined_tree()
    else:
        write_json(run_dir / "normalized-output.json", {"results": [], "errors": []})

    write_json(run_dir / "apply-summary.json", summary)
    write_json(state_path, state)
    print(
        "Reviewed: {reviewed}\nChanged: {changed}\nRejected: {rejected}\n"
        "Skipped low-confidence: {skipped_low_confidence}\nNo-op: {no_op}".format(**summary)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
