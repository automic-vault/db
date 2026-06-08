import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "bootstrap"))

from scripts.bootstrap.build import Step, should_run


class BuildPipelineTests(unittest.TestCase):
    def test_refresh_sensitive_steps_run_on_every_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.txt"
            output_path = root / "output.txt"
            input_path.write_text("input", encoding="utf-8")
            output_path.write_text("output", encoding="utf-8")
            step = Step(
                "brew-fetch",
                ["python", "fetch.py"],
                [input_path],
                [output_path],
                refresh_sensitive=True,
            )
            run, fp = should_run(step, {"brew-fetch": "stale"}, refresh=True, force=False)
            state = {"brew-fetch": fp}
            non_refresh_fp = should_run(step, {}, refresh=False, force=False)[1]

            self.assertTrue(run)
            self.assertTrue(should_run(step, state, refresh=True, force=False)[0])
            self.assertFalse(should_run(step, {"brew-fetch": non_refresh_fp}, refresh=False, force=False)[0])


if __name__ == "__main__":
    unittest.main()
