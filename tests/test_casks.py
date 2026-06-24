import unittest

from scripts.bootstrap.lib.casks import cask_metadata, collect_cask_entries, parse_binary_artifact
from scripts.bootstrap.lib.render import cask_project_record


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
                "sourceArchive": "https://example.com/example.zip",
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

    def test_cask_project_record_renders_binary_cask_as_public_cli(self):
        self.assertEqual(
            cask_project_record(
                "codex",
                {
                    "summary": "OpenAI's coding agent that runs in your terminal",
                    "homepage": "https://github.com/openai/codex",
                    "url": "https://github.com/openai/codex/releases/download/rust-v0.142.0/codex-aarch64-apple-darwin.tar.gz",
                    "version": "0.142.0",
                    "aliases": ["codex-cli"],
                },
                ["codex"],
            ),
            {
                "id": "cask:codex",
                "display-name": "codex",
                "homepage": "https://github.com/openai/codex",
                "repo": "https://github.com/openai/codex",
                "package-manager": {"brew-cask": "codex"},
                "package-manager-url": "https://formulae.brew.sh/cask/codex",
                "version": "0.142.0",
                "description": "OpenAI's coding agent that runs in your terminal",
                "source-archive": "https://github.com/openai/codex/releases/download/rust-v0.142.0/codex-aarch64-apple-darwin.tar.gz",
                "executables": ["codex"],
                "provenance": {
                    "provider": "brew-cask",
                    "source": "https://formulae.brew.sh/api/cask.json",
                    "cask": "codex",
                },
                "aliases": ["codex-cli"],
            },
        )


if __name__ == "__main__":
    unittest.main()
