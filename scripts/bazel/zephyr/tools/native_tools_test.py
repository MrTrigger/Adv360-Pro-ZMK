"""Small executable checks for captured graph syntax and action relocation."""

import os
from pathlib import Path
import pickle
import shutil
import tempfile
import unittest

from capture_graph import extract
from native_action import relocate_pickle, replace_bytes, run
from ninja_graph import Ninja


class NativeToolsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('gcc'), 'Real GCC is needed for prefix-map precedence')
    def test_relocated_compilation_preserves_zephyr_file_macro_names(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            capture = root / 'capture'
            capture.mkdir()
            (capture / 'build.ninja').write_text(
                'rule cc\n  command = /sdk/bin/gcc -E -P -fmacro-prefix-map=/inputs/zephyr=ZEPHYR_BASE '
                '/inputs/zephyr/example.c -o /capture/build/example.i\n'
                'build example.i: cc /inputs/zephyr/example.c\n')
            layout = dict(build='/capture/build', app='/inputs/zmk/app', config='/workspace/config', sdk='/sdk',
                          paths={'zmk': '/inputs/zmk', 'zephyr': '/inputs/zephyr'}, tools={}, firmware=['example.i'])
            graph = extract(capture, layout, {}, {'files': []}, '')
            (root / 'example.c').write_text('const char *source = __FILE__;\n')
            spec = dict(mode='edge', roots={'@SDK@': str(Path(shutil.which('gcc')).parent.parent)},
                        tools={'@PYTHON@': '/unused/python', '@PROTOC@': '/unused/protoc'},
                        project_paths={'@SRC_ZMK@': 'zmk', '@SRC_ZEPHYR@': 'zephyr'},
                        project_names={'zmk': '@SRC_ZMK@', 'zephyr': '@SRC_ZEPHYR@'},
                        command=graph['edges'][0]['command'], inputs=[['example.c', '@SRC_ZEPHYR@/example.c']],
                        outputs=[['result.i', '@BUILD@/example.i']])
            previous = Path.cwd()
            try:
                os.chdir(root)
                run(spec)
            finally:
                os.chdir(previous)
            self.assertEqual((root / 'result.i').read_text(), 'const char *source = "ZEPHYR_BASE/example.c";\n')

    def test_source_file_named_only_in_a_generator_command_is_declared(self):
        with tempfile.TemporaryDirectory() as name:
            build = Path(name)
            (build / 'build.ninja').write_text('rule version\n  command = cat /inputs/zmk/app/VERSION > /capture/build/version.h\nbuild version.h: version\n')
            layout = dict(build='/capture/build', app='/inputs/zmk/app', config='/workspace/config', sdk='/sdk',
                          paths={'zmk': '/inputs/zmk'}, tools={}, firmware=['version.h'])
            graph = extract(build, layout, {}, {'files': [], 'source_files': ['@APP@/VERSION']}, '')
            self.assertEqual(graph['edges'][0]['inputs'], ['@APP@/VERSION'])

    def test_copied_generator_directory_declares_its_plugin_and_imports(self):
        with tempfile.TemporaryDirectory() as name:
            build = Path(name)
            (build / 'build.ninja').write_text(
                'rule copy\n  command = /tools/cmake -E copy_directory /inputs/nanopb/generator/ /capture/build/generator\n'
                'build generator/main.py: copy\n'
                'rule use\n  command = /capture/build/generator/plugin > /capture/build/zmk.uf2\n'
                'build zmk.uf2: use generator/main.py\n')
            layout = dict(build='/capture/build', app='/inputs/zmk/app', config='/workspace/config', sdk='/sdk',
                          paths={'zmk': '/inputs/zmk', 'nanopb': '/inputs/nanopb'},
                          tools={'cmake': '/tools/cmake'}, firmware=['zmk.uf2'])
            source_files = ['@SRC_NANOPB@/generator/main.py', '@SRC_NANOPB@/generator/plugin', '@SRC_NANOPB@/generator/helper.py']
            graph = extract(build, layout, {}, {'files': [], 'source_files': source_files}, '')
            copied, consumer = graph['edges']
            self.assertEqual(copied['outputs'], ['@BUILD@/generator/helper.py', '@BUILD@/generator/main.py', '@BUILD@/generator/plugin'])
            self.assertEqual(copied['inputs'], sorted(source_files))
            self.assertEqual(consumer['inputs'], copied['outputs'])

    def test_generated_tools_keep_executable_permissions_between_actions(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'tool.sh').write_text('#!/bin/sh\nprintf "firmware answer\\n"\n')
            (root / 'tool.sh').chmod(0o755)
            spec = dict(mode='edge', roots={'@SDK@': 'sdk'}, tools={'@PYTHON@': '/unused/python', '@PROTOC@': '/unused/protoc'},
                        project_paths={'@SRC_ZMK@': 'zmk'}, project_names={'zmk': '@SRC_ZMK@'},
                        command='cp @APP@/tool.sh @BUILD@/tool.sh',
                        inputs=[['tool.sh', '@APP@/tool.sh']], outputs=[['generated.sh', '@BUILD@/tool.sh']])
            previous = Path.cwd()
            try:
                os.chdir(root)
                run(spec)
                spec.update(command='@BUILD@/tool.sh > @BUILD@/answer', inputs=[['generated.sh', '@BUILD@/tool.sh']],
                            outputs=[['answer', '@BUILD@/answer']])
                run(spec)
            finally:
                os.chdir(previous)
            self.assertEqual((root / 'answer').read_bytes(), b'firmware answer\n')

    def test_order_only_without_explicit_inputs(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'build.ninja'
            path.write_text('rule generate\n  command = echo ready > $out\nbuild result: generate || prerequisite\n')
            graph = Ninja()
            graph.load(path)
            edge, = graph.resolve()
            self.assertEqual(edge['explicit'], [])
            self.assertEqual(edge['order_only'], ['prerequisite'])
            self.assertEqual(edge['command'], 'echo ready > result')

    def test_generated_input_is_relocated_without_modification(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            original = root / 'source.h'
            original.write_text('#define ROOT "@BUILD@"\n')
            original.chmod(0o444)
            spec = dict(mode='edge', roots={'@SDK@': 'sdk'}, tools={'@PYTHON@': '/unused/python', '@PROTOC@': '/unused/protoc'},
                        project_paths={'@SRC_ZMK@': 'zmk'}, project_names={'zmk': '@SRC_ZMK@'},
                        command='cat @BUILD@/source.h > @BUILD@/result.h',
                        inputs=[['source.h', '@BUILD@/source.h']],
                        outputs=[['result.h', '@BUILD@/result.h']])
            previous = Path.cwd()
            try:
                os.chdir(root)
                run(spec)
            finally:
                os.chdir(previous)
            self.assertEqual((root / 'result.h').read_bytes(), b'#define ROOT "@BUILD@"\n')
            self.assertEqual(original.read_bytes(), b'#define ROOT "@BUILD@"\n')
            self.assertEqual(original.stat().st_mode & 0o777, 0o444)

    def test_nested_source_tokens_in_object_paths_are_relocated(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'main.c').write_text('firmware input\n')
            logical = '@BUILD@/objects/@SRC_ZMK@/app/main.c.obj'
            spec = dict(mode='edge', roots={'@SDK@': 'sdk'}, tools={'@PYTHON@': '/unused/python', '@PROTOC@': '/unused/protoc'},
                        project_paths={'@SRC_ZMK@': 'zmk'}, project_names={'zmk': '@SRC_ZMK@'},
                        command='cat @APP@/main.c > ' + logical,
                        inputs=[['main.c', '@APP@/main.c']], outputs=[['result.obj', logical]])
            previous = Path.cwd()
            try:
                os.chdir(root)
                run(spec)
                spec.update(command='cat ' + logical + ' > @BUILD@/linked.bin',
                            inputs=[['result.obj', logical]], outputs=[['linked.bin', '@BUILD@/linked.bin']])
                run(spec)
            finally:
                os.chdir(previous)
            self.assertEqual((root / 'result.obj').read_bytes(), b'firmware input\n')
            self.assertEqual((root / 'linked.bin').read_bytes(), b'firmware input\n')

    def test_binary_output_is_never_rewritten(self):
        self.assertEqual(replace_bytes(b'ELF\0/tmp/example', {'/tmp': '@WORK@'}), b'ELF\0/tmp/example')

    def test_app_can_read_declared_configuration_headers(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'main.c').write_text('/* app */\n')
            (root / 'shared.h').write_text('#define SHARED_VALUE 42\n')
            spec = dict(mode='edge', roots={'@SDK@': 'sdk'}, tools={'@PYTHON@': '/unused/python', '@PROTOC@': '/unused/protoc'},
                        project_paths={'@SRC_ZMK@': 'zmk'}, project_names={'zmk': '@SRC_ZMK@'},
                        command='cat @CONFIG@/shared.h > @BUILD@/result.h',
                        inputs=[['main.c', '@APP@/main.c'], ['shared.h', '@CONFIG@/shared.h']],
                        outputs=[['result.h', '@BUILD@/result.h']])
            previous = Path.cwd()
            try:
                os.chdir(root)
                run(spec)
            finally:
                os.chdir(previous)
            self.assertEqual((root / 'result.h').read_bytes(), b'#define SHARED_VALUE 42\n')

    def test_pickle_relocation_preserves_string_paths_cycles_and_shared_references(self):
        original = {'path': '/old/build/device', 'source': '/old/source/board.dts'}
        original['cycle'] = original
        original['shared'] = [original['path'], original['path']]
        for protocol in (4, 5):
            portable = relocate_pickle(pickle.dumps(original, protocol=protocol), {'/old': '@WORK@'})
            restored = pickle.loads(relocate_pickle(portable, {'@WORK@': '/new'}))
            self.assertEqual(restored['path'], '/new/build/device')
            self.assertEqual(restored['source'], '/new/source/board.dts')
            self.assertIs(restored['cycle'], restored)
            self.assertIs(restored['shared'][0], restored['shared'][1])


if __name__ == '__main__':
    unittest.main()
