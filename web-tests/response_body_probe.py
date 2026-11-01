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
        row = {"phase": observed_phase, "frame": "main" if request.frame == page.main_frame else "iframe"}
        result["scripts"].append(row)
        try:
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
        for phase in ("cold-main", "warm-iframe", "reset-iframe"):
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
                break
    finally:
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout=15000)
            except Exception as error:
                result["drain_error"] = error_summary(error)
        await context.close()
    with server.lock:
        result["server_fetches"] = [row for row in server.fetches if row["case"] == case]
    result["passed"] = (len(result["phases"]) == 3
        and all(row["executed"] and row.get("body_events", 0) > 0 for row in result["phases"])
        and bool(result["scripts"]) and all(row.get("matches_source") for row in result["scripts"])
        and not result["request_failures"] and "drain_error" not in result)
    return result


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"source_bytes": len(SCRIPT), "source_sha256": SCRIPT_SHA,
              "settle_ms": args.settle_ms, "cases": []}
    server = None
    thread = None
    try:
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
        environment = dict(os.environ)
        if environment.get('MOZ_DISABLE_CONTENT_SANDBOX'):
            raise RuntimeError('Content sandbox must remain enabled')
        environment.update(CAMOUFOX_TRACE='0', CAMOUFOX_ENGINE_TRACE='0')
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
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if len(report["cases"]) == 12 and all(row["passed"] for row in report["cases"]) and "run_error" not in report else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--settle-ms", type=int, default=1000)
    args = parser.parse_args()
    if not 0 <= args.settle_ms <= 10000:
        parser.error("--settle-ms must be between 0 and 10000")
    raise SystemExit(asyncio.run(run(args)))
