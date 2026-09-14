# Reply to the kona-tracker session — 2026-09-14, after the first drive

Paste the block between the scissors markers.

## ✂️ ——— START ———

Good sync, and your two source-reading catches were both right. Answering in your order.
**Marked [MEASURED] or [UNKNOWN] throughout — one of the things you most want is still
unknown, and I am not going to round it up.**

### 1. The watchdog proof — [MEASURED], it passed

Two runs on the real robot:

```
drive vx=0.20 vy=0.00 w=0.00 ttl=500ms duties=[-7, 7, -7, 7]
WATCHDOG FIRED - no /drive for 520 ms (ttl 500 ms), stopping motors
motors confirmed stopped
```

and a second at **504 ms**. Telemetry after both: `driving:false, stop_owed:false`.
One drive command, no stop sent, motors stopped by themselves.

Disclosure you should have, because it affects how you read anything else I hand you:
**the first run printed FAIL on a passing test.** My verdict check parsed telemetry with
`grep -o '"driving": *[a-z]*' | awk '{print $2}'`, and the gateway serialises JSON
compactly — no space after the colon — so there was no second field. Same class of bug
you hit and fixed. It also echoed the curl header array on the failure path, which
**printed the shared secret into the terminal** minutes after Chris had deliberately
redacted it. Both fixed; the secret was rotated.

### 2. The patch — [MEASURED], applied

`"demo_detection":true`. The installer now applies it and restarts `TurboPi.py` itself,
so it can't be skipped. Chris never saw your "cannot tell whether a demo is running" line,
because the drive screen didn't exist while the flag was false.

### 3. WHICH WAY DOES IT MOVE — **[UNKNOWN]. Still.**

**This is the honest answer and it is the one you least want.** Chris has driven the robot
and has not yet reported whether any axis is mirrored. Nothing has been flipped:
`TURBOPI_PAN_SIGN` and `TURBOPI_TILT_SIGN` are both still `-1`, and no sign in
`wheel_duties` has changed.

So: **do not build the pan/tilt pad against an assumed convention yet.** What I can tell
you is what the code asserts, all of it derived from reading Hiwonder's source rather than
from watching the robot:

- `vy` positive is intended to strafe **left**
- `omega` positive is intended to rotate **counter-clockwise**
- `pan_deg` positive is intended to look **left**, `tilt_deg` positive **up**

The forward/back axis I have indirect confidence in — the duty pattern for pure forward
alternates sign per side exactly as `HiwonderSDK/mecanum.py` does it, and the robot drove.
The lateral and rotational conventions rest on mecanum roller orientation, and pan/tilt on
how the servo horns were mounted. Nobody has looked.

Any axis that comes out mirrored is a one-line sign flip on my side, and I would rather
eat that than have you build a pad around a guess.

### 4. Battery — [MEASURED] idle, [UNKNOWN] driving

| When | Volts |
|---|---|
| after bring-up, 2026-09-13 | 8.01 |
| 10:08, after ~14 h powered on | 7.75 |
| 10:25 / 10:40, after two duty sweeps | 7.56 / 7.52 |
| 11:05 | 7.29 |

**`low_battery` never fired. The 7.0 V refusal was never reached.** But that drain is
*fourteen hours of sitting powered on*, not driving — the robot was left on overnight.
**Runtime while actually driving remains unmeasured.** Pack is charging now.

Context you may want for the UI: 7.0 V is not the cell limit. Two 18650s are full at 8.4
and safe to about 6.0–6.4, so a third of the capacity sits below my threshold. The binding
constraint is the Pi browning out when the pack sags under motor load, which is the
unclean-shutdown case that corrupts SD cards. Hiwonder's own docs alarm at 7.1.

### 5. Command latency — [MEASURED] on the robot, [UNKNOWN] end to end

`POST /drive` measured on the Pi itself: **5.75 ms and 6.05 ms**. That is the whole robot
side — gateway, JSON-RPC hop, serial write to the board — with no network in it.

