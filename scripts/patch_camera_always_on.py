#!/usr/bin/env python3
"""
patch_camera_always_on.py - make the camera stream work without a demo loaded.

RUN ON THE PI:
    ~/turbopi-venv/bin/python ~/patch_camera_always_on.py
    ~/turbopi-venv/bin/python ~/patch_camera_always_on.py --revert
    sudo systemctl restart turbopi

THE PROBLEM
    TurboPi.py constructs Camera.Camera() but never calls camera_open(). The
    only code that opens the camera is Functions/Running.py's loadFunc(), which
    runs when you load a demo. So a freshly started TurboPi.py produces no
    frames at all: cam.frame stays None, MjpgServer.img_show stays None, and
    MjpgServer's snapshot branch - which does nothing when img_show is None,
    not even sending a status line - closes the connection with no response.
    curl reports that as HTTP 000, which reads like the server is down.

    Worse, Running.unloadFunc() calls camera_close(), so the stream also dies
    whenever a demo is unloaded.

    That behaviour is fine for Hiwonder's own app, where you always load a demo.
    It is wrong for treating the robot as a camera source that another machine
    can just connect to.

THE FIX
    1. Open the camera at startup.
    2. Reopen it in the main loop if something closed it.

    Demos still work: loadFunc() closes and reopens the camera as before.

    Idempotent, backs up the original, reversible.
"""

import argparse
import ast
import pathlib
import shutil
import sys

TARGET = pathlib.Path("/home/pi/TurboPi/TurboPi.py")
BACKUP = TARGET.with_suffix(".py.orig")

OLD_INIT = """    cam = Camera.Camera()  # 相机读取(camera reading)
    Running.cam = cam"""

NEW_INIT = """    cam = Camera.Camera()  # 相机读取(camera reading)
    Running.cam = cam
    # Open the camera immediately. Upstream only opens it inside
    # Running.loadFunc(), so without this the MJPEG server serves nothing until
    # a demo is loaded - and the snapshot endpoint returns no HTTP response at
    # all, which looks like the server being down.
    cam.camera_open()"""

OLD_LOOP = """            else:
                MjpgServer.img_show = cam.frame"""

NEW_LOOP = """            else:
                # Running.unloadFunc() closes the camera. Reopen it so the
                # stream survives a demo being unloaded.
                if not cam.opened:
                    cam.camera_open()
                MjpgServer.img_show = cam.frame"""


def revert():
    if not BACKUP.exists():
        print(f"No backup at {BACKUP} - nothing to revert.")
        return 1
    shutil.copy2(BACKUP, TARGET)
    print(f"Restored {TARGET}")
    print("Then: sudo systemctl restart turbopi")
    return 0


def apply():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found.")
        return 1
    src = TARGET.read_text()

    if "Open the camera immediately" in src:
        print("Already patched - nothing to do.")
        return 0

    missing = [n for n, frag in (("startup block", OLD_INIT), ("main loop", OLD_LOOP))
               if frag not in src]
    if missing:
        print(f"ERROR: could not find the {', '.join(missing)} to patch.")
        return 1

    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"Backed up original to {BACKUP}")

    patched = src.replace(OLD_INIT, NEW_INIT).replace(OLD_LOOP, NEW_LOOP)
    try:
        ast.parse(patched)
    except SyntaxError as e:
        print(f"ERROR: patched file does not parse ({e}). Nothing written.")
        return 1

    TARGET.write_text(patched)
    print(f"Patched {TARGET}")
    print()
    print("Restart the service and test:")
    print("  sudo systemctl restart turbopi")
    print("  sleep 5")
    print("  curl -s -o /dev/null -w 'HTTP %{http_code}\\n' \\")
    print("       'http://127.0.0.1:8080/?action=snapshot'")
    print()
    print("Want HTTP 200. The camera now streams with no demo loaded.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    sys.exit(revert() if a.revert else apply())
