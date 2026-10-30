"""Verify caching against a real pinned Chromium modular C++ compile command."""

import json
import hashlib
from pathlib import Path
import shlex
import subprocess

PCM_TARGETS = (
    "obj/build/modules/system/module.pcm",
    "clang_x64_for_rust_host_build_tools/obj/build/modules/system/module.pcm",
)
OBJECT_TARGET = "obj/third_party/abseil-cpp/absl/base/base/casts.o"


def parse_stats(value):
    result = {}
    for line in value.splitlines():
        if line.strip():
            name, count = line.split()
            result[name] = int(count)
    return result


def validate_command(command, out):
    argv = shlex.split(command)
    if any(token in (";", "&&", "||", "|", ">", "<") for token in argv):
        raise ValueError("Expected one compiler invocation, without shell operators")
    if not argv or Path(argv[0]).name != "ccache" or not any(
            Path(token).name == "clang++" for token in argv[1:]):
        raise ValueError("Generated C++ command does not invoke ccache and clang++")
    expanded = []
    for token in argv:
        if token.startswith("@"):
            expanded.extend(shlex.split((out / token[1:]).read_text()))
        else:
            expanded.append(token)
    if "-fmodules" not in expanded or not any("module.pcm" in token for token in expanded):
        raise ValueError("Cache probe must use Chromium's actual module flags")
    if OBJECT_TARGET not in expanded:
        raise ValueError("Cache probe command does not produce its pinned object")
    return argv, expanded


def verify_hits(stats):
    hits = stats.get("direct_cache_hit", 0) + stats.get("preprocessed_cache_hit", 0)
    if hits < 1 or hits + stats.get("cache_miss", 0) != 2:
        raise ValueError("Repeated real modular compile did not produce two cacheable calls and a hit")


def probe(source, logs, env, jobs, run):
    out = source / "out/Reference"
    ninja = source / "third_party/ninja/ninja"
    # These exact targets occur in the original pinned headless compile log.
    # Retain rspfiles so the provider's expanded compile can be replayed intact.
    run([str(ninja), "-C", "out/Reference", "-d", "keeprsp", "-j", str(jobs),
         *PCM_TARGETS, OBJECT_TARGET], source, logs / "cache-probe-prerequisites.log", env)
    for target in PCM_TARGETS:
        if not (out / target).is_file():
            raise ValueError(f"Required actual Clang module was not built: {target}")
    required_hash_inputs = {str((out / target).resolve()) for target in PCM_TARGETS}
    if set(env.get("CCACHE_EXTRAFILES", "").split(":")) != required_hash_inputs:
        raise ValueError("Module cache profile must hash both actual PCM files")
    commands = subprocess.check_output([str(ninja), "-C", "out/Reference", "-t",
                                        "commands", OBJECT_TARGET], cwd=source, env=env, text=True)
    command = commands.splitlines()[-1]
    argv, expanded = validate_command(command, out)
    (logs / "actual-module-compile.json").write_text(json.dumps({
        "command": command, "expanded_argv": expanded,
        "extra_pcm_hash_inputs": sorted(required_hash_inputs)}, indent=2) + "\n")
    run(["ccache", "--zero-stats"], source, logs / "cache-probe-reset.log", env)
    for label in ("first", "second"):
        run(argv, out, logs / ("cache-probe-" + label + ".log"), env)
    raw = subprocess.check_output(["ccache", "--print-stats"], env=env, text=True)
    (logs / "cache-probe-counters.txt").write_text(raw)
    stats = parse_stats(raw)
    verify_hits(stats)
    report = {"status": "passed_real_modular_compiler_cache", "counters": stats,
              "object": OBJECT_TARGET, "pcm_files": {}}
    for name in PCM_TARGETS:
        report["pcm_files"][name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    (logs / "cache-probe.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
