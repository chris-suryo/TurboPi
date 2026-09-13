# Phase 1 results — Pi 5 and charger verification

**Date:** 2026-09-13 · **Verdict: both pass.** Proceed to assembly.

Measured on the bare board before mounting, per `docs/01-pi5-power-verification.md`.

---

## Summary

| Question | Answer |
|---|---|
| Is the open-box Pi 5 healthy? | **Yes** — Raspberry Pi 5 Model B Rev 1.1, boots, networks, stable under load |
| Does the Apple 20W charger hold 5V? | **Yes** — 9 mV of sag across 120s at full CPU load |
| Any undervoltage? | **None**, current or sticky, before or after load |
| Is cooling adequate? | **Yes** — 34.5°C idle, 52.1°C peak, no thermal throttling |
| USB peripheral budget | **600 mA** (the low limit), as predicted for a 3A supply |

## The numbers

**System:** Raspberry Pi 5 Model B Rev 1.1 · Debian 13 "Trixie" · kernel `6.18.34+rpt-rpi-2712`

**Throttle flags — `throttled=0x0` throughout.** Every bit clear, both the "now" bits (0–3) and
the sticky "since boot" bits (16–19), before *and* after the load test. This is the clean result.

**Input rail (`EXT5V_V`):**

| | Voltage |
|---|---|
| Idle | 5.0585 V |
| After 120s at full load | 5.0491 V |
| Spec floor | 4.75 V |
| Undervoltage trip | ~4.63 V |

**9 mV of droop under full load.** That's an excellent result — it says both the adapter and
the cable are doing their job. Cable voltage drop is the usual culprit in Pi 5 undervoltage
reports, and there's essentially none here.

**Thermals under sustained load** — 34.5°C idle, rising to a plateau around 51–52°C and
holding. The Pi 5 doesn't soft-throttle until ~80°C, so there's roughly 28°C of headroom. The
official Active Cooler is comfortably ahead of the workload.

## What the 600 mA budget means

`usb_max_current_enable` reads `0`, so USB peripherals share a **600 mA** budget rather than
1.6 A. Expected: the Pi 5 grants the high limit only to a supply advertising 5 A, and this one
advertises 3 A.

**This is not a problem to fix**, for a specific reason: the finished robot doesn't take USB-C
power at all. The Pi is fed from the expansion board off the 7.4 V battery — a path with no
USB-PD negotiation anywhere in it — so the 600 mA cap is the default outcome *regardless* of
what adapter sits in a drawer. Buying the 27 W supply would not change the robot's behaviour.

The camera has to live inside that budget either way, so we re-measure once assembled. If it
misbehaves then, `usb_max_current_enable=1` is the lever — and by then we'll know the battery
path can actually source the current, which is the part that matters.

### One honest gap

`/proc/device-tree/chosen/power/max_current_ma` was **not present** on this firmware, so the
script could not read what the supply advertised and said `UNKNOWN` rather than guessing.
`usb_max_current_enable` was readable and is the value that actually governs behaviour, so
nothing important is missing — but the "3000 mA" figure was inferred from the adapter label,
not measured.

## Consequence for the software build

The board is running **kernel 6.18**, well past the 6.6.45 release where RP1's gpiochip was
renumbered. So `HiwonderSDK/led.py` and `key.py`, which hardcode `gpiod.Chip('gpiochip4')`,
are **very likely to fail** on this system — the risk flagged in
`docs/02-turbopi-pi5-compatibility.md`, now close to confirmed rather than hypothetical.

`check_hardware.py` confirmed it: **RP1 is `gpiochip0`** on this kernel, not `gpiochip4`.

**But this may not actually break anything**, and the first version of the script overstated it.
The device list also contains a `gpiochip4` node, and Raspberry Pi ships a udev rule that
creates `/dev/gpiochip4` as a **backward-compatibility symlink** to the real RP1 chip. If that's
what it is here, `gpiod.Chip('gpiochip4')` resolves to the right chip and the SDK code works
unmodified.

