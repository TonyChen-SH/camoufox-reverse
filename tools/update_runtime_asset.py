#!/usr/bin/env python3
"""Compose one generated Juggler asset with an unchanged compiled browser."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

from apply import replace_file
from generate_patch import ROOT, git_blob, render, transform

SOURCE_ASSET = "juggler/NetworkObserver.js"
RUNTIME_ASSET = "bin/modules/juggler/NetworkObserver.js"
CORE_FILES = ("bin/camoufox-bin", "bin/libxul.so")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(package):
    result = {}
    for path in sorted(package.rglob("*")):
        info = path.lstat()
        relative = path.relative_to(package).as_posix()
        if stat.S_ISLNK(info.st_mode) or not (path.is_dir() or path.is_file()):
            raise ValueError(f"Unsupported package entry: {relative}")
        entry = {"mode": stat.S_IMODE(info.st_mode), "kind": "directory"}
        if path.is_file():
            entry.update(kind="file", size=info.st_size, sha256=sha256(path))
        result[relative] = entry
    return result


def digest_inventory(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def write_receipt(path, result):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as destination:
        staging = Path(destination.name)
        try:
            json.dump(result, destination, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
            os.link(staging, path)
        finally:
            staging.unlink(missing_ok=True)


def validate_identity(identity):
    artifact = identity.get("artifact", {})
    workflow = artifact.get("workflow_run", {})
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
            type(workflow.get("id")) is not int or workflow["id"] <= 0 or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")) or
            not re.fullmatch(r"[0-9a-f]{40}", workflow.get("head_sha", ""))):
        raise ValueError("Invalid base artifact identity")
    hashes = identity.get("core_sha256", {})
    if (set(hashes) != set(CORE_FILES) or
            any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()) or
            not re.fullmatch(r"[0-9a-f]{64}", identity.get("baseline_sha256", ""))):
        raise ValueError("Invalid base baseline/core hashes")
    return {"artifact": {"id": artifact["id"], "digest": artifact["digest"],
                         "workflow_run": {"id": workflow["id"], "head_sha": workflow["head_sha"]}},
            "baseline_sha256": identity["baseline_sha256"], "core_sha256": hashes}


def compare_baselines(compiled, target):
    if compiled.get("schema") != 1 or target.get("schema") != 1:
        raise ValueError("Unsupported baseline schema")
    for baseline in (compiled, target):
        if not isinstance(baseline.get("files"), dict) or not isinstance(baseline.get("additions"), dict):
            raise ValueError("Invalid baseline file manifest")
    def native(baseline):
        result = dict(baseline)
        result.pop("patch_sha256", None)
        result["files"] = {key: value for key, value in baseline["files"].items() if key != SOURCE_ASSET}
        return result
    if native(compiled) != native(target):
        raise ValueError("Compiled baseline differs outside the allowed runtime asset")
    entry = target["files"].get(SOURCE_ASSET)
    if not isinstance(entry, dict) or set(entry) != {"before", "after"}:
        raise ValueError("Target baseline lacks the allowed asset hashes")
    old = compiled["files"].get(SOURCE_ASSET)
    if old is not None and (set(old) != {"before", "after"} or old["before"] != entry["before"]):
        raise ValueError("Runtime asset upstream baseline differs")
    return old["after"] if old is not None else entry["before"]


def update(package, source, identity, target_commit, receipt):
    """The caller authenticates the downloaded artifact and its extraction first."""
    package, source, receipt = Path(package), Path(source), Path(receipt)
    if package.is_symlink() or not package.is_dir():
        raise ValueError("Package must be a real directory")
    package = package.resolve(strict=True)
    receipt = receipt.absolute()
    if receipt.exists() or receipt.is_symlink():
        raise FileExistsError("Composition receipt already exists; refusing repeated application")
    if receipt.resolve().is_relative_to(package):
        raise ValueError("Composition receipt must be outside the original package")
    if not receipt.parent.is_dir():
        raise ValueError("Composition receipt directory does not exist")
    if not re.fullmatch(r"[0-9a-f]{40}", target_commit):
        raise ValueError("Target source commit must be a full commit hash")
    identity = validate_identity(identity)
    before = inventory(package)
    baseline_path = package / "baseline.json"
    if sha256(baseline_path) != identity["baseline_sha256"]:
        raise ValueError("Compiled baseline hash differs from authenticated identity")
    for path, expected in identity["core_sha256"].items():
        if before.get(path, {}).get("sha256") != expected:
            raise ValueError(f"Compiled browser core hash differs: {path}")
    compiled = json.loads(baseline_path.read_text())
    target_path = ROOT / "baseline.json"
    target = json.loads(target_path.read_text())
    expected_before = compare_baselines(compiled, target)
    asset = package / RUNTIME_ASSET
    if before.get(RUNTIME_ASSET, {}).get("kind") != "file":
        raise ValueError("Expected loose runtime asset is missing; packed assets are unsupported")
    for archive in package.rglob("*.ja"):
        try:
            with zipfile.ZipFile(archive) as packed:
                if RUNTIME_ASSET.removeprefix("bin/") in packed.namelist():
                    raise ValueError("Runtime asset also exists in a packed archive; loading is ambiguous")
        except zipfile.BadZipFile as error:
            raise ValueError(f"Unsupported package archive: {archive.relative_to(package)}") from error
    original = asset.read_bytes()
    if git_blob(original) != expected_before:
        raise ValueError("Runtime asset differs from the compiled baseline; refusing repeated application")
    pristine = (source / SOURCE_ASSET).read_bytes()
    hashes = target["files"][SOURCE_ASSET]
    if git_blob(pristine) != hashes["before"]:
        raise ValueError("Runtime asset source hash differs from the pinned upstream")
    generated = transform(SOURCE_ASSET, pristine)
    if git_blob(generated) != hashes["after"]:
        raise ValueError("Generated runtime asset hash differs from target baseline")
    patch = render(source, target).encode()
    if (hashlib.sha256(patch).hexdigest() != target["patch_sha256"] or
            patch != (ROOT / "patches/native-api-trace.patch").read_bytes()):
        raise ValueError("Generated target patch differs from its committed baseline")
    for path, expected in target["additions"].items():
        if sha256(ROOT / "additions" / path) != expected:
            raise ValueError(f"Target native overlay hash differs: {path}")
    if generated == original:
        raise ValueError("Runtime asset already matches target; no update to apply")
    if inventory(package) != before:
        raise ValueError("Package changed during validation")
    try:
        replace_file(asset, generated)
        after = inventory(package)
        changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        if changed != {RUNTIME_ASSET} or after[RUNTIME_ASSET]["mode"] != before[RUNTIME_ASSET]["mode"]:
            raise ValueError("Package changed outside the allowed runtime asset")
        result = {"schema": 1, "composition": "generated-runtime-asset", "acceptance": "not_run",
                  "base_identity": identity, "native_compilation": "reused_without_changes",
                  "identity_authority": "caller-authenticated artifact and extraction",
                  "target_source_commit": target_commit,
                  "target_baseline_sha256": sha256(target_path),
                  "compiled_baseline_preserved": True,
                  "asset": {"source": SOURCE_ASSET, "runtime": RUNTIME_ASSET,
                            "before_git_blob": git_blob(original), "after_git_blob": git_blob(generated),
                            "before_sha256": before[RUNTIME_ASSET]["sha256"],
                            "after_sha256": after[RUNTIME_ASSET]["sha256"]},
                  "package_inventory_before_sha256": digest_inventory(before),
                  "package_inventory_after_sha256": digest_inventory(after),
                  "unchanged_core_sha256": {path: after[path]["sha256"] for path in CORE_FILES}}
        write_receipt(receipt, result)
        return result
    except BaseException:
        replace_file(asset, original)
        raise


def verify_composition(package, receipt, target_baseline):
    package, receipt, target_baseline = Path(package), Path(receipt), Path(target_baseline)
    result = json.loads(receipt.read_text())
    if result.get("schema") != 1 or result.get("composition") != "generated-runtime-asset":
        raise ValueError("Unsupported composition receipt")
    identity = validate_identity(result["base_identity"])
    observed = inventory(package)
    if digest_inventory(observed) != result["package_inventory_after_sha256"]:
        raise ValueError("Unpacked package inventory differs from composition receipt")
    if sha256(package / "baseline.json") != identity["baseline_sha256"]:
        raise ValueError("Original compiled baseline was not preserved")
    if sha256(target_baseline) != result["target_baseline_sha256"]:
        raise ValueError("Target runtime baseline differs from composition receipt")
    compiled = json.loads((package / "baseline.json").read_text())
    target = json.loads(target_baseline.read_text())
    compare_baselines(compiled, target)
    for path, expected in identity["core_sha256"].items():
        if observed[path]["sha256"] != expected:
            raise ValueError("Unpacked compiled core differs from base artifact")
    asset = result["asset"]
    if (asset["source"] != SOURCE_ASSET or asset["runtime"] != RUNTIME_ASSET or
            observed[RUNTIME_ASSET]["sha256"] != asset["after_sha256"] or
            git_blob((package / RUNTIME_ASSET).read_bytes()) != target["files"][SOURCE_ASSET]["after"]):
        raise ValueError("Unpacked generated asset differs from its target baseline")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="pristine pinned patch inputs")
    parser.add_argument("--base-identity", type=Path, required=True)
    parser.add_argument("--target-source-commit", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        update(args.package, args.source, json.loads(args.base_identity.read_text()),
               args.target_source_commit, args.receipt)
    except (OSError, ValueError) as error:
        parser.exit(1, f"error: {error}\n")
    print(json.dumps({"status": "asset_updated", "receipt": str(args.receipt), "acceptance": "not_run"}))


if __name__ == "__main__":
    main()
