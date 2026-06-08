import importlib.util
import argparse
import contextlib
import io
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path


def load_nightly_maintenance():
    path = Path(__file__).resolve().parents[1] / "scripts" / "nightly-maintenance.py"
    spec = importlib.util.spec_from_file_location("nightly_maintenance", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NightlyMaintenanceTests(unittest.TestCase):
    def test_wait_or_stop_returns_when_stop_already_requested(self):
        maintenance = load_nightly_maintenance()
        stop_requested = threading.Event()
        stop_requested.set()

        self.assertTrue(maintenance.wait_or_stop(stop_requested, 60))

    def test_wait_or_stop_reports_timeout_without_stop(self):
        maintenance = load_nightly_maintenance()

        self.assertFalse(maintenance.wait_or_stop(threading.Event(), 0))

    def test_build_jobs_commits_refresh_and_batches_enrichment(self):
        maintenance = load_nightly_maintenance()
        args = argparse.Namespace(
            build_time=maintenance.parse_time("02:15"),
            weekly_day=maintenance.parse_weekday("sunday"),
            enrich_new_time=maintenance.parse_time("03:15"),
            enrich_stale_time=maintenance.parse_time("04:15"),
            enrich_limit=50,
            batch_size=10,
        )

        jobs = {job.key: job for job in maintenance.build_jobs(args)}

        self.assertEqual(jobs["build-refresh"].commit_paths, ["deterministic", "combined"])
        self.assertIn("--commit-after-batch", jobs["enrich-new"].command)
        self.assertIn("--commit-after-batch", jobs["enrich-stale-updated"].command)

    def test_sleeping_status_is_single_cycle_message(self):
        maintenance = load_nightly_maintenance()
        palette = maintenance.Palette(enabled=False, ascii_only=False)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            maintenance.print_sleeping_status(palette, 15 * 60, datetime(2026, 6, 9, 2, 15))

        self.assertEqual(output.getvalue(), "◇ Sleeping next check in 15m; next job Tue 02:15\n")


if __name__ == "__main__":
    unittest.main()
