#!/usr/bin/env python3
"""
bringup.py - staged hardware bring-up for TurboPi.

RUN ON THE PI, with the venv interpreter, from the source directory:

    cd /home/pi/TurboPi
    ~/turbopi-venv/bin/python ~/bringup.py

    --skip-motion    sensors and serial only, nothing moves
    --yes            don't pause for confirmation (only once you trust it)

########################  SAFETY  ########################
#  PUT THE ROBOT ON A STAND. THE WHEELS MUST NOT TOUCH   #
#  ANYTHING. It will try to drive away.                  #
#                                                        #
#  Motors always stop, including on Ctrl-C or a crash.   #
##########################################################

Tests run smallest-first, so a failure tells you WHICH subsystem broke rather
than that something did. Each stage reports what you should observe; you
confirm, because only you can see the robot.
"""

import argparse
import os
import signal
import sys
import time

# The SDK hardcodes this path; make sure imports resolve regardless of cwd.
sys.path.insert(0, "/home/pi/TurboPi")

_TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s
def hdr(s):  print(f"\n{_c('1','=' * 62)}\n{_c('1', s)}\n{_c('1','=' * 62)}")
def ok(s):   print(f"{_c('32','  PASS')}  {s}")
def bad(s):  print(f"{_c('31','  FAIL')}  {s}")
def warn(s): print(f"{_c('33','  WARN')}  {s}")
def info(s): print(f"        {s}")

RESULTS = []
def record(name, passed, detail=""):
    RESULTS.append((name, passed, detail))
    (ok if passed else bad)(f"{name}{': ' + detail if detail else ''}")

BOARD = None

def all_stop():
    """Stop every motor. Safe to call repeatedly, never raises."""
    try:
        if BOARD is not None:
            BOARD.set_motor_duty([[1, 0], [2, 0], [3, 0], [4, 0]])
    except Exception:                                        # noqa: BLE001
        pass

def _sigint(signum, frame):
    print("\n\n  Interrupted — stopping motors.")
    all_stop()
    sys.exit(130)

signal.signal(signal.SIGINT, _sigint)

