import csv
import datetime as dt
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.bootstrap.lib import crates as crates_index
from tests.test_pkg_page_rendering import pkg_pages


def csv_bytes(fieldnames, rows):
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return handle.getvalue().encode("utf-8")


def add_tar_member(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def write_fixture_dump(path: Path) -> None:
    today = dt.date.today().isoformat()
    old_day = (dt.date.today() - dt.timedelta(days=120)).isoformat()
    files = {
        "data/crates.csv": csv_bytes(
            [
                "id",
                "name",
                "updated_at",
                "created_at",
                "description",
                "homepage",
                "documentation",
                "readme",
                "repository",
                "max_upload_size",
                "max_features",
                "trustpub_only",
            ],
            [
                {
                    "id": "1",
                    "name": "ripgrep",
                    "updated_at": "2026-06-01T00:00:00Z",
                    "created_at": "2020-01-01T00:00:00Z",
                    "description": "fast recursive search",
                    "homepage": "https://github.com/BurntSushi/ripgrep",
                    "documentation": "https://docs.rs/ripgrep",
                    "repository": "https://github.com/BurntSushi/ripgrep",
                },
                {"id": "2", "name": "serde", "description": "serialization framework"},
                {"id": "3", "name": "oldcli", "description": "old command"},
                {"id": "4", "name": "yankedcli", "description": "gone"},
            ],
        ),
        "data/crate_downloads.csv": csv_bytes(
            ["crate_id", "downloads"],
            [
                {"crate_id": "1", "downloads": "1000"},
                {"crate_id": "2", "downloads": "900"},
                {"crate_id": "3", "downloads": "800"},
                {"crate_id": "4", "downloads": "700"},
            ],
        ),
        "data/default_versions.csv": csv_bytes(
            ["crate_id", "version_id", "num_versions"],
            [
                {"crate_id": "1", "version_id": "10", "num_versions": "3"},
                {"crate_id": "2", "version_id": "20", "num_versions": "4"},
                {"crate_id": "3", "version_id": "30", "num_versions": "1"},
                {"crate_id": "4", "version_id": "40", "num_versions": "1"},
            ],
        ),
        "data/versions.csv": csv_bytes(
            [
                "id",
                "crate_id",
                "num",
                "updated_at",
                "created_at",
                "downloads",
                "features",
                "yanked",
                "license",
                "crate_size",
                "published_by",
                "checksum",
                "links",
                "rust_version",
                "has_lib",
                "bin_names",
                "edition",
                "description",
                "homepage",
                "documentation",
                "repository",
            ],
            [
                {
                    "id": "10",
                    "crate_id": "1",
                    "num": "15.1.0",
                    "updated_at": "2026-06-01T00:00:00Z",
                    "created_at": "2026-06-01T00:00:00Z",
                    "yanked": "f",
                    "license": "MIT",
                    "checksum": "abc",
                    "rust_version": "1.85",
                    "has_lib": "f",
                    "bin_names": "{rg}",
                    "edition": "2024",
                    "description": "ripgrep command",
                },
                {"id": "20", "crate_id": "2", "num": "1.0.0", "yanked": "f", "bin_names": "{}"},
                {"id": "30", "crate_id": "3", "num": "1.0.0", "yanked": "f", "bin_names": "{oldcli}"},
                {"id": "40", "crate_id": "4", "num": "1.0.0", "yanked": "t", "bin_names": "{yankedcli}"},
            ],
        ),
        "data/version_downloads.csv": csv_bytes(
            ["version_id", "downloads", "date"],
            [
                {"version_id": "10", "downloads": "75", "date": today},
                {"version_id": "30", "downloads": "1000", "date": old_day},
                {"version_id": "40", "downloads": "1000", "date": today},
            ],
        ),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            add_tar_member(archive, name, data)


class CratesIndexTests(unittest.TestCase):
    def test_parse_pg_text_array_handles_quoted_values(self):
        self.assertEqual(
            crates_index.parse_pg_text_array('{"cargo-audit","cargo auditable","rg"}'),
            ["cargo-audit", "cargo auditable", "rg"],
        )
        self.assertTrue(crates_index.valid_executable_name("cargo-audit"))
        self.assertFalse(crates_index.valid_executable_name("cargo auditable"))

    def test_build_index_from_dump_selects_recent_installable_default_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump_path = Path(tmp) / "db-dump.tar.gz"
            write_fixture_dump(dump_path)

            index = crates_index.build_index_from_dump(
                dump_path,
                min_recent_downloads=50,
                recent_window_days=90,
                dump_meta={"source_url": "fixture"},
            )

        self.assertEqual(list(index["crates"]), ["ripgrep"])
        ripgrep = index["crates"]["ripgrep"]
        self.assertEqual(ripgrep["version"], "15.1.0")
        self.assertEqual(ripgrep["executables"][0]["name"], "rg")
        self.assertEqual(ripgrep["popularity"]["recent_downloads"], 75)
        self.assertEqual(ripgrep["packageManagerUrl"], "https://crates.io/crates/ripgrep")

    def test_crates_index_creates_cargo_package_pages_without_db_entries(self):
        sources = {
            "db": {"entries": {}, "formulas": {}, "casks": {}, "npms": {}},
            "crates": {
                "crates": {
                    "ripgrep": {
                        "summary": "ripgrep command",
                        "version": "15.1.0",
                        "executables": [{"name": "rg", "kind": "binary"}],
                        "packageManager": "Cargo",
                        "packageManagerUrl": "https://crates.io/crates/ripgrep",
                    }
                }
            },
            "geiger": {},
            "isotopes": {},
            "pkg_page_enrichment": {},
            "pkg_version_freshness": {},
            "pkg_graph": {},
            "pkg_cross_ecosystem": {},
            "pkg_agent_safety_answers": {},
        }

        pages = pkg_pages.package_pages_from_sources(sources)
        page = pages["cargo:ripgrep"]

        self.assertEqual(page.provider, "cargo")
        self.assertEqual(page.path, "/pkg/cargo/ripgrep/")
        self.assertEqual(pkg_pages.native_install_command(page), "cargo install ripgrep")
        self.assertNotIn("cargo:ripgrep", sources["db"]["entries"].values())


if __name__ == "__main__":
    unittest.main()
