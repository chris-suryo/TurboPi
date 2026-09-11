# Dog-cam integration — parked, not this project

You asked me to flag one fact if I came across it, because it decides whether a later
integration is even possible. I did, so here it is.

**Nothing here is being built. The robot stays a separate project.**

---

## The answer: yes, it's possible. MJPEG over HTTP on port 8080.

TurboPi already runs an HTTP video server. From `MjpgServer.py` in Hiwonder's own source:

```python
server = ThreadedHTTPServer(('', 8080), MJPG_Handler)
```

Two endpoints:

| Request | Response |
|---|---|
| `GET /` | `multipart/x-mixed-replace; boundary=--boundarydonotcross` — a continuous MJPEG stream |
| `GET /?action=snapshot` | A single JPEG (quality 100) |

It binds `''` — all interfaces, not just loopback — so **any machine on your LAN can read it.**
That's a format FastAPI can consume trivially, and browsers render it natively.

**There is no RTSP.** If you want RTSP later you'd have to add it yourself.

## The important caveat: the camera IS held exclusively

`Camera.py` opens `cv2.VideoCapture(-1)` in a background thread and holds it for the lifetime
of the process. `TurboPi.py` then pushes frames into `MjpgServer.img_show`.

So while Hiwonder's stack is running, **a second process cannot open `/dev/video0`.** You
cannot run your own capture alongside theirs.

That's not a blocker, it just determines the shape of the integration: **consume the port-8080
stream over the network**, don't try to grab the device. Which is the better design anyway —
it decouples the robot from your app entirely.

## What this means for the eventual switch

Your app would treat the robot as a network camera source: poll `http://<robot-ip>:8080/` for
the stream, or `?action=snapshot` for stills. Switching between the dog camera and the robot
camera becomes a matter of which URL you read from. No robot code required.

## Caveats to check when you actually get there

- **The stream is unauthenticated plain HTTP.** Fine on a home LAN; do not expose it to the
  internet as-is.
- `MjpgServer.py` sets `img_show = None` at the start of each non-snapshot request, and writes
  headers inside the frame loop in a slightly unusual order. It works, but it's homebrew rather
  than a hardened server. Worth re-reading before depending on it heavily.
- The stream only runs while `TurboPi.py` is running.
- Frame rate is capped by a `time.sleep(0.05)` in the handler — roughly 20fps ceiling.

**Verification when you get there:** open `http://<robot-ip>:8080/` in a browser on your PC.
If video appears, the integration is possible. That's the whole test.
