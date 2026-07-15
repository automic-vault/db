import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate-pkg-version-freshness.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("pkg_version_freshness", MODULE_PATH)
pkg_version_freshness = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pkg_version_freshness)


class PackageVersionFreshnessTests(unittest.TestCase):
    def test_manager_info_prefers_db_version_over_stale_enrichment(self):
        entry = {
            "version": "7.8.4",
            "publishedAt": "2026-06-09T23:50:03.612Z",
        }
        db = {
            "npms": {
                "semver": {
                    "version": "7.8.5",
                    "last_updated_at": "2026-06-19T18:32:48.972Z",
                }
            }
        }

        manager = pkg_version_freshness.manager_info(
            "npm:semver",
            entry,
            db,
            dt.datetime(2026, 7, 15, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(manager["version"], "7.8.5")
        self.assertEqual(manager["updatedAt"], "2026-06-19T18:32:48.972Z")


if __name__ == "__main__":
    unittest.main()
