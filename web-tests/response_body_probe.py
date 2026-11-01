"""Compare known script bytes with Juggler bodies across iframe cache reuse.

Run with the compiled binary under Xvfb; no network interception or browser
preference overrides are used. The fixture uses loopback HTTP exclusively.
"""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import re
import zlib
from urllib.parse import parse_qs, urlsplit


SCRIPT = ("\n".join(f"function fixture_{i}(x) {{ return x + {i}; }}" for i in range(6000))
          + '\ndocument.querySelector("#script-state").textContent = "ready";\n').encode()
SCRIPT_SHA = hashlib.sha256(SCRIPT).hexdigest()
ENCODINGS = ("identity", "gzip", "stacked")
CACHES = ("no-store", "public, max-age=3600")
HEADER_NAMES = ("content-encoding", "content-length", "cache-control", "vary")
PHASES = ("cold-main",) + tuple(f"iframe-{index}" for index in range(1, 6))
LOG_MESSAGES = {
    "fetch_count_below_threshold": b"Bytecode-cache: Skip disk: fetchCount",
    "scheduled_disk_encoding": b"Schedule the disk cache encoding.",
    "started_disk_encoding": b"Start the disk cache encoding.",
    "disk_write_success": b"Write the disk cache (rv = 0,",
    "serialized_stencil_chunks": b"SRIAndSerializedStencil length =",
}


def logging_environment(output, private, recipient):
    if bool(private) != bool(recipient):
        raise ValueError("Private output and recipient must be provided together")
    environment = dict(os.environ)
    if environment.get("MOZ_DISABLE_CONTENT_SANDBOX"):
        raise RuntimeError("Content sandbox must remain enabled")
    environment.update(CAMOUFOX_TRACE="0", CAMOUFOX_ENGINE_TRACE="0")
    for key in ("MOZ_LOG", "MOZ_LOG_FILE", "NSPR_LOG_MODULES", "NSPR_LOG_FILE"):
        environment.pop(key, None)
    if private:
        public_path, private_path = output.resolve(), private.resolve()
        if private_path.is_relative_to(public_path) or public_path.is_relative_to(private_path):
            raise ValueError("Private output must be disjoint from public output")
        if not recipient.is_file():
            raise FileNotFoundError("Evidence recipient is required")
        environment.update(MOZ_LOG="timestamp,ScriptLoader:5",
                           MOZ_LOG_FILE=str(private_path / "scriptloader.log"))
    return environment


def summarize_logs(private):
    summary = {"files": [], "message_counts": {name: 0 for name in LOG_MESSAGES},
               "count_unit": "log-message occurrences, not unique scripts or requests",
               "scope": "All browser ScriptLoader messages; fixture attribution requires the encrypted logs"}
    for path in sorted(private.glob("scriptloader.log*")):
        if not path.is_file() or path.is_symlink():
            continue
        hasher = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for line in stream:
                hasher.update(line)
                size += len(line)
                for name, message in LOG_MESSAGES.items():
                    summary["message_counts"][name] += line.count(message)
        summary["files"].append({"bytes": size, "sha256": hasher.hexdigest()})
    summary["serialized_stencil_observed"] = summary["message_counts"]["serialized_stencil_chunks"] > 0
    return summary


def script_response(encoding, cache):
    if encoding not in ENCODINGS or cache not in CACHES:
        raise ValueError("Unknown fixture option")
    body = gzip.compress(SCRIPT, mtime=0) if encoding in ("gzip", "stacked") else SCRIPT
    if encoding == "stacked":
        body = zlib.compress(body)
    return body, {"Content-Type": "text/javascript; charset=utf-8",
                  "Content-Encoding": "gzip, deflate" if encoding == "stacked" else encoding,
                  "Content-Length": str(len(body)),
                  "Cache-Control": cache, "Vary": "Accept-Encoding"}


def error_summary(error):
    # Never publish browser logs or arbitrary exception text, even for this local fixture.
    codes = sorted(set(re.findall(r"\bNS_ERROR_[A-Z0-9_]+\b", str(error))))
    return {"error_type": type(error).__name__, "error_codes": codes}


