# Robot → kona-tracker integration: answers

**Read this first: I cannot reach your robot.** I work in a cloud container with no route to
your LAN. Every claim below is one of three things, and each is labelled:

| Mark | Meaning |
|---|---|
| **[SOURCE]** | Read directly from TurboPi's code on disk. Definitive — this is what the software does |
| **[MEASURED]** | Observed on your hardware earlier in this project, with the output in `PHASE1-RESULTS.md` |
| **[NEEDS TEST]** | Requires running on your hardware. I've written the test; you run it, I interpret |

You asked me to test rather than reason. I've done that where I can, and built the tests for
the rest rather than guessing. Nothing below is presented as tested when it wasn't.

---

## The two that decide everything

### ⚠️ Read this before anything else: the camera is not on by default [SOURCE]

`TurboPi.py` constructs `Camera.Camera()` but **never calls `camera_open()`**. The only thing
that opens the camera is `Functions/Running.py`'s `loadFunc()` — i.e. **loading a demo**.

So a freshly started robot serves **no frames at all**, and the failure is silent in an
unusually nasty way: `MjpgServer`'s snapshot branch does nothing when there is no frame — it
sends no status line, no headers, nothing — and the connection closes. `curl` reports that as
**HTTP 000**, which is indistinguishable from the server being down. `GET /` meanwhile returns
200, because the streaming branch sends headers before checking for frames.

It gets worse for your use case: `unloadFunc()` calls `camera_close()`, so the stream also dies
whenever a demo is unloaded.

That is reasonable for Hiwonder's own app, where you always load a demo first. **It is wrong
for treating the robot as a camera source another machine can just connect to**, which is
exactly what kona-tracker needs.

**Fixed by `scripts/patch_camera_always_on.py`** — opens the camera at startup and reopens it
if something closes it. Demos still work unchanged.

```bash
~/turbopi-venv/bin/python ~/patch_camera_always_on.py
sudo systemctl restart turbopi
```

### Q1. Stream URL — **yes, the picture can leave the robot** [MEASURED 2026-09-14]

| | |
|---|---|
| **Stream** | `http://10.0.0.3:8080/` — MJPEG, `multipart/x-mixed-replace; boundary=--boundarydonotcross` |
| **Single frame** | `http://10.0.0.3:8080/?action=snapshot` — one JPEG |
| **Credentials** | **None.** No auth of any kind |
| **Binds** | `0.0.0.0` — any machine on the LAN |

**This is your option 2 (MJPEG over plain HTTP), not option 1 (RTSP).** There is no RTSP
server in TurboPi. See "Getting RTSP" below — it's achievable and I recommend it.

**Confirmed on your hardware**, from the Mac, with `scripts/camera_multireader_test.sh`:
reachable, bytes verified as real JPEG (`FFD8` magic, 190692 bytes), **18.9 fps** sustained on
a single reader. Live video also confirmed visually in a browser at `http://10.0.0.3:8080/`.

### The multi-reader question — **ANSWERED: multiple network readers work** [MEASURED 2026-09-14]

Two separate facts, and conflating them is what costs people days:

**`/dev/video0` IS single-reader.** [SOURCE] `Camera.py` opens `cv2.VideoCapture(-1)` in a
background thread and holds it for the process lifetime. While `TurboPi.py` runs, **nothing
else on the Pi can open the camera device** — not another script, not ffmpeg, not v4l2. That
constraint is real, and it is why anything wanting the picture must go through port 8080.

**But the MJPEG server re-publishes those frames over the network**, and that server is a
`ThreadingHTTPServer` — one thread per client. [SOURCE] So multiple *network* readers work even
though multiple *device* readers cannot — and that is now measured, not predicted:

| Test | Result |
|---|---|
| Single reader | **18.9 fps** |
| Two simultaneous readers | **A 18.7 fps / B 18.6 fps** — both received video |
| Degradation from adding the second reader | **~0.2 fps (about 1%)** |
| Snapshot (`?action=snapshot`) *while* a stream is running | **Works** — 212958-byte JPEG |

**What this means for kona-tracker, concretely:** the app can hold a continuous stream while
you simultaneously watch the robot in a browser, or pull snapshots, without either one
breaking the other. The 20 fps ceiling is the server's `time.sleep(0.05)`, not contention —
18.9 fps measured against a 20 fps theoretical maximum is the network and JPEG encode, and it
does not move when a second reader arrives.

