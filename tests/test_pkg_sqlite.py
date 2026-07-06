import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent


def load_pkg_sqlite():
    spec = importlib.util.spec_from_file_location("pkg_sqlite_for_tests", ROOT / "scripts" / "generate-pkg-sqlite.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


pkg_sqlite = load_pkg_sqlite()


class PackageSqliteTests(unittest.TestCase):
    def test_stale_no_executable_geiger_reason_is_removed_when_executables_exist(self):
        page = SimpleNamespace(
            executables=[{"name": "aomdec"}],
            aliases=["aomdec"],
            binaries=[],
            geiger={
                "level": "green",
                "reasons": ["no executable entrypoint in the package index"],
                "signals": ["metadata:no-indexed-executables"],
            },
        )

        self.assertIsNone(pkg_sqlite.sanitized_geiger(page))

    def test_real_geiger_reasons_survive_with_executables(self):
        page = SimpleNamespace(
            executables=[{"name": "sshfs"}],
            aliases=["sshfs"],
            binaries=[],
            geiger={
                "level": "blue",
                "reasons": ["Can mount remote filesystems over SSH."],
                "signals": ["remote filesystem access"],
            },
        )

        self.assertEqual(pkg_sqlite.sanitized_geiger(page), page.geiger)


if __name__ == "__main__":
    unittest.main()
