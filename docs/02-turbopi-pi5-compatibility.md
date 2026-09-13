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

**Update — measured on the actual board (kernel 6.18, Debian 13):** RP1 is `gpiochip0`, *but*
Raspberry Pi ships a udev rule creating `/dev/gpiochip4` as a symlink to it:

```
/dev/gpiochip4 -> gpiochip0
```

So **`gpiod.Chip('gpiochip4')` resolves correctly and no patch is needed.** This risk did not
materialise. It rests on a udev rule rather than the kernel, so it could return if that rule is
ever dropped — affecting only the board LED and buttons — but there is nothing to do today.

### Minor: hardcoded path

`HiwonderSDK/mecanum.py` line 4 does `sys.path.append('/home/pi/TurboPi/')`. The user must be
`pi` and the source must live at `/home/pi/TurboPi`. Worth knowing before you get creative with
`uv` virtualenvs and project layout — see the venv guidance in `07-build-from-clean-os.md`.

---

## Do you need Hiwonder's system image? No.

Hiwonder's own `resources_download.md` says the image is not a public download:

> "System Image & Source Code: If you want to get the system image and source code, please
> email us at support@hiwonder.com, and share your order number :)"

You bought at Micro Center, so you have a retail receipt rather than a Hiwonder order number.

**This was initially assessed as the project's blocker. On investigation it is not.** Four
reasons, in order of how much they matter:

### 1. The public repo is self-contained — verified, not assumed

Every `import` across the codebase was resolved mechanically against the standard library,
PyPI, and the repo itself. **Nothing is unresolved.** Every data file the code opens is
present:

| File | Purpose |
|---|---|
| `lab_config.yaml` | LAB colour thresholds for colour detection |
| `servo_config.yaml` | Per-servo centre calibration |
| `CameraCalibration/calibration_param.npz` | Fisheye lens calibration |
| `loading.jpg` | Splash frame |

The camera calibration data is the one that would have hurt to be missing. It's there.

### 2. The image findable online is the wrong one anyway

The circulating filename is **`TurboPi20230320.zip` — March 2023.** The Raspberry Pi 5 launched
in **October 2023**. A pre-Pi-5 image cannot contain Pi 5 support, and the Pi 5 needs
Bookworm-era-or-newer firmware and kernel to boot at all.

So searching for it online is likely to land you a **Pi 4B image that won't boot on your
board** — and burn an afternoon proving it.

### 3. Their image's unique parts are things you actively don't want

What the image adds over the public repo is `hiwonder-toolbox` / `hw_wifi.service` — the
robot's Wi-Fi **access-point hotspot** mode — and `hw_button_scan.service`.

AP mode is a workaround for classrooms with no usable Wi-Fi. It makes the robot broadcast its
own network, which means **your PC has to leave your own network** to talk to it. You have
normal Wi-Fi. This is a downgrade for you.

It's worse than neutral. `HiwonderSDK/led.py` carries a comment saying the board LED is
**occupied by `hw_wifi`**, and `key.py` says the buttons are held by `hw_button_scan`.
**Their image introduces those conflicts.** A clean OS doesn't have them.

### 4. Current Raspberry Pi OS is a better base for a Pi 5 than a 2023-era vendor image

And it matches how you said you want to work — understanding the system rather than
inheriting an opaque one.

### Conclusion

**Build from clean Raspberry Pi OS.** Runbook:
[`07-build-from-clean-os.md`](07-build-from-clean-os.md).

Send the email anyway if you want the Wi-Fi toolbox as a bonus
([`image-request-email.md`](image-request-email.md)) — it costs two minutes. But nothing waits
on it, and if they never reply you lose nothing that matters.

Check the kit box first: Hiwonder put a QR code / Drive link in the printed booklet for kit
owners, which would get you the image without involving support at all.

## What building it yourself actually costs

The dependency set, enumerated from every third-party import rather than guessed:

```
opencv-python  numpy  mediapipe  pyserial  smbus2  gpiod
PyYAML  pillow  pyzbar  pandas  json-rpc  werkzeug
```

Plus: enable I2C and UART, disable the serial console, use username `pi`, place the source at
`/home/pi/TurboPi`, and fix the `gpiochip4` reference. An evening, not a project.

**Two install traps**, both handled in the runbook:

- **Do not `pip install gpiod`.** The code uses the libgpiod **v1** API (`chip.get_line()`,
  `line.request(type=gpiod.LINE_REQ_DIR_OUT)`). PyPI's `gpiod` is **v2**, with an incompatible
  API. Use apt's `python3-libgpiod`.
- **Do not `pip install opencv-python`** on the Pi — it may compile from source for hours. Use
  apt's `python3-opencv`.

### Risk scoping, if a dependency refuses to install

| Package | Used by | Impact if missing |
|---|---|---|
| `mediapipe` | `FaceTracking.py`, `GestureRecognition.py` only | Those two demos |
| `pyzbar` | `QuickMark.py` only | QR reading |
| `pandas` | `Avoidance.py` only | One demo |
| `cv2`, `numpy` | everything | Genuinely required |

Driving, line following, colour tracking, colour detection and visual patrol need only `cv2`
and `numpy`. The riskiest package on arm64 (`mediapipe`) puts **two** demos at risk and blocks
nothing else — and of the four demos you named, only face tracking touches it.
