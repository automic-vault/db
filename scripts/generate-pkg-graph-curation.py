#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from avdb_paths import DB_JSON_PATH, ISOTOPES_JSON_PATH
from pkg_hub_data import graph_hub_definitions, load_pkg_taxonomy_index, taxonomy_for_package, taxonomy_terms


SCHEMA_VERSION = 1
GENERATED_DATA_DIR = Path("cache")
OUTPUT_PATH = GENERATED_DATA_DIR / "pkg-graph-curation.json"
HUB_DEFINITIONS = graph_hub_definitions()
CONTROLLED_RELS = {
    "alternative",
    "adjacent_workflow",
    "similar_tool",
    "format_peer",
    "language_runtime_peer",
    "command_surface_peer",
    "security_surface_peer",
    "domain_peer",
}
STATIC_HUB_SLUGS = set(HUB_DEFINITIONS)
STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "application",
    "by",
    "cli",
    "command",
    "for",
    "from",
    "in",
    "library",
    "mac",
    "macos",
    "of",
    "on",
    "package",
    "plugin",
    "program",
    "software",
    "the",
    "to",
    "tool",
    "tools",
    "utility",
    "with",
}

CURATED_HUBS = HUB_DEFINITIONS


class Terminal:
    def __init__(self, json_mode: bool = False):
        self.json_mode = json_mode

    def log(self, message: str) -> None:
        if not self.json_mode:
            print(message, file=sys.stderr)

    def ok(self, message: str) -> None:
        self.log(f"OK {message}")

    def error(self, message: str) -> None:
        self.log(f"ERROR {message}")


def ensure_cwd() -> Path:
    scripts_dir = Path(__file__).resolve().parent
    root = scripts_dir.parent
    os.chdir(root)
    return root


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def load_script(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_files() -> list[Path]:
    files = [
        GENERATED_DATA_DIR / "pkg-page-enrichment.json",
        Path("data/pkg-hubs.json"),
        Path("data/pkg-taxonomy.json"),
        DB_JSON_PATH,
        ISOTOPES_JSON_PATH,
        Path("data/npm.json"),
        Path("data/pip.json"),
        Path("scripts/pkg_hub_data.py"),
        Path("scripts/generate-pkg-graph-curation.py"),
    ]
    if Path("agents").exists():
        files.extend(path for path in Path("agents").glob("*.yml") if path.is_file())
    for root in (Path("data/approval-gates"), Path("data/pkg-pages")):
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file() and path.name != ".DS_Store")
    return sorted(path for path in files if path.exists())


