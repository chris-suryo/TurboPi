# Assembly and staged bring-up

The principle: **bring up one subsystem at a time, smallest first.** Each of these can fail on
its own, for its own reasons. Assembling everything and then running the full demo means a
failure could be any of eight things, and you'll spend the evening bisecting hardware.

---

## Follow the video, not the written steps

Hiwonder's written assembly section is **step titles only** — all the actual detail is in
images and video. They link a playlist, and it is the thing to follow:

**https://www.youtube.com/playlist?list=PLFbzd0m6AcmLzo53o2Tsa20BS350rWGMj**

Their step order, for orientation while you watch:

1. Ultrasonic sensor and pan-tilt servo
2. Mecanum wheel chassis
3. Line follower
4. Pan-tilt servo bracket
5. Raspberry Pi 5 and expansion board
6. Ultrasonic + line follower wiring
7. Battery box
8. U-shaped bracket and mecanum wheels
9. Head servo and camera
10. Wiring
11. Top bracket

This doc does **not** duplicate the mechanical steps — the video is better at that than prose
ever will be. What follows is the set of things the video won't flag: the traps, the wiring
table, and one ordering trick that saves you a calibration session.

---

## While you have the box open — four things to check

Quick, and each one answers a question that's currently open:

1. **Is there a microSD card?** If so, don't wipe it — it may be a working image. We'd image
   it to a file first and see which board it targets.
2. **Is there a booklet with a QR code / Drive link?** Hiwonder put the "Source code and
   system image" download there for kit owners. If it's there, you skip emailing support.
3. **Photograph the expansion board silkscreen.** Confirms whether you have the current
   serial-controller board or an older I2C-era revision.
4. ~~Which kit tier is it?~~ **Resolved: standard kit.** The plain-Python path is correct.

### Standard vs advanced — resolved, kept for reference

There are two TurboPi software tiers, and they're genuinely different stacks:

| | Standard | Advanced |
|---|---|---|
| Software | Plain Python, `github.com/Hiwonder/turbopi` | **Docker + ROS2** |
| OS / user | Raspberry Pi OS, `/home/pi` | Ubuntu, `/home/ubuntu` |
| Public source? | **Yes, fully** | No — distributed as container images |

**Confirmed standard**, so everything in this project applies as written: the public repo,
`/home/pi/TurboPi`, and `07-build-from-clean-os.md`. Every demo on your list — line following,
colour tracking, face tracking, obstacle avoidance — is in the standard tier.

The advanced tier only matters if you later want ROS2 / AI-model features, which need
Hiwonder's containers and can't be rebuilt from the public repo.

---

## Before assembly

### 1. The 52Pi aluminium case is out — confirmed

**Resolved by the kit's own step-2 diagram**, not a guess any more. The Pi is built into a
sandwich:

```
        expansion board          ← secured with M2.5*6 screws
   M2.5*16 dual-pass standoffs   ← 16mm, sized to clear the cooler
      Raspberry Pi 5 + active cooler (heatsink + fan)
   M2.5*6 single-pass standoffs  ← feet underneath
```

Those 16 mm standoffs exist specifically to clear an **active cooler** sitting on the Pi. A
full aluminium enclosure cannot live in that gap, and the expansion board has to reach the
40-pin header anyway.

**Use the cooler that comes with the kit.** The 52Pi case is a return candidate — it's still
fine for bench testing before you mount the Pi, and it'd be useful if you ever repurpose the
Pi 5 for something else, so check your return window and decide.

### 2. Before you screw the sandwich together — four checks

This is the point of no easy return, so spend two minutes here.

**a. Bench-test the Pi first.** See the callout below — this is the big one.

**b. Fit the RTC battery.** Much easier now than later. It's a 2-pin JST connector on the Pi 5
(not the GPIO header), and the battery usually has an adhesive back so it can sit on the Pi's
underside. If there's a small flat black part in your step-2 parts bag, that's likely it.

**c. Check you can still reach the microSD slot.** The slot is on the edge of the Pi 5. Once
the sandwich is built and bolted into the chassis, **can you still get a card in and out?**
You will reflash this card more than once. Verify it before you tighten anything — I can't
tell you the answer from here, because it depends on the chassis geometry in front of you.

