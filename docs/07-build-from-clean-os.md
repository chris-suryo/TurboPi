# Building TurboPi on clean Raspberry Pi OS

**This is the primary path.** You do not need Hiwonder's system image — see
[`02-turbopi-pi5-compatibility.md`](02-turbopi-pi5-compatibility.md) for why.

Good news on sequencing: **this reuses the SD card from Phase 1.** The stock Raspberry Pi OS install
you made to test the charger becomes the robot's OS. No reflash, no second card.

All commands are **SSH (Pi)** unless labelled otherwise.

---

## 1. Enable the interfaces the robot needs

Two of the three buses TurboPi uses are off by default.

```bash
sudo raspi-config nonint do_i2c 0          # 0 means enable
```

For the UART, be explicit rather than trusting a menu — this is the link that carries every
motor and servo command, so it's worth seeing the config:

```bash
sudo nano /boot/firmware/config.txt
```

Ensure this line is present, uncommented:

```
enable_uart=1
```

### Disable the serial login console

By default a kernel console and login prompt are routed to a serial port. If that port is the
same one the robot board uses, both fight over it and the symptom is intermittently corrupted
motor commands — which looks like flaky hardware and is miserable to debug.

**Measured on the Pi 5, before and after — and the answer changes:**

| | `/dev/serial0` points to | Is the console a conflict? |
|---|---|---|
| Before `enable_uart=1` | `ttyAMA10` (debug UART, 3-pin connector) | No |
| **After `enable_uart=1` + reboot** | **`ttyAMA0`** (40-pin header UART) | **Yes** |

`enable_uart=1` **remaps the `serial0` alias onto the header UART.** So a `console=serial0`
entry in `cmdline.txt` that was harmless beforehand lands squarely on `/dev/ttyAMA0` — the
exact port the robot board uses — the moment you enable the UART.

**So removing it is genuinely necessary, not just prudent.** Do it in the same pass as
`enable_uart=1`; if you enable the UART and leave the console, you get a kernel console and the
robot controller sharing one serial port, and intermittently corrupted motor commands.

```bash
sudo systemctl disable --now serial-getty@ttyAMA0.service
sudo nano /boot/firmware/cmdline.txt
```

In `cmdline.txt`, delete `console=serial0,115200` if present. **That file must stay a single
line** — don't let the editor wrap it onto two.

```bash
sudo reboot
```

> **Pi 5 note:** on a Pi 5, `enable_uart=1` should be enough to put the header UART on
> `/dev/ttyAMA0`. On a Pi 4 you also needed `dtoverlay=disable-bt`, because Bluetooth had
> claimed it. The Pi 5 has more UARTs and doesn't have that conflict. Verify rather than trust
> this — `check_hardware.py` in step 6 tells you what's actually there.

## 2. System packages

```bash
sudo apt update
sudo apt install -y \
  python3-opencv python3-numpy python3-yaml python3-serial \
  python3-libgpiod python3-pil python3-pandas \
  i2c-tools v4l-utils libzbar0 git
```

Two deliberate choices:

**`python3-opencv` from apt, not `pip install opencv-python`.** The apt build is prebuilt for
arm64. Pip may try to compile OpenCV from source on the Pi, which takes hours and often fails
on memory.

**`python3-libgpiod` from apt — and do NOT `pip install gpiod`.** This one will bite you:

> TurboPi's code uses the **libgpiod v1** Python API — `chip.get_line(n)` and
> `line.request(type=gpiod.LINE_REQ_DIR_OUT)`. The PyPI `gpiod` package is **v2.x**, which has
> a completely different API (`gpiod.request_lines(...)`). Installing it from pip gives you a
> module named `gpiod` that the code cannot use. The distro's `python3-libgpiod` is the v1
> series, which is the one that matches. Verify with `python3 -c "import gpiod; print(gpiod.__version__)"`
> — if it reports 2.x, that's the wrong one.

## 3. Python packages

Debian 12 and later enforce PEP 668 — system-wide `pip install` is refused. Use a virtualenv that can
still see the apt-installed modules:

```bash
python3 -m venv --system-site-packages ~/turbopi-venv
~/turbopi-venv/bin/pip install smbus2 pyzbar json-rpc werkzeug mediapipe
```

`--system-site-packages` is the important flag: it lets the venv use apt's `cv2`, `gpiod`,
`numpy` and `pandas` while you pip-install the rest on top. Without it you'd be back to
compiling OpenCV.

Run everything with `~/turbopi-venv/bin/python` from here on.

**If `mediapipe` fails to install**, don't fight it immediately — here's exactly what it costs:

