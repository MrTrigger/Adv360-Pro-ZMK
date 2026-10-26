#!/usr/bin/env python3
"""Write the keyboard version macro from explicit cacheable build metadata."""

import argparse
from datetime import datetime
from pathlib import Path


def render(date, branch, commit, flavor):
    datetime.strptime(date, "%Y%m%d")
    if len(date) != 8:
        raise ValueError("Build date must be YYYYMMDD")
    tokens = []
    for index, value in enumerate((date, branch, commit, flavor)):
        if index:
            tokens.append("MINUS")
        for char in value:
            if char.isascii() and char.isalpha():
                tokens.append(char.upper())
            elif char in "0123456789":
                tokens.append("N" + char)
            elif char == ".":
                tokens.append("DOT")
    bindings = ", ".join("<&kp " + token + ">" for token in [*tokens, "RET"])
    return ('#define VERSION_MACRO\nmacro_ver: macro_ver {\n'
            'compatible = "zmk,behavior-macro";\n#binding-cells = <0>;\n'
            f'bindings = {bindings};\n}};\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--flavor", choices=[".", "clique"], default=".")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = render(args.date, args.branch, args.commit, args.flavor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload)


if __name__ == "__main__":
    main()
