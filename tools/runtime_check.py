#!/usr/bin/env python3
"""Exercise a compiled browser, then read its real trace through the MCP SDK."""

import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


def child(binary, mode, output):
    from playwright.sync_api import sync_playwright

    environment = os.environ.copy()
    if environment.get("MOZ_DISABLE_CONTENT_SANDBOX"):
        raise RuntimeError("Runtime validation requires the content sandbox to remain enabled")
    environment["CAMOUFOX_TRACE"] = "0" if mode == "off" else "1"
    environment["CAMOUFOX_TRACE_STACK"] = "1" if mode == "stack" else "0"
    environment["CAMOUFOX_TRACE_MAX_EVENTS"] = {"cap": "2", "invalid": "0"}.get(mode, "10000")
    # Playwright's driver owns browser stderr. Its non-coloured debug sink
    # forwards the full native line to the outer collector without a suffix.
    os.environ["DEBUG"] = "pw:browser"
    os.environ["DEBUG_COLORS"] = "0"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "examples")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    @contextmanager
    def launch():
        if mode == "wrapper":
            from camoufox.sync_api import Camoufox
            with Camoufox(executable_path=str(binary), headless=False,
                          os="linux", env=environment) as browser:
                yield browser
        else:
            with sync_playwright() as playwright:
                browser = playwright.firefox.launch(executable_path=str(binary), headless=False,
                                                     env=environment, timeout=60000)
                try:
                    yield browser
                finally:
                    browser.close()
    try:
        with launch() as browser:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            page.wait_for_function("document.querySelector('#result').textContent.startsWith('{')",
                                   timeout=30000)
            observed = json.loads(page.locator("#result").inner_text())
            if observed.get("expectedException") != "IndexSizeError":
                raise AssertionError(f"Unexpected page exception result: {observed}")
            if observed.get("afterException") != 1 or observed.get("argumentConversions") != 1:
                raise AssertionError("Tracing changed exception recovery or argument coercion")
            if observed.get("webgl") == "unavailable" or observed.get("webgl2") == "unavailable":
                raise RuntimeError("Both WebGL versions are required by this runtime gate")
            output.write_text(json.dumps(observed, ensure_ascii=True, indent=2) + "\n")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def check_mcp(trace_root, expected):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "camoufox_trace_mcp", "serve", "--trace-root", str(trace_root)])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            listing = await session.call_tool("list_traces", {})
            if listing.isError or "stack.jsonl" not in listing.structuredContent["traces"]:
                raise AssertionError("MCP cannot list the real browser trace")
            report = await session.call_tool("summarize_trace", {"path": "stack.jsonl"})
            if report.isError or report.structuredContent["event_count"] != expected:
                raise AssertionError("MCP summary differs from the native capture")
            events = await session.call_tool("get_trace_events", {
                "path": "stack.jsonl", "api": "WorkerNavigator.userAgent", "limit": 10})
            if events.isError or not events.structuredContent["matched"]:
                raise AssertionError("MCP lost the real Worker events")
            comparison = await session.call_tool("compare_traces", {
                "baseline": "off.jsonl", "candidate": "stack.jsonl"})
            if comparison.isError or not comparison.structuredContent["call_site_changes"]:
                raise AssertionError("MCP failed to compare the real captures")


def validate(binary, destination):
    from camoufox_trace_mcp import read_capture, summarize
    from camoufox_trace_mcp.collector import collect

    destination.mkdir(parents=True, exist_ok=True)
    captures = {}
    report = {"status": "running", "binary": str(binary), "cases": {}}
    report_path = destination / "runtime-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        for mode in ("off", "stack", "cap", "invalid", "wrapper"):
            observed = destination / f"{mode}-page.json"
            trace = destination / f"{mode}.jsonl"
            outcome = collect([sys.executable, str(Path(__file__).resolve()), "--child",
                               "--binary", str(binary), "--mode", mode,
                               "--output", str(observed)], trace, timeout=180)
            if outcome["termination"] != "normal_exit":
                raise AssertionError(f"Browser case {mode} failed: {outcome}")
            capture = read_capture(trace)
            captures[mode] = capture
            summary = summarize(capture)
            report["cases"][mode] = {key: summary[key] for key in (
                "event_count", "integrity", "statuses", "metadata", "limits")}
            if mode == "off" and capture.events:
                raise AssertionError("Disabled tracer emitted API events")
            if mode == "stack":
                if summary["integrity"] != "no_known_loss" or "no_producer_metadata" in summary["statuses"]:
                    raise AssertionError(f"Incomplete real browser trace: {summary['statuses']}")
                expected = set(json.loads((ROOT / "baseline.json").read_text())["coverage"])
                fixture_events = [event for event in capture.events
                                  if ((event["source"] or {}).get("url") or "").endswith(("/probe.js", "/worker.js"))]
                missing = expected - {event["api"] for event in fixture_events}
                if missing:
                    raise AssertionError(f"Native runtime missed binding entries: {sorted(missing)}")
                if not any(event["stack"] and "/probe.js" in event["stack"] for event in fixture_events):
                    raise AssertionError("Native JS stacks are missing")
                if not all(event["source"]["line"] > 0 and event["source"]["column"] > 0
                           for event in fixture_events):
                    raise AssertionError("Scripted callers lack one-origin positions")
                if not any(event["api"] == "CanvasRenderingContext2D.getImageData" and event["threw"]
                           for event in fixture_events):
                    raise AssertionError("Native exception event was not captured")
                if not any(any(argument.get("value") == "image/png\0中🦊" for argument in event["args"])
                           for event in fixture_events):
                    raise AssertionError("UTF-8/NUL argument was not preserved")
                page = json.loads(observed.read_text())
                for name in ("userAgent", "appVersion", "platform", "hardwareConcurrency"):
                    matching = [event for event in fixture_events if event["api"] == "Navigator." + name]
                    if not any(event["result"].get("value") == page[name] for event in matching):
                        raise AssertionError(f"Event-time result differs from the page for {name}")
                report["coverage"] = sorted(expected)
                report["fixture_events"] = len(fixture_events)
            if mode == "cap":
                if not any(item.get("reason") == "event_cap" for item in capture.limits):
                    raise AssertionError("Native event limit was not reported")
                if any(event["seq"] > 2 for event in capture.events):
                    raise AssertionError("Native event budget was exceeded")
            if mode == "invalid":
                if capture.events or not any(item.get("reason") == "invalid_max_events" for item in capture.limits):
                    raise AssertionError("Invalid producer configuration did not fail explicitly")
            if mode == "wrapper":
                if summary["integrity"] != "no_known_loss" or not capture.events:
                    raise AssertionError("Official Camoufox wrapper did not produce a valid trace")
        asyncio.run(check_mcp(destination, len(captures["stack"].events)))
        report["mcp"] = "passed"
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({"status": "passed", "report": str(report_path),
                      "coverage": len(report["coverage"]), "mcp": report["mcp"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--mode", choices=("off", "stack", "cap", "invalid", "wrapper"))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    if args.child:
        child(binary, args.mode, args.output.resolve())
    else:
        validate(binary, args.output.resolve())


if __name__ == "__main__":
    main()
