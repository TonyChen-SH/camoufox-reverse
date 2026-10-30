import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
import build_visiblev8_reference as builder
import reference_conformance as conformance
import engine_build_compat as compatibility


class ReferenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("VISIBLEV8_REFERENCE_SOURCE")
        if not configured:
            raise RuntimeError("VISIBLEV8_REFERENCE_SOURCE must point to the pinned original checkout")
        cls.reference = Path(configured).resolve()
        cls.lock = json.loads(builder.LOCK.read_text())

    def test_reference_source_and_patch_pin(self):
        builder.verify_reference(self.reference, self.lock)
        changed = dict(self.lock, v8_patch_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            builder.verify_reference(self.reference, changed)

    def test_controlled_wire_fixture_has_real_context_and_eval_parent(self):
        parser = conformance.module(self.reference, "vlp")
        with tempfile.TemporaryDirectory() as temporary:
            path, sources = conformance.fixture(Path(temporary))
            records = [(line[0], parser.parse_raw_fields(line[1:]))
                       for line in path.read_text().splitlines()]
            origins = [fields for op, fields in records if op == "@"]
            self.assertEqual(origins, [['"https://example.test/index.html"', '"https://example.test"']])
            scripts = {int(fields[0]): fields for op, fields in records if op == "$"}
            self.assertEqual(scripts[3][1], "1")
            self.assertEqual({key: fields[2] for key, fields in scripts.items()}, sources)
            call = next(fields for op, fields in records if op == "c")
            self.assertEqual(call[1:], ["%createElement", "{1,HTMLDocument}", '"DIV"'])
            idl = json.loads((Path(temporary) / "idldata.json").read_text())
            self.assertIn("createElement", idl["HTMLDocument"]["methods"])

    def test_feature_oracles_reject_vacuous_or_wrong_results(self):
        with self.assertRaises(AssertionError):
            conformance.assert_stdout("features", [], {})
        with self.assertRaises(AssertionError):
            conformance.assert_stdout("ufeatures", [{"allFeatures": []}], {})
        with self.assertRaises(AssertionError):
            conformance.assert_stdout("Mfeatures", [["mega_instance", {}]] * 3, {})
        with self.assertRaises(AssertionError):
            conformance.assert_stdout("create_element", [["create_element", {
                "tag_name": "div", "create_count": 0}]], {})

    def test_prepare_refuses_unowned_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sentinel = directory / "user-data"
            sentinel.write_bytes(b"preserve this file")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "unowned"):
                builder.prepare(self.reference, directory, "v8-shell", directory, self.lock)
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ["user-data"])

    def patched_engine(self, directory):
        header = directory / "src/runtime/runtime-utils.h"
        header.parent.mkdir(parents=True)
        data = (ROOT / "reference/fixtures/runtime-utils.pristine.h").read_bytes()
        self.assertEqual(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                         "345e326eed8d564e252373ec3ea714a2c3ea03d0")
        header.write_bytes(data)
        subprocess.run(["git", "apply", "--include=src/runtime/runtime-utils.h",
                        "--include=src/runtime/runtime-utils.cc",
                        str(self.reference / self.lock["v8_patch"])], cwd=directory, check=True)
        return header, directory / "src/runtime/runtime-utils.cc"

    def test_build_compatibility_uses_the_exact_patch_and_preserves_logger_bodies(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            header, source = self.patched_engine(engine)
            original_body = source.read_text().split("namespace v8::internal {", 1)[1]
            original_declarations = header.read_text().split("namespace v8 {", 1)[1]
            evidence = engine / "evidence"
            manifest = compatibility.apply(engine, evidence, self.lock["v8_commit"])
            self.assertEqual(manifest["profile"], "visiblev8-linux-build-compat.1")
            self.assertEqual(source.read_text().split("namespace v8::internal {", 1)[1], original_body)
            self.assertEqual(header.read_text().split("namespace v8 {", 1)[1], original_declarations)
            self.assertNotIn("-inl.h", header.read_text())
            self.assertIn('#include "src/api/api-inl.h"', source.read_text())
            self.assertEqual(compatibility.verify(engine, evidence, self.lock["v8_commit"]), manifest)
            source.write_text(source.read_text() + "// changed after generation\n")
            with self.assertRaisesRegex(ValueError, "output changed"):
                compatibility.verify(engine, evidence, self.lock["v8_commit"])

    def test_build_compatibility_refuses_unknown_input_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            header, source = self.patched_engine(engine)
            header.write_text(header.read_text() + "// different source\n")
            before = {p: p.read_bytes() for p in (header, source)}
            evidence = engine / "evidence"
            with self.assertRaisesRegex(ValueError, "Unknown.*input"):
                compatibility.apply(engine, evidence, self.lock["v8_commit"])
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            self.assertFalse(evidence.exists())

    def test_pinned_bootstrap_makes_the_real_python_wrapper_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            depot = Path(temporary) / "depot_tools"
            depot.mkdir()
            logs = Path(temporary) / "logs"
            logs.mkdir()
            wrapper = depot / "python-bin/python3"
            wrapper.parent.mkdir()
            source = ROOT / "reference/fixtures/depot_python3"
            data = source.read_bytes()
            self.assertEqual(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                             "33016e32126b83518243094d3c40183f786214d7")
            shutil.copyfile(source, wrapper)
            wrapper.chmod(0o755)
            failed = subprocess.run([str(wrapper), "--version"], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("python3_bin_reldir.txt not found", failed.stderr)
            # This isolated provider fixture stands in for CIPD provisioning.
            # The production driver must invoke the provider; it never writes its marker.
            bootstrap = depot / "ensure_bootstrap"
            bootstrap.write_text("#!/usr/bin/env python3\n"
                "import os,pathlib,sys\n"
                "p=pathlib.Path(__file__).parent\n"
                "assert os.environ['DEPOT_TOOLS_BOOTSTRAP_PYTHON3']=='1'\n"
                "assert pathlib.Path(os.environ['DEPOT_TOOLS_DIR'])==p\n"
                "b=p/'fixture-runtime/bin';b.mkdir(parents=True,exist_ok=True)\n"
                "(b/'python3').symlink_to(sys.executable)\n"
                "(p/'python3_bin_reldir.txt').write_text('fixture-runtime/bin\\n')\n")
            bootstrap.chmod(0o755)
            subprocess.run(["git", "init", "-q", str(depot)], check=True)
            subprocess.run(["git", "-C", str(depot), "add", "."], check=True)
            subprocess.run(["git", "-C", str(depot), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
            revision = builder.head(depot)
            environment = dict(os.environ, DEPOT_TOOLS_UPDATE="0", DEPOT_TOOLS_BOOTSTRAP_PYTHON3="0")
            builder.bootstrap_depot(depot, logs, environment, revision)
            usable = subprocess.run([str(wrapper), "--version"], capture_output=True, text=True)
            self.assertEqual(usable.returncode, 0)
            self.assertTrue(usable.stdout.startswith("Python "))
            self.assertEqual(builder.head(depot), revision)


if __name__ == "__main__":
    unittest.main()
