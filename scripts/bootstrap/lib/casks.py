from __future__ import annotations

import os
import sys
from typing import Any

from .common import CACHE_DIR, fetch_json, read_json, write_json


CASKS_URL = "https://formulae.brew.sh/api/cask.json"


def cask_url(token: str) -> str:
    return f"https://formulae.brew.sh/api/cask/{token}.json"


def fetch_cask_index(*, refresh: bool = False) -> list[dict[str, Any]]:
    payload = fetch_json(CASKS_URL, namespace="brew.sh", refresh=refresh)
    if not isinstance(payload, list):
        raise ValueError("Homebrew cask API payload must be a list")
    return [item for item in payload if isinstance(item, dict)]


def parse_binary_artifact(artifact: Any) -> dict[str, str | None] | None:
    if not isinstance(artifact, dict):
        return None
    if "binary" not in artifact or not set(artifact.keys()) <= {"binary", "target"}:
        return None

    value = artifact["binary"]
    target = None
    if isinstance(value, str):
        source = value
    elif isinstance(value, list) and value:
        source = value[0]
        if len(value) > 1 and isinstance(value[1], dict):
            target = value[1].get("target")
    else:
        return None

    if target is None:
        artifact_target = artifact.get("target")
        if isinstance(artifact_target, str) and artifact_target:
            target = os.path.basename(artifact_target)

    if not isinstance(source, str) or not source:
        return None
    if target is not None and (not isinstance(target, str) or not target):
        return None
    return {"source": source, "target": target}


def supported_cask_artifacts(artifacts: Any) -> list[dict[str, str | None]] | None:
    if not isinstance(artifacts, list):
        return None
    binaries = []
    for artifact in artifacts:
        parsed = parse_binary_artifact(artifact)
        if parsed is not None:
            binaries.append(parsed)
            continue
        if (
            isinstance(artifact, dict)
            and set(artifact.keys())
            <= {"generate_completions_from_executable", "zap", "uninstall"}
        ):
            continue
        return None
    return binaries if binaries else None


def cask_metadata(cask: dict[str, Any]) -> dict[str, Any] | None:
    token = cask.get("token")
    if not token or cask.get("disabled") or cask.get("deprecated"):
        return None

    binaries = supported_cask_artifacts(cask.get("artifacts") or [])
    if binaries is None:
        return None

    url = cask.get("url")
    sha256 = cask.get("sha256")
    version = cask.get("version")
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(sha256, str) or not sha256:
        return None
    if not isinstance(version, str) or not version:
        return None

    depends_on = cask.get("depends_on") or {}
    formula_dependencies = (depends_on.get("formula") if isinstance(depends_on, dict) else []) or []
    if not isinstance(formula_dependencies, list):
        return None
    if not all(isinstance(dep, str) and dep for dep in formula_dependencies):
        return None

    return {
        "summary": cask.get("desc") or "",
        "homepage": cask.get("homepage") or "",
        "aliases": cask.get("old_tokens") or [],
        "url": url,
        "sourceArchive": url,
        "sha256": sha256,
        "version": version,
        "dependencies": sorted(set(formula_dependencies)),
        "binaries": binaries,
    }


def fetch_supported_casks(index: list[dict[str, Any]], *, refresh: bool = False) -> list[dict[str, Any]]:
    result = []
    candidates = 0
    for entry in index:
        token = entry.get("token")
        if not isinstance(token, str) or not token:
            continue
        if cask_metadata(entry) is None:
            continue
        candidates += 1
        try:
            payload = fetch_json(cask_url(token), namespace="brew.sh", refresh=refresh)
        except Exception as err:
            print(f"Failed to fetch cask {token}: {err}", file=sys.stderr)
            continue
        if isinstance(payload, dict):
            result.append(payload)
    if candidates and not result:
        raise ValueError("No supported Homebrew cask metadata was fetched")
    return result


def collect_cask_entries(casks: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    entries: dict[str, str] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for cask in casks:
        token = cask.get("token")
        if not isinstance(token, str) or not token:
            continue
        supported = cask_metadata(cask)
        if supported is None:
            continue
        metadata[token] = supported
        for binary in supported["binaries"]:
            executable = binary.get("target") or os.path.basename(binary["source"])
            if executable:
                entries.setdefault(executable, f"cask:{token}")
    return dict(sorted(entries.items())), dict(sorted(metadata.items()))


def app_catalog_from_casks(casks: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for cask in casks:
        token = cask.get("token")
        artifacts = cask.get("artifacts")
        if (
            not isinstance(token, str)
            or not token
            or cask.get("disabled")
            or cask.get("deprecated")
            or not isinstance(artifacts, list)
        ):
            continue
        has_app = any(isinstance(artifact, dict) and "app" in artifact for artifact in artifacts)
        if not has_app:
            continue
        bundle_identifiers = set()
        for artifact in artifacts:
            uninstall = artifact.get("uninstall") if isinstance(artifact, dict) else None
            if not isinstance(uninstall, list):
                continue
            for rule in uninstall:
                quit_values = rule.get("quit") if isinstance(rule, dict) else None
                if isinstance(quit_values, str):
                    quit_values = [quit_values]
                if not isinstance(quit_values, list):
                    continue
                bundle_identifiers.update(
                    value
                    for value in quit_values
                    if isinstance(value, str)
                    and value.count(".") >= 2
                    and all(character.isalnum() or character in ".-" for character in value)
                )
        if len(bundle_identifiers) != 1:
            continue
        metadata = {
            key: value
            for key, value in {
                "summary": cask.get("desc"),
                "homepage": cask.get("homepage"),
                "version": cask.get("version"),
            }.items()
            if isinstance(value, str) and value
        }
        candidates.setdefault(bundle_identifiers.pop(), []).append((token, metadata))

    apps = {}
    app_casks = {}
    for bundle_identifier, matches in candidates.items():
        if len(matches) != 1:
            continue
        token, metadata = matches[0]
        apps[bundle_identifier] = {"cask": token, "version_source": "cask"}
        app_casks[token] = metadata
    return dict(sorted(apps.items())), dict(sorted(app_casks.items()))


def write_cask_cache(casks: list[dict[str, Any]], *, all_casks: list[dict[str, Any]] | None = None) -> None:
    entries, metadata = collect_cask_entries(casks)
    write_json(CACHE_DIR / "brew" / "casks.json", {
        "schema": 1,
        "source": CASKS_URL,
        "casks": all_casks if all_casks is not None else casks,
    })
    write_json(CACHE_DIR / "brew" / "cask-entries.json", {
        "schema": 1,
        "provider": "brew-cask",
        "entries": entries,
        "casks": metadata,
    })


def read_cask_authority() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    payload = read_json(CACHE_DIR / "brew" / "cask-entries.json", {})
    entries = payload.get("entries") if isinstance(payload, dict) else {}
    casks = payload.get("casks") if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        entries = {}
    if not isinstance(casks, dict):
        casks = {}
    return (
        {str(key): str(value) for key, value in entries.items() if isinstance(value, str)},
        {str(key): value for key, value in casks.items() if isinstance(value, dict)},
    )
