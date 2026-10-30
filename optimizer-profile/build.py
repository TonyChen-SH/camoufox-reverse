#!/usr/bin/env python3
"""Build the explicitly repaired shell with the original reference toolchain."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import repair

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
import build_visiblev8_reference as original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "build", "all"), default="all")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    reference, work, output = (p.resolve() for p in (args.reference, args.work, args.output))
    lock = json.loads(original.LOCK.read_text())
    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        original.verify_reference(reference, lock)
        if args.phase in ("prepare", "all"):
            engine, _ = original.prepare(reference, work, "v8-shell", logs, lock)
            original.run([sys.executable, str(Path(__file__).with_name("test_repair.py"))],
                ROOT, logs / "optimizer-source-tests.log",
                dict(os.environ, VISIBLEV8_OPTIMIZER_SOURCE=str(engine)))
            repair.apply(engine, logs, shell_fixture=True)
        if args.phase in ("build", "all"):
            profile = repair.verify(work / "v8", logs)
            original.build(reference, work, "v8-shell", logs, output / "bin", lock, args.jobs)
            provenance_file = output / "bin/provenance.json"
            provenance = json.loads(provenance_file.read_text())
            provenance["optimizer_profile"] = profile
            provenance["unmodified_upstream_build"] = False
            provenance_file.write_text(json.dumps(provenance, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        (output / "build-report.json").write_text(json.dumps({
            "status": "failed", "profile": repair.PROFILE, "error": str(error)}, indent=2) + "\n")
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