class Fixture(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.fetches = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        case = query.get("case", [""])[0]
        try:
            encoding, cache_key, origin = case.split("-")
            if cache_key not in ("cached", "uncached") or origin not in ("same", "cross"):
                raise ValueError("Unknown case")
            cache = CACHES[cache_key == "cached"]
            body, headers = script_response(encoding, cache)
        except ValueError:
            self.send_error(400)
            return
        if parsed.path == "/script.js":
            with self.server.lock:
                self.server.fetches.append({"case": case, "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest()})
        elif parsed.path in ("/", "/frame"):
            port = self.server.server_port
            script_url = f"http://127.0.0.1:{port}/script.js?case={case}"
            html = ('<!doctype html><meta charset="utf-8">'
                    '<output id="script-state">loading</output>'
                    f'<script src="{script_url}"></script>')
            if parsed.path == "/":
                frame_host = "localhost" if origin == "cross" else "127.0.0.1"
                frame_url = f"http://{frame_host}:{port}/frame?case={case}"
                html += ('<button id="reset">Create or replace frame</button>'
                         '<script>document.querySelector("#reset").onclick = () => {'
                         'document.querySelector("iframe")?.remove();'
                         'const frame = document.createElement("iframe");'
                         f'frame.src = {json.dumps(frame_url)};'
                         'document.body.append(frame);};</script>')
            body = html.encode()
            headers = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                       "Content-Length": str(len(body))}
        else:
            self.send_error(404)
            return
        self.send_response(200)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


async def probe_case(browser, server, case, settle_ms):
    context = await browser.new_context(no_viewport=True)
    page = await context.new_page()
    page.set_default_timeout(15000)
    result = {"case": case, "phases": [], "scripts": [], "request_failures": []}
    pending = set()
    phase = "cold-main"

    async def capture(request, observed_phase):
        row = {"phase": observed_phase}
        result["scripts"].append(row)
        try:
            row["frame"] = "main" if request.frame == page.main_frame else "iframe"
            response = await request.response()
            row["status"] = response.status
            headers = await response.all_headers()
            row["headers"] = {name: headers[name] for name in HEADER_NAMES if name in headers}
            try:
                row["sizes"] = await request.sizes()
            except Exception as error:
                row["sizes_error"] = error_summary(error)
            body = await response.body()
            row.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
            row["matches_source"] = row["sha256"] == SCRIPT_SHA
        except Exception as error:
            row.update(error_summary(error))

    def finished(request):
        if urlsplit(request.url).path != "/script.js":
            return
        task = asyncio.create_task(capture(request, phase))
        pending.add(task)
        task.add_done_callback(pending.discard)

    page.on("requestfinished", finished)
    page.on("requestfailed", lambda request: result["request_failures"].append(
        {"phase": phase, "resource_type": request.resource_type,
         "failure_codes": re.findall(r"\bNS_ERROR_[A-Z0-9_]+\b", request.failure or "")}))
    try:
        for phase in PHASES:
            before = len(result["scripts"])
            try:
                if phase == "cold-main":
                    await page.goto(f"http://127.0.0.1:{server.server_port}/?case={case}", wait_until="load")
                    state = page.locator("#script-state")
                else:
                    await page.locator("#reset").click()
                    state = page.frame_locator("iframe").locator("#script-state")
                from playwright.async_api import expect
                await expect(state).to_have_text("ready", timeout=15000)
                handle = await state.element_handle()
                frame = await handle.owner_frame()
                await frame.wait_for_load_state("load", timeout=15000)
                await handle.dispose()
                # Allow background bytecode-cache work to run before the next load.
                # This fixed observation window is not a retry or success fallback.
                await page.wait_for_timeout(settle_ms)
                if pending:
                    await asyncio.wait_for(asyncio.gather(*pending), timeout=15000)
                result["phases"].append({"phase": phase, "executed": True,
                                         "body_events": len(result["scripts"]) - before})
            except Exception as error:
                result["phases"].append({"phase": phase, "executed": False,
                                         **error_summary(error)})
    finally:
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=15000)
            except Exception as error:
                result["drain_error"] = error_summary(error)
        await context.close()
    with server.lock:
        result["server_fetches"] = [row for row in server.fetches if row["case"] == case]
    result["passed"] = (len(result["phases"]) == len(PHASES)
        and all(row["executed"] and row.get("body_events", 0) > 0 for row in result["phases"])
        and bool(result["scripts"]) and all(row.get("matches_source") for row in result["scripts"])
        and not result["request_failures"] and "drain_error" not in result)
    return result


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"source_bytes": len(SCRIPT), "source_sha256": SCRIPT_SHA,
              "settle_ms": args.settle_ms, "phases": PHASES,
              "cache_coverage_basis": "Serialized-stencil log evidence only; elapsed time does not prove cache coverage",
              "cases": []}
    server = None
    thread = None
    private = getattr(args, "private_output", None)
    recipient = getattr(args, "recipient", None)
    private_created = False
    try:
        environment = logging_environment(args.output, private, recipient)
        if private:
            private.mkdir(parents=True, exist_ok=False, mode=0o700)
            private_created = True
        from playwright.async_api import async_playwright
        binary = args.binary.resolve(strict=True)
        for name, path in (("binary", binary), ("libxul", binary.parent / "libxul.so")):
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            report[name + "_sha256"] = hasher.hexdigest()
        server = Fixture()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        async with async_playwright() as playwright:
            browser = await playwright.firefox.launch(executable_path=str(binary), headless=False,
                                                       env=environment, timeout=60000)
            report["browser_version"] = browser.version
            try:
                for encoding in ENCODINGS:
                    for cache in ("uncached", "cached"):
                        for origin in ("same", "cross"):
                            case = f"{encoding}-{cache}-{origin}"
                            try:
                                result = await probe_case(browser, server, case, args.settle_ms)
                            except Exception as error:
                                result = {"case": case, "passed": False, **error_summary(error)}
                            report["cases"].append(result)
                            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            finally:
                await browser.close()
    except Exception as error:
        report["run_error"] = error_summary(error)
    finally:
        if server:
            if thread:
                server.shutdown()
                thread.join()
            server.server_close()
        if private_created:
            try:
                report["cache_log"] = summarize_logs(private)
                if not report["cache_log"]["files"]:
                    raise RuntimeError("Requested ScriptLoader logs were not produced")
            except Exception as error:
                report["evidence_error"] = error_summary(error)
            try:
                from run import encrypt_private
                report["private_evidence"] = encrypt_private(private, recipient, args.output / "private-evidence.cms")
            except Exception as error:
                report["evidence_error"] = error_summary(error)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if (len(report["cases"]) == 12 and all(row["passed"] for row in report["cases"])
                 and "run_error" not in report and "evidence_error" not in report) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-output", type=Path)
    parser.add_argument("--recipient", type=Path)
    parser.add_argument("--settle-ms", type=int, default=1000)
    args = parser.parse_args()
    if not 0 <= args.settle_ms <= 10000:
        parser.error("--settle-ms must be between 0 and 10000")
    if bool(args.private_output) != bool(args.recipient):
        parser.error("--private-output and --recipient must be provided together")
    raise SystemExit(asyncio.run(run(args)))
