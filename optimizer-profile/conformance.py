#!/usr/bin/env python3
"""Require unchanged goldens, complete counts, real tiers and source offsets."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import repair

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
import reference_conformance as original


def read_operations(directory, reference):
    paths = sorted(directory.glob("vv8-*.log"))
    if len(paths) != 1:
        raise ValueError("Expected one complete shell trace")
    parser = original.module(reference, "vlp")
    return [(line[0], parser.parse_raw_fields(line[1:]))
            for line in paths[0].read_text().splitlines() if line]


def check_hot(shell, reference, output, script, flags, props, tier=None):
    output.mkdir(parents=True)
    result = original.execute([shell.resolve(), *flags, script.resolve()], output, output / "process")
    stdout = (output / "process.stdout").read_text()
    stderr = (output / "process.stderr").read_text()
    records = read_operations(output, reference)
    counts = Counter(op for op, _ in records)
    sites = Counter(f"{op}@{fields[0]}" for op, fields in records if op in "gsc")
    numbers = [int(line) for line in stdout.splitlines() if re.fullmatch(r"-?\d+", line)]
    source = script.read_text()
    if props:
        required = {"s@58": 1, "s@128": 1338, "g@77": 1, "g@167": 1338,
                    "c@64": 1, "c@153": 1338}
        expected_numbers = [42, *range(1338)]
        expected_counts = 1339
        bar_count = 1338
    else:
        required = {"s@" + str(source.index("Reflect.set(") + len("Reflect.")): 1341,
                    "g@" + str(source.index("Reflect.get(") + len("Reflect.")): 1341,
                    "c@" + str(source.index("print(")): 1341}
        expected_numbers = [-2, -2, *range(1339)]
        expected_counts = 1341
        bar_count = 1339
    failures = []
    if result["exit_code"]:
        failures.append("process_exit")
    if "unknown flag" in stderr.lower() or "Error reading" in stdout:
        failures.append("invalid_execution")
    if numbers != expected_numbers:
        failures.append("actual_print_values")
    calls = [(fields[1], fields[3:]) for op, fields in records if op == "c"]
    if calls != [("%print", [str(value)]) for value in expected_numbers]:
        failures.append("exact_native_call_arguments")
    if any(counts[op] != expected_counts for op in "gsc"):
        failures.append("complete_event_counts")
    if sites != Counter(required):
        failures.append("exact_source_offsets")
    for op in "sg":
        if sum(1 for found, fields in records if found == op and fields[2] == '"bar"') != bar_count:
            failures.append(op + "_bar_events")
    bar_stores = [int(fields[3]) for op, fields in records if op == "s" and fields[2] == '"bar"']
    if bar_stores != list(range(bar_count)):
        failures.append("exact_bar_store_values")
    if tier and (f"target {tier}" not in stdout or "completed compiling" not in stdout):
        failures.append("actual_optimized_tier")
    if tier == "MAGLEV" and "[OSR - entry." not in stdout:
        failures.append("actual_osr_entry")
    if tier == "TURBOFAN_JS" and "deoptimizing" in stdout:
        failures.append("turbofan_deoptimized")
    report = dict(result, flags=flags, record_counts=dict(counts),
                  site_counts=dict(sites), required_sites=required,
                  actual_print_outputs=len(numbers), failures=failures,
                  status="failed" if failures else "passed")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def check_wasm(shell, output):
    output.mkdir(parents=True)
    script = Path(__file__).with_name("wasm-probe.js")
    result = original.execute([shell.resolve(), script.resolve()], output, output / "process")
    stdout = (output / "process.stdout").read_text()
    stderr = (output / "process.stderr").read_text()
    passed = result["exit_code"] == 0 and stdout == "wasm=42\n" and not stderr
    report = dict(result, status="passed" if passed else "failed")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--shell", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    lock = json.loads((ROOT / "reference/visiblev8.json").read_text())
    original_args = argparse.Namespace(reference=args.reference, shell=args.shell,
                                       output=args.output / "unchanged-original-gates")
    original_args.output.mkdir()
    original_result = original.shell(original_args, lock)
    script = args.reference.resolve() / "tests/src/props.js"
    common = ["--trace-opt", "--trace-osr", "--trace-deopt"]
    profiles = {
        "default": check_hot(args.shell, args.reference, args.output / "default", script, [], True),
        "maglev-osr": check_hot(args.shell, args.reference, args.output / "maglev-osr", script,
            [*common, "--invocation-count-for-maglev=1", "--invocation-count-for-maglev-osr=1",
             "--invocation-count-for-osr=1"],
            True, "MAGLEV"),
        "turbofan": check_hot(args.shell, args.reference, args.output / "turbofan",
            Path(__file__).with_name("turbofan-probe.js"),
            ["--allow-natives-syntax", "--no-concurrent-recompilation", *common],
            False, "TURBOFAN_JS"),
    }
    wasm = check_wasm(args.shell, args.output / "wasm")
    failed = (bool(original_result) or any(item["failures"] for item in profiles.values())
              or wasm["status"] != "passed")
    report = {"profile": repair.PROFILE, "unmodified_upstream_build": False,
              "binary_sha256": hashlib.sha256(args.shell.read_bytes()).hexdigest(),
              "original_gate_exit_code": original_result, "profiles": profiles, "wasm": wasm,
              "status": "failed" if failed else "passed"}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "profile": repair.PROFILE}))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
