from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .common import CACHE_DIR, CHECK_INTERVAL_SECONDS, DEFAULT_TIMEOUT, USER_AGENT, replace_if_changed, stable_hash, write_json


SCHEMA_VERSION = 1
CRATES_IO_DUMP_URL = "https://static.crates.io/db-dump.tar.gz"
CRATES_IO_CACHE_DIR = CACHE_DIR / "cratesio"
CRATES_IO_DUMP_PATH = CRATES_IO_CACHE_DIR / "db-dump.tar.gz"
CRATES_IO_DUMP_META_PATH = CRATES_IO_CACHE_DIR / "db-dump.meta.json"
CRATES_IO_INDEX_PATH = CRATES_IO_CACHE_DIR / "index.json"
CRATES_IO_DOWNLOAD_TIMEOUT = int(os.environ.get("CRATES_IO_DOWNLOAD_TIMEOUT", "3600"))
CRATES_IO_RECENT_WINDOW_DAYS = int(os.environ.get("CRATES_IO_RECENT_WINDOW_DAYS", "90"))
CRATES_IO_MIN_RECENT_DOWNLOADS = int(os.environ.get("CRATES_IO_MIN_RECENT_DOWNLOADS", "50000"))

SELECTED_DUMP_FILES = {
    "crates.csv",
    "crate_downloads.csv",
    "default_versions.csv",
    "version_downloads.csv",
    "versions.csv",
}


class CratesIndexError(Exception):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def valid_executable_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value in {".", ".."}:
        return False
    if "/" in value or "\\" in value:
        return False
    return not any(char.isspace() for char in value)