def confirm(prompt, auto=False):
    if auto:
        print(f"  > {prompt} [auto-yes]")
        return True
    try:
        return input(f"  > {prompt} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False

def ask(prompt, auto=False):
    if auto:
        return "(skipped - auto mode)"
    try:
        return input(f"  > {prompt}: ").strip()
    except EOFError:
        return ""


# ---------------------------------------------------------------- 1. imports
def stage_imports():
    hdr("STAGE 1 - Python dependencies")
    mods = {
        "serial":  "pyserial - the motor/servo link",
        "smbus2":  "ultrasonic sensor (Sonar.py)",
        "smbus":   "line-following sensor (FourInfrared.py)",
        "cv2":     "camera and all vision",
        "numpy":   "everything",
        "yaml":    "config files",
    }
    allok = True
    for m, why in mods.items():
        try:
            __import__(m)
            ok(f"{m:<8} {why}")
        except ImportError:
            bad(f"{m:<8} MISSING - {why}")
            if m == "smbus":
                info("        Fix: sudo apt install -y python3-smbus")
            allok = False
    record("dependencies", allok)
    return allok


# ----------------------------------------------------------------- 2. serial
def _raw_serial_probe():
    """Listen on the UART for raw bytes. Distinguishes an unpowered/disconnected
    board (total silence) from a board that is talking but not understood."""
    try:
        import serial as _s
        with _s.Serial("/dev/ttyAMA0", 1000000, timeout=1) as port:
            got = bytearray()
            t0 = time.time()
            while time.time() - t0 < 4:
                chunk = port.read(256)
                if chunk:
                    got.extend(chunk)
                if len(got) > 512:
                    break
        if got:
            info(f"        Raw listen: {len(got)} bytes received.")
            info(f"        First bytes: {got[:24].hex(' ')}")
            if b"\xaa\x55" in bytes(got):
                info("        Found the 0xAA 0x55 frame header — the board IS alive")
                info("        and speaking the right protocol. Likely a timing issue;")
                info("        re-run, and tell the session it got this far.")
            else:
                info("        Bytes arrived but no 0xAA 0x55 header — wrong baud rate,")
                info("        or something else is transmitting on this line.")
        else:
            info("        Raw listen: SILENCE — not one byte in 4 seconds.")
            info("")
            info("        Checking whether the Pi is even driving its UART pins...")
            _pin_function_probe()
            info("")
            info("        If the pins are correct, the board is not transmitting.")
            info("        In order of likelihood:")
            info("          1. The battery pack's cable is not plugged into the")
            info("             expansion board's power input. Batteries sitting in")
            info("             the holder is NOT the same as the board being fed.")
            info("          2. Expansion board power switch is OFF")
            info("          3. Battery pack switch is OFF, or the pack is flat")
            info("          4. The Pi is running on USB-C while the board is unpowered")
            info("             (the Pi boots happily either way — that proves nothing)")
            info("          5. The 40-pin header is not fully seated")
            info("")
            info("        Look at the expansion board's POWER LED (LED1). If it is")
            info("        dark, the board has no power and nothing else matters.")
    except Exception as e:                                   # noqa: BLE001
        info(f"        Raw probe failed: {type(e).__name__}: {e}")


def _pin_function_probe():
    """On a Pi 5, GPIO14/15 must be in their UART alt-function for the header
    UART to reach the expansion board. This rules the Pi side in or out."""
    import subprocess
    try:
        r = subprocess.run(["pinctrl", "get", "14,15"], capture_output=True,
                           text=True, timeout=10)
        out = (r.stdout or r.stderr).strip()
        if not out:
            info("        (pinctrl returned nothing)")
            return
        for line in out.splitlines():
            info(f"          {line}")
        low = out.lower()
        if "txd" in low and "rxd" in low:
            info("        -> GPIO14/15 ARE in UART mode. The Pi side is correct,")
            info("           so the silence is the board's or the wiring's.")
        else:
            info("        -> GPIO14/15 are NOT in UART mode. The Pi is not driving")
            info("           the serial pins - a Pi-side config problem, not the")
            info("           robot. Report this output.")
    except FileNotFoundError:
        info("        (pinctrl not installed - skip)")
    except Exception as e:                                   # noqa: BLE001
        info(f"        (pin probe failed: {type(e).__name__}: {e})")


def stage_board():
    """The single most important test: can we talk to the controller MCU?"""
    global BOARD
    hdr("STAGE 2 - Controller board over serial  (the critical one)")
    info("If this passes, the whole drivetrain path works: UART up, packet")
    info("protocol matching, and the board's microcontroller alive.")
    try:
        import HiwonderSDK.ros_robot_controller_sdk as rrc
    except Exception as e:                                   # noqa: BLE001
        record("board import", False, f"{type(e).__name__}: {e}")
        return False

    try:
        BOARD = rrc.Board()
        # REQUIRED. Board.__init__ leaves enable_recv False, and its receive
        # thread discards everything until this is called. Worse, get_battery()
        # then returns None via a silent early-out whose diagnostic print is
        # commented out in the SDK - so a missing call looks exactly like dead
        # hardware. The SDK's own __main__ calls this immediately after Board().
        BOARD.enable_reception()
        time.sleep(0.5)
    except Exception as e:                                   # noqa: BLE001
        record("board open", False, f"{type(e).__name__}: {e}")
        info("Check: /dev/ttyAMA0 exists, robot powered on, switch ON.")
        return False

    mv = None
    for _ in range(15):                   # the board pushes sys packets; wait for one
        mv = BOARD.get_battery()
        if mv:
            break
        time.sleep(0.4)

    if not mv:
        record("battery read", False, "no response from the board")
        info("Serial opened and reception is enabled, but no packets arrived.")
        info("Running a raw listen to tell 'board silent' from 'protocol mismatch'...")
        _raw_serial_probe()
        return False

    v = mv / 1000.0
    record("battery read", True, f"{v:.2f} V")
    if v < 6.5:
        warn(f"{v:.2f}V is low — charge before the motor tests (7.1V = warning level)")
    elif v > 8.5:
        warn(f"{v:.2f}V is higher than a 2S pack should read — check the reading")
    else:
        info(f"        {v:.2f}V is a healthy 2S lithium voltage.")
    return True


# --------------------------------------------------------- 3. buzzer + RGB
def stage_signals(auto):
    hdr("STAGE 3 - Buzzer and RGB  (proves the board acts on commands)")
    info("Read-back proved the board talks. This proves it listens.")
    try:
        BOARD.set_buzzer(1900, 0.1, 0.1, 1)
        time.sleep(0.5)
        record("buzzer", confirm("Did you hear a short beep?", auto))
    except Exception as e:                                   # noqa: BLE001
        record("buzzer", False, str(e))

    try:
        for col in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]:
            BOARD.set_rgb([[1, *col], [2, *col]])
            time.sleep(0.4)
        BOARD.set_rgb([[1, 0, 0, 0], [2, 0, 0, 0]])
        record("RGB LEDs", confirm("Did the LEDs cycle red/green/blue?", auto))
    except Exception as e:                                   # noqa: BLE001
        record("RGB LEDs", False, str(e))


