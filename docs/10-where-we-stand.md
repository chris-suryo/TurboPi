# Where we stand, and what is actually left

Written 2026-09-14, after the gateway was built. The short version: **everything is built
and nothing has driven.** Every remaining task needs hands on the robot.

## Built and verified

| Thing | State |
|---|---|
| Pi 5, power, charger | ✅ Measured — 9 mV sag under load, no undervoltage |
| Assembly, motors, sensors, servos | ✅ All four motors, sonar, line sensor, pan-tilt confirmed |
| Camera stream | ✅ Measured — 18.9 fps, multi-reader, snapshot while streaming |
| Autostart on boot | ✅ systemd unit, survives reboot and SSH disconnect |
| Safety gateway (`:9031`) | ✅ Built, 67 tests — **against a simulator, not the robot** |
| kona-tracker drive UI | ✅ Built and merged — **against a fake gateway, not the robot** |

## Never done, in order

Each of these needs the robot powered on and in front of you.

1. **Install the gateway** — `bash install_gateway.sh`, then
   `python3 patch_getrunningfunc.py && sudo systemctl restart turbopi turbopi-gateway`.
   The patch is required, not optional.
2. **Watchdog proof on a stand** — `bash gateway_watchdog_proof.sh`. This is the gate.
   If it fails, stop; everything after it assumes it passed.
3. **Network** — `Test-NetConnection 10.0.0.3 -Port 9031` and `-Port 8080` from the PC,
   and a DHCP reservation for `10.0.0.3` at the router.
4. **`.env`** — `KONA_ROBOT_CONTROL_URL` and `KONA_ROBOT_TOKEN`.
5. **First drive, on a stand, on Slow.** Push each direction once and confirm which way it
   actually goes. Nobody has measured whether left is left.
6. **First drive on the floor.**
7. **Measure latency** — video and control, on the LAN first. Every latency number in this
   repo is an estimate.
8. **First remote drive over Tailscale**, once 1–7 pass.

Steps 1–6 are one evening. Step 7 is twenty minutes. Step 8 is where the interesting
problems start.

## The latency budget — estimates, all unmeasured

Phone → Tailscale → PC → LAN → robot. The PC is the only thing that talks to the robot.

| Leg | At home (Wi-Fi) | Away (cellular) |
|---|---|---|
| Control command, press → wheels move | ~30–80 ms | ~150–400 ms |
| Video, movement → pixels on screen | ~200–400 ms | ~400–900 ms |

Video is the slow half, and it is the half that matters for driving — you steer by what you
see. MJPEG is the reason: every frame is a whole JPEG, decoded and re-encoded by the PC
before the phone ever sees it.

**Consequence worth internalising: remote driving is nudge-and-wait, not driving.** At
half a second of video lag you cannot react to anything; you issue a short move, stop, look,
repeat. That is normal for teleoperation over the internet and not a defect in this build.

## If we want it to feel good, in order of payoff

1. **WebRTC instead of MJPEG** — `go2rtc` is a single binary that takes the existing
   `:8080` stream and republishes it as WebRTC at roughly 100–250 ms. This is the single
   biggest improvement available and nothing else comes close.
2. **WebSocket control instead of one HTTP POST per command** — at 5 commands/second over
   a high-latency link, the per-request overhead is real. Needs a gateway change.
3. **Check Tailscale is connecting directly, not via a relay** — `tailscale ping <pc>` says
   `direct` or `via DERP`. A relay roughly doubles the round trip.
4. **A sonar guard in the gateway** — refuse forward motion below some distance. Cheap, and
   it matters much more when nobody is in the room to catch the robot.
5. **Lower the resolution in drive mode** — fewer pixels, less encode, less bandwidth.

## Standalone driving app, or build it into kona-tracker?

**Keep it in kona-tracker.** The reason is that the real product boundary is not the app —
it is the gateway on the Pi. That is a plain HTTP API with a token, and *anything* can talk
to it: kona-tracker today, a standalone app later, a script, a game controller, a different
phone entirely. The UI is not where you get locked in.

What matters is keeping the seam clean: the robot client inside kona-tracker should stay a
thin wrapper over the gateway's HTTP API, with no robot knowledge leaking into the rest of
the app. As long as that holds, pulling it out later is an afternoon, and you get
kona-tracker's auth, Tailscale path and camera plumbing for free in the meantime.

Split it out when the robot grows features the dog camera has no use for, when robot work
starts destabilising the dog camera, or when you want to open-source one and not the other.
None of those is true yet.
