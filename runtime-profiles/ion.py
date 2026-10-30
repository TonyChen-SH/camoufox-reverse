#!/usr/bin/env python3
"""Run engine trace conformance with sample-referenced native Firefox Ion evidence.

Requires Playwright and the installed camoufox_trace_mcp production package.
Run: python runtime-profiles/ion.py --binary /path/to/camoufox --output /new/directory
Raw traces, page observations, native shutdown profiles and ion-report.json are retained.
No JIT preferences are changed. A sample proves execution of its referenced frames;
it does not identify the tier of every individual trace event.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import traceback

HERE = Path(__file__).resolve().parent
FUNCTIONS = ("ionRead", "ionWrite", "ionCall")
PARAMETERS = {"warmupBatches": 2, "warmupIterations": 5000,
              "measuredIterations": 10000, "spins": 512, "pauseMs": 250}
MAX_EVENTS = 300_000
CHECKS = ("off_capture", "on_capture", "unchanged_results", "warmup_counts",
          "measured_counts", "off_profile", "on_profile", "sampled_ion")
PROFILER_ENV = {
    "MOZ_PROFILER_STARTUP": "1", "MOZ_PROFILER_STARTUP_FEATURES": "js,stackwalk",
    "MOZ_PROFILER_STARTUP_FILTERS": "GeckoMain", "MOZ_PROFILER_STARTUP_INTERVAL": "0.5",
    "MOZ_PROFILER_STARTUP_ENTRIES": "16MiB",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expected_values():
    def result(iterations):
        # The JS recurrence uses integer-exact doubles before signed int32 coercion.
        modulus = 1 << 32
        increment = 0
        factor, delta = 1664525, 1013904223
        steps = iterations * PARAMETERS["spins"]
        while steps:
            if steps & 1:
                increment = (increment * factor + delta) % modulus
            delta = delta * (factor + 1) % modulus
            factor = factor * factor % modulus
            steps >>= 1
        checksum = increment if increment < (1 << 31) else increment - modulus
        return {"read": [7 * iterations, checksum], "write": [iterations - 1, checksum],
                "call": [9 * iterations, checksum]}

    return {"warmup": [result(PARAMETERS["warmupIterations"])
                       for _ in range(PARAMETERS["warmupBatches"])],
            "measured": result(PARAMETERS["measuredIterations"]),
            "writtenReadback": PARAMETERS["measuredIterations"] - 1,
            "storedReadback": "ion-value"}


def sampled_ion(profile, script_url):
    """Inspect native raw profile schemas, following only stacks used by samples."""
    patterns = {name: re.compile(re.escape(name) + r" \(" + re.escape(script_url)
                                + r":\d+:\d+\)(?:\[\d+\])?") for name in FUNCTIONS}
    evidence = {name: {"samples": 0, "examples": []} for name in FUNCTIONS}

    def cell(table, row, field, *, optional=False):
        require(isinstance(table, dict) and isinstance(table.get("schema"), dict),
                "Invalid native profiler table/schema")
        index = table["schema"].get(field)
        require(type(index) is int and index >= 0 and isinstance(row, list),
                f"Invalid native profiler {field} field")
        if optional and index >= len(row):
            return None
        require(index < len(row), f"Missing native profiler {field} value")
        return row[index]

    def indexed(rows, index, description):
        require(type(index) is int and 0 <= index < len(rows),
                f"Invalid native profiler {description} index: {index!r}")
        return rows[index]

    def process(node):
        require(isinstance(node, dict), "Invalid native profiler process")
        require(isinstance(node.get("threads", []), list), "Invalid profiler threads")
        for thread in node.get("threads", []):
            require(isinstance(thread, dict), "Invalid native profiler thread")
            samples = thread.get("samples", {})
            require(isinstance(samples, dict) and isinstance(samples.get("data", []), list),
                    "Invalid native profiler samples")
            for sample in samples.get("data", []):
                stack = cell(samples, sample, "stack")
                if stack is None:
                    continue
                stacks, frames = thread.get("stackTable", {}), thread.get("frameTable", {})
                strings = thread.get("stringTable", [])
                require(isinstance(stacks.get("data"), list)
                        and isinstance(frames.get("data"), list) and isinstance(strings, list),
                        "Missing native profiler stack/frame/string table")
                seen, matched = set(), {}
                while stack is not None:
                    require(stack not in seen, "Cyclic native profiler stack")
                    seen.add(stack)
                    row = indexed(stacks["data"], stack, "stack")
                    frame_index = cell(stacks, row, "frame")
                    frame = indexed(frames["data"], frame_index, "frame")
                    implementation = cell(frames, frame, "implementation", optional=True)
                    if implementation is not None:
                        implementation = indexed(strings, implementation, "implementation string")
                    if implementation == "ion":
                        location = indexed(strings, cell(frames, frame, "location"), "location string")
                        require(isinstance(location, str), "Non-string profiler frame location")
                        for name, pattern in patterns.items():
                            if pattern.fullmatch(location):
                                matched[name] = {"location": location, "frame": frame_index}
                    stack = cell(stacks, row, "prefix")
                for name, frame in matched.items():
                    evidence[name]["samples"] += 1
                    if len(evidence[name]["examples"]) < 3:
                        evidence[name]["examples"].append({"pid": thread.get("pid", node.get("pid")),
                            "tid": thread.get("tid"), "time": cell(samples, sample, "time"), **frame})
        require(isinstance(node.get("processes", []), list), "Invalid profiler child processes")
        for child in node.get("processes", []):
            process(child)

    process(profile)
    missing = [name for name, details in evidence.items() if details["samples"] == 0]
    require(not missing, f"No actual sampled Ion execution for {missing}; evidence={evidence}")
    return evidence


def trace_counts(events, script_url, phase, expected):
    owned = [event for event in events if (event.get("source") or {}).get("url") == script_url]

    def marker(value):
        matches = [index for index, event in enumerate(owned)
                   if event.get("vv8_kind") == "s"
                   and event.get("property", {}).get("value") == "__vv8_ion_phase"
                   and event.get("args", [{}])[0].get("value") == value]
        require(len(matches) == 1, f"Expected one owned phase marker {value}, got {len(matches)}")
        return matches[0]

    start, end = marker(phase + "-start"), marker(phase + "-end")
    require(start < end, f"Reversed {phase} markers")
    identity = (owned[start]["pid"], owned[start]["tid"])
    require((owned[end]["pid"], owned[end]["tid"]) == identity, "Phase markers changed native thread")
    interval = owned[start + 1:end]
    require(all((event["pid"], event["tid"]) == identity for event in interval),
            "Owned synchronous profile work changed native thread")
    counts = {"read": 0, "write": 0, "method_reference": 0, "native_call": 0}
    for event in interval:
        kind, prop = event.get("vv8_kind"), event.get("property", {}).get("value")
        counts["read"] += kind == "g" and prop == "__vv8_ion_read"
        counts["write"] += kind == "s" and prop == "__vv8_ion_write"
        counts["method_reference"] += kind == "g" and prop == "getItem"
        fields = event.get("vv8_fields", [])
        counts["native_call"] += (kind == "c" and len(fields) > 1 and fields[1] == "%getItem"
                                  and any(arg.get("value") == "engine-ion-native"
                                          for arg in event.get("args", [])))
    require(all(count == expected for count in counts.values()),
            f"{phase}: expected exactly {expected} per operation, got {counts}")
    return {"expected_per_operation": expected, "observed": counts, "pid": identity[0], "tid": identity[1]}


def run_child(binary, mode, output):
    environment = os.environ.copy()
    require(not environment.get("MOZ_DISABLE_CONTENT_SANDBOX"),
            "Ion conformance requires the enabled content sandbox")
    environment.pop("MOZ_PROFILER_STARTUP_FEATURES_BITFIELD", None)
    environment.pop("MOZ_PROFILER_STARTUP_ACTIVE_TAB_ID", None)
    environment.update(PROFILER_ENV)
    environment.update({"CAMOUFOX_TRACE": "0", "CAMOUFOX_ENGINE_TRACE": "1" if mode == "on" else "0",
                        "CAMOUFOX_ENGINE_MAX_RECORDS": "0",
                        "MOZ_PROFILER_SHUTDOWN": str(output / "profile.json")})
    os.environ.update({"DEBUG": "pw:browser", "DEBUG_COLORS": "0"})
    from playwright.sync_api import sync_playwright

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(HERE)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.firefox.launch(executable_path=str(binary), headless=True,
                                                 env=environment, timeout=60000)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                origin = f"http://127.0.0.1:{server.server_port}"
                page.goto(origin + "/ion.html", wait_until="load", timeout=120000)
                page.wait_for_function("""() => {
                    const text = document.getElementById('result').textContent;
                    return text.startsWith('{') || text.startsWith('Fixture failed:');
                }""", timeout=120000)
                text = page.locator("#result").inner_text()
                require(not text.startswith("Fixture failed:"), text)
                observed = json.loads(text)
                observed["page_errors"] = errors
                (output / "page.json").write_text(json.dumps(observed, indent=2) + "\n", encoding="utf-8")
                require(not errors, f"Unexpected page errors: {errors}")
                require(observed["scriptURL"] == origin + "/ion.js", "Unexpected owned script URL")
            finally:
                # Native parent shutdown gathers content-process exit profiles here.
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def validate(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "running", "binary": str(binary), "parameters": PARAMETERS,
              "checks": {name: {"status": "not_run"} for name in CHECKS},
              "profiler_environment": PROFILER_ENV,
              "budgets": {"reader_max_events": MAX_EVENTS, "reader_max_records": None,
                          "owned_loop_api_events": 80000,
                          "owned_fixed_api_events_upper_bound": 100,
                          "collector_max_output_bytes": None},
              "evidence_scope": "Sample-referenced Ion execution of each named owned function; "
                                "individual trace events do not carry an execution tier.",
              "firefox_source": "9378fbc58660847fbc02c3486a0ecd6049978e25"}
    active = "off_capture"

    def check(name, action):
        nonlocal active
        active = name
        report["checks"][name] = {"status": "passed", "details": action()}

    try:
        require(binary.is_file() and os.access(binary, os.X_OK), f"Missing executable browser: {binary}")
        from camoufox_trace_mcp import read_capture, summarize
        from camoufox_trace_mcp.collector import DEFAULT_OUTPUT_BYTES, collect
        from camoufox_trace_mcp.reader import MAX_RECORDS
        report["budgets"]["collector_max_output_bytes"] = DEFAULT_OUTPUT_BYTES
        report["budgets"]["reader_max_records"] = MAX_RECORDS
        captures, pages, profiles = {}, {}, {}
        for mode in ("off", "on"):
            active = mode + "_capture"
            case = output / mode
            case.mkdir()
            outcome = collect([sys.executable, str(Path(__file__).resolve()), "--child",
                               "--binary", str(binary), "--mode", mode, "--output", str(case)],
                              case / "trace.jsonl", engine=mode == "on", binding=False,
                              timeout=300, max_output_bytes=DEFAULT_OUTPUT_BYTES)
            error_file = case / "error.txt"
            require(outcome["termination"] == "normal_exit",
                    f"{mode} collection failed: {outcome}; "
                    + (error_file.read_text(encoding="utf-8") if error_file.exists() else ""))
            capture = read_capture(case / "trace.jsonl", max_events=MAX_EVENTS)
            summary = summarize(capture, channel="engine")
            require(summary["integrity"] == "no_known_loss", f"{mode} known loss: {summary['statuses']}")
            require(all(event.get("channel") == "engine" for event in capture.events),
                    "Binding trace leaked into the engine-only profile")
            if mode == "off":
                require(not capture.events and not capture.engine.streams, "OFF engine emitted records")
            else:
                require(capture.events, "ON engine emitted no API records")
            captures[mode] = capture
            pages[mode] = json.loads((case / "page.json").read_text(encoding="utf-8"))
            report["checks"][active] = {"status": "passed", "events": len(capture.events),
                                        "statuses": summary["statuses"], "collection": outcome}

        def unchanged():
            for mode, page in pages.items():
                require(page["parameters"] == PARAMETERS, f"{mode} fixture parameter mismatch")
                require(page["values"] == expected_values(), f"{mode} unexpected page values: {page['values']}")
            require(pages["off"]["values"] == pages["on"]["values"], "Trace changed page observations")
            return pages["on"]["values"]

        check("unchanged_results", unchanged)
        for phase, name, expected in (("warmup", "warmup_counts", PARAMETERS["warmupBatches"] * PARAMETERS["warmupIterations"]),
                                      ("measure", "measured_counts", PARAMETERS["measuredIterations"])):
            check(name, lambda: trace_counts(captures["on"].events, pages["on"]["scriptURL"], phase, expected))
        for mode in ("off", "on"):
            def load_profile():
                path = output / mode / "profile.json"
                require(path.is_file() and path.stat().st_size > 0,
                        f"Missing normal-shutdown Firefox profile: {path}")
                profiles[mode] = json.loads(path.read_text(encoding="utf-8"))
                require(isinstance(profiles[mode], dict) and "threads" in profiles[mode],
                        f"Not a native Firefox profile: {path}")
                return {"path": str(path.relative_to(output)), "bytes": path.stat().st_size}
            check(mode + "_profile", load_profile)
        check("sampled_ion", lambda: sampled_ion(profiles["on"], pages["on"]["scriptURL"]))
        report["status"] = "passed"
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["checks"][active] = {"status": "failed", "error": str(error)}
        report["error"] = traceback.format_exc()
        return 1
    finally:
        (output / "ion-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("off", "on"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    binary, output = args.binary.resolve(), args.output.resolve()
    if args.child:
        try:
            require(args.mode in ("off", "on"), "Child mode required")
            run_child(binary, args.mode, output)
            return 0
        except Exception:
            (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            return 1
    result = validate(binary, output)
    print(output / "ion-report.json")
    return result


if __name__ == "__main__":
    sys.exit(main())
