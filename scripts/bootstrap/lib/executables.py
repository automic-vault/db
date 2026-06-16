from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from .brew import stable_version
from .common import CACHE_DIR, COMBINED_DIR, DEFAULT_TIMEOUT, DETERMINISTIC_DIR, USER_AGENT, fetch_url, read_json, write_json


TOKEN_SERVICE = "https://ghcr.io/token"
MANIFEST_ACCEPT = "application/vnd.oci.image.index.v1+json"
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


def executable_entries_from_index(index: dict[str, list[str]]) -> dict[str, str]:
    entries: dict[str, str] = {}
    for formula in sorted(index):
        for executable in sorted(set(index[formula])):
            entries.setdefault(executable, formula)
    return dict(sorted(entries.items()))


def _yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"")


def _simple_yaml_list(text: str, key: str) -> list[str]:
    result: list[str] = []
    lines = text.splitlines()
    in_list = False
    list_indent = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if not in_list:
            if stripped == f"{key}:":
                in_list = True
                list_indent = indent + 2
            continue
        if indent < list_indent:
            break
        if indent == list_indent and stripped.startswith("- "):
            item = _yaml_scalar(stripped[2:])
            if item:
                result.append(item)
    return sorted(set(result))


def _simple_yaml_scalar(text: str, key: str) -> str:
    prefix = f"{key}:"
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        return _yaml_scalar(stripped[len(prefix):])
    return ""


def executable_index_from_project_yaml(root=COMBINED_DIR) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not root.exists():
        return result
    for path in sorted(root.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        identifier = _simple_yaml_scalar(text, "id")
        if not identifier.startswith("brew:"):
            continue
        formula = identifier.split(":", 1)[1]
        executables = _simple_yaml_list(text, "executables")
        if formula and executables:
            result[formula] = executables
    return result


def seed_executables_from_source() -> dict[str, list[str]]:
    seeded = executable_index_from_project_yaml(COMBINED_DIR)
    if seeded:
        return seeded
    return executable_index_from_project_yaml(DETERMINISTIC_DIR)


def parse_exec_paths(paths: list[str]) -> list[str]:
    result = set()
    for entry in paths:
        if not entry:
            continue
        name = entry.strip().rsplit("/", 1)[-1]
        if name:
            result.add(name)
    return sorted(result)


def executables_from_formula(formula: dict[str, Any]) -> list[str]:
    return parse_exec_paths([item for item in formula.get("executables") or [] if isinstance(item, str)])


def executable_index_from_formulae(formulae: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for formula in formulae:
        name = formula.get("name")
        if not isinstance(name, str) or not name:
            continue
        executables = executables_from_formula(formula)
        if executables:
            result[name] = executables
    return result


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
    status, _, body = fetch_url(f"{TOKEN_SERVICE}?{query}", headers=headers, timeout=DEFAULT_TIMEOUT)
    if status >= 400:
        raise urllib.error.HTTPError(f"{TOKEN_SERVICE}?{query}", status, f"HTTP {status}", hdrs=None, fp=None)
    data = json.loads(body)
    token = str(data.get("token") or "")
    if token:
        _TOKENS[cache_key] = {"token": token, "expires_at": now + int(data.get("expires_in", 300)) - 10}
    return token


def fetch_manifest(url: str, *, refresh: bool = False) -> dict[str, Any] | None:
    parsed = urllib.parse.urlparse(url)
    repo = ghcr_repo_from_url(parsed.path)
    headers = {"Accept": MANIFEST_ACCEPT, "User-Agent": USER_AGENT}
    try:
        token = ghcr_bearer_token(repo) if repo else ""
    except Exception:
        return None
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
    try:
        status, response_headers, body = fetch_url(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if status == 304:
            return payload if isinstance(payload, dict) else None
        if status >= 400:
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", hdrs=None, fp=None)
        payload = json.loads(body)
        write_json(path, {META_KEY: {"etag": response_headers.get("etag"), "checked_at": int(time.time())}, PAYLOAD_KEY: payload})
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
    seeded = executable_index_from_formulae(formulae)
    seeded.update(seed_executables_from_source())
    if not fetch_manifests:
        return seeded
    result = {key: list(value) for key, value in seeded.items()}
    count = 0
    for formula in formulae:
        name = formula.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in result:
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
    write_json(CACHE_DIR / "brew" / "executable-entries.json", {
        "schema": 1,
        "provider": "brew",
        "entries": executable_entries_from_index(index),
    })


def read_executable_index() -> dict[str, list[str]]:
    payload = read_json(CACHE_DIR / "brew" / "executables.json", {})
    packages = payload.get("packages") if isinstance(payload, dict) else {}
    if not isinstance(packages, dict):
        return {}
    return {str(key): [str(item) for item in value if isinstance(item, str)] for key, value in packages.items() if isinstance(value, list)}
