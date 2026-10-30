#!/usr/bin/env python3
"""Validate real native engine observations through the collector and MCP SDK."""

import argparse
import asyncio
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import traceback
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    "off_capture", "on_capture", "unchanged_results", "property_attempts",
    "hot_named_read", "hot_keyed_read", "hot_write", "hot_native_call",
    "method_reference", "coercion_and_exception", "reflect_and_has",
    "dispatch_and_constructor", "eval_provenance", "contexts_and_threads", "context_navigation",
    "full_source_and_utf16", "long_argument_and_fragmentation", "excluded_categories",
    "raw_export_roundtrip", "mcp_sdk",
)


def fixture_source(name):
    return (ROOT / "examples" / name).read_bytes().decode("utf-8")


def long_argument():
    source = fixture_source("engine-unicode.js")
    literal = source.split("const engineLongArgument = ", 1)[1].split(";\r\n", 1)[0]
    return json.loads(literal)


def expected_values():
    return {
        "methodReference": "function", "missing": True, "missingKeyed": True,
        "namedBefore": 3, "namedPre": 6, "namedPost": 6, "namedAfter": 7,
        "keyedPost": 6, "keyedAfter": 7,
        "coercion": {"keyConversions": 3, "receiverEvaluations": 3,
                     "rhsEvaluations": 1, "rhsConversions": 1},
        "keyError": "expected-key-error", "rhsError": "expected-rhs-error",
        "afterRhsThrow": 2, "objectAssignment": True,
        "coercionErrors": {"failedKeys": 1, "failedRHS": 1, "assignmentConversions": 0},
        "getterError": "expected-getter-error", "setterError": "expected-setter-error",
        "recovery": 9, "exceptionCalls": {"getterCalls": 1, "setterCalls": 1},
        "reflection": {"reflectedValue": 10, "reflectedSet": True, "receiverValue": 20,
                       "reflectGetters": 1, "reflectSetters": 1},
        "reflectedDefault": 15, "reflectedNumeric": 41, "has": False, "reflectHas": False,
        "dispatch": ["dispatch-value"] * 4, "constructedPath": "/constructed",
        "dynamicFunction": 17, "evaluated": [11, 12, 13],
        "excludedBuiltin": 3, "excludedUserFunction": 3,
        "hot": {"iterations": 1000, "readTotal": 7000, "keyedTotal": 5000,
                "lastWrite": 1000, "methodTotal": 9000},
        "unicode": {"value": 31, "length": len(long_argument().encode("utf-16-le")) // 2},
        "worker": {"value": 19, "pathname": "/worker"},
        "frames": {"same": 23, "cross": 23},
    }


def child(binary, mode, output):
    from playwright.sync_api import sync_playwright

    environment = os.environ.copy()
    if environment.get("MOZ_DISABLE_CONTENT_SANDBOX"):
        raise RuntimeError("Engine validation requires the content sandbox to remain enabled")
    environment["CAMOUFOX_TRACE"] = "0"
    environment["CAMOUFOX_ENGINE_TRACE"] = "1" if mode == "on" else "0"
    os.environ["DEBUG"] = "pw:browser"
    os.environ["DEBUG_COLORS"] = "0"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    servers, threads = [], []
    try:
        for _ in range(2):
            server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         partial(Handler, directory=str(ROOT / "examples")))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
        origin = f"http://127.0.0.1:{servers[0].server_port}"
        second_origin = f"http://127.0.0.1:{servers[1].server_port}"
        query = urlencode({"frameOrigin": second_origin})
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(executable_path=str(binary), headless=False,
                                                 env=environment, timeout=60000)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{origin}/engine.html?{query}", wait_until="load")
                page.wait_for_function("""() => {
                    const text = document.querySelector('#result').textContent;
                    return text.startsWith('{') || text.startsWith('Fixture failed:');
                }""", timeout=60000)
                payload = page.locator("#result").inner_text()
                if payload.startswith("Fixture failed:"):
                    raise AssertionError(payload)
                observed = json.loads(payload)
                observed["page_errors"] = errors
                output.write_text(json.dumps(observed, ensure_ascii=True, indent=2) + "\n")
                if errors:
                    raise AssertionError(f"Page errors: {errors}")
                if observed["values"] != expected_values():
                    raise AssertionError(f"Unexpected engine fixture values: {observed['values']}")
            finally:
                browser.close()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def function_name(event):
    return event["vv8_fields"][1].removeprefix("%")


