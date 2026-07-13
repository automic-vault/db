import importlib.util
import sys
import unittest
from pathlib import Path


def load_build_db():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build-db.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("build_db_npm_scan", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NpmFullScanTests(unittest.TestCase):
    def test_seven_parts_caps_current_registry_at_120_pages(self):
        build_db = load_build_db()
        build_db.NPM_FULL_SCAN_PARTS = 7

        self.assertEqual(build_db._npm_full_scan_page_budget(4_200_000), 120)

    def test_full_scan_has_no_page_cap(self):
        build_db = load_build_db()
        build_db.NPM_FULL_SCAN_PARTS = 1

        self.assertIsNone(build_db._npm_full_scan_page_budget(4_200_000))

    def test_small_registry_still_processes_one_page_per_part(self):
        build_db = load_build_db()
        build_db.NPM_FULL_SCAN_PARTS = 7

        self.assertEqual(build_db._npm_full_scan_page_budget(1), 1)


if __name__ == "__main__":
    unittest.main()
