import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.request import urlopen
import zlib

import response_body_probe as probe


class ProbeTests(unittest.TestCase):
    def test_stacked_encoding_requires_reverse_order(self):
        body, headers = probe.script_response("stacked", "public, max-age=3600")
        self.assertEqual(headers["Content-Encoding"], "gzip, deflate")
        self.assertEqual(gzip.decompress(zlib.decompress(body)), probe.SCRIPT)
        with self.assertRaises(gzip.BadGzipFile):
            gzip.decompress(body)
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_single_encodings_and_deterministic_bytes(self):
        for encoding in ("identity", "gzip"):
            body, headers = probe.script_response(encoding, "no-store")
            self.assertEqual(probe.script_response(encoding, "no-store")[0], body)
            decoded = gzip.decompress(body) if encoding == "gzip" else body
            self.assertEqual(hashlib.sha256(decoded).hexdigest(), probe.SCRIPT_SHA)
            self.assertEqual(headers["Cache-Control"], "no-store")
        with self.assertRaises(ValueError):
            probe.script_response("unknown", "no-store")

    def test_live_fixture_headers_and_reused_script_url(self):
        server = probe.Fixture()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/script.js?case=stacked-cached-cross") as response:
                self.assertEqual(response.headers["Content-Encoding"], "gzip, deflate")
                self.assertEqual(response.headers["Vary"], "Accept-Encoding")
                self.assertEqual(gzip.decompress(zlib.decompress(response.read())), probe.SCRIPT)
            with urlopen(base + "/?case=stacked-cached-cross") as response:
                main = response.read().decode()
            with urlopen(base + "/frame?case=stacked-cached-cross") as response:
                frame = response.read().decode()
            expected = base + "/script.js?case=stacked-cached-cross"
            self.assertIn(expected, main)
            self.assertIn(expected, frame)
            self.assertIn(f"http://localhost:{server.server_port}/frame", main)
            self.assertEqual(len(server.fetches), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_public_errors_do_not_leak_exception_text(self):
        result = probe.error_summary(RuntimeError("token=PRIVATE NS_ERROR_INVALID_CONTENT_ENCODING"))
        self.assertEqual(result["error_codes"], ["NS_ERROR_INVALID_CONTENT_ENCODING"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_missing_browser_fails_with_report(self):
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(binary=Path(root) / "missing", output=Path(root) / "evidence", settle_ms=0)
            self.assertEqual(asyncio.run(probe.run(args)), 1)
            report = json.loads((args.output / "report.json").read_text())
            self.assertIn("run_error", report)
            self.assertEqual(report["cases"], [])


if __name__ == "__main__":
    unittest.main()
