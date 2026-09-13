# Hardware inventory

Three columns matter here: what you have, what has been **verified**, and what is still an
**assumption**. Assumptions are called out so they don't quietly become facts.

| Item | Notes | Status |
|---|---|---|
| Hiwonder TurboPi kit | Mecanum wheels, 2-DOF pan-tilt camera, ultrasonic. $129.99, Micro Center. **No Pi included** | Not yet inspected |
| Raspberry Pi 5, 8GB | **Open box** — arrived with the **official Raspberry Pi Active Cooler** already fitted (silver fins, Pi logo on the fan). Not yet powered on | **Unverified — Phase 1** |
| Spare black active cooler | Second cooler, 4-wire JST — unused. Same function, lower spec than the fitted official one | Spare / return with case |
| 52Pi aluminium case + heatsink fan | **Confirmed unusable on the robot** — the build sandwiches the Pi under 16mm standoffs sized for the kit's active cooler | Return candidate; check window |
| 128GB microSD + USB reader | | Not yet flashed |
| Raspberry Pi RTC battery | 2-pin JST on the Pi 5; fit before the sandwich goes together | Not yet fitted |
| USB-C charger | Model unknown, label unreadable | **Unverified — Phase 1** |
| Official 27W PSU | Not purchased | Decision pending on Phase 1 result |

## Host machines

- **Windows 11, PowerShell 5.1** — primary; flashing and SSH happen here
- **MacBook** — available as a fallback

## Open questions, resolved by looking rather than guessing

1. **Does the charger negotiate 5V/5A?** You can't read the label, so this comes from the Pi's
   own USB-PD negotiation report, not a spec sheet. → `scripts/check_power.sh`, Phase 1.
2. **Is the open-box Pi 5 healthy?** → boot + sustained load test, Phase 1.
3. **Does the kit include a preloaded microSD, and for which board?** Check the box. The Pi 4B
   and Pi 5 images have different default passwords, which identifies it in one login attempt.
4. **Which expansion board revision?** Current source uses a *serial* controller board; older
   TurboPi revisions differ. Determined by looking at the board in Phase 2.
5. ~~Does the 52Pi case fit the build?~~ **RESOLVED: no.** The kit's step-2 diagram shows the
   Pi sandwiched under M2.5×16 standoffs sized to clear an active cooler. A full enclosure
   can't live in that gap. Use the kit's cooler; the case is a return candidate.
6. ~~Is the kit standard or advanced?~~ **RESOLVED (2026-09-13): standard kit.** The
   plain-Python repo on Raspberry Pi OS under `/home/pi` is the correct target. No Docker,
   no ROS2. `07-build-from-clean-os.md` applies as written.
7. **Is SSH enabled by default on the Hiwonder image?** Unverified. Their docs demonstrate VNC
   throughout and never show an SSH login, so this is treated as unknown rather than assumed.
   If SSH is off, VNC is the way in and we enable SSH from there. See
   `06-running-the-demos.md` — VNC is wanted for the demos regardless, and needs no HDMI.

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