def input_hash(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def strip_current_curation(graph: dict[str, Any], curation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return {}
    current_packages = curation.get("packages") if isinstance(curation, dict) else {}
    curated_hubs = set((curation.get("hubs") or {}).keys()) if isinstance(curation, dict) else set()
    result = json.loads(json.dumps(graph))
    packages = result.get("packages") or {}
    if not isinstance(packages, dict):
        return result
    for package_key, entry in list(packages.items()):
        intents = entry.get("linkIntents") if isinstance(entry, dict) else None
        if not isinstance(intents, dict):
            continue
        curated = (current_packages or {}).get(package_key) if isinstance(current_packages, dict) else None
        curated_related = {
            (item.get("provider"), item.get("name"), item.get("rel"))
            for item in (((curated or {}).get("linkIntents") or {}).get("relatedPackages") or [])
            if isinstance(item, dict)
        }
        curated_also = {
            (item.get("provider"), item.get("name"), item.get("rel"))
            for item in (((curated or {}).get("linkIntents") or {}).get("alsoAvailableVia") or [])
            if isinstance(item, dict)
        }
        intents["relatedPackages"] = [
            item for item in intents.get("relatedPackages") or []
            if not isinstance(item, dict)
            or item.get("rel") not in CONTROLLED_RELS
            or (item.get("provider"), item.get("name"), item.get("rel")) not in curated_related
        ]
        intents["alsoAvailableVia"] = [
            item for item in intents.get("alsoAvailableVia") or []
            if not isinstance(item, dict)
            or item.get("rel") not in CONTROLLED_RELS
            or (item.get("provider"), item.get("name"), item.get("rel")) not in curated_also
        ]
        intents["packageHubs"] = [
            item for item in intents.get("packageHubs") or []
            if not isinstance(item, dict) or item.get("slug") not in curated_hubs
        ]
    return result


def load_base_pages(existing_curation: dict[str, Any] | None = None) -> tuple[Any, dict[str, Any]]:
    pages_module = load_script("generate_pkg_pages_for_curation", "generate-pkg-pages.py")
    sources = pages_module.load_sources()
    graph = sources.get("pkg_graph") or {}
    if existing_curation:
        sources["pkg_graph"] = strip_current_curation(graph, existing_curation)
    pages = pages_module.package_pages_from_sources(sources)
    return pages_module, pages


def has_internal_navigation(pages_module: Any, page: Any) -> bool:
    return bool(pages_module.has_internal_package_navigation(page))


def isolated_pages(pages_module: Any, pages: dict[str, Any]) -> list[Any]:
    return sorted(
        [
            page
            for page in pages.values()
            if pages_module.is_indexable_package_page(page) and not has_internal_navigation(pages_module, page)
        ],
        key=lambda page: (page.provider, page.slug, page.name),
    )


def normalized(value: str) -> str:
    value = value.lower().strip().removeprefix("@")
    value = re.sub(r"[@_/+.]+", "-", value)
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-")


def tokenize(value: Any) -> set[str]:
    tokens = set()
    for raw in re.findall(r"[a-zA-Z][a-zA-Z0-9@.+_-]{1,}", str(value or "").lower()):
        token = normalized(raw)
        if len(token) < 2 or token in STOPWORDS:
            continue
        tokens.add(token)
        for part in token.split("-"):
            if len(part) >= 3 and part not in STOPWORDS:
                tokens.add(part)
    return tokens


def host(value: str) -> str:
    match = re.match(r"https?://([^/]+)", value or "")
    return match.group(1).lower().removeprefix("www.") if match else ""


def executable_names(page: Any) -> list[str]:
    names = set(str(alias) for alias in page.aliases)
    for item in page.executables:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("target") or item.get("source") or "").strip()
            if name:
                names.add(name)
    for item in page.binaries:
        if isinstance(item, dict):
            name = str(item.get("target") or item.get("source") or "").strip()
            if name:
                names.add(name)
    return sorted(names)


def page_facts(page: Any) -> dict[str, Any]:
    taxonomy_index = load_pkg_taxonomy_index()
    taxonomy = taxonomy_for_package(taxonomy_index, page.provider, page.name)
    return {
        "key": page.key,
        "provider": page.provider,
        "name": page.name,
        "summary": page.summary,
        "homepage": page.homepage,
        "repository": page.repository,
        "packageManagerUrl": page.package_manager_url,
        "version": page.version,
        "license": page.license,
        "executables": executable_names(page)[:16],
        "aliases": sorted(page.aliases)[:16],
        "dependencies": list(page.dependencies)[:24],
        "buildDependencies": list(page.build_dependencies)[:24],
        "keywords": list(page.keywords)[:24],
        "classifiers": list(page.classifiers)[:12],
        "geigerLevel": (page.geiger or {}).get("level") if page.geiger else "",
        "taxonomyCategory": taxonomy.get("category") or "",
        "taxonomyCategoryPath": list(taxonomy.get("categoryPath") or [])[:8],
        "taxonomyTags": list(taxonomy.get("tags") or [])[:24],
        "taxonomyConfidence": taxonomy.get("categoryConfidence") or "",
    }


def fact_tokens(facts: dict[str, Any]) -> set[str]:
    pieces = [
        facts.get("name"),
        facts.get("summary"),
        " ".join(facts.get("executables") or []),
        " ".join(facts.get("aliases") or []),
        " ".join(facts.get("keywords") or []),
        " ".join(facts.get("classifiers") or []),
        " ".join(facts.get("dependencies") or []),
        facts.get("taxonomyCategory"),
        " ".join(facts.get("taxonomyCategoryPath") or []),
        " ".join(facts.get("taxonomyTags") or []),
    ]
    return set().union(*(tokenize(piece) for piece in pieces))


def relation_for(target: dict[str, Any], candidate: dict[str, Any], shared: set[str]) -> str:
    if target["provider"] != candidate["provider"] and normalized(target["name"]) == normalized(candidate["name"]):
        return "alternative"
    if set(target.get("executables") or []) & set(candidate.get("executables") or []):
        return "command_surface_peer"
    language_terms = {"python", "ruby", "node", "javascript", "java", "go", "rust", "php", "perl", "lua", "haskell", "erlang"}
    if shared & language_terms:
        return "language_runtime_peer"
    format_terms = {"json", "yaml", "xml", "markdown", "pdf", "html", "csv", "svg", "image", "audio", "video"}
    if shared & format_terms:
        return "format_peer"
    security_terms = {"security", "crypto", "password", "secret", "certificate", "tls", "ssh", "encrypt", "decrypt"}
    if shared & security_terms or (target.get("geigerLevel") and candidate.get("geigerLevel")):
        return "security_surface_peer"
    if target["provider"] == candidate["provider"] and (normalized(target["name"]).split("-")[0] == normalized(candidate["name"]).split("-")[0]):
        return "similar_tool"
    workflow_terms = {"build", "test", "deploy", "server", "client", "database", "network", "package", "publish", "documentation"}
    if shared & workflow_terms:
        return "adjacent_workflow"
    return "domain_peer"


def relation_reason(rel: str, candidate: dict[str, Any], shared: set[str]) -> str:
    labels = {
        "alternative": "Same normalized package name appears in another local ecosystem.",
        "adjacent_workflow": "Local metadata places this package in an adjacent workflow.",
        "similar_tool": "Package names and metadata indicate a similar tool family.",
        "format_peer": "Both packages work with overlapping file formats or content types.",
        "language_runtime_peer": "Both packages touch the same language runtime or ecosystem.",
        "command_surface_peer": "Executable or command metadata overlaps with this package.",
        "security_surface_peer": "Security-sensitive metadata or terminology overlaps.",
        "domain_peer": "Local package facts share a topical domain.",
    }
    reason = labels.get(rel, "Local package facts share a topical domain.")
    if shared:
        reason += f" Shared terms: {', '.join(sorted(shared)[:5])}."
    return reason


def score_candidate(target: dict[str, Any], candidate: dict[str, Any], target_tokens: set[str], candidate_tokens: set[str]) -> tuple[float, set[str]]:
    if target["key"] == candidate["key"]:
        return 0.0, set()
    shared = target_tokens & candidate_tokens
    score = float(len(shared))
    target_name = normalized(target["name"])
    candidate_name = normalized(candidate["name"])
    if target_name == candidate_name and target["provider"] != candidate["provider"]:
        score += 12
    if target_name and candidate_name and target_name.split("-")[0] == candidate_name.split("-")[0]:
        score += 4
    if target["provider"] == candidate["provider"]:
        score += 0.5
    if host(target.get("homepage") or "") and host(target.get("homepage") or "") == host(candidate.get("homepage") or ""):
        score += 5
    if host(target.get("repository") or "") and host(target.get("repository") or "") == host(candidate.get("repository") or ""):
        score += 6
    if set(target.get("executables") or []) & set(candidate.get("executables") or []):
        score += 6
    if set(target.get("dependencies") or []) & set(candidate.get("dependencies") or []):
        score += 1.5
    if target.get("taxonomyCategory") and target.get("taxonomyCategory") == candidate.get("taxonomyCategory"):
        score += 5
    shared_paths = set(target.get("taxonomyCategoryPath") or []) & set(candidate.get("taxonomyCategoryPath") or [])
    shared_tags = set(target.get("taxonomyTags") or []) & set(candidate.get("taxonomyTags") or [])
    score += 2 * len(shared_paths)
    score += min(5, len(shared_tags))
    shared.update(shared_paths)
    shared.update(shared_tags)
    return score, shared


def candidate_packets(pages: dict[str, Any], target_keys: set[str], limit: int = 18) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    facts = {key: page_facts(page) for key, page in pages.items()}
    tokens = {key: fact_tokens(value) for key, value in facts.items()}
    token_index: dict[str, list[str]] = defaultdict(list)
    name_index: dict[str, list[str]] = defaultdict(list)
    for key, values in tokens.items():
        for token in values:
            token_index[token].append(key)
        name_index[normalized(facts[key]["name"])].append(key)
    by_key: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(target_keys):
        target = facts[key]
        scored: list[tuple[float, str, set[str]]] = []
        target_tokens = tokens[key]
        candidate_keys = set(name_index.get(normalized(target["name"]), []))
        for token in target_tokens:
            matches = token_index.get(token) or []
            if len(matches) <= 1200:
                candidate_keys.update(matches)
        if len(candidate_keys) > 900:
            candidate_keys = set(sorted(
                candidate_keys,
                key=lambda candidate_key: (
                    -len(target_tokens & tokens[candidate_key]),
                    candidate_key,
                ),
            )[:900])
        for candidate_key in sorted(candidate_keys):
            candidate = facts[candidate_key]
            score, shared = score_candidate(target, candidate, target_tokens, tokens[candidate_key])
            if score <= 0:
                continue
            scored.append((score, candidate_key, shared))
        scored.sort(key=lambda item: (-item[0], item[1]))
        by_key[key] = [
            {
                "key": candidate_key,
                "provider": facts[candidate_key]["provider"],
                "name": facts[candidate_key]["name"],
                "summary": facts[candidate_key]["summary"],
                "executables": facts[candidate_key].get("executables") or [],
                "score": round(score, 3),
                "sharedTerms": sorted(shared)[:12],
            }
            for score, candidate_key, shared in scored[:limit]
        ]
    return facts, by_key


def choose_hub(facts: dict[str, Any], tokens: set[str]) -> dict[str, Any]:
    provider = facts["provider"]
    best_slug = ""
    best_score = -1.0
    category = str(facts.get("taxonomyCategory") or "")
    category_path = set(facts.get("taxonomyCategoryPath") or [])
    tags = set(facts.get("taxonomyTags") or [])
    for slug, hub in CURATED_HUBS.items():
        providers = set(hub.get("providers") or ())
        if providers and provider not in providers:
            continue
        terms = set().union(*(tokenize(term) for term in hub.get("terms") or ()))
        score = float(len(tokens & terms))
        if category and category in set(hub.get("categories") or ()):
            score += 8
        score += 3 * len(category_path & set(hub.get("categoryPaths") or ()))
        score += 2 * len(tags & set(hub.get("tags") or ()))
        if hub.get("riskHub") and facts.get("geigerLevel"):
            score += 3
        if score > best_score:
            best_score = score
            best_slug = slug
    if best_score <= 0:
        best_slug = {
            "brew": "brew-utility-packages",
            "cask": "desktop-app-packages",
            "npm": "npm-cli-packages",
            "pip": "python-cli-packages",
        }.get(provider, "terminal-utilities")
    hub = CURATED_HUBS[best_slug]
    return {
        "slug": best_slug,
        "label": hub["label"],
        "reason": "Matched curated package taxonomy and local package facts.",
        "kicker": hub["kicker"],
        "description": hub["description"],
    }


def local_curate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    target = packet["target"]
    target_tokens = set(packet.get("targetTokens") or [])
    related = []
    for candidate in packet.get("candidates") or []:
        shared = set(candidate.get("sharedTerms") or [])
        rel = relation_for(target, candidate, shared)
        confidence = min(0.86, max(0.52, 0.48 + (float(candidate.get("score") or 0) / 28)))
        related.append({
            "provider": candidate["provider"],
            "name": candidate["name"],
            "label": candidate["name"],
            "rel": rel,
            "reason": relation_reason(rel, candidate, shared),
            "confidence": round(confidence, 2),
            "evidence": "bounded local candidate similarity",
        })
        if len(related) >= 3:
            break
    also = []
    for candidate in packet.get("candidates") or []:
        if candidate["provider"] != target["provider"] and normalized(candidate["name"]) == normalized(target["name"]):
            also.append({
                "provider": candidate["provider"],
                "name": candidate["name"],
                "label": candidate["name"],
                "rel": "alternative",
                "reason": "Same normalized package name appears in another local ecosystem.",
                "confidence": 0.78,
                "evidence": "normalized package name",
            })
    return {
        "relatedPackages": related,
        "alsoAvailableVia": also[:4],
        "packageHubs": [choose_hub(target, target_tokens)],
    }


def run_agent(agent_cmd: str, packet: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        shlex.split(agent_cmd),
        input=json.dumps(packet, sort_keys=True),
        capture_output=True,
        check=True,
        text=True,
    )
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise ValueError("agent command did not return a JSON object")
    return parsed


def comparable_entry(entry: dict[str, Any]) -> dict[str, Any]:
    result = dict(entry)
    result.pop("curated_at", None)
    return result


def build_entry(
    package_key: str,
    facts: dict[str, Any],
    candidates: list[dict[str, Any]],
    existing: dict[str, Any] | None,
    agent_cmd: str,
) -> dict[str, Any]:
    facts_hash = stable_hash(facts)
    candidate_hash = stable_hash(candidates)
    if (
        existing
        and existing.get("facts_hash") == facts_hash
        and existing.get("candidate_hash") == candidate_hash
        and isinstance((existing.get("linkIntents") or {}), dict)
    ):
        return existing
    packet = {
        "task": "Choose useful internal package graph links. Use only supplied candidates and controlled relation names.",
        "allowedRelations": sorted(CONTROLLED_RELS),
        "allowedHubs": sorted(CURATED_HUBS),
        "target": facts,
        "targetTokens": sorted(fact_tokens(facts)),
        "candidates": candidates,
    }
    intents = run_agent(agent_cmd, packet) if agent_cmd else local_curate_packet(packet)
    entry = {
        "target": package_key,
        "facts_hash": facts_hash,
        "candidate_hash": candidate_hash,
        "curated_at": utc_now(),
        "curator": "agent-cmd" if agent_cmd else "codex-local-curator",
        "linkIntents": {
            "relatedPackages": intents.get("relatedPackages") or [],
            "alsoAvailableVia": intents.get("alsoAvailableVia") or [],
            "packageHubs": intents.get("packageHubs") or [],
        },
    }
    return entry


def validate_entry(package_key: str, entry: dict[str, Any], page_keys: set[str], hub_slugs: set[str]) -> list[str]:
    failures: list[str] = []
    intents = entry.get("linkIntents")
    if not isinstance(intents, dict):
        return [f"{package_key}: missing linkIntents"]
    for section in ("relatedPackages", "alsoAvailableVia"):
        items = intents.get(section) or []
        if not isinstance(items, list):
            failures.append(f"{package_key}: {section} must be a list")
            continue
        for item in items:
            if not isinstance(item, dict):
                failures.append(f"{package_key}: {section} contains non-object")
                continue
            provider = str(item.get("provider") or "")
            name = str(item.get("name") or "")
            rel = str(item.get("rel") or "")
            target_key = f"{provider}:{name}"
            if rel not in CONTROLLED_RELS:
                failures.append(f"{package_key}: unknown relation {rel!r}")
            if target_key not in page_keys:
                failures.append(f"{package_key}: target does not exist locally: {target_key}")
            if target_key == package_key:
                failures.append(f"{package_key}: relation points at itself")
            if not str(item.get("reason") or "").strip():
                failures.append(f"{package_key}: relation to {target_key} is missing reason")
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                failures.append(f"{package_key}: relation to {target_key} has invalid confidence")
            else:
                if confidence < 0 or confidence > 1:
                    failures.append(f"{package_key}: relation to {target_key} has out-of-range confidence")
            if not str(item.get("evidence") or "").strip():
                failures.append(f"{package_key}: relation to {target_key} is missing evidence")
    hubs = intents.get("packageHubs") or []
    if not isinstance(hubs, list):
        failures.append(f"{package_key}: packageHubs must be a list")
    else:
        for hub in hubs:
            if not isinstance(hub, dict):
                failures.append(f"{package_key}: packageHubs contains non-object")
                continue
            slug = str(hub.get("slug") or "")
            if slug not in hub_slugs:
                failures.append(f"{package_key}: hub does not have a rendered definition: {slug}")
    return failures


def validate_curation(artifact: dict[str, Any], pages_module: Any, base_pages: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if artifact.get("schema") != SCHEMA_VERSION:
        failures.append(f"schema is {artifact.get('schema')!r}, expected {SCHEMA_VERSION}")
    packages = artifact.get("packages")
    hubs = artifact.get("hubs")
    if not isinstance(packages, dict):
        failures.append("packages must be an object")
        packages = {}
    if not isinstance(hubs, dict):
        failures.append("hubs must be an object")
        hubs = {}
    page_keys = set(base_pages.keys())
    hub_slugs = STATIC_HUB_SLUGS | set(hubs.keys())
    for slug, hub in hubs.items():
        if not isinstance(hub, dict):
            failures.append(f"hub {slug}: definition must be an object")
            continue
        if not hub.get("label") or not hub.get("description"):
            failures.append(f"hub {slug}: missing label or description")
    for package_key, entry in packages.items():
        if package_key not in page_keys:
            failures.append(f"{package_key}: package does not exist locally")
            continue
        if not isinstance(entry, dict):
            failures.append(f"{package_key}: curation entry must be an object")
            continue
        failures.extend(validate_entry(package_key, entry, page_keys, hub_slugs))
    missing = []
    for page in isolated_pages(pages_module, base_pages):
        entry = packages.get(page.key)
        intents = (entry or {}).get("linkIntents") if isinstance(entry, dict) else {}
        if not isinstance(intents, dict) or not (
            intents.get("relatedPackages") or intents.get("alsoAvailableVia") or intents.get("packageHubs")
        ):
            missing.append(page.key)
    if missing:
        preview = ", ".join(missing[:20])
        failures.append(f"{len(missing):,} indexable isolated pages still lack curation: {preview}")
    return failures


def build_curation(agent_cmd: str = "", existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = existing or read_json(OUTPUT_PATH, {})
    pages_module, pages = load_base_pages(existing)
    isolated = isolated_pages(pages_module, pages)
    facts_by_key, candidates_by_key = candidate_packets(pages, {page.key for page in isolated})
    existing_packages = existing.get("packages") if isinstance(existing, dict) else {}
    packages: dict[str, Any] = {}
    for page in isolated:
        package_key = page.key
        packages[package_key] = build_entry(
            package_key,
            facts_by_key[package_key],
            candidates_by_key.get(package_key, []),
            (existing_packages or {}).get(package_key) if isinstance(existing_packages, dict) else None,
            agent_cmd,
        )
    hub_counts = Counter()
    for entry in packages.values():
        for hub in ((entry.get("linkIntents") or {}).get("packageHubs") or []):
            if isinstance(hub, dict) and hub.get("slug"):
                hub_counts[str(hub["slug"])] += 1
    hubs = {
        slug: {
            "label": hub["label"],
            "kicker": hub["kicker"],
            "description": hub["description"],
            "reason": "Agent-curated topical fallback for packages without deterministic package graph links.",
            "packageCount": hub_counts.get(slug, 0),
        }
        for slug, hub in CURATED_HUBS.items()
        if hub_counts.get(slug, 0)
    }
    files = source_files()
    artifact = {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "description": "Agent-curated package graph links for indexable package pages that lack deterministic internal navigation.",
        "input_hash": input_hash(files),
        "input_files": [path.as_posix() for path in files],
        "relation_definitions": {rel: rel.replace("_", " ") for rel in sorted(CONTROLLED_RELS)},
        "hubs": hubs,
        "packages": packages,
        "coverage": {
            "isolatedIndexablePackageCount": len(isolated),
            "curatedPackageCount": len(packages),
            "providerCounts": dict(sorted(Counter(page.provider for page in isolated).items())),
        },
    }
    failures = validate_curation(artifact, pages_module, pages)
    if failures:
        raise ValueError("; ".join(failures[:8]))
    return artifact


def comparable_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(artifact))
    result.pop("generated_at", None)
    for entry in (result.get("packages") or {}).values():
        if isinstance(entry, dict):
            entry.pop("curated_at", None)
    return result


def check_current(path: Path, terminal: Terminal) -> int:
    if not path.exists():
        terminal.error(f"Missing {path}. Run scripts/generate-pkg-graph-curation.py.")
        return 1
    try:
        current = read_json(path)
        pages_module, pages = load_base_pages(current)
        failures = validate_curation(current, pages_module, pages)
        expected = build_curation(existing=current)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as err:
        terminal.error(f"Unable to validate {path}: {err}")
        return 1
    if comparable_artifact(current) != comparable_artifact(expected):
        failures.append("curation artifact does not match current isolated package set or candidate fingerprints")
    if failures:
        terminal.error("Package graph curation is stale.")
        for failure in failures[:24]:
            terminal.log(f"  - {failure}")
        terminal.log("Run scripts/generate-pkg-graph-curation.py, regenerate the package graph, and rebuild the package-origin SQLite artifact.")
        return 1
    coverage = current.get("coverage") or {}
    terminal.ok(
        f"Package graph curation is current "
        f"({coverage.get('curatedPackageCount') or len(current.get('packages') or {}):,} curated packages)"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate agent-curated package graph links for isolated package pages.")
    parser.add_argument("--check", action="store_true", help="Validate the curation artifact without calling the agent.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Path to write or validate. Defaults to {OUTPUT_PATH}.")
    parser.add_argument("--agent-cmd", default=os.environ.get("PKG_GRAPH_CURATOR_CMD", ""), help="Optional command that reads a JSON packet on stdin and returns JSON link intents.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_cwd()
    terminal = Terminal(json_mode=args.json)
    output_path = Path(args.output)
    if args.check:
        return check_current(output_path, terminal)
    try:
        existing = read_json(output_path, {})
        artifact = build_curation(agent_cmd=args.agent_cmd, existing=existing)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as err:
        terminal.error(f"Failed to build package graph curation: {err}")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    count = len(artifact.get("packages") or {})
    terminal.ok(f"Wrote {count:,} curated package graph entries to {output_path}")
    if args.json:
        print(json.dumps({"ok": True, "output": str(output_path), "package_count": count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
