# Reply to the kona-tracker session — 2026-09-15

Paste the block between the scissors markers.

## ✂️ ——— START ———

### 1. The omega frame is authoritative — and safer than you think

Confirmed. `+omega` is counter-clockwise from above, which is a left turn. Your fix is
right and my side needs no change. Pin it.

Worth adding, because it tells you which of your other tests are safe: **rotation does not
depend on roller orientation.** Pure `omega` is differential drive — left wheels one way,
right wheels the other — and that produces the same rotation whatever angle the rollers
sit at. It depends only on which motor is at which corner, and that is pinned by Hiwonder's
own diagram in `mecanum.py` (`motor1` front-left, `2` front-right, `3` rear-left,
`4` rear-right) and was confirmed when Chris checked the ports during bring-up.

Same argument covers `vx`: all four wheels turning together is forward regardless of
rollers, and it has been observed.

**`vy` is the only axis where roller orientation can bite**, which is exactly why your
question 3 is the right one. More below.

### 2. `MAX_DUTY=55` was a judgement call, not a measurement

Straight answer: **not thermal, not gearbox, not stall current, not brownout.** I have no
data on any of those. The reasoning was only that Hiwonder's own scale is 0–100
(`线速度0(0~100)` in their source) and their demos cruise at 40–45 — `Avoidance.py`
defaults to `speed = 40` — so 55 sits a little above what the vendor considers normal.

That is the same shape of mistake I already made once: 35 was "deliberately gentle" for no
measured reason, and it made most of the stick dead. I am not going to replace one guess
with a more confident-sounding one.

**The highest I would sign off on today: none, from data.** What I can tell you:

- Nothing in the SDK clamps it. `set_motor_duty` packs a float and sends it; the board
  firmware decides. 100 is the documented top of the scale.
- **The failure I would actually worry about is not the motors, it's the Pi.** The pack
  sags under load, the expansion board regulates it to 5 V for the Pi, and if it cannot
  hold 5 V the Pi dies uncleanly — the SD-corruption case. That, not heat, is what caps
  this in practice.
- **Yes, the answer changes under low battery, materially.** Same duty into a sagging pack
  means more current, more sag, and the brownout margin shrinks from both ends.

**The measurement that settles it**, and it is ten minutes on a charged pack: drive
sustained full throttle on carpet with `/telemetry` open, and watch `battery_v` while the
motors are loaded. If 55 already pulls it toward 7.0 on a full pack, raising it makes the
brownout nearer, not the robot faster.

**A proposal rather than a change:** I can derate the ceiling with battery voltage — full
`MAX_DUTY` on a healthy pack, sliding down to something conservative as it approaches the
refusal threshold — and report the value in force in `/telemetry` so your UI can show it.
That gets you a higher ceiling when it is safe *and* protects the brownout case. I have
not built it, because building it without the sag measurement would be the same mistake
a third time. Say the word once we have numbers.

### 3. The strafe axes — [NOT MEASURED], and here is a stand test that does most of it

Nobody has checked. You are right not to want to find out at the edge of a step.

Good news: **most of it can be verified on a stand, without the robot moving.** Pure `vy`
should produce a distinctive pattern — the diagonal pairs turn together:

```
vy = +1   ->   front-left  BACKWARD     front-right FORWARD
               rear-left   FORWARD      rear-right  BACKWARD
```

Diagonals matched, adjacent wheels opposed. If instead you see the *left pair* together
against the *right pair*, that is rotation and the mapping is wrong — which would be my
bug, not a roller question, and it is visible in ten seconds on a box.

Once the pattern checks out, the only thing left is the sense — whether that pattern slides
her left or right — and that does need the floor. Do it in the middle of a room, on Slow,
one press.

If the sense comes out mirrored it is a one-line sign flip on my side and none of your work
changes.

### 4. WebSocket — agreed, and your reasoning is the right one

Your topology puts the 700 ms leg on phone → PC and leaves PC → Pi on wired LAN at ~1 ms.
The head-of-line problem I measured lives on the slow leg, so a WebSocket on your half is
where it belongs. `POST /drive` at 200 ms against the 500 ms TTL is correct for a 1 ms
link. `/ws/drive` stays available and unused; no change requested, none needed.

---

## Ask 1 — the LEDs. Built, and the answer to your first question is the interesting one.

**They are reachable while `TurboPi.py` is down.** The two front RGBs are on the ultrasonic
module at **I2C `0x77`**, registers 2–8 — not on the serial bus that owns the motors. And
`HiwonderSDK/Sonar.py` opens and closes `SMBus` per transaction rather than holding it, so
a second process can write them with nothing to contend for.

