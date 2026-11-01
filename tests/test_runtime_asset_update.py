import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import update_runtime_asset as updater
from generate_patch import git_blob, transform


class RuntimeAssetUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        self.package.mkdir()
        self.target = self.root / "target"
        self.target.mkdir()
        shutil.copyfile(ROOT / "baseline.json", self.target / "baseline.json")
        shutil.copytree(ROOT / "patches", self.target / "patches")
        shutil.copytree(ROOT / "additions", self.target / "additions")
        self.source = Path(os.environ["FIREFOX_SOURCE"])
        self.manifest = json.loads((self.target / "baseline.json").read_text())
        compiled = copy.deepcopy(self.manifest)
        del compiled["files"][updater.SOURCE_ASSET]
        compiled["patch_sha256"] = "a" * 64
        self.baseline = self.package / "baseline.json"
        self.baseline.write_text(json.dumps(compiled))
        self.asset = self.package / updater.RUNTIME_ASSET
        self.asset.parent.mkdir(parents=True)
        self.asset.write_bytes((self.source / updater.SOURCE_ASSET).read_bytes())
        self.asset.chmod(0o640)
        for path in updater.CORE_FILES:
            destination = self.package / path
            destination.write_bytes((path + " fixed compiled core").encode())
        (self.package / updater.CORE_FILES[0]).chmod(0o755)
        self.identity = {
            "artifact": {"id": 12, "digest": "sha256:" + "b" * 64,
                         "workflow_run": {"id": 34, "head_sha": "c" * 40}},
            "baseline_sha256": updater.sha256(self.baseline),
            "core_sha256": {path: updater.sha256(self.package / path) for path in updater.CORE_FILES},
        }
        self.receipt = self.root / "composition.json"
        self.commit = "d" * 40
        self.patch_root = patch.object(updater, "ROOT", self.target)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def update(self, receipt=None):
        return updater.update(self.package, self.source, self.identity,
                              self.commit, receipt or self.receipt)

    def reject_unchanged(self, reason):
        before = updater.inventory(self.package)
        with self.assertRaisesRegex((ValueError, OSError), reason):
            self.update()
        self.assertEqual(updater.inventory(self.package), before)
        self.assertFalse(self.receipt.exists())

    def target_manifest(self, mutate):
        changed = copy.deepcopy(self.manifest)
        mutate(changed)
        (self.target / "baseline.json").write_text(json.dumps(changed))

    def test_updates_only_generated_asset_and_preserves_compiled_baseline(self):
        before = updater.inventory(self.package)
        original_baseline = self.baseline.read_bytes()
        result = self.update()
        after = updater.inventory(self.package)
        changed = [path for path in before if before[path] != after[path]]
        self.assertEqual(changed, [updater.RUNTIME_ASSET])
        self.assertEqual(self.baseline.read_bytes(), original_baseline)
        self.assertEqual(self.asset.read_bytes(), transform(updater.SOURCE_ASSET,
                                                          (self.source / updater.SOURCE_ASSET).read_bytes()))
        self.assertEqual(after[updater.RUNTIME_ASSET]["mode"], 0o640)
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.assertEqual(result["unchanged_core_sha256"], self.identity["core_sha256"])
        self.assertEqual(result["target_source_commit"], self.commit)
        self.assertEqual(result["acceptance"], "not_run")
        self.assertEqual(result["asset"]["after_git_blob"], git_blob(self.asset.read_bytes()))

    def test_repeated_application_never_changes_the_package_or_receipt(self):
        self.update()
        before = updater.inventory(self.package)
        receipt = self.receipt.read_bytes()
        with self.assertRaisesRegex(FileExistsError, "already exists"):
            self.update()
        with self.assertRaisesRegex(ValueError, "compiled baseline"):
            self.update(self.root / "another-receipt.json")
        self.assertEqual(updater.inventory(self.package), before)
        self.assertEqual(self.receipt.read_bytes(), receipt)

    def test_verifies_unpacked_composition_and_rejects_modified_files(self):
        self.update()
        unpacked = self.root / "unpacked"
        shutil.copytree(self.package, unpacked)
        updater.verify_composition(unpacked, self.receipt, self.target / "baseline.json")
        (unpacked / updater.CORE_FILES[0]).write_bytes(b"changed after unpack")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            updater.verify_composition(unpacked, self.receipt, self.target / "baseline.json")

    def test_rejects_changed_native_source_hashes_overlays_or_upstream_pins(self):
        mutations = [
            lambda value: value["files"]["js/src/vm/Interpreter.cpp"].update(after="e" * 40),
            lambda value: value["files"]["js/src/vm/Interpreter.cpp"].update(before="e" * 40),
            lambda value: value["additions"].update({"js/src/vm/CamoufoxMonitor.cpp": "e" * 64}),
            lambda value: value["firefox"].update(commit="e" * 40),
            lambda value: value["camoufox"].update(commit="e" * 40),
            lambda value: value["files"].update({"juggler/Other.js": {"before": "e" * 40, "after": "f" * 40}}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.target_manifest(mutate)
                self.reject_unchanged("outside the allowed runtime asset")

    def test_rejects_tampered_compiled_baseline(self):
        self.baseline.write_bytes(self.baseline.read_bytes() + b"\n")
        self.reject_unchanged("Compiled baseline hash")

    def test_rejects_tampered_compiled_core(self):
        (self.package / updater.CORE_FILES[1]).write_bytes(b"unrecognized core")
        self.reject_unchanged("core hash differs")

    def test_rejects_tampered_runtime_asset(self):
        self.asset.write_bytes(b"unrecognized runtime asset")
        self.reject_unchanged("Runtime asset differs")

    def test_rejects_missing_or_packed_asset(self):
        self.asset.unlink()
        (self.package / "bin/omni.ja").write_bytes(b"unsupported packed asset")
        self.reject_unchanged("packed assets are unsupported")

    def test_rejects_symlink_asset_without_writing_its_target(self):
        other = self.root / "outside.js"
        self.asset.rename(other)
        original = other.read_bytes()
        self.asset.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Unsupported package entry"):
            self.update()
        self.assertEqual(other.read_bytes(), original)
        self.assertTrue(self.asset.is_symlink())
        self.assertFalse(self.receipt.exists())

    def test_rejects_ambiguous_packed_and_loose_copies(self):
        with zipfile.ZipFile(self.package / "bin/omni.ja", "w") as archive:
            archive.writestr(updater.RUNTIME_ASSET.removeprefix("bin/"), self.asset.read_bytes())
        self.reject_unchanged("packed archive")

    def test_rolls_back_asset_if_receipt_cannot_be_published(self):
        before = updater.inventory(self.package)
        def fail_publication(*args):
            self.assertEqual(git_blob(self.asset.read_bytes()),
                             self.manifest["files"][updater.SOURCE_ASSET]["after"])
            raise OSError("receipt publication failed")
        with patch.object(updater.os, "link", side_effect=fail_publication):
            with self.assertRaisesRegex(OSError, "receipt publication failed"):
                self.update()
        self.assertEqual(updater.inventory(self.package), before)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(set(self.root.iterdir()), {self.package, self.target})

    def test_verifies_every_package_entry_after_replacement(self):
        original_inventory = updater.inventory
        calls = 0
        def changed_inventory(package):
            nonlocal calls
            calls += 1
            result = original_inventory(package)
            if calls == 3:
                result["unexpected-file"] = {"kind": "file", "sha256": "e" * 64}
            return result
        before = original_inventory(self.package)
        with patch.object(updater, "inventory", side_effect=changed_inventory):
            with self.assertRaisesRegex(ValueError, "outside the allowed runtime asset"):
                self.update()
        self.assertEqual(original_inventory(self.package), before)
        self.assertFalse(self.receipt.exists())

    def test_rejects_incorrect_generated_target_hash(self):
        self.target_manifest(lambda value: value["files"][updater.SOURCE_ASSET].update(after="e" * 40))
        self.reject_unchanged("Generated runtime asset hash")

    def test_rejects_tampered_pinned_asset_source(self):
        self.source = self.root / "unknown-source"
        source = self.source / updater.SOURCE_ASSET
        source.parent.mkdir(parents=True)
        source.write_bytes(b"unrecognized source")
        self.reject_unchanged("source hash differs from the pinned upstream")

    def test_rejects_tampered_generated_patch(self):
        (self.target / "patches/native-api-trace.patch").write_bytes(b"unrecognized patch")
        self.reject_unchanged("Generated target patch")

    def test_rejects_tampered_native_overlay_even_when_manifest_is_unchanged(self):
        (self.target / "additions/js/src/vm/CamoufoxMonitor.cpp").write_bytes(b"unrecognized overlay")
        self.reject_unchanged("native overlay hash")

    def test_receipt_must_not_modify_the_original_package(self):
        with self.assertRaisesRegex(ValueError, "outside the original package"):
            self.update(self.package / "composition.json")
        self.assertEqual(git_blob(self.asset.read_bytes()),
                         self.manifest["files"][updater.SOURCE_ASSET]["before"])


if __name__ == "__main__":
    unittest.main()
