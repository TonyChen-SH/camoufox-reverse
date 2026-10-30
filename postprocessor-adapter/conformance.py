#!/usr/bin/env python3
"""Preserve original failures and verify the separately identified repaired profile."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
import reference_conformance as oracle
from build_visiblev8_reference import LOCK, verify_reference
from repair import PROFILE

EXPECTED_FAILURES = {
    "scripts": "sql: converting argument $8 type: unsupported type core.Origin, a struct",
    "causality": 'pq: invalid input value for enum script_genesis: "static_iframe"',
    "causality_graphml": 'pq: column "logfile_id" of relation "script_causality_graphml" does not exist',
}
COUNTS = {
    "features": {"feature_usage": 5}, "poly_features": {"poly_feature_usage": 2},
    "scripts": {"script_creation": 3}, "blobs": {"script_blobs": 3},
    "ufeatures": {"js_api_features_summary": 1},
    "Mfeatures": {"mega_features": 5, "mega_instances": 3, "mega_scripts": 3, "mega_usages": 8},
    "create_element": {"create_elements": 1}, "causality": {"script_causality": 4},
    "causality_graphml": {"script_causality_graphml": 1}, "flow": {"script_flow": 3},
    "idlapis": {"idlapis": 1}, "adblock": {"adblock": 2}, "fptp": {"thirdpartyfirstparty": 3},
}
ELEMENT_FEATURES = [(0, "Document.createElement", 2), (31, "Document.createElement", 1),
                    (62, "Document.createElement", 1), (93, "Document.createElement", 1),
                    (124, "Document.createElement", 1), (155, "CustomDocument.createElement", 1)]
ELEMENT_TAGS = [(0, "div", 2), (31, "span", 1)]
ELEMENT_SOURCE = "document.createElement('DIV'); document.createElement('SPAN'); document.createElement();"


def element_fixture(directory):
    directory.mkdir()
    fixtures = ROOT / "postprocessor-adapter/fixtures"
    provenance = json.loads((fixtures / "firefox158-document-idl.provenance.json").read_text())
    metadata = fixtures / provenance["projection_file"]
    assert hashlib.sha256(metadata.read_bytes()).hexdigest() == provenance["projection_sha256"]
    tree = json.loads(metadata.read_text())
    assert tree["HTMLDocument"]["parent"] == "Document"
    assert "createElement" in tree["Document"]["methods"]
    shutil.copyfile(metadata, directory / "idldata.json")
    legacy = {"HTMLDocument": {"parent": None, "methods": ["createElement"],
                                "properties": [], "members": ["createElement"]}}
    (directory / "legacy-idldata.json").write_text(json.dumps(legacy) + "\n")
    source = ELEMENT_SOURCE
    header = ["~0x1", '@"https\\://example.test/index.html":"https\\://example.test"',
              '$1:"https\\://example.test/main.js":' + source, "!1"]
    calls = ['c0:%createElement:{1,HTMLDocument}:"DIV"', 'c0:%createElement:{1,HTMLDocument}:"div"',
             'c31:%createElement:{2,Document}:"SPAN"', 'c62:%createElement:{1,HTMLDocument}',
             'c93:%createElement:{1,HTMLDocument}:#U', 'c124:%createElement:{1,HTMLDocument}:17',
             'c155:%createElement:{3,CustomDocument}:"X"']
    (directory / "vv8-inherited-1-1-main.0.log").write_text("\n".join(header + calls) + "\n")
    (directory / "vv8-missing-1-1-main.0.log").write_text("\n".join(header + [calls[3]]) + "\n")
    (directory / "fixture-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return source, provenance


def element_stdout(binary, output, original):
    output.mkdir()
    data = output / "fixture"
    _, provenance = element_fixture(data)
    env = dict(os.environ, IDLDATA_FILE=str(data / "idldata.json"))
    info = oracle.execute([binary, "-aggs", "features+create_element",
                           data / "vv8-inherited-1-1-main.0.log"], output, output / "inherited", env)
    assert info["exit_code"] == 0, info
    rows = oracle.load_rows(output / "inherited.stdout")
    features = sorted((r[1]["script_offset"], r[1]["feature_name"], r[1]["use_count"])
                      for r in rows if r[0] == "feature_usage")
    assert features == ELEMENT_FEATURES, features
    tags = sorted((r[1]["script_offset"], r[1]["tag_name"], r[1]["create_count"])
                  for r in rows if r[0] == "create_element")
    assert all(r[0] in ("feature_usage", "create_element") for r in rows), rows
    assert tags == ([] if original else ELEMENT_TAGS), tags
    info.update(status="failed" if original else "passed", checked_features=features, checked_tags=tags)
    missing_env = dict(env, IDLDATA_FILE=str(data / "legacy-idldata.json"))
    missing = oracle.execute([binary, "-aggs", "create_element",
                              data / "vv8-missing-1-1-main.0.log"], output, output / "missing", missing_env)
    stderr = (output / "missing.stderr").read_text()
    assert missing["stdout_bytes"] == 0, missing
    if original:
        assert missing["exit_code"] == 2 and "index out of range [3] with length 3" in stderr, stderr
    else:
        assert missing["exit_code"] == 0 and "panic:" not in stderr, stderr
    missing["status"] = "failed" if original else "passed"
    report = dict(status="failed" if original else "passed", profile="original" if original else PROFILE,
                  fixture=provenance, inherited_document=info, missing_argument=missing)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def sql(statement, env):
    return subprocess.check_output(["psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                                    "-c", statement], env=env, text=True).strip()


def query_rows(table, columns, env):
    return json.loads(sql("SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT " + columns +
                          " FROM " + table + ") t", env))


def environment(data, adblock):
    return dict(os.environ, IDLDATA_FILE=str(data / "idldata.json"),
                EMAP_FILE=str(data / "entities.json"), EASYLIST_FILE=str(data / "easylist.txt"),
                EASYPRIVACY_FILE=str(data / "easyprivacy.txt"), ADBLOCK_BINARY=str(adblock))


def oracle_failures(binary, data, output, repaired):
    output.mkdir()
    raw = data / "vv8-reference-1-1-main.0.log"
    results = {}
    signatures = {"exit7": "exit status 7", "invalid": "invalid character",
                  "missing": "fewer results", "extra": "extra results",
                  "missing_blocked": "requires url, origin and blocked"}
    for mode, signature in signatures.items():
        child = output / ("oracle-" + mode)
        child.write_text("#!" + sys.executable + "\nimport json,sys\n"
            "items=[json.loads(line) for line in open(sys.argv[1])]\n"
            "mode=" + repr(mode) + "\n"
            "if mode=='invalid': print('{broken');sys.exit(0)\n"
            "if mode=='missing': sys.exit(0)\n"
            "for item in items:\n"
            "    if mode!='missing_blocked': item['blocked']=True\n"
            "    print(json.dumps(item))\n"
            "if mode=='extra': print(json.dumps(dict(items[0],blocked=True)))\n"
            "sys.exit(7 if mode=='exit7' else 0)\n")
        child.chmod(0o700)
        env = environment(data, child)
        info = oracle.execute([binary, "-aggs", "adblock", raw], output, output / mode, env)
        stderr = (output / mode).with_suffix(".stderr").read_text()
        if repaired:
            assert info["exit_code"] != 0 and signature in stderr, (mode, info, stderr)
            info["status"] = "passed_error_propagation"
        else:
            assert info["exit_code"] == 0, (mode, "original failure behavior changed", stderr)
            info["status"] = "expected_upstream_failure"
        results[mode] = info
    return results


def check_pg_values(mode, env, sources):
    if mode == "scripts":
        rows = query_rows("script_creation", "first_origin,script_url,encode(script_hash,'hex') AS hash,encode(eval_parent_hash,'hex') AS parent", env)
        assert all(row["first_origin"] == "https://example.test/index.html" for row in rows)
        parent = hashlib.sha256(sources[1].encode()).hexdigest()
        assert any(row["parent"] == parent for row in rows)
        return rows
    if mode == "blobs":
        rows = query_rows("script_blobs", "script_code,size,encode(sha256sum,'hex') AS hash", env)
        assert {row["script_code"] for row in rows} == set(sources.values())
        for row in rows:
            assert row["size"] == len(row["script_code"].encode())
            assert row["hash"] == hashlib.sha256(row["script_code"].encode()).hexdigest()
        return rows
    if mode == "causality":
        rows = query_rows("script_causality", "genesis,by_url", env)
        assert {row["genesis"] for row in rows} == {"eval", "include", "static", "static_iframe"}
        return rows
    if mode == "causality_graphml":
        rows = query_rows("script_causality_graphml", "id,xml", env)
        for row in rows:
            tree = ET.fromstring(row["xml"])
            assert tree.tag.endswith("graphml") and any(n.tag.endswith("edge") for n in tree.iter())
        return rows
    if mode == "adblock":
        rows = query_rows("adblock", "url,origin,blocked", env)
        assert {row["url"]: row["blocked"] for row in rows} == {
            "https://tracker.test/a.js": True, "https://example.test/main.js": False}
        return rows
    if mode == "fptp":
        rows = query_rows("thirdpartyfirstparty", "url,property_of_script,property_of_first_origin,is_script_third_party_with_root_domain,is_script_third_party_with_first_origin,script_origin_tracking_value", env)
        tracker = next(row for row in rows if row["url"] == "https://tracker.test/a.js")
        assert tracker["property_of_script"] == "Fixture Tracker"
        assert tracker["property_of_first_origin"] == "Fixture First Party"
        assert tracker["is_script_third_party_with_root_domain"] is True
        assert tracker["is_script_third_party_with_first_origin"] is True
        assert tracker["script_origin_tracking_value"] == 0.75
        return rows
    if mode == "features":
        rows = query_rows("feature_usage", "feature_name,script_offset,use_count", env)
        assert any(r["feature_name"] == "Navigator.userAgent" and r["script_offset"] == 31 and r["use_count"] == 2 for r in rows)
        return rows
    if mode == "poly_features":
        rows = query_rows("poly_feature_usage", "script_offset,use_count", env)
        assert all(r["script_offset"] == 70 and r["use_count"] == 1 for r in rows)
        return rows
    if mode == "Mfeatures":
        rows = query_rows("mega_usages", "usage_mode,usage_count", env)
        assert any(row["usage_mode"] == "n" for row in rows)
        assert sum(row["usage_count"] for row in rows) == 9
        return rows
    if mode == "create_element":
        rows = query_rows("create_elements", "tag_name,create_count", env)
        assert rows == [{"tag_name": "div", "create_count": 1}]
        return rows
    if mode == "ufeatures":
        rows = query_rows("js_api_features_summary", "all_features", env)
        assert set(rows[0]["all_features"]) == {"HTMLDocument.createElement", "Navigator.userAgent", "HTMLScriptElement.src", "Screen.width"}
        return rows
    if mode == "flow":
        rows = query_rows("script_flow", "code,url,apis,first_origin,encode(sha256,'hex') AS hash", env)
        assert {row["code"] for row in rows} == set(sources.values())
        assert all(row["hash"] == hashlib.sha256(row["code"].encode()).hexdigest() for row in rows)
        assert all(row["first_origin"] == "https://example.test/index.html" for row in rows)
        assert any("URL" in api for row in rows for api in row["apis"])
        return rows
    if mode == "idlapis":
        rows = query_rows("idlapis", "api,script_list,root_domain", env)
        assert len(rows) == 1 and rows[0]["api"] == "URL."
        assert rows[0]["script_list"] == ["n 60 https://example.test/main.js https://example.test/index.html"]
        return rows
    return []


def postgres_elements(source_root, binary, data, output, env, original):
    schema = "cfv8_elements_original" if original else "cfv8_elements_repaired"
    element_env = dict(env, PGOPTIONS="-c search_path=" + schema,
                       IDLDATA_FILE=str(data / "idldata.json"))
    sql("CREATE SCHEMA " + schema, env)
    subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f",
                    str(source_root / "post-processor/postgres_schema.sql")],
                   env=element_env, stdout=subprocess.DEVNULL, check=True)
    if original:
        # The unchanged upstream expects this table from its external VPC DB,
        # as in the original baseline fixture. The adapter must deliver it.
        sql("CREATE TABLE submissions(id uuid PRIMARY KEY,url text NOT NULL)", element_env)
    sql("INSERT INTO submissions VALUES ('" + oracle.SUBMISSION + "','https://example.test')", element_env)
    info = oracle.execute([binary, "-aggs", "features+create_element", "-submissionid", oracle.SUBMISSION,
                           "-output", "postgresql", data / "vv8-inherited-1-1-main.0.log"],
                          output, output / "postgres-elements-inherited", element_env)
    assert info["exit_code"] == 0, info
    feature_rows = query_rows("feature_usage", "feature_name,script_offset,use_count", element_env)
    assert sorted((r["script_offset"], r["feature_name"], r["use_count"]) for r in feature_rows) == ELEMENT_FEATURES
    tag_rows = query_rows("create_elements", "script_offset,tag_name,create_count,security_origin,encode(script_hash,'hex') AS hash", element_env)
    assert sorted((r["script_offset"], r["tag_name"], r["create_count"]) for r in tag_rows) == ([] if original else ELEMENT_TAGS)
    expected_hash = hashlib.sha256(ELEMENT_SOURCE.encode()).hexdigest()
    assert all(r["hash"] == expected_hash and r["security_origin"] == "https://example.test/index.html" for r in tag_rows)
    report = dict(info, status="failed" if original else "passed",
        checked_features=feature_rows, checked_tags=tag_rows,
        table_counts={"feature_usage": len(feature_rows), "create_elements": len(tag_rows)})
    (output / "postgres-elements-inherited.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def postgres(adapter_source, binary, data, output, env, sources):
    results = {}
    raw = data / "vv8-reference-1-1-main.0.log"
    for mode in COUNTS:
        name = "cfv8_" + mode.lower()
        sql("CREATE SCHEMA " + name, env)
        pg_env = dict(env, PGOPTIONS="-c search_path=" + name)
        subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f",
                        str(adapter_source / "post-processor/postgres_schema.sql")],
                       env=pg_env, stdout=subprocess.DEVNULL, check=True)
        sql("INSERT INTO submissions VALUES ('" + oracle.SUBMISSION + "','https://example.test')", pg_env)
        info = oracle.execute([binary, "-aggs", mode, "-submissionid", oracle.SUBMISSION,
                               "-output", "postgresql", raw], output,
                              output / ("postgres-" + mode), pg_env)
        assert info["exit_code"] == 0, (mode, info)
        info["table_counts"] = {table: int(sql("SELECT count(*) FROM " + table, pg_env))
                                for table in COUNTS[mode]}
        assert info["table_counts"] == COUNTS[mode], (mode, info)
        try:
            info["checked_rows"] = check_pg_values(mode, pg_env, sources)
        except AssertionError as error:
            snapshot = {table: query_rows(table, "*", pg_env) for table in COUNTS[mode]}
            raise AssertionError((mode, "PostgreSQL value mismatch", snapshot)) from error
        info["status"] = "passed"
        results[mode] = info
    # Make root-domain and first-origin classifications different so swapped
    # boolean columns cannot accidentally pass because both values are equal.
    env_fptp = dict(env, PGOPTIONS="-c search_path=cfv8_fptp")
    sql("TRUNCATE thirdpartyfirstparty", env_fptp)
    sql("UPDATE submissions SET url='https://root.test'", env_fptp)
    info = oracle.execute([binary, "-aggs", "fptp", "-submissionid", oracle.SUBMISSION,
                           "-output", "postgresql", raw], output, output / "fptp-distinct-root", env_fptp)
    assert info["exit_code"] == 0
    rows = query_rows("thirdpartyfirstparty", "url,is_script_third_party_with_root_domain,is_script_third_party_with_first_origin", env_fptp)
    first = next(row for row in rows if row["url"] == "https://example.test/main.js")
    assert first["is_script_third_party_with_root_domain"] is True
    assert first["is_script_third_party_with_first_origin"] is False
    results["fptp_distinct_root"] = dict(info, status="passed", checked_rows=rows)
    results["create_element_inherited_document"] = postgres_elements(
        adapter_source, binary, output / "element-regressions/fixture", output, env, False)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--adapter-source", required=True, type=Path)
    parser.add_argument("--original-processor", required=True, type=Path)
    parser.add_argument("--adapter-processor", required=True, type=Path)
    parser.add_argument("--adblock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--postgres", action="store_true")
    args = parser.parse_args()
    for field in ("reference", "adapter_source", "original_processor", "adapter_processor", "adblock", "output"):
        setattr(args, field, getattr(args, field).resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text())
    summary = {"original_commit": lock["commit"], "adapter_profile": PROFILE, "status": "failed"}
    try:
        verify_reference(args.reference, lock)
        original_out = args.output / "original"
        original_out.mkdir()
        original_args = SimpleNamespace(reference=args.reference, processor=args.original_processor,
            adblock=args.adblock, output=original_out, postgres=args.postgres)
        oracle.postprocessors(original_args, lock)
        original = json.loads((original_out / "report.json").read_text())
        expected = {"postgresql:" + mode for mode in EXPECTED_FAILURES} if args.postgres else set()
        assert set(original["failures"]) == expected, original["failures"]
        classification = {"status": "expected_upstream_failures", "postgresql": {}}
        if args.postgres:
            for mode, signature in EXPECTED_FAILURES.items():
                stderr = (original_out / ("postgres-" + mode)).with_suffix(".stderr").read_text()
                assert stderr.endswith(signature + "\n"), (mode, stderr)
                classification["postgresql"][mode] = "expected_upstream_failure"
            old_env = dict(os.environ, PGOPTIONS="-c search_path=vv8_fptp")
            rows = query_rows("thirdpartyfirstparty", "url,property_of_script,property_of_first_origin", old_env)
            tracker = next(r for r in rows if r["url"] == "https://tracker.test/a.js")
            assert tracker["property_of_script"] == "Fixture First Party" and tracker["property_of_first_origin"] == "Fixture Tracker"
            classification["fptp_values"] = {"status": "expected_upstream_failure", "rows": rows}
        classification["oracle_error_propagation"] = oracle_failures(
            args.original_processor, original_out / "fixture", original_out / "negative-oracles", False)
        element_stdout(args.original_processor, original_out / "element-regressions", True)
        classification["create_element"] = {
            "inherited_document": "expected_upstream_failure",
            "missing_argument": "expected_upstream_failure",
            "raw_report": "element-regressions/report.json",
        }
        if args.postgres:
            postgres_elements(args.reference, args.original_processor,
                original_out / "element-regressions/fixture", original_out, os.environ, True)
            classification["create_element"]["postgresql_inherited_document"] = "expected_upstream_failure"
        (original_out / "classification.json").write_text(json.dumps(classification, indent=2) + "\n")
        version = lock["commit"] + "+" + PROFILE
        repaired_out = args.output / "repaired"
        repaired_out.mkdir()
        repaired_args = SimpleNamespace(reference=args.reference, processor=args.adapter_processor,
            adblock=args.adblock, output=repaired_out, postgres=False)
        assert oracle.postprocessors(repaired_args, dict(lock, commit=version)) == 0
        repaired = json.loads((repaired_out / "report.json").read_text())
        repaired.update(profile=PROFILE, reference=lock["commit"], binary_version=version,
                        status="in_progress", postgres_profile_requested=args.postgres)
        (repaired_out / "report.json").write_text(json.dumps(repaired, indent=2) + "\n")
        raw, sources = oracle.fixture(repaired_out / "fixture")
        data = raw.parent
        env = environment(data, args.adblock)
        adblock_rows = oracle.load_rows(repaired_out / "stdout-adblock.stdout")
        assert len(adblock_rows) == 1 and adblock_rows[0][1]["URL"] == "https://tracker.test/a.js"
        assert adblock_rows[0][1]["Blocked"] is True
        repaired["stdout"]["adblock"]["status"] = "passed"
        repaired["limitations"] = ["stdout:poly_features/blobs/idlapis and postgresql:callargs have no reference writer"]
        for mode in ("poly_features", "blobs", "idlapis"):
            repaired["stdout"][mode]["status"] = "not_implemented_by_reference_profile"
        repaired["oracle_error_propagation"] = oracle_failures(
            args.adapter_processor, data, repaired_out / "negative-oracles", True)
        repaired["element_regressions"] = element_stdout(
            args.adapter_processor, repaired_out / "element-regressions", False)
        if args.postgres:
            repaired["postgresql"] = postgres(args.adapter_source, args.adapter_processor,
                                               data, repaired_out, env, sources)
        repaired["status"] = "passed_supported_contracts"
        (repaired_out / "report.json").write_text(json.dumps(repaired, indent=2) + "\n")
        verify_reference(args.reference, lock)
        summary.update(status="passed_supported_contracts", original_status=classification["status"],
            original_expected_failures=sorted(expected),
            original_element_failures=classification["create_element"],
            postgres_profile_executed=args.postgres, adapter_version=version,
            original_binary_sha256=hashlib.sha256(args.original_processor.read_bytes()).hexdigest(),
            adapter_binary_sha256=hashlib.sha256(args.adapter_processor.read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError, ET.ParseError) as error:
        summary["error"] = str(error)
        repaired_report = args.output / "repaired/report.json"
        if repaired_report.exists():
            partial = json.loads(repaired_report.read_text())
            partial.update(profile=PROFILE, status="failed", error=str(error))
            repaired_report.write_text(json.dumps(partial, indent=2) + "\n")
    (args.output / "report.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    raise SystemExit(0 if summary["status"] == "passed_supported_contracts" else 1)


if __name__ == "__main__":
    main()
