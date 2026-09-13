# Handoff: building a custom frontend for a TurboPi robot

**Paste the section below into whatever system is building the frontend.** It is written to
stand alone — it assumes no knowledge of this repository or of the robot.

Everything in it was verified against the running hardware and the TurboPi source, not
inferred from documentation. Full API detail: [`docs/08-robot-api.md`](docs/08-robot-api.md).

---

## ✂️ ——— START OF HANDOFF PROMPT ———

I need a web frontend to drive a physical robot. It will be embedded as a component inside an
existing application, so it must be self-contained and must not assume it owns the page.

### The robot

A Hiwonder TurboPi: a four-wheeled mecanum-drive robot car built on a Raspberry Pi 5, running
Raspberry Pi OS (Debian 13). Mecanum wheels mean it is **holonomic** — it can translate in any
direction and rotate independently, including strafing sideways without turning. It has a
2-DOF pan-tilt USB camera, an ultrasonic distance sensor, and a 4-channel line sensor.

It exposes two HTTP services on the LAN. Both are plain HTTP, unauthenticated, on a fixed
local IP (`10.0.0.3`, also `turbopi.local`). Treat the address as configurable.

Both services only exist while the robot's control program is running. Handle their absence
gracefully — the robot is often powered off.

### Service 1 — video, port 8080

| Request | Response |
|---|---|
| `GET http://<robot>:8080/` | MJPEG stream, `multipart/x-mixed-replace; boundary=--boundarydonotcross` |
| `GET http://<robot>:8080/?action=snapshot` | One JPEG |

640×480, ~20 fps ceiling (the server sleeps 50 ms per frame). Display it with a plain
`<img src="http://10.0.0.3:8080/">` — image loads are exempt from CORS. Do **not** try to
`fetch()` it; that is subject to CORS and will fail.

Show a clear placeholder when the stream is unreachable, and give the user a way to reconnect.
A dead MJPEG connection does not always fire an `error` event, so consider a watchdog that
reloads the `<img>` if no frame has arrived for several seconds.

### Service 2 — control, port 9030, JSON-RPC 2.0

`POST http://<robot>:9030/` with `Content-Type: application/json`.

```json
{"jsonrpc": "2.0", "method": "GetBatteryVoltage", "params": [], "id": 1}
```

**Critical: the response envelope is non-standard.** Every method returns a 3-element array
inside `result`:

```json
{"jsonrpc": "2.0", "result": [true, [8.01], "GetBatteryVoltage"], "id": 1}
```

- `result[0]` — **success boolean. Check this.**
- `result[1]` — data, or an error string
- `result[2]` — method name echo

**HTTP 200 does not mean success.** Failures return 200 with `result[0] === false` and an
error string like `"E02 - Invalid parameter!"` in `result[1]`. Write one wrapper that unpacks
this and throws on `result[0] === false`, and route every call through it.

#### Methods you need

**Drive — `SetBrushMotor`.** Variadic *pairs* of `(motorId, speed)`. Motors: `1` front-left,
`2` front-right, `3` rear-left, `4` rear-right. Speed is a signed duty; sign sets direction.
Reasonable range ±50 for testing.

```json
{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,35,2,35,3,35,4,35],"id":1}
```

An odd number of params returns `E01`; a motor id outside 1–4 returns `E02`.

**Pan-tilt — `SetPWMServo(durationMs, placeholder, servoId, angle, ...)`.** Two real quirks:

1. **`params[1]` is ignored** by the server's argument parser. Pass `1`.
2. **Angles, not microseconds**, and the scale is **inverted**: `+90 → 500 µs`,
   `−90 → 2500 µs`, `0` = centre. Servo `1` is tilt, servo `2` is pan.

```json
{"jsonrpc":"2.0","method":"SetPWMServo","params":[500,1,1,0,2,0],"id":1}
```

Clamp the UI to roughly ±45°. A servo driven into a mechanical stop stalls, draws maximum
current, and strips its own gears.

**Telemetry — poll about 1 Hz:** `GetBatteryVoltage()` → volts (**below 7.1 V is a low-battery
warning the UI must surface**), `GetSonarDistance()` → millimetres, `GetRunningFunc()` →
active demo id.

**Built-in autonomous demos:** `LoadFunc(id)` → `StartFunc()` → `StopFunc()` → `UnloadFunc()`.
Ids: `1` remote control, `2` colour detect, `3` colour tracking, `4` line following,
`5` QR recognition, `6` obstacle avoidance. While one runs it **drives the motors itself** —
the UI must disable manual driving until `StopFunc()`. A running demo also expects
`Heartbeat()` roughly every second or it may stop.

