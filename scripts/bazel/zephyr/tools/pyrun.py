"""A `python`-compatible entry point for Zephyr's build, backed by the hermetic interpreter
and the hashed wheels of this module.

CMake's `find_package(Python3)` probes with `-c`, Zephyr runs scripts and `-m` modules, so
this accepts exactly: `-c CODE [ARGS]`, `-m MODULE [ARGS]`, `-V|--version`, `SCRIPT [ARGS]`.
The script's directory leads sys.path (Zephyr scripts import siblings) and it runs as __main__.
"""

import os
import platform
import runpy
import sys


def main():
    arguments = sys.argv[1:]
    if not arguments:
        raise SystemExit("usage: pyrun.py (-c CODE | -m MODULE | -V | SCRIPT) [ARGS...]")
    first = arguments[0]
    if first in ("-V", "--version"):
        print("Python " + platform.python_version())
        return
    if first == "-c":
        sys.argv = ["-c"] + arguments[2:]
        sys.path.insert(0, "")
        exec(compile(arguments[1], "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
        return
    if first == "-m":
        sys.argv = [arguments[1]] + arguments[2:]
        sys.path.insert(0, os.getcwd())
        runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
        return
    script = os.path.abspath(first)
    sys.argv = [script] + arguments[1:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
