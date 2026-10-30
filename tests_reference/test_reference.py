import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
import build_visiblev8_reference as builder
import reference_conformance as conformance


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


if __name__ == "__main__":
    unittest.main()
