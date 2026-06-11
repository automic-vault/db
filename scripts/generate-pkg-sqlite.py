#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from email.utils import format_datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
OUTPUT_PATH = Path("cache/pkg.sqlite")
HTML_CACHE_CONTROL = "public, max-age=86400, s-maxage=86400"
PACKAGE_PAGE_SCRIPT = Path("scripts/generate-pkg-pages.py")


@dataclass(frozen=True)
class ResponseRecord:
    path: str
    content_type: str
    body: bytes
    etag: str
    last_modified: str
    cache_control: str = HTML_CACHE_CONTROL


@dataclass(frozen=True)
class SearchDocument:
    path: str
    locale: str
    title: str
    summary: str
    provider: str
    package_key: str
    rank: int | None
    search_text: str


@dataclass(frozen=True)
class PackageRecord:
    path: str
    provider: str
    slug: str
    package_key: str
    name: str
    display_name: str
    summary: str
    provider_label: str
    package_manager_url: str
    install_command: str
    native_install_command: str
    version: str
    category: str
    license: str
    homepage: str
    repository: str
    rank: int | None
    last_updated_at: str
    indexable: bool
    data: dict[str, Any]
    search_text: str


@dataclass(frozen=True)
class HubRecord:
    path: str
    slug: str
    title: str
    description: str
    group: str
    data: dict[str, Any]


@dataclass(frozen=True)
class HubPackageRecord:
    hub_slug: str
    package_key: str
    position: int
    reason: str


@dataclass(frozen=True)
class PackageRoute:
    path: str
    slug: str


class Terminal:
    def __init__(self, json_mode: bool = False):
        self.json_mode = json_mode

    def log(self, message: str) -> None:
        if not self.json_mode:
            print(message, file=sys.stderr)

    def step(self, message: str) -> None:
        self.log(f"> {message}")

    def ok(self, message: str) -> None:
        self.log(f"OK {message}")

    def error(self, message: str) -> None:
        self.log(f"ERROR {message}")


def ensure_cwd() -> Path:
    scripts_dir = Path(__file__).resolve().parent
    root = scripts_dir.parent
    os.chdir(root)
    return root


