#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from avdb_paths import DB_JSON_PATH


SCHEMA_VERSION = 1
GENERATED_DATA_DIR = Path("cache")
PKG_PAGE_ENRICHMENT_PATH = GENERATED_DATA_DIR / "pkg-page-enrichment.json"
OUTPUT_PATH = GENERATED_DATA_DIR / "pkg-version-freshness.json"
CACHE_DIR = Path("cache/github.com")
META_KEY = "__pkgdb_meta__"
PAYLOAD_KEY = "__pkgdb_payload__"
CHECK_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TIMEOUT = 30
USER_AGENT = "nucleus/0.1"


class Terminal:
    def __init__(self, json_mode: bool = False):
        self.json_mode = json_mode
        self.use_color = (
            not json_mode
            and sys.stderr.isatty()
            and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") != "dumb"
        )
        if self.use_color:
            self.bold = "\033[1m"
            self.red = "\033[31m"
            self.green = "\033[32m"
            self.blue = "\033[34m"
            self.dim = "\033[2m"
            self.reset = "\033[0m"
            self.step = "◆"
            self.ok = "✓"
            self.error = "✗"
        else:
            self.bold = self.red = self.green = self.blue = self.dim = self.reset = ""
            self.step = ">"
            self.ok = "OK"
            self.error = "ERROR"

    def log(self, message: str = "") -> None:
        if not self.json_mode:
            print(message, file=sys.stderr)

    def step_log(self, message: str) -> None:
        self.log(f"{self.blue}{self.step}{self.reset} {self.bold}{message}{self.reset}")

    def ok_log(self, message: str) -> None:
        self.log(f"  {self.green}{self.ok}{self.reset} {message}")

    def error_log(self, message: str) -> None:
        self.log(f"{self.red}{self.error}{self.reset} {message}")


def ensure_cwd() -> Path:
    scripts_dir = Path(__file__).resolve().parent
    root = scripts_dir.parent
    os.chdir(root)
    return root


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def age_days(value: Any, now: dt.datetime) -> int | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return max(0, (now.date() - parsed.date()).days)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_files() -> list[Path]:
    return [PKG_PAGE_ENRICHMENT_PATH, DB_JSON_PATH]


def source_digest(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def read_cached_json(url: str) -> tuple[Any, dict[str, Any]]:
    data = read_json(cache_path_for(url), None)
    if isinstance(data, dict) and META_KEY in data and PAYLOAD_KEY in data:
        return data.get(PAYLOAD_KEY), data.get(META_KEY) or {}
    return data, {}


def write_cache(path: Path, payload: Any, etag: str | None, checked_at: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            META_KEY: {"etag": etag, "checked_at": checked_at},
            PAYLOAD_KEY: payload,
        },
    )


def github_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def fetch_github_json(url: str, *, force_refresh: bool = False, cache_only: bool = True) -> Any:
    path = cache_path_for(url)
    payload = None
    meta: dict[str, Any] = {}
    if path.exists():
        payload, meta = read_cached_json(url)

    checked_at = meta.get("checked_at")
    now = int(time.time())
    if cache_only:
        return payload
    if (
        not force_refresh
        and isinstance(checked_at, int)
        and now - checked_at < CHECK_INTERVAL_SECONDS
    ):
        return payload

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    etag = meta.get("etag")
    if etag:
        headers["If-None-Match"] = etag

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            payload = json.loads(response.read())
            write_cache(path, payload, response.headers.get("etag"), now)
            return payload
    except urllib.error.HTTPError as err:
        if err.code == 304 and payload is not None:
            write_cache(path, payload, etag, now)
            return payload
        if err.code in {403, 404}:
            if payload is not None:
                return payload
            return None
        if payload is not None:
            print(f"Using cached GitHub data for {url}: {err}", file=sys.stderr)
            return payload
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        if payload is not None:
            print(f"Using cached GitHub data for {url}: {err}", file=sys.stderr)
            return payload
        return None


def normalize_github_repo(url: Any) -> str:
    if not isinstance(url, str) or "github.com" not in url:
        return ""
    text = url.strip()
    text = re.sub(r"^git\+", "", text)
    text = re.sub(r"^git://github\.com/", "https://github.com/", text)
    text = re.sub(r"^ssh://git@github\.com/", "https://github.com/", text)
    text = re.sub(r"^git@github\.com:", "https://github.com/", text)
    text = re.sub(r"\.git$", "", text)
    match = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s?#]+)", text)
    if not match:
        return ""
    owner = match.group(1)
    repo = match.group(2)
    repo = re.sub(r"\.(?:git|tar\.gz|zip)$", "", repo)
    repo = repo.split("/archive/", 1)[0]
    if not owner or not repo:
        return ""
    return f"https://github.com/{owner}/{repo}"


