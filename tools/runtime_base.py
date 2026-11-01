#!/usr/bin/env python3
"""Resolve and authenticate an explicitly pinned runtime composition base."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from update_runtime_asset import digest_inventory, inventory, sha256, verify_composition, write_receipt


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Artifact redirect must remain HTTPS")
        return super().redirect_request(request, response, code, message, headers, newurl)


def api_request(path):
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "camoufox-runtime-base",
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
    })
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=60)


def api_json(path):
    with api_request(path) as response:
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("GitHub metadata exceeds the byte limit")
    return json.loads(data)


def validate_config(config):
    if (config.get("schema") != 1 or
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.get("repository", "")) or
            type(config.get("run_id")) is not int or config["run_id"] <= 0 or
            not re.fullmatch(r"[0-9a-f]{40}", config.get("head_sha", "")) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", config.get("artifact_name", ""))):
        raise ValueError("Invalid explicit runtime base configuration")


def select_artifact(config, run, artifacts):
    validate_config(config)
    if (run.get("id") != config["run_id"] or run.get("head_sha") != config["head_sha"] or
            run.get("repository", {}).get("full_name") != config["repository"] or
            run.get("path") != ".github/workflows/native-browser.yml"):
        raise ValueError("Runtime base run does not match its pinned repository, workflow and source")
    matches = [item for item in artifacts if item.get("name") == config["artifact_name"] and
               item.get("expired") is False]
    if len(matches) != 1:
        raise ValueError("Expected exactly one unexpired runtime base artifact")
    artifact = matches[0]
    workflow = artifact.get("workflow_run", {})
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
            workflow.get("id") != config["run_id"] or workflow.get("head_sha") != config["head_sha"] or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest") or "")):
        raise ValueError("Runtime base artifact identity or digest differs")
    return {"schema": 1, "base": config,
            "artifact": {key: artifact[key] for key in ("id", "name", "digest", "workflow_run")}}


def resolve(config):
    validate_config(config)
    prefix = f"/repos/{config['repository']}/actions/runs/{config['run_id']}"
    run = api_json(prefix)
    artifacts = []
    for page in range(1, 101):
        batch = api_json(f"{prefix}/artifacts?per_page=100&page={page}")["artifacts"]
        artifacts.extend(batch)
        if len(batch) < 100:
            return select_artifact(config, run, artifacts)
    raise ValueError("Runtime base artifact pagination exceeds the limit")


def artifact_stream(selection):
    config, artifact = selection["base"], selection["artifact"]
    validate_config(config)
    path = f"/repos/{config['repository']}/actions/artifacts/{int(artifact['id'])}/zip"
    try:
        with api_request(path):
            raise ValueError("Expected GitHub artifact redirect")
    except urllib.error.HTTPError as response:
        if response.code != 302:
            raise
        location = response.headers.get("Location", "")
        if urllib.parse.urlsplit(location).scheme != "https":
            raise ValueError("Artifact download URL must be HTTPS")
    # This is a fresh unauthenticated request. Never forward the API bearer
    # token to signed storage URLs or their redirects.
    request = urllib.request.Request(location, headers={"User-Agent": "camoufox-runtime-base"})
    return urllib.request.build_opener(HTTPSRedirect()).open(request, timeout=120)


def authenticate_zip(selection, archive, downloaded):
    if hasattr(archive, "read"):
        archive.seek(0)
        actual = hashlib.file_digest(archive, "sha256").hexdigest()
        archive.seek(0)
    else:
        actual = sha256(archive)
    if "sha256:" + actual != selection["artifact"]["digest"]:
        raise ValueError("Runtime base artifact ZIP digest mismatch")
    expected = {}
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                raise ValueError("Unsafe runtime base artifact member")
            if member.is_dir():
                continue
            name = path.as_posix()
            if name in expected or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Duplicate or symlink runtime base artifact member")
            digest = hashlib.sha256()
            with zipped.open(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            expected[name] = digest.hexdigest()
    observed = inventory(downloaded)
    files = {path: entry["sha256"] for path, entry in observed.items() if entry["kind"] == "file"}
    if not expected or files != expected:
        raise ValueError("Officially downloaded files differ from the authenticated ZIP")
    return {"schema": 1, "selection": selection, "verified_zip_sha256": actual,
            "verified_members_sha256": expected,
            "download_inventory_sha256": digest_inventory(observed)}


def authenticate(selection, downloaded):
    with tempfile.TemporaryFile() as archive:
        with artifact_stream(selection) as response:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                archive.write(chunk)
        archive.seek(0)
        return authenticate_zip(selection, archive, downloaded)


def write_checksums(package):
    entries = inventory(package)
    lines = []
    for path, entry in sorted(entries.items()):
        if entry["kind"] != "file" or path == "SHA256SUMS":
            continue
        if "\n" in path or "\r" in path or "\\" in path:
            raise ValueError("Unsupported checksum path")
        lines.append(f"{entry['sha256']}  {path}\n")
    (package / "SHA256SUMS").write_text("".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--config", type=Path, required=True)
    select.add_argument("--selection", type=Path, required=True)
    select.add_argument("--github-output", type=Path, required=True)
    check = commands.add_parser("authenticate")
    check.add_argument("--selection", type=Path, required=True)
    check.add_argument("--downloaded", type=Path, required=True)
    check.add_argument("--receipt", type=Path, required=True)
    checksums = commands.add_parser("checksums")
    checksums.add_argument("--package", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--target-baseline", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "select":
        if args.config.exists():
            selection = resolve(json.loads(args.config.read_text()))
            write_receipt(args.selection, selection)
            output = {"enabled": "true", "repository": selection["base"]["repository"],
                      "head_sha": selection["base"]["head_sha"], "run_id": selection["base"]["run_id"],
                      "artifact_id": selection["artifact"]["id"]}
        else:
            output = {"enabled": "false"}
        with args.github_output.open("a") as destination:
            destination.write("".join(f"{key}={value}\n" for key, value in output.items()))
    elif args.command == "authenticate":
        selection = json.loads(args.selection.read_text())
        write_receipt(args.receipt, authenticate(selection, args.downloaded))
    elif args.command == "verify":
        verify_composition(args.package, args.receipt, args.target_baseline)
        print(json.dumps({"status": "composition_verified"}))
    else:
        write_checksums(args.package)


if __name__ == "__main__":
    main()
