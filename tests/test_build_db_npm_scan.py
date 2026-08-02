import importlib.util
import http.client
import sys
import unittest
from pathlib import Path
from unittest import mock


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
    def test_packument_batch_can_skip_disk_cache(self):
        build_db = load_build_db()

        with mock.patch.object(
            build_db,
            "_fetch_npm_packument",
            return_value={"name": "example"},
        ) as fetch_packument:
            packuments = build_db._fetch_npm_packuments_for_packages(
                ["example"],
                use_cache=False,
            )

        self.assertEqual(packuments, {"example": {"name": "example"}})
        fetch_packument.assert_called_once_with("example", use_cache=False)

    def test_npm_fetch_retries_incomplete_response(self):
        build_db = load_build_db()
        truncated = mock.MagicMock()
        truncated.__enter__.return_value.read.side_effect = http.client.IncompleteRead(
            b'{"partial"',
            10,
        )
        complete = mock.MagicMock()
        complete.__enter__.return_value.read.return_value = b'{"ok": true}'
        complete.__enter__.return_value.headers = {}

        with (
            mock.patch.object(build_db.urllib.request, "urlopen", side_effect=[truncated, complete]) as urlopen,
            mock.patch.object(build_db, "_npm_bucket_for_host") as bucket_for_host,
            mock.patch.object(build_db.time, "sleep"),
        ):
            payload = build_db._npm_fetch_json("https://registry.npmjs.org/example", use_cache=False)

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(bucket_for_host.return_value.wait.call_count, 2)

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
