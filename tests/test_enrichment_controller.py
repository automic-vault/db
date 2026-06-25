import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_enrichment_controller():
    path = Path(__file__).resolve().parents[1] / "scripts" / "enrichment-controller.py"
    spec = importlib.util.spec_from_file_location("enrichment_controller", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EnrichmentControllerTests(unittest.TestCase):
    def test_unresolved_runs_skips_applied_and_empty_runs(self):
        controller = load_enrichment_controller()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            unresolved = runs_dir / "20260623T010101Z"
            applied = runs_dir / "20260623T020202Z"
            empty = runs_dir / "20260623T030303Z"
            unresolved.mkdir(parents=True)
            applied.mkdir(parents=True)
            empty.mkdir(parents=True)

            (unresolved / "controller-manifest.json").write_text(
                json.dumps(
                    {
                        "mode": "new",
                        "provider": "brew",
                        "batch_size": 3,
                        "selected_count": 10,
                        "include_missing_curated_fields": True,
                        "batches": [{"status": "pending"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (applied / "controller-manifest.json").write_text(
                json.dumps(
                    {
                        "mode": "review-stale-updated",
                        "provider": "brew",
                        "batch_size": 5,
                        "selected_count": 4,
                        "batches": [{"status": "pending"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (applied / "apply-summary.json").write_text('{"changed": 2}\n', encoding="utf-8")
            (empty / "controller-manifest.json").write_text(
                json.dumps({"mode": "new", "provider": "brew", "batch_size": 3, "selected_count": 0, "batches": []})
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(controller, "ROOT", tmp_root):
                with mock.patch.object(controller, "RUNS_DIR", runs_dir):
                    runs = controller.unresolved_runs()

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "20260623T010101Z")
        self.assertTrue(runs[0]["include_missing_curated_fields"])

    def test_apply_command_includes_include_missing_when_requested(self):
        controller = load_enrichment_controller()

        command = controller.apply_command(
            {
                "run_id": "20260623T010101Z",
                "mode": "new",
                "provider": "brew",
                "batch_size": 3,
                "include_missing_curated_fields": True,
            }
        )

        self.assertIn("--include-missing-curated-fields", command)
        self.assertNotIn("--commit-after-batch", command)
        self.assertEqual(command[command.index("--batch-size") + 1], "3")
        self.assertEqual(command[-2:], ["20260623T010101Z", "--include-missing-curated-fields"])

    def test_apply_command_omits_include_missing_when_not_requested(self):
        controller = load_enrichment_controller()

        command = controller.apply_command(
            {
                "run_id": "20260623T020202Z",
                "mode": "review-stale-updated",
                "provider": "brew",
                "batch_size": 5,
                "include_missing_curated_fields": False,
            }
        )

        self.assertNotIn("--include-missing-curated-fields", command)
        self.assertIn("review-stale-updated", command)
        self.assertEqual(command[-1], "--commit-after-batch")


if __name__ == "__main__":
    unittest.main()
