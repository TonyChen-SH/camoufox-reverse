#!/usr/bin/env python3
"""Verify and package an IDL export with its actual inputs and generated C++."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tarfile

from idl_inventory import build_inputs, sha256, verify_provider, write_json


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from binding_inventory import inventory


def configured_build(source, objdir, mozconfig):
    """Verify the original build controller's saved, expanded mozconfig."""
    saved = json.loads((objdir / ".mozconfig.json").read_text(encoding="utf-8"))
    if (not isinstance(saved, dict) or set(saved) != {"topsrcdir", "topobjdir", "mozconfig"}
            or not isinstance(saved["mozconfig"], dict)):
        raise ValueError("Unknown Firefox .mozconfig.json shape")
    expected = ((saved["topsrcdir"], source), (saved["topobjdir"], objdir),
                (saved["mozconfig"].get("path"), mozconfig))
    for actual, wanted in expected:
        if not isinstance(actual, str) or Path(actual).resolve() != wanted:
            raise ValueError("Saved Firefox configuration belongs to another source/objdir/mozconfig")
    args = saved["mozconfig"].get("configure_args")
    if not isinstance(args, list) or "--target=x86_64-pc-linux-gnu" not in args:
        raise ValueError("Configured target does not match the pinned Linux x86_64 build")
    for directory in (objdir / "config.statusd/substs", objdir / "config.statusd/defines"):
        if not directory.is_dir() or not any(path.is_file() for path in directory.iterdir()):
            raise ValueError(f"Missing expanded Firefox configuration: {directory}")
    return saved


def add_file(files, name, path):
    name, path = str(name), Path(path)
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith("/") or ".." in parts or "\\" in name
            or path.is_symlink() or not path.is_file()):
        raise ValueError(f"Unsafe or missing provenance file: {name}")
    if name in files and files[name] != path:
        raise ValueError(f"Conflicting provenance file: {name}")
    files[name] = path


def snapshots(files):
    return [{"path": name, "sha256": sha256(path)} for name, path in sorted(files.items())]


def archive(path, files, records):
    expected = {row["path"]: row["sha256"] for row in records}
    if set(files) != set(expected):
        raise ValueError("Archive file list differs from provenance")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as output:
                    for name, source in sorted(files.items()):
                        data = source.read_bytes()
                        if hashlib.sha256(data).hexdigest() != expected[name]:
                            raise ValueError(f"Input changed while archiving: {name}")
                        info = tarfile.TarInfo(name)
                        info.size, info.mode, info.mtime = len(data), 0o644, 0
                        output.addfile(info, io.BytesIO(data))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def revision(repository):
    return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"],
                                   text=True).strip()


