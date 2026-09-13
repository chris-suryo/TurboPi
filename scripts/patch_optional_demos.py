#!/usr/bin/env python3
"""
patch_optional_demos.py - stop one unusable dependency from killing the robot.

RUN ON THE PI:
    ~/turbopi-venv/bin/python ~/patch_optional_demos.py
    ~/turbopi-venv/bin/python ~/patch_optional_demos.py --revert

THE PROBLEM
    RPCServer.py imports Functions.FaceTracking and Functions.GestureRecognition
    at module scope. Both do `mediapipe.solutions...` at import time. If the
    installed mediapipe lacks the legacy `solutions` API - which is the case on
    Python 3.13, where pip resolves a restructured build - the import raises and
    TurboPi.py cannot start AT ALL. No camera, no RPC, no robot.

    The cost of that is zero functionality, because neither module is reachable:
    Functions/Running.py's FUNCTIONS table (the only thing LoadFunc can launch)
    contains RemoteControl, ColorDetect, ColorTracking, VisualPatrol, QuickMark,
    Avoidance and lab_adjust. FaceTracking and GestureRecognition are not in it.
    They are imported, handed a `board` reference in set_board(), and never used.

THE FIX
    Make those two imports optional and guard their board assignments. Every
    demo that actually works keeps working; a broken mediapipe becomes a warning
    instead of a fatal error.

    Idempotent, backs up the original, and reversible with --revert.
"""

import argparse
import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/home/pi/TurboPi/RPCServer.py")
BACKUP = TARGET.with_suffix(".py.orig")

OLD_IMPORTS = """import Functions.FaceTracking as FaceTracking_
import Functions.GestureRecognition as GestureRecognition_"""

NEW_IMPORTS = '''# --- optional: these two need mediapipe's legacy `solutions` API, which is
# --- absent from recent builds. Neither appears in Functions/Running.py's
# --- FUNCTIONS table, so they are not launchable and losing them costs nothing.
try:
    import Functions.FaceTracking as FaceTracking_
except Exception as _e:                       # noqa: BLE001
    FaceTracking_ = None
    print(f"[optional] FaceTracking unavailable ({type(_e).__name__}: {_e})")
try:
    import Functions.GestureRecognition as GestureRecognition_
except Exception as _e:                       # noqa: BLE001
    GestureRecognition_ = None
    print(f"[optional] GestureRecognition unavailable ({type(_e).__name__}: {_e})")'''

OLD_ASSIGN = """    FaceTracking_.board = board
    GestureRecognition_.board = board"""

NEW_ASSIGN = """    if FaceTracking_ is not None:
        FaceTracking_.board = board
    if GestureRecognition_ is not None:
        GestureRecognition_.board = board"""


def revert():
    if not BACKUP.exists():
        print(f"No backup at {BACKUP} - nothing to revert.")
        return 1
    shutil.copy2(BACKUP, TARGET)
    print(f"Restored {TARGET} from {BACKUP}")
    return 0


def apply():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Is the source cloned there?")
        return 1

    src = TARGET.read_text()

    if "optional] FaceTracking unavailable" in src:
        print("Already patched - nothing to do.")
        return 0

    missing = [name for name, frag in
               (("imports", OLD_IMPORTS), ("board assignments", OLD_ASSIGN))
               if frag not in src]
    if missing:
        print(f"ERROR: could not find the {', '.join(missing)} to patch.")
        print("The file may already differ from the upstream version.")
        return 1

    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"Backed up original to {BACKUP}")

    patched = src.replace(OLD_IMPORTS, NEW_IMPORTS).replace(OLD_ASSIGN, NEW_ASSIGN)

    try:
        ast.parse(patched)
    except SyntaxError as e:
        print(f"ERROR: patched file does not parse ({e}). Nothing written.")
        return 1

    TARGET.write_text(patched)
    print(f"Patched {TARGET}")
    print()
    print("Now start the robot:")
    print("  cd /home/pi/TurboPi")
    print("  ~/turbopi-venv/bin/python TurboPi.py")
    print()
    print("Expect two '[optional] ... unavailable' lines, then the servers start.")
    print("Those lines are the patch working, not a failure.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revert", action="store_true",
                    help="restore RPCServer.py from the backup")
    args = ap.parse_args()
    sys.exit(revert() if args.revert else apply())
