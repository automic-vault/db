import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.bootstrap.lib import executables
from scripts.bootstrap.lib import authority
from scripts.bootstrap.lib.authority import build_automic_vault_db, formula_metadata_from_project_yaml, stable_cask_metadata


class ExecutableSeedTests(unittest.TestCase):
    def test_project_yaml_seed_uses_brew_id_and_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "awscli.yml").write_text(
                "\n".join(
                    [
                        "id: brew:awscli",
                        "display-name: AWS CLI",
                        "executables:",
                        "  - aws",
                        "  - aws_completer",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "npm.yml").write_text(
                "\n".join(
                    [
                        "id: npm:aws-cdk",
                        "executables:",
                        "  - cdk",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                executables.executable_index_from_project_yaml(root),
                {"awscli": ["aws", "aws_completer"]},
            )

    def test_seed_prefers_combined_then_falls_back_to_deterministic(self):
        with tempfile.TemporaryDirectory() as combined, tempfile.TemporaryDirectory() as deterministic:
            deterministic_root = Path(deterministic)
            (deterministic_root / "jq.yml").write_text(
                "id: brew:jq\nexecutables:\n  - jq\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(executables, "COMBINED_DIR", Path(combined)),
                mock.patch.object(executables, "DETERMINISTIC_DIR", deterministic_root),
            ):
                self.assertEqual(executables.seed_executables_from_source(), {"jq": ["jq"]})

            combined_root = Path(combined)
            (combined_root / "bat.yml").write_text(
                "id: brew:bat\nexecutables:\n  - bat\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(executables, "COMBINED_DIR", combined_root),
                mock.patch.object(executables, "DETERMINISTIC_DIR", deterministic_root),
            ):
                self.assertEqual(executables.seed_executables_from_source(), {"bat": ["bat"]})

    def test_executable_entries_export_is_automic_vault_seed_shape(self):
        index = {
            "awscli": ["aws_completer", "aws"],
            "bat": ["bat"],
        }

        self.assertEqual(
            executables.executable_entries_from_index(index),
            {
                "aws": "awscli",
                "aws_completer": "awscli",
                "bat": "bat",
            },
        )

    def test_automic_vault_db_export_uses_project_yaml_as_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "awscli.yml").write_text(
                "\n".join(
                    [
                        "id: brew:awscli",
                        "homepage: https://aws.amazon.com/cli/",
                        "repo: https://github.com/aws/aws-cli",
                        "docs:",
                        "  - https://docs.aws.amazon.com/cli/latest/userguide",
                        "category: cloud-infrastructure",
                        "description: Official Amazon AWS command-line interface",
                        "executables:",
                        "  - aws",
                        "  - aws_completer",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            formulae = [
                {
                    "name": "awscli",
                    "aliases": ["awscli@2"],
                    "oldnames": ["awscli-old"],
                }
            ]

            with mock.patch.object(
                authority,
                "read_cask_authority",
                return_value=({"op": "cask:1password-cli"}, {"1password-cli": {"binaries": [{"source": "op", "target": "op"}]}}),
            ):
                db = build_automic_vault_db(
                    root,
                    formulae,
                    generated_at="2026-06-01T00:00:00+00:00",
                )

        self.assertEqual(db["schema"], 7)
        self.assertEqual(db["generated_at"], "2026-06-01T00:00:00+00:00")
        self.assertEqual(db["entries"]["aws"], "awscli")
        self.assertEqual(db["entries"]["aws_completer"], "awscli")
        self.assertEqual(
            db["formulas"]["awscli"],
            {
                "summary": "Official Amazon AWS command-line interface",
                "homepage": "https://aws.amazon.com/cli/",
                "repository": "https://github.com/aws/aws-cli",
                "docs": ["https://docs.aws.amazon.com/cli/latest/userguide"],
                "upstreamDocs": "https://docs.aws.amazon.com/cli/latest/userguide",
                "category": "cloud-infrastructure",
                "aliases": ["awscli@2"],
                "oldnames": ["awscli-old"],
            },
        )
        self.assertNotIn("repo", db["formulas"]["awscli"])
        self.assertEqual(db["entries"]["op"], "cask:1password-cli")
        self.assertIn("1password-cli", db["casks"])
        self.assertEqual(db["npms"], {})

    def test_automic_vault_db_export_strips_volatile_cask_metadata(self):
        self.assertEqual(
            stable_cask_metadata(
                {
                    "1password-cli": {
                        "summary": "1Password CLI",
                        "version": "2.0.0",
                        "sourceArchive": "https://example.com/op.zip",
                        "url": "https://example.com/op.zip",
                        "sha256": "abc123",
                        "binaries": [{"source": "op", "target": "op"}],
                    }
                }
            ),
            {
                "1password-cli": {
                    "summary": "1Password CLI",
                    "binaries": [{"source": "op", "target": "op"}],
                }
            },
        )

    def test_formula_metadata_export_reads_aliases_from_formula_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "jq.yml").write_text(
                "id: brew:jq\ndescription: Lightweight and flexible command-line JSON processor\n",
                encoding="utf-8",
            )

            metadata = formula_metadata_from_project_yaml(
                root,
                [{"name": "jq", "aliases": ["jqlang"], "oldnames": []}],
            )

        self.assertEqual(
            metadata,
            {
                "jq": {
                    "summary": "Lightweight and flexible command-line JSON processor",
                    "aliases": ["jqlang"],
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
