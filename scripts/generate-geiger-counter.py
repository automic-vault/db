#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request

from avdb_paths import DB_JSON_PATH


FORMULA_URL = "https://formulae.brew.sh/api/formula.json"
DB_PATH = os.fspath(DB_JSON_PATH)
OUTPUT_PATH = os.path.join("data", "geiger-counter.json")
CACHE_DIR = os.path.join("cache", "brew.sh")
SCHEMA_VERSION = 1
USER_AGENT = "automic-vault-geiger-counter/0.1"

LEVELS = {
    "green": {
        "rank": 0,
        "category": "appliance",
    },
    "blue": {
        "rank": 1,
        "category": "tool",
    },
    "yellow": {
        "rank": 2,
        "category": "runtime",
    },
    "orange": {
        "rank": 3,
        "category": "infrastructure",
    },
    "red": {
        "rank": 4,
        "category": "escape-surveillance-offensive",
    },
}

OVERRIDES = {
    "jq": ("green", "high", "doc example: narrow deterministic data processor"),
    "ripgrep": ("green", "high", "doc example: narrow deterministic search appliance"),
    "fd": ("green", "high", "doc example: narrow deterministic file finder"),
    "sed": ("green", "high", "doc example: deterministic text transformer"),
    "tree": ("green", "high", "doc example: narrow filesystem listing tool"),
    "pngquant": ("green", "high", "doc example: narrow image optimizer"),
    "ffmpeg": ("blue", "high", "doc example: broad media transform and device-capable tool"),
    "git": ("blue", "high", "doc example: broad file and network tool"),
    "curl": ("blue", "high", "doc example: network transfer tool"),
    "rsync": ("blue", "high", "doc example: file synchronization tool"),
    "sqlite": ("blue", "high", "doc example: bounded database tool"),
    "imagemagick": ("blue", "high", "doc example: broad image transform tool"),
    "node": ("yellow", "high", "doc example: JavaScript runtime and package ecosystem"),
    "python": ("yellow", "high", "doc example: interpreter runtime"),
    "bash": ("yellow", "high", "doc example: shell runtime"),
    "zsh": ("yellow", "high", "doc example: shell runtime"),
    "ruby": ("yellow", "high", "doc example: interpreter runtime"),
    "lua": ("yellow", "high", "doc example: interpreter runtime"),
    "bun": ("yellow", "high", "doc example: JavaScript runtime and package ecosystem"),
    "deno": ("yellow", "high", "doc example: JavaScript runtime"),
    "docker": ("orange", "high", "doc example: container infrastructure"),
    "homebrew": ("orange", "high", "doc example: package manager infrastructure"),
    "npm": ("orange", "high", "doc example: package manager and supply-chain mutator"),
    "pip": ("orange", "high", "doc example: package manager and supply-chain mutator"),
    "cargo": ("orange", "high", "doc example: package manager and build ecosystem"),
    "gdb": ("red", "high", "doc example: debugger and process inspection tool"),
    "lldb": ("red", "high", "doc example: debugger and process inspection tool"),
    "tcpdump": ("red", "high", "doc example: packet capture tool"),
    "mitmproxy": ("red", "high", "doc example: credential interception capable proxy"),
    "metasploit": ("red", "high", "doc example: exploit tooling"),
    "aircrack-ng": ("red", "high", "doc example: offensive wireless tooling"),
}