**d. Seat the 40-pin header fully.** See the technique section below — this is the single
most common source of "it worked yesterday" faults.

---

## Which fan, and where it plugs in

**Verified:** Hiwonder's own expansion-board documentation lists board A — the one TurboPi
uses — as having *"a 4-channel motor interface, a 2-channel bus servo interface, a 6-channel
PWM servo interface, a 3-channel I2C interface, a 2-channel GPIO interface, two RGB lights, a
buzzer, two custom buttons, and three signal indicators."* **No fan interface.** (Board B, for
their humanoid kits, does have one. Board A does not.)

So the cooling fan does **not** connect to the expansion board. It connects to the
**Raspberry Pi 5's own dedicated fan connector** — item 13 in the Pi 5 feature list in
Hiwonder's docs, a small 4-pin JST header on the board.

### Telling two fans apart

If you have more than one fan, sort them by connector, not by looks:

| What it has | What it is | Where it goes |
|---|---|---|
| **4 wires** (e.g. red / black / yellow / blue) ending in a **small white 4-pin JST plug** | Pi 5-style active cooler: power, ground, tacho, PWM | **The Pi 5's fan header.** This is the one to use |
| 2–3 wires ending in **Dupont sockets** that push onto individual GPIO pins | Generic case fan (e.g. from a third-party case) | **Nowhere on this robot** — see below |

**A GPIO-pin fan cannot be used here at all.** The expansion board is a HAT that occupies the
entire 40-pin header, so there are no free pins for it once assembled. If your spare fan is
the 52Pi case's, set it aside with the case.

The 4-pin header is also the *better* option regardless: the Pi 5 controls that fan's speed
automatically from SoC temperature, and reads its tacho back. A fan wired to raw 5V just runs
flat out forever.

> **Don't force a connector that doesn't fit, and don't improvise wiring to the fan header.**
> Reversing a fan's power and ground can damage it or the Pi. If the plug doesn't match the
> Pi 5's fan socket cleanly, stop and check before applying pressure.

Note: the Raspberry Pi 5 does **not** include a cooler in its own retail box — it's a separate
accessory. So a cooler in your parts came from the kit, a case purchase, or (on an open-box
board) whoever had it before you.

**Resolved for this build:** the Pi arrived with the **official Raspberry Pi Active Cooler**
already fitted — silver fins, Raspberry Pi logo on the fan shroud. That's the good one; keep
it. **Do not pull it off** to swap in the other: it's held by a pre-applied thermal pad, and
removing it degrades the thermal contact for no gain. The spare black cooler stays in its bag.

---

## Seating the 40-pin header properly

A partially-seated HAT is the classic source of intermittent, maddening faults that look
exactly like software bugs — the board works, then doesn't, then does.

1. **Align before pressing.** The connector isn't keyed; only the standoffs enforce position.
   Line the expansion board's 40-hole connector over the Pi's 40 pins and **look along the row
   from the side** to confirm every pin is entering a hole. Off-by-one-row is the mistake, and
   it's easy to make and hard to spot afterwards.
2. **Press straight down, evenly.** Thumbs over the connector itself, directly above the pins
   — not on the middle of the board, and not on components. Rocking it on corner-first will
   bend pins.
3. **Feel for firm, even resistance, then a stop.** It should go down parallel. If one end
   drops and the other doesn't, lift **straight up** and start again. Don't lever it flat.
4. **Check the gap.** When seated, the board-to-board distance should match your standoff
   height (16 mm here). If the standoffs don't line up with their screw holes, the connector
   isn't home.
5. **Screws last, and gently.** Standoffs and screws exist to *hold* a board that is already
   seated, not to pull it down. If you're using screws to close a gap, something is wrong.
   Nylon threads strip easily — snug, not tight.

---

### 3. Identify your expansion board revision

Look at the board and note any silkscreen markings. This matters: the current TurboPi source
talks to a **serial** controller (`ros_robot_controller_sdk.py`, `/dev/ttyAMA0`), but older
TurboPi revisions used a different, I2C-era board. If yours is the older one, the SDK revision
you need differs. Photograph the board and we'll confirm which you have rather than assuming.

---

## Stop here and bench-test the Pi

