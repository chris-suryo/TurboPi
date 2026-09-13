# Hardware inventory

Three columns matter here: what you have, what has been **verified**, and what is still an
**assumption**. Assumptions are called out so they don't quietly become facts.

| Item | Notes | Status |
|---|---|---|
| Hiwonder TurboPi kit | Mecanum wheels, 2-DOF pan-tilt camera, ultrasonic. $129.99, Micro Center. **No Pi included**. Standard tier | **Assembly in progress** |
| Raspberry Pi 5, 8GB | **Open box** — **Rev 1.1**, official Active Cooler fitted. Boots, networks, no undervoltage across 120s full load, peaks 52°C | ✅ **VERIFIED HEALTHY (2026-09-13)** |
| Spare black active cooler | Second cooler, 4-wire JST — unused. Same function, lower spec than the fitted official one | Spare / return with case |
| 52Pi aluminium case + heatsink fan | **Confirmed unusable on the robot** — the build sandwiches the Pi under 16mm standoffs sized for the kit's active cooler | Return candidate; check window |
| 128GB microSD + USB reader | Flashed 2026-09-13 with Raspberry Pi OS 64-bit (Imager v2.0.11.1) | **Flashed** |
| Raspberry Pi RTC battery | 2-pin JST on the Pi 5; fit before the sandwich goes together | Not yet fitted |
| USB-C charger | Apple 20W PD, A2305, `5V⎓3A`. **Measured: 5.0585V idle → 5.0491V under full load — 9mV of sag.** Rock solid | ✅ **Good for bench use.** USB budget capped at 600mA |
| Official 27W PSU | Not purchased | **Probably never needed** — see below |

## Bench-test configuration (as flashed)

No secrets recorded here — passwords are not stored in this repo.

| Setting | Value |
|---|---|
| OS | Raspberry Pi OS (64-bit) — **Debian 13 "Trixie"**, kernel `6.18.34+rpt-rpi-2712` (confirmed on the board) |
| Hostname | `turbopi` → `turbopi.local` |
| User | `pi` |
| Wi-Fi SSID | `2101 (2.4 Ghz)` — note the space and lowercase `hz`; the 5GHz SSID is spelled differently (`2101 (5GHz)`) |
| Band choice | 2.4GHz deliberately — better range for a robot that drives around; the Pi 5 is dual-band so either works |
| SSH | Enabled, password auth |
| Raspberry Pi Connect | Not enabled (cloud service, unnecessary for LAN use) |
| LAN | Router `10.0.0.1`, Mac `10.0.0.63` — expect the Pi on `10.0.0.x` |

## Host machines

- **Windows 11, PowerShell 5.1** — primary; flashing and SSH happen here
- **MacBook** — available as a fallback

## Open questions, resolved by looking rather than guessing

1. ~~Does the charger negotiate 5V/5A?~~ **RESOLVED: no.** It's an Apple 20W A2305, `5V⎓3A`.
   The Pi 5 will run on it but caps USB peripherals to 600mA. Remaining question is narrower:
   does it hold 5V under sustained load? → `scripts/check_power.sh --stress`.
2. ~~Is the open-box Pi 5 healthy?~~ **RESOLVED: yes.** Full results in `PHASE1-RESULTS.md`.
3. **Does the kit include a preloaded microSD, and for which board?** Check the box. The Pi 4B
   and Pi 5 images have different default passwords, which identifies it in one login attempt.
4. ~~Does `/dev/gpiochip4` exist as a compat symlink?~~ **RESOLVED: yes.**
   `/dev/gpiochip4 -> gpiochip0`. TurboPi's hardcoded `gpiochip4` works **unmodified**. The
   gpiochip risk tracked through this project does not apply on this system.
5. ~~What does `enable_uart=1` produce on kernel 6.18?~~ **RESOLVED by measurement.** It
   creates `/dev/ttyAMA0` **and remaps `/dev/serial0` from `ttyAMA10` to `ttyAMA0`.** So a
   `console=serial0` entry becomes a real conflict with the robot board the moment the UART is
   enabled — removing it was necessary, not merely tidy.
