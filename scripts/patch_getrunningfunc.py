#!/usr/bin/env python3
"""
Fix a vendor bug: GetRunningFunc can never succeed.

RPCServer.py has:

    @dispatcher.add_method
    def GetRunningFunc():
        return runbymainth("GetRunningFunc", ())

runbymainth's first act is `if callable(req)`. A string is not callable, so this returns
(False, "E05 - Not callable") every single time it is called. There is no code path in
which it works.

Functions/Running.py already has the function it was meant to reach:

    def getLoadedFunc(newf):
        return (True, (RunningFunc,))

which is callable, takes the params tuple the main-thread queue passes, and returns the
envelope the caller expects. So the fix is to point at it.

Why this matters here: the gateway's "409 demo_running" guard needs to know whether a
built-in demo is driving the motors. Without this patch it cannot know, says so in its
logs and in /telemetry's demo_detection field, and leaves the guard off rather than
pretending no demo is ever running.

    python3 patch_getrunningfunc.py [--path /home/pi/TurboPi/RPCServer.py]

Idempotent. Writes a .bak the first time. Requires a TurboPi.py restart to take effect.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys

OLD = 'return runbymainth("GetRunningFunc", ())'
NEW = "return runbymainth(Running.getLoadedFunc, ())"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/home/pi/TurboPi/RPCServer.py")
    args = parser.parse_args()

    try:
        source = open(args.path, encoding="utf-8").read()
    except OSError as exc:
        print(f"FAIL  cannot read {args.path}: {exc}")
        return 1

    if NEW in source:
        print(f"OK    already patched: {args.path}")
        return 0

    if OLD not in source:
        print(f"FAIL  did not find the line to patch in {args.path}")
        print(f"      looking for: {OLD}")
        print("      Refusing to guess. Upstream may have fixed or moved it -- check by hand.")
        return 1

    if "import Functions.Running as Running" not in source:
        print("FAIL  RPCServer.py does not import Functions.Running as Running")
        print("      The replacement references Running.getLoadedFunc, so it would NameError.")
        return 1

    patched = source.replace(OLD, NEW, 1)

    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print(f"FAIL  patched file would not parse: {exc}")
        return 1

    shutil.copy2(args.path, args.path + ".bak")
    with open(args.path, "w", encoding="utf-8") as handle:
        handle.write(patched)

    print(f"OK    patched {args.path} (backup at {args.path}.bak)")
    print("      GetRunningFunc now returns the loaded demo id instead of E05.")
    print("      Restart TurboPi.py for this to take effect:")
    print("        sudo systemctl restart turbopi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
