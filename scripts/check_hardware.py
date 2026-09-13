#!/usr/bin/env python3
"""
check_hardware.py - probe the interfaces TurboPi actually uses.

RUN THIS ON THE PI, over SSH.  Safe to run before assembly (it only reads).

    python3 check_hardware.py
    python3 check_hardware.py --i2c-scan     # also scan the I2C bus for devices

TurboPi does NOT use Raspberry Pi PWM or RPi.GPIO. It uses exactly three things,
and this script checks each one:

    UART  /dev/ttyAMA0 @ 1000000 baud -> the expansion board's microcontroller,
                                         which drives ALL motors and servos
    I2C   bus 1, address 0x77         -> the ultrasonic sensor (and its RGB LEDs)
    gpiod                             -> the board's LED and buttons only

Design rule: never guess. Anything undeterminable is reported as UNKNOWN.
"""

import argparse
import glob
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------- presentation
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s

def hdr(s):   print(f"\n{_c('1', '=== ' + s + ' ===')}")
def ok(s):    print(f"{_c('32','  OK')}      {s}")
def warn(s):  print(f"{_c('33','  WARN')}    {s}")
def bad(s):   print(f"{_c('31','  PROBLEM')} {s}")
def unk(s):   print(f"  UNKNOWN {s}")
def info(s):  print(f"          {s}")

FINDINGS = []
def finding(s): FINDINGS.append(s)


