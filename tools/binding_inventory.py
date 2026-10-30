#!/usr/bin/env python3
"""Inventory trace hooks in the generated C++ files of an actual browser build."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import tempfile

PREFIX = "mozilla::camoutrace::AutoTrace"
MARKER = re.compile(re.escape(PREFIX) + r"\w*")
HOOK = re.compile(
    re.escape(PREFIX)
    + r"(?P<kind>Getter|Setter|Method|Native)\s+camoufoxTrace\s*\(\s*cx\s*,"
      r"(?P<arguments>[^;]*)\)\s*;",
    re.DOTALL,
)
OPERATIONS = ("get", "set", "call", "construct")
SCOPES = ("instance", "static", "constructor")


def _entry(match):
    kind = match["kind"]
    arguments = match["arguments"]
    prefix = r"\s*argc\s*,\s*vp\s*," if kind == "Native" else r"\s*args\s*,"
    start = re.match(prefix, arguments)
    if start is None:
        raise ValueError("unexpected trace argument prefix")
    values = json.loads("[" + arguments[start.end():] + "]")
    if (len(values) != (4 if kind == "Native" else 2)
        or not all(isinstance(value, str) and value
                   and all(32 <= ord(character) < 127 for character in value)
                   for value in values)):
        raise ValueError("trace names and operation/scope must be nonempty printable ASCII strings")
    interface, member = values[:2]
    if kind == "Native":
        operation, scope = values[2:]
        if not ((scope == "static" and operation in ("get", "set", "call"))
                or (scope == "constructor" and operation == "construct")):
            raise ValueError("unexpected native trace operation/scope")
    else:
        operation = {"Getter": "get", "Setter": "set", "Method": "call"}[kind]
        scope = "instance"
    return interface, member, operation, scope


def inventory(root):
    """Count distinct binding operations and each generated physical hook site."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"Generated binding root must be a directory: {root}")
    root = root.resolve()
    entries = defaultdict(Counter)
    files_scanned = 0
    files_with_hooks = 0
    for source in sorted(root.rglob("*")):
        if source.is_symlink():
            if source.suffix == ".cpp" or source.is_dir():
                raise ValueError(f"Refusing symlink in generated binding sources: {source}")
            continue
        if source.suffix != ".cpp" or not source.is_file():
            continue
        files_scanned += 1
        relative = source.relative_to(root).as_posix()
        text = source.read_text(encoding="utf-8")
        found = False
        for marker in MARKER.finditer(text):
            match = HOOK.match(text, marker.start())
            line = text.count("\n", 0, marker.start()) + 1
            try:
                if match is None:
                    raise ValueError("malformed or unknown trace hook")
                entry = _entry(match)
            except ValueError as error:
                raise ValueError(f"{relative}:{line}: {error}") from error
            entries[entry][relative] += 1
            found = True
        files_with_hooks += found
    if not entries:
        raise ValueError(f"No trace hooks found in generated C++ sources: {root}")

    by_operation = {operation: {"logical_entries": 0, "generated_sites": 0}
                    for operation in OPERATIONS}
    by_scope = {scope: {"logical_entries": 0, "generated_sites": 0}
                for scope in SCOPES}
    result_entries = []
    for (interface, member, operation, scope), sources in entries.items():
        count = sum(sources.values())
        for group, name in ((by_operation, operation), (by_scope, scope)):
            group[name]["logical_entries"] += 1
            group[name]["generated_sites"] += count
        result_entries.append({
            "api": f"{interface}.{member}", "interface": interface, "member": member,
            "op": operation, "scope": scope, "generated_sites": count,
            "sources": [{"path": path, "occurrences": occurrences}
                        for path, occurrences in sorted(sources.items())],
        })
    result_entries.sort(key=lambda entry: (entry["api"], entry["op"], entry["scope"]))
    interfaces = sorted({entry["interface"] for entry in result_entries})
    api_names = sorted({entry["api"] for entry in result_entries})
    return {
        "schema": 1,
        "coverage": "generated-webidl-binding-entries",
        "runtime_limitations": "Generated C++ hook sites only; this is not a count of runtime-reachable APIs or a guarantee that optimized, cached or non-WebIDL accesses produce events.",
        "counts": {
            "interfaces": len(interfaces), "api_names": len(api_names),
            "logical_entries": len(result_entries),
            "generated_sites": sum(entry["generated_sites"] for entry in result_entries),
            "cpp_files_scanned": files_scanned, "files_with_hooks": files_with_hooks,
        },
        "interfaces": interfaces,
        "api_names": api_names,
        "by_operation": by_operation,
        "by_scope": by_scope,
        "entries": result_entries,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    temporary = None
    try:
        result = inventory(args.root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as destination:
            temporary = Path(destination.name)
            json.dump(result, destination, ensure_ascii=True, sort_keys=True, indent=2)
            destination.write("\n")
        temporary.replace(args.output)
        temporary = None
    except (OSError, ValueError) as error:
        parser.error(str(error))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps(result["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