def calls(events, name, argument=None, kind="c"):
    return [event for event in events if event["vv8_kind"] == kind
            and function_name(event) == name
            and (argument is None or any(item.get("value") == argument for item in event["args"]))]


def property_count(events, name, kind):
    return sum(event["vv8_kind"] == kind and event.get("property", {}).get("value") == name
               for event in events)


def check_context_navigation(events, contexts):
    original = urlsplit(contexts["main"])
    hrefs = contexts["navigation"]
    pushed = urlsplit(hrefs["push"])
    require(pushed.path == original.path and pushed.netloc == original.netloc
            and parse_qs(pushed.query).get("engineNavigation") == ["push"],
            "pushState did not retain the document path and append the query marker")
    require(hrefs["hash"] == hrefs["push"] + "#engine-context",
            "location.hash did not change the current document href")
    require(hrefs["restore"] == contexts["main"], "replaceState did not restore the original href")
    for kind, href in hrefs.items():
        matched = [event for event in events if event["vv8_kind"] == "s"
                   and event.get("property", {}).get("value") == "__vv8_context_" + kind]
        require(len(matched) == 1, f"Navigation {kind} assignment count: expected 1, observed {len(matched)}")
        require(matched[0].get("origin") == href,
                f"Navigation {kind} native href differs: expected {href!r}, observed {matched[0].get('origin')!r}")
    return hrefs


async def check_mcp(destination, capture, unicode_script):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=[
        "-m", "camoufox_trace_mcp", "serve", "--trace-root", str(destination)],
        env={"PYTHONPATH": os.environ["PYTHONPATH"]} if "PYTHONPATH" in os.environ else None)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()

            async def invoke(name, arguments):
                result = await session.call_tool(name, arguments)
                require(not result.isError and result.structuredContent is not None,
                        f"MCP tool failed: {name}")
                return result.structuredContent

            listing = await invoke("list_traces", {})
            require("on.jsonl" in listing["traces"], "MCP did not list the actual engine capture")
            summary = await invoke("summarize_trace", {"path": "on.jsonl", "channel": "engine"})
            require(summary["event_count"] == len(capture.events), "MCP engine event count differs")
            filtered = await invoke("get_trace_events", {
                "path": "on.jsonl", "channel": "engine", "op": "get",
                "property_name": "__vv8_hot_read", "limit": 5})
            require(filtered["matched"] == 1000 and len(filtered["events"]) == 5,
                    "MCP filtered hot-loop count differs")
            require(all(event["result"] == {"type": "unavailable", "reason": "not_observed"}
                        and event["threw"] is None for event in filtered["events"]),
                    "MCP invented a legacy return or exception result")
            comparison = await invoke("compare_traces", {
                "baseline": "off.jsonl", "candidate": "on.jsonl", "channel": "engine"})
            require(comparison["call_site_changes"], "MCP did not observe OFF/ON activity changes")
            scripts = await invoke("get_trace_scripts", {"path": "on.jsonl", "limit": 100})
            require(scripts["matched"] == len(capture.engine.scripts), "MCP script registry count differs")
            source, offset = "", 0
            while True:
                page = await invoke("get_trace_script_source", {
                    "path": "on.jsonl", "index": unicode_script["index"], "offset": offset, "limit": 2048})
                require(page["source_page_unit"] == "unicode_code_points", "MCP source page unit differs")
                source += page["source"]
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            require(source == fixture_source("engine-unicode.js"), "MCP lost full CRLF/Unicode source")
            raw = await invoke("get_trace_raw_records", {
                "path": "on.jsonl", "pid": unicode_script["pid"], "tid": unicode_script["tid"], "limit": 3})
            require(raw["records"], "MCP did not return original reassembled VV8 records")
    return {"tools": 7, "hot_read_count": filtered["matched"], "source_characters": len(source)}


