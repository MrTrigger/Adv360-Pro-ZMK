"""Extract the firmware closure from a real configured and built Ninja graph."""

import argparse
import hashlib
import json
import posixpath
import re
import shlex
from pathlib import Path

from ninja_graph import Ninja, parse_deps


def extract(build, layout, configurations, inventory, deps, plan_only=False):
    mappings = {layout['build']: '@BUILD@', layout['app']: '@APP@', layout['config']: '@CONFIG@', layout['sdk']: '@SDK@'}
    mappings.update({path: '@SRC_' + name.upper().replace('-', '_') + '@'
                     for name, path in layout['paths'].items()})
    if 'sdk_real' in layout:
        mappings[layout['sdk_real']] = '@SDK@'
    mappings.update({path: '@' + name.upper() + '@' for name, path in layout['tools'].items()})

    def rewrite(value):
        for prefix, token in sorted(mappings.items(), key=lambda item: -len(item[0])):
            value = value.replace(prefix, token)
        return value

    def path(value):
        absolute = value if value.startswith('/') else layout['build'] + '/' + value
        return rewrite(posixpath.normpath(absolute))

    ninja = Ninja()
    ninja.load(Path(build) / 'build.ninja')
    raw = ninja.resolve()
    owners = {}
    for index, edge in enumerate(raw):
        for output in edge['outputs']:
            key = path(output)
            assert key not in owners or owners[key] == index, ('duplicate owner', key)
            owners[key] = index

    selected = set()
    active = set()

    def visit(value):
        index = owners.get(value)
        if index is None or index in selected:
            return
        assert index not in active, ('Ninja cycle', value)
        edge = raw[index]
        # Ninja's directory watchers exist to detect source additions. Bazel's
        # declared source tree replaces those triggers and their directory links.
        if 'subfolder_list.py' in edge['command'] or '/syscalls_links/' in value:
            return
        active.add(index)
        for source in edge['explicit'] + edge['implicit'] + edge['order_only']:
            visit(path(source))
        active.remove(index)
        selected.add(index)

    for output in layout['firmware']:
        visit('@BUILD@/' + output)

    def leaves(value, seen=None):
        seen = set() if seen is None else seen
        assert value not in seen, ('phony cycle', value)
        index = owners.get(value)
        if index is None:
            return {value}
        edge = raw[index]
        if 'subfolder_list.py' in edge['command'] or '/syscalls_links/' in value:
            return set()
        if edge['rule'] != 'phony':
            return {value}
        result = set()
        for source in edge['explicit'] + edge['implicit'] + edge['order_only']:
            result.update(leaves(path(source), seen | {value}))
        return result

    plan = sorted([dict(outputs=sorted({path(value) for value in raw[index]['outputs']}),
                        command=re.sub(r' && @CMAKE@ -E cmake_transform_depfile .*$', '',
                                       rewrite(raw[index]['command'])))
                   for index in selected if raw[index]['rule'] != 'phony'],
                  key=lambda entry: entry['outputs'][0])
    if plan_only:
        return plan

    # Ninja can name only a subset of files copied as a directory. Preserve
    # that entire tool boundary, including plugins and their Python imports.
    source_files = {posixpath.normpath(value) for value in inventory.get('source_files', [])}
    copies = {}
    for index in selected:
        match = re.search(r'@CMAKE@ -E copy_directory (\S+) (\S+)', rewrite(raw[index]['command']))
        if match:
            source, destination = (posixpath.normpath(value) for value in match.groups())
            members = {destination + value[len(source):]: value for value in source_files if value.startswith(source + '/')}
            assert members, ('directory copy requires source_files inventory', source)
            copies[index] = (destination, members)
    all_outputs = {path(output) for index in selected for output in raw[index]['outputs']}
    all_outputs.update(output for _, members in copies.values() for output in members)

    discovered = parse_deps(deps)
    for filename, text in inventory.get('depfiles', {}).items():
        tokens = shlex.split(text.replace('\\\n', ' ').partition(':')[2])
        # Linker preprocessing runs from build/zephyr, unlike compilation.
        target = '@BUILD@/' + filename.removesuffix('.dep')
        discovered[target] = [posixpath.normpath(layout['build'] + '/zephyr/' + token)
                              if not token.startswith('/') else token for token in tokens]

    edges = []
    for index in sorted(selected):
        edge = raw[index]
        if edge['rule'] == 'phony' or not edge['command']:
            continue
        assert not edge['generator'], ('unexpected generator', edge['outputs'])
        outputs = {path(output) for output in edge['outputs']}
        command = rewrite(edge['command'])
        if index in copies:
            outputs.update(copies[index][1])
        # gen_syscalls has additional outputs absent from Ninja's output list.
        if 'gen_syscalls.py' in command:
            outputs.update('@BUILD@/' + filename for filename in inventory['files']
                           if '/syscalls/' in filename)
            outputs.update('@BUILD@/' + filename for filename in inventory['files']
                           if filename.endswith('/syscall_list.h'))
        inputs = set()
        for source in edge['explicit'] + edge['implicit'] + edge['order_only']:
            inputs.update(leaves(path(source)))
        for output in edge['outputs']:
            inputs.update(path(source) for source in discovered.get(output, []))
            inputs.update(path(source) for source in discovered.get(path(output), []))
        if index in copies:
            inputs.update(copies[index][1].values())
        for logical in re.findall(r'@(?:BUILD|APP|CONFIG|SRC_[A-Z0-9_]+)@/[^\s";]+', command):
            logical = posixpath.normpath(logical)
            if logical in source_files:
                inputs.add(logical)
        for producer, (directory, _) in copies.items():
            if producer != index and directory + '/' in command:
                inputs.update(output for output in all_outputs if output.startswith(directory + '/'))
        inputs -= outputs
        if 'check_init_priorities.py' in command:
            # The checker derives this pathname from the ELF directory rather
            # than naming it in Ninja's dependencies or its command line.
            inputs.add('@BUILD@/zephyr/edt.pickle')
        command = re.sub(r' && @CMAKE@ -E cmake_transform_depfile .*$', '', command)
        if re.search(r'@SDK@/[^ ]*g(?:cc|\+\+) ', command):
            # Keep fallback macro mappings in the same GCC option family as
            # Zephyr's later, more specific source mappings. -ffile-prefix-map
            # overrides those mappings regardless of their argument order.
            command = re.sub(r'(@SDK@/[^ ]*g(?:cc|\+\+) )',
                             r'\1-fmacro-prefix-map=@WORK@=. -fdebug-prefix-map=@WORK@=. ', command)
        identifier = 'edge_' + hashlib.sha256('\n'.join(sorted(outputs)).encode()).hexdigest()[:12]
        edges.append(dict(id=identifier, rule=edge['rule'], command=command,
                          outputs=sorted(outputs), inputs=sorted(inputs)))
    edges.sort(key=lambda edge: edge['outputs'][0])
    output_paths = [output for edge in edges for output in edge['outputs']]
    assert len(output_paths) == len(set(output_paths)), 'duplicate native output'
    output_set = set(output_paths)
    required = {source for edge in edges for source in edge['inputs'] if source.startswith('@BUILD@/')}
    configured = sorted(required - output_set)
    available = {'@BUILD@/' + name for name in configurations}
    assert set(configured) <= available, ('unowned build inputs', sorted(set(configured) - available))
    for edge in edges:
        assert all(re.match(r'^@(BUILD|APP|CONFIG|SDK|SRC_[A-Z0-9_]+)@/', value)
                   for value in edge['inputs'] + edge['outputs']), edge['id']
        assert not re.search(r'/(capture|workspace|state|inputs)/', edge['command']), edge['command']
    return dict(schema=1, plan=plan, configured=configured, edges=edges)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('capture', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bzl', type=Path)
    args = parser.parse_args()
    root = args.capture
    inventory = json.loads((root / 'INVENTORY.json').read_text())
    if (root / 'SOURCE-FILES.json').is_file():
        inventory['source_files'] = json.loads((root / 'SOURCE-FILES.json').read_text())
    graph = extract(root / 'build', json.loads((root / 'LAYOUT.json').read_text()),
                    json.loads((root / 'CONFIGURE-FILES.json').read_text()),
                    inventory, (root / 'deps.txt').read_text())
    args.output.write_text(json.dumps(graph, indent=2) + '\n')
    if args.bzl:
        args.bzl.write_text('# Generated from the actual pinned firmware capture.\nGRAPH = ' + json.dumps(graph, indent=2) + '\n')
    print(json.dumps({'edges': len(graph['edges']), 'configured': len(graph['configured'])}))


if __name__ == '__main__':
    main()
