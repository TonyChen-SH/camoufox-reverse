#!/usr/bin/env python3
"""Exercise a compiled browser, then read its real trace through the MCP SDK."""

import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sys
import threading
import traceback

ROOT = Path(__file__).resolve().parents[1]

PILOT_APIS = {
    "Navigator.userAgent", "Navigator.appVersion", "Navigator.platform",
    "Navigator.hardwareConcurrency", "WorkerNavigator.userAgent",
    "WorkerNavigator.appVersion", "WorkerNavigator.platform",
    "WorkerNavigator.hardwareConcurrency", "Screen.width", "Screen.height",
    "WebGLRenderingContext.getParameter", "WebGL2RenderingContext.getParameter",
    "CanvasRenderingContext2D.getImageData", "HTMLCanvasElement.toDataURL",
    "AudioBuffer.getChannelData", "AudioBuffer.copyFromChannel",
}
GENERIC_ENTRIES = {
    ("Document.title", "get", "instance"),
    ("Document.title", "set", "instance"),
    ("HTMLCanvasElement.width", "get", "instance"),
    ("HTMLCanvasElement.width", "set", "instance"),
    ("Element.id", "get", "instance"),
    ("Element.id", "set", "instance"),
    ("CSSStyleProperties.color", "get", "instance"),
    ("CSSStyleProperties.color", "set", "instance"),
    ("CSSStyleProperties.backgroundColor", "get", "instance"),
    ("CSSStyleProperties.backgroundColor", "set", "instance"),
    ("DOMParser.constructor", "construct", "constructor"),
    ("DOMParser.parseFromString", "call", "instance"),
    ("TextEncoder.constructor", "construct", "constructor"),
    ("TextEncoder.encode", "call", "instance"),
    ("Blob.constructor", "construct", "constructor"),
    ("URL.constructor", "construct", "constructor"),
    ("URL.canParse", "call", "static"),
    ("URL.createObjectURL", "call", "static"),
    ("URL.revokeObjectURL", "call", "static"),
    ("Storage.setItem", "call", "instance"),
    ("Storage.getItem", "call", "instance"),
    ("Permissions.query", "call", "instance"),
    ("RTCPeerConnection.constructor", "construct", "constructor"),
    ("RTCPeerConnection.close", "call", "instance"),
}
GENERIC_OBSERVATIONS = {
    "directTitle": "trace-title-direct", "reflectSet": True,
    "reflectedTitle": "trace-title-reflected", "canvasWidth": 17,
    "elementId": "trace-element", "colors": ["rgb(12, 34, 56)", "rgb(78, 90, 123)"],
    "parsedText": "parsed-value", "encoding": "utf-8",
    "encoded": list("中🦊".encode()), "blob": {"size": 7, "type": "text/plain"},
    "blobProtocol": "blob:", "canParse": True, "constructorException": "TypeError",
    "pathname": "/changed", "constructorConversions": 1,
    "storedValue": "stored-value", "permissionPromise": True,
    "permissionCompleted": True, "peerState": "stable", "peerClosed": "closed",
}


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
            page.wait_for_function("""() => {
                const text = document.querySelector('#result').textContent;
                return text.startsWith('{') || text.startsWith('Fixture failed:');
            }""", timeout=30000)
            payload = page.locator("#result").inner_text()
            if payload.startswith("Fixture failed:"):
                raise AssertionError(payload)
            observed = json.loads(payload)
            output.write_text(json.dumps(observed, ensure_ascii=True, indent=2) + "\n")
            if observed.get("expectedException") != "IndexSizeError":
                raise AssertionError(f"Unexpected page exception result: {observed}")
            if observed.get("afterException") != 1 or observed.get("argumentConversions") != 1:
                raise AssertionError("Tracing changed exception recovery or argument coercion")
            if observed.get("webgl") == "unavailable" or observed.get("webgl2") == "unavailable":
                raise RuntimeError("Both WebGL versions are required by this runtime gate")
            if observed.get("generic") != GENERIC_OBSERVATIONS:
                raise AssertionError(f"Unexpected generic API observations: {observed.get('generic')}")
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
            pixels = await session.call_tool("get_trace_events", {
                "path": "stack.jsonl", "api": "CanvasRenderingContext2D.getImageData", "limit": 1000})
            if pixels.isError or not any(
                event["args"] and event["args"][0].get("value") == 0
                and math.copysign(1, event["args"][0]["value"]) < 0
                for event in pixels.structuredContent["events"]
            ):
                raise AssertionError("MCP structured events lost the native negative-zero argument")
            for operation, scope in (("get", "instance"), ("set", "instance"),
                                     ("call", "static"), ("construct", "constructor")):
                filtered = await session.call_tool("get_trace_events", {
                    "path": "stack.jsonl", "op": operation, "scope": scope, "limit": 100})
                if (filtered.isError or not filtered.structuredContent["matched"]
                    or any(event["op"] != operation or event.get("scope") != scope
                           for event in filtered.structuredContent["events"])):
                    raise AssertionError(f"MCP lost or mixed {scope} {operation} events")
            comparison = await session.call_tool("compare_traces", {
                "baseline": "off.jsonl", "candidate": "stack.jsonl"})
            if comparison.isError or not comparison.structuredContent["call_site_changes"]:
                raise AssertionError("MCP failed to compare the real captures")
            reordered = await session.call_tool("compare_traces", {
                "baseline": "stack.jsonl", "candidate": "stack-reordered.jsonl"})
            if reordered.isError or reordered.structuredContent["call_site_changes"] != []:
                raise AssertionError("MCP invented call-site changes after reordering real API records")


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
                details = observed.with_suffix(".error.txt")
                reason = details.read_text() if details.exists() else str(outcome)
                raise AssertionError(f"Browser case {mode} failed: {reason}")
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
                fixture_events = [event for event in capture.events
                                  if ((event["source"] or {}).get("url") or "").endswith(
                                      ("/probe.js", "/generic.js", "/worker.js"))]
                missing = PILOT_APIS - {event["api"] for event in fixture_events}
                if missing:
                    raise AssertionError(f"Native runtime missed binding entries: {sorted(missing)}")
                observed_entries = {(event["api"], event["op"], event.get("scope"))
                                    for event in fixture_events}
                missing_generic = GENERIC_ENTRIES - observed_entries
                if missing_generic:
                    raise AssertionError(f"Native runtime missed generic entries: {sorted(missing_generic)}")
                if not all(item.get("coverage") == "webidl-bindings" for item in capture.metadata):
                    raise AssertionError("Producer did not declare generic WebIDL binding coverage")
                if not any(event["stack"] and "/probe.js" in event["stack"] for event in fixture_events):
                    raise AssertionError("Native JS stacks are missing")
                if not all(event["source"]["line"] > 0 and event["source"]["column"] > 0
                           for event in fixture_events):
                    raise AssertionError("Scripted callers lack one-origin positions")
                if not any(event["api"] == "CanvasRenderingContext2D.getImageData" and event["threw"]
                           for event in fixture_events):
                    raise AssertionError("Native exception event was not captured")
                if not any(
                    event["api"] == "CanvasRenderingContext2D.getImageData" and event["args"]
                    and event["args"][0].get("value") == 0
                    and math.copysign(1, event["args"][0]["value"]) < 0
                    for event in fixture_events
                ):
                    raise AssertionError("Native getImageData arguments lost negative zero")
                mime_types = {"image/png", "image/jpeg", "image/webp"}
                encodings = [event for event in fixture_events
                             if event["api"] == "HTMLCanvasElement.toDataURL" and event["args"]
                             and event["args"][0].get("value") in mime_types]
                if ({event["args"][0]["value"] for event in encodings} != mime_types
                    or len({json.dumps(event["source"], sort_keys=True) for event in encodings}) != 1
                    or len({event["result"]["value"] for event in encodings
                            if not event["threw"] and event["result"]["type"] == "string"}) < 2):
                    raise AssertionError("The repeated toDataURL call site lacks distinct real results")
                if not any(any(argument.get("value") == "image/png\0中🦊%o %O %j %% %s" for argument in event["args"])
                           for event in fixture_events):
                    raise AssertionError("UTF-8/NUL/percent argument was not preserved")
                page = json.loads(observed.read_text())
                for name in ("userAgent", "appVersion", "platform", "hardwareConcurrency"):
                    matching = [event for event in fixture_events if event["api"] == "Navigator." + name]
                    if not any(event["result"].get("value") == page[name] for event in matching):
                        raise AssertionError(f"Event-time result differs from the page for {name}")
                constructors = [event for event in fixture_events if event["api"] == "URL.constructor"]
                if not any(event["threw"] for event in constructors):
                    raise AssertionError("Native invalid-URL constructor exception was not captured")
                if not any(not event["threw"] and event["result"].get("type") == "object"
                           for event in constructors):
                    raise AssertionError("Native URL constructor recovery/result was not captured")
                for title in ("trace-title-direct", "trace-title-reflected"):
                    if not any(event["api"] == "Document.title" and event["op"] == "set"
                               and event["args"] and event["args"][0].get("value") == title
                               and event["result"].get("type") == "undefined"
                               for event in fixture_events):
                        raise AssertionError(f"Direct/reflected title setter lost its value: {title}")
                if not any(event["api"] == "Document.title" and event["op"] == "get"
                           and event["result"].get("value") == "trace-title-reflected"
                           for event in fixture_events):
                    raise AssertionError("Reflect.get did not reach the native getter with its result")
                if not any(event["api"] == "Permissions.query"
                           and event["result"] == {"type": "object", "class": "Promise"}
                           for event in fixture_events):
                    raise AssertionError("Promise-returning native call did not preserve its result summary")
                worker_entries = {(event["api"], event["op"], event.get("scope"))
                                  for event in fixture_events
                                  if event["source"]["url"].endswith("/worker.js")}
                worker_expected = {
                    ("TextEncoder.constructor", "construct", "constructor"),
                    ("TextEncoder.encode", "call", "instance"),
                    ("URL.canParse", "call", "static"),
                }
                if worker_expected - worker_entries:
                    raise AssertionError(f"Worker missed generic bindings: {sorted(worker_expected - worker_entries)}")
                report["coverage"] = sorted(PILOT_APIS | {entry[0] for entry in GENERIC_ENTRIES})
                report["tested_entries"] = [dict(api=api, op=operation, scope=scope)
                                            for api, operation, scope in sorted(GENERIC_ENTRIES)]
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
        off_page = json.loads((destination / "off-page.json").read_text())
        stack_page = json.loads((destination / "stack-page.json").read_text())
        if off_page["generic"] != stack_page["generic"]:
            raise AssertionError("Enabling tracing changed deterministic generic API results")
        for key in ("encoding", "encoded", "pathname", "canParse"):
            if off_page["worker"][key] != stack_page["worker"][key]:
                raise AssertionError(f"Enabling tracing changed Worker {key}")
        report["off_on_observations"] = "matched"
        records = [(line, json.loads(line).get("event") == "api")
                   for line in (destination / "stack.jsonl").read_text().splitlines(keepends=True)]
        api_records = iter(line for line, is_api in reversed(records) if is_api)
        (destination / "stack-reordered.jsonl").write_text(
            "".join(next(api_records) if is_api else line for line, is_api in records))
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
        try:
            child(binary, args.mode, args.output.resolve())
        except Exception:
            args.output.with_suffix(".error.txt").write_text(traceback.format_exc())
            raise
    else:
        validate(binary, args.output.resolve())


if __name__ == "__main__":
    main()
