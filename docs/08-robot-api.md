# TurboPi network API reference

Everything a program on another machine needs to drive this robot. Extracted from
`RPCServer.py` and `MjpgServer.py` in the TurboPi source, not from vendor marketing.

**Robot address:** `turbopi.local` / `10.0.0.3` (set a DHCP reservation to keep it stable).

| Port | Protocol | Purpose |
|---|---|---|
| **8080** | HTTP, MJPEG | Live video |
| **9030** | HTTP, JSON-RPC 2.0 | Control and telemetry |
| 22 | SSH | Admin |

Both bind `0.0.0.0` — reachable from any machine on the LAN, **with no authentication**.

> **Both ports only exist while `TurboPi.py` is running.** Nothing autostarts on this build.
> See "Running it" at the end.

---

## Video — port 8080

| Request | Response |
|---|---|
| `GET http://<robot>:8080/` | `multipart/x-mixed-replace; boundary=--boundarydonotcross` — continuous MJPEG |
| `GET http://<robot>:8080/?action=snapshot` | A single JPEG (quality 100) |

Frame size is 640×480, JPEG quality 70 on the stream, and the handler sleeps 50 ms between
frames — so **~20 fps is the ceiling**, regardless of what the camera can do.

In a browser, the stream works directly in an `<img>` tag:

```html
<img src="http://10.0.0.3:8080/" alt="robot camera">
```

**This is the only way to get the camera.** `Camera.py` opens `cv2.VideoCapture(-1)` and holds
it for the life of the process, so while `TurboPi.py` runs, nothing else can open
`/dev/video0` — not another script on the Pi, not anything.

---

## Control — port 9030, JSON-RPC 2.0

`POST` to `http://<robot>:9030/` with `Content-Type: application/json`.

```json
{"jsonrpc": "2.0", "method": "GetBatteryVoltage", "params": [], "id": 1}
```

### Response envelope — read this before writing a client

Every method returns a **3-tuple**, which JSON-RPC serialises as an array:

```json
{"jsonrpc": "2.0", "result": [true, [8.01], "GetBatteryVoltage"], "id": 1}
```

| Index | Meaning |
|---|---|
| `0` | **success boolean** — check this, not the HTTP status |
| `1` | return data on success, or an error string on failure |
| `2` | echo of the method name |

**HTTP 200 does not mean the call worked.** A failure looks like:

```json
{"jsonrpc": "2.0", "result": [false, "E02 - Invalid parameter!", "SetBrushMotor"], "id": 1}
```

| Code | Meaning |
|---|---|
| `E01` | Invalid number of parameters |
| `E02` | Invalid parameter |
| `E03` | Operation failed |
| `E04` | Operation timeout |
| `E05` | Not callable |

### Motion

**`SetBrushMotor(motor, speed, motor, speed, ...)`** — variadic **pairs**. Motor ids `1`–`4`
(1 FL, 2 FR, 3 RL, 4 RR). Speed is a signed duty; sign sets direction. An odd argument count
returns `E01`; a motor id outside 1–4 returns `E02`.

```json
{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,35,2,35,3,35,4,35],"id":1}
```

Stop by sending `0` to all four. **Always send an explicit stop** — the board holds the last
duty indefinitely, so a dropped connection leaves the robot driving.

**`SetMovementAngle(angle)`** — higher-level directional movement.

### Pan-tilt

**`SetPWMServo(duration_ms, <ignored>, servo_id, angle, servo_id, angle, ...)`**

Two quirks, both real and both in the source:

1. **`params[1]` is skipped** — the parser reads servos from index 2 with a stride of 2. Pass
   any placeholder (`1` is conventional).
2. **Angles, not microseconds.** The server maps `+90 → 500 µs` and `−90 → 2500 µs`, so the
   scale is **inverted** and `0` is centre.

Servo `1` = tilt, servo `2` = pan.

```json
{"jsonrpc":"2.0","method":"SetPWMServo","params":[500,1,1,0,2,0],"id":1}
```
*(500 ms move, both servos to centre)*

Keep to roughly ±45° until you know the mechanical limits. A stalled servo draws maximum
current and strips its own gears.

### Telemetry

| Method | Returns |
|---|---|
| `GetBatteryVoltage()` | Volts. Below **7.1 V** means recharge |
| `GetSonarDistance()` | Millimetres |
| `GetRunningFunc()` | Currently loaded demo id |
| `GetSonarDistanceThreshold()` | Avoidance trigger distance |

### Ultrasonic RGB LEDs

`SetSonarRGBMode(mode)` · `SetSonarRGB(index, r, g, b)` · `SetSonarRGBBreathCycle(index, color, cycle)` · `SetSonarRGBStartSymphony()`

Index is `1` or `2`. Set mode `0` for direct colour control before using `SetSonarRGB`.

### Built-in demos

Load one, start it, and it runs autonomously — driving the motors and consuming the camera
itself.

```
LoadFunc(id) → StartFunc() → [runs] → StopFunc() → UnloadFunc()
```

| id | Demo |
|---|---|
| 0 | none |
| 1 | Remote control (manual motion) |
| 2 | Colour detect |
| 3 | Colour tracking |
| 4 | Visual patrol (line following) |
| 5 | QR code recognition |
| 6 | Obstacle avoidance |
| 9 | LAB colour calibration |

Per-demo configuration: `ColorTracking(color)`, `VisualPatrol(color)`, `ColorDetect(color)`,
`ColorTrackingWheel(state)`, `SetAvoidanceSpeed(speed)`, `SetSonarDistanceThreshold(mm)`.

Colour calibration: `GetLABValue()`, `SetLABValue(...)`, `SaveLABValue(color)`, `HaveLABAdjust()`.

**`Heartbeat()`** — the running demo expects periodic heartbeats and may stop without them.
Call it every second or so while a demo is active.

> **A loaded demo owns the robot.** It drives the motors on its own. Don't send
> `SetBrushMotor` at the same time — `StopFunc()` first.

### Test methods

`echo(s)` returns `s`; `add(a, b)` returns the sum. Handy for checking connectivity without
touching hardware.

---

## Constraints that will bite a web frontend

**1. No CORS headers.** `RPCServer.py` returns `Response(response.json, mimetype='application/json')`
with no `Access-Control-Allow-Origin`. A browser page served from any other origin **cannot**
call port 9030 directly — the request is blocked before it reaches the robot.

Options:
- **Proxy through your own backend** (recommended — your server calls the robot, your page calls your server). Also gives you a place to add auth.
- Add CORS headers to `RPCServer.py` on the robot.

The MJPEG stream is exempt when used as an `<img src>`, because image loads aren't subject to
CORS. But `fetch()`ing it is.

**2. No authentication, no TLS.** Anything on the LAN can drive this robot. Do not port-forward
either port.

**3. No state synchronisation.** The API is fire-and-forget. There's no "what speed am I doing"
query — track commanded state client-side.

**4. Single consumer for the camera.** See above.

---

## Running it

```bash
cd /home/pi/TurboPi
~/turbopi-venv/bin/python TurboPi.py
```

Must be the venv interpreter — `jsonrpc` and `werkzeug` are installed there, not system-wide.

Verify from another machine:

```bash
curl -s -X POST http://10.0.0.3:9030/ \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"GetBatteryVoltage","params":[],"id":1}'
```