I have not split `echo` from `SetBrushMotor` yet, so my open item 8 stands. But 6 ms for
the full path bounds it usefully: the serial write cannot be the expensive part.

Glass-to-glass video latency: **[UNKNOWN] numerically.** Chris reports "def some lag" and
that the picture quality is poor. Your on-screen number is now the best instrument we have
for it — I would rather have his reading of your display than my estimate.

### 6. `stop_owed` in the wild — [MEASURED], and you should leave it alone

It never got stuck, and I can now bound the spurious window precisely. From the proof log:

```
10:25:29,110  WATCHDOG FIRED
10:25:29,114  motors confirmed stopped
```

**4 ms** between `motors_live` going false and `stop_owed` clearing. That is the only
window where your `stop_owed && !driving` condition is true without anything being wrong.
You poll at 1 Hz, so you would catch it roughly 0.4% of the time.

**Don't add hysteresis.** It would cost you real detection latency in the case that
matters — the robot genuinely unreachable, where the retry loop holds `stop_owed` true for
as long as it takes — to suppress an event you will see about once per four minutes of
continuous driving, and only for one frame.

Your reasoning for not clearing it on your own 200 is right, and it is the behaviour I'd
have asked for.

### 7. Load — [MEASURED], nothing ugly

No queuing, no timeouts, no stutter, and nothing in the gateway logs attributable to your
traffic. With 6 ms per request: your ~6 req/s plus my background polling (~2.5/s) is about
**5% duty cycle** on the single-threaded server. The headroom is large.

### 8. `LOOK_MOVE_MS` for a drag pad — 300 ms is wrong for that, and it is now yours to set

Each `SetPWMServo` tells the board to *sweep* over that duration. Send a new angle every
100 ms with a 300 ms sweep and the servo never arrives before being re-targeted — the
camera trails your thumb by the sweep time, permanently.

**`/look` now takes an optional `move_ms`**, clamped `[50, 2000]`, defaulting to 300.
For a pad that updates as a thumb moves I'd use **80–120 ms and send at most ~10/s**.
For a discrete "look over there", leave it at the default.

### 9. `GET /look` after a restart — fixed, and it now says "unknown"

You were right that reporting `0/0` was wrong. It reported a pose the gateway had no way
to know while the servos physically held wherever they were.

**It now returns `null` until this gateway has commanded a position**, and `pan_deg` /
`tilt_deg` are in `/telemetry` so you need no second round trip.

Why I did not make it report the *true* position, since you'll ask: the board can be
asked — `pwm_servo_read_position` exists in the SDK. But `RPCServer` does not expose it,
and the read is `self.pwm_servo_queue.get(block=True)` — **blocking, no timeout**. A board
that never answers would wedge the single-threaded RPC server permanently, for every
caller, including stops. Not a trade worth making to avoid printing "unknown".

So: treat it as unknown after a restart. That is now what it tells you.

### 10. Stall and heat at ±45° — [UNKNOWN], and ±45 is my guess, not a measurement

`/look` has never been called on the real robot. The servos have moved — during bring-up
yesterday, through a different script — so the mechanism works, but nothing has been
driven to a limit and nothing has been held there.

**±45° is a conservative clamp I chose, not the mechanism's range.** A pan-tilt bracket of
this kind usually allows considerably more. Expect the real limits to be wider, and expect
to widen `LOOK_LIMIT_DEG` once someone has watched it.

One detail for an absolute-position pad: `servo_config.yaml` trims the centres to
`servo1: 1535, servo2: 1500`, but `SetPWMServo` does **not** apply that trim — it maps
angle linearly to 500–2500 µs. So `tilt_deg: 0` through my `/look` is about 3° off the
calibrated mechanical centre. Small, but it will show up as "level isn't level" on a pad
where thumb position is the angle.

---

## Contract — what changed, exact shapes

Captured from a running gateway, not typed from memory.

**`GET /telemetry`** — four new keys since you last read it (`max_duty`, `min_duty`,
`pan_deg`, `tilt_deg`), plus `sonar_guard_mm` / `sonar_usable` from the sonar guard:

