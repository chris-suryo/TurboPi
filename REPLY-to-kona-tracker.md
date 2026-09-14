# Reply to the kona-tracker session

Paste the block between the scissors markers into that session. It answers the twelve
questions and nothing else — it does not ask that session to change the app.

Full detail, including every test procedure: [`INTEGRATION-ANSWERS.md`](INTEGRATION-ANSWERS.md).

---

## ✂️ ——— START ———

The robot is built, running, and tested. Here are the twelve answers. Everything marked
**measured** was run against the hardware; everything else is read from the robot's source.
Nothing here is a guess, and where something is still untested I say so.

**Headline: the robot is your option 2 — MJPEG over plain HTTP, no credentials, on the LAN.
There is no RTSP. But the stream is multi-reader, which was the fact everything hinged on.**

### Camera

1. **Stream URL.** `http://10.0.0.3:8080/` — MJPEG, `multipart/x-mixed-replace`, boundary
   `--boundarydonotcross`. Single frame: `http://10.0.0.3:8080/?action=snapshot`.
   **Credentials: none.** Binds `0.0.0.0`, so any machine on the LAN can read it.

2. **Format.** 640×480, MJPEG (every frame a keyframe, no decode state), ~20 fps ceiling,
   JPEG quality 70 on the stream and 100 on snapshots. **Measured 18.9 fps.**

   **Multi-reader: yes, measured.** Two simultaneous clients got 18.7 and 18.6 fps — about 1%
   cost for the second reader — and `?action=snapshot` kept working while both streams ran.
   So the app can hold a continuous stream while someone else watches in a browser.

   One constraint that is real but does not affect you: `/dev/video0` itself is single-reader.
   The robot's control program holds the camera device exclusively for its process lifetime,
   so nothing else *on the Pi* can open it. Port 8080 is the only way in — which is fine,
   because that is the way you were going to consume it anyway.

3. **Latency: not yet measured.** Expect 150–400 ms for MJPEG over LAN Wi-Fi, plus whatever
   your decode/re-encode and Tailscale hop add. Treating that as an estimate, not a number.

4. **Wi-Fi drop.** The stream is one long-lived TCP connection. If Wi-Fi drops, the connection
   dies; the server survives and keeps serving, but **the client must reconnect** — there is no
   protocol-level resumption. Important detail for your reader: a dead MJPEG connection often
   does not raise an error, it just stops delivering bytes. Track time-since-last-frame and
   reconnect after a few seconds of silence rather than waiting for an exception.

### Driving

5. **There is an HTTP control API.** JSON-RPC 2.0 over POST to port **9030**, no auth.
   Forward: `{"jsonrpc":"2.0","method":"SetBrushMotor","params":[1,-35,2,35,3,-35,4,35],"id":1}`
   Stop: the same with all four speeds `0`.
   The sign pattern is not arbitrary — the left-side motors are wired inverted.

   **Trap: the response envelope is non-standard.** Every method returns
   `[success_bool, data, method_name]` inside `result`. **HTTP 200 does not mean success** — a
   failure is a 200 with `result[0] == false` and an error string in `result[1]`. One wrapper
   that unpacks this and raises is worth writing before anything else.

6. **A small HTTP wrapper on the Pi is the right shape — but it must proxy, not bypass.** The
   robot's control program holds `/dev/ttyAMA0` exclusively, the same way it holds the camera.
   A wrapper that imports the robot SDK and opens the board itself **will fail while the robot
   software is running** — and that software is what serves your video. A service that forwards
   to `:9030` needs only `fastapi`, `uvicorn`, `httpx`. No robot libraries, no serial access.

7. **Does the robot stop on its own? No. Nothing in the vendor stack ever expires a motor
   command.** A duty value goes to the expansion board's microcontroller and is held
   indefinitely. If a "drive forward" lands and the matching "stop" is lost, the robot keeps
   going until it hits something or the battery dies.

   **The watchdog has to live on the Pi, not in the app** — the failure you are protecting
   against *is* the app becoming unreachable. A watchdog on the far side of the failing link
   cannot fire. Design: mandatory TTL per command (~500 ms), background task zeroes all motors
   if nothing arrives within it, explicit e-stop endpoint, speeds clamped server-side.

8. **Command latency: not yet measured.** `echo` is a hardware-free method, so timing it gives
   pure network + HTTP overhead, and the delta against `SetBrushMotor` isolates the serial
   write. Physical response adds motor spin-up on top.

### Network and power

9. **`10.0.0.3`**, hostname `turbopi.local`. **Not DHCP-reserved yet** — that is a router
   change and it should happen before you point anything at the address.

10. **Same `/24` as everything else** (router `10.0.0.1`). Reachable from the Mac, measured.
    **Not yet confirmed from the Windows PC**, which is the machine that matters given the PC
    is the only thing that talks to cameras. Worth one `Test-NetConnection` on 8080 and 9030.

11. **Battery: 8.01 V fresh, steady through a full bring-up session. Runtime unmeasured.**
    Two 18650 cells in series. **There is no low-voltage alarm and no automatic shutdown** —
    the buzzer alarm in Hiwonder's documentation lives in their OS image, and this robot runs
    clean Raspberry Pi OS. It will run until the cells sag and the Pi browns out, which is the
    unclean power loss that corrupts SD cards. Voltage monitoring belongs in the same watchdog
    service: refuse drive commands below ~7.0 V.

12. **Autostart: done.** A systemd unit starts the robot software on boot, waits for the
    network, and restarts on crash — so both ports come back after a reboot or a power cycle
    without anyone logging in. One consequence: that service owns the camera and the serial
    port, so anything wanting them directly on the Pi must stop it first. Network consumers
    are unaffected.

### If you want RTSP after all

`mediamtx` (single Go binary) plus `ffmpeg` reading `localhost:8080` republishes as
`rtsp://10.0.0.3:8554/robot`, which is a drop-in for the existing Tapo path and cuts bandwidth
roughly 10× if you re-encode to H.264. Costs two processes and ~100–300 ms of latency.

**Recommendation: start with MJPEG.** One less moving part, and the lower latency matters if
the robot is ever driven rather than just watched. Revisit if it ends up a passive camera.

### Installed on the robot for this

Only a systemd unit (no package) and a one-line patch that opens the camera at startup —
stock, the robot serves no frames until a demo is loaded, and the failure is silent. Proposed
but **not yet installed**: `fastapi` + `uvicorn` + `httpx` for the watchdog/proxy service.

### Still open

Latency (3 and 8), Wi-Fi drop recovery (4), Windows PC reachability (10), battery runtime (11),
DHCP reservation (9).

## ✂️ ——— END ———
