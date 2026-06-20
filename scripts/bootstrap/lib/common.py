from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import signal
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "cache"
STAGE_DIR = CACHE_DIR / "stage"
PROJECTS_DIR = ROOT / "projects"
DETERMINISTIC_DIR = ROOT / "deterministic"
AGENTS_DIR = ROOT / "agents"
AGENTS_JSON_DIR = CACHE_DIR / "agents-json"
HUMAN_OVERRIDE_DIR = ROOT / "human-override"
COMBINED_DIR = ROOT / "combined"
META_KEY = "__pkgdb_meta__"
PAYLOAD_KEY = "__pkgdb_payload__"
DEFAULT_TIMEOUT = 90
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
USER_AGENT = "av.db/1.0"
FETCH_ATTEMPTS = 3


@contextmanager
def hard_timeout(seconds: int):
    if seconds <= 0:
        yield
        return
    if signal.getsignal(signal.SIGALRM) is not signal.SIG_DFL:
        yield
        return

    def handle_alarm(_signum, _frame):
        raise TimeoutError(f"timed out after {seconds}s")

    previous_handler = signal.signal(signal.SIGALRM, handle_alarm)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def ensure_root() -> Path:
    os.chdir(ROOT)
    return ROOT


def git_commit_if_changed(message: str, paths: list[str | Path]) -> str | None:
    if not paths:
        return None
    path_args = [Path(path).as_posix() for path in paths]
    subprocess.run(["git", "add", "-A", "--", *path_args], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", *path_args], cwd=ROOT)
    if diff.returncode == 0:
        return None
    if diff.returncode != 1:
        diff.check_returncode()
    subprocess.run(
        ["git", "commit", "-m", message, "--", *path_args],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


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
        except BaseException:
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
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
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


def _parse_http_headers(raw_headers: str) -> tuple[int, dict[str, str]]:
    blocks = [block for block in re.split(r"\r?\n\r?\n", raw_headers) if block.strip()]
    for block in reversed(blocks):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or not lines[0].startswith("HTTP/"):
            continue
        status_match = re.match(r"HTTP/\S+\s+(\d+)", lines[0])
        status = int(status_match.group(1)) if status_match else 0
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return status, headers
    return 0, {}


def fetch_url(url: str, *, headers: dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> tuple[int, dict[str, str], bytes]:
    body_fd, body_name = tempfile.mkstemp(prefix="avdb-fetch-body-")
    header_fd, header_name = tempfile.mkstemp(prefix="avdb-fetch-headers-")
    os.close(body_fd)
    os.close(header_fd)
    try:
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--connect-timeout",
            str(timeout),
            "--max-time",
            str(timeout),
            "--dump-header",
            header_name,
            "--output",
            body_name,
        ]
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        command.append(url)
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            message = result.stderr.strip() or f"curl exited with {result.returncode}"
            raise TimeoutError(message) if result.returncode == 28 else OSError(message)
        status, response_headers = _parse_http_headers(Path(header_name).read_text(encoding="iso-8859-1"))
        return status, response_headers, Path(body_name).read_bytes()
    finally:
        Path(body_name).unlink(missing_ok=True)
        Path(header_name).unlink(missing_ok=True)


def github_api_endpoint(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname != "api.github.com":
        return None
    endpoint = parsed.path or "/"
    if parsed.query:
        endpoint = f"{endpoint}?{parsed.query}"
    return endpoint


def gh_api_error_status(stderr: str) -> int | None:
    marker = "HTTP "
    index = stderr.rfind(marker)
    if index == -1:
        return None
    status = stderr[index + len(marker) : index + len(marker) + 3]
    return int(status) if status.isdigit() else None


def fetch_github_api_bytes(url: str) -> bytes:
    endpoint = github_api_endpoint(url)
    if endpoint is None:
        raise ValueError(f"not a GitHub API URL: {url}")
    command = [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        "X-GitHub-Api-Version: 2022-11-28",
        endpoint,
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=DEFAULT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise urllib.error.URLError(err) from err
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        status = gh_api_error_status(stderr)
        if status is not None:
            raise urllib.error.HTTPError(
                url,
                status,
                stderr or f"HTTP {status}",
                hdrs=None,
                fp=None,
            )
        raise urllib.error.URLError(stderr or f"gh api exited with {result.returncode}")
    return result.stdout


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
    if urllib.parse.urlparse(url).hostname == "api.github.com":
        try:
            body = fetch_github_api_bytes(url)
            payload = json.loads(body)
            write_json(path, {META_KEY: {"etag": None, "checked_at": now}, PAYLOAD_KEY: payload})
            return payload
        except (json.JSONDecodeError, urllib.error.HTTPError, urllib.error.URLError):
            pass
    last_error: BaseException | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            status, response_headers, body = fetch_url(url, headers=headers)
            if status == 304 and payload is not None:
                write_json(path, {META_KEY: {"etag": etag, "checked_at": now}, PAYLOAD_KEY: payload})
                return payload
            if status >= 400:
                raise urllib.error.HTTPError(url, status, f"HTTP {status}", hdrs=None, fp=None)
            payload = json.loads(body)
            write_json(path, {META_KEY: {"etag": response_headers.get("etag"), "checked_at": now}, PAYLOAD_KEY: payload})
            return payload
        except (http.client.IncompleteRead, json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError) as err:
            if payload is not None:
                return payload
            last_error = err
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(2**attempt)
    if payload is not None:
        return payload
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to fetch {url}")


def fetch_bytes(url: str, *, namespace: str, refresh: bool = False) -> bytes:
    path = cache_path_for_url(url, namespace, ".data")
    cached_data = path.read_bytes() if path.exists() else None
    if cached_data is not None and not refresh:
        return cached_data

    if urllib.parse.urlparse(url).hostname == "api.github.com":
        try:
            data = fetch_github_api_bytes(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            if cached_data != data:
                write_bytes_if_changed(path, data)
            return data
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass

    last_error: BaseException | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            status, _, data = fetch_url(url, headers={"Accept": "*/*", "User-Agent": USER_AGENT})
            if status >= 400:
                raise urllib.error.HTTPError(url, status, f"HTTP {status}", hdrs=None, fp=None)
            break
        except urllib.error.HTTPError:
            raise
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, OSError) as err:
            if cached_data is not None:
                return cached_data
            last_error = err
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(2**attempt)
    else:
        if cached_data is not None:
            return cached_data
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"failed to fetch {url}")

    path.parent.mkdir(parents=True, exist_ok=True)
    if cached_data != data:
        write_bytes_if_changed(path, data)
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
    return write_bytes_if_changed(dst, data)


def write_bytes_if_changed(dst: Path, data: bytes) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.read_bytes() == data:
        return False
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(dst)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return True


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
