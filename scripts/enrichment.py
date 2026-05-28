from __future__ import annotations

import json
import re
import urllib.parse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.bootstrap.lib.common import PROJECTS_DIR, ROOT, stable_hash, write_json
from scripts.bootstrap.lib.yaml_writer import yaml_text


CONFIDENCE = {"low": 1, "medium": 2, "high": 3}
CURATED_FIELDS = ("display-name", "docs", "category", "tags")
CATEGORIES = {
    "developer-tools",
    "cloud-infrastructure",
    "security",
    "data",
    "media",
    "networking",
    "system",
    "language-runtime",
    "science",
    "productivity",
    "games",
    "other",
}
TAG_REPLACEMENTS = {
    "awscli": "aws",
    "cli-tool": "cli",
    "cli-tools": "cli",
    "cmdline": "cli",
    "command-line": "cli",
    "commandline": "cli",
    "k8s": "kubernetes",
}
BANNED_TAGS = {
    "app",
    "application",
    "software",
    "tool",
    "tools",
    "utility",
    "utilities",
}
SOURCE_FACT_KEYS = (
    "id",
    "provider",
    "name",
    "homepage",
    "repo",
    "description",
    "executables",
    "package-manager",
    "package-manager-url",
    "version",
    "license",
    "source-archive",
    "provenance",
)


def today_iso() -> str:
    return date.today().isoformat()


def run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def parse_project_yaml(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, record)]
    pending_key: tuple[int, dict[str, Any], str] | None = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if text.startswith("- "):
            if not isinstance(parent, list):
                continue
            parent.append(unquote_scalar(text[2:].strip()))
            continue

        if ":" not in text or not isinstance(parent, dict):
            continue
        key, value = text.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = unquote_scalar(value)
            pending_key = None
            continue

        child: Any
        pending_key = (indent, parent, key)
        child = {}
        parent[key] = child
        stack.append((indent, child))

        # Convert empty mappings to lists when the first child is a sequence.
        # The project YAML shape only uses this for scalar lists.
        pending_key = (indent, parent, key)

    # Second pass for top-level/nested scalar lists. This keeps the parser small
    # and avoids taking a dependency on PyYAML in the build path.
    return parse_project_yaml_lists(path.read_text(encoding="utf-8"), record)


def parse_project_yaml_lists(text: str, base: dict[str, Any]) -> dict[str, Any]:
    record = deepcopy(base)
    path_stack: list[tuple[int, str]] = []
    current_list: tuple[int, dict[str, Any], str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text_line = raw.strip()
        while path_stack and indent <= path_stack[-1][0]:
            path_stack.pop()
        if text_line.startswith("- ") and current_list and indent > current_list[0]:
            current_list[1].setdefault(current_list[2], []).append(unquote_scalar(text_line[2:].strip()))
            continue
        current_list = None
        if ":" not in text_line or text_line.startswith("- "):
            continue
        key, value = text_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        parent = nested_parent(record, [item[1] for item in path_stack])
        if not value:
            current_list = (indent, parent, key)
            if key not in parent or parent[key] == {}:
                parent[key] = []
            path_stack.append((indent, key))
        else:
            parent[key] = unquote_scalar(value)
    return record


def nested_parent(record: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    parent = record
    for key in keys:
        child = parent.get(key)
        if not isinstance(child, dict):
            return parent
        parent = child
    return parent


def unquote_scalar(value: str) -> Any:
    if value == "null":
        return None
    if value in {"true", "false"}:
        return value == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def provider_name(record: dict[str, Any]) -> tuple[str, str]:
    project_id = str(record.get("id") or "")
    if ":" in project_id:
        provider, name = project_id.split(":", 1)
        return provider, name
    return "", project_id


def normalize_url(value: Any, *, keep_fragment: bool = False) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    query = [(key, val) for key, val in query if not key.lower().startswith("utm_")]
    query = [(key, val) for key, val in query if key.lower() not in {"fbclid", "gclid"}]
    fragment = parsed.fragment if keep_fragment and parsed.fragment else ""
    normalized = urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path or "", urllib.parse.urlencode(query), fragment))
    normalized = re.sub(r"^https://github\.com/([^/]+)/([^/.]+)\.git$", r"https://github.com/\1/\2", normalized)
    normalized = re.sub(r"^https://gitlab\.com/([^/]+)/([^/.]+)\.git$", r"https://gitlab.com/\1/\2", normalized)
    return normalized


