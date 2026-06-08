import importlib.util
import sys
import threading
import unittest
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


if __name__ == "__main__":
    unittest.main()
