import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from binding_inventory import inventory, main


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_counts_logical_entries_and_physical_sites_from_generated_code(self):
        self.source("nested dir/ValueBinding.cpp", '''
bool get_value(JSContext* cx, JSJitGetterCallArgs args) {
  mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value");
  return true;
}
bool set_value(JSContext* cx, JSJitSetterCallArgs args) {
  mozilla::camoutrace::AutoTraceSetter camoufoxTrace(
      cx, args, "Value", "value");
  return true;
}
''')
        self.source("URLBinding.cpp", '''
mozilla::camoutrace::AutoTraceNative camoufoxTrace(
    cx, argc, vp, "URL", "canParse", "call", "static");
mozilla::camoutrace::AutoTraceNative camoufoxTrace(
    cx, argc, vp, "URL", "constructor", "construct", "constructor");
''')
        self.source("duplicate/ValueBinding.cpp", '''
mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value");
mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value");
''')
        self.source("unrelated.cpp", "bool unrelated() { return true; }\n")
        result = inventory(self.root)
        self.assertEqual(result["counts"], {
            "interfaces": 2, "api_names": 3, "logical_entries": 4,
            "generated_sites": 6, "cpp_files_scanned": 4, "files_with_hooks": 3,
        })
        self.assertEqual(result["interfaces"], ["URL", "Value"])
        self.assertEqual(result["api_names"], ["URL.canParse", "URL.constructor", "Value.value"])
        self.assertEqual(result["by_operation"]["get"], {"logical_entries": 1, "generated_sites": 3})
        self.assertEqual(result["by_scope"]["instance"], {"logical_entries": 2, "generated_sites": 4})
        getter = next(entry for entry in result["entries"]
                      if entry["api"] == "Value.value" and entry["op"] == "get")
        self.assertEqual(getter["sources"], [
            {"path": "duplicate/ValueBinding.cpp", "occurrences": 2},
            {"path": "nested dir/ValueBinding.cpp", "occurrences": 1},
        ])
        self.assertEqual(getter["interface"], "Value")
        self.assertEqual(getter["member"], "value")
        self.assertEqual(getter["generated_sites"], 3)
        self.assertEqual(result["coverage"], "generated-webidl-binding-entries")
        self.assertIn("runtime", result["runtime_limitations"])

    def test_instance_method_and_static_accessors_keep_their_operation_and_scope(self):
        self.source("ExampleBinding.cpp", '''
mozilla::camoutrace::AutoTraceMethod camoufoxTrace(cx, args, "Example", "run");
mozilla::camoutrace::AutoTraceNative camoufoxTrace(cx, argc, vp, "Example", "status", "get", "static");
mozilla::camoutrace::AutoTraceNative camoufoxTrace(cx, argc, vp, "Example", "status", "set", "static");
''')
        entries = inventory(self.root)["entries"]
        self.assertEqual([(entry["api"], entry["op"], entry["scope"]) for entry in entries], [
            ("Example.run", "call", "instance"),
            ("Example.status", "get", "static"),
            ("Example.status", "set", "static"),
        ])

    def test_missing_or_uninstrumented_sources_fail(self):
        with self.assertRaisesRegex(ValueError, "directory"):
            inventory(self.root / "missing")
        file = self.source("plain.cpp", "void plain();\n")
        with self.assertRaisesRegex(ValueError, "directory"):
            inventory(file)
        with self.assertRaisesRegex(ValueError, "No trace hooks"):
            inventory(self.root)

    def test_malformed_hooks_do_not_silently_reduce_inventory_counts(self):
        valid = 'mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value");\n'
        invalid = [
            'mozilla::camoutrace::AutoTraceUnknown camoufoxTrace(cx, args, "Value", "value");',
            'mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value")',
            'mozilla::camoutrace::AutoTraceSetter camoufoxTrace(cx, args, "Value");',
            'mozilla::camoutrace::AutoTraceNative camoufoxTrace(cx, argc, vp, "Value", "run", "bogus", "static");',
            'mozilla::camoutrace::AutoTraceNative camoufoxTrace(cx, argc, vp, "Value", "run", "call", "constructor");',
            'mozilla::camoutrace::AutoTraceMethod camoufoxTrace(cx, args, "Value", "");',
        ]
        for malformed in invalid:
            with self.subTest(source=malformed):
                self.source("MalformedBinding.cpp", valid + malformed + "\n")
                with self.assertRaisesRegex(ValueError, r"MalformedBinding.cpp:2"):
                    inventory(self.root)

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "OutsideBinding.cpp"
            outside.write_text('mozilla::camoutrace::AutoTraceGetter camoufoxTrace(cx, args, "Value", "value");')
            (self.root / "LinkedBinding.cpp").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                inventory(self.root)

    def test_cli_writes_json_and_preserves_existing_output_when_scan_fails(self):
        self.source("ExampleBinding.cpp", 'mozilla::camoutrace::AutoTraceMethod camoufoxTrace(cx, args, "Example", "run");')
        output = self.root / "output dir" / "inventory.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "--output", str(output)]), 0)
        saved = output.read_text()
        self.assertEqual(json.loads(saved), inventory(self.root))
        self.source("BrokenBinding.cpp", "mozilla::camoutrace::AutoTraceGetter missing;\n")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--root", str(self.root), "--output", str(output)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(output.read_text(), saved)


if __name__ == "__main__":
    unittest.main()
