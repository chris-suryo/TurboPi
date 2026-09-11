# Assembly and staged bring-up

The principle: **bring up one subsystem at a time, smallest first.** Each of these can fail on
its own, for its own reasons. Assembling everything and then running the full demo means a
failure could be any of eight things, and you'll spend the evening bisecting hardware.

---

## Before assembly: two fit checks

### 1. The 52Pi aluminium case almost certainly has to come off

This is worth knowing before you start screwing things together. The TurboPi expects:

- The expansion board seated on the Pi's **40-pin header** (it's a HAT)
- The Pi **bolted to the chassis** at its mounting holes

A full aluminium enclosure generally conflicts with both. Likely outcome: **use the 52Pi case
for the Phase 1 bench testing** (where its cooling is genuinely useful during the stress test),
then remove it for the robot build and rely on the kit's mounting plus whatever active cooler
the expansion board provides.

Verify by offering the parts up *before* committing. Don't force anything.

### 2. Fit the RTC battery now

It connects to a small dedicated 2-pin connector on the Pi 5 (not the GPIO header). Fitting it
while the Pi is bare is far easier than after it's buried in a chassis. It keeps the clock
running across power cycles — genuinely useful on a robot that gets switched off by a physical
switch and has no internet on first boot.

### 3. Identify your expansion board revision

Look at the board and note any silkscreen markings. This matters: the current TurboPi source
talks to a **serial** controller (`ros_robot_controller_sdk.py`, `/dev/ttyAMA0`), but older
TurboPi revisions used a different, I2C-era board. If yours is the older one, the SDK revision
you need differs. Photograph the board and we'll confirm which you have rather than assuming.

---

## Assembly

Follow Hiwonder's own printed/online assembly guide for the mechanical build — it has the
exploded diagrams and correct screw lengths, and there's no value in paraphrasing it here.

**Wiring, from the official docs — get these right:**

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