def parse_pg_text_array(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text or text == "{}":
        return []
    if not (text.startswith("{") and text.endswith("}")):
        return [text] if text else []
    inner = text[1:-1]
    if not inner:
        return []
    return [
        item
        for row in csv.reader([inner], quotechar='"', escapechar="\\")
        for item in row
        if item
    ]


def read_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_db_dump_meta(meta: dict[str, Any]) -> None:
    CRATES_IO_DUMP_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    CRATES_IO_DUMP_META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def download_db_dump(*, refresh: bool = False, url: str = CRATES_IO_DUMP_URL, output_path: Path = CRATES_IO_DUMP_PATH) -> tuple[Path, dict[str, Any]]:
    meta = read_json_file(CRATES_IO_DUMP_META_PATH, {}) or {}
    checked_at = parse_int(meta.get("checked_at"))
    now = int(time.time())
    if output_path.exists() and not refresh and checked_at and now - checked_at < CHECK_INTERVAL_SECONDS:
        return output_path, meta

    headers = {
        "Accept": "application/gzip, application/octet-stream;q=0.9, */*;q=0.1",
        "User-Agent": USER_AGENT,
    }
    if output_path.exists():
        if meta.get("etag"):
            headers["If-None-Match"] = str(meta["etag"])
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = str(meta["last_modified"])

    request = urllib.request.Request(url, headers=headers)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_path.parent, prefix=".db-dump.", suffix=".tmp")
    os.close(fd)
    Path(tmp_name).unlink(missing_ok=True)
    try:
        try:
            with urllib.request.urlopen(request, timeout=CRATES_IO_DOWNLOAD_TIMEOUT) as response:
                with open(tmp_name, "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                next_meta = {
                    "checked_at": now,
                    "content_length": response.headers.get("content-length"),
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "source_url": url,
                    "resolved_url": response.url,
                }
        except urllib.error.HTTPError as err:
            if err.code == 304 and output_path.exists():
                meta["checked_at"] = now
                write_db_dump_meta(meta)
                return output_path, meta
            if output_path.exists():
                meta["checked_at"] = now
                meta["last_error"] = f"HTTP {err.code}"
                write_db_dump_meta(meta)
                return output_path, meta
            raise
        except OSError as err:
            if output_path.exists():
                meta["checked_at"] = now
                meta["last_error"] = str(err)
                write_db_dump_meta(meta)
                return output_path, meta
            raise
        replace_if_changed(Path(tmp_name), output_path)
        write_db_dump_meta(next_meta)
        return output_path, next_meta
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def extract_selected_csvs(dump_path: Path, target_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    with tarfile.open(dump_path, mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            basename = Path(member.name).name
            if basename not in SELECTED_DUMP_FILES:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target = target_dir / basename
            with target.open("wb") as output:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            found[basename] = target
    missing = sorted(SELECTED_DUMP_FILES - set(found))
    if missing:
        raise CratesIndexError(f"crates.io dump missing expected files: {', '.join(missing)}")
    return found


def csv_rows(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def crate_downloads(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in csv_rows(path):
        crate_id = str(row.get("crate_id") or "").strip()
        if crate_id:
            result[crate_id] = parse_int(row.get("downloads"))
    return result


def default_versions(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in csv_rows(path):
        crate_id = str(row.get("crate_id") or "").strip()
        version_id = str(row.get("version_id") or "").strip()
        if crate_id and version_id:
            result[crate_id] = {
                "version_id": version_id,
                "num_versions": parse_int(row.get("num_versions")),
            }
    return result


def recent_version_downloads(path: Path, version_ids: set[str], *, window_days: int) -> dict[str, int]:
    cutoff = dt.date.today() - dt.timedelta(days=window_days)
    result: dict[str, int] = {}
    for row in csv_rows(path):
        version_id = str(row.get("version_id") or "").strip()
        if version_id not in version_ids:
            continue
        downloaded_at = parse_date(row.get("date"))
        if downloaded_at is not None and downloaded_at < cutoff:
            continue
        result[version_id] = result.get(version_id, 0) + parse_int(row.get("downloads"))
    return result


def latest_version_rows(path: Path, version_ids: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in csv_rows(path):
        version_id = str(row.get("id") or "").strip()
        if version_id in version_ids:
            result[version_id] = row
    return result


def clean_summary(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def source_archive_url(name: str, version: str) -> str:
    quoted_name = urllib.request.pathname2url(name)
    quoted_file = urllib.request.pathname2url(f"{name}-{version}.crate")
    return f"https://static.crates.io/crates/{quoted_name}/{quoted_file}"


def crate_record(
    crate: dict[str, Any],
    version: dict[str, Any],
    *,
    all_time_downloads: int,
    recent_downloads: int,
    num_versions: int,
    rank: int,
    recent_window_days: int,
) -> dict[str, Any]:
    name = str(crate.get("name") or "").strip()
    version_num = str(version.get("num") or "").strip()
    executables = [
        {
            "name": executable,
            "kind": "binary",
            "exposure": "cargo-installed executable",
            "note": "Declared by crates.io version metadata.",
        }
        for executable in parse_pg_text_array(version.get("bin_names"))
        if valid_executable_name(executable)
    ]
    metadata: dict[str, Any] = {
        "summary": clean_summary(version.get("description") or crate.get("description")),
        "homepage": str(version.get("homepage") or crate.get("homepage") or ""),
        "repository": str(version.get("repository") or crate.get("repository") or ""),
        "upstreamDocs": str(version.get("documentation") or crate.get("documentation") or ""),
        "version": version_num,
        "license": str(version.get("license") or ""),
        "sourceArchive": source_archive_url(name, version_num) if name and version_num else "",
        "sha256": str(version.get("checksum") or ""),
        "publishedAt": str(version.get("created_at") or ""),
        "last_updated_at": str(version.get("updated_at") or crate.get("updated_at") or ""),
        "created_at": str(crate.get("created_at") or ""),
        "executables": executables,
        "packageManager": "Cargo",
        "packageManagerUrl": f"https://crates.io/crates/{urllib.request.pathname2url(name)}",
        "registryInsights": {
            "sourceDatabase": "crates.io database dump",
            "crateId": parse_int(crate.get("id")),
            "versionId": parse_int(version.get("id")),
            "numVersions": num_versions,
            "hasLib": str(version.get("has_lib") or "").lower() == "t",
            "rustVersion": str(version.get("rust_version") or ""),
            "edition": str(version.get("edition") or ""),
            "crateSize": parse_int(version.get("crate_size")),
            "recentDownloadWindowDays": recent_window_days,
        },
        "popularity": {
            "downloads": all_time_downloads,
            "recent_downloads": recent_downloads,
            "recent_download_window_days": recent_window_days,
            "rank": rank,
        },
    }
    return {key: value for key, value in metadata.items() if value not in ("", [], {}, None)}


def build_index_from_dump(
    dump_path: Path,
    *,
    min_recent_downloads: int = CRATES_IO_MIN_RECENT_DOWNLOADS,
    recent_window_days: int = CRATES_IO_RECENT_WINDOW_DAYS,
    dump_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="avdb-cratesio-") as tmp:
        files = extract_selected_csvs(dump_path, Path(tmp))
        crates_by_id = {
            str(row.get("id") or "").strip(): row
            for row in csv_rows(files["crates.csv"])
            if str(row.get("id") or "").strip() and str(row.get("name") or "").strip()
        }
        downloads_by_id = crate_downloads(files["crate_downloads.csv"])
        defaults_by_crate = default_versions(files["default_versions.csv"])
        default_version_ids = {
            str(item["version_id"])
            for item in defaults_by_crate.values()
            if item.get("version_id")
        }
        versions_by_id = latest_version_rows(files["versions.csv"], default_version_ids)
        recent_by_version = recent_version_downloads(
            files["version_downloads.csv"],
            default_version_ids,
            window_days=recent_window_days,
        )

    candidates: list[tuple[str, int, int, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for crate_id, crate in crates_by_id.items():
        default = defaults_by_crate.get(crate_id)
        if not default:
            continue
        version = versions_by_id.get(str(default["version_id"]))
        if not version or str(version.get("yanked") or "").lower() == "t":
            continue
        executables = [
            item
            for item in parse_pg_text_array(version.get("bin_names"))
            if valid_executable_name(item)
        ]
        if not executables:
            continue
        recent_downloads = recent_by_version.get(str(default["version_id"]), 0)
        if recent_downloads < min_recent_downloads:
            continue
        all_time_downloads = downloads_by_id.get(crate_id, parse_int(version.get("downloads")))
        name = str(crate.get("name") or "").strip()
        candidates.append((name, recent_downloads, all_time_downloads, crate, version, default))

    candidates.sort(key=lambda item: (-item[1], -item[2], item[0].lower()))
    crates: dict[str, Any] = {}
    for rank, (name, recent_downloads, all_time_downloads, crate, version, default) in enumerate(candidates, start=1):
        crates[name] = crate_record(
            crate,
            version,
            all_time_downloads=all_time_downloads,
            recent_downloads=recent_downloads,
            num_versions=parse_int(default.get("num_versions")),
            rank=rank,
            recent_window_days=recent_window_days,
        )

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "description": "crates.io CLI package index generated from the daily database dump.",
        "source": {
            "url": CRATES_IO_DUMP_URL,
            "dump": dump_meta or {},
            "selected_files": sorted(SELECTED_DUMP_FILES),
            "min_recent_downloads": min_recent_downloads,
            "recent_window_days": recent_window_days,
            "definition_hash": stable_hash({
                "schema": SCHEMA_VERSION,
                "selected_files": sorted(SELECTED_DUMP_FILES),
                "min_recent_downloads": min_recent_downloads,
                "recent_window_days": recent_window_days,
            }),
        },
        "crates": crates,
    }


def build_crates_index(
    *,
    refresh: bool = False,
    dump_path: Path | None = None,
    output_path: Path = CRATES_IO_INDEX_PATH,
    min_recent_downloads: int = CRATES_IO_MIN_RECENT_DOWNLOADS,
    recent_window_days: int = CRATES_IO_RECENT_WINDOW_DAYS,
) -> dict[str, Any]:
    if dump_path is None:
        dump_path, dump_meta = download_db_dump(refresh=refresh)
    else:
        dump_meta = {"source_url": dump_path.as_posix(), "checked_at": int(time.time())}
    index = build_index_from_dump(
        dump_path,
        min_recent_downloads=min_recent_downloads,
        recent_window_days=recent_window_days,
        dump_meta=dump_meta,
    )
    write_json(output_path, index)
    return index
