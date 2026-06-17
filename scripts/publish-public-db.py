#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

try:
    from avdb_paths import COMBINED_JSON_PATH
except ModuleNotFoundError:
    from scripts.avdb_paths import COMBINED_JSON_PATH


DEFAULT_BUCKET = "automicvault.com"
DEFAULT_KEY = "db.json"
DEFAULT_PUBLIC_URL = "https://automicvault.com/db.json"
DEFAULT_CACHE_CONTROL = "public, no-cache"
DEFAULT_CONTENT_TYPE = "application/json; charset=utf-8"


class PublishFailed(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise PublishFailed(f"{path} does not exist") from err
    except json.JSONDecodeError as err:
        raise PublishFailed(f"{path} is not valid JSON: {err}") from err
    if not isinstance(payload, dict):
        raise PublishFailed(f"{path} must contain a JSON object")
    return payload


def generated_at(payload: dict[str, Any], source: str) -> str:
    value = payload.get("generated_at")
    if not isinstance(value, str) or not value:
        raise PublishFailed(f"{source} is missing generated_at")
    return value


def aws_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key.lstrip('/')}"


def upload_command(
    source: Path,
    bucket: str,
    key: str,
    *,
    cache_control: str,
    content_type: str,
) -> list[str]:
    return [
        "aws",
        "s3",
        "cp",
        str(source),
        aws_uri(bucket, key),
        "--content-type",
        content_type,
        "--cache-control",
        cache_control,
    ]


def run(command: list[str]) -> None:
    print("+", shlex.join(command), flush=True)
    subprocess.run(command, check=True)


def fetch_s3_json(bucket: str, key: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        output_path = Path(tmp) / "db.json"
        run(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key.lstrip("/"), str(output_path)])
        return read_json(output_path)


def fetch_public_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise PublishFailed(f"failed to read public {url}: {err}") from err


def verify_generated_at(expected: str, payload: dict[str, Any], source: str) -> None:
    actual = generated_at(payload, source)
    if actual != expected:
        raise PublishFailed(f"{source} generated_at is {actual}; expected {expected}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish the public Automic Vault /db.json payload to S3.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("AV_PUBLIC_DB_SOURCE", COMBINED_JSON_PATH)),
        help=f"Combined public JSON to publish. Defaults to {COMBINED_JSON_PATH}.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AV_PUBLIC_DB_BUCKET", DEFAULT_BUCKET),
        help=f"S3 bucket. Defaults to {DEFAULT_BUCKET}.",
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("AV_PUBLIC_DB_KEY", DEFAULT_KEY),
        help=f"S3 object key. Defaults to {DEFAULT_KEY}.",
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("AV_PUBLIC_DB_URL", DEFAULT_PUBLIC_URL),
        help=f"Public URL to verify. Defaults to {DEFAULT_PUBLIC_URL}.",
    )
    parser.add_argument(
        "--cache-control",
        default=os.environ.get("AV_PUBLIC_DB_CACHE_CONTROL", DEFAULT_CACHE_CONTROL),
        help=f"Cache-Control metadata. Defaults to {DEFAULT_CACHE_CONTROL}.",
    )
    parser.add_argument(
        "--content-type",
        default=os.environ.get("AV_PUBLIC_DB_CONTENT_TYPE", DEFAULT_CONTENT_TYPE),
        help=f"Content-Type metadata. Defaults to {DEFAULT_CONTENT_TYPE}.",
    )
    parser.add_argument(
        "--skip-public-check",
        action="store_true",
        help="Verify only the S3 object body, not the public CloudFront URL.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Do not upload; only verify S3 and public copies match the local source.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        source_payload = read_json(args.source)
        expected_generated_at = generated_at(source_payload, str(args.source))
        if not args.check_only:
            run(
                upload_command(
                    args.source,
                    args.bucket,
                    args.key,
                    cache_control=args.cache_control,
                    content_type=args.content_type,
                )
            )
        verify_generated_at(expected_generated_at, fetch_s3_json(args.bucket, args.key), aws_uri(args.bucket, args.key))
        if not args.skip_public_check:
            verify_generated_at(expected_generated_at, fetch_public_json(args.public_url), args.public_url)
    except (PublishFailed, subprocess.CalledProcessError) as err:
        print(json.dumps({"ok": False, "error": str(err)}, sort_keys=True), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "bucket": args.bucket,
                "key": args.key,
                "generated_at": expected_generated_at,
                "mode": "check" if args.check_only else "publish",
                "public_url": None if args.skip_public_check else args.public_url,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
