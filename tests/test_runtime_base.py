import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import runtime_base


class RuntimeBaseTests(unittest.TestCase):
    def setUp(self):
        self.config = {"schema": 1, "repository": "owner/repo", "run_id": 12,
                       "head_sha": "a" * 40, "artifact_name": "candidate"}
        self.run = {"id": 12, "head_sha": "a" * 40, "repository": {"full_name": "owner/repo"},
                    "path": ".github/workflows/native-browser.yml"}
        self.artifact = {"id": 34, "name": "candidate", "expired": False,
                         "workflow_run": {"id": 12, "head_sha": "a" * 40}, "digest": "sha256:" + "b" * 64}

    def select(self, artifacts=None):
        return runtime_base.select_artifact(self.config, self.run,
                                            [self.artifact] if artifacts is None else artifacts)

    def test_selects_only_the_unique_unexpired_pinned_artifact(self):
        result = self.select([dict(self.artifact, expired=True), self.artifact])
        self.assertEqual(result["artifact"]["id"], 34)
        for artifacts in ([], [dict(self.artifact, expired=True)], [self.artifact, self.artifact]):
            with self.subTest(artifacts=artifacts):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    self.select(artifacts)

    def test_rejects_wrong_run_source_repository_workflow_and_artifact_identity(self):
        for key, value in (("id", 99), ("head_sha", "f" * 40),
                           ("repository", {"full_name": "other/repo"}), ("path", "other.yml")):
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "run does not match"):
                runtime_base.select_artifact(self.config, dict(self.run, **{key: value}), [self.artifact])
        for change in ({"digest": None}, {"id": 0}, {"workflow_run": {"id": 99, "head_sha": "a" * 40}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.select([dict(self.artifact, **change)])

    def test_absent_config_selects_compilation_but_invalid_config_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, output = root / "runtime-base.json", root / "output"
            argv = ["runtime_base.py", "select", "--config", str(config),
                    "--selection", str(root / "selection"), "--github-output", str(output)]
            with patch.object(sys, "argv", argv), patch.object(runtime_base, "api_json") as api:
                runtime_base.main()
                api.assert_not_called()
                self.assertEqual(output.read_text(), "enabled=false\n")
                config.write_text('{}')
                output.write_text('')
                with self.assertRaises(ValueError):
                    runtime_base.main()
                self.assertEqual(output.read_text(), '')

    def test_signed_storage_request_never_contains_the_api_bearer(self):
        selection = self.select()
        redirect = urllib.error.HTTPError("https://api.github.com/artifact", 302, "redirect",
                                          {"Location": "https://storage.example/artifact?signed=value"}, None)
        with patch.object(runtime_base, "api_request", side_effect=redirect), \
                patch.object(runtime_base.urllib.request, "build_opener") as build:
            runtime_base.artifact_stream(selection)
            request = build.return_value.open.call_args.args[0]
            self.assertFalse(request.has_header("Authorization"))
            self.assertFalse(request.has_header("Cookie"))
            self.assertEqual(request.full_url, "https://storage.example/artifact?signed=value")
            self.assertIsInstance(build.call_args.args[0], runtime_base.HTTPSRedirect)
        redirect.headers["Location"] = "http://storage.example/artifact"
        with patch.object(runtime_base, "api_request", side_effect=redirect), \
                self.assertRaisesRegex(ValueError, "HTTPS"):
            runtime_base.artifact_stream(selection)

    def test_authenticated_api_opener_does_not_follow_redirects(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-bearer"}), \
                patch.object(runtime_base.urllib.request, "build_opener") as build:
            runtime_base.api_request("/repos/owner/repo/actions/runs/12")
            self.assertIsInstance(build.call_args.args[0], runtime_base.NoRedirect)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer test-bearer")
            self.assertIsNone(runtime_base.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://storage.example'))

    def test_authenticates_zip_digest_and_every_officially_extracted_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloaded = root / "downloaded"
            downloaded.mkdir()
            archive = root / "artifact.zip"
            payload = {"baseline.json": b'{"schema":1}', "browser.tar.zst": b"compressed candidate"}
            with zipfile.ZipFile(archive, "w") as zipped:
                for name, data in payload.items():
                    zipped.writestr(name, data)
                    (downloaded / name).write_bytes(data)
            selection = self.select()
            selection["artifact"]["digest"] = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
            result = runtime_base.authenticate_zip(selection, archive, downloaded)
            self.assertEqual(result["verified_members_sha256"],
                             {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()})
            with patch.object(runtime_base, "artifact_stream", return_value=io.BytesIO(archive.read_bytes())):
                self.assertEqual(runtime_base.authenticate(selection, downloaded), result)
            (downloaded / "baseline.json").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "downloaded files differ"):
                runtime_base.authenticate_zip(selection, archive, downloaded)
            selection["artifact"]["digest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(ValueError, "ZIP digest mismatch"):
                runtime_base.authenticate_zip(selection, archive, downloaded)

    def test_checksum_manifest_includes_all_composition_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            names = ("baseline.json", "runtime-baseline.json", "runtime-composition.json",
                     "runtime-base.json", "base-artifact-authentication.json", "base-artifact-identity.json", "browser.tar.zst")
            for name in names:
                (package / name).write_text(name)
            runtime_base.write_checksums(package)
            result = (package / "SHA256SUMS").read_text()
            self.assertEqual({line.split("  ")[1] for line in result.splitlines()}, set(names))
            runtime_base.write_checksums(package)
            self.assertEqual((package / "SHA256SUMS").read_text(), result)


if __name__ == "__main__":
    unittest.main()
