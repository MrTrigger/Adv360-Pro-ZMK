"""Small executable checks for captured graph syntax and action relocation."""

import os
from pathlib import Path
import pickle
import tempfile
import unittest

from native_action import relocate_pickle, replace_bytes, run
from ninja_graph import Ninja


class NativeToolsTest(unittest.TestCase):
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
