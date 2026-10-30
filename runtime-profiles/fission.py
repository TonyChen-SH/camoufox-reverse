#!/usr/bin/env python3
"""Require actual cross-process Window observations from the full engine gate."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from engine_runtime_check import CHECKS

PREFERENCES = {"fission.autostart": True, "fission.webContentIsolationStrategy": 1}


def verify_report(report):
    checks = report.get("checks", {})
    if (report.get("status") != "passed" or set(checks) != set(CHECKS)
            or any(check.get("status") != "passed" for check in checks.values())):
        raise ValueError("The complete engine gate must pass in the Fission profile")
    frames = checks["contexts_and_threads"]["details"]["frame_window_classes"]
    main, cross = frames.get("main_pids", []), frames.get("cross_frame_pids", [])
    if (frames.get("cross_process_frame_observed") is not True or not main or not cross
            or any(type(pid) is not int or pid < 1 for pid in main + cross)
            or set(main) & set(cross)):
        raise ValueError("The cross-site frame must execute in a different native process")
    if frames.get("reads") != 2 or frames.get("receiver_classes") != ["Window", "Window"]:
        raise ValueError("Both live frame reads must retain their native Window class")
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"profile": "fission", "status": "failed", "startup_preferences": PREFERENCES,
              "binary": str(binary)}
    try:
        environment = dict(os.environ, CAMOU_PREFS_1=json.dumps(PREFERENCES, separators=(",", ":")))
        subprocess.run([sys.executable, str(ROOT / "tools/engine_runtime_check.py"),
                        "--binary", str(binary), "--output", str(output / "engine")],
                       env=environment, check=True)
        engine = json.loads((output / "engine/engine-runtime-report.json").read_text())
        report["frame_window_classes"] = verify_report(engine)
        report["engine_checks"] = len(CHECKS)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (output / "profile-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
