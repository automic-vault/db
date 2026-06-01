from __future__ import annotations

import gzip
import io
import json
import lzma
import re
import sqlite3
import subprocess
import tarfile
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from typing import Any

from .common import fetch_bytes, stable_hash, stable_hash_bytes


SCHEMA_VERSION = 1
ALLOWED_PLATFORMS = {"macos", "linux", "windows", "portable"}
SOURCE_BACKED_MANAGER_CONFIDENCE = {
    "macports": 0.94,
    "nix": 0.92,
    "ubuntu": 0.92,
    "debian": 0.92,
    "dnf": 0.92,
    "pacman": 0.92,
    "apk": 0.92,
    "zypper": 0.92,
    "winget": 0.92,
    "chocolatey": 0.92,
    "scoop": 0.92,
}


MANAGER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "macports": {
        "display_name": "MacPorts",
        "platform": "macos",
        "command_template": "sudo port install {id}",
        "source_label": "MacPorts ports tree",
        "urls": ["https://api.github.com/repos/macports/macports-ports/git/trees/master?recursive=1"],
    },
    "nix": {
        "display_name": "Nix",
        "platform": "linux",
        "command_template": "nix profile install nixpkgs#{id}",
        "source_label": "nixpkgs package indexes",
        "urls": [
            "https://api.github.com/repos/NixOS/nixpkgs/git/trees/master?recursive=1",
            "https://raw.githubusercontent.com/NixOS/nixpkgs/master/pkgs/top-level/all-packages.nix",
        ],
    },
    "ubuntu": {
        "display_name": "Ubuntu apt",
        "platform": "linux",
        "command_template": "sudo apt install {id}",
        "source_label": "Ubuntu 24.04 LTS package indexes",
        "urls": [
            "https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.gz",
            "https://archive.ubuntu.com/ubuntu/dists/noble/universe/binary-amd64/Packages.gz",
            "https://archive.ubuntu.com/ubuntu/dists/noble/multiverse/binary-amd64/Packages.gz",
            "https://archive.ubuntu.com/ubuntu/dists/noble/restricted/binary-amd64/Packages.gz",
        ],
    },
    "debian": {
        "display_name": "Debian apt",
        "platform": "linux",
        "command_template": "sudo apt install {id}",
        "source_label": "Debian stable package indexes",
        "urls": [
            "https://deb.debian.org/debian/dists/stable/main/binary-amd64/Packages.xz",
            "https://deb.debian.org/debian/dists/stable/contrib/binary-amd64/Packages.xz",
            "https://deb.debian.org/debian/dists/stable/non-free/binary-amd64/Packages.xz",
            "https://deb.debian.org/debian/dists/stable/non-free-firmware/binary-amd64/Packages.xz",
        ],
    },
    "dnf": {
        "display_name": "dnf",
        "platform": "linux",
        "command_template": "sudo dnf install {id}",
        "source_label": "Fedora Rawhide package metadata",
        "urls": ["https://dl.fedoraproject.org/pub/fedora/linux/development/rawhide/Everything/x86_64/os/repodata/repomd.xml"],
    },
    "pacman": {
        "display_name": "pacman",
        "platform": "linux",
        "command_template": "sudo pacman -S {id}",
        "source_label": "Arch Linux sync databases",
        "urls": [
            "https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db.tar.gz",
            "https://geo.mirror.pkgbuild.com/extra/os/x86_64/extra.db.tar.gz",
        ],
    },
    "apk": {
        "display_name": "apk",
        "platform": "linux",
        "command_template": "sudo apk add {id}",
        "source_label": "Alpine Linux edge package indexes",
        "urls": [
            "https://dl-cdn.alpinelinux.org/alpine/edge/main/x86_64/APKINDEX.tar.gz",
            "https://dl-cdn.alpinelinux.org/alpine/edge/community/x86_64/APKINDEX.tar.gz",
            "https://dl-cdn.alpinelinux.org/alpine/edge/testing/x86_64/APKINDEX.tar.gz",
        ],
    },
    "zypper": {
        "display_name": "zypper",
        "platform": "linux",
        "command_template": "sudo zypper install {id}",
        "source_label": "openSUSE Tumbleweed package metadata",
        "urls": ["https://download.opensuse.org/tumbleweed/repo/oss/repodata/repomd.xml"],
    },
    "winget": {
        "display_name": "winget",
        "platform": "windows",
        "command_template": "winget install --id {id} -e",
        "source_label": "Windows Package Manager source index",
        "urls": ["https://cdn.winget.microsoft.com/cache/source.msix"],
    },
    "chocolatey": {
        "display_name": "Chocolatey",
        "platform": "windows",
        "command_template": "choco install {id}",
        "source_label": "Chocolatey community package catalog",
        "urls": ["https://community.chocolatey.org/api/v2/Packages()?$filter=IsLatestVersion&$select=Id&$top=1000"],
        "max_pages": 80,
    },
    "scoop": {
        "display_name": "Scoop",
        "platform": "windows",
        "command_template": "scoop install {id}",
        "source_label": "Scoop official bucket manifest trees",
        "urls": [
            "https://api.github.com/repos/ScoopInstaller/Main/git/trees/master?recursive=1",
            "https://api.github.com/repos/ScoopInstaller/Extras/git/trees/master?recursive=1",
            "https://api.github.com/repos/ScoopInstaller/Versions/git/trees/master?recursive=1",
        ],
    },
}


