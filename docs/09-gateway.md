# The safety gateway

A small FastAPI service on the Pi that sits between a network client and the robot's own
JSON-RPC server. The client talks to it and to nothing else on the robot.

It exists for one reason. **Nothing in Hiwonder's stack ever stops the motors.** A duty
value goes to the expansion board's microcontroller and is held indefinitely — there is
no timeout in `RPCServer.py`, none in `TurboPi.py`, none in the SDK. A "drive" that
arrives with its matching "stop" lost means a robot that keeps going until it hits
something. The watchdog in this service is what prevents that, and it runs **on the
robot**, on the near side of every link that can fail.

| | |
|---|---|
| Listens | `0.0.0.0:9031` |
| Forwards to | `http://127.0.0.1:9030/` (the robot's JSON-RPC) |
| Auth | `X-Robot-Token: <secret>` on every endpoint except `/health` |
| Secret | `/etc/turbopi/gateway-token`, mode 0600, owned by `pi` |
| Source | [`gateway/robot_gateway.py`](../gateway/robot_gateway.py) |
| Tests | [`gateway/test_gateway.py`](../gateway/test_gateway.py) — 108 checks, no robot needed |

## Install

Everything the installer needs lives in `gateway/`, so this is the whole procedure:

```bash
scp -r gateway pi@10.0.0.3:~/turbopi-gateway     # PowerShell or Terminal, on your PC
ssh pi@10.0.0.3
cd ~/turbopi-gateway && bash install_gateway.sh  # SSH, on the Pi
```

It installs the service, applies the required `GetRunningFunc` patch, restarts
`TurboPi.py` so the patch takes effect, and prints the shared secret at the end. That
value goes in kona-tracker's `.env`.

## The API

### `GET /health` — no auth

```json
{"ok": true, "turbopi": true, "battery_v": 7.97, "uptime_s": 412}
```

`turbopi` is whether `echo()` on port 9030 answered within the last 5 seconds; a
background probe runs every 2 s. This endpoint reads cached values only and never touches
the motors, so it is safe to poll hard and safe to call when you have no idea what state
the robot is in.

It has no auth deliberately: it is the endpoint you need most when something is wrong, and
it exposes only liveness, battery voltage and uptime.

### `GET /telemetry`

```json
{"battery_v": 7.97, "sonar_mm": 412, "driving": false, "demo": null,
 "last_command_age_ms": 1200, "low_battery": false,
 "battery_age_ms": 340, "demo_detection": true}
```

RPC answers are cached for 500 ms, so two viewers polling at 1 Hz do not double the load
on the robot's single-threaded RPC server.

Three keys beyond the contract, all additive and safe to ignore: `battery_age_ms` (how
stale the voltage is — see "Battery readings are intermittent"), `demo_detection` (whether
the `demo_running` guard is actually active), and `stop_owed` (the gateway believes the
board may still be driving and has not managed to stop it — see "The stop is owed").

`last_command_age_ms` is **`null` before the first `/drive`**, not `0` — zero would mean
"a command just arrived", which is the opposite of the truth.

### `POST /drive`

```json
{"vx": 0.4, "vy": 0.0, "omega": -0.2, "ttl_ms": 500}
```

`vx` forward, `vy` left, `omega` counter-clockwise, each clamped to `[-1, 1]`. `ttl_ms`
clamped to `[100, 2000]`. Anything non-numeric — a string, a boolean, `null`, a list — is
refused with **400** and nothing is forwarded.

| Response | When |
|---|---|
| `200 {"ok": true, "battery_v": 7.97}` | Accepted |
| `400 {"ok": false, "reason": "invalid_body"}` | Non-numeric input |
| `409 {"ok": false, "reason": "low_battery"}` | Below 7.0 V — motors are also zeroed |
| `409 {"ok": false, "reason": "demo_running"}` | A built-in demo is driving |
| `502 {"ok": false, "reason": "E03 - ..."}` | The robot answered and said no |
| `503 {"ok": false, "reason": "turbopi_unreachable"}` | Port 9030 silent — treated as a stop |

**Out-of-range is clamped, not refused.** `{"vx": 5, "omega": -9, "ttl_ms": 99999}` returns
`200` having driven at `vx=1, omega=-1, ttl=2000`. Only *non-numeric* input gets a 400.

Exact bodies, captured from a running gateway rather than written from memory:

```
401  {"ok":false,"reason":"unauthorized"}
400  {"ok":false,"reason":"invalid_body","detail":[{"type":"float_type",
      "loc":["body","vx","float"],"msg":"Input should be a valid number","input":"fast"}, ...]}
409  {"ok":false,"reason":"low_battery"}
409  {"ok":false,"reason":"demo_running"}
503  {"ok":false,"reason":"turbopi_unreachable"}
200  {"ok":true,"battery_v":7.968}
```

`detail` is pydantic's own error list, truncated to four entries. Match on `reason`, not on
`detail`.

The client never sends motor ids. The gateway owns the kinematics and the wiring
inversion.

### `POST /stop`

Always works: not gated by battery, demo state, or anything else. Motors go to zero
synchronously, which is the part that matters, and the call returns in a few
milliseconds.

`StopFunc` is fired afterwards and **only if a demo is actually loaded**. That is not an
optimisation — Hiwonder's `StopFunc` with nothing running raises inside the main-thread
queue, never sets a result, and returns `E04 - Operation timeout!` after a full two
seconds. The robot's RPC server handles one request at a time, so an unnecessary
`StopFunc` would block the *next* stop for two seconds. A safety stop that can be delayed
two seconds by a previous safety stop is not a safety stop.

### `GET /ws/drive` — WebSocket, the low-latency path

Same safety rules, without a request per command. Use this in preference to `POST /drive`
for anything holding a stick down; see [`docs/11-latency.md`](11-latency.md) for the
measurements that justify it. Auth is the same `X-Robot-Token` header, checked **before**
the handshake is accepted, so a bad token fails to connect rather than opening a socket
nobody trusts.

Client → server, as often as it likes:

```json
{"vx": 0.4, "vy": 0, "omega": -0.2, "seq": 41}
{"stop": true}
```

Server → client:

```json
{"type":"ack","seq":41,"ok":true,"battery_v":7.97}
{"type":"ack","seq":42,"ok":false,"reason":"obstacle","sonar_mm":180}
{"type":"error","reason":"invalid_body"}
{"type":"telemetry","battery_v":7.97,"sonar_mm":412, ...}
```

`seq` is echoed untouched so a client can measure its own round trip and display it.
Telemetry arrives on the same socket once a second, so a driving client needs no second
connection and no polling.

A malformed frame gets an `error` and the socket stays open — turning one bad frame into a
dropped control link would be worse than ignoring it.

**Closing the socket stops the robot immediately**, without waiting out the TTL. A closed
socket is a released stick, and TCP says so within milliseconds on a LAN.

### `POST /look` and `GET /look`

```json
{"pan_deg": 0, "tilt_deg": -10}
```

Clamped to ±45° server-side. `GET` returns the last **clamped** values, not what was
asked for.

### `POST /led` and `GET /led` — the two front RGBs

```json
{"on": true, "r": 0, "g": 255, "b": 40}
```

Channels clamped to 0–255. `GET` returns the last state asked for, or all `null` before
anything has been. Switching off writes black to the hardware but **remembers the colour**,
so a UI toggling back on does not come back black.

**These work when `TurboPi.py` is down**, unlike everything else here. The LEDs live on the
ultrasonic module at I2C `0x77`, not on the serial bus the motors use, and
`HiwonderSDK/Sonar.py` opens and closes SMBus per transaction rather than holding it — so
the gateway drives them directly and nothing contends. It also keeps LED traffic off the
single-threaded RPC server, where drive commands live.

Two things to know. `Functions/Avoidance.py` writes these same LEDs, so a running demo will
fight a UI for them — the demo wins, intermittently. And a colour is three sequential byte
writes, so a write interleaved with the demo's can show a wrong colour for a frame. Neither
is dangerous; both are worth not being surprised by.

## The sonar guard

Forward motion is refused when the ultrasonic sensor reads closer than
`TURBOPI_SONAR_STOP_MM` (default **250 mm**; set `0` to disable). The refusal is a `409`
carrying the distance:

```json
{"ok": false, "reason": "obstacle", "sonar_mm": 180}
```

Three decisions worth knowing:

**Only forward is blocked.** Reversing, strafing and rotating are how you get out of a
corner; blocking them would strand the robot against a wall with no way back.

**An unknown distance does not block.** The vendor's `getDistance()` returns `99999` when
the I2C read throws, and `0` when no echo comes back — which on an ultrasonic sensor
usually means *nothing is in range*, the opposite of near. Treating either as an obstacle
would make the robot undrivable the moment the sensor hiccuped. Both are treated as "no
reading", and `/telemetry` exposes `sonar_usable` so a client can say so.

**It polls harder while driving** (5 Hz) than idle (1 Hz), because a guard acting on a
one-second-old distance is not a guard — but the robot's RPC server takes one request at a
time, so polling that hard when nothing is moving would compete with the commands that
matter.

**Never tested against the real sensor.** The thresholds are guesses. Check what the sonar
actually reads at 25 cm before trusting this, and set it to `0` if it misbehaves.

## The watchdog

A background task on a 25 ms tick — a quarter of the shortest legal TTL. If the last
`/drive` is older than the TTL it carried, all four motors go to zero. It is not a timer
armed by the request, not a cancellation callback, and not anything the client can fail
to trigger.

Layers, outermost first:

1. **The watchdog task** — covers the client, the network, the phone, the app.
2. **The service's SIGTERM handler** — covers `systemctl stop/restart` and reboot.
3. **`ExecStopPost=/home/pi/turbopi-stop-motors.sh`** — runs on *any* exit, including a
   crash or a `SIGKILL`, when no Python of ours gets to run at all.
4. **A stop on startup** — covers the case where the previous process died mid-drive.

### The stop is owed until the robot acknowledges it

Every one of those four layers reaches the motors through port 9030. **If `TurboPi.py`
itself dies while the wheels are turning, none of them can do anything** — there is no
other route to the board while it is down.

So the gateway tracks two separate facts, and keeps them separate:

| | |
|---|---|
| `motors_live` | an unexpired drive command is in effect → decides *when to fire* |
| `stop_owed` | the board may be holding non-zero duty, unacknowledged → decides *when to keep trying* |

A stop that could not be delivered **does not clear `stop_owed`**. The watchdog re-attempts
it every 250 ms (logging at most every 5 s) until the robot answers. `turbopi.service` has
`Restart=always`, so `TurboPi.py` comes back within seconds — and the pending stop lands on
the first attempt after it does.

That bounds a runaway to however long `TurboPi.py` takes to restart, instead of forever.
It does not eliminate it. `stop_owed` is exposed in `/telemetry` so a client can show it.

**Nothing zeroes the motors when `TurboPi.py` starts.** `set_motor_duty` appears in exactly
one place in the vendor source — the `SetBrushMotor` RPC handler — so a restart does not by
itself change what the board is doing. The gateway's pending stop is the only thing that
does.

**The remaining gap, and how it could be closed.** The board's serial port is *not* opened
with `exclusive=True` (`ros_robot_controller_sdk.py` line 105), so it is only held by
convention, not by the kernel. While `TurboPi.py` is dead the port is genuinely free, and a
stop could be written to it directly:

```
AA 55 03 <len> 05 04  00 00000000  01 00000000  02 00000000  03 00000000  <crc8>
```

— `set_motor_duty` sub-command `0x05`, four motors, each `<Bf` of (index, 0.0). Building
that fallback is the real fix for this failure mode. It is **not built**, deliberately:
writing to a port another process may hold is only safe behind a check that nothing does,
and none of it can be tested without the robot. Worth doing after the first hardware
session, not before.

What nothing covers: the Pi losing power while the expansion board keeps its own. Both are
fed from the same battery here, so that case does not arise.

## Two vendor bugs this works around

### The `GetRunningFunc` bug

`RPCServer.py`:

```python
def GetRunningFunc():
    return runbymainth("GetRunningFunc", ())
```

`runbymainth` begins `if callable(req)`. A string is not callable, so this returns
`E05 - Not callable` **every time**. There is no path where it succeeds.
`Functions/Running.py` already contains `getLoadedFunc`, which is what it was reaching
for. [`gateway/patch_getrunningfunc.py`](../gateway/patch_getrunningfunc.py) changes the
one line.

Until it is applied, the gateway cannot see demo state. It says so — in its log at
startup, and in `/telemetry`'s `demo_detection` — and leaves the `demo_running` guard
off rather than reporting "no demo running" when it simply cannot tell.

### Battery readings are intermittent

`get_battery()` pops from a queue, and `TurboPi.py`'s own `voltageDetection` thread is
draining that same queue once a second. So a read can legitimately come back `None`
without anything being wrong.

The gateway keeps a background poll at 1 Hz plus a last-known-good value, and treats a
reading older than 30 s as unknown rather than as stale-but-good. **An unknown battery
does not block driving** — refusing to drive whenever a queue read missed would make the
robot unusable, and the failure would look identical to a flat battery. It reports
`battery_v: null` and logs instead.

## Why it proxies rather than driving the hardware directly

`TurboPi.py` holds `/dev/ttyAMA0` exclusively, the same way it holds the camera. Any
service importing `HiwonderSDK` and opening the board itself fails while TurboPi.py is
running — and TurboPi.py is what serves the video. Hence four dependencies, no robot
libraries, no serial access, no conflict.

The robot's RPC server is `run_simple()` with threading off: **one request at a time**.
The gateway therefore serialises its own calls behind a priority lock so a stop never
queues behind a telemetry poll, and sends `Connection: close` on every request — a
kept-alive connection to a single-threaded werkzeug server can hold that server open and
block every other caller, including the watchdog.

## Security, stated plainly

The token is sent as a plain header over plain HTTP on the LAN. Anyone who can sniff
your Wi-Fi can read it, and then drive the robot. That is an accepted trade for a robot
that never leaves the LAN, is never port-forwarded, and is switched off most of the time
— but it is a trade, not an absence of risk. TLS with a self-signed certificate is the
upgrade if the threat model changes.

`/health` is unauthenticated and leaks liveness, uptime and battery voltage to anything
on the LAN.

## Tests

```bash
cd gateway && python3 test_gateway.py
```

108 checks against a stand-in for the robot's RPC server that reproduces its real quirks:
the 3-element envelope and the 2-element failure variant, `echo` returning no envelope at
all, the broken `GetRunningFunc`, the 2-second `StopFunc`, millivolt battery readings.

The gateway runs as a real subprocess over real HTTP, the same way systemd runs it, so
startup behaviour, the background watchdog and SIGTERM are exercised rather than
simulated. No robot required.

## Not verified against hardware

Everything above is tested, but tested against a simulator. Two things can only be
confirmed on the robot:

1. **Which way `vy` and `omega` actually turn it.** The kinematics are derived from
   `HiwonderSDK/mecanum.py` and cross-check against it (pure forward alternates duty
   signs; pure rotation makes all four equal — both fall out of the wiring inversion).
   But "positive `vy` moves left" rests on the mecanum roller orientation, which is a
   physical fact nobody has measured on this robot. Drive it on a stand and watch.
2. **Which way pan and tilt move.** `SetPWMServo` maps `+90 → 500 µs`, inverted from the
   usual convention; the gateway negates to compensate. If either axis comes out
   mirrored, flip `TURBOPI_PAN_SIGN` or `TURBOPI_TILT_SIGN` in the unit file and restart.
