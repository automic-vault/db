import http.client
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts.bootstrap.lib import common


class CommonGitTests(unittest.TestCase):
    def test_git_commit_if_changed_commits_only_requested_paths(self):
        original_root = common.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
            (root / "data").mkdir()
            (root / "other").mkdir()
            (root / "data" / "record.yml").write_text("name: old\n", encoding="utf-8")
            (root / "other" / "note.txt").write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            (root / "data" / "record.yml").write_text("name: new\n", encoding="utf-8")
            (root / "other" / "note.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "other/note.txt"], cwd=root, check=True)

            try:
                common.ROOT = root
                commit = common.git_commit_if_changed("nightly: test data", ["data"])
            finally:
                common.ROOT = original_root

            self.assertIsNotNone(commit)
            committed = subprocess.run(
                ["git", "show", "--name-only", "--format=", "HEAD"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            staged = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(committed, ["data/record.yml"])
            self.assertEqual(staged, ["other/note.txt"])

    def test_git_commit_if_changed_returns_none_without_changes(self):
        original_root = common.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
            (root / "data").mkdir()
            (root / "data" / "record.yml").write_text("name: old\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            try:
                common.ROOT = root
                commit = common.git_commit_if_changed("nightly: test data", ["data"])
            finally:
                common.ROOT = original_root

            self.assertIsNone(commit)

    def test_git_commit_if_changed_preserves_only_preexisting_dirty_paths(self):
        original_root = common.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
            (root / "combined").mkdir()
            (root / "combined" / "old.yml").write_text("name: old\n", encoding="utf-8")
            (root / "combined" / "new.yml").write_text("version: 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            try:
                common.ROOT = root
                (root / "combined" / "old.yml").write_text("name: dirty\n", encoding="utf-8")
                preserved_tracked_dirty, preserved_untracked_dirty = common.git_dirty_paths(["combined"])
                (root / "combined" / "new.yml").write_text("version: 2\n", encoding="utf-8")
                commit = common.git_commit_if_changed(
                    "hourly: refresh package database",
                    ["combined"],
                    preserve_existing_dirty=True,
                    preserved_tracked_dirty=preserved_tracked_dirty,
                    preserved_untracked_dirty=preserved_untracked_dirty,
                )
            finally:
                common.ROOT = original_root

            self.assertIsNotNone(commit)
            committed = subprocess.run(
                ["git", "show", "--name-only", "--format=", "HEAD"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            dirty = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(committed, ["combined/new.yml"])
            self.assertEqual(dirty, ["combined/old.yml"])


class FetchBytesTests(unittest.TestCase):
    def test_fetch_bytes_retries_incomplete_read(self):
        original_cache_dir = common.CACHE_DIR
        calls = []

        def fetch_url(_url, *, headers):
            calls.append(headers)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"partial", 5)
            return 200, {}, b"complete"

        with tempfile.TemporaryDirectory() as tmp:
            try:
                common.CACHE_DIR = Path(tmp)
                with (
                    mock.patch.object(common, "fetch_url", side_effect=fetch_url),
                    mock.patch.object(common.time, "sleep"),
                ):
                    data = common.fetch_bytes(
                        "https://example.com/index.data",
                        namespace="test",
                        refresh=True,
                    )
            finally:
                common.CACHE_DIR = original_cache_dir

        self.assertEqual(data, b"complete")
        self.assertEqual(len(calls), 2)

    def test_fetch_bytes_uses_cached_data_after_retryable_refresh_failure(self):
        original_cache_dir = common.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                common.CACHE_DIR = Path(tmp)
                url = "https://example.com/index.data"
                path = common.cache_path_for_url(url, "test", ".data")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"cached")

                with (
                    mock.patch.object(
                        common,
                        "fetch_url",
                        side_effect=common.urllib.error.URLError("timeout"),
                    ) as fetch_url,
                    mock.patch.object(common.time, "sleep"),
                ):
                    data = common.fetch_bytes(url, namespace="test", refresh=True)
            finally:
                common.CACHE_DIR = original_cache_dir

        self.assertEqual(data, b"cached")
        self.assertEqual(fetch_url.call_count, 1)


class ReplaceIfChangedTests(unittest.TestCase):
    def test_equal_files_are_compared_without_whole_file_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"same contents")
            destination.write_bytes(b"same contents")

            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                changed = common.replace_if_changed(source, destination)

            self.assertFalse(changed)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_text(encoding="utf-8"), "same contents")

    def test_different_same_size_file_replaces_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"new!")
            destination.write_bytes(b"old!")

            changed = common.replace_if_changed(source, destination)

            self.assertTrue(changed)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"new!")


class FetchJsonTests(unittest.TestCase):
    def test_fetch_json_retries_incomplete_read(self):
        original_cache_dir = common.CACHE_DIR
        calls = []

        def fetch_url(_url, *, headers):
            calls.append(headers)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b'{"partial"', 10)
            return 200, {"etag": "abc"}, b'{"ok": true}'

        with tempfile.TemporaryDirectory() as tmp:
            try:
                common.CACHE_DIR = Path(tmp)
                with (
                    mock.patch.object(common, "fetch_url", side_effect=fetch_url),
                    mock.patch.object(common.time, "sleep"),
                ):
                    data = common.fetch_json(
                        "https://example.com/index.json",
                        namespace="test",
                        refresh=True,
                    )
            finally:
                common.CACHE_DIR = original_cache_dir

        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_fetch_json_uses_cached_payload_after_retryable_refresh_failure(self):
        original_cache_dir = common.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                common.CACHE_DIR = Path(tmp)
                url = "https://example.com/index.json"
                path = common.cache_path_for_url(url, "test", ".json")
                path.parent.mkdir(parents=True, exist_ok=True)
                common.write_json(
                    path,
                    {
                        common.META_KEY: {"checked_at": 0, "etag": "abc"},
                        common.PAYLOAD_KEY: {"cached": True},
                    },
                )

                with (
                    mock.patch.object(
                        common,
                        "fetch_url",
                        side_effect=common.urllib.error.URLError("timeout"),
                    ) as fetch_url,
                    mock.patch.object(common.time, "sleep"),
                ):
                    data = common.fetch_json(url, namespace="test", refresh=True)
            finally:
                common.CACHE_DIR = original_cache_dir

        self.assertEqual(data, {"cached": True})
        self.assertEqual(fetch_url.call_count, 1)


if __name__ == "__main__":
    unittest.main()
