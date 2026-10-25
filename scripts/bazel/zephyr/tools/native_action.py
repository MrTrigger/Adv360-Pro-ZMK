"""Run one declared firmware action in a private, relocatable build layout."""

import json
import os
from pathlib import Path
import pickletools
import shutil
import struct
import subprocess
import sys
import tempfile

from capture_graph import extract


def replace_bytes(data, mappings):
    if b'\0' in data:
        return data
    try:
        value = data.decode('utf-8')
    except UnicodeDecodeError:
        return data
    for old, new in sorted(mappings.items(), key=lambda item: -len(item[0])):
        value = value.replace(old, new)
    return value.encode('utf-8')


def relocate_pickle(data, mappings):
    """Relocate serialized strings without importing or executing pickle objects.

    Frames contain byte lengths, so drop them when rewriting strings. Protocol
    four and five streams remain valid without optional FRAME opcodes. Memo
    references and the object graph are preserved verbatim.
    """
    operations = list(pickletools.genops(data))
    result = bytearray()
    for index, (opcode, value, start) in enumerate(operations):
        end = operations[index + 1][2] if index + 1 < len(operations) else len(data)
        if opcode.name == 'FRAME':
            continue
        if opcode.name in {'SHORT_BINUNICODE', 'BINUNICODE', 'BINUNICODE8', 'UNICODE'}:
            changed = replace_bytes(value.encode('utf-8'), mappings)
            result.extend(b'X' + struct.pack('<I', len(changed)) + changed)
        else:
            result.extend(data[start:end])
    return bytes(result)


def relocate(data, mappings, logical):
    return relocate_pickle(data, mappings) if logical.endswith('.pickle') else replace_bytes(data, mappings)


def local_path(logical, roots):
    for token, root in roots.items():
        logical = logical.replace(token, root)
    return Path(logical)


def run(spec):
    execroot = Path.cwd()
    original_roots = {token: str(execroot / path) for token, path in spec['roots'].items()}
    tools = {token: str(execroot / path) for token, path in spec['tools'].items()}
    with tempfile.TemporaryDirectory(prefix='zephyr-action-') as directory:
        work = Path(directory)
        build = work / 'build'
        build.mkdir()
        roots = {'@WORK@': str(work), '@BUILD@': str(build), '@CONFIG@': str(work / 'config'),
                 '@SDK@': original_roots['@SDK@'], **tools}
        for token, relative in spec['project_paths'].items():
            roots[token] = str(work / 'inputs' / relative)
        roots['@APP@'] = roots['@SRC_ZMK@'] + '/app'
        shim = work / 'tools'
        shim.mkdir()
        launcher = '#!/bin/sh\nexec /usr/bin/python3 ' + tools['@PYTHON@'] + ' "$@"\n'
        for name in ['python', 'python3']:
            path = shim / name
            path.write_text(launcher)
            path.chmod(0o755)
        (shim / 'protoc').symlink_to(tools['@PROTOC@'])
        roots['@PYTHON@'] = str(shim / 'python')
        for filename, logical in spec['inputs']:
            token, relative = logical.split('/', 1)
            if token == '@SDK@':
                continue
            target = local_path(logical, roots)
            target.parent.mkdir(parents=True, exist_ok=True)
            original = execroot / filename
            if token == '@BUILD@':
                target.write_bytes(relocate(original.read_bytes(), roots, logical))
                target.chmod(original.stat().st_mode & 0o777)
            elif spec['mode'] == 'configure':
                shutil.copyfile(original, target)
            else:
                target.symlink_to(original)
        for _, logical in spec['outputs']:
            local_path(logical, roots).parent.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, HOME=str(work / 'home'), USER_CACHE_DIR=str(work / 'cache'),
                           PATH=str(shim) + ':/usr/bin:/bin')
        # A nested py_binary must discover its own wheels and interpreter.
        for variable in ('RUNFILES_DIR', 'RUNFILES_MANIFEST_FILE', 'JAVA_RUNFILES',
                         'PYTHON_RUNFILES', 'PYTHONPATH', 'PYTHONHOME'):
            environment.pop(variable, None)
        Path(environment['HOME']).mkdir()
        if spec['mode'] == 'configure':
            environment.update(ZEPHYR_BASE=roots['@SRC_ZEPHYR@'], ZEPHYR_TOOLCHAIN_VARIANT='zephyr',
                               ZEPHYR_SDK_INSTALL_DIR=roots['@SDK@'])
            side, flavor = spec['variant'].split('_')
            modules = [roots[token] for token in spec['project_order'] if token not in ['@SRC_ZMK@', '@SRC_ZEPHYR@']]
            command = [tools['@CMAKE@'], '-S', roots['@APP@'], '-B', str(build), '-GNinja',
                       '-DBOARD=adv360_' + side, '-DZMK_CONFIG=' + roots['@CONFIG@'],
                       '-DZephyr_DIR=' + roots['@SRC_ZEPHYR@'] + '/share/zephyr-package/cmake',
                       '-DUSER_CACHE_DIR=' + str(work / 'cache'), '-DZEPHYR_BASE=' + roots['@SRC_ZEPHYR@'],
                       '-DZEPHYR_SDK_INSTALL_DIR=' + roots['@SDK@'], '-DZEPHYR_MODULES=' + ';'.join(modules),
                       '-DPython3_EXECUTABLE=' + roots['@PYTHON@'], '-DDTC=' + tools['@DTC@'],
                       '-DGPERF=' + tools['@GPERF@'], '-DCMAKE_MAKE_PROGRAM=' + tools['@NINJA@'],
                       '-DPROTOBUF_PROTOC_EXECUTABLE=' + tools['@PROTOC@'], '-DBUILD_VERSION=dacab4875df7']
            if side == 'left' and flavor == 'studio':
                command += ['-DSNIPPET=studio-rpc-usb-uart', '-DCONFIG_ZMK_STUDIO=y']
            subprocess.run(command, env=environment, cwd=build, check=True)
            layout = dict(build=str(build), app=roots['@APP@'], config=roots['@CONFIG@'], sdk=roots['@SDK@'],
                          sdk_real=str((Path(roots['@SDK@']) / 'sdk_version').resolve().parent),
                          paths={name: roots[token] for name, token in spec['project_names'].items()},
                          tools={token.strip('@').lower(): roots[token] for token in tools},
                          firmware=['zephyr/zmk.' + ext for ext in ['elf', 'hex', 'uf2']])
            expected = spec['plan']
            actual = extract(build, layout, {}, {}, '', plan_only=True)
            if actual != expected:
                for left, right in zip(actual, expected):
                    if left != right:
                        print('Configured graph differs:', json.dumps(dict(actual=left, expected=right)), file=sys.stderr)
                        break
                raise RuntimeError('Firmware graph is stale; recapture the configured Ninja plan and dependencies')
        else:
            command = spec['command']
            for token, value in roots.items():
                command = command.replace(token, value)
            subprocess.run(command, env=environment, cwd=build, shell=True, check=True)
        normalizations = {value: token for token, value in roots.items()}
        normalizations[str((Path(roots['@SDK@']) / 'sdk_version').resolve().parent)] = '@SDK@'
        for filename, logical in spec['outputs']:
            original = local_path(logical, roots)
            if not original.is_file() or original.is_symlink():
                raise RuntimeError('Firmware action did not create a regular output: ' + logical)
            target = execroot / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relocate(original.read_bytes(), normalizations, logical))
            target.chmod(original.stat().st_mode & 0o777)


if __name__ == '__main__':
    run(json.loads(Path(sys.argv[1]).read_text()))