# ----------------------------------------------------------------- 4. motors
MOTOR_POSITIONS = {1: "front-left", 2: "front-right",
                   3: "rear-left",  4: "rear-right"}

def stage_motors(auto):
    hdr("STAGE 4 - Motors, one at a time  (wiring check)")
    print()
    print(_c('31', "  *** WHEELS MUST BE OFF THE GROUND ***"))
    print()
    if not confirm("Robot on a stand, wheels free and clear?", auto):
        warn("Skipping motor tests.")
        record("motors", False, "skipped by user")
        return

    info("Each motor spins alone for 1s at low power. Note WHICH wheel moves.")
    info("The expected port map is:")
    for mid, pos in MOTOR_POSITIONS.items():
        info(f"          M{mid} = {pos}")
    print()

    wrong = []
    for mid, expect in MOTOR_POSITIONS.items():
        input(f"  > Press Enter to spin M{mid} (expect: {expect}) ..."
              if not auto else "")
        try:
            BOARD.set_motor_duty([[mid, 35]])
            time.sleep(1.0)
        finally:
            all_stop()
        time.sleep(0.3)
        answer = ask(f"Which wheel actually moved? (blank = {expect})", auto)
        if answer and expect.lower() not in answer.lower() \
                and not answer.lower().startswith("(skip"):
            wrong.append(f"M{mid}: expected {expect}, got '{answer}'")
            bad(f"M{mid} mismatch")
        else:
            ok(f"M{mid} -> {expect}")

    if wrong:
        record("motor wiring", False, "; ".join(wrong))
        info("Fix by swapping the motor connectors on the expansion board.")
        info("mecanum.py assumes this exact numbering — wrong order makes")
        info("'forward' come out as a spin or a strafe.")
    else:
        record("motor wiring", True, "all four match the expected positions")


# ------------------------------------------------------------ 5. mecanum
def stage_mecanum(auto):
    hdr("STAGE 5 - Mecanum vectors  (wheel orientation check)")
    if not confirm("Still on the stand? Run coordinated motion?", auto):
        record("mecanum", False, "skipped by user")
        return
    try:
        import HiwonderSDK.mecanum as mecanum
        chassis = mecanum.MecanumChassis()
    except Exception as e:                                   # noqa: BLE001
        record("mecanum import", False, str(e))
        return

    moves = [("FORWARD", 50, 90, 0), ("BACKWARD", 50, 270, 0),
             ("STRAFE RIGHT", 50, 0, 0), ("STRAFE LEFT", 50, 180, 0),
             ("ROTATE", 0, 0, 0.5)]
    try:
        for name, vel, direction, rate in moves:
            info(f"  {name} for 1.5s ...")
            chassis.set_velocity(vel, direction, rate)
            time.sleep(1.5)
            chassis.reset_motors()
            time.sleep(0.8)
    except Exception as e:                                   # noqa: BLE001
        record("mecanum motion", False, str(e))
        return
    finally:
        all_stop()

    info("With wheels off the ground, watch the ROLLERS, not the robot.")
    record("mecanum motion",
           confirm("Did all five movements look distinct and sensible?", auto))


# ------------------------------------------------------------- 6. servos
def stage_servos(auto):
    hdr("STAGE 6 - Pan-tilt servos")
    info("Servo 1 = tilt (up/down), Servo 2 = pan (left/right). 1500us = centre.")
    info("Small movements only. Stop immediately if anything binds or buzzes.")
    if not confirm("Camera pan-tilt clear of obstructions?", auto):
        record("servos", False, "skipped by user")
        return
    try:
        BOARD.pwm_servo_set_position(0.5, [[1, 1500], [2, 1500]])
        time.sleep(1.0)
        for sid, name, lo, hi in [(2, "pan", 1350, 1650), (1, "tilt", 1400, 1600)]:
            info(f"  {name} (servo {sid}): centre -> {lo} -> {hi} -> centre")
            for pulse in (lo, hi, 1500):
                BOARD.pwm_servo_set_position(0.4, [[sid, pulse]])
                time.sleep(0.8)
        record("servos", confirm("Did the camera pan and tilt smoothly?", auto))
    except Exception as e:                                   # noqa: BLE001
        record("servos", False, str(e))


