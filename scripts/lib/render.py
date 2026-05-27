from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .brew import formula_record
from .common import PROJECTS_DIR, STAGE_DIR, read_json, reset_dir, write_text_if_changed
from .executables import read_executable_index
from .managers import manager_matcher, package_manager_routes
from .yaml_writer import yaml_text


def install_line_key(manager: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", manager.lower()).strip("-") or "package-manager"


def project_record(formula: dict[str, Any], executables: list[str], matcher: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    record = formula_record(formula)
    if record is None:
        return None
    name = str(formula.get("name") or "")
    if not executables:
        return None
    record["executables"] = [{"name": item, "kind": "cli", "exposure": "global executable"} for item in executables]
    package_managers: dict[str, str] = {"brew": name}
    package_manager_match: dict[str, str] = {}
    install_lines: dict[str, str] = {
        "av": f"sudo av install brew:{name}",
        "brew": f"brew install {name}",
    }
    for route in package_manager_routes(name, executables, matcher):
        key = install_line_key(str(route.get("manager_key") or route.get("manager") or ""))
        package_id = str(route.get("package_id") or "").strip()
        command = str(route.get("command") or "").strip()
        if not key or not package_id:
            continue
        if key not in package_managers:
            package_managers[key] = package_id
        if command and key not in install_lines:
            install_lines[key] = command
        if route.get("match_tier") == "fallback":
            package_manager_match[key] = "fallback"
    record["package-manager"] = package_managers
    if package_manager_match:
        record["package-manager-match"] = package_manager_match
    record["install-lines"] = install_lines
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
        if "install-lines:" not in text:
            failures.append(f"{path}: missing install-lines")
        if "executables:" not in text:
            failures.append(f"{path}: missing executables")
        if "\ngenerated-at:" in text:
            failures.append(f"{path}: published output must not include generated-at")
    return failures


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