EXACT_OVERRIDES = {
    "awscli": ("orange", "high", "cloud infrastructure mutation tool"),
    "azure-cli": ("orange", "high", "cloud infrastructure mutation tool"),
    "bettercap": ("red", "high", "network interception and offensive security tool"),
    "bpftrace": ("red", "high", "kernel tracing and introspection tool"),
    "cabal-install": ("orange", "high", "package manager and build ecosystem"),
    "cmake": ("yellow", "high", "build system capable of executing project logic"),
    "chef": ("orange", "high", "configuration management infrastructure"),
    "colima": ("orange", "high", "container and virtual machine infrastructure"),
    "coreutils": ("blue", "high", "broad file and shell utility collection"),
    "dtrace": ("red", "high", "system tracing and process inspection tool"),
    "frida": ("red", "high", "dynamic instrumentation and process injection tool"),
    "frida-tools": ("red", "high", "dynamic instrumentation and process injection tool"),
    "gh": ("blue", "high", "broad networked developer tool"),
    "ghc": ("yellow", "high", "compiler and runtime ecosystem"),
    "go": ("yellow", "high", "compiler and runtime ecosystem"),
    "gradle": ("yellow", "high", "build system capable of executing project logic"),
    "helm": ("orange", "high", "Kubernetes package manager"),
    "hydra": ("red", "high", "credential attack tooling"),
    "john": ("red", "high", "credential attack tooling"),
    "kubernetes-cli": ("orange", "high", "cluster orchestration infrastructure"),
    "lima": ("orange", "high", "virtual machine infrastructure"),
    "masscan": ("red", "high", "large-scale network scanning tool"),
    "minikube": ("orange", "high", "Kubernetes cluster infrastructure"),
    "mise": ("orange", "high", "runtime and toolchain version manager"),
    "maven": ("yellow", "high", "build system capable of executing project logic"),
    "meson": ("yellow", "high", "build system capable of executing project logic"),
    "ninja": ("yellow", "high", "build system capable of executing project logic"),
    "nmap": ("red", "medium", "network reconnaissance tool"),
    "opentofu": ("orange", "high", "infrastructure as code tool"),
    "packer": ("orange", "high", "machine image infrastructure tool"),
    "podman": ("orange", "high", "container infrastructure"),
    "pkgconf": ("green", "high", "narrow compiler metadata query tool"),
    "pulumi": ("orange", "high", "infrastructure as code tool"),
    "pyenv": ("orange", "high", "runtime version manager"),
    "radare2": ("red", "high", "reverse engineering and debugging tool"),
    "salt": ("orange", "high", "configuration management infrastructure"),
    "strace": ("red", "medium", "process tracing tool"),
    "terraform": ("orange", "high", "infrastructure as code tool"),
    "vault": ("orange", "high", "privileged secrets infrastructure"),
    "vagrant": ("orange", "high", "virtual machine infrastructure"),
    "wireshark": ("red", "high", "packet capture and network inspection tool"),
    "yarn": ("orange", "high", "package manager and supply-chain mutator"),
}

PATTERN_OVERRIDES = [
    (re.compile(r"^python@\d+(?:\.\d+)?$"), "yellow", "high", "versioned Python interpreter runtime"),
    (re.compile(r"^ruby@\d+(?:\.\d+)?$"), "yellow", "high", "versioned Ruby interpreter runtime"),
    (re.compile(r"^node(@\d+)?$"), "yellow", "high", "JavaScript runtime and package ecosystem"),
    (re.compile(r"^openjdk(@\d+)?$"), "yellow", "high", "Java runtime and development kit"),
    (re.compile(r"^llvm(@\d+)?$"), "yellow", "high", "compiler and toolchain runtime"),
    (re.compile(r"^gcc(@\d+)?$"), "yellow", "high", "compiler and toolchain runtime"),
    (re.compile(r"^terraform(@\d+)?$"), "orange", "high", "infrastructure as code tool"),
]

SIGNAL_RULES = [
    (
        "red",
        "medium",
        "escape, surveillance, or offensive capability signal",
        (
            "debugger",
            "packet capture",
            "sniffer",
            "exploit",
            "penetration",
            "password cracker",
            "credential interception",
            "credential theft",
            "secret extraction",
            "reverse engineering",
            "process injection",
            "kernel tracing",
            "man-in-the-middle",
            "mitm",
            "vulnerability scanner",
        ),
    ),
    (
        "orange",
        "medium",
        "infrastructure mutation or orchestration signal",
        (
            "package manager",
            "dependency manager",
            "container",
            "virtual machine",
            "virtualization",
            "kubernetes",
            "cluster",
            "orchestration",
            "infrastructure",
            "configuration management",
            "provision",
            "cloud",
            "daemon manager",
            "service manager",
            "version manager",
            "toolchain manager",
        ),
    ),
    (
        "yellow",
        "medium",
        "generalized runtime or code generation signal",
        (
            "interpreter",
            "runtime",
            "scripting language",
            "programming language",
            "jit",
            "just-in-time",
            "shell",
            "repl",
            "build system",
            "build tool",
            "plugin ecosystem",
        ),
    ),
    (
        "blue",
        "medium",
        "broad file, network, media, or database tool signal",
        (
            "download",
            "upload",
            "network",
            "http",
            "ftp",
            "ssh",
            "sync",
            "backup",
            "archive",
            "compress",
            "encrypt",
            "decrypt",
            "database",
            "sql",
            "video",
            "audio",
            "image",
            "media",
            "stream",
            "record",
            "version control",
            "client",
            "server",
        ),
    ),
]


def ensure_cwd():
    scripts_dir = os.path.abspath(os.path.dirname(__file__))
    root = os.path.dirname(scripts_dir)
    os.chdir(root)


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def cache_path_for(url):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.json")