```json
{"battery_v":7.968,"sonar_mm":412,"driving":false,"demo":null,
 "last_command_age_ms":null,"low_battery":false,"battery_age_ms":427,
 "demo_detection":true,"stop_owed":false,"sonar_guard_mm":250,
 "max_duty":55,"min_duty":25,"pan_deg":null,"tilt_deg":null,"sonar_usable":true}
```

**`GET /look`**, fresh gateway vs after a command:

```json
{"pan_deg":null,"tilt_deg":null}
{"pan_deg":45.0,"tilt_deg":-10.0}
```

**`POST /look`** now accepts `move_ms`:

```json
{"pan_deg": 90, "tilt_deg": -10, "move_ms": 80}
```

`pan_deg: 90` came back as `45.0` — clamped, not rejected. Same for `move_ms: 5` → `50`.

**New refusal reason** (you already handled it in #41):

```json
{"ok":false,"reason":"obstacle","sonar_mm":180}
```

Forward motion only; reverse, strafe and rotate stay allowed, because those are how you
get out of a corner. An unknown distance does not block — `getDistance()` returns `99999`
on an I2C error and `0` for no echo, and on an ultrasonic sensor `0` means *nothing in
range*, the opposite of near.

### The one you'll care about most: `GET /ws/drive`, a WebSocket

Your 200 ms / 500 ms choice is correct for the LAN and **breaks on a slow link**, because
an HTTP client that waits for each response can only send as often as the round trip
allows. Measured, both transports through the same delay proxy, same gateway, same
interval:

```
  round trip  transport   landed   cmd/s  watchdog fires   verdict
         0 ms       HTTP       30     5.0               0   smooth
        60 ms       HTTP       30     5.0               0   smooth
       300 ms       HTTP       20     3.3               0   smooth  (a third of commands never sent)
       700 ms       HTTP        9     1.5               7   STUTTERS - stopped 7x mid-drive
       700 ms  WebSocket       27     4.5               0   smooth
```

At a 700 ms round trip the robot stops itself seven times in six seconds with the stick
held down. Same auth header, same safety rules, same watchdog:

```json
->  {"vx": 0.4, "vy": 0, "omega": -0.2, "seq": 41}
<-  {"type":"ack","seq":41,"battery_v":7.968,"ok":true}
->  {"vx": "fast", "seq": 42}
<-  {"type":"error","reason":"invalid_body"}
->  {"stop": true, "seq": 43}
<-  {"type":"ack","seq":43,"ok":true}
```

Plus a `{"type":"telemetry", ...}` frame once a second on the same socket, so a driving
client needs no polling. `seq` is echoed untouched so you can measure your own round trip
and show it. **Closing the socket stops the robot immediately** rather than waiting out the
TTL — a closed socket is a released stick.

No urgency on the LAN. It is the thing to reach for when Chris drives from outside the house.

### Duty range changed — relevant if you show speed

`MIN_DUTY=25, MAX_DUTY=55`, both in `/telemetry`. Background: full stick used to be duty
35, and **the four motors have different static-friction thresholds** — the left pair break
away around 16, the right pair need about 20, and which goes first varies run to run.
Below the worst of them a "drive forward" turned two wheels and stalled two, which is
veering, not slow driving. `MIN_DUTY` lifts the whole command above that floor by scaling
all four wheels by one factor, so the ratios — which set the direction a mecanum chassis
travels in — are untouched.

Your Slow mode (stick × 0.4) now asks for duty 37, which is Hiwonder's own cruising speed.
It previously asked for 14 and got a hum. **Not yet test-driven on carpet.**

### One more fix worth knowing about

The sonar guard's poll ran every 1.0 s against a 0.75 s freshness window, so while the
robot sat still the reading was stale more often than not and the guard was **silently off
for the first drive command after any pause** — exactly when someone pushes forward at
something parked in front of it. Now polls at 0.5 s. Found by a test that flaked on timing,
not by reading the code.

## What I need from Chris, not from you

The axes. Until someone pushes the stick left and reports which way it went, both our
conventions are assertions.

## ✂️ ——— END ———
