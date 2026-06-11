#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from avdb_paths import DB_JSON_PATH, ISOTOPES_JSON_PATH
from pkg_hub_data import graph_hub_definitions, load_pkg_taxonomy_index, taxonomy_for_package, taxonomy_terms


SCHEMA_VERSION = 1
GENERATED_DATA_DIR = Path("cache")
PKG_PAGE_ENRICHMENT_PATH = GENERATED_DATA_DIR / "pkg-page-enrichment.json"
PKG_VERSION_FRESHNESS_PATH = GENERATED_DATA_DIR / "pkg-version-freshness.json"
OUTPUT_PATH = GENERATED_DATA_DIR / "pkg-graph.json"
CURATION_PATH = GENERATED_DATA_DIR / "pkg-graph-curation.json"
CROSS_ECOSYSTEM_PATH = GENERATED_DATA_DIR / "pkg-cross-ecosystem.json"
HUB_DEFINITIONS = graph_hub_definitions()

RELATION_DEFINITIONS = {
    "runtime_dependency": "Homebrew declares the target as a runtime dependency.",
    "build_dependency": "Homebrew declares the target as a build dependency.",
    "depended_on_by": "Another Homebrew package depends on this package.",
    "same_family": "Package names indicate a versioned or adjacent formula family.",
    "same_repository": "Packages resolve to the same upstream source repository.",
    "same_homepage": "Packages share the same homepage.",
    "same_software_cross_ecosystem": "A package with the same normalized name exists in another local ecosystem.",
    "hub_member": "Package belongs to a generated package hub.",
    "alternative": "Agent-curated package that can satisfy a similar need.",
    "adjacent_workflow": "Agent-curated package in an adjacent workflow.",
    "similar_tool": "Agent-curated package with similar tool semantics.",
    "format_peer": "Agent-curated package that works with overlapping file formats.",
    "language_runtime_peer": "Agent-curated package that shares a language runtime or ecosystem.",
    "command_surface_peer": "Agent-curated package with overlapping command surfaces.",
    "security_surface_peer": "Agent-curated package with related security-sensitive surfaces.",
    "domain_peer": "Agent-curated package in the same topical domain.",
}


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


def slugify(value: str) -> str:
    value = value.lower().strip()
    if value.startswith("@"):
        value = value[1:]
    value = value.replace("@", "-").replace("+", "plus").replace("/", "-")
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    return value.strip("-") or "package"


def normalize_name(value: str) -> str:
    value = value.lower().strip()
    value = value.removeprefix("@")
    value = re.sub(r"[@_/+.]+", "-", value)
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def family_key(name: str) -> str:
    value = normalize_name(name)
    value = re.sub(r"-?\d+(\-\d+)*$", "", value)
    value = re.sub(r"-v?\d+(\-\d+)*$", "", value)
    value = re.sub(r"-(cli|client|tool|tools|core|common|lib|libs)$", "", value)
    return value or normalize_name(name)


def repository_url(info: dict[str, Any]) -> str:
    for key in ("repository", "homepage", "sourceArchive"):
        value = info.get(key)
        if isinstance(value, str):
            match = re.search(r"https://github\.com/([^/\s]+)/([^/#?\s]+)", value)
            if match:
                owner = match.group(1)
                repo = re.sub(r"\.git$", "", match.group(2))
                return f"https://github.com/{owner}/{repo}"
    return ""


def source_host(value: str) -> str:
    match = re.match(r"https?://([^/]+)", value or "")
    return match.group(1).lower() if match else ""


