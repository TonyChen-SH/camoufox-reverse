#!/usr/bin/env python3
"""Generate the explicit optimizer repair from the pinned VisibleV8 source.

V8 and VisibleV8 retain their original BSD notices in generated source.
This profile changes optimization boundaries and is not unmodified upstream.
"""

import argparse
import difflib
import hashlib
import json
from pathlib import Path

PROFILE = "camoufox-visiblev8-optimizer.1"
SHELL_PROFILE = "visiblev8-shell-fixture-compat.1"
V8_COMMIT = "82b65fdd7b5847a41e76c9eeb04351ebec267785"
BEFORE = {
    "src/maglev/maglev-graph-builder.cc": "657f35026ac9e3ec8276171d47386cb58a9e3746369eb3f8b898990a2dc97453",
    "src/compiler/js-call-reducer.cc": "a7271c187d803fa2c6e642afbe0e5fd593e07c7902737ca5a30d77d2534a5595",
    "src/execution/isolate.cc": "e2515ba49449fd7d54675a7628e9f1833d4243f6783ba6a82c7504bf7141ccd5",
    "samples/shell.cc": "884cfece4e9dab4f630fd7be24ea9a72aefbba134bf62169d5bb0702042b3786",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError("Pinned optimizer repair anchor changed")
    return source.replace(before, after, 1)


def preserve_builtin(source, start, end, fallback):
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Pinned optimizer function boundary changed")
    prefix, remainder = source.split(start, 1)
    body, suffix = remainder.split(end, 1)
    return (prefix + start + "\n#ifdef VV8_TRACE_PROPERTIES\n"
            "  // Preserve VisibleV8's instrumented builtin boundary.\n"
            f"  return {fallback};\n#else" + body + "\n#endif" + end + suffix)


def transform(name, source):
    if name == "src/maglev/maglev-graph-builder.cc":
        source = preserve_builtin(source,
            "MaybeReduceResult MaglevGraphBuilder::TryReduceReflectGet(\n"
            "    compiler::JSFunctionRef target_function, CallArguments& args) {",
            "\n}\n\nMaybeReduceResult MaglevGraphBuilder::TryReduceReflectHas(", "{}")
        source = preserve_builtin(source,
            "MaybeReduceResult MaglevGraphBuilder::TryBuildCallKnownApiFunction(\n"
            "    compiler::JSFunctionRef function, compiler::SharedFunctionInfoRef shared,\n"
            "    CallArguments& args) {",
            "\n}\n\nbool MaglevGraphBuilder::IsTheHoleConstant(", "{}")
        source = replace_once(source,
            "void MaglevGraphBuilder::BuildLoopForPeeling() {\n"
            "  int loop_header = iterator_.current_offset();\n",
            "void MaglevGraphBuilder::BuildLoopForPeeling() {\n"
            "  int loop_header = iterator_.current_offset();\n"
            "#ifdef VV8_TRACE_PROPERTIES\n"
            "  const auto start_source_positions = source_position_iterator_.GetState();\n"
            "  const auto start_provenance = reducer_.current_provenance();\n"
            "#endif\n")
        source = replace_once(source,
            "  DCHECK_NE(iterator_.current_offset(), loop_header);\n"
            "  iterator_.SetOffset(loop_header);\n",
            "  DCHECK_NE(iterator_.current_offset(), loop_header);\n"
            "#ifdef VV8_TRACE_PROPERTIES\n"
            "  // Revisited bytecode needs the same source-position state.\n"
            "  source_position_iterator_.RestoreState(start_source_positions);\n"
            "  reducer_.SetCurrentProvenance(start_provenance);\n"
            "#endif\n"
            "  iterator_.SetOffset(loop_header);\n")
        return source
    if name == "src/compiler/js-call-reducer.cc":
        return preserve_builtin(source,
            "Reduction JSCallReducer::ReduceReflectGet(Node* node) {",
            "\n}\n\n// https://tc39.es/ecma262/#sec-reflect.has", "NoChange()")
    if name == "src/execution/isolate.cc":
        return replace_once(source,
            "bool Isolate::NeedsDetailedOptimizedCodeLineInfo() const {\n"
            "  return NeedsSourcePositions() || detailed_source_positions_for_profiling();\n"
            "}",
            "bool Isolate::NeedsDetailedOptimizedCodeLineInfo() const {\n"
            "#ifdef VV8_TRACE_PROPERTIES\n"
            "  // Runtime loggers resolve optimized callers through their PC tables.\n"
            "  return true;\n"
            "#else\n"
            "  return NeedsSourcePositions() || detailed_source_positions_for_profiling();\n"
            "#endif\n"
            "}")
    if name == "samples/shell.cc":
        return replace_once(source,
            "  v8::Local<v8::ObjectTemplate> global = v8::ObjectTemplate::New(isolate);",
            "  // Match the embedder class used by the unchanged upstream goldens.\n"
            "  v8::Local<v8::FunctionTemplate> global_constructor =\n"
            "      v8::FunctionTemplate::New(isolate);\n"
            "  global_constructor->SetClassName(\n"
            '      v8::String::NewFromUtf8Literal(isolate, "global"));\n'
            "  v8::Local<v8::ObjectTemplate> global =\n"
            "      v8::ObjectTemplate::New(isolate, global_constructor);")
    raise ValueError(f"Unexpected optimizer source: {name}")


def apply(engine, evidence, shell_fixture=False):
    names = [name for name in BEFORE if shell_fixture or name != "samples/shell.cc"]
    original = {name: (engine / name).read_bytes() for name in names}
    for name, data in original.items():
        if sha(data) != BEFORE[name]:
            raise ValueError(f"Unknown optimizer profile input: {name}")
    changed = {name: transform(name, data.decode()) for name, data in original.items()}
    manifest = {
        "profile": PROFILE, "v8_commit": V8_COMMIT,
        "unmodified_upstream_build": False,
        "native_runtime_verification_required": True,
        "jit_tiers_disabled": [], "wasm_disabled": False,
        "shell_embedder_profile": SHELL_PROFILE if shell_fixture else None,
        "repairs": ["preserve Maglev API-JSFunction and Reflect.get builtin calls",
                    "preserve TurboFan Reflect.get builtin calls",
                    "retain optimized PC source-position tables",
                    "restore source positions when Maglev rewinds peeled loops"],
        "files": {},
    }
    patch = []
    for name, after in changed.items():
        manifest["files"][name] = {"before_sha256": BEFORE[name],
                                   "after_sha256": sha(after.encode())}
        patch.extend(difflib.unified_diff(original[name].decode().splitlines(True),
            after.splitlines(True), fromfile="a/" + name, tofile="b/" + name))
    generated = "".join(patch)
    manifest["generated_patch_sha256"] = sha(generated.encode())
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "optimizer-profile.patch").write_text(generated)
    (evidence / "optimizer-profile.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for name, after in changed.items():
        (engine / name).write_text(after)
    return manifest


def verify(engine, evidence):
    manifest = json.loads((evidence / "optimizer-profile.json").read_text())
    if (manifest["profile"] != PROFILE or manifest["v8_commit"] != V8_COMMIT
            or manifest["unmodified_upstream_build"] is not False
            or manifest["jit_tiers_disabled"] or manifest["wasm_disabled"] is not False):
        raise ValueError("Optimizer profile identity or capabilities changed")
    names = set(BEFORE) - {"samples/shell.cc"}
    if manifest["shell_embedder_profile"] == SHELL_PROFILE:
        names.add("samples/shell.cc")
    elif manifest["shell_embedder_profile"] is not None:
        raise ValueError("Unknown shell embedder profile")
    if set(manifest["files"]) != names:
        raise ValueError("Optimizer profile file set changed")
    for name in names:
        item = manifest["files"][name]
        if item["before_sha256"] != BEFORE[name] or sha((engine / name).read_bytes()) != item["after_sha256"]:
            raise ValueError(f"Optimizer profile output changed: {name}")
    if sha((evidence / "optimizer-profile.patch").read_bytes()) != manifest["generated_patch_sha256"]:
        raise ValueError("Optimizer profile review diff changed")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--shell-fixture", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = (verify(args.engine, args.evidence) if args.verify else
              apply(args.engine, args.evidence, args.shell_fixture))
    print(json.dumps(result, indent=2))