def load_pkg_pages_module():
    spec = importlib.util.spec_from_file_location("av_pkg_pages", PACKAGE_PAGE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {PACKAGE_PAGE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def http_date(value: Any) -> str:
    text = str(value or "")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = dt.datetime.now(dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return format_datetime(parsed.astimezone(dt.timezone.utc), usegmt=True)


def response_path(path: str) -> str:
    if path.endswith("/"):
        return f"{path}index.html"
    return path


def etag_for(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest() + '"'


def response_record(
    path: str,
    content: str | bytes,
    content_type: str,
    last_modified: str,
    cache_control: str = HTML_CACHE_CONTROL,
) -> ResponseRecord:
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return ResponseRecord(
        path=response_path(path),
        content_type=content_type,
        body=data,
        etag=etag_for(data),
        last_modified=last_modified,
        cache_control=cache_control,
    )


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;

        CREATE TABLE metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE responses (
          path TEXT PRIMARY KEY,
          content_type TEXT NOT NULL,
          body BLOB NOT NULL,
          etag TEXT NOT NULL,
          last_modified TEXT NOT NULL,
          cache_control TEXT NOT NULL
        );

        CREATE TABLE search_documents (
          path TEXT PRIMARY KEY,
          locale TEXT NOT NULL,
          title TEXT NOT NULL,
          summary TEXT NOT NULL,
          provider TEXT NOT NULL,
          package_key TEXT NOT NULL,
          rank INTEGER,
          search_text TEXT NOT NULL
        );

        CREATE INDEX search_documents_locale_idx
          ON search_documents(locale);

        CREATE TABLE packages (
          path TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          slug TEXT NOT NULL,
          package_key TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          display_name TEXT NOT NULL,
          summary TEXT NOT NULL,
          provider_label TEXT NOT NULL,
          package_manager_url TEXT NOT NULL,
          install_command TEXT NOT NULL,
          native_install_command TEXT NOT NULL,
          version TEXT NOT NULL,
          category TEXT NOT NULL,
          license TEXT NOT NULL,
          homepage TEXT NOT NULL,
          repository TEXT NOT NULL,
          rank INTEGER,
          last_updated_at TEXT NOT NULL,
          indexable INTEGER NOT NULL,
          data_json TEXT NOT NULL,
          search_text TEXT NOT NULL
        );

        CREATE INDEX packages_provider_slug_idx
          ON packages(provider, slug);
        CREATE INDEX packages_rank_idx
          ON packages(rank);

        CREATE TABLE hubs (
          path TEXT PRIMARY KEY,
          slug TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          description TEXT NOT NULL,
          group_name TEXT NOT NULL,
          data_json TEXT NOT NULL
        );

        CREATE TABLE hub_packages (
          hub_slug TEXT NOT NULL,
          package_key TEXT NOT NULL,
          position INTEGER NOT NULL,
          reason TEXT NOT NULL,
          PRIMARY KEY(hub_slug, package_key)
        );

        CREATE INDEX hub_packages_hub_idx
          ON hub_packages(hub_slug, position);
        """
    )


def write_sqlite(
    output_path: Path,
    responses: list[ResponseRecord],
    search_documents: list[SearchDocument],
    packages: list[PackageRecord],
    hubs: list[HubRecord],
    hub_packages: list[HubPackageRecord],
    metadata: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        connection = sqlite3.connect(temp_path)
        try:
            create_schema(connection)
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                [(str(key), json.dumps(value, sort_keys=True)) for key, value in sorted(metadata.items())],
            )
            connection.executemany(
                """
                INSERT INTO responses(path, content_type, body, etag, last_modified, cache_control)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.path,
                        record.content_type,
                        record.body,
                        record.etag,
                        record.last_modified,
                        record.cache_control,
                    )
                    for record in responses
                ],
            )
            connection.executemany(
                """
                INSERT INTO search_documents(path, locale, title, summary, provider, package_key, rank, search_text)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document.path,
                        document.locale,
                        document.title,
                        document.summary,
                        document.provider,
                        document.package_key,
                        document.rank,
                        document.search_text,
                    )
                    for document in search_documents
                ],
            )
            connection.executemany(
                """
                INSERT INTO packages(
                  path, provider, slug, package_key, name, display_name, summary,
                  provider_label, package_manager_url, install_command, native_install_command,
                  version, category, license, homepage, repository, rank, last_updated_at,
                  indexable, data_json, search_text
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        package.path,
                        package.provider,
                        package.slug,
                        package.package_key,
                        package.name,
                        package.display_name,
                        package.summary,
                        package.provider_label,
                        package.package_manager_url,
                        package.install_command,
                        package.native_install_command,
                        package.version,
                        package.category,
                        package.license,
                        package.homepage,
                        package.repository,
                        package.rank,
                        package.last_updated_at,
                        1 if package.indexable else 0,
                        json.dumps(package.data, sort_keys=True, separators=(",", ":")),
                        package.search_text,
                    )
                    for package in packages
                ],
            )
            connection.executemany(
                """
                INSERT INTO hubs(path, slug, title, description, group_name, data_json)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        hub.path,
                        hub.slug,
                        hub.title,
                        hub.description,
                        hub.group,
                        json.dumps(hub.data, sort_keys=True, separators=(",", ":")),
                    )
                    for hub in hubs
                ],
            )
            connection.executemany(
                """
                INSERT INTO hub_packages(hub_slug, package_key, position, reason)
                VALUES(?, ?, ?, ?)
                """,
                [
                    (record.hub_slug, record.package_key, record.position, record.reason)
                    for record in hub_packages
                ],
            )
            connection.execute("PRAGMA optimize")
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"sqlite integrity_check failed: {result}")
            connection.commit()
        finally:
            connection.close()
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def previous_manifest_from_sqlite(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(path)
        try:
            rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    metadata = {}
    for key, value in rows:
        try:
            metadata[key] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            metadata[key] = value
    manifest = metadata.get("manifest")
    return manifest if isinstance(manifest, dict) else None


def search_script(default_locale: str) -> str:
    return f"""(() => {{
  const defaultLocale = {json.dumps(default_locale)};

  function escapeHtml(value) {{
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }}

  function resultRow(result) {{
    const provider = result.provider ? result.provider + " / " : "";
    return `
      <a class="av-search-result package-row" href="${{escapeHtml(result.url)}}">
        <span>${{escapeHtml(result.title)}}</span>
        <small>${{escapeHtml(provider + (result.summary || result.packageKey || ""))}}</small>
      </a>`;
  }}

  function init(root) {{
    const locale = root.dataset.locale || defaultLocale || "en";
    const endpoint = root.dataset.searchEndpoint || "/pkg/search.json";
    const placeholder = root.dataset.placeholder || "Search packages";
    root.innerHTML = `
      <form class="av-search-form" role="search">
        <input class="av-search-input" type="search" name="q" autocomplete="off" placeholder="${{escapeHtml(placeholder)}}" aria-label="${{escapeHtml(placeholder)}}">
      </form>
      <div class="av-search-status" aria-live="polite"></div>
      <div class="av-search-results"></div>`;

    const form = root.querySelector("form");
    const input = root.querySelector("input");
    const status = root.querySelector(".av-search-status");
    const results = root.querySelector(".av-search-results");
    let activeController = null;

    async function search() {{
      const query = input.value.trim();
      if (!query) {{
        status.textContent = "";
        results.innerHTML = "";
        return;
      }}
      if (activeController) activeController.abort();
      activeController = new AbortController();
      const params = new URLSearchParams({{ q: query, locale, limit: "8" }});
      status.textContent = "Searching...";
      try {{
        const response = await fetch(`${{endpoint}}?${{params}}`, {{
          signal: activeController.signal,
          headers: {{ Accept: "application/json" }}
        }});
        if (!response.ok) throw new Error(`search failed: ${{response.status}}`);
        const data = await response.json();
        const count = Number(data.totalCount || 0);
        status.textContent = count === 1 ? "1 result" : `${{count}} results`;
        results.innerHTML = (data.results || []).map(resultRow).join("");
      }} catch (error) {{
        if (error.name === "AbortError") return;
        status.textContent = "Search unavailable";
        results.innerHTML = "";
      }}
    }}

    form.addEventListener("submit", event => {{
      event.preventDefault();
      search();
    }});
    input.addEventListener("input", () => {{
      window.clearTimeout(input._avSearchTimer);
      input._avSearchTimer = window.setTimeout(search, 160);
    }});
  }}

  window.addEventListener("DOMContentLoaded", () => {{
    document.querySelectorAll("[data-av-package-search]").forEach(init);
  }});
}})();
"""


def adapt_package_index_search(html_text: str, page_module: Any, locale: dict[str, Any] | None) -> str:
    code = page_module.locale_code(locale)
    endpoint = page_module.locale_path("/pkg/search.json", locale)
    script = page_module.locale_path("/pkg/search.js", locale)
    placeholder = page_module.tx(locale, "searchPlaceholder", "Search awscli, gh, .env, npm publish")
    replacement = (
        '<div id="pkg-search" class="pkg-search" data-av-package-search '
        f'data-locale="{page_module.attr(code)}" '
        f'data-search-endpoint="{page_module.attr(endpoint)}" '
        f'data-placeholder="{page_module.attr(placeholder)}"></div>'
    )
    html_text = html_text.replace(
        '<div id="pkg-search" class="pkg-search" data-pagefind-ui></div>',
        replacement,
    )
    html_text = re.sub(
        r'\n\s*<link rel="stylesheet" href="/pagefind/pagefind-ui.css">',
        "",
        html_text,
    )
    html_text = re.sub(
        r'\n\s*<script src="/pagefind/pagefind-ui\.js"></script>\s*'
        r"<script>\s*window\.addEventListener\(\"DOMContentLoaded\", \(\) => \{.*?</script>",
        f'\n  <script src="{page_module.attr(script)}"></script>',
        html_text,
        flags=re.DOTALL,
    )
    return html_text


def css_with_search_styles(page_module: Any) -> str:
    return page_module.render_css() + """
.av-search-form {
  display: grid;
}
.av-search-input {
  width: 100%;
  min-height: 56px;
  padding: 13px 16px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.035), transparent),
    #10100f;
  color: var(--ink);
  font: 700 0.92rem/1.3 var(--font-mono);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition: border-color 180ms cubic-bezier(0.16, 1, 0.3, 1), background 180ms cubic-bezier(0.16, 1, 0.3, 1), transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.av-search-input::placeholder {
  color: #8d887f;
  opacity: 1;
}
.av-search-input:focus-visible {
  border-color: rgba(114, 182, 97, 0.72);
  background: #22211f;
  outline: none;
  transform: translateY(-1px);
}
.av-search-status {
  min-height: 1.3em;
  margin-top: 12px;
  color: var(--dim);
  font: 700 0.74rem/1.3 var(--font-mono);
  text-transform: uppercase;
}
.av-search-results {
  display: grid;
  gap: 1px;
  margin-top: 12px;
  border: 1px solid var(--line);
  background: var(--line);
}
.av-search-result {
  border: 0;
}
"""


def manifest_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "manifest": manifest,
        "source_hash": manifest.get("source_hash", ""),
        "generated_at": manifest.get("generated_at", ""),
        "last_modified": http_date(manifest.get("generated_at")),
    }


def populate_manifest_counts(page_module: Any, manifest: dict[str, Any], pages: list[Any], hubs: list[tuple[Any, list[Any]]]) -> None:
    indexable_pages = [page for page in pages if page_module.is_indexable_package_page(page)]
    sitemap_names = ["sitemap-hubs.xml"] + [
        f"sitemap-{provider}.xml"
        for provider in ("brew", "cask", "npm", "pip")
        if any(page.provider == provider for page in indexable_pages)
    ]
    manifest["hub_count"] = len(hubs)
    manifest["package_count"] = len(pages)
    manifest["indexable_page_count"] = len(indexable_pages)
    manifest["noindex_page_count"] = len(pages) - len(indexable_pages)
    manifest["markdown_page_count"] = len(indexable_pages)
    manifest["hub_markdown_page_count"] = len(hubs)
    manifest["approval_gate_count"] = sum(1 for page in pages if getattr(page, "approval_gate", None))
    manifest["sitemap_count"] = len(sitemap_names)
    manifest["sitemap_page_counts"] = {
        provider: sum(1 for page in indexable_pages if page.provider == provider)
        for provider in ("brew", "cask", "npm", "pip")
        if any(page.provider == provider for page in indexable_pages)
    }


def page_search_text(page_module: Any, page: Any, locale: dict[str, Any] | None) -> str:
    pieces: list[str] = [
        page.display_name,
        page.name,
        page.key,
        page.slug,
        page.provider,
        page_module.package_manager_label(page),
        page_module.clean_summary(page.summary),
        page.category,
        page.license,
        page.repository,
        page.homepage,
    ]
    pieces.extend(sorted(page.aliases))
    pieces.extend(str(item.get("name") or item.get("target") or item.get("source") or "") for item in page.executables if isinstance(item, dict))
    pieces.extend(str(item.get("target") or item.get("source") or "") for item in page.binaries if isinstance(item, dict))
    pieces.extend(str(item) for item in page.keywords)
    pieces.extend(str(item) for item in page.classifiers)
    for hub in page.package_hubs:
        if isinstance(hub, dict):
            pieces.extend(str(hub.get(key) or "") for key in ("slug", "label", "reason"))
    if page.geiger:
        pieces.extend(str(item) for item in page.geiger.get("reasons") or [])
    if page.isotope:
        justification = page.isotope.get("justification") or {}
        pieces.append(page_module.public_copy(justification.get("title") or ""))
    if page.approval_gate:
        pieces.extend(str(item) for item in page.approval_gate.get("rules") or [])
    if getattr(page, "agent_safety_answer", None):
        pieces.extend(str(value) for value in page.agent_safety_answer.values())
    taxonomy = page.extra.get("pkgTaxonomy") if isinstance(page.extra.get("pkgTaxonomy"), dict) else {}
    pieces.extend(str(item) for item in page_module.taxonomy_terms(taxonomy))
    return page_module.normalize_space(" ".join(str(piece or "") for piece in pieces))


def string_items(values: Any, keys: tuple[str, ...] = ("name", "target", "source", "label", "title")) -> list[str]:
    items: list[str] = []
    if not isinstance(values, (list, tuple, set)):
        return items
    for value in values:
        if isinstance(value, dict):
            for key in keys:
                text = str(value.get(key) or "").strip()
                if text:
                    items.append(text)
                    break
        else:
            text = str(value or "").strip()
            if text:
                items.append(text)
    return sorted(dict.fromkeys(items))


def package_security_signals(page_module: Any, page: Any) -> list[str]:
    signals: list[str] = []
    if page.isotope:
        justification = page.isotope.get("justification") or {}
        title = page_module.public_copy(justification.get("title") or "")
        if title:
            signals.append(title)
    if page.geiger:
        signals.extend(str(item) for item in page.geiger.get("reasons") or [])
    if page.approval_gate:
        rule_count = page.approval_gate.get("rule_count")
        if rule_count:
            signals.append(f"{rule_count} approval-gate rules")
        signals.extend(str(item) for item in page.approval_gate.get("rules") or [])
    return sorted(dict.fromkeys(item.strip() for item in signals if item and item.strip()))


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(jsonable(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def is_scoped_npm_package(page: Any) -> bool:
    name = str(getattr(page, "name", ""))
    return str(getattr(page, "provider", "")) == "npm" and name.startswith("@") and "/" in name


def route_hash(page: Any) -> str:
    return hashlib.sha256(str(getattr(page, "key", "")).encode("utf-8")).hexdigest()[:8]


def collision_slug_candidates(page: Any, base_slug: str) -> list[str]:
    candidates: list[str] = []
    if is_scoped_npm_package(page):
        candidates.append(f"scoped-{base_slug}")
    candidates.extend([base_slug, f"{base_slug}-{route_hash(page)}"])
    return candidates


def package_routes(pages: list[Any]) -> dict[str, PackageRoute]:
    groups: dict[tuple[str, str], list[Any]] = {}
    base_slugs_by_provider: dict[str, set[str]] = {}
    for page in pages:
        provider = str(getattr(page, "provider", ""))
        slug = str(getattr(page, "slug", ""))
        groups.setdefault((provider, slug), []).append(page)
        base_slugs_by_provider.setdefault(provider, set()).add(slug)

    assigned_by_provider: dict[str, set[str]] = {}
    routes: dict[str, PackageRoute] = {}
    for (provider, base_slug), group in sorted(groups.items()):
        if len(group) > 1:
            group = sorted(group, key=lambda page: (is_scoped_npm_package(page), str(getattr(page, "name", ""))))
        assigned = assigned_by_provider.setdefault(provider, set())
        for page in group:
            candidates = [base_slug] if len(group) == 1 else collision_slug_candidates(page, base_slug)
            slug = ""
            for candidate in candidates:
                if candidate in assigned:
                    continue
                if candidate != base_slug and candidate in base_slugs_by_provider.get(provider, set()):
                    continue
                slug = candidate
                break
            if not slug:
                slug = f"{base_slug}-{route_hash(page)}"
            assigned.add(slug)
            routes[str(getattr(page, "key", ""))] = PackageRoute(
                path=f"/pkg/{provider}/{slug}/",
                slug=slug,
            )
    return routes


def full_package_data(page_module: Any, page: Any, route: PackageRoute | None = None) -> dict[str, Any]:
    extra = getattr(page, "extra", {})
    if not isinstance(extra, dict):
        extra = {}
    slug = route.slug if route else getattr(page, "slug", "")
    path = route.path if route else getattr(page, "path", "")
    return {
        "provider": getattr(page, "provider", ""),
        "name": getattr(page, "name", ""),
        "displayName": getattr(page, "display_name", ""),
        "key": getattr(page, "key", ""),
        "slug": slug,
        "path": path,
        "summary": getattr(page, "summary", ""),
        "homepage": getattr(page, "homepage", ""),
        "version": getattr(page, "version", ""),
        "lastUpdatedAt": getattr(page, "last_updated_at", ""),
        "pulseKind": getattr(page, "pulse_kind", ""),
        "url": getattr(page, "url", ""),
        "sha256": getattr(page, "sha256", ""),
        "binaries": getattr(page, "binaries", []),
        "popularity": getattr(page, "popularity", {}),
        "aliases": sorted(str(item) for item in getattr(page, "aliases", [])),
        "sourceNotes": getattr(page, "source_notes", []),
        "packageManager": getattr(page, "package_manager", ""),
        "packageManagerUrl": getattr(page, "package_manager_url", ""),
        "repository": getattr(page, "repository", ""),
        "upstreamDocs": getattr(page, "upstream_docs", ""),
        "category": getattr(page, "category", ""),
        "license": getattr(page, "license", ""),
        "sourceArchive": getattr(page, "source_archive", ""),
        "lastVerified": getattr(page, "last_verified", ""),
        "dependencies": getattr(page, "dependencies", []),
        "buildDependencies": getattr(page, "build_dependencies", []),
        "usesFromMacos": getattr(page, "uses_from_macos", []),
        "install": getattr(page, "install", {}),
        "installCommands": getattr(page, "install_commands", []),
        "executablesDetailed": getattr(page, "executables", []),
        "installBehavior": getattr(page, "install_behavior", {}),
        "bottle": getattr(page, "bottle", {}),
        "publishedAt": getattr(page, "published_at", ""),
        "keywords": getattr(page, "keywords", []),
        "issueTracker": getattr(page, "issue_tracker", ""),
        "classifiers": getattr(page, "classifiers", []),
        "projectUrls": getattr(page, "project_urls", {}),
        "versionFreshness": getattr(page, "version_freshness", {}),
        "geiger": getattr(page, "geiger", None),
        "relatedPackages": getattr(page, "related_packages", []),
        "alsoAvailableVia": getattr(page, "also_available_via", []),
        "packageHubs": getattr(page, "package_hubs", []),
        "agentSafetyAnswer": getattr(page, "agent_safety_answer", None),
        "isotope": getattr(page, "isotope", None),
        "isotopeReadme": getattr(page, "isotope_readme", ""),
        "isotopeReadmeHtml": page_module.public_copy(getattr(page, "isotope_readme_html", "")),
        "isotopeReadmeSource": getattr(page, "isotope_readme_source", ""),
        "approvalGate": getattr(page, "approval_gate", None),
        "registryInsights": extra.get("registryInsights", {}),
        "externalPackageManagerMatches": getattr(page, "external_package_manager_matches", []),
        "extra": extra,
    }


def package_data(page_module: Any, page: Any, route: PackageRoute) -> dict[str, Any]:
    data = {
        "aliases": sorted(str(item) for item in getattr(page, "aliases", [])),
        "binaries": string_items(getattr(page, "binaries", [])),
        "classifiers": string_items(getattr(page, "classifiers", [])),
        "executables": string_items(getattr(page, "executables", [])),
        "hubs": [
            {
                "slug": str(hub.get("slug") or ""),
                "label": str(hub.get("label") or ""),
                "reason": str(hub.get("reason") or ""),
            }
            for hub in getattr(page, "package_hubs", [])
            if isinstance(hub, dict)
        ],
        "keywords": string_items(getattr(page, "keywords", [])),
        "related": string_items(getattr(page, "related_packages", []), ("label", "name", "target", "package", "key")),
        "security": package_security_signals(page_module, page),
    }
    data["full"] = jsonable(full_package_data(page_module, page, route))
    return data


def package_record(page_module: Any, page: Any, route: PackageRoute, search_text: str) -> PackageRecord:
    return PackageRecord(
        path=route.path,
        provider=page.provider,
        slug=route.slug,
        package_key=page.key,
        name=page.name,
        display_name=page.display_name,
        summary=page_module.short_text(page_module.clean_summary(page.summary) or page_module.hero_sentence(page), 320),
        provider_label=page_module.package_manager_label(page),
        package_manager_url=getattr(page, "package_manager_url", ""),
        install_command=page_module.install_command(page),
        native_install_command=page_module.native_install_command(page),
        version=getattr(page, "version", ""),
        category=getattr(page, "category", ""),
        license=getattr(page, "license", ""),
        homepage=getattr(page, "homepage", ""),
        repository=getattr(page, "repository", ""),
        rank=page.popularity.get("rank") if isinstance(page.popularity, dict) else None,
        last_updated_at=getattr(page, "last_updated_at", ""),
        indexable=page_module.is_indexable_package_page(page),
        data=package_data(page_module, page, route),
        search_text=search_text,
    )


def hub_record(hub: Any) -> HubRecord:
    return HubRecord(
        path=hub.path,
        slug=hub.slug,
        title=hub.title,
        description=hub.description,
        group=hub.group,
        data={
            "match": getattr(hub, "match", ""),
            "source": getattr(hub, "source", ""),
        },
    )


def build_records(
    page_module: Any,
    output_path: Path,
) -> tuple[list[ResponseRecord], list[SearchDocument], list[PackageRecord], list[HubRecord], list[HubPackageRecord], dict[str, Any]]:
    sources = page_module.load_sources()
    pages_by_key = page_module.package_pages_from_sources(sources)
    if not pages_by_key:
        raise RuntimeError("no package metadata found")
    pages = sorted(pages_by_key.values(), key=lambda page: (page.provider, page.slug, page.name))
    routes = package_routes(pages)
    hubs = page_module.package_hub_pages(pages)
    files = page_module.source_files()
    previous_manifest = previous_manifest_from_sqlite(output_path)
    manifest = page_module.build_manifest(len(pages), files, previous_manifest)
    populate_manifest_counts(page_module, manifest, pages, hubs)
    last_modified = http_date(manifest.get("generated_at"))
    css = css_with_search_styles(page_module)
    responses: list[ResponseRecord] = []
    documents: list[SearchDocument] = []
    package_rows: list[PackageRecord] = []
    hub_rows: list[HubRecord] = [hub_record(hub) for hub, _hub_pages in hubs]
    hub_package_rows: list[HubPackageRecord] = []
    indexable_pages = [page for page in pages if page_module.is_indexable_package_page(page)]

    def add(path: str, content: str | bytes, content_type: str) -> None:
        responses.append(response_record(path, content, content_type, last_modified))

    for page in pages:
        search_text = page_search_text(page_module, page, None)
        package_rows.append(package_record(page_module, page, routes[page.key], search_text))

    for hub, hub_pages in hubs:
        for position, page in enumerate(hub_pages, start=1):
            hub_package_rows.append(HubPackageRecord(
                hub_slug=hub.slug,
                package_key=page.key,
                position=position,
                reason=page_module.hub_package_reason(page),
            ))

    for locale in page_module.i18n_locales():
        locale_code = page_module.locale_code(locale)
        add(page_module.locale_path("/pkg/styles.css", locale), css, "text/css; charset=utf-8")
        add(page_module.locale_path("/pkg/search.js", locale), search_script(locale_code), "application/javascript; charset=utf-8")
        add(
            page_module.locale_path("/pkg/.manifest.json", locale),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            "application/json; charset=utf-8",
        )
        for page in pages:
            documents.append(SearchDocument(
                path=page_module.locale_path(routes[page.key].path, locale),
                locale=locale_code,
                title=page.display_name,
                summary=page_module.short_text(page_module.clean_summary(page.summary) or page_module.hero_sentence(page), 180),
                provider=page.provider,
                package_key=page.key,
                rank=page.popularity.get("rank") if isinstance(page.popularity, dict) else None,
                search_text=page_search_text(page_module, page, locale),
            ))

    metadata = manifest_metadata(manifest)
    metadata["locales"] = page_module.i18n_locales()
    metadata["providers"] = sorted({page.provider for page in indexable_pages})
    return responses, documents, package_rows, hub_rows, hub_package_rows, metadata


def check_current(page_module: Any, output_path: Path, terminal: Terminal) -> int:
    if not output_path.exists():
        terminal.error(f"Missing {output_path}. Run scripts/generate-pkg-sqlite.py.")
        return 1
    try:
        connection = sqlite3.connect(output_path)
        try:
            metadata = {
                key: json.loads(value)
                for key, value in connection.execute("SELECT key, value FROM metadata")
            }
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, json.JSONDecodeError) as err:
        terminal.error(f"Invalid {output_path}: {err}")
        return 1
    failures: list[str] = []
    if metadata.get("schema") != SCHEMA_VERSION:
        failures.append(f"schema is {metadata.get('schema')!r}, expected {SCHEMA_VERSION}")
    if not integrity or integrity[0] != "ok":
        failures.append(f"integrity_check failed: {integrity}")
    expected_hash, _latest = page_module.source_digest(page_module.source_files())
    if metadata.get("source_hash") != expected_hash:
        failures.append("source hash does not match current package source files")
    manifest = metadata.get("manifest") if isinstance(metadata.get("manifest"), dict) else {}
    if not manifest:
        failures.append("manifest metadata is missing")
    if failures:
        terminal.error("Package SQLite artifact is stale.")
        for failure in failures:
            terminal.log(f"  - {failure}")
        terminal.log("Run scripts/generate-pkg-sqlite.py and retry deploy.")
        return 1
    terminal.ok(f"Package SQLite artifact is current ({output_path})")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the Atlas package-origin SQLite artifact.")
    parser.add_argument("--check", action="store_true", help="Validate that pkg.sqlite matches current package source data.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Output SQLite path. Defaults to {OUTPUT_PATH}.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_cwd()
    terminal = Terminal(json_mode=args.json)
    output_path = Path(args.output)
    page_module = load_pkg_pages_module()

    if args.check:
        return check_current(page_module, output_path, terminal)

    terminal.step("Rendering package pages into SQLite")
    responses, documents, packages, hubs, hub_packages, metadata = build_records(page_module, output_path)
    write_sqlite(output_path, responses, documents, packages, hubs, hub_packages, metadata)
    terminal.ok(
        f"Wrote {len(packages):,} packages, {len(hubs):,} hubs, "
        f"{len(documents):,} search documents, and {len(responses):,} static responses to {output_path}"
    )
    if args.json:
        print(json.dumps({
            "ok": True,
            "output": str(output_path),
            "packages": len(packages),
            "hubs": len(hubs),
            "responses": len(responses),
            "search_documents": len(documents),
            "source_hash": metadata.get("source_hash"),
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
