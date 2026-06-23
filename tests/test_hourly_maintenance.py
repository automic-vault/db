import importlib.util
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
                mock.patch.object(maintenance, "run_prepare_enrichment", return_value=None) as run_prepare_enrichment,
            ):
                self.assertEqual(maintenance.main(), 0)
        return [call.args[0] for call in run.call_args_list], run_prepare_enrichment.call_args_list

    def test_default_builds_new_isotopes(self):
        commands, _ = self.run_hourly()

        self.assertIn(["bash", "scripts/build-isotopes.sh"], commands)
        self.assertNotIn(["bash", "scripts/build-isotopes.sh", "--skip-builds"], commands)

    def test_skip_isotope_builds_refreshes_summary_only(self):
        commands, _ = self.run_hourly("--skip-isotope-builds")

        self.assertIn(["bash", "scripts/build-isotopes.sh", "--skip-builds"], commands)

    def test_skip_isotopes_skips_isotope_command(self):
        commands, _ = self.run_hourly("--skip-isotopes")

        isotope_commands = [
            command
            for command in commands
            if command[:2] == ["bash", "scripts/build-isotopes.sh"]
        ]
        self.assertEqual(isotope_commands, [])

    def test_runs_automic_vault_db_health_check_after_export(self):
        commands, _ = self.run_hourly("--skip-isotopes")

        export_index = commands.index([sys.executable, "scripts/export-automic-vault-db.py"])
        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])

        self.assertEqual(health_index, export_index + 1)

    def test_hourly_enrichment_prepares_external_controller_batches(self):
        _, prepare_calls = self.run_hourly("--skip-isotopes")

        self.assertEqual(len(prepare_calls), 1)
        command = prepare_calls[0].args[0]
        self.assertIn("--include-missing-curated-fields", command)
        self.assertIn("--backend", command)
        self.assertIn("external", command)
        self.assertIn("--phase", command)
        self.assertIn("prepare", command)
        self.assertNotIn("--commit-after-batch", command)

    def test_publishes_public_db_after_health_check(self):
        commands, _ = self.run_hourly("--skip-isotopes")

        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])
        publish_index = commands.index([sys.executable, "scripts/publish-public-db.py"])

        self.assertEqual(publish_index, health_index + 1)

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


if __name__ == "__main__":
    unittest.main()
