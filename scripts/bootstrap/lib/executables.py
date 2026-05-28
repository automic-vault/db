from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .brew import stable_version
from .common import CACHE_DIR, DEFAULT_TIMEOUT, USER_AGENT, fetch_json, read_json, write_json


TOKEN_SERVICE = "https://ghcr.io/token"
MANIFEST_ACCEPT = "application/vnd.oci.image.index.v1+json"
SOURCE_DB = Path.home() / "src" / "automic-vault" / "data" / "db.json"
_TOKENS: dict[tuple[str, bool], dict[str, Any]] = {}


def executable_index_from_db(db: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    entries = db.get("entries") or {}
    if not isinstance(entries, dict):
        return {}
    for executable, provider_key in entries.items():
        if not isinstance(executable, str) or not executable:
            continue
        if not isinstance(provider_key, str) or not provider_key:
            continue
        if ":" in provider_key:
            provider, name = provider_key.split(":", 1)
            if provider == "formula":
                provider = "brew"
        else:
            provider, name = "brew", provider_key
        if provider == "brew" and name:
            result.setdefault(name, set()).add(executable)
    return {name: sorted(executables) for name, executables in result.items()}


def seed_executables_from_source() -> dict[str, list[str]]:
    if not SOURCE_DB.exists():
        return {}
    payload = read_json(SOURCE_DB)
    if not isinstance(payload, dict):
        return {}
    return executable_index_from_db(payload)


def parse_exec_paths(paths: list[str]) -> list[str]:
    result = set()
    for entry in paths:
        if not entry:
            continue
        name = entry.strip().rsplit("/", 1)[-1]
        if name:
            result.add(name)
    return sorted(result)


def manifest_url(formula: dict[str, Any]) -> str:
    name = formula.get("name")
    version = stable_version(formula)
    if not isinstance(name, str) or not name or not version:
        return ""
    url = f"https://ghcr.io/v2/homebrew/core/{name.replace('+', 'x')}/manifests/{version}"
    revision = formula.get("revision")
    stable = (formula.get("versions") or {}).get("stable")
    if revision is None and isinstance(stable, dict):
        revision = stable.get("revision")
    if revision not in (None, 0):
        url = f"{url}_{revision}"
    rebuild = ((formula.get("bottle") or {}).get("stable") or {}).get("rebuild")
    if rebuild:
        url = f"{url}-{rebuild}"
    return url


def ghcr_repo_from_url(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 4 or parts[0] != "v2":
        return ""
    return "/".join(parts[1:-2])


def github_token() -> str:
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def github_username() -> str:
    return (os.environ.get("GHCR_USERNAME") or os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or "x-access-token").strip()


def ghcr_bearer_token(repo: str) -> str:
    now = int(time.time())
    has_token = bool(github_token())
    cache_key = (repo, has_token)
    cached = _TOKENS.get(cache_key)
    if cached and cached["expires_at"] > now:
        return str(cached["token"])
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repo}:pull"})
    headers = {"User-Agent": USER_AGENT}
    if has_token:
        basic = base64.b64encode(f"{github_username()}:{github_token()}".encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {basic}"
    request = urllib.request.Request(f"{TOKEN_SERVICE}?{query}", headers=headers)
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        data = json.loads(response.read())
    token = str(data.get("token") or "")
    if token:
        _TOKENS[cache_key] = {"token": token, "expires_at": now + int(data.get("expires_in", 300)) - 10}
    return token


def fetch_manifest(url: str, *, refresh: bool = False) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    repo = ghcr_repo_from_url(parsed.path)
    headers = {"Accept": MANIFEST_ACCEPT, "User-Agent": USER_AGENT}
    token = ghcr_bearer_token(repo) if repo else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return fetch_json_with_headers(url, headers=headers, namespace="brew-manifests", refresh=refresh)


def fetch_json_with_headers(url: str, *, headers: dict[str, str], namespace: str, refresh: bool) -> dict[str, Any] | None:
    # Use the shared JSON cache shape, but GHCR needs custom Accept/Auth headers.
    from .common import META_KEY, PAYLOAD_KEY, cache_path_for_url, read_cached_payload

    path = cache_path_for_url(url, namespace, ".json")
    payload = None
    meta: dict[str, Any] = {}
    if path.exists():
        payload, meta = read_cached_payload(path)
    if payload is not None and not refresh:
        return payload if isinstance(payload, dict) else None
    etag = meta.get("etag")
    if etag:
        headers = {**headers, "If-None-Match": str(etag)}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            payload = json.loads(response.read())
            write_json(path, {META_KEY: {"etag": response.headers.get("etag"), "checked_at": int(time.time())}, PAYLOAD_KEY: payload})
    except Exception:
        if payload is None:
            return None
    return payload if isinstance(payload, dict) else None


def executables_from_manifest(payload: dict[str, Any]) -> list[str]:
    manifests = payload.get("manifests")
    if not isinstance(manifests, list):
        return []
    for manifest in manifests:
        annotations = manifest.get("annotations") if isinstance(manifest, dict) else None
        if not isinstance(annotations, dict):
            continue
        provides = annotations.get("sh.brew.path_exec_files")
        if not isinstance(provides, str) or not provides:
            continue
        return parse_exec_paths([item.strip() for item in provides.split(",") if item.strip()])
    return []


def build_executable_index(formulae: list[dict[str, Any]], *, refresh: bool = False, fetch_manifests: bool = False, limit: int = 0) -> dict[str, list[str]]:
    seeded = seed_executables_from_source()
    if not fetch_manifests:
        return seeded
    result = {key: list(value) for key, value in seeded.items()}
    count = 0
    for formula in formulae:
        name = formula.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in result and not refresh:
            continue
        url = manifest_url(formula)
        if not url:
            continue
        payload = fetch_manifest(url, refresh=refresh)
        if payload:
            executables = executables_from_manifest(payload)
            if executables:
                result[name] = executables
        count += 1
        if limit and count >= limit:
            break
    return {key: sorted(set(value)) for key, value in sorted(result.items())}


def write_executable_index(index: dict[str, list[str]]) -> None:
    write_json(CACHE_DIR / "brew" / "executables.json", {"schema": 1, "packages": index})


def read_executable_index() -> dict[str, list[str]]:
    payload = read_json(CACHE_DIR / "brew" / "executables.json", {})
    packages = payload.get("packages") if isinstance(payload, dict) else {}
    if not isinstance(packages, dict):
        return {}
    return {str(key): [str(item) for item in value if isinstance(item, str)] for key, value in packages.items() if isinstance(value, list)}
