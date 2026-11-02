import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import repair
import conformance


class RepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("VISIBLEV8_REFERENCE_SOURCE")
        if not configured:
            raise RuntimeError("VISIBLEV8_REFERENCE_SOURCE must point to the pinned original checkout")
        cls.reference = Path(configured).resolve()

    def test_generated_repairs_are_reviewable_and_original_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "repaired"
            manifest = repair.create(self.reference, output)
            self.assertEqual(set(manifest["repairs"]), set(repair.PATHS))
            for path, hashes in manifest["repairs"].items():
                original = subprocess.check_output(["git", "-C", str(self.reference), "show", "HEAD:" + path])
                self.assertEqual(hashlib.sha256(original).hexdigest(), hashes["before_sha256"])
                self.assertEqual(hashlib.sha256((output / path).read_bytes()).hexdigest(), hashes["after_sha256"])
                self.assertNotEqual(hashes["before_sha256"], hashes["after_sha256"])
            self.assertIn("script_causality_graphml", (output / "repairs.patch").read_text())
            self.assertEqual((output / "LICENSE.visiblev8").read_bytes(),
                             (self.reference / "LICENSE").read_bytes())
            subprocess.run(["git", "-C", str(self.reference), "diff", "--exit-code", "HEAD", "--", "post-processor"],
                           check=True, stdout=subprocess.DEVNULL)

    def test_pristine_test_copy_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "original"
            manifest = repair.create(self.reference, output, repaired=False)
            self.assertEqual(manifest["repairs"], {})
            for path in repair.PATHS:
                self.assertEqual((output / path).read_bytes(), (self.reference / path).read_bytes())
            self.assertEqual((output / "repairs.patch").read_text(), "")

    def test_real_document_fixture_and_synthetic_attempts_are_identified(self):
        fixtures = Path(__file__).resolve().parent / "fixtures"
        provenance = json.loads((fixtures / "firefox158-document-idl.provenance.json").read_text())
        self.assertEqual(provenance["source_full_idldata_sha256"],
                         "a879bbd7f685a3a480f0da7cb183e3061cd81830c84327b808a9e623e24cc7df")
        metadata = fixtures / provenance["projection_file"]
        self.assertEqual(hashlib.sha256(metadata.read_bytes()).hexdigest(),
                         "628128dc040413d7500003acf3984d0d84ede48aeee6b0e7b314d417e72e6cd5")
        tree = json.loads(metadata.read_text())
        self.assertEqual(tree["HTMLDocument"]["parent"], "Document")
        self.assertNotIn("createElement", tree["HTMLDocument"]["methods"])
        self.assertIn("createElement", tree["Document"]["methods"])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "fixture"
            _, actual_provenance = conformance.element_fixture(output)
            self.assertEqual(actual_provenance, provenance)
            lines = (output / "vv8-inherited-1-1-main.0.log").read_text().splitlines()
            self.assertIn("c62:%createElement:{1,HTMLDocument}", lines)
            self.assertEqual(sum(line.startswith("c") for line in lines), 7)

    def test_existing_output_and_unknown_repair_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinel = output / "preserve"
            sentinel.write_text("user data")
            with self.assertRaisesRegex(ValueError, "new directory"):
                repair.create(self.reference, output)
            self.assertEqual(sentinel.read_text(), "user data")
        for path in repair.PATHS:
            with self.subTest(path=path), self.assertRaises(ValueError):
                repair.transform(path, "not the pinned source")


if __name__ == "__main__":
    unittest.main()
