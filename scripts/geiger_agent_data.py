from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BOOTSTRAP_DIR = SCRIPT_DIR / "bootstrap"
if str(BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(BOOTSTRAP_DIR))

from lib.render import parse_simple_yaml  # noqa: E402


AGENTS_DIR = SCRIPT_DIR.parent / "agents"


def clean_string(value: Any) -> str:
    return str(value or "").strip()


def clean_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for text in (clean_string(item) for item in value) if text]


def normalize_agent_geiger(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    level = clean_string(value.get("color") or value.get("level"))
    reasons = clean_string_list(value.get("reasons"))
    primary_reason = clean_string(value.get("reason"))
    if primary_reason and primary_reason not in reasons:
        reasons = [primary_reason, *reasons]
    if not level or not reasons:
        return None

    record: dict[str, Any] = {
        "level": level,
        "reasons": reasons,
    }
    if category := clean_string(value.get("category")):
        record["category"] = category
    if confidence := clean_string(value.get("confidence")):
        record["confidence"] = confidence
    signals = clean_string_list(value.get("signals"))
    if signals:
        record["signals"] = signals
    return record


def load_agent_geiger_packages(agents_dir: Path = AGENTS_DIR) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for path in sorted(agents_dir.glob("*.yml")):
        record = parse_simple_yaml(path.read_text(encoding="utf-8"))
        package_id = clean_string(record.get("id") or f"brew:{path.stem}")
        if not package_id.startswith("brew:"):
            continue
        geiger = normalize_agent_geiger(record.get("geiger"))
        if geiger:
            packages[package_id.removeprefix("brew:")] = geiger
    return packages


def load_agent_geiger_data(agents_dir: Path = AGENTS_DIR) -> dict[str, Any]:
    return {
        "schema": 1,
        "source": {
            "kind": "agents-yaml",
            "path": str(agents_dir),
        },
        "packages": load_agent_geiger_packages(agents_dir),
    }