**The `img_show = None` blip I flagged from source did not materialise as a problem.** The code
does reset that global per stream request, but the main loop overwrites it fast enough that
neither reader lost frames measurably. Recorded as: real in the code, not observable in
practice at this frame rate.

One thing this does **not** prove: sustained multi-hour stability, or behaviour with three or
more readers. Two readers over ~10 seconds each is what was tested.

---

## Camera

### Q2. Resolution, frame rate, codec [SOURCE]

| | |
|---|---|
| Resolution | **640×480** (`Camera.py`, `resolution=(640, 480)`) |
| Codec | **MJPEG** — independent JPEG frames, no inter-frame compression |
| Frame rate | **~20 fps ceiling.** The handler does `time.sleep(0.05)` between frames |
| Quality | 70 on the stream, 100 on `?action=snapshot` |
| Camera native | YUYV requested from the USB camera, re-encoded to JPEG by the Pi |

MJPEG means every frame is a keyframe: high bandwidth (roughly 5–15 Mbps at this size and
rate), but trivially seekable and no decode state. Your existing OpenCV path will handle it.

### Q3. Latency [NEEDS TEST]

Must be measured with a human in the loop. Procedure, from most to least accurate:

1. Open the stream in a browser next to the robot. Wave sharply, ~10 times, and estimate.
2. Better: **film your hand and the screen together with a phone**, then count frames between
   the real wave and the on-screen one. At 30 fps phone video, each frame is 33 ms.
3. Also measure it **through the app** — your pipeline adds OpenCV decode, re-encode to JPEG,
   and Tailscale. That number is the one that matters operationally.

Rough expectation, stated as expectation not measurement: MJPEG over LAN Wi-Fi is typically
150–400 ms. Your app's re-encode and Tailscale will add to it.

### Q4. Wi-Fi drop and recovery [NEEDS TEST]

[SOURCE] The stream is a single long-lived TCP connection. When Wi-Fi drops, that connection
dies. The **server** survives — `ThreadingHTTPServer` just loses a thread — but **the client
must reconnect.** There is no automatic resumption at the protocol level.

Practical consequence for the app: **your consumer needs a watchdog.** A dead MJPEG connection
does not reliably raise an error; it just stops delivering. Track time-since-last-frame and
reconnect after a few seconds of silence.

Test: start the stream, walk the robot out of range until it drops, walk back, and see whether
frames resume without restarting anything on the Pi. My expectation is the server is fine and
the client must reconnect — worth confirming.

---

## Driving

### Q5. How to send a drive command — **there is an HTTP API** [SOURCE]

JSON-RPC 2.0 over HTTP POST to port **9030**. Full reference in
[`docs/08-robot-api.md`](docs/08-robot-api.md). The curl you asked for:

**Forward:**
```bash
curl -s -X POST http://10.0.0.3:9030/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,-35,2,35,3,-35,4,35],"id":1}'
```

**Stop — always send this:**
```bash
curl -s -X POST http://10.0.0.3:9030/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,0,2,0,3,0,4,0],"id":1}'
```

The sign pattern is not arbitrary: motors 1 and 3 (the left side) are wired inverted, and
`HiwonderSDK/mecanum.py` negates them when driving forward. Send all four positive and the
robot spins instead of advancing.

**Two traps for whoever writes the client:**

1. **The response envelope is non-standard.** Every method returns `[success_bool, data,
   method_name]` inside `result`. **HTTP 200 does not mean success** — a failure is a 200 with
   `result[0] == false` and an error string in `result[1]`.
2. `SetPWMServo` ignores its second parameter (an argument-slicing quirk) and takes **angles on
   an inverted scale**, not microseconds.

### Q6. Is a small HTTP wrapper on the Pi reasonable? **Yes — but it must proxy, not bypass**

