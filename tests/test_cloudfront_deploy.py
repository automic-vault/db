import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy-pkg-cloudfront.sh"


class CloudFrontDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = DEPLOY_SCRIPT.read_text()

    def test_pkg_owned_origin_is_default(self):
        self.assertIn(
            'origin_domain="${PKG_CF_ORIGIN_DOMAIN:-origin.pkg.so}"',
            self.script,
        )

    def test_flattened_search_behaviors_use_query_string_cache_policy(self):
        for path in (
            "search.json",
            "de/search.json",
            "fr/search.json",
            "ja/search.json",
            "zh-hans/search.json",
        ):
            self.assertIn(f'PathPattern: "{path}"', self.script)

        for old_path in (
            "pkg/search.json",
            "de/pkg/search.json",
            "fr/pkg/search.json",
            "ja/pkg/search.json",
            "zh-hans/pkg/search.json",
        ):
            self.assertNotIn(f'PathPattern: "{old_path}"', self.script)

        self.assertIn(
            'search_behavior_json="$(behavior_json "${search_cache_policy_id}")"',
            self.script,
        )
        self.assertIn(
            'QueryStrings: {Quantity: 4, Items: ["q", "offset", "limit", "locale"]}',
            self.script,
        )


if __name__ == "__main__":
    unittest.main()
