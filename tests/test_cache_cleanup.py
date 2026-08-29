import os
import tempfile
import unittest
from pathlib import Path

from scripts.cache_cleanup import prune_completed_enrichment_runs, remove_stale_atomic_temp_files


class CacheCleanupTests(unittest.TestCase):
    def test_prunes_only_old_completed_enrichment_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            for name in ("001", "002", "003", "004"):
                run_dir = runs_dir / name
                run_dir.mkdir()
                (run_dir / "controller-manifest.json").write_text("{}\n", encoding="utf-8")
                (run_dir / "apply-summary.json").write_text("{}\n", encoding="utf-8")
            unresolved = runs_dir / "000-unresolved"
            unresolved.mkdir()
            (unresolved / "controller-manifest.json").write_text("{}\n", encoding="utf-8")
            unrelated = runs_dir / "history-only"
            unrelated.mkdir()
            (unrelated / "codex-output.json").write_text("{}\n", encoding="utf-8")

            removed = prune_completed_enrichment_runs(runs_dir, keep=2)

            self.assertEqual([path.name for path in removed], ["002", "001"])
            self.assertTrue((runs_dir / "003").is_dir())
            self.assertTrue((runs_dir / "004").is_dir())
            self.assertTrue(unresolved.is_dir())
            self.assertTrue(unrelated.is_dir())

    def test_removes_only_old_hidden_atomic_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            old_temp = cache_dir / ".index.json.abcd.tmp"
            recent_temp = cache_dir / ".index.json.efgh.tmp"
            durable_cache = cache_dir / "index.json"
            ordinary_tmp = cache_dir / "download.tmp"
            for path in (old_temp, recent_temp, durable_cache, ordinary_tmp):
                path.write_text("data", encoding="utf-8")
            os.utime(old_temp, (100, 100))

            removed = remove_stale_atomic_temp_files(cache_dir, min_age_seconds=60, now=200)

            self.assertEqual(removed, [old_temp])
            self.assertFalse(old_temp.exists())
            self.assertTrue(recent_temp.exists())
            self.assertTrue(durable_cache.exists())
            self.assertTrue(ordinary_tmp.exists())


if __name__ == "__main__":
    unittest.main()
