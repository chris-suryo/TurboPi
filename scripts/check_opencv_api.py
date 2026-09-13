#!/usr/bin/env python3
"""
check_opencv_api.py - verify the OpenCV API surface TurboPi actually uses.

RUN THIS ON THE PI, with the venv interpreter:

    ~/turbopi-venv/bin/python ~/check_opencv_api.py

Why this exists: TurboPi's code dates from the OpenCV 4.x era. Raspberry Pi OS
(Debian 13) ships OpenCV 5.x, a major version bump that removed and relocated
some symbols. Rather than guess which calls survived, this exercises every cv2
name the codebase references and reports what actually works on your install.

Every symbol below was extracted from the TurboPi source, not invented.
Exit code is non-zero if anything TurboPi needs is missing or broken.
"""

import sys
import traceback

FAILURES = []
WARNINGS = []

_TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s
def hdr(s):  print(f"\n{_c('1', '=== ' + s + ' ===')}")
def ok(s):   print(f"{_c('32','  OK')}      {s}")
def bad(s):  print(f"{_c('31','  FAIL')}    {s}"); FAILURES.append(s)
def warn(s): print(f"{_c('33','  WARN')}    {s}"); WARNINGS.append(s)
def info(s): print(f"          {s}")

try:
    import cv2
    import numpy as np
except ImportError as e:
    print(f"FAIL: cannot import {e.name}. Wrong interpreter? Use "
          f"~/turbopi-venv/bin/python")
    sys.exit(1)

hdr("0. Version")
info(f"OpenCV: {cv2.__version__}")
info(f"NumPy:  {np.__version__}")
info(f"Python: {sys.version.split()[0]}")
major = int(cv2.__version__.split(".")[0])
if major >= 5:
    info(f"OpenCV {major}.x — newer than the 4.x this code was written for.")
    info("That is exactly why this check exists.")

# ---- symbols referenced by the TurboPi source ----
SYMBOLS = [
    # drawing / text
    "resize", "putText", "FONT_HERSHEY_SIMPLEX", "circle", "rectangle",
    "polylines", "drawContours",
    # colour
    "cvtColor", "COLOR_BGR2LAB", "COLOR_BGR2RGB", "inRange",
    # morphology
    "morphologyEx", "getStructuringElement", "MORPH_RECT", "MORPH_OPEN",
    "MORPH_CLOSE", "erode", "dilate", "GaussianBlur",
    # contours
    "findContours", "contourArea", "minAreaRect", "boxPoints",
    "minEnclosingCircle", "RETR_EXTERNAL", "CHAIN_APPROX_NONE",
    "CHAIN_APPROX_TC89_L1",
    # capture / encode
    "VideoCapture", "CAP_PROP_FOURCC", "CAP_PROP_FPS", "CAP_PROP_SATURATION",
    "imencode", "imread", "imwrite", "IMWRITE_JPEG_QUALITY",
    # remap / interpolation
    "remap", "INTER_NEAREST", "INTER_LINEAR", "CV_16SC2", "BORDER_CONSTANT",
    # timing
    "getTickCount", "getTickFrequency",
    # GUI (demos only)
    "imshow", "waitKey", "destroyAllWindows",
]

hdr("1. Symbols TurboPi references")
missing = [s for s in SYMBOLS if not hasattr(cv2, s)]
for s in missing:
    bad(f"cv2.{s} is MISSING")
if not missing:
    ok(f"All {len(SYMBOLS)} referenced cv2 symbols exist.")

hdr("2. cv2.fisheye — runtime path (Camera.py builds these at construction)")
RUNTIME = ["estimateNewCameraMatrixForUndistortRectify", "initUndistortRectifyMap"]
for s in RUNTIME:
    if hasattr(cv2, "fisheye") and hasattr(cv2.fisheye, s):
        ok(f"cv2.fisheye.{s}")
    else:
        bad(f"cv2.fisheye.{s} is MISSING — Camera.py cannot construct")

hdr("2b. cv2.fisheye — re-calibration only (CameraCalibration/Calibration.py)")
info("These are NOT on the runtime path. The repo ships a pre-computed")
info("calibration_param.npz, so the robot works without them. They matter")
info("only if you re-calibrate the camera yourself.")
CALIB = ["calibrate", "CALIB_RECOMPUTE_EXTRINSIC", "CALIB_CHECK_COND",
         "CALIB_FIX_SKEW"]
cal_missing = []
for s in CALIB:
    if hasattr(cv2, "fisheye") and hasattr(cv2.fisheye, s):
        ok(f"cv2.fisheye.{s}")
    else:
        warn(f"cv2.fisheye.{s} is missing — re-calibration only, not the robot")
        cal_missing.append(s)