[SOURCE] **`TurboPi.py` holds `/dev/ttyAMA0`**, the same way it holds the camera. Two
processes writing packets to the same port interleave bytes and corrupt each other, so in
practice it owns it. (Correction to an earlier version of this doc: it is *not* OS-enforced
— the port is opened without `exclusive=True`, so a second open succeeds. The exclusivity
is by convention, not by the kernel. That matters only for a last-resort stop written
directly to the port while `TurboPi.py` is dead; see `docs/09-gateway.md`.) So a
wrapper that imports `HiwonderSDK` and opens the board itself **will fail while TurboPi.py is
running** — and TurboPi.py is what serves your camera.

The correct shape: a small service that **proxies to port 9030**. Dependencies: `fastapi`,
`uvicorn`, `httpx`. That's it — no robot libraries, no serial access, no conflict.

**Built.** [`gateway/robot_gateway.py`](gateway/robot_gateway.py), documented in
[`docs/09-gateway.md`](docs/09-gateway.md), 62 passing tests in
[`gateway/test_gateway.py`](gateway/test_gateway.py).

### Q7. **Does the robot stop on its own? NO.** [SOURCE] — and this is the important one

There is **no timeout, no watchdog, and no dead-man behaviour anywhere** in Hiwonder's stack.

`board.set_motor_duty()` sends a duty value to the expansion board's microcontroller, which
**holds it indefinitely**. Nothing in `RPCServer.py`, `TurboPi.py`, or the SDK expires a motor
command. A "drive forward" that arrives with the matching "stop" lost means a robot that keeps
going until it hits something, runs out of battery, or you physically switch it off.

Your instinct is right, and the watchdog belongs on the Pi. **Reasons it must not live in the
app:** the failure mode you're protecting against *is* the app becoming unreachable — over
Tailscale, over Wi-Fi, or because the phone locked. A watchdog on the far side of the failing
link cannot fire.

**Built** — the same FastAPI service from Q6, with four independent layers of stop:
the background watchdog, a SIGTERM handler, a systemd `ExecStopPost` that runs even on a
crash or `SIGKILL`, and a stop on startup. Design:

- Accepts drive commands with a **mandatory TTL** (say 500 ms)
- Forwards them to `:9030`
- Runs a background task: if no command arrives within the TTL, **send all-motors-zero**
- Exposes an explicit emergency stop
- Clamps speeds server-side

The robot then physically cannot run away, regardless of what the network or the app does.
**I'd strongly recommend building this before you drive it from a phone at all.**

### Q8. Command latency [NEEDS TEST]

Measure the round trip first:

```bash
curl -s -o /dev/null -w 'rpc round trip: %{time_total}s\n' \
  -X POST http://10.0.0.3:9030/ -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"echo","params":["ping"],"id":1}'
```

`echo` touches no hardware, so that's pure network + HTTP + JSON-RPC overhead. Then time
`SetBrushMotor` the same way; the difference is the serial write to the board. Physical
response adds motor spin-up on top, which needs the phone-video method from Q3.

---

## Network and power

### Q9. IP address and reservation [MEASURED / ACTION NEEDED]

**Current: `10.0.0.3`** (measured: `hostname -I`). Hostname `turbopi.local`.

**Not reserved yet — you should do this.** Log into your router at `10.0.0.1`, find the DHCP
reservation page, and pin `10.0.0.3` to the Pi's Wi-Fi MAC. You asked for fixed IP and no
mDNS-only addressing; this is that step, and I can't do it for you.

Get the MAC with: `ip link show wlan0 | grep ether`

### Q10. Same LAN segment, reachable from the Windows PC [NEEDS TEST]

Your Mac is `10.0.0.63`, router `10.0.0.1`, robot `10.0.0.3` — same `/24`, so the PC should
reach it directly if it's on the same network. **Confirm from the Windows PC**, since that's
the machine that matters:

```powershell
ping 10.0.0.3
Test-NetConnection 10.0.0.3 -Port 8080
Test-NetConnection 10.0.0.3 -Port 9030
```

Both ports must show `TcpTestSucceeded : True`. Note Tailscale doesn't enter into this — the PC
talks to the robot over plain LAN, exactly as you wanted.

### Q11. Battery life and low-battery behaviour [NEEDS TEST / SOURCE]

**Measured so far:** 8.01 V freshly charged, steady through the whole bring-up session.

**Runtime is unmeasured.** Two 18650 cells in series. To measure, run this and note when it
crosses thresholds:

