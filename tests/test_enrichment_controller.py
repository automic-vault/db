import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
    def write_manifest(self, run_dir: Path, *, selected_count: int = 1) -> None:
        run_dir.mkdir(parents=True)
        (run_dir / "controller-manifest.json").write_text(
            json.dumps(
                {
                    "mode": "new",
                    "provider": "brew",
                    "batch_size": 5,
                    "selected_count": selected_count,
                    "batches": [{"status": "pending"}] if selected_count else [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

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

    def test_apply_command_includes_missing_and_explicit_commit_when_requested(self):
        controller = load_enrichment_controller()

        command = controller.apply_command(
            {
                "run_id": "20260623T010101Z",
                "mode": "new",
                "provider": "brew",
                "batch_size": 3,
                "commit_after_batch": True,
                "include_missing_curated_fields": True,
            }
        )

        self.assertIn("--include-missing-curated-fields", command)
        self.assertIn("--commit-after-batch", command)
        self.assertEqual(command[command.index("--batch-size") + 1], "3")
        self.assertEqual(command[-2:], ["--include-missing-curated-fields", "--commit-after-batch"])

    def test_legacy_missing_field_run_remains_uncommitted(self):
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

        self.assertNotIn("--commit-after-batch", command)

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

    def test_apply_command_maps_history_only_manifest_mode(self):
        controller = load_enrichment_controller()

        command = controller.apply_command(
            {
                "run_id": "history-only-20260630T214000Z",
                "mode": "history-only",
                "provider": "brew",
                "batch_size": 6,
                "include_missing_curated_fields": False,
            }
        )

        self.assertEqual(command[command.index("--mode") + 1], "history-missing")
        self.assertEqual(command[-1], "--commit-after-batch")

    def test_apply_command_maps_mixed_manifest_provider(self):
        controller = load_enrichment_controller()

        command = controller.apply_command(
            {
                "run_id": "history-only-20260701T000500Z",
                "mode": "history-only",
                "provider": "mixed",
                "batch_size": 6,
                "include_missing_curated_fields": False,
            }
        )

        self.assertEqual(command[command.index("--provider") + 1], "brew")

    def test_unresolved_runs_skip_active_claims_but_include_stale_claims(self):
        controller = load_enrichment_controller()
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            active = runs_dir / "20260726T010101Z"
            stale = runs_dir / "20260726T020202Z"
            self.write_manifest(active)
            self.write_manifest(stale)
            (active / "controller-claim.json").write_text(
                json.dumps({"lease_expires_at": (now + timedelta(hours=1)).isoformat()}) + "\n",
                encoding="utf-8",
            )
            (stale / "controller-claim.json").write_text(
                json.dumps({"lease_expires_at": (now - timedelta(seconds=1)).isoformat()}) + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(controller, "ROOT", tmp_root),
                mock.patch.object(controller, "RUNS_DIR", runs_dir),
            ):
                runs = controller.unresolved_runs(now=now)

        self.assertEqual([run["run_id"] for run in runs], ["20260726T020202Z"])

    def test_claim_next_atomically_leases_oldest_available_run(self):
        controller = load_enrichment_controller()
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            oldest = runs_dir / "20260726T010101Z"
            newest = runs_dir / "20260726T020202Z"
            self.write_manifest(oldest)
            self.write_manifest(newest)

            with (
                mock.patch.object(controller, "ROOT", tmp_root),
                mock.patch.object(controller, "RUNS_DIR", runs_dir),
                mock.patch.object(controller.uuid, "uuid4", side_effect=["claim-one", "claim-two"]),
            ):
                first = controller.claim_next(owner="nightly-monolith", lease_seconds=3600, now=now)
                second = controller.claim_next(owner="nightly-monolith", lease_seconds=3600, now=now)
                third = controller.claim_next(owner="nightly-monolith", lease_seconds=3600, now=now)

            first_claim = json.loads((oldest / "controller-claim.json").read_text(encoding="utf-8"))
            second_claim = json.loads((newest / "controller-claim.json").read_text(encoding="utf-8"))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertEqual(first["run_id"], "20260726T010101Z")
        self.assertEqual(first["claim"]["claim_id"], "claim-one")
        self.assertEqual(second["run_id"], "20260726T020202Z")
        self.assertEqual(first_claim["owner"], "nightly-monolith")
        self.assertEqual(second_claim["claim_id"], "claim-two")
        self.assertIsNone(third)

    def test_claim_next_replaces_a_stale_lease(self):
        controller = load_enrichment_controller()
        now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runs_dir = tmp_root / "cache" / "enrichment" / "runs"
            run_dir = runs_dir / "20260726T010101Z"
            self.write_manifest(run_dir)
            (run_dir / "controller-claim.json").write_text(
                json.dumps(
                    {
                        "claim_id": "expired-claim",
                        "lease_expires_at": (now - timedelta(hours=1)).isoformat(),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(controller, "ROOT", tmp_root),
                mock.patch.object(controller, "RUNS_DIR", runs_dir),
                mock.patch.object(controller.uuid, "uuid4", return_value="replacement-claim"),
            ):
                claimed = controller.claim_next(owner="nightly-monolith", lease_seconds=3600, now=now)

            claim = json.loads((run_dir / "controller-claim.json").read_text(encoding="utf-8"))

        self.assertIsNotNone(claimed)
        self.assertEqual(claim["claim_id"], "replacement-claim")


if __name__ == "__main__":
    unittest.main()
