#!/usr/bin/env python3
"""Exercise the pinned original VisibleV8 programs without replacing their logic."""

import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET

from build_visiblev8_reference import LOCK, verify_reference

ROOT = Path(__file__).resolve().parents[1]
MODES = ("noop", "features", "poly_features", "scripts", "blobs", "ufeatures",
         "Mfeatures", "create_element", "callargs", "causality", "causality_graphml",
         "flow", "idlapis", "adblock", "fptp")
SUBMISSION = "2208ac9b-80c3-4fd9-b236-228e46db8e7e"


def execute(argv, cwd, output, env=None, timeout=120):
    with output.with_suffix(".stdout").open("wb") as stdout, \
            output.with_suffix(".stderr").open("wb") as stderr:
        result = subprocess.run([str(v) for v in argv], cwd=cwd, env=env,
                                stdout=stdout, stderr=stderr, timeout=timeout)
    return {"exit_code": result.returncode,
            "stdout_bytes": output.with_suffix(".stdout").stat().st_size,
            "stderr_bytes": output.with_suffix(".stderr").stat().st_size}


def module(reference, name):
    directory = reference / "tests/logs"
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location("original_" + name, directory / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def load_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fixture(directory):
    directory.mkdir(parents=True, exist_ok=True)
    # This is a controlled wire fixture, not a browser capture or a real entity database.
    sources = {
        1: "document.createElement('DIV'); navigator.userAgent; script.src = 'https://tracker.test/a.js';",
        2: "navigator.userAgent;",
        3: "navigator.userAgent; // evaluated child",
    }
    def escaped(text):
        return text.replace("\\", "\\\\").replace(":", "\\:").replace("\n", "\\x0a")
    lines = ["~0x1", '@"https\\://example.test/index.html":"https\\://example.test"',
             '$1:"https\\://example.test/main.js":' + escaped(sources[1]), "!1",
             'c0:%createElement:{1,HTMLDocument}:"DIV"',
             'g31:{2,Navigator}:"userAgent"', 'g31:{2,Navigator}:"userAgent"',
             's52:{3,HTMLScriptElement}:"src":"https\\://tracker.test/a.js"',
             'n60:%URL:"https\\://example.test/path"',
             '$2:"https\\://tracker.test/a.js":' + escaped(sources[2]), "!2",
             'g0:{2,Navigator}:"userAgent"',
             '$3:1:' + escaped(sources[3]), "!3", 'g0:{2,Navigator}:"userAgent"',
             "!1", 'g70:{2,Navigator}:"userAgent"', 'g70:{4,Screen}:"width"']
    raw = directory / "vv8-reference-1-1-main.0.log"
    raw.write_text("\n".join(lines) + "\n", encoding="ascii")
    members = {"HTMLDocument": ([], ["createElement"]),
               "Navigator": (["userAgent"], []), "HTMLScriptElement": (["src"], []),
               "URL": ([], []), "Screen": (["width"], [])}
    idl = {name: {"parent": None, "aliases": [], "properties": props,
                  "methods": methods, "members": props + methods}
           for name, (props, methods) in members.items()}
    (directory / "idldata.json").write_text(json.dumps(idl, sort_keys=True) + "\n")
    (directory / "entities.json").write_text(json.dumps({
        "example.test": {"displayName": "Fixture First Party", "tracking": 0.0},
        "tracker.test": {"displayName": "Fixture Tracker", "tracking": 0.75}}) + "\n")
    (directory / "easylist.txt").write_text("[Adblock Plus 2.0]\n||tracker.test^$script\n")
    (directory / "easyprivacy.txt").write_text("[Adblock Plus 2.0]\n@@||example.test^$script\n")
    return raw, sources


def assert_stdout(mode, rows, sources):
    if mode in ("noop", "poly_features", "blobs", "idlapis"):
        assert not rows, f"{mode} unexpectedly emitted stdout rows"
    elif mode == "ufeatures":
        assert len(rows) == 1
        assert set(rows[0]["allFeatures"]) == {
            "HTMLDocument.createElement", "Navigator.userAgent", "HTMLScriptElement.src", "Screen.width"}
    else:
        assert all(isinstance(row, list) and len(row) == 2 for row in rows)
        payloads = [row[1] for row in rows]
        if mode == "features":
            repeated = [r for r in payloads if r["feature_name"] == "Navigator.userAgent"
                        and r["script_offset"] == 31]
            assert len(repeated) == 1 and repeated[0]["use_count"] == 2
            assert not any(r["script_offset"] == 70 for r in payloads), "polymorphic site was not suppressed"
        elif mode == "scripts":
            assert len(payloads) == 3
            parent_hash = hashlib.sha256(sources[1].encode()).hexdigest()
            assert any(r["eval_parent_hash"] == parent_hash for r in payloads)
        elif mode == "Mfeatures":
            assert Counter(row[0] for row in rows)["mega_instance"] == 3
            assert any(row[0] == "mega_usage" and row[1]["mode"] == "n" for row in rows)
        elif mode == "create_element":
            assert len(payloads) == 1 and payloads[0]["tag_name"] == "div"
            assert payloads[0]["create_count"] == 1
        elif mode == "callargs":
            assert len(payloads) == 1 and "createElement" in json.dumps(payloads)
            assert "DIV" in json.dumps(payloads)
        elif mode == "causality":
            assert any(r["genesis"] == "eval" for r in payloads)
        elif mode == "flow":
            assert len(payloads) == 3
            assert any("URL" in api for r in payloads for api in r["APIs"])
        elif mode == "fptp":
            assert any(r["URL"] == "https://tracker.test/a.js" and r["ThirdParty"] for r in payloads)


def postprocessors(args, lock):
    output = args.output.resolve()
    data = output / "fixture"
    raw, sources = fixture(data)
    env = os.environ.copy()
    env.update(IDLDATA_FILE=str(data / "idldata.json"), EMAP_FILE=str(data / "entities.json"),
               EASYLIST_FILE=str(data / "easylist.txt"), EASYPRIVACY_FILE=str(data / "easyprivacy.txt"),
               ADBLOCK_BINARY=str(args.adblock.resolve()))
    version = subprocess.check_output([str(args.processor.resolve()), "-version"], text=True).strip()
    if version != lock["commit"]:
        raise ValueError("Original processor version does not match its source pin")
    report = {"reference": lock["commit"], "profile": "original-postprocessors",
              "stdout": {}, "postgresql": {}, "limitations": [], "failures": []}
    for mode in MODES:
        base = output / ("stdout-" + mode)
        info = execute([args.processor.resolve(), "-aggs", mode, "-rootdomain", "https://example.test",
                        "-output", "stdout", raw], output, base, env)
        report["stdout"][mode] = info
        try:
            if info["exit_code"]:
                raise AssertionError(f"original processor exited {info['exit_code']}")
            if mode == "causality_graphml":
                tree = ET.fromstring(base.with_suffix(".stdout").read_bytes())
                assert tree.tag.endswith("graphml")
                assert any(node.tag.endswith("edge") for node in tree.iter())
            else:
                rows = load_rows(base.with_suffix(".stdout"))
                if mode != "adblock":
                    assert_stdout(mode, rows, sources)
                else:
                    # Upstream's readout goroutine is not joined: record the result without
                    # promoting a race-dependent row count to a passing assertion.
                    report["limitations"].append("adblock stdout has no readout synchronization; use direct Rust oracle below")
                    info["observed_rows"] = rows
                    info["status"] = "reference_race_not_a_pass"
            info.setdefault("status", "passed")
        except (AssertionError, ValueError, KeyError, ET.ParseError) as error:
            info.update(status="failed", error=str(error))
            report["failures"].append("stdout:" + mode)
    # Run the fixed original Rust engine directly with known ABP rules.
    query = data / "adblock-queries.jsonl"
    query.write_text("\n".join(json.dumps({"url": u, "origin": "https://example.test/index.html"})
                               for u in ("https://tracker.test/a.js", "https://example.test/main.js")) + "\n")
    rust = execute([args.adblock.resolve(), query], output, output / "rust-oracle", env)
    rows = load_rows(output / "rust-oracle.stdout")
    if rust["exit_code"] or [row.get("blocked") for row in rows] != [True, False]:
        report["failures"].append("rust-oracle")
    report["rust"] = dict(rust, rows=rows)
    if args.postgres:
        pg_version = subprocess.check_output(["psql", "-X", "-A", "-t", "-c", "SHOW server_version"],
                                             env=env, text=True).strip()
        if pg_version != lock["postgres_version"]:
            raise ValueError("PostgreSQL server differs from the fixed integration profile")
        report["postgres_version"] = pg_version
        for mode in MODES:
            if mode == "noop":
                continue
            name = "vv8_" + mode.lower()
            pg_env = dict(env, PGOPTIONS="-c search_path=" + name)
            subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", "CREATE SCHEMA " + name],
                           check=True, env=env, stdout=subprocess.DEVNULL)
            subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f",
                            str(args.reference / "post-processor/postgres_schema.sql")],
                           check=True, env=pg_env, stdout=subprocess.DEVNULL)
            extra = ("CREATE TABLE submissions(id uuid PRIMARY KEY,url text);"
                     "INSERT INTO submissions VALUES ('" + SUBMISSION + "','https://example.test');"
                     "CREATE TABLE idlapis(api text,script_list text[],log_file_id int,root_domain text);"
                     "CREATE TABLE script_causality_graphml(id uuid,xml text);")
            subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-c", extra],
                           check=True, env=pg_env, stdout=subprocess.DEVNULL)
            info = execute([args.processor.resolve(), "-aggs", mode, "-submissionid", SUBMISSION,
                            "-output", "postgresql", raw], output, output / ("postgres-" + mode), pg_env)
            report["postgresql"][mode] = info
            if info["exit_code"]:
                info["status"] = "reference_failure"
                report["failures"].append("postgresql:" + mode)
            elif mode in ("callargs", "adblock"):
                info["status"] = "unsupported_or_racy_not_a_pass"
                report["limitations"].append("postgresql:" + mode + " lacks a deterministic supported writer")
            else:
                info["status"] = "executed"
            table_rows = subprocess.check_output([
                "psql", "-X", "-A", "-t", "-c",
                "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename"],
                env=pg_env, text=True).splitlines()
            info["table_counts"] = {table: int(subprocess.check_output([
                "psql", "-X", "-A", "-t", "-c", "SELECT count(*) FROM " + table],
                env=pg_env, text=True).strip()) for table in table_rows}
    report["status"] = "failed" if report["failures"] else "passed_supported_contracts"
    report["binary_sha256"] = hashlib.sha256(args.processor.read_bytes()).hexdigest()
    report["rust_sha256"] = hashlib.sha256(args.adblock.read_bytes()).hexdigest()
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "failures": report["failures"]}))
    return int(bool(report["failures"]))


