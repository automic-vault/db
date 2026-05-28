from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "cache"
STAGE_DIR = CACHE_DIR / "stage"
PROJECTS_DIR = ROOT / "projects"
META_KEY = "__pkgdb_meta__"
PAYLOAD_KEY = "__pkgdb_payload__"
DEFAULT_TIMEOUT = 90
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
USER_AGENT = "av.db/1.0"


def ensure_root() -> Path:
    os.chdir(ROOT)
    return ROOT


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def stable_hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return json.load(handle)
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def write_json(path: Path, value: Any, *, gzip_output: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gzip_output or path.suffix == ".gz":
        data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as handle:
                    handle.write(data)
            return replace_if_changed(Path(tmp_name), path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    data = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return write_text_if_changed(path, data)


def write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    Path(tmp_name).replace(path)
    return True


def replace_if_changed(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and src.read_bytes() == dst.read_bytes():
        src.unlink()
        return False
    src.replace(dst)
    return True


def cache_path_for_url(url: str, namespace: str, suffix: str = ".json") -> Path:
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix or suffix
    if parsed.path.endswith(".tar.gz"):
        ext = ".tar.gz"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    host = parsed.netloc.replace(":", "-")
    return CACHE_DIR / namespace / f"{host}-{digest[:24]}{ext}"


def read_cached_payload(path: Path) -> tuple[Any, dict[str, Any]]:
    data = read_json(path)
    if isinstance(data, dict) and META_KEY in data and PAYLOAD_KEY in data:
        return data.get(PAYLOAD_KEY), data.get(META_KEY) or {}
    return data, {}


def fetch_json(url: str, *, namespace: str, refresh: bool = False) -> Any:
    path = cache_path_for_url(url, namespace, ".json")
    payload = None
    meta: dict[str, Any] = {}
    if path.exists():
        payload, meta = read_cached_payload(path)
    checked_at = meta.get("checked_at")
    now = int(time.time())
    if payload is not None and not refresh and isinstance(checked_at, int) and now - checked_at < CHECK_INTERVAL_SECONDS:
        return payload
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    etag = meta.get("etag")
    if etag:
        headers["If-None-Match"] = str(etag)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            payload = json.loads(response.read())
            write_json(path, {META_KEY: {"etag": response.headers.get("etag"), "checked_at": now}, PAYLOAD_KEY: payload})
            return payload
    except urllib.error.HTTPError as err:
        if err.code == 304 and payload is not None:
            write_json(path, {META_KEY: {"etag": etag, "checked_at": now}, PAYLOAD_KEY: payload})
            return payload
        if payload is not None:
            return payload
        raise
    except urllib.error.URLError:
        if payload is not None:
            return payload
        raise


def fetch_bytes(url: str, *, namespace: str, refresh: bool = False) -> bytes:
    path = cache_path_for_url(url, namespace, ".data")
    if path.exists() and not refresh:
        return path.read_bytes()
    request = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        data = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return data


def sync_tree(staged_root: Path, published_root: Path) -> None:
    published_root.mkdir(parents=True, exist_ok=True)
    staged_files = {path.relative_to(staged_root) for path in staged_root.rglob("*") if path.is_file()}
    for path in sorted(published_root.rglob("*"), reverse=True):
        rel = path.relative_to(published_root)
        if path.is_file() and rel not in staged_files:
            path.unlink()
    for rel in sorted(staged_files):
        copy_if_changed(staged_root / rel, published_root / rel)
    for path in sorted(published_root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def copy_if_changed(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    if dst.exists() and dst.read_bytes() == data:
        return False
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp")
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    Path(tmp_name).replace(dst)
    return True


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
