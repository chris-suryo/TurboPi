# Verifying the Pi 5 and your charger

Goal: know whether your Pi 5 works and whether your charger is adequate — **measured, before
you build a robot on top of them.** You specifically asked about undervoltage and the USB
current cap. Both are checked here, and the second one matters more than most people realise.

Run: `scripts/check_power.sh` (on the Pi).

---

## Why this is worth a whole phase

The Pi 5's official supply is **5V / 5A (27W) USB-C PD**. That is unusually demanding, and the
failure mode when you miss it is nasty: the Pi boots, looks completely healthy, and then
misbehaves later under load in ways that look like software bugs.

There are **two separate problems**, and conflating them is how people lose a day.

### Problem 1: undervoltage

The supply can't hold 5V. Voltage at the board should stay above **4.75V**; the undervoltage
flag trips around **4.63V**. The Pi responds by throttling, and eventually by behaving
erratically. Classic causes: an underpowered brick, but *very* often just a bad or thin USB-C
cable — cable resistance is a real and underrated culprit.

### Problem 2: the USB peripheral current cap — this is the one you were warned about

At boot the Pi 5 asks the supply, over USB-PD, what it can deliver.

- Supply advertises **5A** → USB peripherals get a **1.6A** budget.
- Anything less, or no PD negotiation at all → USB peripherals get **600mA**, total, shared
  across every USB device.

**The Pi itself runs fine either way.** It's the *peripherals* that starve. And they starve
intermittently, under load — exactly the "weird intermittent failures" you wanted to get ahead
of. A USB camera that works on the bench and drops out when the motors spin is this bug.

You can override it with `usb_max_current_enable=1` in `/boot/firmware/config.txt`. Understand
what that flag does: **it tells the firmware to *assume* the supply is capable. It does not
make your charger deliver more current.** If the supply can't actually source it, you've
traded a conservative limit for a brownout. It's the right call with a genuinely capable
supply that just negotiates badly; it's a foot-gun otherwise.

### Why this matters more for you than for a desktop Pi

On the finished robot, **the Pi is not powered over USB-C at all.** It's powered from the
expansion board, which runs off the 7.4V battery pack. That path involves **no PD negotiation
whatsoever**, so the 600mA cap is the default outcome — and your USB camera has to live inside
it.

So there are really two power questions, and the bench test only answers the first:
1. Is the charger good enough to develop on? ← Phase 1, now
2. Is the on-robot supply good enough to drive a camera? ← re-measured after assembly

This is flagged rather than asserted: exactly how the Hiwonder board feeds the Pi 5 isn't
something to take on faith. We measure it on your hardware.

---

## What the script checks, and how to read it

### Section 1 — throttle flags (the authoritative undervoltage check)

`vcgencmd get_throttled` returns a bitmask. The split that matters:

- **bits 0–3** — what is happening *right now*
- **bits 16–19** — what has happened *at any point since boot*

| Bit | Meaning |
|---|---|
| 0 / 16 | **Under-voltage** |
| 1 / 17 | ARM frequency capped |
| 2 / 18 | Currently throttled |
| 3 / 19 | Soft temperature limit |

`throttled=0x0` is the clean result. **A sticky bit (16–19) still counts as a failure** even if
the current bits are clear — it means it already happened once, and it will happen again.

Bit 3/19 is thermal, not electrical. If that's what's set, you have a cooling problem, not a
charger problem — different fix entirely.

### Section 2 — PMIC rails

`vcgencmd pmic_read_adc` reads the Pi 5's power management IC directly. `EXT5V_V` is the actual
voltage arriving from your charger. Below 4.75V is out of spec. If the command isn't available
on your firmware the script says so rather than inventing a number — Section 1 remains the
authoritative check either way.

### Section 3 — the USB budget

Read from the device tree, which is where the firmware records what it decided at boot:

| Path | Meaning |
|---|---|
| `/proc/device-tree/chosen/power/max_current_ma` | what the supply *claimed* it could deliver |
| `/proc/device-tree/chosen/power/usb_max_current_enable` | `0` = 600mA limit, non-zero = 1.6A |

`max_current_ma` of `0` means no PD negotiation happened at all — typical of a non-PD charger,
a USB-A brick with an A-to-C cable, or power arriving over the GPIO header.

**This is how we identify your charger.** You said you can't read the label, which is fine:
the Pi reports what it actually negotiated, and that's a better number than the label anyway.

### Section 4 — kernel log

Independent confirmation. `dmesg` records undervoltage events as they occur.

### Section 5 — load test (`--stress`)

**Do not skip this.** Idle flags prove almost nothing — a marginal supply looks perfect at idle
and fails when the CPU ramps. The script loads every core, watches the flags live, then
re-reads the sticky bits.

---

## Running it

**SSH (on the Pi)** — snapshot first:

```bash
./check_power.sh
```

**SSH (on the Pi)** — then the one that actually proves something:

```bash
./check_power.sh --stress
```

Paste the full output back and I'll give you a verdict. If a value came back UNKNOWN, that's
reported as UNKNOWN — no number will be invented to fill a gap.

## Interpreting the outcome

| Result | Meaning | Action |
|---|---|---|
| No undervoltage under load, `usb_max_current_enable` non-zero | Charger is genuinely fine | Proceed |
| No undervoltage, but `usb_max_current_enable` = 0 | Pi is fine; peripherals capped at 600mA | Decide on the flag vs. a 5A PSU — see above |
| Undervoltage sticky bit set | Marginal supply **or a bad cable** | Try a better cable first, then replace the PSU |
| Undervoltage active right now | Inadequate | Stop. Don't build on it |

A note on the open-box Pi itself: a clean `--stress` run with no undervoltage and no unexpected
thermal throttling is also decent evidence the board is healthy. It isn't a full RMA-grade
test, but combined with a successful boot, network, and USB enumeration, it covers the
failure modes that actually show up in practice.
