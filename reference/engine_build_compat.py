#!/usr/bin/env python3
"""Explicit include-only build repair for the immutable VisibleV8 V8 patch.

The original instrumentation is BSD-3-Clause; its checkout and patch stay intact.
Generated source keeps its upstream notices. See fixtures/LICENSE.v8 and the
delivered LICENSE.visiblev8 for the original source licenses.
"""

import difflib
import hashlib
import json
from pathlib import Path

PROFILE = "visiblev8-linux-build-compat.1"
BEFORE = {
    "src/runtime/runtime-utils.h": "d80112c190dd61a7025fa7077ff8c9b4e948c132ff87b292d458ddac07c69543",
    "src/runtime/runtime-utils.cc": "17c4c761fb4a1baef0c456ef54a0e447e66e96e244dca357c0ea4c0be6da775a",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def apply(engine, evidence, v8_commit):
    originals = {name: (engine / name).read_bytes() for name in BEFORE}
    for name, data in originals.items():
        if digest(data) != BEFORE[name]:
            raise ValueError(f"Unknown VisibleV8 build-repair input: {name}")
    header_name, source_name = BEFORE
    header, source = (originals[name].decode() for name in BEFORE)
    start = "#define V8_RUNTIME_RUNTIME_UTILS_H_\n\n"
    end = '\n#include "src/objects/objects.h"\n\nnamespace v8 {\n'
    first = '#include "src/runtime/runtime-utils.h"\n'
    if header.count(start) != 1 or header.count(end) != 1 or not source.startswith(first):
        raise ValueError("Pinned include-move anchors changed")
    prefix, implementation = header.split(start, 1)
    includes, declarations = implementation.split(end, 1)
    # Preserve every original include and every declaration/function body.
    changed = {
        header_name: prefix + start + "#include <vector>\n" + end + declarations,
        source_name: first + "\n" + includes + "\n" + source[len(first):],
    }
    manifest = {"profile": PROFILE, "v8_commit": v8_commit,
                "unmodified_upstream_build": False,
                "repair": "move implementation includes from runtime-utils.h to runtime-utils.cc",
                "runtime_function_bodies_modified": False, "files": {}}
    patch = []
    for name, after in changed.items():
        manifest["files"][name] = {"before_sha256": BEFORE[name],
                                   "after_sha256": digest(after.encode())}
        patch.extend(difflib.unified_diff(originals[name].decode().splitlines(True),
                                         after.splitlines(True), fromfile="a/" + name,
                                         tofile="b/" + name))
    evidence.mkdir(parents=True, exist_ok=True)
    generated = "".join(patch)
    (evidence / "engine-build-compat.patch").write_text(generated)
    manifest["generated_patch_sha256"] = digest(generated.encode())
    (evidence / "engine-build-compat.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name, after in changed.items():
        (engine / name).write_text(after)
    return manifest


def verify(engine, evidence, v8_commit):
    manifest = json.loads((evidence / "engine-build-compat.json").read_text())
    if (manifest["profile"] != PROFILE or manifest["v8_commit"] != v8_commit
            or manifest["runtime_function_bodies_modified"] is not False
            or set(manifest["files"]) != set(BEFORE)):
        raise ValueError("Missing or mismatched engine build compatibility profile")
    for name, before in BEFORE.items():
        info = manifest["files"][name]
        if info["before_sha256"] != before or digest((engine / name).read_bytes()) != info["after_sha256"]:
            raise ValueError(f"Engine build-repair output changed: {name}")
    if digest((evidence / "engine-build-compat.patch").read_bytes()) != manifest["generated_patch_sha256"]:
        raise ValueError("Engine build-repair review diff changed")
    return manifest
