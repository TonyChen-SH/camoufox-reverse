#!/usr/bin/env python3
"""Export VisibleV8's idldata.json from this Firefox build's own WebIDL provider."""

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
import tempfile


FIREFOX_COMMIT = "9378fbc58660847fbc02c3486a0ecd6049978e25"
PROVIDER_BLOBS = {
    "dom/bindings/Configuration.py": "b56ffde5c3b4c2913c5cf39f802e32ecc3b47976",
    "dom/bindings/parser/WebIDL.py": "dede4572a188b3269934ef88eafecaededa375e9",
    "dom/bindings/mozwebidlcodegen/__init__.py": "f15aeba11d3c9fcd0e9c00ee1b99ceb7e6b9cffa",
}
ARRAY_ROLES = {
    "staticMethods": "methods", "staticAttrs": "properties",
    "methods": "methods", "attrs": "properties",
    "unforgeableMethods": "methods", "unforgeableAttrs": "properties",
    "consts": "properties",
}
FILE_LIST_KEYS = {"webidls", "exported_stems", "generated_events_stems", "example_interfaces"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def verify_provider(source, baseline):
    """Pin provider internals; a Firefox upgrade needs a reviewed adapter."""
    if baseline["firefox"]["commit"] != FIREFOX_COMMIT:
        raise ValueError("Unsupported Firefox revision in baseline")
    expected = dict(PROVIDER_BLOBS)
    expected["dom/bindings/Codegen.py"] = baseline["files"]["dom/bindings/Codegen.py"]
    blobs = {}
    for name, accepted in expected.items():
        blob = git_blob(source / name)
        allowed = set(accepted.values()) if isinstance(accepted, dict) else {accepted}
        if blob not in allowed:
            raise ValueError(f"Unknown Firefox provider content: {name} ({blob})")
        blobs[name] = blob
    return blobs


def import_provider(source):
    # These are existing Firefox source dependencies, also present in mach's site.
    for relative in ("xpcom/ds/tools", "third_party/python/ply",
                     "dom/bindings/parser", "dom/bindings"):
        sys.path.insert(0, str(source / relative))
    modules = {}
    for name, relative in (("WebIDL", "dom/bindings/parser/WebIDL.py"),
                           ("Configuration", "dom/bindings/Configuration.py"),
                           ("Codegen", "dom/bindings/Codegen.py")):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve() != source / relative:
            raise ValueError(f"{name} was imported from a different Firefox tree")
        modules[name] = module
    return modules["Codegen"]


def build_inputs(source, objdir):
    file_list = objdir / "dom/bindings/file-lists.json"
    files = json.loads(file_list.read_text(encoding="utf-8"))
    if not isinstance(files, dict) or set(files) != FILE_LIST_KEYS:
        raise ValueError("Unknown Firefox file-lists.json shape")
    for key, values in files.items():
        if (not isinstance(values, list) or not all(isinstance(v, str) and v for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"Invalid build input list: {key}")
    if not files["webidls"]:
        raise ValueError("Firefox build lists no WebIDL inputs")
    inputs = []
    for name in files["webidls"]:
        path = Path(name)
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"Missing exported WebIDL input: {name}; run mach build export")
        path = path.resolve()
        for root, label in ((objdir, "objdir"), (source, "source")):
            if path.is_relative_to(root):
                inputs.append({"path": f"{label}/{path.relative_to(root).as_posix()}",
                               "sha256": sha256(path)})
                break
        else:
            raise ValueError(f"WebIDL input outside source and object trees: {name}")
    return file_list, sorted(inputs, key=lambda item: item["path"])


def _name(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError(f"Unexpected Firefox binding name: {value!r}")
    return value


def _class_name(generated, symbol):
    # Read the actual provider output, including ClassString and iterator overrides.
    pattern = (r'\A\s*static const DOMIfaceAndProtoJSClass ' + re.escape(symbol)
               + r'\s*=\s*\{\s*\{\s*("(?:[^"\\]|\\.)*")\s*,')
    match = re.match(pattern, generated)
    if match is None:
        raise ValueError(f"Unknown Firefox JSClass output for {symbol}")
    return _name(json.loads(match[1]))


def extract(config, codegen):
    """Use parsed/merged descriptors and the original generator's property arrays."""
    if set(codegen.PropertyArrays.arrayNames()) != set(ARRAY_ROLES):
        raise ValueError("Unknown Firefox PropertyArrays shape")
    descriptors = {}
    for descriptor in config.getDescriptors():
        name = _name(descriptor.name)
        if name in descriptors:
            raise ValueError(f"Duplicate Firefox descriptor: {name}")
        descriptors[name] = descriptor
    if not descriptors:
        raise ValueError("No Firefox interface descriptors")

    tree, aliases, constructors = {}, {}, {}
    for name, descriptor in sorted(descriptors.items()):
        iface = descriptor.interface
        parent = iface.parent.identifier.name if iface.parent else None
        if parent is not None and parent not in descriptors:
            raise ValueError(f"Unresolved Firefox parent: {name} -> {parent}")
        roles = {"methods": set(), "properties": set()}
        properties = codegen.PropertyArrays(descriptor)
        for array, role in ARRAY_ROLES.items():
            definer = getattr(properties, array)
            for audience in ("regular", "chrome"):
                values = getattr(definer, audience)
                if not isinstance(values, list):
                    raise ValueError(f"Unknown property list: {name}.{array}.{audience}")
                for value in values:
                    if array == "consts":
                        if not value.isConst():
                            raise ValueError(f"Unknown constant entry: {name}")
                        roles[role].add(_name(value.identifier.name))
                        continue
                    if not isinstance(value, dict) or "name" not in value:
                        raise ValueError(f"Unknown property entry: {name}.{array}")
                    roles[role].add(_name(value["name"]))
        for member in iface.members:
            if member.isMethod():
                roles["methods"].update(_name(alias) for alias in member.aliases)
        if iface.hasInterfaceObject() and iface.hasInterfacePrototypeObject():
            # BindingUtils links these objects with JS_LinkConstructorAndPrototype.
            roles["properties"].update(("constructor", "prototype"))
        alias_names = set(iface.legacyWindowAliases)
        factories = [_name(factory.identifier.name) for factory in iface.legacyFactoryFunctions]
        alias_names.update(factories)
        if iface.isNamespace():
            alias_names.add(_class_name(codegen.CGNamespaceObjectJSClass(descriptor).define(),
                                        "sNamespaceObjectClass"))
        else:
            alias_names.add(_name(iface.getClassName()))
            if iface.hasInterfacePrototypeObject():
                alias_names.add(_class_name(codegen.CGPrototypeJSClass(descriptor, properties).define(),
                                            "sPrototypeClass"))
        alias_names.discard(name)
        for alias in sorted(alias_names):
            alias = _name(alias)
            if alias in descriptors or (alias in aliases and aliases[alias] != name):
                raise ValueError(f"Ambiguous Firefox class/constructor alias: {alias}")
            aliases[alias] = name
        tree[name] = {"parent": parent, "aliasFor": None, "aliases": sorted(alias_names),
                      "methods": sorted(roles["methods"]), "properties": sorted(roles["properties"]),
                      "members": sorted(roles["methods"] | roles["properties"])}
        constructors[name] = {"constructor": iface.ctor() is not None,
                              "legacy_factories": sorted(factories)}

    # Global registration is separate from IDL members. Use the provider's exact
    # Window name table, and concrete exposure sets for registered other globals.
    if config.windowGlobalNames and "Window" not in tree:
        raise ValueError("Window registration table has no Window descriptor")
    if "Window" in tree:
        for name, descriptor in config.windowGlobalNames:
            if descriptor.name not in descriptors:
                raise ValueError(f"Unknown Window registration descriptor: {descriptor.name}")
            tree["Window"]["properties"].append(_name(name))
    for descriptor in config.getDescriptors(hasInterfaceObject=True, register=True):
        for global_name in descriptor.interface.exposureSet:
            if global_name != "Window" and global_name in descriptors:
                if not descriptors[global_name].isGlobal():
                    raise ValueError(f"Exposure target is not a global: {global_name}")
                tree[global_name]["properties"].append(descriptor.name)
    for name, row in tree.items():
        visited = {name}
        parent = row["parent"]
        while parent:
            if parent in visited:
                raise ValueError(f"Firefox inheritance cycle: {name}")
            visited.add(parent)
            parent = tree[parent]["parent"]
        row["properties"] = sorted(set(row["properties"]))
        row["members"] = sorted(set(row["methods"]) | set(row["properties"]))
    for alias, canonical in sorted(aliases.items()):
        # Some original Go modes do not follow aliasFor; retain the real member
        # lists and parent as well so those modes can query native class names.
        row = tree[canonical]
        tree[alias] = {**row, "aliasFor": canonical, "aliases": []}
    return tree, descriptors, constructors


def crosscheck(inventory, descriptors, tree, codegen):
    """Reject generated hooks that cannot be explained by this build's provider."""
    if (not isinstance(inventory, dict) or inventory.get("schema") != 1
            or inventory.get("coverage") != "generated-webidl-binding-entries"
            or not isinstance(inventory.get("entries"), list) or not inventory["entries"]):
        raise ValueError("Unknown or empty binding-inventory shape")
    allowed = set()
    for name, descriptor in descriptors.items():
        iface = descriptor.interface
        for member in iface.members:
            if member.isAttr() or member.isMethod():
                scope = "static" if member.isStatic() else "instance"
                member_name = codegen.GetWebExposedName(member, descriptor)
                operations = ("get", "set") if member.isAttr() else ("call",)
                allowed.update((name, member_name, op, scope) for op in operations)
        if iface.ctor():
            allowed.add((name, "constructor", "construct", "constructor"))
        for factory in iface.legacyFactoryFunctions:
            allowed.add((name, factory.identifier.name, "construct", "constructor"))
    seen, internal, sites = set(), [], 0
    for entry in inventory["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("Unknown binding-inventory entry")
        key = tuple(entry.get(field) for field in ("interface", "member", "op", "scope"))
        if not all(isinstance(item, str) and item for item in key):
            raise ValueError("Invalid binding-inventory operation")
        if key in seen or key not in allowed or entry.get("api") != f"{key[0]}.{key[1]}":
            raise ValueError(f"Unmatched or duplicate generated binding: {key}")
        count = entry.get("generated_sites")
        if type(count) is not int or count < 1:
            raise ValueError(f"Invalid generated site count: {key}")
        seen.add(key)
        sites += count
        if key[3] != "constructor" and key[1] not in tree[key[0]]["members"]:
            internal.append(entry["api"])
    counts = inventory.get("counts", {})
    if (counts.get("logical_entries") != len(seen) or counts.get("generated_sites") != sites
            or inventory.get("interfaces") != sorted({key[0] for key in seen})
            or inventory.get("api_names") != sorted({f"{key[0]}.{key[1]}" for key in seen})):
        raise ValueError("Inconsistent binding-inventory totals")
    return {"logical_entries": len(seen), "generated_sites": sites,
            "binding_only_api_names": sorted(set(internal))}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, indent=2, sort_keys=True, ensure_ascii=True)
            output.write("\n")
        temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--objdir", type=Path, required=True)
    parser.add_argument("--mozconfig", type=Path, required=True)
    parser.add_argument("--binding-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline", type=Path,
                        default=Path(__file__).resolve().parents[1] / "baseline.json")
    args = parser.parse_args(argv)
    try:
        source, objdir = args.source.resolve(), args.objdir.resolve()
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        blobs = verify_provider(source, baseline)
        file_list, inputs = build_inputs(source, objdir)
        config_status = objdir / "config.status"
        configuration = {"mozconfig_sha256": sha256(args.mozconfig),
                         "config_status_sha256": sha256(config_status),
                         "bindings_conf_sha256": sha256(source / "dom/bindings/Bindings.conf"),
                         "file_lists_sha256": sha256(file_list)}
        codegen = import_provider(source)
        try:
            provider = importlib.import_module("mozwebidlcodegen")
        except ImportError as error:
            raise ValueError("Firefox build Python environment is required; run with ./mach python") from error
        if Path(provider.__file__).resolve() != source / "dom/bindings/mozwebidlcodegen/__init__.py":
            raise ValueError("mozwebidlcodegen was imported from a different Firefox tree")
        manager = provider.create_build_system_manager(
            topsrcdir=str(source), topobjdir=str(objdir), dist_dir=str(objdir / "dist"))
        tree, descriptors, constructors = extract(manager.config, codegen)
        checked = crosscheck(json.loads(args.binding_inventory.read_text(encoding="utf-8")),
                             descriptors, tree, codegen)
        manifest = {"schema": 1, "coverage": "configured-firefox-webidl-declarations",
                    "firefox_commit": FIREFOX_COMMIT,
                    "camoufox_commit": baseline["camoufox"]["commit"],
                    "provider_git_blobs": blobs, "configuration": configuration,
                    "inputs": inputs, "binding_inventory_sha256": sha256(args.binding_inventory),
                    "crosscheck": checked, "constructors": constructors,
                    "counts": {"interfaces": len(descriptors), "names": len(tree),
                               "aliases": len(tree) - len(descriptors)},
                    "limitations": [
                        "Build declarations include ChromeOnly and conditional bindings; runtime prefs, realms and principals still govern availability.",
                        "Original Go schema combines static and instance members and has no exposure-condition fields.",
                        "Non-WebIDL SpiderMonkey builtins, custom native classes and application properties are outside this inventory.",
                        "Generic Proxy objects are not aliases of Window; the producer must identify WindowProxy natively."]}
        output_manifest = args.manifest or args.output.with_name(args.output.stem + "-manifest.json")
        if output_manifest.resolve() == args.output.resolve():
            raise ValueError("IDL output and manifest must be different files")
        # All parsing and cross-checking finish before an existing output is replaced.
        write_json(args.output, tree)
        manifest["idldata_sha256"] = sha256(args.output)
        write_json(output_manifest, manifest)
    except (OSError, ValueError, KeyError, AttributeError, TypeError, ImportError) as error:
        parser.error(str(error))
    print(json.dumps(manifest["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