```bash
while true; do
  printf '%s ' "$(date +%H:%M:%S)"
  curl -s -X POST http://10.0.0.3:9030/ -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"GetBatteryVoltage","params":[],"id":1}'
  echo; sleep 60
done | tee battery-log.txt
```

Do it once idle-with-streaming and once driving.

**Low-battery behaviour — this needs saying plainly.** [SOURCE] Hiwonder's docs describe a
buzzer alarm below 7.1 V, but that lives in **their image's** services. **This build runs clean
Raspberry Pi OS, so there is no low-voltage alarm and no automatic shutdown.** The robot will
run until the cells sag enough that the Pi browns out — which is an unclean power loss, and the
exact thing that corrupts SD cards.

**Add voltage monitoring to the watchdog service.** It's already polling; having it refuse
drive commands below ~7.0 V and log a warning is nearly free.

### Q12. Does anything need starting by hand after reboot? **Yes — everything.** [SOURCE]

Nothing autostarts on this build. After any reboot or power cycle you must SSH in and run
`TurboPi.py` by hand, or there is no camera and no control.

**Fixed.** `scripts/install_turbopi_service.sh` installs a systemd unit that starts on boot,
waits for the network, restarts on crash, and survives SSH disconnects:

```bash
bash ~/install_turbopi_service.sh
```

This also fixes a subtler problem that bit us during setup: run from an SSH session,
`TurboPi.py` dies when that session closes — so the camera and API disappear with no error
anywhere, and the next thing you notice is a connection refused.

One consequence to know: **the service owns the camera and the serial port.** Anything wanting
them directly on the Pi must `sudo systemctl stop turbopi` first. This does not affect network
consumers, which is what kona-tracker will be.

---

## Getting RTSP — how to reach your option 1

You'd rather have RTSP because it's a drop-in for your existing Tapo code path. TurboPi doesn't
speak it, but the Pi can republish:

**`mediamtx`** (a single Go binary, no dependencies) runs an RTSP server on the Pi. **`ffmpeg`**
reads the MJPEG from `localhost:8080` and publishes into it. Your app then opens
`rtsp://10.0.0.3:8554/robot` with the code it already has.

| | |
|---|---|
| **Install** | `mediamtx` (single binary), `ffmpeg` (apt) |
| **Gains** | Drop-in for your existing path; RTSP is genuinely multi-reader; optional re-encode to H.264 cuts bandwidth by ~10× |
| **Costs** | Two more processes; adds ~100–300 ms latency; H.264 encode costs CPU (the Pi 5 has headroom) |

**My recommendation: start with MJPEG, add RTSP only if you want it.** Reasons: MJPEG is one
less moving part while you're still proving the robot works end to end, the "a bit of new code"
is genuinely small (read `multipart/x-mixed-replace`, split on the boundary), and you'll want
the lower latency for *driving* — every transcode hop makes teleoperation worse. If the robot
ends up mostly a passive camera, revisit RTSP then.

---

## What I'd install, and why

You keep a list. Nothing has been installed for this yet — these are proposals:

| Package | Why | Needed? |
|---|---|---|
| `fastapi`, `uvicorn`, `httpx`, `pydantic` | The watchdog/proxy service (Q6, Q7, Q11), in a dedicated venv at `/home/pi/gateway-venv` | **Built** — see `docs/09-gateway.md` |
| systemd unit (no package) | Autostart `TurboPi.py` (Q12) | **Recommended** — you asked for it |
| `mediamtx` + `ffmpeg` | RTSP republish | Optional — only if you want option 1 |

---

## What I need from you

1. ~~`./scripts/camera_multireader_test.sh 10.0.0.3`~~ — **done 2026-09-14, all five sections
   passed.** Results recorded above. Q1 and the multi-reader question are closed.
2. ~~The systemd unit~~ — **done.** `scripts/install_turbopi_service.sh` is installed and the
   service comes back on boot.
3. **The PowerShell reachability output** from the Windows PC (Q10) — still open, and it is the
   one that matters, since the PC is the only machine that talks to cameras
4. **A latency estimate** from the wave test (Q3)
5. **A yes/no on the watchdog service** — I'll build it. Q7 is the reason: nothing in the
   vendor stack ever stops the motors on its own

Everything else is either answered above from source, or waits on those.
