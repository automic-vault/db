import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.bootstrap.lib import executables


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

    def test_formula_executables_seed_allows_formula_cli_name_mismatch(self):
        formulae = [
            {
                "name": "sem-cli",
                "executables": ["sem"],
            }
        ]

        with mock.patch.object(executables, "seed_executables_from_source", return_value={}):
            self.assertEqual(
                executables.build_executable_index(formulae, fetch_manifests=False),
                {"sem-cli": ["sem"]},
            )

    def test_formula_executables_keep_colliding_formulae_in_package_index(self):
        formulae = [
            {"name": "parallel", "executables": ["parallel", "sem"]},
            {"name": "sem-cli", "executables": ["sem"]},
        ]

        with mock.patch.object(executables, "seed_executables_from_source", return_value={}):
            index = executables.build_executable_index(formulae, fetch_manifests=False)

        self.assertEqual(index["parallel"], ["parallel", "sem"])
        self.assertEqual(index["sem-cli"], ["sem"])



if __name__ == "__main__":
    unittest.main()
