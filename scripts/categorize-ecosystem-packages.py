#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.bootstrap.lib.common import read_json, write_json
from scripts.enrichment import CATEGORIES, normalize_category_path, normalize_tags


SCHEMA_VERSION = 1
RUNS_DIR = ROOT / "cache" / "ecosystem-taxonomy" / "runs"
OUTPUT_PATH = ROOT / "data" / "pkg-ecosystem-taxonomy.json"
DB_JSON_PATH = ROOT / "cache" / "automic-vault" / "db.json"
CRATES_INDEX_PATH = ROOT / "cache" / "cratesio" / "index.json"
CONFIDENCE = {"high", "medium", "low"}


def utc_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def package_url(provider: str, name: str, info: dict[str, Any]) -> str:
    if info.get("packageManagerUrl"):
        return str(info["packageManagerUrl"])
    if provider == "npm":
        return f"https://www.npmjs.com/package/{urllib.parse.quote(name, safe='@/')}"
    return f"https://crates.io/crates/{urllib.parse.quote(name, safe='')}"


def npm_packages(db: dict[str, Any]) -> list[dict[str, Any]]:
    packages = db.get("npms") if isinstance(db, dict) else None
    if not isinstance(packages, dict):
        return []
    result = []
    for name, info in sorted(packages.items()):
        if not isinstance(name, str) or not name or not isinstance(info, dict):
            continue
        executable = clean_text(info.get("executable"))
        result.append({
            "id": f"npm:{name}",
            "provider": "npm",
            "name": name,
            "displayName": name,
            "summary": clean_text(info.get("summary")),
            "homepage": clean_text(info.get("homepage")),
            "repository": clean_text(info.get("repository") or info.get("repo")),
            "version": clean_text(info.get("version")),
            "license": clean_text(info.get("license")),
            "packageManagerUrl": package_url("npm", name, info),
            "executables": [executable] if executable else [],
            "keywords": [clean_text(item) for item in info.get("keywords") or [] if clean_text(item)],
        })
    return result


def cargo_packages(index: dict[str, Any]) -> list[dict[str, Any]]:
    packages = index.get("crates") if isinstance(index, dict) else None
    if not isinstance(packages, dict):
        return []
    result = []
    for name, info in sorted(packages.items()):
        if not isinstance(name, str) or not name or not isinstance(info, dict):
            continue
        result.append({
            "id": f"cargo:{name}",
            "provider": "cargo",
            "name": name,
            "displayName": name,
            "summary": clean_text(info.get("summary")),
            "homepage": clean_text(info.get("homepage")),
            "repository": clean_text(info.get("repository")),
            "version": clean_text(info.get("version")),
            "license": clean_text(info.get("license")),
            "packageManagerUrl": package_url("cargo", name, info),
            "executables": [
                clean_text(item.get("name"))
                for item in info.get("executables") or []
                if isinstance(item, dict) and clean_text(item.get("name"))
            ],
            "keywords": [],
        })
    return result


