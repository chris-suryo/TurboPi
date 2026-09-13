# The concepts, explained against your actual robot

Written to be read alongside code you have just run, not as an upfront lecture. Every example
is real code from `github.com/Hiwonder/turbopi`.

---

## 1. GPIO, PWM, and why TurboPi barely uses either

**GPIO** — General Purpose Input/Output — is just a pin the CPU can set high (3.3V) or low
(0V), or read. That's the whole idea. One bit, in or out.

**PWM** — Pulse Width Modulation — solves a problem: a digital pin only does on or off, but you
want a motor at 40% speed. So you switch the pin on and off very fast and vary the *fraction of
time it spends on*. That fraction is the **duty cycle**. 40% duty ≈ 40% power. The motor's
inertia and inductance smooth the pulses into something that behaves like a lower voltage.

The catch: **the timing has to be precise.** Linux is not a real-time OS. If your PWM loop gets
descheduled mid-pulse, your motor stutters and your servo twitches.

### What TurboPi actually does

It sidesteps the problem. From `HiwonderSDK/mecanum.py`:

```python
board.set_motor_duty([[1, -v1], [2, v2], [3, -v3], [4, v4]])
```

That builds a packet and writes it to a **serial port** — `/dev/ttyAMA0` at 1,000,000 baud.
There is a **microcontroller on the expansion board** on the other end, and *it* generates the
actual PWM. An MCU has no operating system competing for its attention, so its timing is exact.

Two consequences worth internalising:

1. **This is why the Pi 5 works.** The Pi 5's GPIO hardware changed completely (the new RP1
   chip), which broke a lot of Pi robotics code. TurboPi was never using that hardware for the
   drivetrain, so there was nothing to break. See `02-turbopi-pi5-compatibility.md`.
2. **This is a common robotics pattern.** Linux SBC for the thinking (vision, planning, Python),
   microcontroller for the microsecond-accurate twitching. You'll see this split everywhere.

The Pi's GPIO *is* used on this robot — for exactly two things, the board LED and the buttons,
via `gpiod` in `HiwonderSDK/led.py` and `key.py`. That's the whole GPIO footprint.

---

## 2. Servos: position, not speed

A regular DC motor takes power and spins. A **hobby servo** takes a *position command* and
holds that angle against resistance. Inside is a motor, gears, a potentiometer reading the
output shaft angle, and a small controller that drives the motor until measured angle matches
commanded angle. It's a closed loop in a plastic box.

The command is a pulse, repeated ~50 times a second, and **the pulse's width encodes the
angle**:

| Pulse width | Position |
|---|---|
| ~500 µs | one extreme |
| **1500 µs** | **centre** |
| ~2500 µs | other extreme |

You can see this directly in TurboPi. From `Functions/ColorDetect.py`:

```python
board.pwm_servo_set_position(1, [[1, servo1], [2, servo2]])
```

The signature is `pwm_servo_set_position(duration_seconds, [[servo_id, pulse_microseconds], ...])`.
Servo 1 is the camera's tilt axis, servo 2 is pan. Elsewhere in that same file you'll see
`servo1 - 100` and `servo2 + 150` — nudging the pulse width to move the camera toward whatever
it's tracking. That's the entire pan-tilt tracking mechanism.

`servo_config.yaml` stores the *centre* pulse for each servo, because no two servos are
mechanically identical — "centre" for your particular hardware might be 1480µs, not 1500µs.
That file is the calibration.

**Two rules that will save you hardware:**

- **Never force a powered servo by hand.** It fights you — it's actively driving to hold
  position — and you'll strip the plastic gears. Hiwonder's own docs lead with this warning.
- **Don't command past the mechanical limits.** The servo will happily stall trying to reach an
  angle the bracket physically prevents, drawing maximum current until something gives.

---

## 3. Mecanum wheels: the actually interesting part

A normal wheel can do one thing: roll forward or backward. To go sideways, a normal car has to
turn, drive, and turn back. It cannot move sideways. This is a *non-holonomic* constraint.