def shell(args, lock):
    reference = args.reference.resolve()
    if Path("/testsrc").resolve() != (reference / "tests/src").resolve():
        raise ValueError("Original goldens require /testsrc to point to the pinned tests/src")
    relabel = module(reference, "relabel")
    report = {"reference": lock["commit"], "profile": "v8-shell", "cases": {}, "failures": []}
    for script in sorted((reference / "tests/src").glob("*.js")):
        destination = args.output / script.stem
        destination.mkdir()
        result = execute([args.shell.resolve(), "--no-maglev", "--no-turbofan",
                          Path("/testsrc") / script.name], destination, destination / "process")
        logs = sorted(destination.glob("vv8-*.log"))
        if result["exit_code"] or len(logs) != 1:
            raise ValueError(f"{script.name}: expected one complete shell trace and successful exit")
        expected = reference / "tests/logs/trace-apis-obj" / (script.stem + ".log")
        with logs[0].open() as actual, expected.open() as oracle:
            a = list(relabel.relabel_volatile_log_fields(actual))
            b = list(relabel.relabel_volatile_log_fields(oracle))
        passed = a == b
        report["cases"][script.name] = dict(result, passed=passed, records=len(a))
        if not passed:
            (destination / "actual-normalized.txt").write_text("\n".join(a) + "\n")
            (destination / "expected-normalized.txt").write_text("\n".join(b) + "\n")
            report["failures"].append(script.name)
    assert len(report["cases"]) == 9, "Pinned reference fixture set changed"
    # The upstream runner disables optimizing tiers. Test its most substantial
    # loop separately with normal defaults and compare semantic log counts.
    hot = args.output / "optimized-props"
    hot.mkdir()
    result = execute([args.shell.resolve(), "/testsrc/props.js"], hot, hot / "process")
    logs = list(hot.glob("vv8-*.log"))
    if result["exit_code"] or len(logs) != 1:
        raise ValueError("Optimized shell did not produce one complete trace")
    counts = Counter(line[0] for line in logs[0].read_text().splitlines() if line)
    if any(counts[k] != 1339 for k in "gsc"):
        report["failures"].append("optimized-props-counts")
    report["optimized"] = dict(result, record_counts=dict(counts))
    report["status"] = "failed" if report["failures"] else "passed"
    report["binary_sha256"] = hashlib.sha256(args.shell.read_bytes()).hexdigest()
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "goldens": len(report["cases"]),
                      "optimized": report["optimized"]["record_counts"]}))
    return int(bool(report["failures"]))


