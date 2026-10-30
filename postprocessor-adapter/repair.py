#!/usr/bin/env python3
"""Generate a separately identified repair profile from pinned VisibleV8 source."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))
from build_visiblev8_reference import LOCK, verify_reference

PROFILE = "camoufox-adapter.2"
PATHS = ("post-processor/features/features.go", "post-processor/causality/causality.go",
         "post-processor/adblock/main.go", "post-processor/fptp/main.go",
         "post-processor/elements/elements.go",
         "post-processor/postgres_schema.sql")


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Pinned postprocessor repair anchor is missing or ambiguous")
    return text.replace(before, after, 1)


def transform(path, text):
    if path.endswith("elements/elements.go"):
        text = replace_once(text,
            "\t\toffset, err := strconv.Atoi(fields[0])\n",
            "\t\tif len(fields) < 3 {\n\t\t\treturn fmt.Errorf(\"%d: incomplete native call record\", lineNumber)\n\t\t}\n\t\toffset, err := strconv.Atoi(fields[0])\n")
        return replace_once(text,
            '\t\tif fullName == "HTMLDocument.createElement" {\n\t\t\ttagName, ok := core.StripQuotes(fields[3])',
            '\t\tif fullName == "Document.createElement" || fullName == "HTMLDocument.createElement" {\n\t\t\tif len(fields) < 4 {\n\t\t\t\treturn nil\n\t\t\t}\n\t\t\ttagName, ok := core.StripQuotes(fields[3])')
    if path.endswith("fptp/main.go"):
        return replace_once(text, '''\t\t\tagg.firstPartyProperty.DisplayName,
\t\t\tscriptProperty.DisplayName,
\t\t\toriginProperty.DisplayName,
\t\t\tscriptProperty.DisplayName != originProperty.DisplayName,
\t\t\tscriptProperty.DisplayName != agg.firstPartyProperty.DisplayName,
''', '''\t\t\tagg.firstPartyProperty.DisplayName,
\t\t\toriginProperty.DisplayName,
\t\t\tscriptProperty.DisplayName,
\t\t\tscriptProperty.DisplayName != agg.firstPartyProperty.DisplayName,
\t\t\tscriptProperty.DisplayName != originProperty.DisplayName,
''')
    if path.endswith("features/features.go"):
        return replace_once(text,
            "\t\t_, err = stmt.Exec(logID, visitDomain, script.CodeHash.SHA2[:], nullableURL, nullableParentHash, script.Isolate.ID, script.ID, script.FirstOrigin)",
            "\t\tvar nullableOrigin interface{}\n\t\tif script.FirstOrigin != nil {\n\t\t\tnullableOrigin = script.FirstOrigin.Origin\n\t\t}\n\t\t_, err = stmt.Exec(logID, visitDomain, script.CodeHash.SHA2[:], nullableURL, nullableParentHash, script.Isolate.ID, script.ID, nullableOrigin)")
    if path.endswith("causality/causality.go"):
        text = replace_once(text,
            'pq.CopyIn("script_causality_graphml", scriptCausalityFields[:]...)',
            'pq.CopyIn("script_causality_graphml", scriptCausalityGraphMLFields[:]...)')
        return replace_once(text, '''\tstmt.Exec(
\t\tuuid.New(),
\t\tbuf,
\t)
''', '''\t_, err = stmt.Exec(uuid.New().String(), buf.String())
\tif err != nil {
\t\ttxn.Rollback()
\t\treturn err
\t}
''')
    if path.endswith("adblock/main.go"):
        text = replace_once(text, '\t"encoding/json"\n', '\t"encoding/json"\n\t"fmt"\n')
        text = text.replace("`json:url`", '`json:"url"`').replace("`json:origin`", '`json:"origin"`')
        text = text.replace("`json:blocked`", '`json:"blocked"`')
        text = replace_once(text, '''\tvar tmp ScriptURLPair
\terr := json.Unmarshal(ln, &tmp)
\treturn &tmp, err
''', '''\tvar payload struct {
\t\tURL string `json:"url"`
\t\tOrigin string `json:"origin"`
\t\tBlocked *bool `json:"blocked"`
\t}
\tif err := json.Unmarshal(ln, &payload); err != nil {
\t\treturn nil, err
\t}
\tif payload.URL == "" || payload.Origin == "" || payload.Blocked == nil {
\t\treturn nil, fmt.Errorf("adblock result requires url, origin and blocked")
\t}
\treturn &ScriptURLPair{URL: payload.URL, Origin: payload.Origin, Blocked: *payload.Blocked}, nil
''')
        text = replace_once(text, '\tdefer adblockProc.Wait()\n', '')
        text = replace_once(text, '\tjstreamAdblock := json.NewEncoder(file)\n',
                            '\tdefer os.Remove(agg.adblockTmpFilename)\n\tjstreamAdblock := json.NewEncoder(file)\n')
        text = replace_once(text, '''\tgo agg.readFromAdblock(sc, cnt)

\treturn nil
''', '''\tif err := agg.readFromAdblock(sc, cnt); err != nil {
\t\tadblockProc.Process.Kill()
\t\tadblockProc.Wait()
\t\treturn err
\t}
\treturn adblockProc.Wait()
''')
        text = replace_once(text, '\tfor cnt > 0 && sc.Scan() {\n', '\tfor sc.Scan() {\n')
        text = replace_once(text, '''\t\tagg.urlPairList = append(agg.urlPairList, scriptURLPair)
\t\tcnt--
\t}

\treturn nil
''', '''\t\tif cnt == 0 {
\t\t\treturn fmt.Errorf("adblock returned unexpected extra results")
\t\t}
\t\tagg.urlPairList = append(agg.urlPairList, scriptURLPair)
\t\tcnt--
\t}
\tif err := sc.Err(); err != nil {
\t\treturn fmt.Errorf("adblock output: %w", err)
\t}
\tif cnt != 0 {
\t\treturn fmt.Errorf("adblock returned %d fewer results than requested", cnt)
\t}
\treturn nil
''')
        return text
    if path.endswith("postgres_schema.sql"):
        text = replace_once(text, "\t'static',          -- No parent", "\t'iframe',          -- Native iframe assignment heuristic\n\t'static_iframe',   -- Inferred static iframe origin\n\t'static',          -- No parent")
        return text + '''
-- Standalone adapter prerequisites otherwise supplied by the original VPC database.
CREATE TABLE IF NOT EXISTS submissions (
    id UUID PRIMARY KEY,
    url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idlapis (
    id SERIAL PRIMARY KEY,
    api TEXT NOT NULL,
    script_list TEXT[] NOT NULL,
    log_file_id INT REFERENCES logfile(id),
    root_domain TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS script_causality_graphml (
    id UUID PRIMARY KEY,
    xml TEXT NOT NULL
);
'''
    raise ValueError(f"Unsupported repair source: {path}")


def create(reference, output, repaired=True):
    lock = json.loads(LOCK.read_text())
    verify_reference(reference, lock)
    if output.exists():
        raise ValueError("Repair output must be a new directory")
    listing = subprocess.check_output(["git", "-C", str(reference), "ls-tree", "-rz", "HEAD", "--", "post-processor"])
    files = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, kind, _ = metadata.decode().split()
        path = path_bytes.decode()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError(f"Unsupported source entry {path}")
        data = subprocess.check_output(["git", "-C", str(reference), "show", "HEAD:" + path])
        files[path] = (data, int(mode, 8) & 0o777)
    manifest = {"schema": 1, "original_commit": lock["commit"],
                "profile": PROFILE if repaired else "original-owned-test-copy",
                "binary_version": lock["commit"] + ("+" + PROFILE if repaired else ""), "repairs": {}}
    patch = []
    for path in PATHS if repaired else ():
        original = files[path][0]
        updated = transform(path, original.decode()).encode()
        manifest["repairs"][path] = {"before_sha256": hashlib.sha256(original).hexdigest(),
                                     "after_sha256": hashlib.sha256(updated).hexdigest()}
        files[path] = (updated, files[path][1])
        patch.extend(difflib.unified_diff(original.decode().splitlines(keepends=True),
                                         updated.decode().splitlines(keepends=True),
                                         fromfile="a/" + path, tofile="b/" + path))
    output.mkdir(parents=True)
    for path, (data, mode) in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    (output / "LICENSE.visiblev8").write_bytes(subprocess.check_output([
        "git", "-C", str(reference), "show", "HEAD:LICENSE"]))
    (output / "adapter-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "repairs.patch").write_text("".join(patch))
    verify_reference(reference, lock)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pristine-copy", action="store_true")
    args = parser.parse_args()
    try:
        result = create(args.reference.resolve(), args.output.resolve(), not args.pristine_copy)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps({"profile": result["profile"], "version": result["binary_version"],
                      "repaired_files": len(result["repairs"])}))


if __name__ == "__main__":
    main()
