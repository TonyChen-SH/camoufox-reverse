#!/usr/bin/env python3
"""Fetch only the pinned binding files for patch checks, not a Firefox build."""

import argparse
import json
from pathlib import Path
import urllib.request

from generate_patch import PATHS, ROOT, git_blob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "baseline.json").read_text())
    commit = manifest["firefox"]["commit"]
    try:
        for path in PATHS:
            url = f"https://raw.githubusercontent.com/mozilla-firefox/firefox/{commit}/{path}"
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000 or git_blob(data) != manifest["files"][path]["before"]:
                raise ValueError(f"Upstream hash mismatch: {path}")
            target = args.destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"Verified {len(PATHS)} source files at Firefox {commit}")


if __name__ == "__main__":
    main()
