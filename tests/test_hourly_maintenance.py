import importlib.util
import sys
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
            with mock.patch.object(maintenance, "run") as run:
                self.assertEqual(maintenance.main(), 0)
        return [call.args[0] for call in run.call_args_list]

    def test_default_builds_new_isotopes(self):
        commands = self.run_hourly()

        self.assertIn(["bash", "scripts/build-isotopes.sh"], commands)
        self.assertNotIn(["bash", "scripts/build-isotopes.sh", "--skip-builds"], commands)

    def test_skip_isotope_builds_refreshes_summary_only(self):
        commands = self.run_hourly("--skip-isotope-builds")

        self.assertIn(["bash", "scripts/build-isotopes.sh", "--skip-builds"], commands)

    def test_skip_isotopes_skips_isotope_command(self):
        commands = self.run_hourly("--skip-isotopes")

        isotope_commands = [
            command
            for command in commands
            if command[:2] == ["bash", "scripts/build-isotopes.sh"]
        ]
        self.assertEqual(isotope_commands, [])

    def test_runs_automic_vault_db_health_check_after_export(self):
        commands = self.run_hourly("--skip-isotopes")

        export_index = commands.index([sys.executable, "scripts/export-automic-vault-db.py"])
        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])

        self.assertEqual(health_index, export_index + 1)

    def test_hourly_enrichment_prepares_external_controller_batches(self):
        commands = self.run_hourly("--skip-isotopes")
        enrich_commands = [
            command
            for command in commands
            if command[:2] == [sys.executable, "scripts/enrich-projects.py"]
        ]

        self.assertEqual(len(enrich_commands), 1)
        command = enrich_commands[0]
        self.assertIn("--include-missing-curated-fields", command)
        self.assertIn("--backend", command)
        self.assertIn("external", command)
        self.assertIn("--phase", command)
        self.assertIn("prepare", command)
        self.assertNotIn("--commit-after-batch", command)

    def test_publishes_public_db_after_health_check(self):
        commands = self.run_hourly("--skip-isotopes")

        health_index = commands.index([sys.executable, "scripts/check-automic-vault-db-health.py"])
        publish_index = commands.index([sys.executable, "scripts/publish-public-db.py"])

        self.assertEqual(publish_index, health_index + 1)


if __name__ == "__main__":
    unittest.main()
