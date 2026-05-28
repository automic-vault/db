import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.bootstrap.lib.render import validate_curated_fields
from scripts.enrichment import (
    apply_results,
    curation_facts,
    hash_curation_facts,
    hash_source_facts,
    needs_new_curation,
    normalize_docs,
    normalize_tags,
    parse_project_yaml,
    select_projects,
    update_observed_state,
)


def sample_record():
    return {
        "id": "brew:bat",
        "display-name": "bat",
        "homepage": "https://github.com/sharkdp/bat/",
        "repo": "https://github.com/sharkdp/bat",
        "package-manager": {"brew": "bat"},
        "package-manager-url": "https://formulae.brew.sh/formula/bat",
        "version": "0.26.1",
        "license": "Apache-2.0 OR MIT",
        "tags": ["cli"],
        "description": "Clone of cat(1) with syntax highlighting and Git integration",
        "source-archive": "https://github.com/sharkdp/bat/archive/refs/tags/v0.26.1.tar.gz",
        "executables": ["bat"],
        "provenance": {"provider": "brew", "source": "https://formulae.brew.sh/api/formula.json", "formula": "bat"},
    }


def sample_result(**overrides):
    result = {
        "id": "brew:bat",
        "display-name": "bat",
        "display-name-confidence": "high",
        "category_path": ["developer-tools"],
        "category-confidence": "high",
        "docs": ["https://github.com/sharkdp/bat?utm_source=x#readme"],
        "docs-confidence": "high",
        "tags": ["cli-tool", "git", "k8s"],
        "tags-confidence": "high",
        "docs_sources": ["README"],
        "category_sources": ["README"],
        "tags_sources": ["README"],
        "display_name_sources": ["GitHub About"],
    }
    result.update(overrides)
    return result


class EnrichmentTests(unittest.TestCase):
    def test_source_hash_excludes_curated_fields(self):
        before = sample_record()
        after = sample_record()
        after["docs"] = ["https://github.com/sharkdp/bat#readme"]
        after["category"] = "developer-tools"
        after["tags"] = ["cli", "git"]
        after["display-name"] = "bat"
        self.assertEqual(hash_source_facts(before), hash_source_facts(after))
        self.assertNotEqual(hash_curation_facts(before), hash_curation_facts(after))

    def test_normalization_before_hashing(self):
        first = sample_record()
        second = sample_record()
        second["homepage"] = "https://github.com/sharkdp/bat"
        self.assertEqual(hash_source_facts(first), hash_source_facts(second))

    def test_new_mode_treats_slug_display_name_as_missing(self):
        record = sample_record()
        self.assertTrue(needs_new_curation(record))
        entry = {"last_verified": date.today().isoformat(), "field_confidence": {"display-name": "high"}}
        record["docs"] = ["https://github.com/sharkdp/bat#readme"]
        record["category"] = "developer-tools"
        record["tags"] = ["cli", "git"]
        self.assertFalse(needs_new_curation(record, entry))
        record["display-name"] = "Bat"
        self.assertFalse(needs_new_curation(record))

    def test_review_stale_updated_selection(self):
        record = sample_record()
        today = date.today()
        state = {
            "brew:bat": {
                "last_source_change": today.isoformat(),
                "last_verified": (today - timedelta(days=91)).isoformat(),
                "field_confidence": {"docs": "high"},
            }
        }
        selected = select_projects([record], state, mode="review-stale-updated", today=today.isoformat())
        self.assertEqual([item["id"] for item in selected], ["brew:bat"])

    def test_low_confidence_skips_non_empty_field_but_caches_review(self):
        record = sample_record()
        record["category"] = "developer-tools"
        record["__path"] = Path("unused.yml")
        state = {"brew:bat": {"field_ownership": {"category": "managed"}, "managed_values": {"category": "developer-tools"}}}
        summary = apply_results(
            {"brew:bat": record},
            state,
            [sample_result(**{"category_path": ["security"], "category-confidence": "low"})],
            confidence_threshold="medium",
            today=date.today().isoformat(),
            dry_run=True,
        )
        self.assertEqual(record["category"], "developer-tools")
        self.assertEqual(summary["skipped_low_confidence"], 1)
        self.assertEqual(state["brew:bat"]["field_confidence"]["category"], "low")

    def test_manual_tags_are_preserved_and_codex_tags_are_normalized(self):
        record = sample_record()
        record["tags"] = ["cli", "manual"]
        state = {"brew:bat": {"field_ownership": {"tags": "manual"}, "managed_values": {"tags": ["cli"]}}}
        apply_results(
            {"brew:bat": record},
            state,
            [sample_result()],
            confidence_threshold="medium",
            today=date.today().isoformat(),
            dry_run=True,
        )
        self.assertEqual(record["tags"], ["cli", "git", "kubernetes", "manual"])

    def test_docs_ranking_rejects_package_manager_and_tracking(self):
        docs = normalize_docs(
            [
                "https://formulae.brew.sh/formula/bat",
                "https://github.com/sharkdp/bat/wiki/",
                "https://docs.rs/bat?utm_campaign=x",
                "https://example.com/blog/bat-tutorial",
            ]
        )
        self.assertEqual(docs, ["https://docs.rs/bat", "https://github.com/sharkdp/bat/wiki"])

    def test_tag_canonicalization(self):
        self.assertEqual(normalize_tags(["cli-tool", "k8s", "awscli", "utility"]), ["aws", "cli", "kubernetes"])

    def test_parse_project_yaml_round_trip_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bat.yml"
            path.write_text(
                "id: brew:bat\n"
                "display-name: Bat\n"
                "docs:\n"
                "  - https://github.com/sharkdp/bat#readme\n"
                "category: developer-tools\n"
                "tags:\n"
                "  - cli\n"
                "  - git\n"
                "package-manager:\n"
                "  brew: bat\n",
                encoding="utf-8",
            )
            record = parse_project_yaml(path)
        self.assertEqual(record["docs"], ["https://github.com/sharkdp/bat#readme"])
        self.assertEqual(record["package-manager"], {"brew": "bat"})
        self.assertEqual(curation_facts(record)["category"], "developer-tools")

    def test_update_observed_state_marks_manual_field(self):
        record = sample_record()
        record["tags"] = ["cli", "manual"]
        state = {"brew:bat": {"managed_values": {"tags": ["cli"]}, "field_ownership": {"tags": "managed"}}}
        update_observed_state(state, [record], date.today().isoformat())
        self.assertEqual(state["brew:bat"]["field_ownership"]["tags"], "manual")

    def test_validation_allows_curated_docs_and_category(self):
        failures = validate_curated_fields(
            Path("bat.yml"),
            "id: brew:bat\n"
            "docs:\n"
            "  - https://github.com/sharkdp/bat#readme\n"
            "category: developer-tools\n"
            "tags:\n"
            "  - cli\n"
            "  - git\n",
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