def load_script(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def term_matches(haystack: str, term: str) -> bool:
    escaped = re.escape(term.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", haystack) is not None


def input_files() -> list[Path]:
    files = [
        PKG_PAGE_ENRICHMENT_PATH,
        CROSS_ECOSYSTEM_PATH,
        Path("data/pkg-hubs.json"),
        Path("data/pkg-taxonomy.json"),
        DB_JSON_PATH,
        Path("data/geiger-counter.json"),
        ISOTOPES_JSON_PATH,
        Path("data/npm.json"),
        Path("data/pip.json"),
        Path("scripts/generate-pkg-pages.py"),
        Path("scripts/generate-pkg-graph.py"),
        Path("scripts/pkg_hub_data.py"),
    ]
    if CURATION_PATH.exists():
        files.append(CURATION_PATH)
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


def provider_packages(db: dict[str, Any], pip: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {"brew": set(), "cask": set(), "npm": set(), "pip": set()}
    for provider, section in (("brew", "formulas"), ("cask", "casks"), ("npm", "npms")):
        values = db.get(section) or {}
        if isinstance(values, dict):
            result[provider].update(str(name) for name in values)
    if isinstance(pip, dict):
        result["pip"].update(str(name) for name in pip)
    return result


def approval_gate_packages() -> set[str]:
    result = set()
    root = Path("data/approval-gates/brew")
    if root.exists():
        result.update(path.stem for path in root.glob("*.yaml"))
    return result


def isotope_packages(isotopes: dict[str, Any]) -> set[str]:
    result = set()
    for isotope in isotopes.values() if isinstance(isotopes, dict) else []:
        if not isinstance(isotope, dict):
            continue
        modifies = isotope.get("modifies") or isotope.get("replaces")
        if isinstance(modifies, str) and modifies.startswith("brew:"):
            result.add(modifies.split(":", 1)[1])
    return result


def package_popularity(db: dict[str, Any], name: str) -> int:
    info = ((db.get("formulas") or {}).get(name) or {})
    popularity = info.get("popularity") if isinstance(info, dict) else {}
    if not isinstance(popularity, dict):
        return 999999
    try:
        return int(popularity.get("rank") or 999999)
    except (TypeError, ValueError):
        return 999999


def link_target(provider: str, name: str, reason: str, rel: str, confidence: float, evidence: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "name": name,
        "label": name,
        "reason": reason,
        "rel": rel,
        "confidence": confidence,
        "evidence": evidence,
    }


def append_unique(targets: list[dict[str, Any]], item: dict[str, Any], seen: set[tuple[str, str, str]]) -> None:
    key = (item.get("provider", ""), item.get("name", ""), item.get("rel", ""))
    if key in seen:
        return
    seen.add(key)
    targets.append(item)


def shared_taxonomy_terms(left: dict[str, Any], right: dict[str, Any]) -> set[str]:
    return taxonomy_terms(left) & taxonomy_terms(right)


def taxonomy_peer_candidates(
    package_key: str,
    taxonomy: dict[str, Any],
    taxonomy_by_key: dict[str, dict[str, Any]],
    db: dict[str, Any],
    candidate_keys: set[str],
    limit: int = 8,
) -> list[tuple[str, set[str], float]]:
    provider, name = package_key_parts(package_key)
    if provider != "brew" or not taxonomy:
        return []
    scored: list[tuple[float, int, str, set[str]]] = []
    category = str(taxonomy.get("category") or "")
    category_path = set(taxonomy.get("categoryPath") or [])
    tags = set(taxonomy.get("tags") or [])
    for other_key in sorted(candidate_keys):
        other = taxonomy_by_key.get(other_key) or {}
        other_provider, other_name = package_key_parts(other_key)
        if other_key == package_key or other_provider != "brew":
            continue
        score = 0.0
        shared = shared_taxonomy_terms(taxonomy, other)
        if category and category == str(other.get("category") or ""):
            score += 5
        score += 2 * len(category_path & set(other.get("categoryPath") or []))
        score += min(5, len(tags & set(other.get("tags") or [])))
        if score < 5:
            continue
        scored.append((score, package_popularity(db, other_name), other_name, shared))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(other_name, shared, score) for score, _rank, other_name, shared in scored[:limit]]


def taxonomy_peer_reason(shared: set[str]) -> str:
    if shared:
        return f"Shares av.db curated category or tags: {', '.join(sorted(shared)[:5])}."
    return "Shares av.db curated package taxonomy."


def hub_memberships(
    provider: str,
    name: str,
    info: dict[str, Any],
    geiger: dict[str, Any] | None,
    gated: bool,
    isotope: bool,
    taxonomy: dict[str, Any],
) -> list[dict[str, str]]:
    memberships = []
    names = {normalize_name(name)}
    for executable in info.get("executables") or []:
        if isinstance(executable, dict) and executable.get("name"):
            names.add(normalize_name(str(executable["name"])))
    taxonomy_values = taxonomy_terms(taxonomy)
    category = str(taxonomy.get("category") or "").strip()
    category_path = {str(item).strip() for item in taxonomy.get("categoryPath") or [] if str(item or "").strip()}
    tags = {str(item).strip() for item in taxonomy.get("tags") or [] if str(item or "").strip()}
    haystack = " ".join([
        *(str(info.get(key) or "") for key in ("summary", "homepage")),
        " ".join(sorted(taxonomy_values)),
    ]).lower()
    for slug, hub in HUB_DEFINITIONS.items():
        providers = set(hub.get("providers") or ())
        if providers and provider not in providers:
            continue
        matched = False
        if hub.get("riskHub"):
            level = str((geiger or {}).get("level") or "").lower()
            matched = isotope or gated or level not in {"", "green", "low", "unknown"}
        else:
            matched = (
                any(normalize_name(item) in names for item in hub.get("names") or ())
                or any(term_matches(haystack, term) for term in hub.get("terms") or ())
                or (category and category in set(hub.get("categories") or ()))
                or bool(category_path & set(hub.get("categoryPaths") or ()))
                or bool(tags & set(hub.get("tags") or ()))
            )
        if matched:
            memberships.append({"slug": slug, "label": str(hub["label"]), "reason": str(hub["reason"])})
    memberships.sort(key=lambda item: (int(HUB_DEFINITIONS.get(item["slug"], {}).get("priority") or 100), item["slug"]))
    return memberships[:4]


def curation_target_key(item: dict[str, Any]) -> str:
    return f"{item.get('provider') or ''}:{item.get('name') or ''}"


def package_key_parts(package_key: str) -> tuple[str, str]:
    if ":" not in package_key:
        return "", ""
    provider, name = package_key.split(":", 1)
    return provider, name


def db_section_for_provider(provider: str) -> str:
    return {"brew": "formulas", "cask": "casks", "npm": "npms"}.get(provider, "")


def package_keys_from_sources(
    enrichment_packages: dict[str, Any],
    db: dict[str, Any],
    pip: dict[str, Any],
    curation: dict[str, Any],
    cross_ecosystem: dict[str, Any] | None = None,
) -> set[str]:
    keys = set(enrichment_packages.keys())
    for provider, section in (("brew", "formulas"), ("cask", "casks"), ("npm", "npms")):
        values = db.get(section) or {}
        if isinstance(values, dict):
            keys.update(f"{provider}:{name}" for name in values)
    if isinstance(pip, dict):
        keys.update(f"pip:{name}" for name in pip)
    curation_packages = curation.get("packages") if isinstance(curation, dict) else None
    if isinstance(curation_packages, dict):
        keys.update(curation_packages.keys())
        for entry in curation_packages.values():
            intents = (entry or {}).get("linkIntents") if isinstance(entry, dict) else {}
            if not isinstance(intents, dict):
                continue
            for section in ("relatedPackages", "alsoAvailableVia"):
                for item in intents.get(section) or []:
                    if isinstance(item, dict) and item.get("provider") and item.get("name"):
                        keys.add(f"{item['provider']}:{item['name']}")
    cross_packages = cross_ecosystem.get("packages") if isinstance(cross_ecosystem, dict) else None
    if isinstance(cross_packages, dict):
        keys.update(cross_packages.keys())
        for entry in cross_packages.values():
            if not isinstance(entry, dict):
                continue
            for item in entry.get("localLinks") or []:
                if isinstance(item, dict) and item.get("provider") and item.get("name"):
                    keys.add(f"{item['provider']}:{item['name']}")
    return keys


def page_keys_from_filtered_pages(
    db: dict[str, Any],
    geiger_data: dict[str, Any],
    isotopes: dict[str, Any],
    npm: dict[str, Any],
    pip: dict[str, Any],
    enrichment: dict[str, Any],
    freshness: dict[str, Any],
) -> set[str]:
    pages_module = load_script("generate_pkg_pages_for_graph_scope", "generate-pkg-pages.py")
    pages = pages_module.package_pages_from_sources({
        "db": db,
        "geiger": geiger_data,
        "isotopes": isotopes,
        "npm": npm,
        "pip": pip,
        "pkg_graph": {},
        "pkg_cross_ecosystem": {},
        "pkg_page_enrichment": enrichment,
        "pkg_version_freshness": freshness,
    })
    return set(pages)


def package_info_for_key(package_key: str, enrichment_packages: dict[str, Any], db: dict[str, Any], geiger_packages: dict[str, Any]) -> dict[str, Any]:
    provider, name = package_key_parts(package_key)
    enrichment = enrichment_packages.get(package_key) if isinstance(enrichment_packages.get(package_key), dict) else {}
    db_info = {}
    section = db_section_for_provider(provider)
    if section:
        db_info = ((db.get(section) or {}).get(name) or {})
        if not isinstance(db_info, dict):
            db_info = {}
    package = enrichment.get("package") or {}
    repo = enrichment.get("repository") or repository_url(enrichment)
    homepage = enrichment.get("homepage") or db_info.get("homepage") or ""
    geiger = geiger_packages.get(name) if provider == "brew" and isinstance(geiger_packages.get(name), dict) else None
    return {
        "identity": {
            "provider": provider,
            "name": name,
            "summary": enrichment.get("summary") or db_info.get("summary") or "",
            "version": enrichment.get("version") or db_info.get("version") or "",
            "homepage": homepage,
            "repository": repo,
            "sourceHost": source_host(enrichment.get("sourceArchive") or homepage or ""),
            "packageManagerUrl": package.get("packageManagerUrl") or "",
        },
        "operationalContext": {
            "runtimeDependencyCount": len(enrichment.get("dependencies") or db_info.get("dependencies") or []),
            "buildDependencyCount": len(enrichment.get("buildDependencies") or []),
            "executableCount": len(enrichment.get("executables") or []),
            "bottleAvailable": bool((enrichment.get("bottle") or {}).get("available")),
            "postInstallDefined": (enrichment.get("installBehavior") or {}).get("postInstallDefined"),
            "serviceDeclared": bool((enrichment.get("installBehavior") or {}).get("service")),
            "geigerLevel": (geiger or {}).get("level") or "",
            "radioisotope": False,
            "approvalGate": False,
        },
    }


def empty_graph_entry(package_key: str, enrichment_packages: dict[str, Any], db: dict[str, Any], geiger_packages: dict[str, Any]) -> dict[str, Any]:
    info = package_info_for_key(package_key, enrichment_packages, db, geiger_packages)
    return {
        "identity": info["identity"],
        "operationalContext": info["operationalContext"],
        "linkIntents": {
            "relatedPackages": [],
            "alsoAvailableVia": [],
            "packageHubs": [],
        },
        "claims": [],
    }


def merge_unique_link(target: list[dict[str, Any]], item: dict[str, Any], page_keys: set[str], limit: int) -> bool:
    provider = str(item.get("provider") or "").strip()
    name = str(item.get("name") or "").strip()
    rel = str(item.get("rel") or "").strip()
    if not provider or not name or f"{provider}:{name}" not in page_keys:
        return False
    key = (provider, name, rel)
    seen = {(existing.get("provider"), existing.get("name"), existing.get("rel")) for existing in target if isinstance(existing, dict)}
    if key in seen or len(target) >= limit:
        return False
    target.append(dict(item))
    return True


def merge_unique_hub(target: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    slug = str(item.get("slug") or "").strip()
    if not slug:
        return False
    if slug in {str(existing.get("slug") or "") for existing in target if isinstance(existing, dict)}:
        return False
    target.append(dict(item))
    return True


def apply_curation(
    graph_packages: dict[str, Any],
    curation: dict[str, Any],
    page_keys: set[str],
    enrichment_packages: dict[str, Any],
    db: dict[str, Any],
    geiger_packages: dict[str, Any],
) -> None:
    packages = curation.get("packages") if isinstance(curation, dict) else None
    if not isinstance(packages, dict):
        return
    for package_key in sorted(packages):
        if package_key not in page_keys:
            continue
        entry = packages[package_key]
        if not isinstance(entry, dict):
            continue
        graph_entry = graph_packages.setdefault(package_key, empty_graph_entry(package_key, enrichment_packages, db, geiger_packages))
        intents = graph_entry.setdefault("linkIntents", {})
        curated_intents = entry.get("linkIntents") or {}
        if not isinstance(curated_intents, dict):
            continue
        for item in curated_intents.get("relatedPackages") or []:
            if isinstance(item, dict) and merge_unique_link(intents.setdefault("relatedPackages", []), item, page_keys, 24):
                graph_entry.setdefault("claims", []).append({
                    "intent": "internal-link",
                    "predicate": item.get("rel") or "domain_peer",
                    "target": curation_target_key(item),
                    "why": item.get("reason") or "Agent-curated package graph relation.",
                    "confidence": item.get("confidence") or 0.6,
                    "evidence": item.get("evidence") or "pkg-graph-curation",
                })
        for item in curated_intents.get("alsoAvailableVia") or []:
            if isinstance(item, dict) and merge_unique_link(intents.setdefault("alsoAvailableVia", []), item, page_keys, 12):
                graph_entry.setdefault("claims", []).append({
                    "intent": "cross-ecosystem-link",
                    "predicate": item.get("rel") or "alternative",
                    "target": curation_target_key(item),
                    "why": item.get("reason") or "Agent-curated cross-ecosystem relation.",
                    "confidence": item.get("confidence") or 0.6,
                    "evidence": item.get("evidence") or "pkg-graph-curation",
                })
        for hub in curated_intents.get("packageHubs") or []:
            if isinstance(hub, dict) and merge_unique_hub(intents.setdefault("packageHubs", []), hub):
                graph_entry.setdefault("claims", []).append({
                    "intent": "hub-backlink",
                    "predicate": "hub_member",
                    "target": f"pkg-hub:{hub.get('slug')}",
                    "why": hub.get("reason") or "Agent-curated package hub membership.",
                    "confidence": 0.66,
                    "evidence": "pkg-graph-curation.packageHubs",
                })


def apply_cross_ecosystem(
    graph_packages: dict[str, Any],
    cross_ecosystem: dict[str, Any],
    page_keys: set[str],
    enrichment_packages: dict[str, Any],
    db: dict[str, Any],
    geiger_packages: dict[str, Any],
) -> None:
    packages = cross_ecosystem.get("packages") if isinstance(cross_ecosystem, dict) else None
    if not isinstance(packages, dict):
        return
    for package_key in sorted(packages):
        if package_key not in page_keys:
            continue
        entry = packages[package_key]
        if not isinstance(entry, dict):
            continue
        graph_entry = graph_packages.setdefault(package_key, empty_graph_entry(package_key, enrichment_packages, db, geiger_packages))
        intents = graph_entry.setdefault("linkIntents", {})
        for item in entry.get("localLinks") or []:
            if isinstance(item, dict) and merge_unique_link(intents.setdefault("alsoAvailableVia", []), item, page_keys, 12):
                graph_entry.setdefault("claims", []).append({
                    "intent": "cross-ecosystem-link",
                    "predicate": item.get("rel") or "same_software_cross_ecosystem",
                    "target": curation_target_key(item),
                    "why": item.get("reason") or "Agent-curated cross-ecosystem install relation.",
                    "confidence": item.get("confidence") or 0.6,
                    "evidence": item.get("evidence") or "pkg-cross-ecosystem.localLinks",
                })


def hub_catalog(curation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog = {
        slug: {
            "label": str(hub["label"]),
            "reason": str(hub["reason"]),
        }
        for slug, hub in HUB_DEFINITIONS.items()
    }
    curated = curation.get("hubs") if isinstance(curation, dict) else None
    if isinstance(curated, dict):
        for slug, hub in curated.items():
            if not isinstance(hub, dict):
                continue
            catalog[slug] = {
                "label": str(hub.get("label") or slug),
                "kicker": str(hub.get("kicker") or "package graph cluster"),
                "description": str(hub.get("description") or hub.get("reason") or "Agent-curated package graph hub."),
                "reason": str(hub.get("reason") or "Agent-curated package graph hub."),
            }
    return catalog


def count_hub_memberships(graph_packages: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for entry in graph_packages.values():
        intents = entry.get("linkIntents") if isinstance(entry, dict) else None
        if not isinstance(intents, dict):
            continue
        seen = set()
        for hub in intents.get("packageHubs") or []:
            if isinstance(hub, dict) and hub.get("slug"):
                seen.add(str(hub["slug"]))
        for slug in seen:
            counts[slug] += 1
    return counts


def build_graph() -> dict[str, Any]:
    enrichment = read_json(PKG_PAGE_ENRICHMENT_PATH)
    db = read_json(DB_JSON_PATH)
    geiger_data = read_json(Path("data/geiger-counter.json"), {})
    isotopes = read_json(ISOTOPES_JSON_PATH, {})
    npm = read_json(Path("data/npm.json"), {})
    pip = read_json(Path("data/pip.json"), {})
    freshness = read_json(PKG_VERSION_FRESHNESS_PATH, {})
    curation = read_json(CURATION_PATH, {})
    cross_ecosystem = read_json(CROSS_ECOSYSTEM_PATH, {})
    packages = enrichment.get("packages") if isinstance(enrichment, dict) else {}
    if not isinstance(packages, dict):
        raise ValueError(f"{PKG_PAGE_ENRICHMENT_PATH} must contain packages")
    page_keys = page_keys_from_filtered_pages(db, geiger_data, isotopes, npm, pip, enrichment, freshness)
    taxonomy_index = load_pkg_taxonomy_index()
    taxonomy_by_key = {
        key: taxonomy_for_package(taxonomy_index, *package_key_parts(key))
        for key in page_keys
        if taxonomy_for_package(taxonomy_index, *package_key_parts(key))
    }
    taxonomy_term_index: dict[str, set[str]] = defaultdict(set)
    for key, taxonomy in taxonomy_by_key.items():
        for term in taxonomy_terms(taxonomy):
            taxonomy_term_index[term].add(key)

    provider_names = provider_packages(db, pip)
    normalized_by_provider: dict[str, dict[str, list[str]]] = {}
    for provider, names in provider_names.items():
        normalized: dict[str, list[str]] = defaultdict(list)
        for name in names:
            normalized[normalize_name(name)].append(name)
        normalized_by_provider[provider] = {key: sorted(value) for key, value in normalized.items()}

    brew_names = {key.split(":", 1)[1] for key in packages if key.startswith("brew:")}
    brew_page_names = {key.split(":", 1)[1] for key in page_keys if key.startswith("brew:")}
    reverse_runtime: dict[str, list[str]] = defaultdict(list)
    reverse_build: dict[str, list[str]] = defaultdict(list)
    family_groups: dict[str, list[str]] = defaultdict(list)
    repo_groups: dict[str, list[str]] = defaultdict(list)
    homepage_groups: dict[str, list[str]] = defaultdict(list)

    for key, info in packages.items():
        if not key.startswith("brew:") or not isinstance(info, dict):
            continue
        name = key.split(":", 1)[1]
        family_groups[family_key(name)].append(name)
        repo = repository_url(info)
        if repo:
            repo_groups[repo].append(name)
        homepage = info.get("homepage")
        if isinstance(homepage, str) and homepage:
            homepage_groups[homepage].append(name)
        for dep in info.get("dependencies") or []:
            if dep in brew_names:
                reverse_runtime[dep].append(name)
        for dep in info.get("buildDependencies") or []:
            if dep in brew_names:
                reverse_build[dep].append(name)

    geiger_packages = (geiger_data.get("packages") or {}) if isinstance(geiger_data, dict) else {}
    gated_packages = approval_gate_packages()
    isotope_names = isotope_packages(isotopes)
    graph_packages: dict[str, Any] = {}

    for key in sorted(packages):
        if not key.startswith("brew:"):
            continue
        if key not in page_keys:
            continue
        info = packages[key]
        if not isinstance(info, dict):
            continue
        name = key.split(":", 1)[1]
        db_info = ((db.get("formulas") or {}).get(name) or {})
        summary = db_info.get("summary") if isinstance(db_info, dict) else ""
        repo = repository_url(info)
        geiger = geiger_packages.get(name) if isinstance(geiger_packages.get(name), dict) else None
        gated = name in gated_packages
        isotope = name in isotope_names
        related: list[dict[str, Any]] = []
        related_seen: set[tuple[str, str, str]] = set()

        for dep in sorted(info.get("dependencies") or [], key=lambda item: package_popularity(db, item))[:8]:
            if dep in brew_page_names:
                append_unique(related, link_target("brew", dep, "Runtime dependency declared by Homebrew.", "runtime_dependency", 1.0, "pkg-page-enrichment.dependencies"), related_seen)
        for dep in sorted(info.get("buildDependencies") or [], key=lambda item: package_popularity(db, item))[:4]:
            if dep in brew_page_names:
                append_unique(related, link_target("brew", dep, "Build dependency declared by Homebrew.", "build_dependency", 0.82, "pkg-page-enrichment.buildDependencies"), related_seen)
        for dependent in sorted(reverse_runtime.get(name, []), key=lambda item: package_popularity(db, item))[:8]:
            if dependent in brew_page_names:
                append_unique(related, link_target("brew", dependent, "Popular package that depends on this formula.", "depended_on_by", 0.76, "reverse runtime dependency"), related_seen)
        for sibling in sorted(family_groups.get(family_key(name), []), key=lambda item: package_popularity(db, item))[:8]:
            if sibling != name and sibling in brew_page_names:
                append_unique(related, link_target("brew", sibling, "Package name indicates the same formula family.", "same_family", 0.68, "normalized package family"), related_seen)
        if repo:
            for sibling in sorted(repo_groups.get(repo, []), key=lambda item: package_popularity(db, item))[:8]:
                if sibling != name and sibling in brew_page_names:
                    append_unique(related, link_target("brew", sibling, "Shares the same upstream source repository.", "same_repository", 0.9, repo), related_seen)
        homepage = info.get("homepage")
        if isinstance(homepage, str) and homepage:
            for sibling in sorted(homepage_groups.get(homepage, []), key=lambda item: package_popularity(db, item))[:6]:
                if sibling != name and sibling in brew_page_names:
                    append_unique(related, link_target("brew", sibling, "Shares the same upstream homepage.", "same_homepage", 0.72, homepage), related_seen)
        taxonomy = taxonomy_by_key.get(key) or {}
        taxonomy_candidate_keys: set[str] = set()
        for term in taxonomy_terms(taxonomy):
            matches = taxonomy_term_index.get(term) or set()
            if len(matches) <= 1200:
                taxonomy_candidate_keys.update(matches)
        if len(taxonomy_candidate_keys) > 300:
            target_terms = taxonomy_terms(taxonomy)
            taxonomy_candidate_keys = set(sorted(
                taxonomy_candidate_keys,
                key=lambda candidate_key: (
                    -len(target_terms & taxonomy_terms(taxonomy_by_key.get(candidate_key) or {})),
                    package_popularity(db, package_key_parts(candidate_key)[1]),
                    candidate_key,
                ),
            )[:300])
        for peer, shared, score in taxonomy_peer_candidates(key, taxonomy, taxonomy_by_key, db, taxonomy_candidate_keys):
            append_unique(
                related,
                link_target("brew", peer, taxonomy_peer_reason(shared), "domain_peer", min(0.74, 0.58 + (score / 40)), "data/pkg-taxonomy.json"),
                related_seen,
            )

        also: list[dict[str, Any]] = []
        also_seen: set[tuple[str, str, str]] = set()
        normalized_name = normalize_name(name)
        for provider in ("cask", "npm", "pip"):
            for other in normalized_by_provider.get(provider, {}).get(normalized_name, [])[:4]:
                if f"{provider}:{other}" in page_keys:
                    append_unique(
                        also,
                        link_target(provider, other, "Same normalized package name in another local ecosystem.", "same_software_cross_ecosystem", 0.74, "normalized package name"),
                        also_seen,
                    )

        hubs = hub_memberships("brew", name, {**info, "summary": summary}, geiger, gated, isotope, taxonomy)

        claims = []
        for item in related[:20]:
            claims.append({
                "intent": "internal-link",
                "predicate": item["rel"],
                "target": f"{item['provider']}:{item['name']}",
                "why": item["reason"],
                "confidence": item["confidence"],
                "evidence": item["evidence"],
            })
        for item in also:
            claims.append({
                "intent": "cross-ecosystem-link",
                "predicate": item["rel"],
                "target": f"{item['provider']}:{item['name']}",
                "why": item["reason"],
                "confidence": item["confidence"],
                "evidence": item["evidence"],
            })
        for hub in hubs:
            claims.append({
                "intent": "hub-backlink",
                "predicate": "hub_member",
                "target": f"pkg-hub:{hub['slug']}",
                "why": hub["reason"],
                "confidence": 0.7,
                "evidence": "generated hub classifier",
            })

        graph_packages[key] = {
            "identity": {
                "provider": "brew",
                "name": name,
                "summary": summary or "",
                "version": info.get("version") or "",
                "homepage": info.get("homepage") or "",
                "repository": repo,
                "sourceHost": source_host(info.get("sourceArchive") or info.get("homepage") or ""),
                "packageManagerUrl": ((info.get("package") or {}).get("packageManagerUrl") or ""),
                "taxonomy": {
                    "category": taxonomy.get("category") or "",
                    "categoryPath": list(taxonomy.get("categoryPath") or [])[:8],
                    "categoryConfidence": taxonomy.get("categoryConfidence") or "",
                    "tags": list(taxonomy.get("tags") or [])[:16],
                },
            },
            "operationalContext": {
                "runtimeDependencyCount": len(info.get("dependencies") or []),
                "buildDependencyCount": len(info.get("buildDependencies") or []),
                "executableCount": len(info.get("executables") or []),
                "bottleAvailable": bool((info.get("bottle") or {}).get("available")),
                "postInstallDefined": (info.get("installBehavior") or {}).get("postInstallDefined"),
                "serviceDeclared": bool((info.get("installBehavior") or {}).get("service")),
                "geigerLevel": (geiger or {}).get("level") or "",
                "radioisotope": isotope,
                "approvalGate": gated,
            },
            "linkIntents": {
                "relatedPackages": related[:24],
                "alsoAvailableVia": also[:12],
                "packageHubs": hubs,
            },
            "claims": claims[:40],
        }

    apply_cross_ecosystem(graph_packages, cross_ecosystem, page_keys, packages, db, geiger_packages)
    apply_curation(graph_packages, curation, page_keys, packages, db, geiger_packages)
    hub_counts = count_hub_memberships(graph_packages)
    hubs = hub_catalog(curation)
    files = input_files()
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "description": "Agent-oriented package relationship graph. Edges are link intents with evidence, confidence, and operational reasons rather than generic tags.",
        "input_hash": input_hash(files),
        "input_files": [path.as_posix() for path in files],
        "relation_definitions": RELATION_DEFINITIONS,
        "hubs": {
            slug: {**hub, "packageCount": hub_counts.get(slug, 0)}
            for slug, hub in hubs.items()
        },
        "packages": graph_packages,
    }


def comparable_graph(graph: dict[str, Any]) -> dict[str, Any]:
    result = dict(graph)
    result.pop("generated_at", None)
    return result


def check_current(path: Path, terminal: Terminal) -> int:
    if not path.exists():
        terminal.error(f"Missing {path}. Run scripts/generate-pkg-graph.py.")
        return 1
    try:
        current = read_json(path)
        expected = build_graph()
    except (OSError, ValueError, json.JSONDecodeError) as err:
        terminal.error(f"Unable to validate {path}: {err}")
        return 1
    failures = []
    if current.get("schema") != SCHEMA_VERSION:
        failures.append(f"schema is {current.get('schema')!r}, expected {SCHEMA_VERSION}")
    if comparable_graph(current) != comparable_graph(expected):
        failures.append("package graph does not match current local package data")
    if failures:
        terminal.error("Package graph is stale.")
        for failure in failures:
            terminal.log(f"  - {failure}")
        terminal.log("Run scripts/generate-pkg-graph.py, then rebuild the package-origin SQLite artifact.")
        return 1
    terminal.ok(f"Package graph is current ({len(current.get('packages') or {}):,} packages)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate agent-friendly package relationship graph data.")
    parser.add_argument("--check", action="store_true", help="Validate that the graph matches current local inputs.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Path to write or validate. Defaults to {OUTPUT_PATH}.")
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
        graph = build_graph()
    except (OSError, ValueError, json.JSONDecodeError) as err:
        terminal.error(f"Failed to build package graph: {err}")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    terminal.ok(f"Wrote {len(graph.get('packages') or {}):,} package graph entries to {output_path}")
    if args.json:
        print(json.dumps({"ok": True, "output": str(output_path), "package_count": len(graph.get("packages") or {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