A **mecanum wheel** has free-spinning rollers around its rim, each mounted at **45°** to the
wheel's axis. When the wheel turns, friction with the ground produces a force at 45° rather
than straight ahead. Each wheel pushes the robot *diagonally*.

Mount four of them with the roller angles mirrored correctly, and the four diagonal force
vectors combine. Drive all four forward: the sideways components cancel, the forward components
add — you go straight. Drive the diagonal pairs in opposite directions: the *forward*
components cancel and the sideways ones add — **you slide sideways without rotating.**

That gives you all three degrees of freedom in the plane, independently and simultaneously:
forward/back (`vx`), left/right (`vy`), and rotation (`ω`). This is called *holonomic* motion.

### The real code

From `HiwonderSDK/mecanum.py`, `set_velocity()` — this is the whole idea in six lines:

```python
vx = velocity * math.cos(direction * rad_per_deg)
vy = velocity * math.sin(direction * rad_per_deg)
vp = -angular_rate * (self.a + self.b)

v1 = int(vy + vx - vp)
v2 = int(vy - vx + vp)
v3 = int(vy - vx - vp)
v4 = int(vy + vx + vp)
```

Read it carefully — the sign pattern *is* the physics:

- **`vx` (forward)** — signs are `+ - - +`. Wheels 1 and 4 versus 2 and 3.
- **`vy` (sideways)** — `+` on all four. They agree.
- **`vp` (rotation)** — `- + - +`. Left side against right side, which is exactly how you spin
  a differential-drive robot.

Three independent inputs, summed per wheel, producing four motor speeds. That's **inverse
kinematics**: desired body motion → required joint (wheel) velocities. It is the same idea as a
robot arm computing joint angles for a target hand position, just unusually legible here.

`self.a` (67mm) and `self.b` (59mm) are half the wheelbase and half the track width. They
appear only in the rotation term, because how much wheel speed a given spin rate demands
depends on how far each wheel sits from the centre of rotation.

Note `set_velocity` takes **polar** coordinates (a speed and a heading in degrees). If you'd
rather think in Cartesian, `translation(velocity_x, velocity_y)` converts for you.

**The tradeoffs** (mecanum wheels are not free): those rollers only contact the ground at a
point, so you lose traction and efficiency; they're poor on carpet, gravel, and thresholds;
and because there's no encoder feedback in this configuration, small differences between the
four motors accumulate as drift. Your robot will not drive perfectly straight. That's normal,
and it's the motivation for closed-loop control later.

---

## 4. Three buses, three jobs

Worth understanding why one robot uses three different communication mechanisms.

| Bus | Used for | Why this one |
|---|---|---|
| **UART** (serial) | Motors, servos — `/dev/ttyAMA0` @ 1,000,000 baud | Point-to-point, fast, dead simple. One device, continuous command stream |
| **I2C** | Ultrasonic sensor (address `0x77`) | Two wires shared by *many* devices, each with an address. Ideal for occasional small reads |
| **GPIO** | Board LED, buttons | A single bit. No protocol needed |

**UART** is two wires (transmit, receive) between exactly two devices. Both sides must agree on
the baud rate in advance — hence `1000000` appearing in the code. Nobody is addressed, because
there's only one possible recipient.

**I2C** is two wires (clock, data) shared by every device on the bus. Each has an address, so
one master can talk to many peripherals. `0x77` is the ultrasonic sensor. Slower than UART, but
you can hang a dozen sensors off the same two pins.

The ultrasonic sensor is worth a note: it's an *I2C* ultrasonic, not the classic HC-SR04. A raw
HC-SR04 makes the Pi send a trigger pulse and then time the echo in microseconds — again, bad
news on a non-real-time OS. This one does the timing internally and hands you a finished
distance over I2C. Same design philosophy as the motor board: push precise timing to hardware
that can actually do it.

---

## 5. How the vision demos are structured

All of `Functions/*.py` follow one shape, and once you see it the demos stop being magic:

1. **Get a frame** — `Camera.py` runs a background thread doing `cv2.VideoCapture(-1)` and
   keeps the most recent frame available.
