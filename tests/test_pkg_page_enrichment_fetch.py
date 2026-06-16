import importlib.util
import http.client
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate-pkg-page-enrichment.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("pkg_page_enrichment", MODULE_PATH)
pkg_page_enrichment = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pkg_page_enrichment)


class FetchJsonTests(unittest.TestCase):
    def test_fetch_json_retries_incomplete_read(self):
        original_cache_dir = pkg_page_enrichment.CACHE_DIR
        calls = []

        class Response:
            headers = {"etag": "abc"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                calls.append(None)
                if len(calls) == 1:
                    raise http.client.IncompleteRead(b'{"partial"', 10)
                return b'{"ok": true}'

        with tempfile.TemporaryDirectory() as tmp:
            try:
                pkg_page_enrichment.CACHE_DIR = Path(tmp)
                with (
                    mock.patch.object(pkg_page_enrichment.urllib.request, "urlopen", return_value=Response()),
                    mock.patch.object(pkg_page_enrichment.time, "sleep"),
                ):
                    data = pkg_page_enrichment.fetch_json(
                        "https://example.com/index.json",
                        ecosystem="test",
                        force_refresh=True,
                    )
            finally:
                pkg_page_enrichment.CACHE_DIR = original_cache_dir

        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_fetch_json_uses_cached_payload_after_retryable_refresh_failure(self):
        original_cache_dir = pkg_page_enrichment.CACHE_DIR

        with tempfile.TemporaryDirectory() as tmp:
            try:
                pkg_page_enrichment.CACHE_DIR = Path(tmp)
                url = "https://example.com/index.json"
                path = pkg_page_enrichment.cache_path_for(url, "test")
                pkg_page_enrichment.write_cache(path, {"cached": True}, "abc", 0)

                with (
                    mock.patch.object(
                        pkg_page_enrichment.urllib.request,
                        "urlopen",
                        side_effect=pkg_page_enrichment.urllib.error.URLError("timeout"),
                    ) as urlopen,
                    mock.patch.object(pkg_page_enrichment.time, "sleep"),
                ):
                    data = pkg_page_enrichment.fetch_json(
                        url,
                        ecosystem="test",
                        force_refresh=True,
                    )
            finally:
                pkg_page_enrichment.CACHE_DIR = original_cache_dir

        self.assertEqual(data, {"cached": True})
        self.assertEqual(urlopen.call_count, pkg_page_enrichment.FETCH_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