def browser(args, lock):
    observations = []
    directory = ROOT / "reference/browser"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            name = self.path.split("?", 1)[0].strip("/") or "index.html"
            if name not in ("index.html", "probe.js", "worker.js"):
                self.send_error(404)
                return
            data = (directory / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript" if name.endswith(".js") else "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path == "/result":
                observations.append(json.loads(self.rfile.read(length)))
            else:
                self.rfile.read(length)
            self.send_response(204)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:" + str(server.server_port) + "/"
        result = execute([args.browser.resolve(), "--no-sandbox", "--headless", "--disable-gpu",
                          "--disable-dev-shm-usage", "--no-first-run", "--dump-dom",
                          "--virtual-time-budget=20000", url], args.output, args.output / "process", timeout=120)
    finally:
        server.shutdown()
        thread.join()
    (args.output / "page-observations.json").write_text(json.dumps({
        "process": result, "observations": observations}, indent=2) + "\n")
    if result["exit_code"] or len(observations) != 1:
        raise ValueError("Original browser did not complete its controlled localhost fixture")
    observed = observations[0]
    if (observed.get("error") or observed.get("hotSum") != 6000
            or observed.get("keyConversions") != 2 or observed.get("argumentAccessorReads") != 0):
        raise ValueError("Original browser fixture returned incorrect page observations")
    raw_parser = module(args.reference.resolve(), "vlp")
    kinds = Counter()
    hot_gets = hot_calls = accessor_calls = 0
    sources = set()
    for path in args.output.glob("vv8-*.log"):
        for line in path.read_text().splitlines():
            if not line:
                continue
            kinds[line[0]] += 1
            fields = raw_parser.parse_raw_fields(line[1:])
            if line[0] == "$":
                sources.add(fields[2])
            if line[0] == "g" and fields[2] == '"__ref_hot"':
                hot_gets += 1
            if line[0] == "c" and fields[1] == "%createElement" and fields[3:] == ['"ref-hot"']:
                hot_calls += 1
            if line[0] == "c" and "userAgent" in fields[1]:
                accessor_calls += 1
    lost_events = hot_gets != 6000 or hot_calls != 4096 or not all(kinds[k] for k in "gscn$!")
    report = dict(result, status="failed" if lost_events else "passed", reference=lock["commit"], profile="headless-shell",
                  observations=observed, record_counts=dict(kinds), hot_gets=hot_gets,
                  hot_calls=hot_calls, user_agent_native_call_records=accessor_calls,
                  registered_sources=len(sources),
                  binary_sha256=hashlib.sha256(args.browser.read_bytes()).hexdigest())
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if lost_events:
        raise ValueError(f"Original optimized browser lost expected events: get={hot_gets}, call={hot_calls}")
    print(json.dumps({k:report[k] for k in ("status", "hot_gets", "hot_calls", "user_agent_native_call_records")}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("postprocessors", "shell", "browser"):
        p = sub.add_parser(name)
        p.add_argument("--reference", required=True, type=Path)
        p.add_argument("--output", required=True, type=Path)
        if name == "postprocessors":
            p.add_argument("--processor", required=True, type=Path)
            p.add_argument("--adblock", required=True, type=Path)
            p.add_argument("--postgres", action="store_true")
        else:
            p.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    args.reference = args.reference.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text())
    try:
        verify_reference(args.reference, lock)
        code = {"postprocessors": postprocessors, "shell": shell, "browser": browser}[args.command](args, lock)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        (args.output / "failure.json").write_text(json.dumps({"status":"failed", "error":str(error)}) + "\n")
        parser.exit(1, str(error) + "\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