2. **Find something** — colour threshold in LAB space (`lab_config.yaml` holds the ranges), or
   MediaPipe for faces and hands.
3. **Compute an error** — how far is the target from the centre of the image?
4. **Correct** — feed that error to a PID controller (`HiwonderSDK/PID.py`), which outputs a
   correction. Steer the chassis, or nudge the pan-tilt servos.
5. **Repeat**, ~30 times a second.

That loop — *measure, compare to desired, correct proportionally* — is the foundation of
essentially all control theory. Line following, colour tracking, and face tracking in this
codebase are the same loop with different step-2 implementations.

Colour detection uses **LAB** colour space rather than RGB for a specific reason: LAB separates
lightness (`L`) from colour (`a`, `b`), so a red ball stays "red" whether it's in shadow or
sunlight. In RGB, changing the lighting changes all three channels at once and your threshold
falls apart. This is why `lab_config.yaml` exists and why re-calibrating it for your room's
lighting is usually the fix when colour tracking misbehaves.


---

## 6. A debugging lesson from this build: silent failures

While bringing the robot up, `get_battery()` returned nothing and the natural reading was
"the board is dead." It wasn't. The SDK requires `board.enable_reception()` after `Board()`,
and without it `get_battery()` returns `None` through a branch whose explanatory `print` is
commented out.

Two things worth taking from that, because they generalise far beyond this robot:

**A `None` that means "you used me wrong" and a `None` that means "the hardware is gone" are
indistinguishable to the caller.** That's an API design failure, not a user failure. When you
write code that talks to hardware, make the two cases *different* — raise on misuse, return a
sentinel on absence, or at minimum leave the diagnostic message uncommented.

**When a test fails, check whether your test is wrong before you check whether the world is
wrong.** The temptation was to start unplugging the robot: reseating the header, checking
switches, suspecting the battery. All of that would have been wasted, because the defect was
three lines of Python. Reading the SDK was faster than re-seating a connector, and it was
right.

The fix in `bringup.py` also added a **raw serial probe** for the next time something like this
happens: if no packets arrive, it listens for raw bytes on the UART. Total silence means the
board is unpowered or disconnected; bytes without the `0xAA 0x55` frame header mean it's alive
but being misunderstood. Those are genuinely different problems, and worth distinguishing
before you pick up a screwdriver.


---

## 7. Reading a UART with no instruments

A real diagnostic from this build, worth keeping because it needed no oscilloscope.

The robot's controller board was silent. `pinctrl get 14,15` returned:

```
14: a4    pn | hi // GPIO14 = TXD0
15: a4    pu | lo // GPIO15 = RXD0
```

Three facts in six columns:

**`a4`** — both pins are in *alternate function 4*, their UART mode. A GPIO pin can be a plain
input, a plain output, or handed to a hardware peripheral. These are handed to the UART, so the
Pi's configuration is correct. That alone rules out half the possible causes.

**`hi` on TXD** — an idle UART line sits **high**. This is the "mark" state, a convention
inherited from telegraphy: an idle line is held energised so that a *broken* line (which goes
low) is distinguishable from an idle one. The Pi's transmit line is idle and healthy.

**`lo` on RXD** — and this is the tell. Receive should also idle high. The `pu` says the Pi has
its internal **pull-up** enabled, which weakly ties the line high. It reads low anyway, so
**something external is actively sinking it**.

An unpowered chip is not an open circuit. Its input protection diodes conduct once the line
rises above its (absent) supply rail, clamping it near ground. So an unpowered peripheral
wired to a powered host drags the shared line low — which is exactly this signature.

So without touching the robot: the Pi is configured correctly, its transmitter is fine, and
something on the other end is either unpowered or held in reset.

**The useful part is that this becomes a one-command test.** Once the board has power,
`pinctrl get 15` should read `hi`. No script, no imports, no test harness — one line that
answers "is the other end alive?" before any software is involved.

That's a general habit worth having: when a protocol won't talk, drop a layer. Before debugging
packets, ask whether the wire is in the right state.
