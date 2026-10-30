#!/usr/bin/env python3
"""Retain original browser evidence, then incrementally build a labeled repair."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import build_visiblev8_reference as builder
import reference_conformance as gate

ROOT = Path(__file__).resolve().parents[1]
PROFILE_LOCK = Path(__file__).with_name("optimizer-profile.json")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_repair(provider):
    lock = json.loads(PROFILE_LOCK.read_text())
    source = provider / "optimizer-profile/repair.py"
    if builder.head(provider) != lock["commit"] or digest(source) != lock["repair_sha256"]:
        raise ValueError("Optimizer provider revision or source hash differs from its pin")
    spec = importlib.util.spec_from_file_location("fixed_optimizer_repair", source)
    repair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repair)
    if repair.PROFILE != lock["profile"]:
        raise ValueError("Optimizer provider profile identity differs from its pin")
    return repair, lock


def classify_original(exit_code, report, failure):
    if exit_code == 0 and report.get("status") == "passed":
        return "passed_original_profile"
    expected = (f"Original optimized browser lost expected events: "
                f"get={report.get('hot_gets')}, call={report.get('hot_calls')}")
    observed = report.get("observations", {})
    kinds = report.get("record_counts", {})
    if (exit_code == 1 and report.get("status") == "failed"
            and failure == {"status": "failed", "error": expected}
            and report.get("exit_code") == 0 and report.get("hot_gets") == 6000
            and type(report.get("hot_calls")) is int and 0 <= report["hot_calls"] < 4096
            and all(kinds.get(kind, 0) > 0 for kind in "gscn$!")
            and not observed.get("error") and observed.get("hotSum") == 6000
            and observed.get("keyConversions") == 2 and observed.get("argumentAccessorReads") == 0):
        return "expected_upstream_failure"
    return "unexpected_original_failure"


def runtime(reference, binary, output):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "runtime").exists():
        raise ValueError("Runtime evidence directory must be new")
    result = gate.execute([sys.executable, str(Path(__file__).with_name("reference_conformance.py")),
        "browser", "--reference", reference, "--browser", binary, "--output", output / "runtime"],
        output, output / "strict-runtime", timeout=150)
    def read(name):
        path = output / "runtime" / name
        return json.loads(path.read_text()) if path.is_file() else {}
    return result, read("report.json"), read("failure.json")


def run_profiles(args, aggregate):
    lock = json.loads(builder.LOCK.read_text())
    builder.verify_reference(args.reference, lock)
    repair, provider_lock = load_repair(args.optimizer_provider)
    binary = args.original / "bin/headless_shell"
    original_provenance = args.original / "bin/provenance.json"
    before_hash = digest(binary)
    provenance = json.loads(original_provenance.read_text())
    original_provenance_hash = digest(original_provenance)
    if provenance["binary_sha256"] != before_hash or provenance["commit"] != lock["commit"]:
        raise ValueError("Original browser binary or provenance changed before runtime capture")
    original_result, original_report, original_failure = runtime(args.reference, binary, args.original)
    classification = classify_original(original_result["exit_code"], original_report, original_failure)
    aggregate["original"] = {"strict_gate": original_result, "report": original_report,
                             "failure": original_failure, "classification": classification,
                             "binary_sha256": before_hash}
    (args.original / "runtime-classification.json").write_text(
        json.dumps(aggregate["original"], indent=2) + "\n")
    if classification == "unexpected_original_failure":
        aggregate["failures"].append(classification)

    logs = args.repaired / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for name in ("engine-build-compat.json", "engine-build-compat.patch"):
        shutil.copy2(args.original / "logs" / name, logs / name)
    engine = args.work / "src/v8"
    manifest = repair.apply(engine, logs, shell_fixture=False)
    if manifest["shell_embedder_profile"] is not None or "samples/shell.cc" in manifest["files"]:
        raise ValueError("Browser repair must not include the shell embedder profile")
    env = dict(os.environ, CCACHE_STATSLOG=str(logs / "ccache-statistics.log"))
    builder.run([sys.executable, str(Path(__file__).with_name("build_visiblev8_reference.py")),
        "--reference", str(args.reference), "--work", str(args.work),
        "--output", str(args.repaired), "--target", "headless-shell", "--phase", "build",
        "--jobs", str(args.jobs)], ROOT, logs / "incremental-build-driver.log", env)
    repair.verify(engine, logs)
    repaired_provenance_file = args.repaired / "bin/provenance.json"
    repaired_provenance = json.loads(repaired_provenance_file.read_text())
    repaired_provenance.update(optimizer_profile=manifest, optimizer_provider=provider_lock,
                              unmodified_upstream_build=False, incremental_from_original=before_hash)
    repaired_provenance_file.write_text(json.dumps(repaired_provenance, indent=2) + "\n")
    if digest(args.repaired / "bin/headless_shell") != repaired_provenance["binary_sha256"]:
        raise ValueError("Repaired browser binary differs from its provenance")
    result, report, failure = runtime(args.reference, args.repaired / "bin/headless_shell", args.repaired)
    aggregate["repaired"] = {"strict_gate": result, "report": report, "failure": failure,
                             "profile": manifest["profile"], "provider": provider_lock}
    if result["exit_code"] or report.get("status") != "passed":
        aggregate["failures"].append("repaired_browser_runtime_failed")
    if digest(binary) != before_hash or digest(original_provenance) != original_provenance_hash:
        raise ValueError("The retained original browser binary or provenance changed during repair")
    builder.verify_reference(args.reference, lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference", "optimizer-provider", "work", "original", "repaired"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for name in ("reference", "optimizer_provider", "work", "original", "repaired"):
        setattr(args, name, getattr(args, name).resolve())
    if args.original == args.repaired or args.repaired.exists():
        parser.error("The repaired delivery directory must be separate and new")
    aggregate = {"status": "failed", "reuse_source_and_output_tree": True, "failures": []}
    try:
        run_profiles(args, aggregate)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        aggregate["failures"].append(str(error))
    if not aggregate["failures"]:
        aggregate["status"] = "passed_repaired_profile"
    args.original.mkdir(parents=True, exist_ok=True)
    (args.original / "profiles-report.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps({"status": aggregate["status"], "failures": aggregate["failures"]}))
    return int(bool(aggregate["failures"]))


if __name__ == "__main__":
    sys.exit(main())