# ------------------------------------------------------------ 7. ultrasonic
def stage_sonar(auto):
    hdr("STAGE 7 - Ultrasonic sensor  (I2C 0x77)")
    try:
        import HiwonderSDK.Sonar as Sonar
        s = Sonar.Sonar()
        info("Reading for 5s — wave your hand in front of it.")
        seen = []
        for _ in range(12):
            d = s.getDistance()
            seen.append(d)
            print(f"        {d} mm")
            time.sleep(0.4)
        valid = [d for d in seen if 0 < d < 5000]
        if not valid:
            record("ultrasonic", False, "no plausible readings")
            info("Check it's on port No. 3 and the cable is seated.")
        elif max(valid) - min(valid) < 5:
            record("ultrasonic", True, f"reads {valid[0]}mm but never varied")
            warn("Value never changed — did you move your hand in front of it?")
        else:
            record("ultrasonic", True, f"varied {min(valid)}-{max(valid)} mm")
    except Exception as e:                                   # noqa: BLE001
        record("ultrasonic", False, f"{type(e).__name__}: {e}")


# ------------------------------------------------------------ 8. line sensor
def stage_line(auto):
    hdr("STAGE 8 - 4-channel line sensor  (I2C 0x78)")
    try:
        import HiwonderSDK.FourInfrared as FI
        line = FI.FourInfrared()
        info("Reading for 5s — slide something dark under each sensor.")
        info("True = sees black line.")
        states = set()
        for _ in range(12):
            d = line.readData()
            states.add(tuple(d))
            print(f"        {d}")
            time.sleep(0.4)
        if len(states) > 1:
            record("line sensor", True, f"{len(states)} distinct states seen")
        else:
            record("line sensor", True, f"reads {list(states)[0]} but never changed")
            warn("No variation — pass something dark under it to confirm.")
    except ImportError as e:
        record("line sensor", False, f"missing module: {e.name}")
        info("Fix: sudo apt install -y python3-smbus")
    except Exception as e:                                   # noqa: BLE001
        record("line sensor", False, f"{type(e).__name__}: {e}")


# --------------------------------------------------------------- 9. camera
def stage_camera(auto):
    hdr("STAGE 9 - USB camera")
    import glob
    vids = sorted(glob.glob("/dev/video*"))
    if not vids:
        record("camera", False, "no /dev/video* at all")
        return
    try:
        import cv2
        cap = cv2.VideoCapture(-1)
        got = None
        for _ in range(10):
            okf, frame = cap.read()
            if okf and frame is not None:
                got = frame
                break
            time.sleep(0.2)
        cap.release()
        if got is None:
            record("camera", False, "opened but returned no frames")
            info("Is the USB camera plugged in? It is NOT the CSI ribbon port.")
        else:
            record("camera", True, f"captured a {got.shape[1]}x{got.shape[0]} frame")
    except Exception as e:                                   # noqa: BLE001
        record("camera", False, f"{type(e).__name__}: {e}")


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-motion", action="store_true",
                    help="sensors and serial only; nothing moves")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation prompts")
    args = ap.parse_args()

    print(__doc__)
    if os.geteuid() == 0:
        warn("Running as root. Not required, and it muddies file ownership.")

    try:
        if not stage_imports():
            warn("Continuing anyway — later stages may fail on imports.")
        if not stage_board():
            hdr("STOPPED")
            print("  Cannot reach the controller board, so nothing downstream")
            print("  can be tested. Fix that first — everything else depends on it.")
            return 1

        stage_signals(args.yes)
        if not args.skip_motion:
            stage_motors(args.yes)
            stage_mecanum(args.yes)
            stage_servos(args.yes)
        else:
            info("\n(motion stages skipped via --skip-motion)")
        stage_sonar(args.yes)
        stage_line(args.yes)
        stage_camera(args.yes)
    finally:
        all_stop()

    hdr("SUMMARY")
    passed = sum(1 for _, p, _ in RESULTS if p)
    for name, p, detail in RESULTS:
        mark = _c('32', 'PASS') if p else _c('31', 'FAIL')
        print(f"  [{mark}] {name}{': ' + detail if detail else ''}")
    print(f"\n  {passed}/{len(RESULTS)} stages passed.")
    print("  Paste this whole output back into the session.")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