def normalize_upstream_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    text = url.strip()
    text = re.sub(r"^git\+", "", text)
    text = re.sub(r"^git://", "https://", text)
    text = re.sub(r"^ssh://git@", "https://", text)
    text = re.sub(r"^git@([^:]+):", r"https://\1/", text)
    text = re.sub(r"\.git$", "", text)
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return ""


def github_source_archive_tag(url: Any) -> str:
    if not isinstance(url, str) or "github.com" not in url:
        return ""
    text = url.strip()
    match = re.search(r"github\.com[:/]+[^/\s]+/[^/\s?#]+/archive/(?:refs/tags/)?([^?#]+)", text)
    if not match:
        return ""
    tag = urllib.parse.unquote(match.group(1)).strip("/")
    tag = re.sub(r"\.(?:tar\.gz|tgz|zip)$", "", tag)
    return tag


SEMVER_RE = re.compile(
    r"(?P<version>\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)"
)


def semver_key(version: str) -> tuple[int, int, int, int, tuple[Any, ...]] | None:
    parsed = normalize_version(version)
    if parsed is None:
        return None
    core, _, prerelease = parsed.partition("-")
    core = core.split("+", 1)[0]
    pieces = [int(piece) for piece in core.split(".")]
    while len(pieces) < 3:
        pieces.append(0)
    prerelease_rank = 1 if not prerelease else 0
    prerelease_key: list[Any] = []
    if prerelease:
        for piece in prerelease.split("."):
            prerelease_key.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return pieces[0], pieces[1], pieces[2], prerelease_rank, tuple(prerelease_key)


def normalize_version(value: Any, package_name: str = "") -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    text = re.sub(r"\.(?:tar\.gz|tgz|zip)$", "", text)
    text = text.rsplit("/", 1)[-1]
    if text.startswith("v"):
        text = text[1:]
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?", text):
        return text
    if package_name:
        for separator in ("_", "-"):
            prefix = f"{package_name}{separator}"
            if text.startswith(prefix):
                candidate = text[len(prefix):]
                if re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?", candidate):
                    return candidate.removeprefix("v")
    return None


def compare_versions(current: Any, latest: Any, package_name: str = "") -> str:
    current_key = semver_key(str(normalize_version(current, package_name) or ""))
    latest_key = semver_key(str(normalize_version(latest, package_name) or ""))
    if current_key is None or latest_key is None:
        return "unknown"
    if latest_key > current_key:
        return "behind"
    return "current"


def version_confidence(tag: str, package_name: str = "") -> str:
    if normalize_version(tag):
        return "high"
    if package_name and normalize_version(tag, package_name):
        return "medium"
    return "none"


def github_api_repo(repository: str) -> str:
    parsed = urllib.parse.urlparse(repository)
    path = parsed.path.strip("/")
    owner, repo = path.split("/", 1)
    return f"{owner}/{repo}"


def latest_github_release(repository: str, *, force_refresh: bool, cache_only: bool) -> dict[str, Any] | None:
    repo = github_api_repo(repository)
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    payload = fetch_github_json(url, force_refresh=force_refresh, cache_only=cache_only)
    if isinstance(payload, dict) and isinstance(payload.get("tag_name"), str):
        tag = payload["tag_name"]
        return {
            "latestVersion": tag,
            "latestSource": "github_release",
            "evidence": payload.get("html_url") or f"{repository}/releases/tag/{tag}",
        }
    return None


def latest_github_tag(repository: str, package_name: str, *, force_refresh: bool, cache_only: bool) -> dict[str, Any] | None:
    repo = github_api_repo(repository)
    url = f"https://api.github.com/repos/{repo}/tags?per_page=100"
    payload = fetch_github_json(url, force_refresh=force_refresh, cache_only=cache_only)
    if not isinstance(payload, list):
        return None
    candidates = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        key = semver_key(item["name"]) or semver_key(str(normalize_version(item["name"], package_name) or ""))
        if key is not None:
            candidates.append((key, item))
    if candidates:
        item = max(candidates, key=lambda pair: pair[0])[1]
    elif payload and isinstance(payload[0], dict) and isinstance(payload[0].get("name"), str):
        item = payload[0]
    else:
        return None
    tag = item["name"]
    return {
        "latestVersion": tag,
        "latestSource": "github_tag",
        "evidence": f"{repository}/tree/{urllib.parse.quote(tag, safe='')}",
    }