**Nothing software-related is *required* before mounting the Pi.** No SD card needed, no
configuration. You can bolt it in now and flash later — the card goes in the slot on the Pi's
edge and the OS doesn't care when it arrives.

But this step is the **last easy moment**, and it's worth taking:

- Your open-box Pi 5 is still **unverified**. If it's faulty, you want to know while it's a
  bare board on a desk, not bolted under an expansion board inside a chassis.
- The charger is unverified too, and the undervoltage test is far easier on the bench.
- That was your stated reason for this whole project: verify the foundations before building
  on top of them. This is the moment that stops being possible for free.

It costs roughly **45 minutes** and it's the same work you'd do later anyway:

1. Flash the card — [`03-headless-boot.md`](03-headless-boot.md)
2. Boot the bare Pi, SSH in
3. `./check_power.sh --stress`

Then mount it, knowing the board and the power are good.

**If you'd rather keep your assembly momentum:** mounting is only four screws, so this is
recoverable rather than irreversible — just make sure you've done check **c** above, so you
can get the card in and out without disassembly. The risk you're accepting is diagnosing a
bad Pi or a marginal charger through a fully-built robot, which is a materially worse
experience.

## The one ordering trick: centre the servos before you tighten the pan-tilt

**Do this and you skip a whole calibration session.**

Hiwonder's servos **auto-centre when the board powers on**. If you bolt the U-shaped bracket
onto the servo shaft while the servo happens to be sitting at some arbitrary angle, your
pan-tilt ends up permanently offset — and the software trim range is only **1372–1627 µs**
(about **±13°**) around the 1500 µs centre. Outside that, software can't save you and you have
to take it apart again.

Hiwonder document this only as an *after-the-fact repair* ("calibrate large deviation"):

> Power off → remove the screw on the servo's main shaft → pull the bracket off → power on and
> let the servo self-centre → power off → refit the bracket square → replace the screw.

Notice that's just "let it centre before you attach the bracket." **So do it in that order the
first time:**

1. Assemble up to the point where the servos are mounted but the brackets are **not yet
   screwed down**.
2. Power the board on. Servos snap to centre; the buzzer beeps.
3. Power off.
4. Fit the brackets square to the centred servos, then screw them down.

> **Do not rotate a servo by hand while it's powered.** It actively fights you, and the gears
> are plastic. If you nudge one during step 4, power-cycle and let it re-centre.

Fine trim after that is a software offset, covered when we do bring-up.

---

## Assembly wiring

The mechanical steps are in the video. These are the connections to get right:

| Connection | Port |
|---|---|
| Front-left motor | **M1** |
| Front-right motor | **M2** |
| Rear-left motor | **M3** |
| Rear-right motor | **M4** |
| Pan-tilt **pitch** (up/down) servo | **No. 1** |
| Pan-tilt **pan** (left/right) servo | **No. 2** |
| Ultrasonic sensor | **No. 3** |
| 4-channel line-following sensor | **No. 4** |

Motor order matters — the mecanum kinematics in `mecanum.py` assume this exact numbering. Swap
two and the robot will move in genuinely confusing ways (a classic symptom: commanding
"forward" makes it spin or strafe).

**Mecanum wheel orientation matters even more.** The rollers form an **X pattern** when viewed
from above. Get one wheel mirrored and omnidirectional motion breaks in a way that looks like a
software bug. Check this before the first drive test.

### Battery safety

- Charge the two 18650s fully before first use (~3 hours; the charger LED goes green)
- **Do not reverse the polarity** — check the `+`/`−` markings in the holder
- Battery case switch **OFF** while inserting cells
- Unplug the charger when done; don't leave cells on charge indefinitely

---

## Bring-up order

Run each step and confirm before moving on. All of these are **SSH (Pi)**.

> **Note on `KEY1`.** Hiwonder's docs say pressing **KEY1** on the expansion board runs a
> built-in self-test that exercises every servo and motor in a known order — a genuinely good
> wiring check. **It won't exist on our clean-OS build**, because it's provided by their
> image's `hw_button_scan.service`. The staged bring-up below replaces it, and is more
> informative anyway: the self-test tells you *something* is wrong, while these steps tell you
> *which* thing.

### Step 0 — interfaces present

```bash
./check_hardware.py
```

