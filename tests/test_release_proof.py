import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from release_proof.cli import main
from release_proof.core import ManifestError, create_manifest, verify_manifest


class ReleaseProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.file = self.dist / "app.apk"
        self.file.write_bytes(b"release bytes\n")
        self.manifest = self.root / "release.json"

    def test_create_and_verify(self):
        created = create_manifest(self.manifest, "1.0.0", [self.file], "https://example.org/releases")
        self.assertEqual(created["artifacts"][0]["sha256"], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(created["artifacts"][0]["url"], "https://example.org/releases/dist/app.apk")
        loaded, results = verify_manifest(self.manifest)
        self.assertEqual(loaded["release"], "1.0.0")
        self.assertEqual(results, [{"path": "dist/app.apk", "scope": "local", "status": "pass"}])

    def test_changed_artifact_fails(self):
        create_manifest(self.manifest, "1.0.0", [self.file])
        self.file.write_bytes(b"changed bytes\n")
        _, results = verify_manifest(self.manifest)
        self.assertEqual(results[0]["status"], "fail")
        self.assertIn("SHA-256", results[0]["reason"])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["verify", str(self.manifest), "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue())["results"][0]["status"], "fail")

    def test_rejects_path_traversal(self):
        manifest = {
            "schema_version": 1,
            "release": "1.0.0",
            "artifacts": [{"path": "../private.txt", "size": 1, "sha256": "0" * 64}],
        }
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "unsafe artifact path"):
            verify_manifest(self.manifest)

    def test_rejects_duplicate_json_keys(self):
        self.manifest.write_text('{"schema_version":1,"release":"one","release":"two","artifacts":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ManifestError, "duplicate JSON key"):
            verify_manifest(self.manifest)

    def test_missing_remote_url_fails(self):
        create_manifest(self.manifest, "1.0.0", [self.file])
        _, results = verify_manifest(self.manifest, remote=True)
        self.assertEqual([r["status"] for r in results], ["pass", "fail"])
        self.assertIn("no URL", results[1]["reason"])

    def test_remote_hash_mismatch_fails(self):
        create_manifest(self.manifest, "1.0.0", [self.file], "https://example.org/releases")
        with patch("release_proof.core._hash_url", return_value=(14, "0" * 64)):
            _, results = verify_manifest(self.manifest, remote=True)
        self.assertEqual(results[0]["status"], "pass")
        self.assertEqual(results[1]["status"], "fail")
        self.assertIn("SHA-256", results[1]["reason"])

    def test_remote_download_hashes_body(self):
        create_manifest(self.manifest, "1.0.0", [self.file], "https://example.org/releases")

        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/releases/dist/app.apk"

        with patch("release_proof.core.build_opener") as opener:
            opener.return_value.open.return_value = Response(self.file.read_bytes())
            _, results = verify_manifest(self.manifest, remote=True)
        self.assertEqual([result["status"] for result in results], ["pass", "pass"])

    def test_create_refuses_outside_file_and_overwrite(self):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        outside = Path(external.name) / "outside.bin"
        outside.write_bytes(b"outside")
        with self.assertRaisesRegex(ManifestError, "inside the output directory"):
            create_manifest(self.manifest, "1.0.0", [outside])
        create_manifest(self.manifest, "1.0.0", [self.file])
        with self.assertRaisesRegex(ManifestError, "manifest exists"):
            create_manifest(self.manifest, "1.0.0", [self.file])

    def test_rejects_http_url(self):
        with self.assertRaisesRegex(ManifestError, "HTTPS"):
            create_manifest(self.manifest, "1.0.0", [self.file], "http://example.org")

    def test_rejects_bad_timeout(self):
        create_manifest(self.manifest, "1.0.0", [self.file])
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["verify", str(self.manifest), "--timeout", "nan"]), 2)
        self.assertIn("positive finite", error.getvalue())


if __name__ == "__main__":
    unittest.main()