def latest_source_archive_tag(entry: dict[str, Any], package_name: str) -> dict[str, Any] | None:
    source_archive = entry.get("sourceArchive")
    tag = github_source_archive_tag(source_archive)
    if not tag or version_confidence(tag, package_name) == "none":
        return None
    return {
        "latestVersion": tag,
        "latestSource": "source_archive_tag",
        "evidence": str(source_archive),
    }


def apply_upstream_comparison(upstream: dict[str, Any], latest: dict[str, Any], current_version: Any, package_name: str) -> None:
    upstream.update(latest)
    comparison = compare_versions(current_version, latest["latestVersion"], package_name)
    confidence = version_confidence(str(latest["latestVersion"]), package_name)
    if comparison == "behind" and confidence == "high":
        upstream["comparison"] = "likely_lag"
    elif comparison == "behind":
        upstream["comparison"] = "maybe_lag"
    elif comparison == "current":
        upstream["comparison"] = "current"
    else:
        upstream["comparison"] = "not comparable"
    upstream["confidence"] = confidence


def upstream_metadata(package_key: str, entry: dict[str, Any], *, force_refresh: bool, cache_only: bool) -> dict[str, Any]:
    package = entry.get("package") if isinstance(entry.get("package"), dict) else {}
    package_name = str(package.get("name") or package_key.split(":", 1)[-1])
    github_repository = (
        normalize_github_repo(entry.get("repository"))
        or normalize_github_repo(entry.get("sourceArchive"))
        or normalize_github_repo(entry.get("homepage"))
    )
    repository = (
        github_repository
        or normalize_upstream_url(entry.get("repository"))
        or normalize_upstream_url(entry.get("homepage"))
    )
    upstream: dict[str, Any] = {
        "repository": repository,
        "comparison": "unknown" if github_repository else "not available",
        "confidence": "none",
    }
    if not github_repository:
        if repository:
            upstream["comparison"] = "not checked"
            upstream["note"] = "Release/tag comparison is only available for GitHub repositories."
        else:
            upstream["note"] = "No upstream repository was available in local package data."
        return upstream

    latest = latest_source_archive_tag(entry, package_name)
    if latest is not None:
        apply_upstream_comparison(upstream, latest, entry.get("version"), package_name)
        return upstream

    latest = latest_github_release(github_repository, force_refresh=force_refresh, cache_only=cache_only)
    if latest is None:
        latest = latest_github_tag(github_repository, package_name, force_refresh=force_refresh, cache_only=cache_only)
    if latest is None:
        upstream["comparison"] = "not checked"
        upstream["note"] = "No cached GitHub release or tag data was available."
        return upstream

    apply_upstream_comparison(upstream, latest, entry.get("version"), package_name)
    return upstream


def input_generated_at(enrichment: dict[str, Any], db: dict[str, Any]) -> dict[str, str]:
    return {
        PKG_PAGE_ENRICHMENT_PATH.as_posix(): str(enrichment.get("generated_at") or ""),
        DB_JSON_PATH.as_posix(): str(db.get("generated_at") or ""),
    }


def site_data_status(inputs: dict[str, str], now: dt.datetime) -> dict[str, Any]:
    ages = [age_days(value, now) for value in inputs.values()]
    known = [age for age in ages if age is not None]
    max_age = max(known) if known else None
    status = "unknown"
    if max_age is not None:
        if max_age <= 7:
            status = "ok"
        elif max_age <= 14:
            status = "notice"
        else:
            status = "warning"
    return {"inputs": inputs, "ageDays": max_age, "status": status}