def load_candidates(provider: str, db_path: Path, crates_index_path: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if provider in {"all", "npm"}:
        candidates.extend(npm_packages(read_json(db_path, {})))
    if provider in {"all", "cargo"}:
        candidates.extend(cargo_packages(read_json(crates_index_path, {})))
    return sorted(candidates, key=lambda item: (item["provider"], item["name"].lower(), item["name"]))


def load_taxonomy(path: Path) -> dict[str, Any]:
    data = read_json(path, {"schema": SCHEMA_VERSION, "packages": {}})
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise SystemExit(f"{path} must contain a packages object")
    return data


def selected_candidates(candidates: list[dict[str, Any]], taxonomy: dict[str, Any], include_existing: bool) -> list[dict[str, Any]]:
    if include_existing:
        return candidates
    existing = set((taxonomy.get("packages") or {}).keys())
    return [item for item in candidates if item["id"] not in existing]


def batches(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size < 1:
        raise SystemExit("--batch-size must be at least 1")
    return [items[index:index + size] for index in range(0, len(items), size)]


def prompt_text(input_path: Path, count: int) -> str:
    roots = ", ".join(sorted(CATEGORIES))
    return f"""Categorize these npm and Cargo CLI packages for av.db package pages.

Read the input JSON at `{input_path}`. Return JSON only in `codex-output.json` shape:
{{"results":[{{"id":"npm:name","displayName":"Name","category":"developer-tools","categoryPath":["developer-tools","subarea"],"categoryConfidence":"high","tags":["cli"],"tagsConfidence":"high","categorySources":["source note"],"tagsSources":["source note"]}}]}}

Rules:
- Return exactly one result for each of the {count} package ids in the input.
- The first categoryPath item and category must be one of: {roots}.
- Tags and category path parts must be lowercase slug strings.
- Prefer source facts from summary, homepage, repository, executables, keywords, and package manager URL.
- Use concise source notes. Do not invent repo, docs, history, or install metadata.
"""


def output_schema(expected_ids: list[str]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "minItems": len(expected_ids),
                "maxItems": len(expected_ids),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "displayName",
                        "category",
                        "categoryPath",
                        "categoryConfidence",
                        "tags",
                        "tagsConfidence",
                        "categorySources",
                        "tagsSources",
                    ],
                    "properties": {
                        "id": {"type": "string", "enum": expected_ids},
                        "displayName": {"type": "string", "minLength": 1},
                        "category": {"type": "string", "enum": sorted(CATEGORIES)},
                        "categoryPath": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "categoryConfidence": {"type": "string", "enum": sorted(CONFIDENCE)},
                        "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "tagsConfidence": {"type": "string", "enum": sorted(CONFIDENCE)},
                        "categorySources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                        "tagsSources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    },
                },
            },
        },
        "required": ["results"],
    }


def write_run_artifacts(run_dir: Path, selected: list[dict[str, Any]], batch_size: int) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "batch_size": batch_size,
        "selected_count": len(selected),
        "batches": [],
    }
    write_json(run_dir / "input.json", {"schema": SCHEMA_VERSION, "packages": selected})
    (run_dir / "prompt.md").write_text(prompt_text(run_dir / "input.json", len(selected)), encoding="utf-8")
    for index, batch in enumerate(batches(selected, batch_size), start=1):
        batch_dir = run_dir / "batches" / f"{index:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        ids = [item["id"] for item in batch]
        write_json(batch_dir / "input.json", {"schema": SCHEMA_VERSION, "packages": batch})
        (batch_dir / "prompt.md").write_text(prompt_text(batch_dir / "input.json", len(batch)), encoding="utf-8")
        write_json(batch_dir / "output-schema.json", output_schema(ids))
        manifest["batches"].append({
            "batch": f"{index:04d}",
            "batch_dir": str(batch_dir.relative_to(ROOT)),
            "codex_output_path": str((batch_dir / "codex-output.json").relative_to(ROOT)),
            "expected_ids": ids,
            "input_path": str((batch_dir / "input.json").relative_to(ROOT)),
            "prompt_path": str((batch_dir / "prompt.md").relative_to(ROOT)),
        })
    write_json(run_dir / "controller-manifest.json", manifest)
    return manifest


def clean_sources(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(item) for item in value if clean_text(item)]


