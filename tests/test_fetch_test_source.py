import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fetch_test_source


class SourceFetchTests(unittest.TestCase):
    MODEL_SOURCES = ("dom/base/nsGlobalWindowOuter.cpp", "js/src/vm/WindowProxy.cpp")

    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["FIREFOX_SOURCE"])
        cls.manifest = json.loads((ROOT / "baseline.json").read_text())

    def response(self, url, timeout):
        self.assertEqual(timeout, 60)
        prefix = ("https://raw.githubusercontent.com/mozilla-firefox/firefox/"
                  + self.manifest["firefox"]["commit"] + "/")
        self.assertTrue(url.startswith(prefix), url)
        path = url.removeprefix(prefix)
        return io.BytesIO((self.source / path).read_bytes())

    def test_fetches_validated_model_sources_without_adding_them_to_the_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            with patch.object(sys, "argv", ["fetch_test_source.py", directory]), \
                 patch.object(fetch_test_source.urllib.request, "urlopen", side_effect=self.response), \
                 contextlib.redirect_stdout(io.StringIO()):
                fetch_test_source.main()
            for path in (*fetch_test_source.PATHS, *self.MODEL_SOURCES):
                self.assertTrue((destination / path).is_file(), f"Missing test prerequisite: {path}")
                self.assertEqual((destination / path).read_bytes(), (self.source / path).read_bytes())
            self.assertTrue(set(self.MODEL_SOURCES).isdisjoint(fetch_test_source.PATHS))

    def test_rejects_corrupt_additional_source_before_writing_it(self):
        corrupted = self.MODEL_SOURCES[0]

        def response(url, timeout):
            data = self.response(url, timeout).read()
            return io.BytesIO(data + b"\n" if url.endswith(corrupted) else data)

        with tempfile.TemporaryDirectory() as directory:
            errors = io.StringIO()
            with patch.object(sys, "argv", ["fetch_test_source.py", directory]), \
                 patch.object(fetch_test_source.urllib.request, "urlopen", side_effect=response), \
                 contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    fetch_test_source.main()
            self.assertEqual(raised.exception.code, 1)
            self.assertIn("Upstream hash mismatch: " + corrupted, errors.getvalue())
            self.assertFalse((Path(directory) / corrupted).exists())
