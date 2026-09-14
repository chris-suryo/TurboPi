# TurboPi on Raspberry Pi 5

Setting up a Hiwonder TurboPi robot car with a Raspberry Pi 5 — verification first, assembly
second, and the underlying concepts explained along the way.

This is a standalone robotics project. It is deliberately **not** connected to any other
project.

---

## The two questions that had to be answered first

### 1. Does TurboPi's software support the Pi 5? — **Yes. Verified.**

TurboPi's bundles are usually sold with a Pi 4B, and the Pi 5's new RP1 I/O controller broke
`RPi.GPIO` for a lot of Pi robotics code. So this was worth checking before assembling
anything, while returning hardware was still an option.

Read from Hiwonder's own published source and docs:

- The repo README states **"Supported Platform: Raspberry Pi 4B or 5B"**
- The official docs publish **separate default logins per board** (Pi 4B: `pi`/`raspberry`;
  Pi 5: `pi`/`raspberrypi`) — two images are genuinely maintained
- **`RPi.GPIO` appears nowhere in the codebase.** Motors and servos are driven over a serial
  link to a microcontroller on the expansion board; the Pi's own GPIO is used only for a
  status LED and two buttons, via `gpiod` (the RP1-correct library)

**The part of the Pi that changed is the part TurboPi was already not using.**

One small residual risk (a hardcoded `gpiochip4` that has moved across kernel versions) affects
only the board LED and buttons — not driving, not the camera, not the sensors.

Full reasoning and citations: **[`docs/02-turbopi-pi5-compatibility.md`](docs/02-turbopi-pi5-compatibility.md)**

### 2. Is the Pi 5 healthy, and is the charger adequate? — **Measure before building.**

The Pi 5 wants **5V/5A USB-C PD**. With a lesser supply it still boots and looks fine, but caps
**USB peripherals to 600mA** — which shows up later as intermittent, software-looking failures.
That's the trap worth catching before it costs a debugging day.

Run **[`scripts/check_power.sh`](scripts/check_power.sh)** on the Pi. It reports undervoltage
flags (current *and* sticky-since-boot), PMIC rail voltages, and the negotiated USB budget —
and says UNKNOWN rather than guessing when a value isn't available.

Background and how to read the results:
**[`docs/01-pi5-power-verification.md`](docs/01-pi5-power-verification.md)**

> **Note for this build:** on the finished robot the Pi is powered from the expansion board off
> the battery, not over USB-C — so no PD negotiation happens at all and the 600mA cap is the
> default. The USB camera has to live inside that. Re-measure after assembly.

---

## Start here

**The robot is built and every subsystem is verified working.** Results:
[`PHASE1-RESULTS.md`](PHASE1-RESULTS.md).

Day-to-day:

Run it as a service so it survives reboots and SSH disconnects:

```bash
bash ~/install_turbopi_service.sh        # once
sudo systemctl status turbopi            # check any time
```

Camera: `http://10.0.0.3:8080/` · Control API: `http://10.0.0.3:9030/`

**The service owns the camera and serial port.** `sudo systemctl stop turbopi` before running
demos or `bringup.py` by hand.

To re-verify hardware after any change:
```bash
~/turbopi-venv/bin/python ~/bringup.py
```

**Building something against the robot?** [`docs/08-robot-api.md`](docs/08-robot-api.md) is the
network API; [`HANDOFF-frontend.md`](HANDOFF-frontend.md) is a ready-to-paste brief for a
custom UI.

**Rebuilding from scratch?** [`docs/03-headless-boot.md`](docs/03-headless-boot.md) then
[`docs/07-build-from-clean-os.md`](docs/07-build-from-clean-os.md). You do **not** need
Hiwonder's system image — the public repo is self-contained and clean Raspberry Pi OS is a
better base for a Pi 5.

---

## Current status

| Phase | State |
|---|---|
| 0. Request the image from Hiwonder | **Optional** — not on the critical path |
| 1. Verify Pi 5 + charger | ✅ **PASSED** — see [`PHASE1-RESULTS.md`](PHASE1-RESULTS.md) |
| 2. Physical fit check | ✅ 52Pi case ruled out; RTC battery + board revision pending at mount time |
| 3. Build robot software on clean Raspberry Pi OS | ✅ **DONE** — all deps installed, I2C + UART enabled, OpenCV 5 verified |
| 4. Assemble and drive | ✅ **All subsystems verified** — see [`PHASE1-RESULTS.md`](PHASE1-RESULTS.md) |
| 5. Camera and built-in demos | Camera verified; demos next — see `docs/06-running-the-demos.md` |
| 6. Concepts | Written, read as you go |

**The image turned out not to be a blocker.** It was initially assessed as one; on checking,
the public repo resolves every import and ships every config and calibration file it needs, so
clean Raspberry Pi OS plus the GitHub source is the better path — not a fallback. Their image's
distinctive feature is Wi-Fi access-point mode, which would force your PC off its own network
to reach the robot. Reasoning in
[`docs/02-turbopi-pi5-compatibility.md`](docs/02-turbopi-pi5-compatibility.md).

