from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from .common import fetch_json


FORMULA_URL = "https://formulae.brew.sh/api/formula.json"
GITLAB_HOSTS = {
    "0xacab.org",
    "code.sits.lu",
    "code.videolan.org",
    "framagit.org",
    "git.openldap.org",
    "invent.kde.org",
    "salsa.debian.org",
}


def fetch_formulae(*, refresh: bool = False) -> list[dict[str, Any]]:
    payload = fetch_json(FORMULA_URL, namespace="brew.sh", refresh=refresh)
    if not isinstance(payload, list):
        raise ValueError("Homebrew formula API payload must be a list")
    return [item for item in payload if isinstance(item, dict)]


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item:
            result.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("formula")
            if isinstance(name, str) and name:
                result.append(name)
    return sorted(set(result))


def normalize_license(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " AND ".join(str(item) for item in value if item)
    if not isinstance(value, dict):
        return ""
    if "any_of" in value:
        return " OR ".join(normalize_license(item) for item in value.get("any_of") or [] if normalize_license(item))
    if "all_of" in value:
        return " AND ".join(normalize_license(item) for item in value.get("all_of") or [] if normalize_license(item))
    if "with" in value:
        base = normalize_license(value.get("with"))
        exception = normalize_license(value.get("exception"))
        return f"{base} WITH {exception}" if base and exception else base
    return ""


def stable_version(formula: dict[str, Any]) -> str:
    versions = formula.get("versions") or {}
    value = versions.get("stable") if isinstance(versions, dict) else None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("version") or value.get("tag")
        return str(nested) if nested else ""
    return ""


def source_archive(formula: dict[str, Any]) -> str:
    urls = formula.get("urls") or {}
    stable = urls.get("stable") if isinstance(urls, dict) else None
    if not isinstance(stable, dict):
        return ""
    url = stable.get("url")
    return url if isinstance(url, str) else ""


def clean_summary(value: Any) -> str:
    text = html.unescape(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\b[^>]*>", ". ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"https?://\S*$", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(\.\s*){2,}", ". ", text).strip(" ,-")
    return text[:720].rsplit(" ", 1)[0].strip(" ,-") if len(text) > 720 else text


def normalize_repository(value: Any) -> str:
    url = ""
    if isinstance(value, str):
        url = value
    elif isinstance(value, dict):
        for key in ("url", "web", "browse"):
            child = value.get(key)
            if isinstance(child, str) and child.strip():
                url = child.strip()
                break
    if not url:
        return ""
    url = re.sub(r"^git\+", "", url)
    url = re.sub(r"^git://github\.com/", "https://github.com/", url)
    url = re.sub(r"^ssh://git@github\.com/", "https://github.com/", url)
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    url = re.sub(r"^git://gitlab\.com/", "https://gitlab.com/", url)
    url = re.sub(r"^ssh://git@gitlab\.com/", "https://gitlab.com/", url)
    url = re.sub(r"^git@gitlab\.com:", "https://gitlab.com/", url)
    return re.sub(r"\.git$", "", url)


def repository_project_url(value: str) -> str:
    github = re.match(r"^(https://github\.com/[^/]+/[^/]+)(?:/(?:archive|releases|refs|tags|tarball|zipball)(?:/.*)?)?$", value)
    if github:
        return github.group(1).removesuffix(".git")
    gitlab = re.match(r"^(https://[^/]*gitlab[^/]+/[^/]+/[^/]+)(?:/(?:-|archive|releases)(?:/.*)?)?$", value)
    if gitlab:
        return gitlab.group(1).removesuffix(".git")
    known_gitlab = re.match(r"^https://([^/]+)/([^/]+/[^/]+)(?:/(?:-|archive|releases)(?:/.*)?)?$", value)
    if known_gitlab and known_gitlab.group(1) in GITLAB_HOSTS:
        return f"https://{known_gitlab.group(1)}/{known_gitlab.group(2)}".removesuffix(".git")
    return ""


def repository_from_formula(formula: dict[str, Any]) -> str:
    repo = normalize_repository(formula.get("repository"))
    repo = repository_project_url(repo)
    if repo:
        return repo
    urls = formula.get("urls") or {}
    stable = urls.get("stable") if isinstance(urls, dict) else None
    if isinstance(stable, dict):
        archive = normalize_repository(stable.get("url"))
        return repository_project_url(archive)
    return ""


def tags_for_formula(formula: dict[str, Any]) -> list[str]:
    tags = {"cli"}
    name = str(formula.get("name") or "").lower()
    desc = str(formula.get("desc") or "").lower()
    for token in re.split(r"[^a-z0-9]+", f"{name} {desc}"):
        if token in {"aws", "cloud", "git", "json", "yaml", "kubernetes", "docker", "database", "security"}:
            tags.add(token)
    return sorted(tags)


def is_cli_formula(formula: dict[str, Any]) -> bool:
    if formula.get("disabled"):
        return False
    if formula.get("keg_only") and not formula.get("bottle"):
        return False
    name = formula.get("name")
    return isinstance(name, str) and bool(name.strip())


def formula_record(formula: dict[str, Any]) -> dict[str, Any] | None:
    if not is_cli_formula(formula):
        return None
    name = str(formula["name"])
    homepage = formula.get("homepage") if isinstance(formula.get("homepage"), str) else ""
    repo = repository_from_formula(formula)
    return {
        "id": f"brew:{name}",
        "display-name": name,
        "homepage": homepage,
        "repo": repo or None,
        "package-manager-url": f"https://formulae.brew.sh/formula/{urllib.parse.quote(name, safe='@+/')}",
        "version": stable_version(formula),
        "license": normalize_license(formula.get("license")),
        "tags": tags_for_formula(formula),
        "description": clean_summary(formula.get("desc")),
        "source-archive": source_archive(formula),
        "provenance": {
            "provider": "brew",
            "source": FORMULA_URL,
            "formula": name,
        },
    }
