#!/usr/bin/env python3
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from avdb_paths import ISOTOPES_JSON_PATH
from geiger_agent_data import load_agent_geiger_data


OUTPUT_PATH = Path("data/security-recommendations.json")
SCHEMA_VERSION = 1
GEIGER_LEVEL_PRIORITIES = {
    "red": 20,
    "orange": 30,
}


def _ensure_cwd():
    scripts_dir = Path(__file__).resolve().parent
    os.chdir(scripts_dir.parent)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _string(value):
    if isinstance(value, str):
        value = value.strip()
        if value:
            return value
    return None


def _brew_key(formula):
    formula = _string(formula)
    if formula is None:
        return None
    if formula.startswith("brew:"):
        return formula
    return f"brew:{formula}"


def _unqualified_isotope_name(record_key, record):
    name = _string(record.get("name")) or record_key
    return name.removeprefix("isotope:")


def _ensure_package(packages, package_key):
    package = packages.setdefault(
        package_key,
        {
            "provider": "brew",
            "name": package_key.removeprefix("brew:"),
            "installPackageName": package_key,
            "priority": 999,
            "signals": [],
            "reasons": [],
        },
    )
    return package


def _append_unique(values, value):
    value = _string(value)
    if value is not None and value not in values:
        values.append(value)


def _add_signal(package, signal, priority, reason):
    _append_unique(package["signals"], signal)
    _append_unique(package["reasons"], reason)
    package["priority"] = min(package["priority"], priority)


def _versioned_formulae_for(base, geiger):
    result = []
    for name in geiger.get("packages", {}):
        if not isinstance(name, str) or not name.startswith(f"{base}@"):
            continue
        version = name[len(base) + 1 :]
        if version and version.isascii() and version.isdigit():
            result.append(name)
    return sorted(set(result))


def _manifest_scalar(path, key):
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"{key}:":
            for child in lines[index + 1 :]:
                value = child.strip()
                if value:
                    return value
            return None
        prefix = f"{key}:"
        if stripped.startswith(prefix):
            return _string(stripped[len(prefix) :])
    return None


def _versioned_radioisotope_bases(root):
    bases = set()
    for path in root.glob("*/automic-vault.yml"):
        if (_manifest_scalar(path, "appliesToVersionedFormulae") or "").lower() != "true":
            continue
        modifies = _manifest_scalar(path, "modifies")
        if modifies is not None and modifies.startswith("brew:"):
            bases.add(modifies.removeprefix("brew:"))
    return bases


def _isotope_reason(record):
    justification = record.get("justification")
    if isinstance(justification, dict):
        return _string(justification.get("title"))
    return None


def _apply_isotope_metadata(package, isotope, mode, reason):
    package["isotope"] = isotope
    package["isotopePackage"] = f"isotope:{isotope}"
    package["isotopeMode"] = mode
    _add_signal(
        package,
        "isotope",
        0,
        reason or "Automic Vault protected-tool coverage is available.",
    )


def _add_isotope_packages(packages, isotopes, geiger, versioned_radioisotope_bases):
    explicit_brew_targets = set()
    for record in isotopes.values():
        if not isinstance(record, dict):
            continue
        target = _string(record.get("modifies")) or _string(record.get("replaces"))
        if target is not None and target.startswith("brew:"):
            explicit_brew_targets.add(target)

    for record_key, record in isotopes.items():
        if not isinstance(record, dict):
            continue
        modifies = _string(record.get("modifies"))
        replaces = _string(record.get("replaces"))
        target = modifies or replaces
        if target is None or not target.startswith("brew:"):
            continue

        package = _ensure_package(packages, target)
        isotope = _unqualified_isotope_name(record_key, record)
        mode = "modifies" if modifies is not None else "replaces"
        reason = _isotope_reason(record)
        _apply_isotope_metadata(package, isotope, mode, reason)

        base = target.removeprefix("brew:")
        if (
            record.get("appliesToVersionedFormulae") is not True
            and base not in versioned_radioisotope_bases
        ):
            continue
        for formula in _versioned_formulae_for(base, geiger):
            versioned_target = f"brew:{formula}"
            if versioned_target in explicit_brew_targets:
                continue
            versioned_package = _ensure_package(packages, versioned_target)
            _apply_isotope_metadata(versioned_package, formula, mode, reason)


def _add_approval_gate_packages(packages, approval_root):
    for path in sorted(approval_root.glob("*.yaml")):
        package_key = _brew_key(path.stem)
        if package_key is None:
            continue
        package = _ensure_package(packages, package_key)
        package["approvalGate"] = True
        _add_signal(
            package,
            "approval_gate",
            10,
            "Approval gate metadata covers sensitive commands.",
        )


def _add_geiger_packages(packages, geiger):
    for name, record in geiger.get("packages", {}).items():
        if not isinstance(record, dict):
            continue
        level = _string(record.get("level"))
        priority = GEIGER_LEVEL_PRIORITIES.get(level or "")
        if priority is None:
            continue

        package_key = _brew_key(name)
        if package_key is None:
            continue
        package = _ensure_package(packages, package_key)
        package["geigerLevel"] = level
        if confidence := _string(record.get("confidence")):
            package["geigerConfidence"] = confidence
        if category := _string(record.get("category")):
            package["geigerCategory"] = category

        reasons = [
            reason
            for reason in record.get("reasons", [])
            if isinstance(reason, str) and reason.strip()
        ]
        reason = reasons[0] if reasons else f"Geiger {level} package risk classification."
        _add_signal(package, f"geiger:{level}", priority, reason)


def _expected():
    packages = {}
    geiger = load_agent_geiger_data()
    _add_isotope_packages(
        packages,
        _read_json(ISOTOPES_JSON_PATH),
        geiger,
        _versioned_radioisotope_bases(Path("data/radioisotopes")),
    )
    _add_approval_gate_packages(packages, Path("data/approval-gates/brew"))
    _add_geiger_packages(packages, geiger)

    for package in packages.values():
        package["signals"].sort()
        package["reasons"].sort()

    return {
        "schema": SCHEMA_VERSION,
        "packages": dict(sorted(packages.items())),
    }


def _load_output(path):
    if not path.exists():
        raise FileNotFoundError(path)
    return _read_json(path)


def _validate_document(document):
    if document.get("schema") != SCHEMA_VERSION:
        raise ValueError(
            f"schema {document.get('schema')!r}; expected {SCHEMA_VERSION}"
        )
    if not isinstance(document.get("packages"), dict) or not document["packages"]:
        raise ValueError("packages must be a non-empty object")
    for key, package in document["packages"].items():
        if not key.startswith("brew:"):
            raise ValueError(f"package key {key!r} is not brew-qualified")
        if package.get("installPackageName") != key:
            raise ValueError(f"package {key!r} has mismatched installPackageName")


def _validate_output(path):
    actual = _load_output(path)
    _validate_document(actual)
    expected = _expected()
    if actual.get("packages") != expected["packages"]:
        raise ValueError(
            f"{path} is stale; regenerate it with scripts/generate-security-recommendations.py"
        )
    if not actual.get("generated_at"):
        raise ValueError(f"{path} is missing generated_at")


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Homebrew package security recommendations."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that the output already matches local source data.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Path to write or validate. Defaults to {OUTPUT_PATH}.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    _ensure_cwd()

    if args.check:
        try:
            _validate_output(args.output)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as err:
            print(f"Invalid {args.output}: {err}", file=sys.stderr)
            return 1
        print(f"OK {args.output} is current")
        return 0

    try:
        document = _expected()
        document["generated_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        _validate_document(document)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as err:
        print(f"Failed to build {args.output}: {err}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