def run(cmd):
    """Run a command, returning (rc, stdout+stderr). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except Exception as e:                                  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def read_text(path):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip("\x00\n ")
    except OSError:
        return None


# ------------------------------------------------------------------ 0. identity
def check_identity():
    hdr("0. Board identity")
    model = read_text("/proc/device-tree/model")
    if model:
        info(f"Model:  {model}")
    else:
        unk("/proc/device-tree/model unreadable - probably not a Raspberry Pi.")
    info(f"Kernel: {os.uname().release}")
    try:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    except Exception:                                       # noqa: BLE001
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or "?"
    info(f"User:   {user}   Home: {os.path.expanduser('~')}")

    # HiwonderSDK/mecanum.py hardcodes sys.path.append('/home/pi/TurboPi/')
    if user != "pi":
        warn(f"You are '{user}', not 'pi'. HiwonderSDK/mecanum.py hardcodes")
        warn("  sys.path.append('/home/pi/TurboPi/') - imports will fail elsewhere.")
        finding(f"Running as '{user}'; TurboPi's SDK expects user 'pi' and /home/pi/TurboPi.")
    return model or ""


# ----------------------------------------------------------------- 1. gpiochips
def check_gpio():
    hdr("1. GPIO chips (used ONLY for the board LED and buttons)")
    chips = sorted(glob.glob("/dev/gpiochip*"))
    if not chips:
        bad("No /dev/gpiochip* devices at all.")
        finding("No gpiochip devices present.")
        return
    info(f"Present: {', '.join(os.path.basename(c) for c in chips)}")

    # Raspberry Pi ships a udev rule that creates /dev/gpiochip4 as a backward
    # compatibility SYMLINK to the real RP1 chip. A node existing under that name
    # therefore does not mean the kernel numbered it that way - resolve it.
    links = {}
    for c in chips:
        real = os.path.realpath(c)
        if real != c:
            links[os.path.basename(c)] = os.path.basename(real)
    for name, target in sorted(links.items()):
        info(f"  {name} -> {target}  (symlink)")

    # Map each chip to its label so we can find the RP1 (the 40-pin header on Pi 5).
    labels = {}
    for c in chips:
        n = os.path.basename(c)
        lbl = read_text(f"/sys/class/gpio/{n}/label") or \
              read_text(f"/sys/bus/gpio/devices/{n}/label")
        if lbl:
            labels[n] = lbl
    if not labels:
        rc, out = run(["gpiodetect"])
        if rc == 0:
            for line in out.splitlines():
                m = re.match(r"(gpiochip\d+)\s+\[([^\]]+)\]", line)
                if m:
                    labels[m.group(1)] = m.group(2)
        else:
            unk("gpiodetect unavailable (sudo apt install gpiod) and sysfs labels absent.")

    for n, lbl in sorted(labels.items()):
        info(f"  {n:<12} {lbl}")

    rp1 = [n for n, lbl in labels.items() if "rp1" in lbl.lower()]
    if not labels:
        unk("Could not determine chip labels - cannot verify the gpiochip4 assumption.")
        finding("gpiochip labels unreadable; the led.py/key.py gpiochip4 risk is unverified.")
        return

    if rp1:
        name = rp1[0]
        info(f"RP1 (40-pin header) is: {name}")

        # What would gpiod.Chip('gpiochip4') actually open?
        if name == "gpiochip4":
            ok("Matches the 'gpiochip4' hardcoded in HiwonderSDK/led.py and key.py.")
        elif links.get("gpiochip4") == name:
            ok(f"/dev/gpiochip4 is a compatibility symlink to {name}.")
            ok("  So the hardcoded 'gpiochip4' in led.py/key.py still resolves correctly.")
            info("  Fragile though: it depends on a udev rule, not the kernel. Worth")
            info("  looking the chip up by label anyway so it stops mattering.")
        elif os.path.exists("/dev/gpiochip4"):
            warn(f"RP1 is {name}, and /dev/gpiochip4 exists but is NOT a symlink to it.")
            warn("  led.py/key.py would open the wrong chip. Verify before trusting:")
            warn("    ls -l /dev/gpiochip*")
            finding(f"gpiochip4 exists but does not point at RP1 ({name}). Check led.py/key.py.")
        else:
            warn(f"TurboPi hardcodes 'gpiochip4', but RP1 is {name} and no gpiochip4 exists.")
            warn("  HiwonderSDK/led.py and HiwonderSDK/key.py will fail.")
            warn("  Impact is limited to the board LED and buttons - NOT motors,")
            warn("  NOT servos, NOT the camera, NOT the sensors.")
            finding(f"gpiochip mismatch: SDK expects gpiochip4, RP1 is {name}. Patch led.py/key.py.")
    else:
        # Pi 4 has no RP1; its header is the bcm pinctrl chip.
        if any("bcm" in l.lower() or "pinctrl" in l.lower() for l in labels.values()):
            info("No RP1 chip - consistent with a Pi 4B rather than a Pi 5.")
        else:
            unk("No RP1 chip found and no obvious header controller either.")


# --------------------------------------------------------------------- 2. I2C
def check_i2c(do_scan):
    hdr("2. I2C (ultrasonic sensor lives at address 0x77 on bus 1)")
    buses = sorted(glob.glob("/dev/i2c-*"))
    if not buses:
        bad("No /dev/i2c-* devices. I2C is not enabled.")
        info("Enable with: sudo raspi-config  ->  Interface Options  ->  I2C")
        finding("I2C disabled - the ultrasonic sensor cannot work until it is enabled.")
        return
    info(f"Present: {', '.join(os.path.basename(b) for b in buses)}")
    if "/dev/i2c-1" in buses:
        ok("/dev/i2c-1 present (the bus TurboPi's Sonar.py uses).")
    else:
        warn("/dev/i2c-1 missing - HiwonderSDK/Sonar.py hardcodes bus 1.")
        finding("/dev/i2c-1 absent but Sonar.py requires it.")

    try:
        import smbus2  # noqa: F401
        ok("python module 'smbus2' is installed (required by Sonar.py).")
    except ImportError:
        warn("python module 'smbus2' NOT installed. Sonar.py will fail on import.")
        info("Install with: pip install smbus2   (or: sudo apt install python3-smbus2)")
        finding("smbus2 not installed.")

    if not do_scan:
        info("Re-run with --i2c-scan to probe for devices (needs the robot powered on).")
        return

    rc, out = run(["i2cdetect", "-y", "1"])
    if rc != 0:
        unk(f"i2cdetect unavailable or failed ({out.strip()[:60]}).")
        info("Install with: sudo apt install i2c-tools")
        return
    print("\n".join("          " + l for l in out.splitlines()))
    if re.search(r"\b77\b", out):
        ok("Found a device at 0x77 - that is the TurboPi ultrasonic sensor.")
    else:
        warn("Nothing at 0x77. Either the robot is unpowered, the sensor is")
        warn("  unplugged, or it is on a different port. Not alarming pre-assembly.")


# ------------------------------------------------------------------- 3. serial
def check_serial():
    hdr("3. Serial UART (this carries ALL motor and servo commands)")
    info("HiwonderSDK/ros_robot_controller_sdk.py opens /dev/ttyAMA0 @ 1000000 baud.")

    ports = sorted(glob.glob("/dev/ttyAMA*") + glob.glob("/dev/ttyS*") +
                   glob.glob("/dev/serial*"))
    info(f"Present: {', '.join(os.path.basename(p) for p in ports) or '(none)'}")
    for prt in ports:
        real = os.path.realpath(prt)
        if real != prt:
            info(f"  {os.path.basename(prt)} -> {os.path.basename(real)}  (symlink)")

    if os.path.exists("/dev/ttyAMA0"):
        ok("/dev/ttyAMA0 exists.")
    else:
        bad("/dev/ttyAMA0 does NOT exist. The robot cannot be driven at all.")
        info("Fix: add 'enable_uart=1' to /boot/firmware/config.txt, then reboot.")
        finding("/dev/ttyAMA0 missing - no motor/servo control possible.")

    try:
        import serial  # noqa: F401
        ok("python module 'serial' (pyserial) is installed.")
    except ImportError:
        warn("pyserial NOT installed. ros_robot_controller_sdk.py will fail on import.")
        finding("pyserial not installed.")

    # The classic trap: a login console holding the same UART.
    rc, out = run(["systemctl", "is-active", "serial-getty@ttyAMA0.service"])
    state = out.strip()
    if rc == 0 and state == "active":
        bad("A serial LOGIN CONSOLE is running on ttyAMA0 and will fight the robot board.")
        info("Fix: sudo systemctl disable --now serial-getty@ttyAMA0.service")
        info("     and remove 'console=serial0,115200' from /boot/firmware/cmdline.txt")
        finding("serial-getty is occupying ttyAMA0 - motor control will be unreliable.")
    elif state in ("inactive", "failed", "unknown"):
        ok(f"No serial login console on ttyAMA0 (state: {state}).")
    else:
        oneline = " ".join(state.split())[:90] if state else "no output"
        unk(f"Could not determine serial-getty state ({oneline}).")

    cmdline = read_text("/boot/firmware/cmdline.txt") or read_text("/boot/cmdline.txt")
    if cmdline and "console=serial0" in cmdline:
        warn("cmdline.txt still routes a kernel console to serial0.")
        finding("cmdline.txt has console=serial0 - remove it before driving.")

    for cfg in ("/boot/firmware/config.txt", "/boot/config.txt"):
        txt = read_text(cfg)
        if txt is None:
            continue
        info(f"config.txt in use: {cfg}")
        if re.search(r"^\s*enable_uart\s*=\s*1", txt, re.M):
            ok("  enable_uart=1 is set.")
        else:
            warn("  enable_uart=1 is NOT set.")
            finding("enable_uart=1 missing from config.txt.")
        break
    else:
        unk("Could not read config.txt.")


# ------------------------------------------------------------------- 4. camera
def check_camera():
    hdr("4. Camera (TurboPi uses a USB camera, not the CSI ribbon camera)")
    vids = sorted(glob.glob("/dev/video*"))
    if not vids:
        warn("No /dev/video* devices. Expected if the USB camera is not plugged in.")
        finding("No video devices present.")
        return
    info(f"Present: {', '.join(os.path.basename(v) for v in vids)}")

    rc, out = run(["v4l2-ctl", "--list-devices"])
    if rc == 0 and out.strip():
        print("\n".join("          " + l for l in out.splitlines() if l.strip()))
        if re.search(r"usb-", out, re.I):
            ok("A USB-attached video device is present - consistent with TurboPi.")
    else:
        unk("v4l2-ctl unavailable (sudo apt install v4l-utils) - cannot identify devices.")

    try:
        import cv2
        ok(f"python module 'cv2' (OpenCV) is installed, version {cv2.__version__}.")
    except ImportError:
        warn("OpenCV NOT installed. Camera.py and every vision demo will fail.")
        finding("OpenCV (cv2) not installed.")


# ---------------------------------------------------------------- 5. SDK layout
def check_sdk():
    hdr("5. TurboPi source layout")
    candidates = ["/home/pi/TurboPi", os.path.expanduser("~/TurboPi")]
    found = None
    for c in candidates:
        if os.path.isdir(c):
            found = c
            break
    if not found:
        warn("No TurboPi source directory found at /home/pi/TurboPi or ~/TurboPi.")
        info("Expected on the Hiwonder image. On a clean OS, clone it there.")
        finding("TurboPi source not found on this system.")
        return
    info(f"Found: {found}")
    if found != "/home/pi/TurboPi":
        warn(f"It is at {found}, but mecanum.py hardcodes '/home/pi/TurboPi/'.")
        finding(f"TurboPi source at {found} but SDK hardcodes /home/pi/TurboPi.")
    for f in ("HiwonderSDK/ros_robot_controller_sdk.py", "HiwonderSDK/mecanum.py",
              "HiwonderSDK/Sonar.py", "Camera.py", "MjpgServer.py", "TurboPi.py"):
        p = os.path.join(found, f)
        (ok if os.path.exists(p) else warn)(f"  {'present' if os.path.exists(p) else 'MISSING'}: {f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--i2c-scan", action="store_true",
                    help="also scan I2C bus 1 (robot must be powered on)")
    args = ap.parse_args()

    check_identity()
    check_gpio()
    check_i2c(args.i2c_scan)
    check_serial()
    check_camera()
    check_sdk()

    hdr("SUMMARY")
    if not FINDINGS:
        ok("Nothing flagged. Every interface TurboPi needs looks present.")
    else:
        for f in FINDINGS:
            print(f"  - {f}")
    print("\n  Paste this whole output back into the session for interpretation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