def normalize_result(item: Any, expected_ids: set[str]) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(item, dict):
        return None, ["result must be an object"]
    package_id = clean_text(item.get("id"))
    errors = []
    if package_id not in expected_ids:
        errors.append(f"{package_id or '<missing>'}: unexpected package id")
    category_path = normalize_category_path(item.get("categoryPath"))
    category = clean_text(item.get("category"))
    if not category_path:
        errors.append(f"{package_id}: categoryPath must start with a known category")
    elif category and category != category_path[0]:
        errors.append(f"{package_id}: category must match categoryPath root")
    tags = normalize_tags(item.get("tags"))
    category_confidence = clean_text(item.get("categoryConfidence"))
    tags_confidence = clean_text(item.get("tagsConfidence"))
    if category_confidence not in CONFIDENCE:
        errors.append(f"{package_id}: invalid categoryConfidence")
    if tags_confidence not in CONFIDENCE:
        errors.append(f"{package_id}: invalid tagsConfidence")
    display_name = clean_text(item.get("displayName"))
    if not display_name:
        errors.append(f"{package_id}: missing displayName")
    if errors:
        return None, errors
    sources = clean_sources(item.get("categorySources"))
    tag_sources = clean_sources(item.get("tagsSources"))
    return {
        "id": package_id,
        "displayName": display_name,
        "category": category_path[0],
        "categoryPath": category_path,
        "categoryConfidence": category_confidence,
        "categorySources": sources or ["AI curation from local package facts."],
        "tags": tags,
        "tagsConfidence": tags_confidence,
        "tagsSources": tag_sources or ["AI curation from local package facts."],
    }, []


def validate_payload(payload: Any, expected_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return [], ["codex output must contain a results list"]
    normalized = []
    errors = []
    seen = set()
    for item in payload["results"]:
        result, item_errors = normalize_result(item, expected_ids)
        if item_errors:
            errors.extend(item_errors)
            continue
        assert result is not None
        if result["id"] in seen:
            errors.append(f"{result['id']}: duplicate result")
            continue
        seen.add(result["id"])
        normalized.append(result)
    missing = sorted(expected_ids - seen)
    if missing:
        errors.append(f"missing results for {len(missing)} package ids: {', '.join(missing[:20])}")
    return normalized, errors


def merge_taxonomy(taxonomy: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    packages = dict(taxonomy.get("packages") or {})
    for entry in entries:
        packages[entry["id"]] = entry
    return {
        "schema": SCHEMA_VERSION,
        "packages": {key: packages[key] for key in sorted(packages)},
    }


def apply_run(run_dir: Path, output_path: Path) -> int:
    manifest = read_json(run_dir / "controller-manifest.json")
    taxonomy = load_taxonomy(output_path)
    all_entries: list[dict[str, Any]] = []
    all_errors: list[str] = []
    for batch in manifest.get("batches") or []:
        expected_ids = set(str(item) for item in batch.get("expected_ids") or [])
        output = ROOT / str(batch["codex_output_path"])
        if not output.exists():
            all_errors.append(f"missing codex output: {output.relative_to(ROOT)}")
            continue
        entries, errors = validate_payload(read_json(output), expected_ids)
        all_entries.extend(entries)
        all_errors.extend(errors)
        write_json(ROOT / str(batch["batch_dir"]) / "normalized-output.json", {"results": entries, "errors": errors})
    write_json(output_path, merge_taxonomy(taxonomy, all_entries))
    write_json(run_dir / "apply-summary.json", {"changed": len(all_entries), "errors": all_errors})
    if all_errors:
        for error in all_errors[:20]:
            print(f"ERROR {error}", file=sys.stderr)
        return 1
    print(f"Applied {len(all_entries)} taxonomy entries to {output_path.relative_to(ROOT)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or apply npm/Cargo taxonomy curation batches.")
    parser.add_argument("--phase", choices=["prepare", "apply"], default="prepare")
    parser.add_argument("--provider", choices=["all", "npm", "cargo"], default="all")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--db", type=Path, default=DB_JSON_PATH)
    parser.add_argument("--crates-index", type=Path, default=CRATES_INDEX_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = RUNS_DIR / (args.run_id or utc_run_id())
    if args.phase == "apply":
        return apply_run(run_dir, args.output)
    taxonomy = load_taxonomy(args.output)
    candidates = selected_candidates(
        load_candidates(args.provider, args.db, args.crates_index),
        taxonomy,
        args.include_existing,
    )
    if args.limit:
        candidates = candidates[:args.limit]
    manifest = write_run_artifacts(run_dir, candidates, args.batch_size)
    print(f"Prepared {manifest['selected_count']} packages in {len(manifest['batches'])} batches under {run_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
