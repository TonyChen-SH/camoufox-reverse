"""Exercise the pinned Firefox parser/generator, not a replacement IDL parser.

Run with FIREFOX_SOURCE=/path/to/firefox python3 -m unittest discover -s tests_metadata -v.
The source tree and its existing vendored Python dependencies are prerequisites.
"""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "metadata"), str(ROOT / "tools")]
import idl_inventory as export
from binding_inventory import inventory
from generate_patch import transform


DECLARATIONS = """
[Global=Window, Exposed=Window] interface Window {};
[Global=(Worker,DedicatedWorker), Exposed=DedicatedWorker]
interface DedicatedWorkerGlobalScope {};
[Exposed=(Window,Worker)] interface Base {
  attribute DOMString id;
  undefined ping();
};
[Exposed=Window, LegacyWindowAlias=Alias, LegacyFactoryFunction=Maker()]
interface Child : Base {
  constructor();
  [BindingAlias=tint] attribute DOMString color;
  [Alias=other] undefined run();
  [ChromeOnly] readonly attribute boolean secret;
  [LegacyUnforgeable] readonly attribute DOMString token;
  [Pref="dom.test.enabled"] undefined conditional();
  static readonly attribute boolean supported;
  static undefined check();
  const long VERSION = 1;
  stringifier;
};
partial interface Child { readonly attribute long extra; };
interface mixin Shared { undefined shared(); };
Child includes Shared;
[Exposed=Window] interface Pairs { iterable<DOMString, DOMString>; };
[Exposed=Window, ClassString="Console"] namespace console { undefined log(); };
"""


class FirefoxProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        location = os.environ.get("FIREFOX_SOURCE")
        if not location:
            raise RuntimeError("FIREFOX_SOURCE is required; these tests must use the real pinned provider")
        cls.source = Path(location).resolve()
        cls.baseline = json.loads((ROOT / "baseline.json").read_text())
        export.verify_provider(cls.source, cls.baseline)
        cls.codegen = export.import_provider(cls.source)
        import Configuration
        import WebIDL
        cls.Configuration, cls.WebIDL = Configuration, WebIDL

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.webidl = self.root / "Example.webidl"
        self.webidl.write_text(DECLARATIONS)
        self.conf = self.root / "Bindings.conf"
        self.conf.write_text("DOMInterfaces = {}\nTemplatedAttributes = {}\n")
        parser = self.WebIDL.Parser(str(self.root))
        parser.parse(self.webidl.read_text(), str(self.webidl))
        self.config = self.Configuration.Configuration(
            str(self.conf), (str(self.root),), parser.finish(), [])
        self.tree, self.descriptors, self.constructors = export.extract(self.config, self.codegen)

    def traced_generator(self):
        module = types.ModuleType("idl_inventory_traced_codegen")
        module.__file__ = self.codegen.__file__
        data = Path(module.__file__).read_bytes()
        hashes = self.baseline["files"]["dom/bindings/Codegen.py"]
        if export.git_blob(module.__file__) == hashes["before"]:
            data = transform("dom/bindings/Codegen.py", data)
        elif export.git_blob(module.__file__) != hashes["after"]:
            raise ValueError("Unsupported Codegen content in provider integration test")
        with patch.dict(sys.modules, {module.__name__: module}):
            exec(compile(data, module.__file__, "exec"), module.__dict__)
        return module

    def generated_inventory(self):
        generator = self.traced_generator()
        generated = generator.CGBindingRoot(
            self.config, "ExampleBinding", str(self.webidl)).define()
        (self.root / "ExampleBinding.cpp").write_text(generated)
        return inventory(self.root)

    def test_original_parser_merges_partials_mixins_inheritance_and_binding_aliases(self):
        child = self.tree["Child"]
        self.assertEqual(child["parent"], "Base")
        self.assertIn("extra", child["properties"])
        self.assertIn("tint", child["properties"])
        self.assertIn("shared", child["methods"])
        self.assertIn("other", child["methods"])
        self.assertIn("toString", child["methods"])
        self.assertIn("check", child["methods"])
        self.assertIn("secret", child["properties"])
        self.assertIn("token", child["properties"])
        self.assertIn("supported", child["properties"])
        self.assertIn("VERSION", child["properties"])
        self.assertIn("conditional", child["methods"])
        self.assertNotIn("id", child["members"])
        self.assertNotIn("Shared", self.tree)
        for row in self.tree.values():
            self.assertEqual(set(row), {"parent", "aliasFor", "aliases", "members", "methods", "properties"})
            self.assertEqual(row["members"], sorted(set(row["methods"] + row["properties"])))

    def test_native_class_prototypes_factories_and_namespace_use_real_provider_names(self):
        for name in ("Alias", "Maker", "ChildPrototype"):
            self.assertEqual(self.tree[name]["aliasFor"], "Child")
            self.assertEqual(self.tree[name]["parent"], "Base")
            self.assertEqual(self.tree[name]["members"], self.tree["Child"]["members"])
            self.assertEqual(self.tree[name]["aliases"], [])
        self.assertEqual(self.tree["Console"]["aliasFor"], "console")
        self.assertEqual(self.constructors["Child"], {"constructor": True, "legacy_factories": ["Maker"]})
        self.assertEqual(self.tree["Pairs Iterator"]["aliasFor"], "PairsIterator")
        self.assertEqual(self.tree["Pairs IteratorPrototype"]["aliasFor"], "PairsIterator")
        self.assertEqual(self.tree["PairsIterator"]["methods"], ["next"])
        self.assertTrue({"entries", "keys", "values", "forEach", "@@iterator"}.issubset(
            self.tree["Pairs"]["methods"]))
        self.assertNotIn("Proxy", self.tree)

    def test_global_properties_follow_actual_registration_and_concrete_exposure_sets(self):
        self.assertTrue({"Base", "Child", "Alias", "Maker", "console"}.issubset(
            self.tree["Window"]["properties"]))
        worker = self.tree["DedicatedWorkerGlobalScope"]["properties"]
        self.assertIn("Base", worker)
        self.assertNotIn("Child", worker)
        self.assertNotIn("Maker", worker)

    def test_crosschecks_actual_cpp_emitted_by_the_pinned_patched_generator(self):
        actual = self.generated_inventory()
        checked = export.crosscheck(actual, self.descriptors, self.tree, self.codegen)
        self.assertEqual(checked["logical_entries"], actual["counts"]["logical_entries"])
        self.assertEqual(checked["generated_sites"], actual["counts"]["generated_sites"])
        self.assertGreater(checked["logical_entries"], 15)
        self.assertEqual(checked["binding_only_api_names"], [])
        operations = {(e["api"], e["op"], e["scope"]) for e in actual["entries"]}
        self.assertIn(("Child.constructor", "construct", "constructor"), operations)
        self.assertIn(("Child.Maker", "construct", "constructor"), operations)
        self.assertIn(("Child.check", "call", "static"), operations)
        self.assertIn(("Child.color", "get", "instance"), operations)
        self.assertIn(("Child.color", "set", "instance"), operations)
        self.assertIn(("PairsIterator.next", "call", "instance"), operations)

    def test_already_patched_provider_is_used_without_reapplying_the_trace_patch(self):
        source = Path(self.codegen.__file__)
        data = source.read_bytes()
        if export.git_blob(source) == self.baseline["files"]["dom/bindings/Codegen.py"]["before"]:
            data = transform("dom/bindings/Codegen.py", data)
        traced = self.root / "TracedCodegen.py"
        traced.write_bytes(data)
        with patch.object(self.codegen, "__file__", str(traced)):
            generator = self.traced_generator()
        generated = generator.CGBindingRoot(
            self.config, "ExampleBinding", str(self.webidl)).define()
        self.assertIn('camoufoxTrace(cx, args, "Child", "color")', generated)

    def test_unknown_hook_scope_member_and_duplicate_fail_instead_of_guessing(self):
        actual = self.generated_inventory()
        for field, value in (("member", "notAnIDLMember"), ("scope", "unknown"),
                             ("interface", "ChromeOnlyInvented")):
            modified = copy.deepcopy(actual)
            modified["entries"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Unmatched"):
                export.crosscheck(modified, self.descriptors, self.tree, self.codegen)
        modified = copy.deepcopy(actual)
        modified["entries"].append(modified["entries"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            export.crosscheck(modified, self.descriptors, self.tree, self.codegen)

    def test_unknown_provider_shapes_and_missing_parents_fail_loudly(self):
        with patch.object(self.codegen.PropertyArrays, "arrayNames", return_value=["futureArray"]):
            with self.assertRaisesRegex(ValueError, "PropertyArrays"):
                export.extract(self.config, self.codegen)

        with self.assertRaisesRegex(ValueError, "JSClass"):
            export._class_name('static const DifferentClass sPrototypeClass = {};', "sPrototypeClass")
        descriptors = self.config.descriptors
        with patch.object(self.config, "descriptors", [d for d in descriptors if d.name != "Base"]):
            with self.assertRaisesRegex(ValueError, "Unresolved Firefox parent"):
                export.extract(self.config, self.codegen)

    def test_provider_drift_requires_an_explicit_port(self):
        source = self.root / "changed-source"
        path = source / "dom/bindings/Configuration.py"
        path.parent.mkdir(parents=True)
        path.write_bytes((self.source / "dom/bindings/Configuration.py").read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "Unknown Firefox provider content"):
            export.verify_provider(source, self.baseline)

    def test_input_manifest_hashes_the_actual_selected_and_exported_webidl(self):
        source, objdir = self.root / "source", self.root / "obj"
        source.mkdir()
        (objdir / "dom/bindings").mkdir(parents=True)
        (source / "Base.webidl").write_text("interface Base {};\n")
        generated = objdir / "dom/bindings/Generated.webidl"
        generated.write_text("interface Generated {};\n")
        file_list = objdir / "dom/bindings/file-lists.json"
        content = {"webidls": [str(source / "Base.webidl"), str(generated)],
                   "exported_stems": ["Base", "Generated"],
                   "generated_events_stems": [], "example_interfaces": []}
        file_list.write_text(json.dumps(content))
        _, inputs = export.build_inputs(source, objdir)
        self.assertEqual({row["path"] for row in inputs},
                         {"source/Base.webidl", "objdir/dom/bindings/Generated.webidl"})
        self.assertEqual(next(row["sha256"] for row in inputs if row["path"].startswith("objdir/")),
                         export.sha256(generated))
        generated.unlink()
        with self.assertRaisesRegex(ValueError, "Missing exported WebIDL"):
            export.build_inputs(source, objdir)
        content["new_unknown_list"] = []
        file_list.write_text(json.dumps(content))
        with self.assertRaisesRegex(ValueError, "Unknown Firefox file-lists"):
            export.build_inputs(source, objdir)

    def test_missing_prerequisite_does_not_replace_an_existing_delivery(self):
        output = self.root / "idldata.json"
        output.write_text("existing delivery\n")
        arguments = ["--source", str(self.root / "missing-source"), "--objdir", str(self.root),
                     "--mozconfig", str(self.conf), "--binding-inventory", str(self.conf),
                     "--output", str(output)]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            export.main(arguments)
        self.assertEqual(failure.exception.code, 2)
        self.assertEqual(output.read_text(), "existing delivery\n")


if __name__ == "__main__":
    unittest.main()