if cal_missing:
    info("")
    info("What this install actually offers, so a fix can be written:")
    fe = [n for n in dir(cv2.fisheye) if "CALIB" in n] if hasattr(cv2, "fisheye") else []
    info(f"  cv2.fisheye.*CALIB*: {fe or '(none)'}")
    for name in cal_missing:
        if hasattr(cv2, name):
            info(f"  cv2.{name} exists at top level — likely the replacement")
    tl = [n for n in dir(cv2) if "RECOMPUTE" in n or "FIX_SKEW" in n
          or "CHECK_COND" in n]
    info(f"  cv2.* candidates:     {tl or '(none)'}")

hdr("3. VideoWriter_fourcc — the most likely casualty of the 4.x to 5.x bump")
info("Camera.py:39 calls cv2.VideoWriter_fourcc('Y','U','Y','V')")
if hasattr(cv2, "VideoWriter_fourcc"):
    try:
        v = cv2.VideoWriter_fourcc('Y', 'U', 'Y', 'V')
        ok(f"cv2.VideoWriter_fourcc works, returns {v}")
    except Exception as e:                                   # noqa: BLE001
        bad(f"cv2.VideoWriter_fourcc exists but raised: {e}")
else:
    bad("cv2.VideoWriter_fourcc does NOT exist — Camera.py:39 will crash.")
    if hasattr(cv2, "VideoWriter") and hasattr(cv2.VideoWriter, "fourcc"):
        info("  Replacement available: cv2.VideoWriter.fourcc(...)")
        info("  Fix Camera.py:39 to use it.")
    else:
        info("  No obvious replacement found — needs investigation.")

hdr("4. Functional tests on a synthetic frame")
try:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (100, 100), (300, 300), (0, 0, 200), -1)

    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    ok("cvtColor BGR2LAB (the colour space every colour demo uses)")

    blurred = cv2.GaussianBlur(lab, (3, 3), 3)
    mask = cv2.inRange(blurred, (0, 150, 100), (255, 255, 255))
    ok("GaussianBlur + inRange (colour thresholding)")

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, k)
    ok("morphologyEx open/close")

    # TurboPi uses the version-agnostic [-2] idiom - verify it still holds
    res = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours = res[-2]
    info(f"  findContours returned {len(res)} values; [-2] gave "
         f"{len(contours)} contour(s)")
    if len(contours) >= 1 and hasattr(contours[0], "shape"):
        ok("findContours [-2] idiom still resolves to the contour list")
    else:
        bad("findContours [-2] did NOT yield contours — every colour demo breaks")

    a = cv2.contourArea(contours[0])
    rect = cv2.minAreaRect(contours[0])
    box = cv2.boxPoints(rect)
    ok(f"contourArea / minAreaRect / boxPoints (area={int(a)})")

    okf, jpg = cv2.imencode('.jpg', frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if okf and len(jpg.tobytes()) > 0:
        ok(f"imencode JPEG ({len(jpg.tobytes())} bytes) — the MJPEG stream path")
    else:
        bad("imencode returned nothing — MjpgServer would serve empty frames")

    cv2.resize(frame, (320, 240))
    cv2.putText(frame, "t", (10, 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    ok("resize / putText")
except Exception:                                            # noqa: BLE001
    bad("Functional pipeline raised:")
    traceback.print_exc()

hdr("5. Fisheye undistort maps (exactly what Camera.__init__ does)")
try:
    dim = (640, 480)
    k = np.array([[300.0, 0.0, 320.0], [0.0, 300.0, 240.0], [0.0, 0.0, 1.0]])
    d = np.array([[0.0], [0.0], [0.0], [0.0]])
    p = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
        k, d, dim, None).copy()
    m1, m2 = cv2.fisheye.initUndistortRectifyMap(
        k, d, np.eye(3), p, dim, cv2.CV_16SC2)
    test = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.remap(test, m1, m2, interpolation=cv2.INTER_LINEAR,
              borderMode=cv2.BORDER_CONSTANT)
    ok("fisheye map build + remap — Camera.py's correction path works")
except Exception:                                            # noqa: BLE001
    bad("Fisheye path raised — Camera(correction=True) would fail:")
    traceback.print_exc()

hdr("SUMMARY")
if not FAILURES:
    ok("No problems. TurboPi's OpenCV usage is compatible with this install.")
else:
    for f in FAILURES:
        print(f"  - {f}")
    print(f"\n  {len(FAILURES)} problem(s). Paste this output back.")
print("\n  Note: GUI calls (imshow/waitKey) are checked for existence only —")
print("  they need a display to actually run. See docs/06-running-the-demos.md")
sys.exit(1 if FAILURES else 0)
