import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
