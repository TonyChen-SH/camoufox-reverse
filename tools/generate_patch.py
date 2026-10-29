#!/usr/bin/env python3
"""Generate the binding patch from the pinned, unmodified Firefox files."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    "dom/bindings/Codegen.py",
    "dom/bindings/BindingUtils.h",
    "dom/bindings/moz.build",
)
APIS = {
    "Navigator.userAgent": "get",
    "Navigator.appVersion": "get",
    "Navigator.platform": "get",
    "Navigator.hardwareConcurrency": "get",
    "WorkerNavigator.userAgent": "get",
    "WorkerNavigator.appVersion": "get",
    "WorkerNavigator.platform": "get",
    "WorkerNavigator.hardwareConcurrency": "get",
    "Screen.width": "get",
    "Screen.height": "get",
    "WebGLRenderingContext.getParameter": "call",
    "WebGL2RenderingContext.getParameter": "call",
    "CanvasRenderingContext2D.getImageData": "call",
    "HTMLCanvasElement.toDataURL": "call",
    "AudioBuffer.getChannelData": "call",
    "AudioBuffer.copyFromChannel": "call",
}


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Source anchor is missing or ambiguous; port the patch first")
    return text.replace(old, new, 1)


def transform(path, data):
    text = data.decode("utf-8")
    if path.endswith("Codegen.py"):
        getters = tuple(api for api, op in APIS.items() if op == "get")
        methods = tuple(api for api, op in APIS.items() if op == "call")
        hook = '''        # Trace only the specialized binding entry, not its generic wrapper.
        member = getattr(self, "method", None) or getattr(self, "attr", None)
        if member and profiler_label and not member.isStatic():
            api = self.descriptor.interface.identifier.name + "." + member.identifier.name
            trace_class = None
            if "STRING_TEMPLATE_GETTER" in profiler_label and api in %s:
                trace_class = "AutoTraceGetter"
            elif "STRING_TEMPLATE_METHOD" in profiler_label and api in %s:
                trace_class = "AutoTraceMethod"
            if trace_class:
                prologue += indent(
                    'mozilla::camoutrace::%%s camoufoxTrace(cx, args, "%%s");\\n'
                    %% (trace_class, api)
                )

''' % (repr(getters), repr(methods))
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
