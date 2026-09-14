# Running the demos: what needs a display and what doesn't

There's a split in this codebase that will otherwise cost you an evening, because the error you
get won't say "you need a screen." Worth knowing before you hit it.

---

## The split

**`TurboPi.py` — the main application — is fully headless.**

It never opens a window. It pushes frames into the MJPEG server and you watch it in a browser.
Runs fine over a plain SSH session.

**Every script in `Functions/` calls `cv2.imshow()`.**

All of them:

```
Functions/LineFollower.py       Functions/ColorTracking.py     Functions/FaceTracking.py
Functions/Avoidance.py          Functions/ColorDetect.py       Functions/ColorWarning.py
Functions/GestureRecognition.py Functions/QuickMark.py         Functions/VisualPatrol.py
Functions/lab_adjust.py         CameraCalibration/*.py
```

`cv2.imshow()` needs somewhere to draw. Over bare SSH with no display there is nowhere, and
these scripts fail — usually with a Qt or GTK plugin error that reads like a broken OpenCV
install rather than a missing screen. It isn't broken. It just has no display.

## This does not mean buying a micro-HDMI cable

Three ways to get a display without one.

### 1. VNC — the default, use this

The Hiwonder image ships with VNC and their documentation assumes it throughout. You get the
Pi's actual desktop in a window on your PC, over the network.

Install VNC Viewer on Windows, connect to the Pi's IP, log in. Now `cv2.imshow()` has a
desktop to draw on and every demo works, including the ones where watching the OpenCV debug
window *is* the point — `lab_adjust.py` is a live colour-threshold tuner and is meaningless
without seeing it.

**"Headless" means no monitor attached to the Pi. It does not mean no graphical output.**
Those are different things, and the demos want the second one.

### 2. Run `TurboPi.py` and watch port 8080

Fully headless, no VNC:

```bash
cd /home/pi/TurboPi
~/turbopi-venv/bin/python TurboPi.py
```

Then open `http://<pi-ip>:8080/` in any browser on your LAN.

`TurboPi.py` needs `jsonrpc` and `werkzeug`, which are pip-installed into the venv — so it
**must** run under `~/turbopi-venv/bin/python`. Bare `python3` fails on import.

You're using the packaged application rather than the individual demo scripts — control is via
the WonderPi phone app or the JSON-RPC API on port 9030. Great for "is the camera working",
less good for reading a demo's source while watching what it does.

### 3. `ssh -X` with an X server on Windows

Install VcXsrv, then `ssh -X pi@turbopi.local`. Works, more setup, and video over X11 is slow.
Mentioned for completeness; VNC is better for this.

---

## Run it as a service, not from an SSH session

`TurboPi.py` runs in the **foreground**. Started by hand over SSH it dies when that session
closes, when the laptop sleeps, or on any stray Ctrl-C — and the camera and control API vanish
with it. That is a bad foundation for anything that consumes the robot over the network.

```bash
bash ~/install_turbopi_service.sh
```

Installs a systemd unit that starts on boot, restarts on crash, and survives SSH disconnects.
Uninstall with `--uninstall`.

```bash
sudo systemctl status turbopi      # healthy?
sudo journalctl -u turbopi -f      # live logs
sudo systemctl restart turbopi     # after changing code
sudo systemctl stop turbopi        # free the camera + serial port
```

> **The service owns the camera and the serial port while it runs.** To run a `Functions/`
> demo, a `MecanumControl/` script, or `bringup.py` by hand, **stop the service first** —
> otherwise they fail to open `/dev/video0` or `/dev/ttyAMA0`, which looks like broken
> hardware and isn't.

## Recommended order

1. **Camera present?** — `./check_hardware.py` reports `/dev/video*`.
2. **Camera actually working?** — run `TurboPi.py`, open `http://<pi-ip>:8080/`. If you see
   video, the camera, OpenCV and the capture thread are all fine. This is the fastest
   end-to-end proof and it needs no display at all.
3. **Then the demos over VNC**, where you can read each script while watching it run. Start
   with `Functions/ColorDetect.py` — it's the most legible.

## Port map

| Port | What | Bound to |
|---|---|---|
| **8080** | MJPEG video. `/` streams, `/?action=snapshot` gives one JPEG | all interfaces |
| **9030** | JSON-RPC control API (`RPCServer.py`, werkzeug) | all interfaces |
| 22 | SSH | — |
| 5900 | VNC | — |

Both 8080 and 9030 bind `''` — every interface, unauthenticated. Fine on a home LAN. Do not
port-forward them.

## Note on the camera

While `TurboPi.py` is running it **holds the camera exclusively** (`Camera.py` opens
`cv2.VideoCapture(-1)` and keeps it). You cannot run a `Functions/` demo at the same time —
the second one gets a camera it can't open.

Stop `TurboPi.py` before running a demo directly. If a demo reports no frames, this is the
first thing to check.
