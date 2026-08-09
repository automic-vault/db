import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAINTENANCE = ROOT / "scripts" / "atlas-maintenance.sh"
CONTROLLER = ROOT / "scripts" / "nightly-discover-feed.sh"


class NightlyDiscoverFeedTests(unittest.TestCase):
    def test_maintenance_runs_feed_after_database_health_check(self):
        script = MAINTENANCE.read_text()
        health_check = 'curl -fsS http://127.0.0.1:3004/healthz >/dev/null'
        feed_stage = 'scripts/nightly-discover-feed.sh'
        self.assertIn(health_check, script)
        self.assertIn(feed_stage, script)
        self.assertLess(script.index(health_check), script.index(feed_stage))

    def test_controller_reenters_codex_and_publishes_only_after_validation(self):
        script = CONTROLLER.read_text()
        self.assertIn('PMM_FEED_STATUS=NEEDS_AGENT', script)
        self.assertIn('codex --search --ask-for-approval never exec', script)
        self.assertIn('"${repo_root}/scripts/update-discover-feed" --check', script)
        self.assertIn('git push origin main', script)
        self.assertIn('"${repo_root}/scripts/publish-discover-feed-atlas.sh"', script)
        self.assertLess(
            script.index('"${repo_root}/scripts/update-discover-feed" --check'),
            script.index('"${repo_root}/scripts/publish-discover-feed-atlas.sh"'),
        )


if __name__ == "__main__":
    unittest.main()