**Connectivity test without touching hardware:** `echo("hi")` returns `"hi"`.

### The CORS problem — decide this first

**The robot sends no CORS headers.** A browser page on any other origin cannot call port 9030
directly; the request is blocked before it reaches the robot.

**Strongly preferred: proxy through the host application's backend.** The browser calls your
server, your server calls the robot. This also gives you somewhere to put authentication,
rate limiting, and a safety watchdog — none of which the robot has.

If the frontend must talk to the robot directly, the robot's `RPCServer.py` needs CORS headers
added, which means modifying and redeploying robot software. Flag this rather than assuming it.

### Safety requirements — these are not optional

This drives a physical object that can fall off a table or hit someone.

1. **Dead-man behaviour.** Motion commands must stop when input stops. A held key or touch
   drives; release stops. Never a fire-once "go forward" with no stop.
2. **Heartbeat watchdog.** If the UI loses focus, the tab is hidden, the socket drops, or the
   user navigates away — **send an explicit stop**. The robot holds its last motor duty
   indefinitely. A dropped connection while driving means a robot that keeps going.
3. **Emergency stop.** Always-visible, always-enabled control that sends all four motors to
   zero. Bind it to Escape as well.
4. **Clamp everything** at the UI layer. Don't rely on the robot to reject bad values; it
   mostly won't.
5. **Surface connection state prominently.** "Connected", "reconnecting", "offline" — the
   operator must never be unsure whether their commands are landing.

### What to build

A single embeddable component:

- **Live camera view** with connection state and a reconnect affordance
- **Holonomic drive control** — this robot strafes, so a simple forward/back/left/right pad
  wastes it. Prefer a virtual joystick or WASD+QE (translate + rotate), with the sideways axis
  genuinely mapped to strafe rather than turning. Compute the four wheel speeds from a desired
  (vx, vy, ω); the inverse kinematics are below.
- **Pan-tilt control** — drag-pad or two sliders, clamped, with a recentre button
- **Telemetry strip** — battery volts with a low warning, sonar distance, connection status
- **Demo launcher** — the six built-ins, with manual driving disabled while one is active
- **Emergency stop** — prominent, always live

### Mecanum inverse kinematics

For a desired body velocity — `vx` forward, `vy` sideways, `ω` rotation — the four wheel
speeds, matching the convention the robot's own firmware uses:

```
v1 = vy + vx - ω·(a+b)     // front-left
v2 = vy - vx + ω·(a+b)     // front-right
v3 = vy - vx - ω·(a+b)     // rear-left
v4 = vy + vx + ω·(a+b)     // rear-right
```

where `a = 67 mm` (half wheelbase) and `b = 59 mm` (half track width). Note the sign pattern:
`vy` agrees across all four, `vx` alternates in pairs, and `ω` splits left side from right.
Scale the results into the motor duty range and clamp.

*(The robot's own `set_velocity` negates motors 1 and 3 when sending, because of how the left
side is wired. If straight-ahead comes out as a spin, that inversion is the first thing to
check.)*

### Tech constraints

- Embeds into an existing application — no global styles, no route hijacking, no assumptions
  about the host's framework beyond what I specify
- The robot address must be configurable, not hard-coded
- Works on desktop and touch; the drive control needs to be usable with a thumb
- Degrade honestly when the robot is offline — never show stale telemetry as if it were live

## ✂️ ——— END OF HANDOFF PROMPT ———

---

## Notes for you, not for the frontend builder

**The proxy decision is the one that matters.** If the host application already has a backend,
routing robot calls through it solves CORS, and gives you a natural place to add auth and a
server-side safety watchdog. If you instead patch CORS into `RPCServer.py`, you own a modified
copy of vendor software, and any robot on the network becomes drivable by any page the
operator visits.

**This is the same finding as the dog-cam note.** The MJPEG stream on :8080 is exactly what
[`NOTES-dog-cam-integration.md`](NOTES-dog-cam-integration.md) flagged — one camera source
among several in your app. The constraint stands: while `TurboPi.py` runs it holds
`/dev/video0` exclusively, so port 8080 is the only way in.

**Nothing autostarts.** `TurboPi.py` must be running for either port to exist. If you want the
robot available on boot, that's a systemd unit — worth adding before you build a UI that
assumes the robot is reachable.
