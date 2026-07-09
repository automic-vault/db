import unittest

from scripts.bootstrap.lib.render import parse_simple_yaml


class ParseSimpleYamlTests(unittest.TestCase):
    def test_indented_scalar_override_stays_scalar(self) -> None:
        parsed = parse_simple_yaml(
            "changelog:\n"
            "  https://github.com/aws/aws-cli/blob/v2/CHANGELOG.rst\n"
        )

        self.assertEqual(
            parsed,
            {"changelog": "https://github.com/aws/aws-cli/blob/v2/CHANGELOG.rst"},
        )


if __name__ == "__main__":
    unittest.main()
