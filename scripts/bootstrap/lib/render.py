from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .brew import formula_record
from .common import PROJECTS_DIR, STAGE_DIR, read_json, reset_dir, write_text_if_changed
from .executables import read_executable_index
from .managers import manager_matcher, package_manager_routes
from .yaml_writer import yaml_text


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


def install_line_key(manager: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", manager.lower()).strip("-") or "package-manager"


def project_record(formula: dict[str, Any], executables: list[str], matcher: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    record = formula_record(formula)
    if record is None:
        return None
    name = str(formula.get("name") or "")
    if not executables:
        return None
    record["executables"] = sorted(set(executables))
    package_managers: dict[str, str] = {"brew": name}
    package_manager_match: dict[str, str] = {}
    for route in package_manager_routes(name, executables, matcher):
        key = install_line_key(str(route.get("manager_key") or route.get("manager") or ""))
        package_id = str(route.get("package_id") or "").strip()
        if not key or not package_id:
            continue
        if key not in package_managers:
            package_managers[key] = package_id
        if route.get("match_tier") == "fallback":
            package_manager_match[key] = "fallback"
    record["package-manager"] = package_managers
    if package_manager_match:
        record["package-manager-match"] = package_manager_match
    return record


def render_stage(formulae: list[dict[str, Any]], manager_indexes: dict[str, Any]) -> int:
    reset_dir(STAGE_DIR / "projects")
    executables = read_executable_index()
    matcher = manager_matcher(manager_indexes)
    count = 0
    for formula in sorted(formulae, key=lambda item: str(item.get("name") or "")):
        name = str(formula.get("name") or "")
        record = project_record(formula, executables.get(name, []), matcher)
        if record is None:
            continue
        write_text_if_changed(STAGE_DIR / "projects" / "brew" / f"{name}.yml", yaml_text(record))
        count += 1
    return count


def validate_stage() -> list[str]:
    failures = []
    staged = STAGE_DIR / "projects"
    if not staged.exists():
        return ["missing staged projects directory"]
    files = sorted(staged.glob("brew/*.yml"))
    if not files:
        return ["no Homebrew project YAML files were staged"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "package-manager:" not in text:
            failures.append(f"{path}: missing package-manager")
        if "install-lines:" in text:
            failures.append(f"{path}: contains deprecated install-lines")
        for deprecated in (
            "tags:",
            "dependencies:",
            "build-dependencies:",
            "uses-from-macos:",
            "bottle:",
            "install-behavior:",
        ):
            if deprecated in text:
                failures.append(f"{path}: contains deprecated Homebrew metadata {deprecated.rstrip(':')}")
        if "executables:" not in text:
            failures.append(f"{path}: missing executables")
        if "kind: cli" in text or "exposure: global executable" in text:
            failures.append(f"{path}: contains verbose executable metadata")
        failures.extend(validate_curated_fields(path, text))
        if "\ngenerated-at:" in text:
            failures.append(f"{path}: published output must not include generated-at")
    return failures


def validate_curated_fields(path: Path, text: str) -> list[str]:
    failures = []
    record = parse_simple_yaml(text)
    category = record.get("category")
    if category is not None and category not in CATEGORIES:
        failures.append(f"{path}: category must be one of {sorted(CATEGORIES)}")
    tags = record.get("tags")
    if tags is not None:
        if not isinstance(tags, list) or "cli" not in tags:
            failures.append(f"{path}: tags must include cli")
        elif tags != sorted(tags) or any(not is_slug(tag) for tag in tags):
            failures.append(f"{path}: tags must be sorted slug strings")
    docs = record.get("docs")
    if docs is not None:
        if not isinstance(docs, list) or any(not str(url).startswith(("http://", "https://")) for url in docs):
            failures.append(f"{path}: docs URLs must be HTTP(S)")
    return failures


def is_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value))


def parse_simple_yaml(text: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    current_list: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.startswith(" " * 4):
            continue
        if line.startswith("  - ") and current_list:
            record.setdefault(current_list, []).append(line[4:].strip().strip("'\""))
            continue
        current_list = None
        if line.startswith(" ") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if not value:
            if key in {"docs", "tags"}:
                record[key] = []
                current_list = key
            continue
        record[key] = value.strip("'\"")
    return record


def read_formula_cache() -> list[dict[str, Any]]:
    payload = read_json(Path("cache/brew/formulae.json"))
    formulae = payload.get("formulae") if isinstance(payload, dict) else None
    if not isinstance(formulae, list):
        raise ValueError("cache/brew/formulae.json is missing formulae")
    return [item for item in formulae if isinstance(item, dict)]


def read_manager_cache() -> dict[str, Any]:
    payload = read_json(Path("cache/pkg-manager-indexes.json.gz"))
    if not isinstance(payload, dict):
        raise ValueError("cache/pkg-manager-indexes.json.gz is not an object")
    return payload
