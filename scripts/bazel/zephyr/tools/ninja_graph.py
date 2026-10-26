"""Turn a configured CMake/Meson build.ninja into a declared Bazel action graph.

Stage 0 (configure) is an ordinary declared action. Everything after it - every compile,
link, code generator and objcopy edge - becomes its own Bazel action with explicit inputs
and outputs, so Bazel caches and invalidates per translation unit instead of treating
`ninja` as one opaque step. The graph is checked in (generated `graph.bzl`) and a test
regenerates it from a fresh configure to catch drift.

    ninja_graph.py --build-dir B --map /tmp/build=@BUILD@ --map /opt/zephyr=@ZEPHYR@ \
        [--deps ninja-deps.txt] --out graph.json [--bzl graph.bzl] [--strict]

Paths are rewritten through the longest matching --map prefix. Any absolute host path
left in a command is reported under "unmapped": that is an undeclared input (a host tool or
file) and --strict turns it into a failure.
"""

import argparse
import hashlib
import json
import re
import posixpath
import shlex
import sys
from pathlib import Path

HOST_PATH = re.compile(r"(?<![\w@./$-])/(?:opt|tmp|usr|home|root|var|etc|workspace|bin|lib|nix|mnt)\b[^\s\"';&|)]*")


def logical_lines(text):
    """Join `$`-continued lines; the continuation's leading blanks are dropped (ninja syntax)."""
    buffer = ""
    for raw in text.split("\n"):
        line = raw.lstrip() if buffer else raw
        if line.endswith("$") and not line.endswith("$$"):
            buffer += line[:-1]
            continue
        yield buffer + line
        buffer = ""
    if buffer:
        yield buffer


def expand(text, scopes):
    def sub(match):
        token = match.group(0)
        if token == "$$":
            return "$"
        if token in ("$ ", "$:"):
            return token[1]
        name = match.group(1) or match.group(2)
        for scope in scopes:
            if name in scope:
                return scope[name]
        return ""
    return re.sub(r"\$\$|\$ |\$:|\$\{([\w.-]+)\}|\$([\w-]+)", sub, text)


def split_paths(text, scopes):
    parts = []
    for part in re.split(r"(?<!\$) +", text.strip()):
        if part:
            parts.append(expand(part, scopes))
    return parts


class Ninja:
    def __init__(self):
        self.top, self.rules, self.edges = {}, {}, []

    def load(self, path):
        path = Path(path)
        rule = edge = None
        for line in logical_lines(path.read_text()):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if line[0] in " \t":
                key, _, value = stripped.partition("=")
                target = rule["vars"] if rule is not None else edge["vars"] if edge is not None else None
                if target is not None:
                    target[key.strip()] = value.strip()
                continue
            rule = edge = None
            word, _, rest = stripped.partition(" ")
            if word == "rule":
                rule = {"name": rest.strip(), "vars": {}}
                self.rules[rule["name"]] = rule
            elif word in ("include", "subninja"):
                self.load(path.parent / expand(rest.strip(), [self.top]))
            elif word == "build":
                edge = self.parse_build(rest)
                self.edges.append(edge)
            elif word in ("pool", "default"):
                continue
            elif "=" in stripped:
                key, _, value = stripped.partition("=")
                self.top[key.strip()] = expand(value.strip(), [self.top])

    def parse_build(self, rest):
        left, right = re.split(r"(?<!\$): ", rest, maxsplit=1)
        outs, _, implicit_outs = left.partition(" | ")
        rule_and_rest = right.strip().split(" ", 1)
        rule = rule_and_rest[0]
        remainder = rule_and_rest[1] if len(rule_and_rest) > 1 else ""
        pieces = re.split(r"(?<!\$) +", remainder.strip())
        groups = {"explicit": [], "implicit": [], "order_only": []}
        group = "explicit"
        for token in pieces:
            if token == "|":
                group = "implicit"
            elif token == "||":
                group = "order_only"
            elif token:
                groups[group].append(token)
        return {"rule": rule, "outs": outs, "implicit_outs": implicit_outs,
                **{name: " ".join(tokens) for name, tokens in groups.items()}, "vars": {}}

    def resolve(self):
        resolved = []
        for edge in self.edges:
            scopes = [edge["vars"], self.top]
            outs = split_paths(edge["outs"], scopes) + split_paths(edge["implicit_outs"], scopes)
            explicit = split_paths(edge["explicit"], scopes)
            implicit = split_paths(edge["implicit"], scopes)
            order_only = split_paths(edge["order_only"], scopes)
            rule = self.rules.get(edge["rule"], {"vars": {}})
            scope = dict(self.top)
            scope.update(rule["vars"])
            scope.update(edge["vars"])
            scope["in"] = " ".join(shlex.quote(p) for p in explicit)
            scope["out"] = " ".join(shlex.quote(p) for p in split_paths(edge["outs"], scopes))
            command = expand(scope.get("command", ""), [scope])
            resolved.append({"rule": edge["rule"], "command": command, "outputs": outs, "explicit": explicit,
                             "implicit": implicit, "order_only": order_only,
                             "generator": scope.get("generator") == "1", "restat": scope.get("restat") == "1"})
        return resolved


