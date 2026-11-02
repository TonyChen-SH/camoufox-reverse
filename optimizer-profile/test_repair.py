import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import repair


class OptimizerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("VISIBLEV8_OPTIMIZER_SOURCE")
        if not configured:
            raise RuntimeError("VISIBLEV8_OPTIMIZER_SOURCE must contain the exact original patched V8 inputs")
        cls.original = {name: (Path(configured) / name).read_bytes() for name in repair.BEFORE}
        for name, data in cls.original.items():
            if repair.sha(data) != repair.BEFORE[name]:
                raise ValueError(f"Unknown optimizer test input: {name}")

    def copy_source(self, engine):
        for name, data in self.original.items():
            target = engine / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def test_original_reducers_and_loop_rewind_lack_instrumentation_guards(self):
        graph = self.original["src/maglev/maglev-graph-builder.cc"].decode()
        self.assertIn("return AddNewNode<CallKnownApiFunction>(", graph)
        self.assertIn("Builtin::kGetPropertyWithReceiver", graph)
        loop = graph.split("void MaglevGraphBuilder::BuildLoopForPeeling() {", 1)[1]
        loop = loop.split("void MaglevGraphBuilder::BeginLoopEffects", 1)[0]
        self.assertIn("iterator_.SetOffset(loop_header)", loop)
        self.assertNotIn("source_position_iterator_.RestoreState", loop)
        self.assertNotIn("reducer_.SetCurrentProvenance", loop)

    def test_exact_generation_preserves_original_paths_and_generated_diff_roundtrips(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            self.copy_source(engine)
            evidence = engine / "evidence"
            manifest = repair.apply(engine, evidence, shell_fixture=True)
            self.assertEqual(repair.verify(engine, evidence), manifest)
            self.assertFalse(manifest["unmodified_upstream_build"])
            self.assertFalse(manifest["wasm_disabled"])
            self.assertEqual(manifest["jit_tiers_disabled"], [])
            graph = (engine / "src/maglev/maglev-graph-builder.cc").read_text()
            self.assertIn("source_position_iterator_.RestoreState(start_source_positions)", graph)
            self.assertIn("reducer_.SetCurrentProvenance(start_provenance)", graph)
            self.assertIn("return AddNewNode<CallKnownApiFunction>(", graph)
            self.assertIn("Builtin::kGetPropertyWithReceiver", graph)
            self.assertEqual(graph.count("#ifdef VV8_TRACE_PROPERTIES"), 4)
            patch = evidence / "optimizer-profile.patch"
            expected = {name: (engine / name).read_bytes() for name in repair.BEFORE}
            subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=engine, check=True)
            subprocess.run(["git", "apply", "--reverse", str(patch)], cwd=engine, check=True)
            self.assertEqual({name: (engine / name).read_bytes() for name in repair.BEFORE}, self.original)
            subprocess.run(["git", "apply", str(patch)], cwd=engine, check=True)
            self.assertEqual({name: (engine / name).read_bytes() for name in repair.BEFORE}, expected)

    def test_unknown_input_is_rejected_before_any_source_or_evidence_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            self.copy_source(engine)
            changed = engine / "src/execution/isolate.cc"
            changed.write_bytes(changed.read_bytes() + b"\n")
            before = {name: (engine / name).read_bytes() for name in repair.BEFORE}
            with self.assertRaisesRegex(ValueError, "Unknown optimizer profile input"):
                repair.apply(engine, engine / "evidence", shell_fixture=True)
            self.assertEqual({name: (engine / name).read_bytes() for name in repair.BEFORE}, before)
            self.assertFalse((engine / "evidence").exists())

    def test_core_profile_does_not_change_shell_embedder(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            self.copy_source(engine)
            manifest = repair.apply(engine, engine / "evidence")
            self.assertIsNone(manifest["shell_embedder_profile"])
            self.assertNotIn("samples/shell.cc", manifest["files"])
            self.assertEqual((engine / "samples/shell.cc").read_bytes(), self.original["samples/shell.cc"])
            self.assertEqual(repair.verify(engine, engine / "evidence"), manifest)

    def test_verified_profile_rejects_source_and_review_diff_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            self.copy_source(engine)
            evidence = engine / "evidence"
            repair.apply(engine, evidence)
            changed = engine / "src/execution/isolate.cc"
            before = changed.read_bytes()
            changed.write_bytes(before + b"\n")
            with self.assertRaisesRegex(ValueError, "output changed"):
                repair.verify(engine, evidence)
            changed.write_bytes(before)
            patch = evidence / "optimizer-profile.patch"
            patch.write_bytes(patch.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "review diff changed"):
                repair.verify(engine, evidence)


if __name__ == "__main__":
    unittest.main()