| Package | Used by | Impact if missing |
|---|---|---|
| `mediapipe` | `FaceTracking.py`, `GestureRecognition.py` | Those two demos only |
| `pyzbar` | `QuickMark.py` | QR reading only |
| `pandas` | `Avoidance.py` | One demo |
| `cv2`, `numpy` | everything | Genuinely required |

Driving, line following, colour tracking, colour detection and visual patrol need only `cv2`
and `numpy`. Get those working first; treat mediapipe as a later nice-to-have.

## 4. Get the source

The path is **not** negotiable — `HiwonderSDK/mecanum.py` hardcodes
`sys.path.append('/home/pi/TurboPi/')`:

```bash
git clone https://github.com/Hiwonder/turbopi /home/pi/TurboPi
```

(If your username isn't `pi`, either create a `pi` user or symlink `/home/pi` — but the
simplest fix is to have used `pi` when flashing, as `03-headless-boot.md` recommends.)

## 5. Fix the hardcoded gpiochip

`HiwonderSDK/led.py` and `key.py` hardcode `gpiod.Chip('gpiochip4')`. On the Pi 5 the RP1
chip's number has moved across kernel releases. Find out what yours is:

```bash
gpiodetect
```

Look for the line labelled `pinctrl-rp1` — that's the 40-pin header.

**Watch out for a genuinely misleading error here.** Both files wrap their entire body in a
bare `except:` that prints:

```
led默认被hw_wifi占用，需要自行注释掉相关代码
```

...meaning "the LED is occupied by hw_wifi." On Hiwonder's image that's true. **On a clean OS
that service doesn't exist**, so if you see this message it almost certainly means the
gpiochip name is wrong — the real exception was swallowed. Don't go hunting for a service
that isn't there.

The durable fix is to look the chip up by label rather than number, so it stops depending on
kernel version. We'll do that together when you get here — it's a few lines.

**This only affects the board LED and buttons.** Motors, servos, camera and sensors don't
touch it.

## 6. Verify before touching the robot

```bash
cd /home/pi/TurboPi
~/turbopi-venv/bin/python ~/check_hardware.py
```

> **Run it with the venv's Python, not `./check_hardware.py`.** The bare form uses the *system*
> interpreter, which cannot see anything pip-installed into `~/turbopi-venv` — so it reports
> `smbus2`, `pyzbar` and `mediapipe` as missing even when they're correctly installed. Same
> script, different interpreter, different answer.

### Verify every dependency at once

```bash
for m in cv2 numpy yaml serial gpiod PIL pandas smbus2 pyzbar jsonrpc werkzeug mediapipe; do
  ~/turbopi-venv/bin/python -c "import $m" 2>/dev/null \
    && echo "  OK   $m" || echo "  FAIL $m"
done
```

`cv2`, `numpy`, `serial`, `gpiod`, `yaml`, `PIL` and `pandas` come from apt and are visible
through `--system-site-packages`; the rest come from pip inside the venv. A `FAIL` on
`mediapipe` costs you face and gesture tracking only — see the risk table above.

Expect: `/dev/ttyAMA0` present, `/dev/i2c-1` present, **no serial-getty on ttyAMA0**, gpiochip
labels listed, pyserial and smbus2 importable.

Then the real test — talk to the board (robot powered on, **wheels off the ground**):

```bash
~/turbopi-venv/bin/python -c "
import HiwonderSDK.ros_robot_controller_sdk as rrc
b = rrc.Board()
print('battery mV:', b.get_battery())
"
```

A plausible number (~6000–8400 mV on charged cells) means serial is up, the packet protocol
matches, and the board's MCU is alive. **That single number validates the entire drivetrain
path.** If it works, continue to [`04-assembly-bringup.md`](04-assembly-bringup.md).

## 7. VNC, for the demos

The `Functions/` demos call `cv2.imshow()` and need a display — see
[`06-running-the-demos.md`](06-running-the-demos.md). No HDMI cable required:

```bash
sudo raspi-config nonint do_vnc 0
```

Then connect with VNC Viewer from Windows. `TurboPi.py` itself is headless and needs none of
this.

---

## What you're giving up versus Hiwonder's image

Honest accounting:

| Their image has | Do you need it? |
|---|---|
| `hiwonder-toolbox` / `hw_wifi.service` — Wi-Fi **access point** mode | **No.** AP mode is for rooms without Wi-Fi; it forces your PC off its own network |
| `hw_button_scan.service` — board buttons | No. And it's what *blocks* `key.py` on their image |
| Preinstalled dependencies | You just did this, and now you know what's installed |
| Pinned known-good kernel | Mild loss — it's why `gpiochip4` is hardcoded and works there |
| VNC preconfigured | One command, step 7 |
| WonderPi phone app support | Works either way — it talks to `RPCServer.py` on port 9030, which is in the repo |

The only genuine loss is the pinned kernel, and its practical effect is the one `gpiochip`
line in step 5.
