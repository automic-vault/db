import unittest

from scripts.bootstrap.lib.casks import cask_metadata, collect_cask_entries, parse_binary_artifact


class CaskAuthorityTests(unittest.TestCase):
    def test_parse_binary_artifact_supports_target_forms(self):
        self.assertEqual(
            parse_binary_artifact({"binary": "op"}),
            {"source": "op", "target": None},
        )
        self.assertEqual(
            parse_binary_artifact({"binary": ["bin/tool", {"target": "renamed"}]}),
            {"source": "bin/tool", "target": "renamed"},
        )
        self.assertEqual(
            parse_binary_artifact({"binary": "bin/tool", "target": "/usr/local/bin/tool"}),
            {"source": "bin/tool", "target": "tool"},
        )

    def test_cask_metadata_accepts_binary_only_casks(self):
        metadata = cask_metadata(
            {
                "token": "example-cli",
                "desc": "Example CLI",
                "homepage": "https://example.com",
                "url": "https://example.com/example.zip",
                "sha256": "abc123",
                "version": "1.2.3",
                "old_tokens": ["old-example-cli"],
                "depends_on": {"formula": ["jq"]},
                "artifacts": [
                    {"binary": ["example", {"target": "ex"}]},
                    {"generate_completions_from_executable": "ex"},
                    {"zap": ["~/Library/Application Support/Example"]},
                ],
            }
        )

        self.assertEqual(
            metadata,
            {
                "summary": "Example CLI",
                "homepage": "https://example.com",
                "aliases": ["old-example-cli"],
                "url": "https://example.com/example.zip",
                "sha256": "abc123",
                "version": "1.2.3",
                "dependencies": ["jq"],
                "binaries": [{"source": "example", "target": "ex"}],
            },
        )

    def test_cask_metadata_rejects_non_binary_artifacts(self):
        self.assertIsNone(
            cask_metadata(
                {
                    "token": "example-app",
                    "url": "https://example.com/example.zip",
                    "sha256": "abc123",
                    "version": "1.2.3",
                    "artifacts": [{"app": "Example.app"}],
                }
            )
        )

    def test_collect_cask_entries_exports_automic_vault_provider_names(self):
        entries, metadata = collect_cask_entries(
            [
                {
                    "token": "1password-cli",
                    "desc": "Command-line interface for 1Password",
                    "homepage": "https://developer.1password.com/docs/cli",
                    "url": "https://example.com/op.zip",
                    "sha256": "abc123",
                    "version": "2.0.0",
                    "artifacts": [{"binary": "op"}],
                }
            ]
        )

        self.assertEqual(entries, {"op": "cask:1password-cli"})
        self.assertIn("1password-cli", metadata)


if __name__ == "__main__":
    unittest.main()
