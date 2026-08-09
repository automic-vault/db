import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "publish-discover-feed-atlas.sh"


class DiscoverFeedPublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = PUBLISHER.read_text()

    def test_publisher_validates_before_switching_current_release(self):
        validation = '"${repo_root}/scripts/update-discover-feed" --check'
        switch = 'sudo ln -sfn "${release}" "${current_link}"'
        self.assertIn(validation, self.script)
        self.assertIn(switch, self.script)
        self.assertLess(self.script.index(validation), self.script.index(switch))

    def test_publisher_keeps_current_and_previous_releases(self):
        self.assertIn('previous="$(sudo readlink -f "${current_link}"', self.script)
        self.assertIn('! -path "${release}" ! -path "${previous}"', self.script)
        self.assertIn('sudo mv -- "${tmp_release}" "${release}"', self.script)


if __name__ == "__main__":
    unittest.main()
