#!/usr/bin/env python3
"""Apply this pilot to a prepared source tree, refusing unknown source revisions."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from generate_patch import PATHS, ROOT, git_blob


def replace_file(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        staging = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            os.chmod(staging, path.stat().st_mode if path.exists() else 0o644)
            staging.replace(path)
        finally:
            staging.unlink(missing_ok=True)


def apply(source, check=False):
    source = source.resolve(strict=True)
    manifest = json.loads((ROOT / "baseline.json").read_text())
    patch = ROOT / "patches/native-api-trace.patch"
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest["patch_sha256"]:
        raise ValueError("Patch integrity check failed")
    originals = {}
    expected = {}
    for path in PATHS:
        target = source / path
        if not target.resolve().is_relative_to(source) or target.is_symlink():
            raise ValueError(f"Source path escapes the selected tree: {path}")
        data = target.read_bytes()
        actual = git_blob(data)
        hashes = manifest["files"][path]
        if actual not in (hashes["before"], hashes["after"]):
            raise ValueError(f"Unsupported or modified source {path}: git blob {actual}")
        originals[path] = data
        expected[path] = hashes["after"]
    overlay = {}
    for path, sha in manifest["additions"].items():
        data = (ROOT / "additions" / path).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"Overlay integrity check failed: {path}")
        target = source / path
        if not target.resolve().is_relative_to(source) or target.is_symlink():
            raise ValueError(f"Overlay path escapes the selected tree: {path}")
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f"Refusing to overwrite existing overlay: {path}")
        overlay[path] = data

    after = [git_blob(data) == expected[path] for path, data in originals.items()]
    if all(after):
        if not all((source / path).is_file() for path in overlay):
            raise ValueError("Partial installation: patched bindings but missing overlay")
        return "already_applied"
    if any(after) or any((source / path).exists() for path in overlay):
        raise ValueError("Partial installation; restore the pristine baseline before applying")

    with tempfile.TemporaryDirectory(prefix="camoufox-trace-") as temporary:
        stage = Path(temporary)
        for path, data in originals.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=stage, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=stage, check=True)
        updates = {path: (stage / path).read_bytes() for path in PATHS}
        if any(git_blob(data) != expected[path] for path, data in updates.items()):
            raise ValueError("Patched output hash differs from the baseline")
        updates.update(overlay)
        if check:
            return "applicable"
        written = []
        try:
            for path, data in updates.items():
                target = source / path
                written.append(path)
                replace_file(target, data)
        except OSError:
            for path in reversed(written):
                if path in originals:
                    replace_file(source / path, originals[path])
                else:
                    (source / path).unlink(missing_ok=True)
            raise
    return "applied"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="prepared Firefox/Camoufox source root")
    parser.add_argument("--check", action="store_true", help="validate without changing the source")
    args = parser.parse_args()
    try:
        status = apply(args.source, args.check)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({"status": status, "source": str(args.source.resolve())}))


if __name__ == "__main__":
    main()