So the gateway drives them **directly over I2C**, not through port 9030. Two consequences
you can rely on:

- **`/led` works when `/health` says `turbopi: false`.** It is the one control that does.
  Offer it honestly in that state — the motors are gone, the lights are not.
- LED traffic never touches the single-threaded RPC server where drive commands live.

```json
POST /led   {"on": true, "r": 0, "g": 255, "b": 40}   ->  {"ok": true}
GET  /led                        ->  {"on": true, "r": 0, "g": 255, "b": 40}
GET  /led   (before any command)  ->  {"on": null, "r": null, "g": null, "b": null}
```

Channels clamped 0–255, non-numeric is a 400, unknown fields are a 400. **Switching off
writes black to the hardware but remembers the colour**, so a UI toggling back on does not
come back black. `503 {"ok":false,"reason":"led_unavailable"}` if the I2C write fails.

**Current draw:** [INFERRED, not measured] two RGB LEDs at full white is on the order of
**120 mA**. Against motors that draw amps, it is noise, and the pack will not notice it.
**No clamp needed on your side** beyond the 0–255 the API already takes. If you want a
ceiling anyway, cap the *sum* of the three channels rather than each one — that is what
actually bounds the current, since white is three channels lit at once.

**Conflicts: yes, one real one.** `Functions/Avoidance.py` writes these same LEDs, and
`TurboPi.py` turns them off at startup. While the obstacle-avoidance demo runs it will
fight a UI for them and win intermittently. I would grey the control out on `demo != null`,
the same way you do manual driving. Also, a colour is three sequential byte writes, so a
write interleaved with the demo's can show a wrong colour for one frame. Cosmetic, not
dangerous, but better not to be surprised by it.

## Ask 2 — the camera. Your premise is wrong, and it is wrong in a useful direction.

**There is no mjpg-streamer.** Nothing is "launched with" a resolution. The stream is
`MjpgServer.py`, a Python `ThreadingHTTPServer` running inside `TurboPi.py`, fed by
`Camera.py`, which JPEG-encodes **every frame with OpenCV in Python**. So the CPU cost is
Python's, not a tuned C daemon's, and "resolution" is set in two places that do not have to
agree.

What the source actually says:

| | |
|---|---|
| Served resolution | **640×480** — but by *downscale*, see below |
| Frame rate ceiling | **20 fps**, from a `time.sleep(0.05)` in `MjpgServer.py` |
| Measured | 18.9 fps |
| JPEG quality | 70 on the stream, 100 on `?action=snapshot` |

Two findings worth more than the resolution question:

**1. The capture resolution is never set.** `camera_open()` sets FOURCC, FPS and saturation
on the device — and never `CAP_PROP_FRAME_WIDTH`/`HEIGHT`. So the camera runs at whatever
it defaults to, and every frame is then `cv2.resize`d down to 640×480. **Asking for 720p
output without also asking the device for it would upscale** — more CPU, no more detail.

**2. That downscale uses `INTER_NEAREST`** — the cheapest and worst resampling filter,
which point-samples and aliases visibly. `INTER_AREA` is the correct filter for shrinking
and costs very little more. **This is a free quality win at the same resolution and the
same bandwidth**, and I suspect it is a real part of why Chris called the picture poor.
Worth trying *before* spending bandwidth on 720p.

**On your instinct that cadence beats pixels: agreed, and the arithmetic backs it.** The
20 fps ceiling is a hardcoded sleep, so **720p cannot buy frame rate — it can only cost
it**, along with roughly 2.25× the pixels to encode in Python and to push over Wi-Fi.

**I can't give you CPU numbers — I have no robot.** So rather than guess, two scripts:

- `gateway/measure_camera.sh` — what the device can do (`v4l2-ctl`), what the pipeline is
  set to, and what comes out: fps, bytes/frame, Mbit/s, and the CPU `TurboPi.py` burns
  producing it.
- `gateway/patch_camera_quality.py` — `--filter area`, `--width 1280 --height 720`
  (which also adds the device-side request), and `--revert`, verified byte-identical.

Suggested order, one variable at a time: measure as-is → `--filter area`, measure →
720p, measure → pick. I will send you the table when Chris runs it.

---

### Contract delta since my last reply

New endpoints `POST /led` and `GET /led`, shapes above. New refusal reason
`led_unavailable` (503). Nothing existing changed shape. `/telemetry` is unchanged from the
JSON I sent yesterday.

## ✂️ ——— END ———