def package(source, objdir, mozconfig, upstream, output):
    source, objdir, mozconfig = source.resolve(), objdir.resolve(), mozconfig.resolve()
    upstream, output = upstream.resolve(), output.resolve()
    baseline = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))
    upstream_commit = revision(upstream)
    if upstream_commit != baseline["camoufox"]["commit"]:
        raise ValueError("Camoufox checkout does not match the browser baseline")
    configured_build(source, objdir, mozconfig)
    manifest_path = output / "idldata-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("coverage") != "configured-firefox-webidl-declarations":
        raise ValueError("Unknown IDL manifest shape")
    if (manifest["firefox_commit"] != baseline["firefox"]["commit"]
            or manifest["camoufox_commit"] != upstream_commit):
        raise ValueError("IDL export belongs to a different browser baseline")
    if manifest["provider_git_blobs"] != verify_provider(source, baseline):
        raise ValueError("Firefox provider changed after IDL export")
    file_list, inputs = build_inputs(source, objdir)
    if manifest["inputs"] != inputs:
        raise ValueError("Selected WebIDL inputs changed after IDL export")
    configuration = {"mozconfig_sha256": sha256(mozconfig),
                     "config_status_sha256": sha256(objdir / "config.status"),
                     "bindings_conf_sha256": sha256(source / "dom/bindings/Bindings.conf"),
                     "file_lists_sha256": sha256(file_list)}
    if manifest["configuration"] != configuration:
        raise ValueError("Build configuration changed after IDL export")
    if manifest["idldata_sha256"] != sha256(output / "idldata.json"):
        raise ValueError("IDL output does not match its export manifest")
    binding_path = output / "binding-inventory.json"
    bindings = json.loads(binding_path.read_text(encoding="utf-8"))
    if (manifest["binding_inventory_sha256"] != sha256(binding_path)
            or bindings != inventory(objdir / "dom/bindings")):
        raise ValueError("Binding inventory does not match the actual generated C++")
    if (manifest["crosscheck"]["logical_entries"] != bindings["counts"]["logical_entries"]
            or manifest["crosscheck"]["generated_sites"] != bindings["counts"]["generated_sites"]):
        raise ValueError("Binding inventory differs from the IDL cross-check")

    files = {}
    for row in inputs:
        label, relative = row["path"].split("/", 1)
        add_file(files, row["path"], {"source": source, "objdir": objdir}[label] / relative)
    for relative in (*manifest["provider_git_blobs"], "dom/bindings/Bindings.conf",
                     "dom/bindings/Makefile.in",
                     "xpcom/ds/tools/perfecthash.py", "third_party/python/ply/ply/__init__.py",
                     "third_party/python/ply/ply/lex.py", "third_party/python/ply/ply/yacc.py"):
        add_file(files, f"source/{relative}", source / relative)
    add_file(files, "configuration/mozconfig", mozconfig)
    for relative in (".mozconfig.json", "config.status", "dom/bindings/file-lists.json"):
        add_file(files, f"objdir/{relative}", objdir / relative)
    for path in sorted((objdir / "config.statusd").rglob("*")):
        if path.is_file() or path.is_symlink():
            add_file(files, f"objdir/{path.relative_to(objdir).as_posix()}", path)
    for relative in ("baseline.json", "patches/native-api-trace.patch", "tools/apply.py",
                     "tools/generate_patch.py", "tools/binding_inventory.py",
                     "metadata/idl_inventory.py", "metadata/package_inventory.py",
                     ".github/workflows/firefox-idl-inventory.yml"):
        add_file(files, f"reverse/{relative}", ROOT / relative)
    for relative in ("ci/run_prepare.py", "browser/scripts/patch.py", "browser/scripts/_mixin.py",
                     "browser/assets/base.mozconfig",
                     "browser/assets/linux.mozconfig", "browser/upstream.sh"):
        add_file(files, f"upstream/{relative}", upstream / relative)
    generated = {}
    for path in sorted((objdir / "dom/bindings").rglob("*.cpp")):
        add_file(generated, f"objdir/{path.relative_to(objdir).as_posix()}", path)
    if len(generated) != bindings["counts"]["cpp_files_scanned"]:
        raise ValueError("Generated C++ file count differs from the binding scan")
    input_records, generated_records = snapshots(files), snapshots(generated)
    archive(output / "metadata-inputs.tar.gz", files, input_records)
    archive(output / "generated-bindings.tar.gz", generated, generated_records)
    source_archive = upstream / "browser" / f"firefox-{baseline['firefox']['version']}.source.tar.xz"
    provenance = {
        "schema": 1, "kind": "firefox-configure-export-only",
        "reverse_commit": revision(ROOT), "camoufox_commit": upstream_commit,
        "firefox_baseline_commit": baseline["firefox"]["commit"],
        "baseline_sha256": sha256(ROOT / "baseline.json"),
        "source_archive": {"name": source_archive.name, "sha256": sha256(source_archive)},
        "configured_target": "linux,x86_64", "configuration": configuration,
        "python": sys.version, "platform": platform.platform(),
        "idl_manifest_sha256": sha256(manifest_path),
        "metadata_inputs": input_records, "generated_bindings": generated_records,
        "commands": [
            {"cwd": str(upstream), "argv": ["python3", "-m", "ci.run_prepare"]},
            {"cwd": str(ROOT), "argv": ["python3", "tools/apply.py", "--source", str(source)]},
            {"cwd": str(source), "argv": ["./mach", "configure"]},
            {"cwd": str(source), "argv": ["./mach", "build", "export"]},
            {"cwd": str(ROOT), "argv": ["python3", "tools/binding_inventory.py", "--root",
             str(objdir / "dom/bindings"), "--output", str(binding_path)]},
            {"cwd": str(source), "argv": ["./mach", "python", "--virtualenv", "build",
             str(ROOT / "metadata/idl_inventory.py"), "--source", str(source), "--objdir", str(objdir),
             "--mozconfig", str(mozconfig), "--binding-inventory", str(binding_path),
             "--output", str(output / "idldata.json")]},
        ],
        "github": {name: os.environ[name] for name in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_REF",
                   "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "ImageOS", "ImageVersion") if name in os.environ},
    }
    write_json(output / "export-provenance.json", provenance)
    for name in ("baseline.json", "LICENSE"):
        (output / name).write_bytes((ROOT / name).read_bytes())
    names = ["LICENSE", "baseline.json", "binding-inventory.json", "export-provenance.json",
             "generated-bindings.tar.gz", "idldata-manifest.json", "idldata.json", "metadata-inputs.tar.gz"]
    sums = "".join(f"{sha256(output / name)}  {name}\n" for name in names)
    (output / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return {"input_files": len(files), "generated_cpp_files": len(generated),
            "logical_entries": bindings["counts"]["logical_entries"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "objdir", "mozconfig", "upstream", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = package(args.source, args.objdir, args.mozconfig, args.upstream, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