Confirms `/dev/ttyAMA0`, `/dev/i2c-1`, the gpiochip layout, the camera, and — importantly —
that **no serial login console is squatting on ttyAMA0**. That last one is a real trap: if a
getty holds the UART, motor commands get corrupted intermittently.

Fix anything flagged here *before* powering the motors.

### Step 1 — talk to the controller board

The single most important test. If this works, the whole drivetrain path works.

```bash
cd /home/pi/TurboPi
python3 -c "
import HiwonderSDK.ros_robot_controller_sdk as rrc
b = rrc.Board()
print('battery mV:', b.get_battery())
"
```

A plausible voltage (roughly 6000–8400 mV on charged 18650s) means serial is up, the packet
protocol matches, and the board's MCU is alive and responding.

Nothing, or `None`? Stop. It's one of: UART not enabled, getty on the port, wrong baud, board
unpowered, or the power switch is off. Don't proceed until this returns a number.

### Step 2 — one motor

**Put the robot on a stand so the wheels can't touch the ground.** It will try to drive away.

```bash
python3 -c "
import HiwonderSDK.ros_robot_controller_sdk as rrc, time
b = rrc.Board()
b.set_motor_duty([[1, 35]]); time.sleep(1); b.set_motor_duty([[1, 0]])
"
```

Confirm **which** wheel moved and **which direction**. Repeat for motors 2, 3, 4. This is how
you catch swapped connectors while it's still trivial to fix.

### Step 3 — all four, via the mecanum layer

Still on the stand:

```bash
cd /home/pi/TurboPi/MecanumControl
python3 Car_Forward_Demo.py
```

All four wheels should turn in the direction that would drive it forward.

### Step 4 — mecanum motion on the floor

Now on the ground, with space:

```bash
python3 Car_Move_Demo.py      # general motion
python3 Car_Slant_Demo.py     # diagonal — the mecanum party trick
python3 Car_Turn_Demo.py      # rotation in place
```

`Car_Slant_Demo.py` is the one that proves the wheels are mounted correctly: the robot should
translate diagonally **without rotating**. If it rotates or crabs oddly, a wheel is mirrored or
a motor is on the wrong port.

Expect some drift. There are no wheel encoders, so the four motors are never perfectly matched.
This is normal — see `05-concepts.md`.

### Step 5 — pan-tilt servos

```bash
python3 -c "
import HiwonderSDK.ros_robot_controller_sdk as rrc, time
b = rrc.Board()
b.pwm_servo_set_position(0.5, [[1, 1500], [2, 1500]])  # both to centre
time.sleep(1)
b.pwm_servo_set_position(0.5, [[2, 1300]]); time.sleep(1)
b.pwm_servo_set_position(0.5, [[2, 1700]]); time.sleep(1)
b.pwm_servo_set_position(0.5, [[2, 1500]])
"
```

Servo 1 tilts, servo 2 pans. Move in **small steps** and stop if anything binds or buzzes — a
stalled servo draws maximum current and strips its own gears.

### Step 6 — ultrasonic sensor

```bash
python3 -c "
import HiwonderSDK.Sonar as Sonar, time
s = Sonar.Sonar()
for _ in range(10):
    print(s.getDistance(), 'mm'); time.sleep(0.3)
"
```

Put your hand in front of it and watch the number drop.

### Step 7 — line sensor

```bash
python3 -c "
import HiwonderSDK.FourInfrared as FI, time
l = FI.FourInfrared()
for _ in range(10):
    print(l.readData()); time.sleep(0.3)
"
```

Four booleans. Slide something dark under each sensor in turn and watch the corresponding value
flip. This also tells you the sensor ordering, which line-following depends on.

---

## Then: camera and demos

Covered in **[`06-running-the-demos.md`](06-running-the-demos.md)** — read it first. There is a
trap there: the `Functions/` demos all call `cv2.imshow()` and need a display, while
`TurboPi.py` is fully headless. That doc explains how to get a display without an HDMI cable. Before starting the full stack, re-run the power check **with the robot
assembled and running on battery** — this is the configuration where the USB current budget
actually bites, and the bench result doesn't predict it:

```bash
./check_power.sh
```

Compare `usb_max_current_enable` and the PMIC rail voltage against what you measured on the
charger in Phase 1. If the camera drops out intermittently once the motors are running, this is
the first place to look.
