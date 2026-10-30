#!/usr/bin/env python3
"""Generate the binding patch from the pinned, unmodified Firefox files."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path

import engine_emit_patch
import engine_native_patch

ROOT = Path(__file__).resolve().parents[1]
BINDING_PATHS = (
    "dom/bindings/Codegen.py",
    "dom/bindings/BindingUtils.h",
    "dom/bindings/moz.build",
)
PATHS = BINDING_PATHS + engine_emit_patch.PATHS + engine_native_patch.PATHS

def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Source anchor is missing or ambiguous; port the patch first")
    return text.replace(old, new, 1)


def transform(path, data):
    if path in engine_emit_patch.PATHS:
        return engine_emit_patch.transform(path, data)
    if path in engine_native_patch.PATHS:
        return engine_native_patch.transform(path, data)
    text = data.decode("utf-8")
    if path.endswith("Codegen.py"):
        hook = '''        # Per-property forwarding entries retain template member names.
        member = (getattr(self, "method", None) or getattr(self, "attr", None)
                  or getattr(self, "_ctor", None))
        if (member and getattr(member, "identifier", None)
                and not hasattr(self, "attrNameString")):
            types = {arg.argType for arg in self.args}
            names = {arg.name for arg in self.args}
            trace_class = None
            operation = None
            if "JSJitGetterCallArgs" in types:
                trace_class = "AutoTraceGetter"
            elif "JSJitSetterCallArgs" in types:
                trace_class = "AutoTraceSetter"
            elif "const JSJitMethodCallArgs&" in types:
                trace_class = "AutoTraceMethod"
            elif {"argc", "vp"} <= names:
                if getattr(self, "_ctor", None):
                    operation = "construct"
                elif profiler_label and "STRING_TEMPLATE_GETTER" in profiler_label:
                    operation = "get"
                elif profiler_label and "STRING_TEMPLATE_SETTER" in profiler_label:
                    operation = "set"
                elif profiler_label and "STRING_TEMPLATE_METHOD" in profiler_label:
                    operation = "call"
                if operation:
                    trace_class = "AutoTraceNative"
            if trace_class:
                interface = self.descriptor.interface.identifier.name
                name = GetWebExposedName(member, self.descriptor)
                if operation:
                    scope = "constructor" if operation == "construct" else "static"
                    arguments = 'argc, vp, "%s", "%s", "%s", "%s"' % (
                        interface, name, operation, scope)
                else:
                    arguments = 'args, "%s", "%s"' % (interface, name)
                prologue += indent(
                    'mozilla::camoutrace::%s camoufoxTrace(cx, %s);\\n'
                    % (trace_class, arguments)
                )

'''
        old = '            prologue += indent(profiler_label) + "\\n"\n\n        return prologue'
        text = replace_once(text, old, old.replace("        return prologue", hook + "        return prologue"))
    elif path.endswith("BindingUtils.h"):
        old = '#include "mozilla/dom/BindingDeclarations.h"\n'
        text = replace_once(text, old, old + '#include "mozilla/dom/CamoufoxTrace.h"\n')
    elif path.endswith("moz.build"):
        old = '    "CallbackObject.h",\n'
        text = replace_once(text, old, old + '    "CamoufoxTrace.h",\n')
        old = '\nUNIFIED_SOURCES += [\n'
        text = replace_once(text, old, '\nSOURCES += ["CamoufoxTrace.cpp"]\n' + old)
    else:
        raise ValueError(f"Unsupported source path: {path}")
    return text.encode("utf-8")


def render(source, manifest):
    patch = []
    for path in PATHS:
        original = (source / path).read_bytes()
        actual = git_blob(original)
        if actual != manifest["files"][path]["before"]:
            raise ValueError(f"Unrecognized source {path}: git blob {actual}")
        updated = transform(path, original)
        if git_blob(updated) != manifest["files"][path]["after"]:
            raise ValueError(f"Generated hash differs for {path}; update the baseline deliberately")
        patch.append(f"diff --git a/{path} b/{path}\n")
        patch.extend(difflib.unified_diff(
            original.decode().splitlines(keepends=True),
            updated.decode().splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
    return "".join(patch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="fail if the committed patch differs")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "baseline.json").read_text())
    try:
        patch = render(args.source, manifest)
        destination = ROOT / "patches/native-api-trace.patch"
        if args.check:
            if destination.read_text() != patch:
                raise ValueError("Committed patch differs from the generated diff")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(patch)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
