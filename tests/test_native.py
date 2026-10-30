import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from apply import apply
from generate_patch import PATHS, git_blob, render, transform


class BufferTests(unittest.TestCase):
    def test_real_cpp_serializer(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "buffer-test"
            subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra",
                "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                "-I", str(ROOT / "additions/js/public/experimental"),
                str(ROOT / "tests/test_buffer.cpp"), "-o", str(binary),
            ], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, check=True)
            records = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertIn("\0", records[0])
            self.assertIn("\n", records[0])
            self.assertEqual(records[1], "中")
            self.assertEqual(records[2], "\ufffd" * 5)
            self.assertEqual(records[3], [2**64 - 1, 0.125, "Infinity", "-Infinity", "NaN"])
            self.assertEqual(records[4], "%o %O %j %% %s")
            # Browser-driver log sinks can interpret printf-style formatting.
            self.assertNotIn("%", result.stdout)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "FIREFOX_SOURCE" not in os.environ:
            raise RuntimeError("Set FIREFOX_SOURCE to the pinned source files; see README")
        cls.source = Path(os.environ["FIREFOX_SOURCE"])
        cls.manifest = json.loads((ROOT / "baseline.json").read_text())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.tree = Path(self.temporary.name)
        for path in PATHS:
            destination = self.tree / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.source / path, destination)

    def test_patch_reproducible_and_matches_pinned_files(self):
        patch = render(self.source, self.manifest)
        self.assertEqual(patch, (ROOT / "patches/native-api-trace.patch").read_text())

    def test_check_does_not_mutate_and_apply_is_idempotent(self):
        before = {path: (self.tree / path).read_bytes() for path in PATHS}
        self.assertEqual(apply(self.tree, check=True), "applicable")
        self.assertEqual(before, {path: (self.tree / path).read_bytes() for path in PATHS})
        self.assertEqual(apply(self.tree), "applied")
        self.assertEqual(apply(self.tree), "already_applied")
        for path in PATHS:
            self.assertEqual(git_blob((self.tree / path).read_bytes()), self.manifest["files"][path]["after"])
        for path in self.manifest["additions"]:
            self.assertEqual((self.tree / path).read_bytes(), (ROOT / "additions" / path).read_bytes())

    def test_unknown_version_fails_before_any_writes(self):
        path = self.tree / PATHS[-1]
        path.write_bytes(path.read_bytes() + b"# unknown source\n")
        before = {name: (self.tree / name).read_bytes() for name in PATHS}
        with self.assertRaisesRegex(ValueError, "Unsupported or modified"):
            apply(self.tree)
        self.assertEqual(before, {name: (self.tree / name).read_bytes() for name in PATHS})
        self.assertFalse((self.tree / "dom/bindings/CamoufoxTrace.cpp").exists())

    def test_partial_install_and_existing_overlay_are_rejected(self):
        target = self.tree / PATHS[0]
        target.write_bytes(transform(PATHS[0], target.read_bytes()))
        with self.assertRaisesRegex(ValueError, "Partial installation"):
            apply(self.tree)
        shutil.copyfile(self.source / PATHS[0], target)
        (self.tree / "dom/bindings/CamoufoxTrace.cpp").write_text("existing change\n")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            apply(self.tree)

    def test_missing_overlay_after_apply_is_not_a_success(self):
        apply(self.tree)
        (self.tree / "dom/bindings/CamoufoxTrace.cpp").unlink()
        with self.assertRaisesRegex(ValueError, "missing overlay"):
            apply(self.tree)

    def test_symlink_source_cannot_write_outside_tree(self):
        with tempfile.TemporaryDirectory() as external:
            target = self.tree / PATHS[0]
            other = Path(external) / "Codegen.py"
            target.rename(other)
            target.symlink_to(other)
            with self.assertRaisesRegex(ValueError, "escapes"):
                apply(self.tree)

    def test_codegen_hook_covers_binding_shapes_without_an_api_allowlist(self):
        updated = transform(PATHS[0], (self.source / PATHS[0]).read_bytes())
        module = ast.parse(updated)
        base = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CGAbstractMethod")
        function = next(node for node in base.body if isinstance(node, ast.FunctionDef) and node.name == "definition_prologue")
        namespace = {"indent": lambda text: textwrap.indent(text, "  ")}
        exposed_name = next(node for node in module.body
                            if isinstance(node, ast.FunctionDef) and node.name == "GetWebExposedName")
        exec(compile(ast.Module(body=[exposed_name], type_ignores=[]), "Codegen.py", "exec"), namespace)
        exec(compile(ast.Module(body=[function], type_ignores=[]), "Codegen.py", "exec"), namespace)

        def generate(api, op, static=False, template_common=False):
            interface, name = api.split(".")
            member = NS(identifier=NS(name=name), isStatic=lambda: static)
            argtype = {"GETTER": "JSJitGetterCallArgs", "SETTER": "JSJitSetterCallArgs",
                       "METHOD": "const JSJitMethodCallArgs&"}.get(op)
            args = [NS(name="cx", argType="JSContext*")]
            if static or op == "CONSTRUCTOR":
                args += [NS(name="argc", argType="unsigned"), NS(name="vp", argType="JS::Value*")]
            elif argtype:
                args += [NS(name="args", argType=argtype)]
            mock = NS(
                error_reporting_label=lambda: None,
                _template=lambda: "", _decorators=lambda: "", name="binding",
                _argstring=lambda _: "JSContext* cx, JSJitGetterCallArgs args",
                auto_profiler_label=lambda: f"STRING_TEMPLATE_{op}",
                descriptor=NS(interface=NS(identifier=NS(name=interface)),
                              operations={"Stringifier": member if name == "__stringifier" else None}),
                args=args,
            )
            if op == "CONSTRUCTOR": mock._ctor = member
            elif op == "METHOD": mock.method = member
            else: mock.attr = member
            if template_common:
                mock.attrNameString = '"shared property template"'
            return namespace["definition_prologue"](mock, False)

        for api, kind, native in (
            ("Navigator.language", "GETTER", "AutoTraceGetter"),
            ("Document.title", "SETTER", "AutoTraceSetter"),
            ("Storage.setItem", "METHOD", "AutoTraceMethod"),
            ("FutureInterface.futureMember", "GETTER", "AutoTraceGetter"),
            ("URL.__stringifier", "METHOD", "AutoTraceMethod"),
        ):
            with self.subTest(api=api, kind=kind):
                generated = generate(api, kind)
                self.assertEqual(generated.count("camoufoxTrace("), 1)
                self.assertIn(native, generated)
                interface, member = api.split(".")
                if member == "__stringifier":
                    member = "toString"
                self.assertIn(f'"{interface}", "{member}"', generated)
                if kind != "METHOD":
                    self.assertNotIn("camoufoxTrace(", generate(api, kind, template_common=True))
                static = generate(api, kind, static=True)
                self.assertEqual(static.count("camoufoxTrace("), 1)
                self.assertIn("AutoTraceNative", static)
                self.assertIn('"static"', static)
        constructor = generate("URL.constructor", "CONSTRUCTOR")
        self.assertEqual(constructor.count("camoufoxTrace("), 1)
        self.assertIn("AutoTraceNative", constructor)
        self.assertIn('"construct", "constructor"', constructor)


if __name__ == "__main__":
    unittest.main()
