# Does TurboPi's software support the Raspberry Pi 5?

**Short answer: yes, and the concern about RP1/`RPi.GPIO` does not apply to this project.**

This was the question worth answering before assembling anything, because the fix — buying a
Pi 4B — is much cheaper before the robot is built than after. The answer is favorable, but the
reasoning matters more than the verdict, so here is the evidence.

---

## How this was established

Not from marketing copy. Hiwonder publishes both the application source and their official
documentation on GitHub, and both were read directly:

| Source | What it is |
|---|---|
| `github.com/Hiwonder/turbopi` @ `122abc6` (2025-11-24) | The actual TurboPi application source |
| `github.com/Hiwonder-docs/TurboPi` | Mirror of the official docs site |

A caveat, stated plainly: `docs.hiwonder.com` and `wiki.hiwonder.com` are **blocked by the
network policy of the environment this research ran in.** The GitHub mirrors were used instead.
They are Hiwonder's own repositories, so this is a primary source, but it is not literally the
live doc site and could in principle lag it.

## Evidence

1. **The repo README states it outright:** *"Supported Platform: Raspberry Pi 4B or 5B"*, and
   under Hardware Configuration, *"Processor: Raspberry Pi 4B or 5B"*.
2. **The official docs publish different default logins per board**, which only makes sense if
   two separate images are actually maintained:
   - Pi 4B → user `pi`, password `raspberry`
   - **Pi 5 → user `pi`, password `raspberrypi`**
3. **Hiwonder sells TurboPi bundles with a Pi 5** (4GB and 8GB variants).

## Why the RP1 / `RPi.GPIO` worry doesn't land here

The worry is real *in general*: the Pi 5 moved its GPIO behind the new RP1 I/O controller, the
old `RPi.GPIO` library does not work against it, and `gpiozero`/`lgpio` is the modern path.

But it is the wrong worry for *this* robot. Every import in the codebase was enumerated.
**`RPi.GPIO` does not appear anywhere.** What TurboPi actually uses:

| Job | Mechanism | Where | Pi 5 impact |
|---|---|---|---|
| **All motors, all servos** | UART `/dev/ttyAMA0` @ 1,000,000 baud | `HiwonderSDK/ros_robot_controller_sdk.py` | **None** — no Pi PWM involved |
| Ultrasonic sensor + its RGB LEDs | I2C address `0x77`, via `smbus2` | `HiwonderSDK/Sonar.py` | None |
| Board LED and buttons | `gpiod` (libgpiod — the RP1-correct API) | `HiwonderSDK/led.py`, `key.py` | One caveat, below |
| Camera | USB UVC via `cv2.VideoCapture(-1)` | `Camera.py` | None — no libcamera/picamera2 |

### The architectural fact that explains everything

**The expansion board has its own microcontroller.** The Pi does not generate a single PWM
pulse for the drivetrain. It builds a packet and writes it to a serial port:

```python
board.set_motor_duty([[1, -v1], [2, v2], [3, -v3], [4, v4]])
```

The MCU on the expansion board receives that and does the real-time work. Servos are the same
story — `board.pwm_servo_set_position(duration, [[servo_id, pulse_us], ...])`.

This is why the Pi 4 → Pi 5 GPIO overhaul barely touches this project: **the part of the Pi
that changed is the part TurboPi was already not using.** It is also, incidentally, good
engineering — precise pulse timing is miserable to do from Linux userspace.

## The one real residual risk (small, and contained)

`HiwonderSDK/led.py` and `HiwonderSDK/key.py` both hardcode:

```python
chip = gpiod.Chip('gpiochip4')
```

On the Pi 5 the RP1 chip's *number* has moved around across Raspberry Pi OS kernel releases —
`gpiochip4` originally, then `gpiochip0` from roughly 6.6.45 onward, and other numbers on some
6.12 kernels. If the running kernel disagrees with that hardcoded string, those two modules
raise on import.

**Blast radius: the board's LED and buttons. That is all.** Not driving, not steering, not the
camera, not the ultrasonic sensor, not any vision demo. The fix is a few lines — look up the
chip by its label instead of its number.

`scripts/check_hardware.py` detects this specific mismatch and tells you which chip RP1 is on.

Hiwonder's own image pins a known-good kernel, so this mainly bites if you build from a current
stock Raspberry Pi OS (the Path B fallback).

### Minor: hardcoded path

`HiwonderSDK/mecanum.py` line 4 does `sys.path.append('/home/pi/TurboPi/')`. The user must be
`pi` and the source must live at `/home/pi/TurboPi`. Worth knowing before you get creative with
`uv` virtualenvs and project layout.

---

## The thing that actually blocks you: getting the image

The code is fine. **The OS image is the hard part.**

Hiwonder's own `resources_download.md` says the system image is *not* a public download:

> "System Image & Source Code: If you want to get the system image and source code, please
> email us at support@hiwonder.com, and share your order number :)"

You bought at Micro Center, so you have a retail receipt rather than a Hiwonder order number.
**Start this request on day one** — it has lead time and nothing else depends on it.

The docs also note kits normally ship with a preloaded microSD card. Check your box. But note
that a card bundled with a *no-Pi* kit may well carry the **Pi 4B** image — the differing
default passwords above are the quickest way to tell which one you have.

### If Hiwonder doesn't come through

Build from clean Raspberry Pi OS Bookworm 64-bit. This is a genuine fallback, not a
consolation prize. The application source is public on GitHub, and every third-party import in
the codebase has been enumerated, so the dependency set is known rather than guessed:

```
opencv-python  numpy  mediapipe  pyserial  smbus2  gpiod
PyYAML  pillow  pyzbar  pandas  json-rpc  werkzeug
```

`pyzbar` additionally needs the system package `libzbar0`. `mediapipe` on arm64 is the one
most likely to give you trouble — pin it rather than taking whatever pip resolves.

You would also own: enabling I2C and UART, disabling the serial console, creating the `pi`
user, placing the source at `/home/pi/TurboPi`, and fixing the `gpiochip4` reference. That is
an evening, not a project — but more work than Path A, so we only take it if forced.

**What the image gives you that the public repo doesn't:** `hiwonder-toolbox` (their Wi-Fi
AP/STA manager — `wifi_conf.py` plus `hw_wifi.service`), a pinned known-good kernel,
preinstalled dependencies, and VNC already configured. Of those, the Wi-Fi toolbox is the only
piece with no public equivalent, and plain NetworkManager covers the same need.
