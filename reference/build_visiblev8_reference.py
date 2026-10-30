#!/usr/bin/env python3
"""Build the pinned original VisibleV8 shell or headless browser on Linux."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import engine_build_compat

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "reference/visiblev8.json"


def run(argv, cwd, output, env=None):
    print(f"Running {argv[0]} ({output.name})", flush=True)
    with output.open("wb") as stream:
        completed = subprocess.run(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT)
    if completed.returncode:
        tail = output.read_text(errors="replace").splitlines()[-35:]
        print("\n".join(tail), file=sys.stderr)
        raise RuntimeError(f"{argv[0]} exited {completed.returncode}; see {output}")


def head(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"],
                                   text=True).strip()


def bootstrap_depot(depot, logs, env, revision):
    if head(depot) != revision:
        raise ValueError("Refusing to bootstrap a different depot_tools revision")
    bootstrap_env = dict(env, DEPOT_TOOLS_DIR=str(depot), DEPOT_TOOLS_UPDATE="0",
                         DEPOT_TOOLS_BOOTSTRAP_PYTHON3="1")
    # The pinned provider explicitly bootstraps without updating its Git checkout.
    # gclient does not provision python-bin when DEPOT_TOOLS_UPDATE is disabled.
    run([str(depot / "ensure_bootstrap")], depot, logs / "depot-bootstrap.log", bootstrap_env)
    if head(depot) != revision:
        raise ValueError("depot_tools changed revision during bootstrap")
    marker = depot / "python3_bin_reldir.txt"
    if not marker.is_file() or not marker.read_text().strip():
        raise ValueError("Pinned depot_tools bootstrap did not provision its Python runtime")
    run([str(depot / "python-bin/python3"), "--version"], depot,
        logs / "depot-python.log", bootstrap_env)


def verify_reference(reference, lock):
    if head(reference) != lock["commit"]:
        raise ValueError("VisibleV8 checkout differs from the immutable reference")
    patch = reference / lock["v8_patch"]
    if hashlib.sha256(patch.read_bytes()).hexdigest() != lock["v8_patch_sha256"]:
        raise ValueError("VisibleV8 patch SHA-256 mismatch")
    companion = reference / lock["chromium_patch"]
    if hashlib.sha256(companion.read_bytes()).hexdigest() != lock["chromium_patch_sha256"]:
        raise ValueError("Chromium companion patch SHA-256 mismatch")
    subprocess.run(["git", "-C", str(reference), "diff", "--exit-code", "HEAD",
                    "--", "patches", "post-processor", "tests"], check=True,
                   stdout=subprocess.DEVNULL)


def prepare(reference, work, target, logs, lock):
    work.mkdir(parents=True, exist_ok=True)
    marker = work / ".camoufox-reference.json"
    identity = {"visiblev8": lock["commit"], "target": target}
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            raise ValueError("Work directory belongs to another reference build")
    elif any(work.iterdir()):
        raise ValueError("Refusing to prepare a nonempty, unowned work directory")
    else:
        marker.write_text(json.dumps(identity) + "\n")

    depot = work / "depot_tools"
    if not depot.exists():
        depot.mkdir()
        run(["git", "init"], depot, logs / "depot-init.log")
        run(["git", "remote", "add", "origin",
             "https://chromium.googlesource.com/chromium/tools/depot_tools.git"],
            depot, logs / "depot-remote.log")
        run(["git", "fetch", "--depth=1", "origin", lock["depot_tools_commit"]],
            depot, logs / "depot-fetch.log")
        run(["git", "checkout", "--detach", "FETCH_HEAD"], depot,
            logs / "depot-checkout.log")
    if head(depot) != lock["depot_tools_commit"]:
        raise ValueError("depot_tools revision mismatch")
    env = os.environ.copy()
    env.update(DEPOT_TOOLS_UPDATE="0", DEPOT_TOOLS_WIN_TOOLCHAIN="0")
    env["PATH"] = str(depot) + os.pathsep + env["PATH"]
    bootstrap_depot(depot, logs, env, lock["depot_tools_commit"])
    name = "v8" if target == "v8-shell" else "src"
    repository = "v8/v8" if target == "v8-shell" else "chromium/src"
    revision = lock["v8_commit"] if target == "v8-shell" else lock["chromium_commit"]
    configuration = ("solutions = [{\"name\": " + repr(name) +
                     ", \"url\": " + repr("https://chromium.googlesource.com/" + repository + ".git") +
                     ", \"deps_file\": \"DEPS\", \"managed\": False, \"custom_deps\": {}}]\n")
    config = work / ".gclient"
    if config.exists() and config.read_text() != configuration:
        raise ValueError("gclient configuration differs from this build profile")
    config.write_text(configuration)
    run([str(depot / "gclient"), "sync", "--nohooks", "--no-history", "--shallow",
         "--revision", name + "@" + revision], work, logs / "gclient-sync.log", env)
    source = work / name
    if head(source) != revision:
        raise ValueError("Source checkout revision mismatch")
    engine = source if target == "v8-shell" else source / "v8"
    if head(engine) != lock["v8_commit"]:
        raise ValueError("Chromium's V8 checkout differs from its pinned DEPS")
    patch = reference / lock["v8_patch"]
    run(["git", "apply", "--check", str(patch)], engine, logs / "v8-patch-check.log")
    run(["git", "apply", str(patch)], engine, logs / "v8-patch.log")
    engine_build_compat.apply(engine, logs, lock["v8_commit"])
    if target == "headless-shell":
        companion = reference / lock["chromium_patch"]
        run(["git", "apply", "--check", str(companion)], source,
            logs / "chromium-patch-check.log")
        run(["git", "apply", str(companion)], source, logs / "chromium-patch.log")
    run([str(depot / "gclient"), "runhooks"], work, logs / "gclient-hooks.log", env)
    return source, env


def build(reference, work, target, logs, delivery, lock, jobs):
    source = work / ("v8" if target == "v8-shell" else "src")
    expected = lock["v8_commit"] if target == "v8-shell" else lock["chromium_commit"]
    if head(source) != expected:
        raise ValueError("Build source revision mismatch")
    engine = source if target == "v8-shell" else source / "v8"
    compatibility = engine_build_compat.verify(engine, logs, lock["v8_commit"])
    depot = work / "depot_tools"
    if head(depot) != lock["depot_tools_commit"]:
        raise ValueError("Build tools revision mismatch")
    env = os.environ.copy()
    env.update(DEPOT_TOOLS_UPDATE="0", DEPOT_TOOLS_WIN_TOOLCHAIN="0")
    env["PATH"] = str(depot) + os.pathsep + env["PATH"]
    bootstrap_depot(depot, logs, env, lock["depot_tools_commit"])
    args = ["is_debug=false", "dcheck_always_on=false", "symbol_level=0",
            "is_component_build=false", "use_remoteexec=false", "use_siso=false",
            'cc_wrapper="ccache"',
            "v8_enable_lazy_source_positions=false", "vv8_trace_properties=true",
            "v8_use_metagen_instance_types=false",
            "v8_enable_debugging_features=true", "v8_enable_object_print=true",
            "v8_enable_disassembler=true", "v8_enable_backtrace=true"]
    if target == "v8-shell":
        args += ["v8_use_external_startup_data=false", "v8_enable_i18n_support=false"]
    out = source / "out/Reference"
    out.mkdir(parents=True, exist_ok=True)
    (out / "args.gn").write_text("\n".join(args) + "\n")
    gn = source / "buildtools/linux64/gn"
    run([str(gn), "gen", "out/Reference"], source, logs / "gn.log", env)
    binary = "v8_shell" if target == "v8-shell" else "headless_shell"
    run([str(depot / "autoninja"), "-C", "out/Reference", "-j", str(jobs), binary],
        source, logs / "compile.log", env)
    if not (out / binary).is_file():
        raise ValueError("Build completed without its requested executable")
    delivery.mkdir(parents=True, exist_ok=True)
    # These are build products required by the requested executable, not arbitrary source files.
    resources = [out / binary]
    if target == "headless-shell":
        resources += sorted(out.glob("*.pak")) + sorted(out.glob("*.bin"))
        resources += [p for p in [out / "icudtl.dat", out / "locales"] if p.exists()]
        resources += [p for p in out.glob("*.so*") if p.is_file()]
    copied = []
    for path in resources:
        destination = delivery / path.name
        if path.is_dir():
            shutil.copytree(path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(path, destination)
        copied.append(path.name)
    shutil.copy2(reference / "LICENSE", delivery / "LICENSE.visiblev8")
    shutil.copy2(source / "LICENSE", delivery / "LICENSE.engine")
    if target == "headless-shell":
        license_dir = delivery / "third-party-notices"
        for pattern in ("LICENSE*", "COPYING*", "NOTICE*"):
            for notice in (source / "third_party").rglob(pattern):
                if notice.is_file() and not notice.is_symlink():
                    destination = license_dir / notice.relative_to(source / "third_party")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(notice, destination)
    provenance = dict(lock, target=target, gn_args=args, resources=copied,
                      build_compatibility=compatibility,
                      binary_sha256=hashlib.sha256((out / binary).read_bytes()).hexdigest())
    (delivery / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"status": "built", "target": target,
                      "binary_sha256": provenance["binary_sha256"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target", choices=("v8-shell", "headless-shell"), required=True)
    parser.add_argument("--phase", choices=("prepare", "build", "all"), default="all")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    lock = json.loads(LOCK.read_text())
    logs = args.output.resolve() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        verify_reference(args.reference.resolve(), lock)
        if args.phase in ("prepare", "all"):
            prepare(args.reference.resolve(), args.work.resolve(), args.target, logs, lock)
        if args.phase in ("build", "all"):
            build(args.reference.resolve(), args.work.resolve(), args.target, logs,
                  args.output.resolve() / "bin", lock, args.jobs)
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as error:
        (args.output / "build-report.json").write_text(json.dumps({
            "status": "failed", "target": args.target, "error": str(error),
            "elapsed_seconds": round(time.monotonic() - started, 2)}, indent=2) + "\n")
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
