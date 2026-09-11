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

## Current status

| Phase | State |
|---|---|
| 0. Request the TurboPi Pi 5 image from Hiwonder | **Do this first — it has lead time** |
| 1. Verify Pi 5 + charger | Ready to run |
| 2. Physical fit check (52Pi case, RTC battery, board revision) | Pending |
| 3. Flash TurboPi image, headless boot | Pending |
| 4. Assemble and drive | Pending |
| 5. Camera and built-in demos | Pending |
| 6. Concepts | Written, read as you go |

**The image is the real blocker.** Hiwonder don't publish it — their docs ask you to email
`support@hiwonder.com` with an order number. Bought at Micro Center means a retail receipt
instead, so start that conversation early. Fallback (clean Raspberry Pi OS + the public GitHub
source) is documented and viable.

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
| [`scripts/check_power.sh`](scripts/check_power.sh) | Power verification. Run on the Pi |
| [`scripts/check_hardware.py`](scripts/check_hardware.py) | Probes serial, I2C, gpiochip, camera. Run on the Pi |
| [`NOTES-dog-cam-integration.md`](NOTES-dog-cam-integration.md) | One parked finding. No code |

## Conventions

- Every command is labelled **PowerShell (PC)** or **SSH (Pi)**. No guessing which shell.
- Both scripts report **UNKNOWN** rather than inventing a value they couldn't determine.
- Findings cite the file they came from, so you can check the reasoning rather than trust it.

## Reference

Hiwonder's source is public and worth reading — it's the ground truth for everything here:
[`github.com/Hiwonder/turbopi`](https://github.com/Hiwonder/turbopi), and the docs mirror at
[`github.com/Hiwonder-docs/TurboPi`](https://github.com/Hiwonder-docs/TurboPi).