PACKAGE_ALIAS_MATCHES: dict[str, dict[str, list[str]]] = {
    "node": {"ubuntu": ["nodejs"], "debian": ["nodejs"], "dnf": ["nodejs", "nodejs24"], "pacman": ["nodejs"], "apk": ["nodejs"], "zypper": ["nodejs", "nodejs24"], "nix": ["nodejs"], "macports": ["nodejs24"], "winget": ["OpenJS.NodeJS"], "chocolatey": ["nodejs"], "scoop": ["main/nodejs"]},
    "postgresql": {"ubuntu": ["postgresql-client"], "debian": ["postgresql-client"], "apk": ["postgresql-client"]},
    "openssl@3": {"apk": ["libssl3"], "macports": ["openssl3"], "zypper": ["openssl-3"]},
}


def normalize_match(value: str) -> str:
    value = value.lower().strip().removeprefix("@")
    value = re.sub(r"[@_/+.]+", "-", value)
    value = re.sub(r"[^a-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def maybe_decompress(data: bytes, url: str = "") -> bytes:
    if url.endswith(".gz") or data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    if url.endswith(".xz") or data.startswith(b"\xfd7zXZ\x00"):
        return lzma.decompress(data)
    if url.endswith(".zst") or data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard

            return zstandard.ZstdDecompressor().decompress(data)
        except ImportError:
            try:
                return subprocess.run(
                    ["zstd", "-dcq"],
                    input=data,
                    check=True,
                    capture_output=True,
                ).stdout
            except FileNotFoundError as err:
                raise RuntimeError("zstd-compressed package metadata requires the zstandard Python module or zstd executable") from err
    return data


def package_stanzas(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key = ""
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if current:
                result.append(current)
                current = {}
                last_key = ""
            continue
        if line.startswith((" ", "\t")) and last_key:
            current[last_key] = f"{current[last_key]}\n{line.strip()}"
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        last_key = key.strip()
        current[last_key] = value.strip()
    if current:
        result.append(current)
    return result


def record(package_id: str, *, match_names: list[str] | None = None, source_url: str = "", source_name: str = "") -> dict[str, Any]:
    names = [package_id, *(match_names or [])]
    return {
        "id": package_id,
        "match_names": sorted({normalize_match(name) for name in names if normalize_match(name)}),
        "source_name": source_name or package_id,
        "source_url": source_url,
    }


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        package_id = str(item.get("id") or "").strip()
        if not package_id:
            continue
        existing = result.setdefault(package_id, {"id": package_id, "match_names": [], "source_name": item.get("source_name") or package_id, "source_url": item.get("source_url") or ""})
        existing["match_names"] = sorted(set(existing.get("match_names") or []) | set(item.get("match_names") or []))
    return [result[key] for key in sorted(result)]


def parse_debian_packages(data: bytes, source_url: str) -> list[dict[str, Any]]:
    text = maybe_decompress(data, source_url).decode("utf-8", errors="replace")
    return dedupe_records([record(stanza["Package"].strip(), source_url=source_url) for stanza in package_stanzas(text) if stanza.get("Package")])


def parse_pacman_db(data: bytes, source_url: str) -> list[dict[str, Any]]:
    records = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith("/desc"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            match = re.search(r"^%NAME%\n([^\n]+)", handle.read().decode("utf-8", errors="replace"), flags=re.MULTILINE)
            if match:
                records.append(record(match.group(1).strip(), source_url=source_url))
    return dedupe_records(records)


def parse_apk_index(data: bytes, source_url: str) -> list[dict[str, Any]]:
    records = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if member.name.endswith("APKINDEX") and member.isfile():
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                for stanza in handle.read().decode("utf-8", errors="replace").split("\n\n"):
                    for line in stanza.splitlines():
                        if line.startswith("P:") and line[2:].strip():
                            records.append(record(line[2:].strip(), source_url=source_url))
                            break
    return dedupe_records(records)


def parse_repomd_primary_location(data: bytes, base_url: str) -> str:
    root = ET.fromstring(data)
    namespace = {"repo": "http://linux.duke.edu/metadata/repo"}
    for item in root.findall("repo:data", namespace):
        if item.get("type") == "primary":
            location = item.find("repo:location", namespace)
            href = location.get("href") if location is not None else ""
            if href:
                return urllib.parse.urljoin(base_url.rsplit("/repodata/", 1)[0] + "/", href)
    raise ValueError("repomd.xml did not contain primary metadata")


def parse_rpm_primary(data: bytes, source_url: str) -> list[dict[str, Any]]:
    text = maybe_decompress(data, source_url)
    records = []
    for _event, element in ET.iterparse(io.BytesIO(text), events=("end",)):
        if element.tag.endswith("package"):
            if element.get("type") in {"rpm", None}:
                for child in element:
                    if child.tag.endswith("name") and child.text:
                        records.append(record(child.text.strip(), source_url=source_url))
                        break
            element.clear()
    return dedupe_records(records)


def parse_github_tree(data: bytes, source_url: str, manager: str) -> list[dict[str, Any]]:
    payload = json.loads(data.decode("utf-8"))
    tree = payload.get("tree") if isinstance(payload, dict) else []
    records = []
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if manager == "macports" and path.endswith("/Portfile") and path.count("/") >= 2:
            records.append(record(path.rsplit("/", 2)[-2], source_url=source_url, source_name=path))
        elif manager == "nix":
            match = re.fullmatch(r"pkgs/by-name/[^/]+/([^/]+)/package\.nix", path)
            if match:
                records.append(record(match.group(1), source_url=source_url, source_name=path))
        elif manager == "scoop" and re.fullmatch(r"(?:bucket/)?[^/]+\.json", path):
            bucket = "main" if "ScoopInstaller/Main" in source_url else "extras" if "ScoopInstaller/Extras" in source_url else "versions"
            records.append(record(f"{bucket}/{path.rsplit('/', 1)[-1][:-5]}", match_names=[path.rsplit("/", 1)[-1][:-5]], source_url=source_url, source_name=path))
    return dedupe_records(records)


def parse_nix_all_packages(data: bytes, source_url: str) -> list[dict[str, Any]]:
    text = data.decode("utf-8", errors="replace")
    return dedupe_records([record(match.group(1), source_url=source_url, source_name=match.group(1)) for match in re.finditer(r"^\s{2}([A-Za-z0-9_+.-]+)\s*=", text, flags=re.MULTILINE) if not match.group(1).startswith("_")])


def parse_winget_source_msix(data: bytes, source_url: str) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        db_data = archive.read("Public/index.db")
    with tempfile.NamedTemporaryFile(suffix=".db") as database:
        database.write(db_data)
        database.flush()
        connection = sqlite3.connect(database.name)
        try:
            rows = connection.execute("SELECT DISTINCT ids.id, names.name, monikers.moniker FROM manifest JOIN ids ON ids.rowid = manifest.id JOIN names ON names.rowid = manifest.name JOIN monikers ON monikers.rowid = manifest.moniker")
            return dedupe_records([record(str(package_id).strip(), match_names=[str(name or ""), str(moniker or "")], source_url=source_url, source_name=str(package_id).strip()) for package_id, name, moniker in rows if str(package_id or "").strip()])
        finally:
            connection.close()


def parse_chocolatey_atom(data: bytes, source_url: str) -> tuple[list[dict[str, Any]], str]:
    root = ET.fromstring(data)
    atom = {"atom": "http://www.w3.org/2005/Atom", "d": "http://schemas.microsoft.com/ado/2007/08/dataservices"}
    records = []
    for entry in root.findall("atom:entry", atom):
        content_id = entry.find(".//d:Id", atom)
        title = entry.find("atom:title", atom)
        package_id = content_id.text.strip() if content_id is not None and content_id.text else title.text.strip() if title is not None and title.text else ""
        if package_id:
            records.append(record(package_id, source_url=source_url))
    next_node = root.find("atom:link[@rel='next']", atom)
    return dedupe_records(records), next_node.get("href") if next_node is not None else ""


def build_records_for_manager(manager: str, urls: list[str], *, refresh: bool = False, max_pages: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    source_fingerprints: list[dict[str, str]] = []
    for url in urls:
        data = fetch_bytes(url, namespace="pkg-manager-indexes", refresh=refresh)
        source_fingerprints.append({"url": url, "sha256": stable_hash_bytes(data)})
        if manager == "macports":
            records.extend(parse_github_tree(data, url, manager))
        elif manager == "nix":
            records.extend(parse_nix_all_packages(data, url) if url.endswith("all-packages.nix") else parse_github_tree(data, url, manager))
        elif manager in {"ubuntu", "debian"}:
            records.extend(parse_debian_packages(data, url))
        elif manager == "pacman":
            records.extend(parse_pacman_db(data, url))
        elif manager == "apk":
            records.extend(parse_apk_index(data, url))
        elif manager in {"dnf", "zypper"}:
            primary_url = parse_repomd_primary_location(data, url)
            primary_data = fetch_bytes(primary_url, namespace="pkg-manager-indexes", refresh=refresh)
            source_fingerprints.append({"url": primary_url, "sha256": stable_hash_bytes(primary_data)})
            records.extend(parse_rpm_primary(primary_data, primary_url))
        elif manager == "winget":
            records.extend(parse_winget_source_msix(data, url))
        elif manager == "scoop":
            records.extend(parse_github_tree(data, url, manager))
        elif manager == "chocolatey":
            next_url = url
            seen_pages = set()
            page_count = 0
            while next_url and next_url not in seen_pages:
                if max_pages is not None and page_count >= max_pages:
                    break
                page_count += 1
                seen_pages.add(next_url)
                page_data = data if next_url == url else fetch_bytes(next_url, namespace="pkg-manager-indexes", refresh=refresh)
                if next_url != url:
                    source_fingerprints.append({"url": next_url, "sha256": stable_hash_bytes(page_data)})
                page_records, next_url = parse_chocolatey_atom(page_data, next_url)
                records.extend(page_records)
    return dedupe_records(records), source_fingerprints


def apply_alias_matches(managers: dict[str, Any]) -> None:
    for local_name, by_manager in PACKAGE_ALIAS_MATCHES.items():
        for manager, package_ids in by_manager.items():
            packages = managers.get(manager, {}).get("packages")
            if not isinstance(packages, dict):
                continue
            for package_id in package_ids:
                item = packages.get(package_id)
                if isinstance(item, dict):
                    item["match_names"] = sorted(set(item.get("match_names") or []) | {normalize_match(local_name)})


def build_manager_indexes(*, refresh: bool = False) -> dict[str, Any]:
    managers: dict[str, Any] = {}
    for manager, definition in MANAGER_DEFINITIONS.items():
        records, source_fingerprints = build_records_for_manager(manager, definition["urls"], refresh=refresh, max_pages=definition.get("max_pages"))
        managers[manager] = {
            "display_name": definition["display_name"],
            "platform": definition["platform"],
            "command_template": definition["command_template"],
            "source_label": definition["source_label"],
            "sources": source_fingerprints,
            "packages": {item["id"]: item for item in records},
        }
    apply_alias_matches(managers)
    return {
        "schema": SCHEMA_VERSION,
        "description": "Compact package-manager database indexes used to emit source-backed install commands.",
        "definition_hash": stable_hash(MANAGER_DEFINITIONS),
        "alias_hash": stable_hash(PACKAGE_ALIAS_MATCHES),
        "managers": managers,
    }


def command(
    platform: str,
    manager_key: str,
    display_name: str,
    package_id: str,
    value: str,
    confidence: float,
    evidence: str,
) -> dict[str, Any]:
    return {
        "platform": platform,
        "manager_key": manager_key,
        "display_name": display_name,
        "manager": display_name,
        "package_id": package_id,
        "command": value,
        "confidence": round(float(confidence), 2),
        "evidence": evidence,
    }


def manager_matcher(manager_indexes: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for manager_key, definition in (manager_indexes.get("managers") or {}).items():
        packages = definition.get("packages") if isinstance(definition, dict) else None
        if not isinstance(packages, dict):
            continue
        display_name = str(definition.get("display_name") or manager_key)
        platform = str(definition.get("platform") or "")
        command_template = str(definition.get("command_template") or "")
        source_label = str(definition.get("source_label") or "")
        if platform not in ALLOWED_PLATFORMS or "{id}" not in command_template:
            continue
        for package_id, package in packages.items():
            if not isinstance(package, dict):
                continue
            install_id = str(package.get("id") or package_id).strip()
            match_names = package.get("match_names")
            if not install_id or not isinstance(match_names, list):
                continue
            source_url = str(package.get("source_url") or "")
            source_name = str(package.get("source_name") or install_id)
            evidence = f"{source_label}: {source_name} from {source_url}" if source_url else f"{source_label}: {source_name}"
            item = command(
                platform,
                str(manager_key),
                display_name,
                install_id,
                command_template.format(id=install_id),
                SOURCE_BACKED_MANAGER_CONFIDENCE.get(str(manager_key), 0.9),
                evidence,
            )
            for match_name in match_names:
                normalized = normalize_match(str(match_name))
                if normalized:
                    result[normalized].append(item)
    return {key: dedupe_commands(items) for key, items in result.items()}


def versioned_name_tiers(name: str) -> list[list[str]]:
    if "@" not in name:
        return []
    base, version = name.split("@", 1)
    version_digits = re.sub(r"[^0-9]+", "", version)
    version_hyphen = re.sub(r"[^0-9]+", "-", version).strip("-")
    specific = [
        name,
        f"{base}{version_digits}" if version_digits else "",
        f"{base}{version_hyphen}" if version_hyphen else "",
        f"{base}-{version_hyphen}" if version_hyphen else "",
        f"{base}{version.replace('-', '.')}",
    ]
    if base == "python" and version_hyphen:
        specific.extend([
            f"python{version_digits}" if version_digits else "",
            f"python3{version_digits[1:]}" if version_digits.startswith("3") else "",
            f"python{version.replace('-', '.')}",
            f"python3.{version_digits[1:]}" if version_digits.startswith("3") else "",
            f"python3-{version_digits[1:]}" if version_digits.startswith("3") else "",
        ])
    if base == "node" and version_digits:
        specific.extend([f"nodejs{version_digits}", f"nodejs-{version_digits}"])
    if base == "openssl" and version_digits:
        specific.extend([f"openssl{version_digits}", f"openssl-{version_digits}", f"libssl{version_digits}"])
    return [dedupe_match_names(specific), dedupe_match_names([base])]


def dedupe_match_names(values: list[Any]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        normalized = normalize_match(str(value or ""))
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def package_manager_routes(name: str, executables: list[str], matcher: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    tiers = versioned_name_tiers(name)
    executable_tier = dedupe_match_names([name, *executables])
    search_tiers: list[tuple[str, list[str]]] = []
    if tiers:
        search_tiers.append(("exact", tiers[0]))
        if executable_tier:
            search_tiers.append(("exact", executable_tier))
        for tier in tiers[1:]:
            search_tiers.append(("fallback", tier))
    elif executable_tier:
        search_tiers.append(("exact", executable_tier))
    result = []
    seen_managers = set()
    for match_tier, tier in search_tiers:
        for normalized in tier:
            for item in matcher.get(normalized) or []:
                manager = item.get("manager_key") or item.get("manager")
                if manager and manager not in seen_managers:
                    seen_managers.add(manager)
                    routed = dict(item)
                    routed["match_tier"] = match_tier
                    result.append(routed)
    return dedupe_commands(result)


def source_backed_commands(name: str, executables: list[str], matcher: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return package_manager_routes(name, executables, matcher)


def dedupe_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in commands:
        key = (item.get("platform"), item.get("manager_key") or item.get("manager"), item.get("command"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