def parse_deps(text):
    """`ninja -t deps` output -> {target: [dep, ...]}."""
    deps, target = {}, None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            target = line.split(":", 1)[0]
            deps[target] = []
        elif line.strip() and target:
            deps[target].append(line.strip())
    return deps


def rewriter(build_dir, mappings):
    ordered = sorted(mappings.items(), key=lambda kv: -len(kv[0]))

    def rewrite(text):
        for prefix, placeholder in ordered:
            text = re.sub(re.escape(prefix) + r"(?![\w.-])", placeholder, text)
        return text

    def path(value):
        absolute = value if value.startswith("/") else posixpath.normpath(str(Path(build_dir) / value))
        return rewrite(absolute)

    return rewrite, path


def build_graph(ninja_file, build_dir, mappings, deps_text="", extra_compile_flag=None, strict=False):
    ninja = Ninja()
    ninja.load(ninja_file)
    rewrite, path = rewriter(build_dir, mappings)
    deps = parse_deps(deps_text)
    edges, unmapped, seen = [], {}, set()
    for raw in ninja.resolve():
        if raw["rule"] == "phony" or raw["generator"] or not raw["command"]:
            continue
        command = rewrite(raw["command"])
        if extra_compile_flag and re.search(r"-g(cc|\+\+)\b", command.split()[0] if command.split() else ""):
            command += " " + extra_compile_flag
        outputs = sorted(path(o) for o in raw["outputs"])
        identifier = "edge_" + hashlib.sha256("\n".join(outputs).encode()).hexdigest()[:12]
        if identifier in seen:
            raise SystemExit("duplicate edge id for outputs %s" % outputs)
        seen.add(identifier)
        discovered = sorted({path(d) for o in raw["outputs"] for d in deps.get(o, [])})
        edge = {"id": identifier, "rule": raw["rule"], "command": command, "outputs": outputs,
                "inputs": sorted(path(p) for p in raw["explicit"]), "implicit": sorted(path(p) for p in raw["implicit"]),
                "order_only": sorted(path(p) for p in raw["order_only"]), "deps": discovered, "restat": 1 if raw["restat"] else 0}
        leftovers = sorted(set(HOST_PATH.findall(command)))
        if leftovers:
            unmapped[identifier] = leftovers
        edges.append(edge)
    edges.sort(key=lambda e: e["outputs"][0])
    graph = {"schema": 1, "map": dict(sorted(mappings.items())), "edges": edges, "unmapped": unmapped}
    if strict and unmapped:
        raise SystemExit("unmapped host paths (undeclared inputs) in %d edges, e.g. %s" % (len(unmapped), next(iter(unmapped.values()))))
    return graph


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--ninja-file")
    parser.add_argument("--map", action="append", default=[], metavar="PREFIX=PLACEHOLDER")
    parser.add_argument("--deps")
    parser.add_argument("--extra-compile-flag")
    parser.add_argument("--out", required=True)
    parser.add_argument("--bzl")
    parser.add_argument("--strict", action="store_true")
    arguments = parser.parse_args()
    mappings = dict(item.split("=", 1) for item in arguments.map)
    ninja_file = arguments.ninja_file or str(Path(arguments.build_dir) / "build.ninja")
    deps_text = Path(arguments.deps).read_text() if arguments.deps else ""
    graph = build_graph(ninja_file, arguments.build_dir, mappings, deps_text, arguments.extra_compile_flag, arguments.strict)
    Path(arguments.out).write_text(json.dumps(graph, indent=1, sort_keys=True) + "\n")
    if arguments.bzl:
        Path(arguments.bzl).write_text("# Generated by ninja_graph.py; do not edit.\nEDGES = " + json.dumps(graph["edges"], indent=1, sort_keys=True) + "\n")
    print(json.dumps({"edges": len(graph["edges"]), "unmapped_edges": len(graph["unmapped"])}), file=sys.stderr)


if __name__ == "__main__":
    main()
