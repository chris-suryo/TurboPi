#!/usr/bin/env python3
"""
Change what the camera pipeline actually does, reversibly.

Two independent things, both read out of Camera.py rather than assumed:

**The capture resolution is never set.** `camera_open()` sets FOURCC, FPS and saturation
on the device but never `CAP_PROP_FRAME_WIDTH`/`HEIGHT`, so the camera runs at whatever it
defaults to. Every frame is then *downscaled* to Camera.py's `resolution` argument. Asking
for 1280x720 output without also asking the device for it would upscale, which costs CPU
and adds no detail.

**The downscale uses INTER_NEAREST**, the cheapest and worst resampling filter there is --
it point-samples, so it aliases visibly on anything with fine detail. INTER_AREA is the
correct filter for shrinking and costs very little more. This is a free quality win at the
same resolution and the same bandwidth, and it is probably a real part of why the picture
looks soft.

    python3 patch_camera_quality.py --filter area
    python3 patch_camera_quality.py --width 1280 --height 720 --filter area
    python3 patch_camera_quality.py --revert

Requires a TurboPi.py restart. Measure with measure_camera.sh before and after.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys

DEFAULT_PATH = "/home/pi/TurboPi/Camera.py"
BACKUP = ".prepatch.bak"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--filter", choices=["area", "nearest"])
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    try:
        source = open(args.path, encoding="utf-8").read()
    except OSError as exc:
        print(f"FAIL  cannot read {args.path}: {exc}")
        return 1

    if args.revert:
        try:
            shutil.copy2(args.path + BACKUP, args.path)
        except OSError as exc:
            print(f"FAIL  no backup to revert to: {exc}")
            return 1
        print(f"OK    reverted {args.path} from {args.path}{BACKUP}")
        print("      sudo systemctl restart turbopi")
        return 0

    if not (args.width or args.filter):
        print("Nothing to do. Pass --width/--height, --filter, or --revert.")
        return 1
    if bool(args.width) != bool(args.height):
        print("FAIL  --width and --height go together")
        return 1

    patched, changes = source, []

    if args.width:
        new_default = f"def __init__(self, resolution=({args.width}, {args.height})):"
        patched, n = re.subn(r"def __init__\(self, resolution=\(\d+, *\d+\)\):",
                             new_default, patched, count=1)
        if not n:
            print("FAIL  could not find Camera.__init__'s resolution default")
            return 1
        changes.append(f"resize target -> {args.width}x{args.height}")

        # Ask the DEVICE for it too, or we are upscaling from whatever it felt like.
        if "CAP_PROP_FRAME_WIDTH" not in patched:
            anchor = "            self.cap.set(cv2.CAP_PROP_FPS, 30)"
            if anchor not in patched:
                print("FAIL  could not find the capture-property block to extend")
                return 1
            patched = patched.replace(anchor,
                "            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)\n"
                "            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)\n"
                + anchor, 1)
            changes.append("capture resolution now requested from the device")
        else:
            changes.append("capture resolution was already being requested")

    if args.filter:
        want = "cv2.INTER_AREA" if args.filter == "area" else "cv2.INTER_NEAREST"
        patched, n = re.subn(r"interpolation=cv2\.INTER_(NEAREST|AREA)",
                             f"interpolation={want}", patched, count=1)
        if not n:
            print("FAIL  could not find the resize interpolation argument")
            return 1
        changes.append(f"resize filter -> {want.split('.')[-1]}")

    if patched == source:
        print("OK    already in that state; nothing changed")
        return 0

    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print(f"FAIL  patched file would not parse: {exc}")
        return 1

    try:
        open(args.path + BACKUP, encoding="utf-8").read()
    except OSError:
        shutil.copy2(args.path, args.path + BACKUP)   # first patch only: pristine backup

    with open(args.path, "w", encoding="utf-8") as handle:
        handle.write(patched)

    print(f"OK    patched {args.path}")
    for change in changes:
        print(f"        {change}")
    print(f"      backup of the ORIGINAL is at {args.path}{BACKUP}")
    print("      sudo systemctl restart turbopi   # then re-run measure_camera.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
