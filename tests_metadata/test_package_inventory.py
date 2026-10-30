import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "metadata"))
from package_inventory import add_file, archive, configured_build, snapshots


class ExportPackagingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_archive_preserves_input_bytes_and_records_reproducible_file_metadata(self):
        files = {}
        for name, content in (("source/dom/webidl/Example.webidl", b"interface Example {};\n"),
                              ("objdir/dom/bindings/Generated.webidl", "// Unicode: 测试\n".encode())):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            add_file(files, name, path)
        records = snapshots(files)
        first, second = self.root / "first.tar.gz", self.root / "second.tar.gz"
        archive(first, files, records)
        archive(second, files, records)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as stored:
            self.assertEqual(sorted(stored.getnames()), sorted(files))
            for member in stored.getmembers():
                self.assertEqual((member.mtime, member.uid, member.gid, member.mode), (0, 0, 0, 0o644))
                data = stored.extractfile(member).read()
                record = next(row for row in records if row["path"] == member.name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertEqual(data, files[member.name].read_bytes())

    def test_changed_input_does_not_replace_an_existing_archive(self):
        source = self.root / "Example.webidl"
        source.write_bytes(b"interface Example {};\n")
        files = {"source/Example.webidl": source}
        records = snapshots(files)
        target = self.root / "inputs.tar.gz"
        archive(target, files, records)
        previous = target.read_bytes()
        source.write_bytes(b"interface Different {};\n")
        with self.assertRaisesRegex(ValueError, "Input changed"):
            archive(target, files, records)
        self.assertEqual(target.read_bytes(), previous)
        self.assertFalse((self.root / "inputs.tar.gz.tmp").exists())

    def test_missing_files_symlinks_and_archive_traversal_fail(self):
        source = self.root / "Example.webidl"
        source.write_bytes(b"interface Example {};\n")
        link = self.root / "link.webidl"
        link.symlink_to(source)
        for name, path in (("source/Missing.webidl", self.root / "missing"),
                           ("source/Link.webidl", link), ("../outside", source)):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe or missing"):
                add_file({}, name, path)

    def configured_paths(self):
        source, objdir = self.root / "source", self.root / "objdir"
        source.mkdir()
        objdir.mkdir()
        mozconfig = source / "mozconfig"
        mozconfig.write_text("ac_add_options --target=x86_64-pc-linux-gnu\n")
        for group in ("substs", "defines"):
            directory = objdir / "config.statusd" / group
            directory.mkdir(parents=True)
            (directory / "SAVED_VALUE").write_text('"configured value"\n')
        saved = {"topsrcdir": str(source), "topobjdir": str(objdir),
                 "mozconfig": {"path": str(mozconfig),
                               "configure_args": ["--target=x86_64-pc-linux-gnu"]}}
        (objdir / ".mozconfig.json").write_text(json.dumps(saved))
        return source, objdir, mozconfig, saved

    def test_saved_configuration_must_match_the_actual_source_objdir_and_mozconfig(self):
        source, objdir, mozconfig, saved = self.configured_paths()
        self.assertEqual(configured_build(source, objdir, mozconfig), saved)
        other = source / "another-mozconfig"
        other.write_bytes(mozconfig.read_bytes())
        with self.assertRaisesRegex(ValueError, "belongs to another"):
            configured_build(source, objdir, other)
        saved["mozconfig"]["configure_args"] = ["--target=aarch64-unknown-linux-gnu"]
        (objdir / ".mozconfig.json").write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "target"):
            configured_build(source, objdir, mozconfig)

    def test_missing_expanded_configuration_is_an_error(self):
        source, objdir, mozconfig, _ = self.configured_paths()
        (objdir / "config.statusd/defines/SAVED_VALUE").unlink()
        with self.assertRaisesRegex(ValueError, "Missing expanded Firefox configuration"):
            configured_build(source, objdir, mozconfig)


if __name__ == "__main__":
    unittest.main()