def validate(binary, destination):
    destination.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "binary": str(binary),
              "reference": "VisibleV8 068c617f desktop core categories; this gate executes Camoufox",
              "checks": {name: {"status": "not_run"} for name in CHECKS}}
    report_path = destination / "engine-runtime-report.json"
    active_check = None

    def check(name, action):
        nonlocal active_check
        active_check = name
        details = action()
        report["checks"][name] = {"status": "passed", "details": details}

    def count_check(events, name, kind, expected):
        observed = property_count(events, name, kind)
        require(observed == expected, f"{name} {kind}: expected {expected}, observed {observed}")
        return observed

    try:
        active_check = "off_capture"
        from camoufox_trace_mcp import read_capture, summarize
        from camoufox_trace_mcp.collector import collect
        from camoufox_trace_mcp.vv8 import export_vv8, import_vv8

        captures, pages = {}, {}
        for mode in ("off", "on"):
            active_check = f"{mode}_capture"
            output = destination / f"{mode}-page.json"
            outcome = collect([sys.executable, str(Path(__file__).resolve()), "--child",
                               "--binary", str(binary), "--mode", mode, "--output", str(output)],
                              destination / f"{mode}.jsonl", timeout=300,
                              engine=mode == "on", binding=False)
            if outcome["termination"] != "normal_exit":
                error = output.with_suffix(".error.txt")
                raise AssertionError(error.read_text() if error.exists() else str(outcome))
            capture = read_capture(destination / f"{mode}.jsonl")
            summary = summarize(capture, channel="engine")
            if mode == "off":
                require(not capture.events and not capture.engine.streams, "Disabled engine monitor emitted records")
            else:
                require(summary["integrity"] == "no_known_loss" and capture.events,
                        f"Incomplete/empty native engine capture: {summary['statuses']}")
                require(all(event.get("channel") == "engine" for event in capture.events),
                        "Binding events leaked into the engine-only gate")
            captures[mode] = capture
            pages[mode] = json.loads(output.read_text())
            report["checks"][active_check] = {"status": "passed", "events": len(capture.events),
                                                "statuses": summary["statuses"]}
        capture, observed = captures["on"], pages["on"]
        events, scripts = capture.events, capture.engine.scripts

        def unchanged():
            require(pages["off"]["values"] == observed["values"] == expected_values(),
                    "Engine monitoring changed observable fixture results")
            require(pages["off"]["sources"] == observed["sources"], "Monitoring changed generated source text")
            return {"values": "identical", "generated_sources": "identical"}
        check("unchanged_results", unchanged)
        check("property_attempts", lambda: {
            "missing_named": count_check(events, "__vv8_missing_named", "g", 1),
            "missing_keyed": count_check(events, "__vv8_missing_keyed", "g", 1),
            "named_get": count_check(events, "__vv8_named", "g", 5),
            "named_set": count_check(events, "__vv8_named", "s", 4)})
        check("hot_named_read", lambda: count_check(events, "__vv8_hot_read", "g", 1000))
        check("hot_keyed_read", lambda: count_check(events, "__vv8_hot_keyed", "g", 1000))
        check("hot_write", lambda: {"get": count_check(events, "__vv8_hot_write", "g", 1000),
                                     "set": count_check(events, "__vv8_hot_write", "s", 1001)})

        def native_hot():
            matched = calls(events, "getItem", "engine-hot-key")
            require(len(matched) == 1000, f"Native hot call count: expected 1000, observed {len(matched)}")
            return len(matched)
        check("hot_native_call", native_hot)

        def reference():
            count = count_check(events, "clear", "g", 1)
            require(not calls(events, "clear"), "Reading a method reference invoked the method")
            return {"reads": count, "calls": 0}
        check("method_reference", reference)
        check("coercion_and_exception", lambda: {
            "getter_attempts": count_check(events, "__vv8_throwing", "g", 1),
            "setter_attempts": count_check(events, "__vv8_throwing", "s", 1),
            "rhs_throw_get": count_check(events, "__vv8_rhs_throw", "g", 2),
            "rhs_throw_set": count_check(events, "__vv8_rhs_throw", "s", 1),
            "object_assignment": count_check(events, "__vv8_object_assigned", "s", 1),
            "coercion": observed["values"]["coercion"],
            "coercion_errors": observed["values"]["coercionErrors"],
            "exception_calls": observed["values"]["exceptionCalls"]})
        def reflection():
            numeric = [event for event in events if event["vv8_kind"] == "s"
                       and event.get("property", {}).get("value") == "0"
                       and any(value.get("value") == 41 for value in event["args"])]
            require(len(numeric) == 1 and numeric[0]["property"]["type"] == "string",
                    "Reflect.set lost the canonical numeric property string key")
            return {
                "explicit_get": count_check(events, "__vv8_reflected", "g", 1),
                "explicit_set": count_check(events, "__vv8_reflected", "s", 1),
                "default_get": count_check(events, "__vv8_reflect_default", "g", 1),
                "numeric_string_set": len(numeric),
                "in": count_check(events, "__vv8_has_in", "g", 1),
                "reflect_has": count_check(events, "__vv8_has_reflect", "g", 1)}
        check("reflect_and_has", reflection)

        def dispatch():
            matched = calls(events, "getItem", "engine-dispatch-key")
            require(len(matched) == 4, f"bind/call/apply/direct expected 4 native calls, observed {len(matched)}")
            constructors = calls(events, "URL", "/constructed", kind="n")
            require(len(constructors) == 1, "The host URL constructor was not recorded exactly once")
            return {"dispatch_calls": len(matched), "constructors": len(constructors)}
        check("dispatch_and_constructor", dispatch)

        def eval_provenance():
            sources = observed["sources"]
            found = {name: [script for script in scripts if script["source"] == sources[name]]
                     for name in ("direct", "indirect", "outer", "nested", "functionSource")}
            require(all(found.values()), f"Missing registered dynamic sources: {[key for key, value in found.items() if not value]}")
            main_ids = {(script["pid"], script["tid"], script["isolate"], script["script_id"])
                        for script in scripts if script["source"] == fixture_source("engine.js")}
            outer_ids = {(script["pid"], script["tid"], script["isolate"], script["script_id"])
                         for script in found["outer"]}
            for name, parents in (("direct", main_ids), ("outer", main_ids), ("nested", outer_ids)):
                require(any((script["pid"], script["tid"], script["isolate"], script["parent_id"]) in parents
                            for script in found[name]), f"Missing native eval parent for {name}")
            for name in ("direct", "indirect", "outer", "nested"):
                require(len(calls(events, "eval", sources[name])) == 1,
                        f"The {name} eval call was not recorded exactly once")
            require(len(calls(events, "Function", sources["functionBody"])) == 1,
                    "Dynamic Function did not use the reference call-kind convention")
            return {name: [{key: script[key] for key in ("pid", "tid", "isolate", "script_id", "parent_id", "parent_unknown")}
                           for script in value] for name, value in found.items()}
        check("eval_provenance", eval_provenance)

        def contexts():
            worker = [script for script in scripts if script["source"] == fixture_source("engine-worker.js")]
            main = [script for script in scripts if script["source"] == fixture_source("engine.js")]
            frames = [script for script in scripts if script["source"] == fixture_source("engine-frame.js")]
            require(main and worker and len(frames) >= 2, "Missing main/worker/distinct frame source instances")
            main_threads = {(script["pid"], script["tid"]) for script in main}
            require(any((script["pid"], script["tid"]) not in main_threads for script in worker),
                    "Worker was not attributed to a distinct native thread")
            for kind in ("main", "worker", "same", "cross"):
                require(any(event.get("origin") == observed["contexts"][kind] for event in events),
                        f"Native document/worker href is missing for {kind}")
            frame_ids = {(script["pid"], script["tid"], script["isolate"], script["script_id"]) for script in frames}
            require(len(frame_ids) >= 2, "Identical frame sources were merged into one native instance")
            return {"worker_instances": len(worker), "frame_instances": len(frame_ids),
                    "producer_streams": len(capture.engine.streams), "hrefs": observed["contexts"]}
        check("contexts_and_threads", contexts)
        check("context_navigation", lambda: check_context_navigation(events, observed["contexts"]))

        unicode_source = fixture_source("engine-unicode.js")
        unicode_scripts = [script for script in scripts if script["source"] == unicode_source]

        def source_positions():
            require(unicode_scripts, "Full CRLF/astral source was not preserved")
            require("\r\n" in unicode_source and "🦊" in unicode_source, "The source fixture lost CRLF or astral text")
            expected_units = len(unicode_source.encode("utf-16-le")) // 2
            require(all(script["utf16_length"] == expected_units for script in unicode_scripts),
                    "Full source UTF16 length differs")
            line = unicode_source.splitlines(keepends=True)[2]
            expected_column = len(line[:line.index("window.")].encode("utf-16-le")) // 2 + 1
            expected_offset = sum(len(item.encode("utf-16-le")) // 2
                                  for item in unicode_source.splitlines(keepends=True)[:2]) + expected_column - 1
            matching = [event for event in events if event.get("property", {}).get("value") == "__vv8_unicode_probe"
                        and event["vv8_kind"] == "g" and event["source"]["line"] == 3]
            require(any(event["source_offset"] == expected_offset and event["source"]["column"] == expected_column
                        and event["source_offset_unit"] == "utf16" for event in matching),
                    f"UTF16/CRLF location differs: expected offset {expected_offset}, line 3 column {expected_column}")
            return {"utf16_length": expected_units, "offset": expected_offset, "line": 3, "column": expected_column}
        check("full_source_and_utf16", source_positions)

        def fragmented():
            require(len(calls(events, "setItem", long_argument())) == 1, "The long Unicode/NUL argument was clipped or altered")
            envelopes = [json.loads(line) for line in (destination / "on.jsonl").read_text().splitlines()]
            fragments = [item for item in envelopes if item.get("event") == "vv8" and item["part"] > 0]
            require(fragments, "The large real source/argument did not exercise transport fragmentation")
            return {"continuation_fragments": len(fragments),
                    "argument_utf16_length": len(long_argument().encode("utf-16-le")) // 2}
        check("long_argument_and_fragmentation", fragmented)

        def excluded():
            require(property_count(events, "__vv8_plain_missing", "g") == 0,
                    "A plain JavaScript object was incorrectly treated as a host object")
            require(not calls(events, "abs"), "An ordinary Math native builtin was mislabeled as a host API")
            return {"plain_object_property_events": 0, "Math_abs_host_calls": 0}
        check("excluded_categories", excluded)

        def export_roundtrip():
            exported = destination / "exported"
            manifest = export_vv8(capture, exported)
            for index, file in enumerate(manifest["files"]):
                stream = capture.engine.streams[(file["pid"], file["tid"])]
                require((exported / file["name"]).read_bytes() ==
                        "".join(record["raw"] + "\n" for record in stream.records).encode("ascii"),
                        "Standard VV8 export changed a raw record")
                imported = destination / f"roundtrip-{index}.jsonl"
                import_vv8(exported / file["name"], imported, pid=file["pid"], tid=file["tid"])
                restored = read_capture(imported)
                require(summarize(restored)["integrity"] == "no_known_loss", "Export/import introduced known loss")
                require([record["raw"] for record in restored.engine.streams[(file["pid"], file["tid"])].records]
                        == [record["raw"] for record in stream.records], "VV8 import did not restore exact raw records")
            return {"streams": len(manifest["files"]), "records": sum(file["records"] for file in manifest["files"])}
        check("raw_export_roundtrip", export_roundtrip)
        check("mcp_sdk", lambda: asyncio.run(check_mcp(destination, capture, unicode_scripts[0])))
        require(all(item["status"] == "passed" for item in report["checks"].values()),
                "An engine conformance gate did not finish")
        report["status"] = "passed"
        report["event_count"] = len(events)
        report["script_count"] = len(scripts)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        if active_check is not None:
            report["checks"][active_check] = {"status": "failed", "error": str(error)}
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "report": str(report_path),
                      "checks": len(CHECKS), "events": report["event_count"],
                      "scripts": report["script_count"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--mode", choices=("off", "on"))
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
