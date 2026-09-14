# First hardware day — 2026-09-14

The day the two halves met. Everything here is measured on the robot, not inferred.

## The gate: the watchdog works

```
drive vx=0.20 vy=0.00 w=0.00 ttl=500ms duties=[-7, 7, -7, 7]
WATCHDOG FIRED - no /drive for 504 ms (ttl 500 ms), stopping motors
motors confirmed stopped
```

Telemetry after: `driving:false, stop_owed:false`. One drive command, no stop sent, motors
stopped by themselves 4 ms past the deadline and the robot acknowledged it. This is the
property everything else was gated on, and it now holds outside a simulator.

## End to end

| | |
|---|---|
| Gateway installed, autostarts | ✅ |
| `GetRunningFunc` patched — demo guard live | ✅ `demo_detection: true` |
| Port 9031 reachable from the Windows PC | ✅ `TcpTestSucceeded: True` |
| kona-tracker `.env` configured, Robot tab live | ✅ |
| **Driving, phone → PC → gateway → robot** | ✅ **works** |
| Camera through the app | ✅ works, laggy, low quality |

## Static friction is per-motor, and that is a veering problem

Measured with `find_min_duty.sh`, wheels off the ground, `MAX_DUTY` 35:

| Duty | Behaviour |
|---|---|
| 4–14 | nothing turns — audible hum only |
| 16–19 | **some wheels turn, others stall.** Which ones varied between runs |
| 20+ | all four turn together |

Two findings, and the second is the one that matters.

**The cap was too low.** Hiwonder's own source comments give the scale as `0~100`
(`线速度0(0~100)`), and their demos drive at 40–45 — `Avoidance.py` defaults to
`speed = 40`, colour tracking uses `set_velocity(45, ...)`. The gateway's original
`MAX_DUTY = 35` put *full stick* below the vendor's normal cruising speed. That was a
guess presented as caution, and it is why most of the stick did nothing.

**The four motors do not share a threshold.** The left pair break away first, the right
pair need more, and the order changed between runs — which is exactly how static friction
behaves, since it depends on where each rotor stopped relative to its magnetic detents.
Below the worst motor's threshold, "drive forward" turns some wheels and stalls others.
That is not slow driving, it is **veering**, and it would read as a wiring fault or bad
kinematics to anyone who met it without knowing.

### The fix, and why it is shaped this way

`MIN_DUTY` lifts the whole command above the floor. The obvious alternative — give each
motor its own floor — would fix the stall and **bend the robot's path**, because a mecanum
chassis travels in the direction set by the *ratios* between the four wheel speeds. So the
lift is applied to the magnitude and all four wheels are scaled by one factor: ratios
untouched, direction exactly preserved. Four tests pin that.

Settings in force after tuning, in `/etc/turbopi/gateway.env`:

```
TURBOPI_MIN_DUTY=25      # 5 above the measured all-four point, for run-to-run variation
TURBOPI_MAX_DUTY=55      # brisk but under Hiwonder's 0-100 ceiling
```

Slow mode (stick × 0.4) now asks for duty 37 — vendor cruising speed — instead of 14 and
a hum. **Not yet test-driven on carpet.**

## Battery

| When | Volts |
|---|---|
| 2026-09-13, after bring-up | 8.01 |
| 10:08, after ~14 h powered on | 7.75 |
| 10:25, after the coarse sweep | 7.56 |
| 10:40, after the fine sweep | 7.52 |
| 11:05 | **7.29** |

The robot was left powered on overnight — that, not driving, is what spent the pack. The
curve steepens near the end, which is what the last row is.

**Why the gateway refuses below 7.0 V**, since it is not the cell limit: two 18650s in
series are full at 8.4 V and safe down to about 6.0–6.4 V, so 7.0 leaves roughly a third
of the capacity unused. The binding constraint is the **Pi browning out** — the expansion
board regulates the pack to 5 V, the pack sags hard under motor load, and an unclean power
loss is what corrupts SD cards. Hiwonder's own docs alarm at 7.1 V. Lowering it to 6.8 is
one line, but only with real data on how far it dips while driving.

**Still unmeasured: runtime while actually driving.**

## Two bugs in my own tooling, found by running it

**The proof script scored a pass as a failure.** Its verdict parsed telemetry with
`grep -o '"driving": *[a-z]*' | awk '{print $2}'`. The gateway serialises JSON compactly —
no space after the colon — so there was no second field and `driving` came back empty.
Parsed with `python3` now.

**The same script printed the shared secret.** The failure branch echoed the curl header
array, expanding the token into the terminal minutes after it had been deliberately
redacted. The token now lives only inside the header array; the failure branch prints a
literal `$(sudo cat ...)` for the operator to run. **The leaked secret was rotated.**

## Open

1. Test drive on carpet with the new duty floor — does it still pull to one side?
2. **Has anyone confirmed left is left?** The robot has been driven, but the sign of `vy`
   and `omega`, and of pan/tilt, is still unverified against the physical machine.
3. Latency, measured. Every number except the transport benchmark is an estimate.
4. go2rtc on the PC for video — the single biggest improvement available.
5. DHCP reservation for `10.0.0.3`. Still not done; the address has held so far by luck.