6. ~~Which expansion board revision?~~ **RESOLVED: "Adapter5A V1.1"** — the serial-controller
   board the current SDK targets. Confirmed from silkscreen, and by the board answering over
   serial once the Pi's UART config was corrected.
7. ~~Does the 52Pi case fit the build?~~ **RESOLVED: no.** The kit's step-2 diagram shows the
   Pi sandwiched under M2.5×16 standoffs sized to clear an active cooler. A full enclosure
   can't live in that gap. Use the kit's cooler; the case is a return candidate.
8. ~~Is the kit standard or advanced?~~ **RESOLVED (2026-09-13): standard kit.** The
   plain-Python repo on Raspberry Pi OS under `/home/pi` is the correct target. No Docker,
   no ROS2. `07-build-from-clean-os.md` applies as written.
9. ~~Where did `cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC` go in OpenCV 5?~~ **RESOLVED: all three
   fisheye calibration flags moved to the top-level `cv2.*` namespace.** One-line fix recorded
   in `02-turbopi-pi5-compatibility.md`; affects re-calibration only, not the robot.
10. **Is SSH enabled by default on the Hiwonder image?** Unverified. Their docs demonstrate VNC
   throughout and never show an SSH login, so this is treated as unknown rather than assumed.
   If SSH is off, VNC is the way in and we enable SSH from there. See
   `06-running-the-demos.md` — VNC is wanted for the demos regardless, and needs no HDMI.

## Why the 20W charger probably doesn't matter

The Pi 5 wants 5V/5A. This supply gives 5V/3A, so on the bench the USB peripheral budget will
be the low one (600mA) unless overridden.

**But the finished robot never uses USB-C power.** The Pi is fed from the expansion board off
the 7.4V battery pack, a path with no USB-PD negotiation in it at all. So the charger's rating
governs bench work only, and 15W comfortably covers a bare Pi 5 under CPU load (roughly
11-13W including the cooler fan).

Buy the 27W supply only if the stress test shows undervoltage, or if you later want to run the
Pi 5 on the desk with power-hungry USB devices attached.

**Cable matters as much as the brick.** It must be USB-C to USB-C — an Apple 20W often ships
with USB-C to Lightning, which won't fit. A thin or poor-quality cable is one of the most
common causes of Pi 5 undervoltage, because the voltage drop happens in the cable rather than
the supply.

## The config that actually matters — corrected

**Hiwonder's manual is wrong for current Pi 5 firmware.** Their `enable_uart=1` +
`dtparam=uart0=on` produced a UART0 that looked configured but had RXD0 stuck low; the board
could never be heard. Working configuration, measured:

```
dtoverlay=uart0-pi5
usb_max_current_enable=1
avoid_warnings=1
```

`enable_uart=1` must **not** be present. Bootloader on this board: 2025-06-13, i.e. after the
2025-02 firmware change that altered `enable_uart` behaviour on Pi 5. Full story in
`07-build-from-clean-os.md`.

## Known constraints

- **The Pi is not powered over USB-C on the finished robot.** It runs from the expansion board
  off the 7.4V battery. That path does no USB-PD negotiation, so the 600mA USB peripheral cap
  is the default outcome — and the USB camera lives inside that budget. See
  `01-pi5-power-verification.md`.
- **TurboPi's SDK hardcodes `/home/pi/TurboPi`** (`HiwonderSDK/mecanum.py`). Use username `pi`.
- **The TurboPi OS image is not a public download** — Hiwonder require an email with an order
  number, and you have a Micro Center receipt instead. **This is no longer on the critical
  path.** The public GitHub repo is self-contained (verified), and clean Raspberry Pi OS is a
  better base for a Pi 5 than a 2023-era vendor image. See `02-turbopi-pi5-compatibility.md`;
  build per `07-build-from-clean-os.md`.