def unwrap_cached_json(data):
    if isinstance(data, dict) and "__pkgdb_payload__" in data:
        return data["__pkgdb_payload__"]
    return data


def fetch_formula_api():
    path = cache_path_for(FORMULA_URL)
    if os.path.exists(path):
        return unwrap_cached_json(read_json(path))

    request = urllib.request.Request(
        FORMULA_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def formula_api_by_name():
    try:
        payload = fetch_formula_api()
    except Exception as err:
        print(f"warning: failed to load Homebrew formula API: {err}", file=sys.stderr)
        return {}
    if not isinstance(payload, list):
        print("warning: Homebrew formula API payload was not a list", file=sys.stderr)
        return {}
    return {
        item["name"]: item
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def executable_index(entries, formula_names):
    index = {name: [] for name in formula_names}
    if not isinstance(entries, dict):
        return index
    for executable, provider in entries.items():
        providers = provider if isinstance(provider, list) else [provider]
        for candidate in providers:
            if not isinstance(candidate, str):
                continue
            if ":" in candidate:
                continue
            if candidate in index:
                index[candidate].append(executable)
    for executables in index.values():
        executables.sort()
    return index


def text_fields(name, db_record, api_record, executables):
    fields = [name, " ".join(executables)]
    for record in (db_record, api_record):
        if not isinstance(record, dict):
            continue
        for key in ("summary", "desc"):
            value = record.get(key)
            if isinstance(value, str):
                fields.append(value)
        for key in ("aliases", "oldnames"):
            value = record.get(key)
            if isinstance(value, list):
                fields.extend(item for item in value if isinstance(item, str))
    return " ".join(fields).lower()


def add_signal(current, level, confidence, reason, signal):
    current["signals"].append(signal)
    current["reasons"].append(reason)
    if LEVELS[level]["rank"] > LEVELS[current["level"]]["rank"]:
        current["level"] = level
        current["confidence"] = confidence
    elif LEVELS[level]["rank"] == LEVELS[current["level"]]["rank"]:
        current["confidence"] = stronger_confidence(current["confidence"], confidence)


def stronger_confidence(left, right):
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order[left] >= order[right] else right


def override_for(name):
    if name in OVERRIDES:
        return OVERRIDES[name]
    if name in EXACT_OVERRIDES:
        return EXACT_OVERRIDES[name]
    for pattern, level, confidence, reason in PATTERN_OVERRIDES:
        if pattern.match(name):
            return level, confidence, reason
    return None


def classify_formula(name, db_record=None, api_record=None, executables=None):
    executables = executables or []
    result = {
        "level": "green",
        "confidence": "low",
        "reasons": [],
        "signals": [],
    }

    override = override_for(name)
    if override is not None:
        level, confidence, reason = override
        add_signal(result, level, confidence, reason, f"override:{name}")
        return finalized_result(result)

    haystack = text_fields(name, db_record or {}, api_record or {}, executables)
    library_like = "library" in haystack and (
        not executables
        or name.startswith("lib")
        or all(exe.endswith("-config") or exe.endswith("config") for exe in executables)
    )
    for level, confidence, reason, phrases in SIGNAL_RULES:
        if level == "blue" and not executables:
            continue
        if level == "yellow" and library_like:
            continue
        if level == "blue" and library_like:
            continue
        matches = [phrase for phrase in phrases if phrase in haystack]
        if matches:
            add_signal(
                result,
                level,
                confidence,
                reason,
                f"text:{','.join(matches[:5])}",
            )

    if isinstance(api_record, dict) and api_record.get("service") is not None:
        add_signal(
            result,
            "orange",
            "medium",
            "formula declares a Homebrew service",
            "metadata:service",
        )

    if not result["reasons"]:
        if not executables:
            result["reasons"].append("no executable entrypoint in the package index")
            result["signals"].append("metadata:no-indexed-executables")
        elif "library" in haystack or name.startswith(("lib", "py3-", "go-")):
            result["reasons"].append("library-like package without higher-risk signals")
            result["signals"].append("metadata:library-like")
        else:
            result["reasons"].append("narrow executable package without higher-risk signals")
            result["signals"].append("metadata:no-higher-risk-signals")

    return finalized_result(result)


def finalized_result(result):
    result["reasons"] = sorted(set(result["reasons"]))
    result["signals"] = sorted(set(result["signals"]))
    result["category"] = LEVELS[result["level"]]["category"]
    return {
        "level": result["level"],
        "category": result["category"],
        "confidence": result["confidence"],
        "reasons": result["reasons"],
        "signals": result["signals"],
    }


def build_payload():
    db = read_json(DB_PATH)
    formulas = db.get("formulas")
    if not isinstance(formulas, dict) or not formulas:
        raise ValueError(f"{DB_PATH} must contain a non-empty formulas object")

    formula_names = sorted(formulas.keys())
    api_records = formula_api_by_name()
    executables_by_formula = executable_index(db.get("entries"), formula_names)

    packages = {}
    for name in formula_names:
        packages[name] = classify_formula(
            name,
            formulas.get(name) or {},
            api_records.get(name) or {},
            executables_by_formula.get(name) or [],
        )

    payload = {
        "schema": SCHEMA_VERSION,
        "generated_at": db.get("generated_at"),
        "source": {
            "ecosystem": "brew",
            "scope": "homebrew/core formulas",
            "package_count": len(packages),
            "db_path": DB_PATH,
            "policy_path": os.path.join("docs", "geiger-counter.md"),
            "formula_api": FORMULA_URL,
        },
        "method": {
            "classifier": "deterministic-rules",
            "levels": {
                level: {"rank": spec["rank"], "category": spec["category"]}
                for level, spec in LEVELS.items()
            },
            "notes": [
                "Ratings estimate catastrophic potential if a package is compromised or misused.",
                "Integrity confidence is intentionally out of scope for this package-level data file.",
                "Taps, casks, npm packages, and pip packages are not included.",
            ],
        },
        "packages": packages,
    }
    validate_payload(payload, set(formula_names))
    return payload


def validate_payload(payload, expected_names):
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("payload packages must be an object")
    actual_names = set(packages.keys())
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)[:10]
        extra = sorted(actual_names - expected_names)[:10]
        raise ValueError(f"package key mismatch: missing={missing} extra={extra}")
    slash_names = sorted(name for name in actual_names if "/" in name)
    if slash_names:
        raise ValueError(f"tap-like package keys are not allowed: {slash_names[:10]}")
    for name, record in packages.items():
        level = record.get("level")
        if level not in LEVELS:
            raise ValueError(f"{name}: invalid level {level!r}")
        if record.get("category") != LEVELS[level]["category"]:
            raise ValueError(f"{name}: category does not match level")
        if record.get("confidence") not in ("low", "medium", "high"):
            raise ValueError(f"{name}: invalid confidence")
        if not isinstance(record.get("reasons"), list) or not record["reasons"]:
            raise ValueError(f"{name}: missing reasons")
        if not isinstance(record.get("signals"), list) or not record["signals"]:
            raise ValueError(f"{name}: missing signals")


def dumps_payload(payload):
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def run_self_checks(payload):
    packages = payload["packages"]
    expected_if_present = {
        "jq": "green",
        "ripgrep": "green",
        "ffmpeg": "blue",
        "git": "blue",
        "curl": "blue",
        "sqlite": "blue",
        "node": "yellow",
        "python@3.13": "yellow",
        "bash": "yellow",
        "docker": "orange",
        "gdb": "red",
        "tcpdump": "red",
    }
    fixture_expected = {
        "npm": "orange",
        "pip": "orange",
        "lldb": "red",
        "mitmproxy": "red",
    }

    failures = []
    for name, expected in expected_if_present.items():
        if name in packages and packages[name]["level"] != expected:
            failures.append(f"{name}: expected {expected}, got {packages[name]['level']}")

    for name, expected in fixture_expected.items():
        actual = classify_formula(name, {"summary": ""}, {"desc": ""}, [name])["level"]
        if actual != expected:
            failures.append(f"{name} fixture: expected {expected}, got {actual}")

    if failures:
        raise AssertionError("; ".join(failures))


def check_output(rendered):
    if not os.path.exists(OUTPUT_PATH):
        print(f"{OUTPUT_PATH} does not exist yet; skipped output comparison")
        return
    with open(OUTPUT_PATH, "r", encoding="utf-8") as handle:
        existing = handle.read()
    if existing != rendered:
        raise AssertionError(f"{OUTPUT_PATH} is stale; rerun scripts/generate-geiger-counter.py")


def write_output(rendered):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    print(f"Wrote {OUTPUT_PATH}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate deterministic Geiger Counter ratings for Homebrew core formulae."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate classifier examples and compare existing output without writing",
    )
    return parser.parse_args()


def main():
    ensure_cwd()
    args = parse_args()
    try:
        payload = build_payload()
        rendered = dumps_payload(payload)
        run_self_checks(payload)
        if args.check:
            check_output(rendered)
            print("geiger counter checks passed")
        else:
            write_output(rendered)
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as err:
        print(f"Failed to generate {OUTPUT_PATH}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
