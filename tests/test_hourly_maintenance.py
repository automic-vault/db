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


if __name__ == "__main__":
    unittest.main()
