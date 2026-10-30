#!/usr/bin/env python3
"""Fetch pinned patch inputs and native model prerequisites, not a Firefox build."""

import argparse
import json
from pathlib import Path
import urllib.request

from generate_patch import PATHS, ROOT, git_blob


# Unmodified model inputs at Firefox 9378fbc58660847fbc02c3486a0ecd6049978e25.
TEST_SOURCE = {
    "js/src/frontend/ExpressionStatementEmitter.cpp": "5e4b56bf4eafe04b643857b4a3fcfc5693408129",
    "js/src/frontend/ExpressionStatementEmitter.h": "3c752341190a1e7bcd2db54e2413399db466eaad",
    "js/src/frontend/CForEmitter.cpp": "81ad612792e532fe24fceb82c8dc8d94c842e220",
    "dom/base/nsGlobalWindowOuter.cpp": "a287649849cedcffec66191b8c393e613c0519ed",
    "dom/base/RemoteOuterWindowProxy.cpp": "1ec0bd6d5567521ea28e0c5e3b9a98bac6992036",
    "dom/bindings/RemoteObjectProxy.cpp": "5be2968f4df0fad3c0b215c855fa9cd7b46ffd85",
    "dom/bindings/RemoteObjectProxy.h": "e3a2caf14c88829f20da9cf7517acbf6a1b87891",
    "js/public/Proxy.h": "f502ab65ce2e3dd7322df70b5070cf3391a18d40",
    "js/src/vm/WindowProxy.cpp": "891a79ad41a5ce2ef85f3e15f0233e593e2572c8",
    "js/src/proxy/Wrapper.cpp": "c18c92e1c81394d3b53699915f4512d1f6a4e759",
    "js/public/Wrapper.h": "e8b233c9e05257e834d1e8cb4c0435d73030fa41",
    "js/src/vm/WrapperObject.h": "dea538fdcb24db504fab98b3341ac78ca2c943c0",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "baseline.json").read_text())
    commit = manifest["firefox"]["commit"]
    sources = {path: manifest["files"][path]["before"] for path in PATHS}
    sources.update(TEST_SOURCE)
    try:
        for path, expected_blob in sources.items():
            url = f"https://raw.githubusercontent.com/mozilla-firefox/firefox/{commit}/{path}"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000 or git_blob(data) != expected_blob:
                raise ValueError(f"Upstream hash mismatch: {path}")
            target = args.destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"Verified {len(sources)} source files at Firefox {commit}")


if __name__ == "__main__":
    main()