**Confirmed by `ls -l /dev/gpiochip*`:**

```
lrwxrwxrwx 1 root root 9 ... /dev/gpiochip4 -> gpiochip0
```

**It is the compatibility symlink. No patch needed.** `HiwonderSDK/led.py` and `key.py` work
unmodified on this system. The risk flagged throughout this project turns out not to apply
here — the udev rule absorbs it.

Worth keeping in mind rather than forgetting: this rests on a **udev rule, not the kernel**, so
it is a weaker guarantee than the chip numbering would be. If a future update drops the rule,
those two modules break — LED and buttons only. Looking the chip up by label is still the
durable fix, just no longer urgent.

## Interface enablement (same session)

| Interface | Before | After `raspi-config do_i2c 0` + `enable_uart=1` + reboot |
|---|---|---|
| I2C bus 1 | absent | ✅ `/dev/i2c-1` present |
| Header UART | absent | ✅ `/dev/ttyAMA0` present |
| `/dev/serial0` | → `ttyAMA10` (debug UART) | → **`ttyAMA0`** (header UART) |
| Serial console | `console=serial0,115200` in cmdline.txt | removed |

The `serial0` remap is the notable one: enabling the UART moves that alias onto the header
port, so leaving the console entry in place would have put a kernel console on the exact
device the robot controller uses.

## Decisions this settles

- **Don't buy the 27 W PSU.** The measured sag is negligible and the robot won't use USB-C power.
- **The open-box gamble paid off.** Board is healthy under sustained load.
- **Keep the fitted Active Cooler.** 28°C of thermal headroom at full tilt.
- **Mounting the Pi is now safe** — the foundations are verified, which was the whole point of
  doing this before assembly.


---

# Phase 4 — first hardware bring-up (2026-09-13)

**Result: the robot is alive.** 7/10 stages passed on the first successful run; the three
"failures" are one skipped stage and two that need a closer look, not blockers.

| Stage | Result |
|---|---|
| Serial link to controller board | ✅ **8.01 V** battery read back |
| Buzzer | ✅ Board acts on commands |
| Motors, individually | ✅ **All four on the correct ports** — M1 FL, M2 FR, M3 RL, M4 RR |
| Ultrasonic (0x77) | ✅ 369–380 mm steady, dropped to 112 mm with a hand in front |
| Line sensor (0x78) | ✅ Reads; all-True is expected on a stand with no surface beneath |
| USB camera | ✅ 640×480 frame captured |
| Board RGB LEDs | ❓ Not observed cycling — see below |
| Mecanum vectors | ⏭ Skipped this run |
| Pan-tilt servos | ❓ Reported not smooth — needs detail |

## The bug that blocked everything, and its fix

The board was silent for an entire evening. Not hardware — **the Pi's UART config.**

Hiwonder's manual says `enable_uart=1` + `dtparam=uart0=on`. On a Pi 5 whose bootloader is
from 2025-02 or later (this one: **2025-06-13**), `enable_uart=1` moves the firmware console
onto UART0 and leaves it in a state Linux cannot fully take over. The signature:

```
15: a4    pu | lo // GPIO15 = RXD0      ← receive line stuck LOW against its pull-up
```

Replacing both lines with `dtoverlay=uart0-pi5` flipped it to `hi` on the next boot, and the
board answered immediately.

What made this hard: **every check passed except the one that mattered.** `/dev/ttyAMA0`
existed, the pins showed the right alt-function, the port opened, I2C worked through the same
header, both sensors answered, the board powered the Pi. Three wrong hypotheses were killed
by evidence (unpowered board, misaligned header, wrong board revision) before a forum search
for the exact `pinctrl` signature found other Pi 5 owners with the same fault and the fix.

Lesson recorded in `docs/05-concepts.md`: when a vendor recipe is for a fast-moving platform,
check its date against your firmware's.
