import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
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

    def test_logging_requires_pair_and_disjoint_private_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            certificate = root / "recipient.pem"
            certificate.write_text("test certificate")
            output, private = root / "public", root / "private"
            with self.assertRaises(ValueError):
                probe.logging_environment(output, private, None)
            for unsafe in (output, output / "nested", root):
                with self.assertRaises(ValueError):
                    probe.logging_environment(output, unsafe, certificate)
            with patch.dict(os.environ, {"MOZ_LOG": "secret:5", "MOZ_LOG_FILE": "/unsafe",
                                         "NSPR_LOG_MODULES": "secret:5"}, clear=True):
                disabled = probe.logging_environment(output, None, None)
                self.assertNotIn("MOZ_LOG", disabled)
                self.assertNotIn("MOZ_LOG_FILE", disabled)
                self.assertNotIn("NSPR_LOG_MODULES", disabled)
                enabled = probe.logging_environment(output, private, certificate)
                self.assertEqual(enabled["MOZ_LOG"], "timestamp,ScriptLoader:5")
                self.assertEqual(enabled["MOZ_LOG_FILE"], str(private / "scriptloader.log"))
                self.assertEqual(enabled["CAMOUFOX_TRACE"], "0")
                self.assertEqual(enabled["CAMOUFOX_ENGINE_TRACE"], "0")
            with patch.dict(os.environ, {"MOZ_DISABLE_CONTENT_SANDBOX": "1"}):
                with self.assertRaises(RuntimeError):
                    probe.logging_environment(output, None, None)

    def test_log_summary_uses_evidence_without_exposing_log_content(self):
        with tempfile.TemporaryDirectory() as root:
            private = Path(root)
            data = (b"PRIVATE token/path Bytecode-cache: Skip disk: fetchCount\n"
                    b"PRIVATE SRIAndSerializedStencil length = 1234\n")
            (private / "scriptloader.log.child-1").write_bytes(data)
            summary = probe.summarize_logs(private)
            self.assertEqual(summary["files"], [{"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}])
            self.assertEqual(summary["message_counts"]["fetch_count_below_threshold"], 1)
            self.assertTrue(summary["serialized_stencil_observed"])
            self.assertNotIn("PRIVATE", json.dumps(summary))
            self.assertNotIn("child-1", json.dumps(summary))

    def test_six_fixed_phases_preserve_failure_and_continue(self):
        page = MagicMock()
        page.goto = AsyncMock(side_effect=RuntimeError("first phase failure"))
        page.wait_for_timeout = AsyncMock()
        state = MagicMock()
        state.click = AsyncMock()
        frame = SimpleNamespace(wait_for_load_state=AsyncMock())
        handle = SimpleNamespace(owner_frame=AsyncMock(return_value=frame), dispose=AsyncMock())
        state.element_handle = AsyncMock(return_value=handle)
        page.locator.return_value = state
        page.frame_locator.return_value.locator.return_value = state
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        server = SimpleNamespace(server_port=1234, lock=threading.Lock(), fetches=[])
        with patch("playwright.async_api.expect", return_value=SimpleNamespace(to_have_text=AsyncMock())):
            result = asyncio.run(probe.probe_case(browser, server, "gzip-cached-same", 0))
        self.assertEqual([row["phase"] for row in result["phases"]], list(probe.PHASES))
        self.assertFalse(result["phases"][0]["executed"])
        self.assertEqual(state.click.await_count, 5)
        self.assertFalse(result["passed"])
        self.assertNotIn("first phase failure", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
