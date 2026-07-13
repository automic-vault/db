import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_hourly_maintenance():
    path = Path(__file__).resolve().parents[1] / "scripts" / "hourly-maintenance.py"
    spec = importlib.util.spec_from_file_location("hourly_maintenance", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HourlyMaintenanceTests(unittest.TestCase):
    def run_hourly(self, *args):
        maintenance = load_hourly_maintenance()
        with mock.patch.object(sys, "argv", ["hourly-maintenance.py", "--no-commit", "--skip-sqlite", *args]):
            with (
                mock.patch.object(maintenance, "run") as run,
                mock.patch.object(maintenance, "run_publish_public_db", return_value=True) as run_publish_public_db,
                mock.patch.object(maintenance, "run_prepare_enrichment", return_value=None) as run_prepare_enrichment,
                mock.patch.object(maintenance, "unresolved_enrichment_run_ids", return_value=[]),
            ):
                self.assertEqual(maintenance.main(), 0)
        return (
            [call.args[0] for call in run.call_args_list],
            [call.args[0] for call in run_publish_public_db.call_args_list],
            run_prepare_enrichment.call_args_list,
        )

    def test_does_not_run_deleted_builder(self):
        commands, _, _ = self.run_hourly()

        self.assertNotIn("bash", [command[0] for command in commands])

    def test_runs_automic_vault_db_health_check_after_export(self):
        commands, _, _ = self.run_hourly()

        export_index = commands.index([sys.executable, "scripts/export-automic-vault-db.py"])
        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])

        self.assertEqual(health_index, export_index + 1)

    def test_runs_one_seventh_of_npm_full_scan(self):
        commands, _, _ = self.run_hourly()

        self.assertIn(
            [sys.executable, "scripts/build-db.py", "--refresh", "--npm-full-scan-parts=7"],
            commands,
        )

    def test_hourly_enrichment_prepares_external_controller_batches(self):
        _, _, prepare_calls = self.run_hourly()

        self.assertEqual(len(prepare_calls), 1)
        command = prepare_calls[0].args[0]
        self.assertIn("--include-missing-curated-fields", command)
        self.assertIn("--backend", command)
        self.assertIn("external", command)
        self.assertIn("--phase", command)
        self.assertIn("prepare", command)
        self.assertNotIn("--commit-after-batch", command)

    def test_publishes_public_db_after_health_check(self):
        commands, publish_commands, _ = self.run_hourly()

        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])

        self.assertEqual(publish_commands, [[sys.executable, "scripts/publish-public-db.py"]])
        self.assertEqual(health_index, 3)

    def test_snapshots_dirty_paths_before_running_commit_flow(self):
        maintenance = load_hourly_maintenance()

        with mock.patch.object(sys, "argv", ["hourly-maintenance.py", "--skip-sqlite"]):
            with (
                mock.patch.object(maintenance, "run"),
                mock.patch.object(maintenance, "run_publish_public_db", return_value=True),
                mock.patch.object(maintenance, "run_prepare_enrichment", return_value=None),
                mock.patch.object(maintenance, "unresolved_enrichment_run_ids", return_value=[]),
                mock.patch.object(maintenance, "git_dirty_paths", return_value=(["combined/existing.yml"], [])) as git_dirty_paths,
                mock.patch.object(maintenance, "git_commit_if_changed", return_value="abc123") as git_commit_if_changed,
            ):
                self.assertEqual(maintenance.main(), 0)

        git_dirty_paths.assert_called_once_with(maintenance.COMMIT_PATHS)
        git_commit_if_changed.assert_called_once()
        self.assertEqual(
            git_commit_if_changed.call_args.kwargs["preserved_tracked_dirty"],
            ["combined/existing.yml"],
        )
        self.assertEqual(git_commit_if_changed.call_args.kwargs["preserved_untracked_dirty"], [])

    def test_skips_public_db_publish_when_unattended_aws_approval_is_unavailable(self):
        maintenance = load_hourly_maintenance()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                [sys.executable, "scripts/publish-public-db.py"],
                1,
                stdout="",
                stderr='{"error": "missing AWS credential_process approval token", "ok": false}\n',
            )
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                self.assertFalse(maintenance.run_publish_public_db([sys.executable, "scripts/publish-public-db.py"]))

        self.assertIn("skipping public db publish", stderr.getvalue())

    def test_skips_public_db_publish_when_aws_credentials_are_missing(self):
        maintenance = load_hourly_maintenance()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                [sys.executable, "scripts/publish-public-db.py"],
                1,
                stdout="",
                stderr="upload failed: cache/automic-vault/combined.json to s3://automicvault.com/db.json Unable to locate credentials\n",
            )
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                self.assertFalse(maintenance.run_publish_public_db([sys.executable, "scripts/publish-public-db.py"]))

        self.assertIn("skipping public db publish", stderr.getvalue())

    def test_skips_public_db_publish_when_unattended_publish_stalls(self):
        maintenance = load_hourly_maintenance()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["aws"], 120)):
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                self.assertFalse(maintenance.run_publish_public_db([sys.executable, "scripts/publish-public-db.py"]))

        self.assertIn("stalled", stderr.getvalue())

    def test_skips_public_db_publish_when_av_inject_connection_is_interrupted(self):
        maintenance = load_hourly_maintenance()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                [sys.executable, "scripts/publish-public-db.py"],
                1,
                stdout="",
                stderr="av inject: Connection interrupted\n",
            )
            with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                self.assertFalse(maintenance.run_publish_public_db([sys.executable, "scripts/publish-public-db.py"]))

        self.assertIn("skipping public db publish", stderr.getvalue())

    def test_public_db_publish_still_fails_for_other_errors(self):
        maintenance = load_hourly_maintenance()

        with mock.patch("subprocess.run") as subprocess_run:
            subprocess_run.return_value = subprocess.CompletedProcess(
                [sys.executable, "scripts/publish-public-db.py"],
                1,
                stdout="",
                stderr='{"error": "boom", "ok": false}\n',
            )
            with self.assertRaises(subprocess.CalledProcessError):
                maintenance.run_publish_public_db([sys.executable, "scripts/publish-public-db.py"])

    def test_parse_prepared_run_dir_reads_prepare_output(self):
        maintenance = load_hourly_maintenance()

        run_dir = maintenance.parse_prepared_run_dir(
            "Prepared 10 projects in 4 batches under cache/enrichment/runs/20260623T110419Z\n"
        )

        self.assertEqual(run_dir, maintenance.ROOT / "cache/enrichment/runs/20260623T110419Z")

    def test_hourly_enrichment_health_ignores_empty_prepare_runs(self):
        maintenance = load_hourly_maintenance()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            current_dir = runs_dir / "current-run"
            current_dir.mkdir(parents=True)
            (current_dir / "controller-manifest.json").write_text('{"selected_count": 0}\n', encoding="utf-8")

            with mock.patch.object(maintenance, "ROOT", tmp_root):
                with mock.patch.object(maintenance, "ENRICHMENT_RUNS_DIR", runs_dir):
                    maintenance.assert_hourly_enrichment_progress(current_dir)

    def test_hourly_enrichment_health_fails_when_older_runs_remain_unapplied(self):
        maintenance = load_hourly_maintenance()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            older_dir = runs_dir / "older-run"
            current_dir = runs_dir / "current-run"
            older_dir.mkdir(parents=True)
            current_dir.mkdir(parents=True)
            (older_dir / "controller-manifest.json").write_text('{"selected_count": 5}\n', encoding="utf-8")
            (current_dir / "controller-manifest.json").write_text('{"selected_count": 3}\n', encoding="utf-8")

            with mock.patch.object(maintenance, "ROOT", tmp_root):
                with mock.patch.object(maintenance, "ENRICHMENT_RUNS_DIR", runs_dir):
                    with self.assertRaises(maintenance.EnrichmentHealthError):
                        maintenance.assert_hourly_enrichment_progress(current_dir)

    def test_hourly_enrichment_health_allows_older_runs_that_were_applied(self):
        maintenance = load_hourly_maintenance()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            older_dir = runs_dir / "older-run"
            current_dir = runs_dir / "current-run"
            older_dir.mkdir(parents=True)
            current_dir.mkdir(parents=True)
            (older_dir / "controller-manifest.json").write_text('{"selected_count": 5}\n', encoding="utf-8")
            (older_dir / "apply-summary.json").write_text('{"changed": 2}\n', encoding="utf-8")
            (current_dir / "controller-manifest.json").write_text('{"selected_count": 3}\n', encoding="utf-8")

            with mock.patch.object(maintenance, "ROOT", tmp_root):
                with mock.patch.object(maintenance, "ENRICHMENT_RUNS_DIR", runs_dir):
                    maintenance.assert_hourly_enrichment_progress(current_dir)

    def test_hourly_skips_prepare_when_older_runs_remain_unapplied(self):
        maintenance = load_hourly_maintenance()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            older_dir = runs_dir / "older-run"
            older_dir.mkdir(parents=True)
            (older_dir / "controller-manifest.json").write_text('{"selected_count": 5}\n', encoding="utf-8")

            stderr = io.StringIO()
            with mock.patch.object(maintenance, "ROOT", tmp_root):
                with mock.patch.object(maintenance, "ENRICHMENT_RUNS_DIR", runs_dir):
                    with mock.patch.object(sys, "argv", ["hourly-maintenance.py", "--no-commit", "--skip-sqlite"]):
                        with (
                            mock.patch.object(maintenance, "run") as run,
                            mock.patch.object(maintenance, "run_publish_public_db", return_value=True) as run_publish_public_db,
                            mock.patch.object(maintenance, "run_prepare_enrichment") as run_prepare_enrichment,
                            mock.patch("sys.stderr", stderr),
                        ):
                            self.assertEqual(maintenance.main(), 0)

            run_prepare_enrichment.assert_not_called()
            self.assertIn("skipping hourly enrichment prepare", stderr.getvalue())
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn([sys.executable, "scripts/build.py", "--refresh"], commands)
            run_publish_public_db.assert_called_once_with([sys.executable, "scripts/publish-public-db.py"])


if __name__ == "__main__":
    unittest.main()
