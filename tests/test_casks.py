import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.bootstrap.lib.casks import (
    app_catalog_from_casks,
    cask_metadata,
    collect_cask_entries,
    parse_binary_artifact,
)
from scripts.bootstrap.lib.render import cask_project_record


def load_public_db_export():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts/bootstrap"))
    spec = importlib.util.spec_from_file_location(
        "export_public_db",
        root / "scripts/bootstrap/05-export-automic-vault-db.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CaskAuthorityTests(unittest.TestCase):
    def test_public_export_preserves_the_combined_database_contract(self):
        exporter = load_public_db_export()
        authority = {
            "schema": 8,
            "generated_at": "2026-08-05T12:00:00Z",
            "entries": {},
            "formulas": {},
            "casks": {},
            "npms": {},
            "crates": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {name: root / f"{name}.json" for name in exporter.PUBLIC_SOURCES}
            for source in sources.values():
                source.write_text("{}\n", encoding="utf-8")
            output = root / "db.json"
            with (
                mock.patch.object(exporter, "PUBLIC_SOURCES", sources),
                mock.patch.object(exporter, "build_automic_vault_db", return_value=authority),
                mock.patch.object(exporter, "read_cask_cache", return_value=[]),
                mock.patch.object(exporter, "app_catalog_from_casks", return_value=({}, {})),
            ):
                exporter.write_public_db(output)

            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(document["schema"], 1)
        self.assertEqual(document["generated_at"], authority["generated_at"])
        self.assertEqual(document["sources"]["db"]["schema"], 8)
        self.assertIn("security-recommendations", document["sources"])

    def test_app_catalog_associates_an_app_cask_by_quit_bundle_identifier(self):
        apps, casks = app_catalog_from_casks([
            {
                "token": "chatgpt",
                "desc": "OpenAI's official ChatGPT desktop app",
                "homepage": "https://chatgpt.com/",
                "version": "26.730.61639",
                "artifacts": [
                    {"uninstall": [{"quit": "com.openai.codex"}]},
                    {"app": ["ChatGPT.app"], "target": "/Applications/ChatGPT.app"},
                    {"zap": [{"trash": [
                        "~/Library/Caches/com.openai.sky.CUAService",
                        "~/Library/Preferences/com.openai.sky.CUAService.plist",
                    ]}]},
                ],
            }
        ])

        self.assertEqual(apps, {"com.openai.codex": {"cask": "chatgpt", "version_source": "cask"}})
        self.assertEqual(casks["chatgpt"]["version"], "26.730.61639")

    def test_app_catalog_associates_vlc_by_repeated_zap_bundle_identifier(self):
        apps, casks = app_catalog_from_casks([
            {
                "token": "vlc",
                "desc": "Multimedia player",
                "homepage": "https://www.videolan.org/vlc/",
                "version": "3.0.23",
                "artifacts": [
                    {"app": ["VLC.app"], "target": "/Applications/VLC.app"},
                    {"zap": [{"trash": [
                        "~/Library/Application Support/org.videolan.vlc",
                        "~/Library/Caches/org.videolan.vlc",
                        "~/Library/Preferences/org.videolan.vlc.plist",
                    ]}]},
                ],
            }
        ])

        self.assertEqual(apps, {"org.videolan.vlc": {"cask": "vlc", "version_source": "cask"}})
        self.assertEqual(casks["vlc"]["version"], "3.0.23")

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
