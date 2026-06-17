import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load_publish_public_db():
    path = Path(__file__).resolve().parents[1] / "scripts" / "publish-public-db.py"
    spec = importlib.util.spec_from_file_location("publish_public_db", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublishPublicDbTests(unittest.TestCase):
    def test_upload_command_publishes_combined_json_to_public_db_key(self):
        publish = load_publish_public_db()
        command = publish.upload_command(
            Path("cache/automic-vault/combined.json"),
            "example.com",
            "db.json",
            cache_control="public, no-cache",
            content_type="application/json; charset=utf-8",
        )

        self.assertEqual(
            command,
            [
                "aws",
                "s3",
                "cp",
                "cache/automic-vault/combined.json",
                "s3://example.com/db.json",
                "--content-type",
                "application/json; charset=utf-8",
                "--cache-control",
                "public, no-cache",
            ],
        )

    def test_verify_generated_at_rejects_stale_remote_payload(self):
        publish = load_publish_public_db()

        with self.assertRaisesRegex(publish.PublishFailed, "expected 2026-06-17"):
            publish.verify_generated_at(
                "2026-06-17T13:01:56Z",
                {"generated_at": "2026-06-11T15:09:29Z"},
                "s3://example.com/db.json",
            )

    def test_read_json_requires_generated_at(self):
        publish = load_publish_public_db()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.json"
            path.write_text('{"schema": 1}\n', encoding="utf-8")
            payload = publish.read_json(path)

        with self.assertRaisesRegex(publish.PublishFailed, "missing generated_at"):
            publish.generated_at(payload, str(path))

    def test_check_only_does_not_upload(self):
        publish = load_publish_public_db()

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "combined.json"
            source.write_text('{"generated_at": "2026-06-17T13:01:56Z", "schema": 1}\n', encoding="utf-8")
            with mock.patch.object(sys, "argv", ["publish-public-db.py", "--source", str(source), "--check-only"]):
                with mock.patch.object(publish, "run") as run:
                    with mock.patch.object(
                        publish,
                        "fetch_s3_json",
                        return_value={"generated_at": "2026-06-17T13:01:56Z"},
                    ):
                        with mock.patch.object(
                            publish,
                            "fetch_public_json",
                            return_value={"generated_at": "2026-06-17T13:01:56Z"},
                        ):
                            with contextlib.redirect_stdout(io.StringIO()):
                                self.assertEqual(publish.main(), 0)

        self.assertEqual(run.call_args_list, [])


if __name__ == "__main__":
    unittest.main()