def manager_info(package_key: str, entry: dict[str, Any], db: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    provider, name = package_key.split(":", 1)
    section = {"brew": "formulas", "cask": "casks", "npm": "npms"}.get(provider)
    db_entry = (db.get(section) or {}).get(name) if section else {}
    if not isinstance(db_entry, dict):
        db_entry = {}
    updated_at = (
        db_entry.get("last_updated_at")
        or entry.get("publishedAt")
        or entry.get("last_updated_at")
        or ""
    )
    days = age_days(updated_at, now)
    activity = "unknown"
    if days is not None:
        if days <= 180:
            activity = "fresh"
        elif days <= 365:
            activity = "quiet"
        else:
            activity = "stale"
    return {
        "version": str(entry.get("version") or db_entry.get("version") or ""),
        "updatedAt": str(updated_at or ""),
        "ageDays": days,
        "activity": activity,
    }


def package_rank(package_key: str, db: dict[str, Any]) -> int:
    provider, name = package_key.split(":", 1)
    section = {"brew": "formulas", "cask": "casks", "npm": "npms"}.get(provider)
    if not section:
        return 10_000_000
    info = (db.get(section) or {}).get(name)
    if not isinstance(info, dict):
        return 10_000_000
    popularity = info.get("popularity") or {}
    if not isinstance(popularity, dict):
        return 10_000_000
    try:
        return int(popularity.get("rank") or 10_000_000)
    except (TypeError, ValueError):
        return 10_000_000


def has_github_repository(entry: dict[str, Any]) -> bool:
    return bool(
        normalize_github_repo(entry.get("repository"))
        or normalize_github_repo(entry.get("sourceArchive"))
        or normalize_github_repo(entry.get("homepage"))
    )


def warning(kind: str, severity: str, message: str, evidence: str = "", confidence: str = "high") -> dict[str, str]:
    result = {
        "kind": kind,
        "severity": severity,
        "message": message,
        "confidence": confidence,
    }
    if evidence:
        result["evidence"] = evidence
    return result


def warnings_for(site: dict[str, Any], manager: dict[str, Any], upstream: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if site.get("status") == "notice":
        warnings.append(warning("site_data_age", "notice", "Local package-page source data is more than 7 days old.", confidence="high"))
    elif site.get("status") == "warning":
        warnings.append(warning("site_data_age", "warning", "Local package-page source data is more than 14 days old.", confidence="high"))

    if manager.get("activity") == "quiet":
        warnings.append(warning("package_manager_quiet", "notice", "The package-manager record has not changed recently.", str(manager.get("updatedAt") or ""), "high"))
    elif manager.get("activity") == "stale":
        warnings.append(warning("package_manager_stale", "warning", "The package-manager record has not been updated in over a year.", str(manager.get("updatedAt") or ""), "high"))
    elif manager.get("activity") == "unknown":
        warnings.append(warning("package_manager_unknown", "info", "No package-manager update timestamp was available.", confidence="low"))

    comparison = upstream.get("comparison")
    if comparison == "likely_lag":
        warnings.append(warning(
            "version_lag",
            "warning",
            f"Upstream appears newer than the package-manager version ({upstream.get('latestVersion')}).",
            str(upstream.get("evidence") or ""),
            str(upstream.get("confidence") or "high"),
        ))
    elif comparison == "maybe_lag":
        warnings.append(warning(
            "version_lag",
            "notice",
            f"Upstream may be newer, but the release/tag format is noisy ({upstream.get('latestVersion')}).",
            str(upstream.get("evidence") or ""),
            str(upstream.get("confidence") or "low"),
        ))
    elif comparison in {"not checked", "not available"}:
        warnings.append(warning("upstream_not_checked", "info", str(upstream.get("note") or "No upstream release/tag comparison was available."), str(upstream.get("repository") or ""), str(upstream.get("confidence") or "low")))
    elif comparison == "not comparable":
        warnings.append(warning("upstream_unknown", "info", str(upstream.get("note") or "No reliable upstream version comparison was available."), str(upstream.get("evidence") or upstream.get("repository") or ""), str(upstream.get("confidence") or "low")))
    return warnings


def db_packages(db: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    for provider, section in (("cask", "casks"),):
        items = db.get(section) or {}
        if not isinstance(items, dict):
            continue
        for name, info in items.items():
            if isinstance(name, str) and isinstance(info, dict):
                packages[f"{provider}:{name}"] = {
                    "package": {"provider": provider, "name": name},
                    "version": info.get("version") or "",
                    "homepage": info.get("homepage") or "",
                    "sourceArchive": info.get("url") or "",
                }
    return packages


def build_freshness(
    enrichment: dict[str, Any],
    db: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    force_refresh: bool = False,
    cache_only: bool = True,
    upstream_limit: int | None = None,
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    input_times = input_generated_at(enrichment, db)
    packages = dict(db_packages(db))
    enriched_packages = enrichment.get("packages") or {}
    if isinstance(enriched_packages, dict):
        packages.update({key: value for key, value in enriched_packages.items() if isinstance(key, str) and isinstance(value, dict)})

    site = site_data_status(input_times, now)
    result_packages: dict[str, Any] = {}
    refresh_keys: set[str] | None = None
    if force_refresh and upstream_limit is not None:
        ranked_keys = [key for key in sorted(packages, key=lambda key: (package_rank(key, db), key)) if has_github_repository(packages[key])]
        refresh_keys = set(ranked_keys[:upstream_limit])
    for package_key, entry in sorted(packages.items()):
        manager = manager_info(package_key, entry, db, now)
        should_fetch_upstream = force_refresh and (refresh_keys is None or package_key in refresh_keys)
        upstream = upstream_metadata(
            package_key,
            entry,
            force_refresh=should_fetch_upstream,
            cache_only=cache_only or not should_fetch_upstream,
        )
        result_packages[package_key] = {
            "siteData": site,
            "packageManager": manager,
            "upstream": upstream,
            "warnings": warnings_for(site, manager, upstream),
        }

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "input_hash": source_digest(source_files()),
        "packages": result_packages,
    }


def expected_freshness(*, force_refresh: bool = False, cache_only: bool = True, upstream_limit: int | None = None) -> dict[str, Any]:
    enrichment = read_json(PKG_PAGE_ENRICHMENT_PATH, {})
    db = read_json(DB_JSON_PATH, {})
    if not isinstance(enrichment, dict) or not isinstance(db, dict):
        raise ValueError("freshness inputs must be JSON objects")
    return build_freshness(enrichment, db, force_refresh=force_refresh, cache_only=cache_only, upstream_limit=upstream_limit)


def check_current(path: Path, terminal: Terminal) -> int:
    if not path.exists():
        terminal.error_log(f"Missing {path}. Run scripts/generate-pkg-version-freshness.py.")
        return 1
    try:
        current = read_json(path)
        expected = expected_freshness(cache_only=True)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        terminal.error_log(f"Unable to validate {path}: {err}")
        return 1
    failures: list[str] = []
    if current.get("schema") != SCHEMA_VERSION:
        failures.append(f"schema is {current.get('schema')!r}, expected {SCHEMA_VERSION}")
    if not current.get("generated_at"):
        failures.append("missing generated_at")
    if current.get("input_hash") != expected.get("input_hash"):
        failures.append("input hash does not match current freshness inputs")
    if current.get("packages") != expected.get("packages"):
        failures.append("freshness package data does not match current inputs and cached upstream metadata")
    if failures:
        terminal.error_log("Package version freshness is stale.")
        for failure in failures:
            terminal.log(f"  - {failure}")
        terminal.log("Run scripts/generate-pkg-version-freshness.py, then rebuild the package-origin SQLite artifact.")
        return 1
    terminal.ok_log(f"Package version freshness is current ({len(current.get('packages') or {}):,} packages)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate package-page version and freshness warnings.")
    parser.add_argument("--check", action="store_true", help="Validate that the output already matches current inputs.")
    parser.add_argument("--refresh-upstream", action="store_true", help="Refresh cached GitHub release/tag metadata.")
    parser.add_argument("--upstream-limit", type=int, default=None, help="Maximum GitHub repositories to refresh in this run.")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help=f"Path to write or validate. Defaults to {OUTPUT_PATH}.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable status and disable terminal styling.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_cwd()
    terminal = Terminal(json_mode=args.json)
    output_path = Path(args.output)
    if args.check:
        return check_current(output_path, terminal)
    try:
        terminal.step_log("Building package version freshness warnings")
        freshness = expected_freshness(
            force_refresh=args.refresh_upstream,
            cache_only=not args.refresh_upstream,
            upstream_limit=args.upstream_limit,
        )
    except (OSError, ValueError, json.JSONDecodeError) as err:
        terminal.error_log(f"Failed to build package freshness: {err}")
        return 1
    write_json(output_path, freshness)
    terminal.ok_log(f"Wrote {len(freshness.get('packages') or {}):,} package freshness records to {output_path}")
    if args.json:
        print(json.dumps({"ok": True, "output": str(output_path), "package_count": len(freshness.get("packages") or {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
