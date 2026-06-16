import importlib.util
import sys
import unittest
from pathlib import Path


def load_health_check():
    path = Path(__file__).resolve().parents[1] / "scripts" / "check-automic-vault-db-health.py"
    spec = importlib.util.spec_from_file_location("automic_vault_db_health", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AutomicVaultDbHealthTests(unittest.TestCase):
    def test_pulse_health_reports_coverage(self):
        health = load_health_check()

        coverage = health.check_pulse_health(
            {
                "formulas": {
                    "awscli": {
                        "last_updated_at": "2026-06-15T12:00:00Z",
                        "pulse_kind": "updated",
                    }
                },
                "casks": {
                    "1password-cli": {
                        "last_updated_at": "2026-06-14T12:00:00Z",
                        "pulse_kind": "new",
                    }
                },
                "npms": {
                    "ts-node": {
                        "last_updated_at": "2026-06-13T12:00:00Z",
                    }
                },
            }
        )

        self.assertEqual(coverage["formulas"]["last_updated_at"], 1)
        self.assertEqual(coverage["casks"]["pulse_kind"], 1)
        self.assertEqual(coverage["npms"]["last_updated_at"], 1)
        self.assertEqual(coverage["npms"]["pulse_kind"], 0)

    def test_pulse_health_fails_when_populated_source_has_no_updates(self):
        health = load_health_check()

        with self.assertRaisesRegex(
            health.HealthCheckFailed,
            "formulas: no last_updated_at values",
        ):
            health.check_pulse_health(
                {
                    "formulas": {"awscli": {"summary": "AWS CLI"}},
                    "casks": {},
                    "npms": {},
                }
            )

    def test_pulse_health_requires_homebrew_pulse_kind_when_updates_exist(self):
        health = load_health_check()

        with self.assertRaisesRegex(
            health.HealthCheckFailed,
            "casks: no pulse_kind values",
        ):
            health.check_pulse_health(
                {
                    "formulas": {},
                    "casks": {
                        "1password-cli": {
                            "last_updated_at": "2026-06-14T12:00:00Z",
                        }
                    },
                    "npms": {},
                }
            )


if __name__ == "__main__":
    unittest.main()