---

## Contents

| File | What it's for |
|---|---|
| [`docs/00-hardware-inventory.md`](docs/00-hardware-inventory.md) | What you have, what's verified, what's still assumed |
| [`docs/01-pi5-power-verification.md`](docs/01-pi5-power-verification.md) | Undervoltage and the USB current cap, explained |
| [`docs/02-turbopi-pi5-compatibility.md`](docs/02-turbopi-pi5-compatibility.md) | The Pi 5 question, with evidence |
| [`docs/03-headless-boot.md`](docs/03-headless-boot.md) | Flashing and SSH with no monitor |
| [`docs/04-assembly-bringup.md`](docs/04-assembly-bringup.md) | Staged bring-up — one subsystem at a time |
| [`docs/05-concepts.md`](docs/05-concepts.md) | GPIO/PWM, servos, mecanum kinematics, I2C vs UART |
| [`docs/06-running-the-demos.md`](docs/06-running-the-demos.md) | Which demos need a display, and how to get one without HDMI |
| [`docs/07-build-from-clean-os.md`](docs/07-build-from-clean-os.md) | **The build runbook** — the primary path, no vendor image needed |
| [`docs/08-robot-api.md`](docs/08-robot-api.md) | **Network API** — MJPEG on :8080, JSON-RPC on :9030 |
| [`HANDOFF-frontend.md`](HANDOFF-frontend.md) | Paste-ready brief for building a custom UI |
| [`INTEGRATION-ANSWERS.md`](INTEGRATION-ANSWERS.md) | Integrating the robot as a camera + drivable device in another app |
| [`docs/image-request-email.md`](docs/image-request-email.md) | The email to send Hiwonder for the Pi 5 image |
| [`scripts/check_power.sh`](scripts/check_power.sh) | Power verification. Run on the Pi |
| [`scripts/check_hardware.py`](scripts/check_hardware.py) | Probes serial, I2C, gpiochip, camera. Run on the Pi |
| [`scripts/check_opencv_api.py`](scripts/check_opencv_api.py) | Verifies TurboPi's OpenCV calls against the installed version. Run on the Pi |
| [`scripts/bringup.py`](scripts/bringup.py) | **Staged hardware bring-up** — serial, motors, servos, sensors, camera. Run on the Pi |
| [`scripts/camera_multireader_test.sh`](scripts/camera_multireader_test.sh) | Can the camera be read from another machine, by more than one reader? Run on the **client** |
| [`scripts/patch_optional_demos.py`](scripts/patch_optional_demos.py) | Stops a broken mediapipe from blocking the whole robot app |
| [`scripts/install_turbopi_service.sh`](scripts/install_turbopi_service.sh) | Run the robot as a systemd service — autostart, crash restart, survives SSH |
| [`scripts/patch_camera_always_on.py`](scripts/patch_camera_always_on.py) | Camera streams without a demo loaded — upstream only opens it via `loadFunc()` |

## Two bugs that cost an evening each — both configuration, not hardware

**The board was silent.** Hiwonder's manual says `enable_uart=1` + `dtparam=uart0=on`. On a Pi 5
with a 2025-02-or-later bootloader that leaves UART0 in a state Linux can't take over — RXD0
stuck `lo` against its pull-up while every other check passed. Fix: **`dtoverlay=uart0-pi5`
instead, with no `enable_uart=1`.**

**The servos didn't move.** Both connectors were reversed. VCC is the middle pin, so a reversed
plug still powers the servo — it holds position and resists a nudge, but its signal pin is on
ground. Diagnostic: **nudge the head by hand.** Resists = powered, so it's signal. Limp = no
power.
| [`PHASE1-RESULTS.md`](PHASE1-RESULTS.md) | Measured verification results for the Pi and charger |
| [`NOTES-dog-cam-integration.md`](NOTES-dog-cam-integration.md) | One parked finding. No code |

## Operating rule

**Always `sudo poweroff` and wait for the LED to settle before cutting power** — including
before flipping the expansion board's switch, which cuts power instantly with no warning to
the OS. Yanking power from a running Pi is the most common cause of a corrupted SD card.

## Conventions

- Every command is labelled **PowerShell (PC)** or **SSH (Pi)**. No guessing which shell.
- Both scripts report **UNKNOWN** rather than inventing a value they couldn't determine.
- Findings cite the file they came from, so you can check the reasoning rather than trust it.

## Reference

Hiwonder's source is public and worth reading — it's the ground truth for everything here:
[`github.com/Hiwonder/turbopi`](https://github.com/Hiwonder/turbopi), and the docs mirror at
[`github.com/Hiwonder-docs/TurboPi`](https://github.com/Hiwonder-docs/TurboPi).
