import importlib.util
import contextlib
import io
import sys
import unittest
import argparse
from unittest import mock
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


def load_enrich_projects():
    path = Path(__file__).resolve().parents[1] / "scripts" / "enrich-projects.py"
    spec = importlib.util.spec_from_file_location("enrich_projects", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NightlyMaintenanceTests(unittest.TestCase):
    def test_build_tasks_commits_refresh_and_prepares_controller_enrichment(self):
        maintenance = load_nightly_maintenance()
        args = argparse.Namespace(
            enrich_limit=50,
            batch_size=10,
            no_commit=False,
        )

        tasks = maintenance.build_tasks(args)

        self.assertEqual(tasks["refresh"].commit_paths, ["deterministic", "combined"])
        self.assertIn("--backend", tasks["enrich-new"].command)
        self.assertIn("external", tasks["enrich-new"].command)
        self.assertIn("--phase", tasks["enrich-new"].command)
        self.assertIn("prepare", tasks["enrich-new"].command)
        self.assertNotIn("--commit-after-batch", tasks["enrich-new"].command)
        self.assertIn("--backend", tasks["review-stale-updated"].command)
        self.assertIn("external", tasks["review-stale-updated"].command)
        self.assertIn("--phase", tasks["review-stale-updated"].command)
        self.assertIn("prepare", tasks["review-stale-updated"].command)
        self.assertNotIn("--commit-after-batch", tasks["review-stale-updated"].command)

    def test_list_prints_automation_commands(self):
        maintenance = load_nightly_maintenance()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            with mock.patch.object(sys, "argv", ["nightly-maintenance.py", "--list"]):
                self.assertEqual(maintenance.main(), 0)

        self.assertIn("refresh\tRefresh deterministic package data", output.getvalue())
        self.assertIn("enrich-new\tPrepare newly observed project enrichment batches", output.getvalue())
        self.assertIn("review-stale-updated\tPrepare stale or upstream-updated project review batches", output.getvalue())

    def test_codex_cli_timeout_defaults_to_bounded_run(self):
        enrich_projects = load_enrich_projects()

        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(enrich_projects.codex_timeout_seconds(), 900)

    def test_codex_cli_timeout_can_be_customized_or_disabled(self):
        enrich_projects = load_enrich_projects()

        with mock.patch.dict("os.environ", {"AVDB_CODEX_TIMEOUT_SECONDS": "42"}, clear=True):
            self.assertEqual(enrich_projects.codex_timeout_seconds(), 42)
        with mock.patch.dict("os.environ", {"AVDB_CODEX_TIMEOUT_SECONDS": "0"}, clear=True):
            self.assertIsNone(enrich_projects.codex_timeout_seconds())

    def test_codex_cli_timeout_rejects_invalid_values(self):
        enrich_projects = load_enrich_projects()

        with mock.patch.dict("os.environ", {"AVDB_CODEX_TIMEOUT_SECONDS": "-1"}, clear=True):
            with self.assertRaises(SystemExit):
                enrich_projects.codex_timeout_seconds()
        with mock.patch.dict("os.environ", {"AVDB_CODEX_TIMEOUT_SECONDS": "nope"}, clear=True):
            with self.assertRaises(SystemExit):
                enrich_projects.codex_timeout_seconds()


if __name__ == "__main__":
    unittest.main()