def normalize_docs(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        url = normalize_url(value, keep_fragment="README" in str(value) or "readme" in str(value))
        if url and not rejected_docs_url(url):
            result.append(url)
    return sorted(set(result), key=docs_rank)


def rejected_docs_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "formulae.brew.sh" in host:
        return True
    if any(token in host for token in ("tutorial", "mirror", "readthedocs.io.evil")):
        return True
    if any(token in path for token in ("/blog/", "/posts/", "/tutorial")):
        return True
    return False


def docs_rank(url: str) -> tuple[int, str]:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.startswith("docs.") or ".docs." in host or "readthedocs.io" in host:
        return (0, url)
    if any(part in path for part in ("/docs", "/documentation", "/manual")):
        return (1, url)
    if "github.com" in host and "/wiki" in path:
        return (2, url)
    if "github.com" in host and "readme" in path:
        return (3, url)
    return (4, url)


def normalize_tag(value: Any) -> str:
    tag = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return TAG_REPLACEMENTS.get(tag, tag)


def normalize_tags(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        values = []
    tags = {"cli"}
    for value in values:
        tag = normalize_tag(value)
        if tag and tag not in BANNED_TAGS:
            tags.add(tag)
    return sorted(tags)


def normalize_category_path(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = [part for part in value.split("/") if part]
    elif isinstance(value, list):
        parts = [str(part) for part in value if str(part).strip()]
    else:
        parts = []
    parts = [re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-") for part in parts]
    if not parts or parts[0] not in CATEGORIES:
        return []
    return parts


def normalize_display_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def source_facts(record: dict[str, Any]) -> dict[str, Any]:
    provider, name = provider_name(record)
    facts = {
        "id": record.get("id"),
        "provider": provider,
        "name": name,
        "homepage": normalize_url(record.get("homepage")),
        "repo": normalize_url(record.get("repo")),
        "description": re.sub(r"\s+", " ", str(record.get("description") or "")).strip(),
        "executables": sorted(str(item) for item in record.get("executables") or []),
        "package-manager": normalize_string_map(record.get("package-manager")),
        "package-manager-url": normalize_url(record.get("package-manager-url")),
        "version": str(record.get("version") or ""),
        "license": str(record.get("license") or ""),
        "source-archive": normalize_url(record.get("source-archive")),
        "provenance": normalize_string_map(record.get("provenance")),
    }
    return {key: facts[key] for key in SOURCE_FACT_KEYS}


def normalize_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in sorted(value.items()) if val not in (None, "", [], {})}


def curation_facts(record: dict[str, Any]) -> dict[str, Any]:
    path = normalize_category_path(record.get("category"))
    return {
        "display-name": normalize_display_name(record.get("display-name")),
        "docs": normalize_docs(record.get("docs")),
        "category_path": path,
        "category": path[0] if path else "",
        "tags": normalize_tags(record.get("tags")),
    }


def hash_source_facts(record: dict[str, Any]) -> str:
    return stable_hash(source_facts(record))


def hash_curation_facts(record: dict[str, Any]) -> str:
    return stable_hash(curation_facts(record))


def load_projects(provider: str = "brew", projects_dir: Path = PROJECTS_DIR) -> list[dict[str, Any]]:
    projects = []
    for path in sorted((projects_dir / provider).glob("*.yml")):
        record = parse_project_yaml(path)
        record["__path"] = path
        projects.append(record)
    return projects


def update_observed_state(state: dict[str, Any], projects: list[dict[str, Any]], today: str) -> None:
    for record in projects:
        project_id = str(record.get("id") or "")
        if not project_id:
            continue
        entry = state.setdefault(project_id, {})
        source_hash = hash_source_facts(record)
        curation_hash = hash_curation_facts(record)
        if not entry.get("first_observed"):
            entry["first_observed"] = today
        entry["last_observed"] = today
        if entry.get("source_fact_hash") != source_hash:
            previous = entry.setdefault("previous_source_hashes", [])
            if entry.get("source_fact_hash") and entry["source_fact_hash"] not in previous:
                previous.append(entry["source_fact_hash"])
            entry["last_source_change"] = today
        entry["source_fact_hash"] = source_hash
        entry["curation_hash"] = curation_hash
        mark_manual_fields(entry, record)


def mark_manual_fields(entry: dict[str, Any], record: dict[str, Any]) -> None:
    ownership = entry.setdefault("field_ownership", {})
    managed = entry.setdefault("managed_values", {})
    current = curation_facts(record)
    for field in CURATED_FIELDS:
        current_value = current.get("category" if field == "category" else field)
        if field in managed and current_value != managed[field]:
            ownership[field] = "manual"
        else:
            ownership.setdefault(field, "managed" if field in managed else "unknown")


def select_projects(
    projects: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    mode: str,
    today: str,
) -> list[dict[str, Any]]:
    selected = []
    cutoff = date.fromisoformat(today) - timedelta(days=90)
    for record in projects:
        project_id = str(record.get("id") or "")
        entry = state.get(project_id) or {}
        if mode == "replace":
            selected.append(record)
        elif mode == "new" and needs_new_curation(record):
            selected.append(record)
        elif mode == "review-stale-updated" and needs_stale_updated_review(entry, cutoff):
            selected.append(record)
    return selected


def needs_new_curation(record: dict[str, Any]) -> bool:
    _, name = provider_name(record)
    facts = curation_facts(record)
    return (
        not facts["docs"]
        or not facts["category"]
        or facts["tags"] == ["cli"]
        or not facts["display-name"]
        or facts["display-name"] == name
    )


def needs_stale_updated_review(entry: dict[str, Any], cutoff: date) -> bool:
    changed = parse_date(entry.get("last_source_change"))
    if not changed or changed < cutoff:
        return False
    verified = parse_date(entry.get("last_verified"))
    if not verified or verified < cutoff:
        return True
    confidence = entry.get("field_confidence") or {}
    return any(value == "low" for value in confidence.values())


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def review_input(projects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": 1,
        "projects": [
            {
                "id": record.get("id"),
                "source_facts": source_facts(record),
                "current_curation": curation_facts(record),
            }
            for record in projects
        ],
    }


def prompt_text(input_path: Path) -> str:
    return f"""Determine official documentation URLs, category, tags, and display name for the projects listed in this input JSON:

{input_path}

Return JSON that matches the provided output schema. Do not edit files. Use official sources only.

Documentation URL priority:
1. dedicated official docs domain
2. official /docs, /documentation, or equivalent path
3. official GitHub/GitLab wiki
4. official README anchor
5. homepage fallback only when it is the best official documentation surface

Reject random tutorials, blog posts, package-manager pages, mirrors, scraped docs sites, SEO aggregators, and unrelated vendor pages.

Use confidence values high, medium, or low for each field, and include concise provenance/source notes.
"""


def validate_codex_payload(payload: Any, expected_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    errors = []
    if not isinstance(payload, dict):
        return [], ["codex output must be a JSON object"]
    results = payload.get("results")
    if not isinstance(results, list):
        return [], ["codex output must contain results list"]
    normalized = []
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            errors.append(f"results[{index}] must be an object")
            continue
        project_id = str(item.get("id") or "")
        if project_id not in expected_ids:
            errors.append(f"{project_id or f'results[{index}]'}: unexpected id")
            continue
        normalized_item, item_errors = normalize_codex_result(item)
        if item_errors:
            errors.extend(f"{project_id}: {error}" for error in item_errors)
            continue
        normalized.append(normalized_item)
    return normalized, errors


def normalize_codex_result(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors = []
    category_path = normalize_category_path(item.get("category_path"))
    if not category_path:
        errors.append("category_path must start with a known top-level category")
    docs = normalize_docs(item.get("docs"))
    tags = normalize_tags(item.get("tags"))
    display_name = normalize_display_name(item.get("display-name"))
    if not display_name:
        errors.append("display-name is required")
    result = {
        "id": str(item.get("id")),
        "display-name": display_name,
        "display-name-confidence": normalize_confidence(item.get("display-name-confidence"), errors, "display-name-confidence"),
        "category_path": category_path,
        "category-confidence": normalize_confidence(item.get("category-confidence"), errors, "category-confidence"),
        "docs": docs,
        "docs-confidence": normalize_confidence(item.get("docs-confidence"), errors, "docs-confidence"),
        "tags": tags,
        "tags-confidence": normalize_confidence(item.get("tags-confidence"), errors, "tags-confidence"),
        "docs_sources": normalize_sources(item.get("docs_sources")),
        "category_sources": normalize_sources(item.get("category_sources")),
        "tags_sources": normalize_sources(item.get("tags_sources")),
        "display_name_sources": normalize_sources(item.get("display_name_sources")),
    }
    return result, errors


def normalize_confidence(value: Any, errors: list[str], field: str) -> str:
    confidence = str(value or "").lower()
    if confidence not in CONFIDENCE:
        errors.append(f"{field} must be high, medium, or low")
        return "low"
    return confidence


def normalize_sources(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return sorted({re.sub(r"\s+", " ", str(item)).strip() for item in value if str(item).strip()})


def confidence_allows(value: str, threshold: str) -> bool:
    return CONFIDENCE[value] >= CONFIDENCE[threshold]


def apply_results(
    projects_by_id: dict[str, dict[str, Any]],
    state: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    confidence_threshold: str,
    today: str,
    dry_run: bool = False,
) -> dict[str, int]:
    summary = {"reviewed": len(results), "changed": 0, "rejected": 0, "skipped_low_confidence": 0, "no_op": 0}
    for result in results:
        project_id = result["id"]
        record = projects_by_id.get(project_id)
        if record is None:
            summary["rejected"] += 1
            continue
        entry = state.setdefault(project_id, {})
        original = deepcopy(record)
        changed, skipped = merge_result(record, entry, result, confidence_threshold)
        entry["last_verified"] = today
        entry["field_confidence"] = {
            "display-name": result["display-name-confidence"],
            "category": result["category-confidence"],
            "docs": result["docs-confidence"],
            "tags": result["tags-confidence"],
        }
        entry["provenance"] = {
            "docs_sources": result["docs_sources"],
            "category_sources": result["category_sources"],
            "tags_sources": result["tags_sources"],
            "display_name_sources": result["display_name_sources"],
        }
        entry["curation_hash"] = hash_curation_facts(record)
        if skipped:
            summary["skipped_low_confidence"] += skipped
        if curation_facts(original) == curation_facts(record):
            summary["no_op"] += 1
            continue
        summary["changed"] += 1
        if not dry_run:
            path = record.get("__path")
            if isinstance(path, Path):
                public = {key: value for key, value in record.items() if not key.startswith("__")}
                path.write_text(yaml_text(public), encoding="utf-8")
    return summary


def merge_result(record: dict[str, Any], entry: dict[str, Any], result: dict[str, Any], threshold: str) -> tuple[bool, int]:
    skipped = 0
    ownership = entry.setdefault("field_ownership", {})
    managed = entry.setdefault("managed_values", {})

    field_values = {
        "display-name": normalize_display_name(result["display-name"]),
        "category": normalize_category_path(result["category_path"])[0],
        "docs": normalize_docs(result["docs"]),
        "tags": normalize_tags(result["tags"]),
    }
    confidence_fields = {
        "display-name": result["display-name-confidence"],
        "category": result["category-confidence"],
        "docs": result["docs-confidence"],
        "tags": result["tags-confidence"],
    }
    changed = False
    for field, new_value in field_values.items():
        current_value = curation_facts(record).get("category" if field == "category" else field)
        is_empty = current_value in ("", [], None) or (field == "tags" and current_value == ["cli"])
        if not confidence_allows(confidence_fields[field], threshold) and not is_empty:
            skipped += 1
            continue
        if ownership.get(field) == "manual" and not is_empty:
            if field == "tags":
                new_value = sorted(set(current_value) | set(new_value))
            elif field == "docs":
                new_value = sorted(set(current_value) | set(new_value), key=docs_rank)
            else:
                continue
        if current_value == new_value:
            managed[field] = new_value
            ownership[field] = "managed" if ownership.get(field) != "manual" else ownership[field]
            continue
        record[field] = new_value
        managed[field] = new_value
        ownership[field] = "managed" if ownership.get(field) != "manual" else ownership[field]
        changed = True
    return changed, skipped


def write_run_artifacts(run_dir: Path, input_payload: dict[str, Any], prompt: str) -> tuple[Path, Path]:
    input_path = run_dir / "input.json"
    prompt_path = run_dir / "prompt.md"
    write_json(input_path, input_payload)
    prompt_path.write_text(prompt, encoding="utf-8")
    return input_path, prompt_path
