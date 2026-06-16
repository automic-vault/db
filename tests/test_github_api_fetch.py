import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.bootstrap.lib import common


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_db = load_script_module("build_db", ROOT / "scripts" / "build-db.py")
pkg_version_freshness = load_script_module(
    "pkg_version_freshness",
    ROOT / "scripts" / "generate-pkg-version-freshness.py",
)
pkg_manager_indexes = load_script_module(
    "pkg_manager_indexes",
    ROOT / "scripts" / "generate-pkg-manager-indexes.py",
)


class GithubApiFetchTests(unittest.TestCase):
    def test_common_github_api_bytes_uses_gh_api_endpoint(self):
        url = "https://api.github.com/repos/NixOS/nixpkgs/git/trees/master?recursive=1"
        completed = subprocess.CompletedProcess(
            ["gh", "api"],
            0,
            stdout=b'{"tree":[]}',
            stderr=b"",
        )

        with mock.patch.object(common.subprocess, "run", return_value=completed) as run:
            data = common.fetch_github_api_bytes(url)

        self.assertEqual(data, b'{"tree":[]}')
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["gh", "api"])
        self.assertEqual(command[-1], "/repos/NixOS/nixpkgs/git/trees/master?recursive=1")

    def test_manager_index_fetch_bytes_uses_gh_api_for_github_api_urls(self):
        url = "https://api.github.com/repos/ScoopInstaller/Main/git/trees/master?recursive=1"
        completed = subprocess.CompletedProcess(
            ["gh", "api"],
            0,
            stdout=b'{"tree":[]}',
            stderr=b"",
        )
        original_cache_dir = pkg_manager_indexes.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                pkg_manager_indexes.CACHE_DIR = Path(tmp)
                with mock.patch.object(pkg_manager_indexes.subprocess, "run", return_value=completed) as run:
                    data = pkg_manager_indexes.fetch_bytes(url, force_refresh=True)
            finally:
                pkg_manager_indexes.CACHE_DIR = original_cache_dir

        self.assertEqual(data, b'{"tree":[]}')
        self.assertEqual(
            run.call_args.args[0][-1],
            "/repos/ScoopInstaller/Main/git/trees/master?recursive=1",
        )

    def test_build_db_fetch_json_prefers_gh_api_for_github_api_urls(self):
        url = "https://api.github.com/repos/acme/tool"
        completed = subprocess.CompletedProcess(
            ["gh", "api"],
            0,
            stdout=json.dumps({"full_name": "acme/tool"}),
            stderr="",
        )
        original_cache_dir = build_db.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                build_db.CACHE_DIR = tmp
                with (
                    mock.patch.object(build_db.subprocess, "run", return_value=completed) as run,
                    mock.patch.object(build_db.urllib.request, "urlopen", side_effect=AssertionError("urlopen should not run")),
                ):
                    payload = build_db._fetch_json(url)
            finally:
                build_db.CACHE_DIR = original_cache_dir

        self.assertEqual(payload, {"full_name": "acme/tool"})
        self.assertEqual(run.call_args.args[0][-1], "/repos/acme/tool")

    def test_freshness_fetch_github_json_prefers_gh_api(self):
        url = "https://api.github.com/repos/acme/tool/releases/latest"
        completed = subprocess.CompletedProcess(
            ["gh", "api"],
            0,
            stdout=json.dumps({"tag_name": "v1.2.3"}),
            stderr="",
        )
        original_cache_dir = pkg_version_freshness.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                pkg_version_freshness.CACHE_DIR = Path(tmp)
                with (
                    mock.patch.object(pkg_version_freshness.subprocess, "run", return_value=completed) as run,
                    mock.patch.object(
                        pkg_version_freshness.urllib.request,
                        "urlopen",
                        side_effect=AssertionError("urlopen should not run"),
                    ),
                ):
                    payload = pkg_version_freshness.fetch_github_json(url, cache_only=False)
            finally:
                pkg_version_freshness.CACHE_DIR = original_cache_dir

        self.assertEqual(payload, {"tag_name": "v1.2.3"})
        self.assertEqual(run.call_args.args[0][-1], "/repos/acme/tool/releases/latest")


if __name__ == "__main__":
    unittest.main()
