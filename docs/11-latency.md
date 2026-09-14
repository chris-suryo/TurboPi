# Input lag: what is actually slow, and what to do about it

The goal is a drive experience that feels direct. This is the plan for getting there, with
the one thing that has been measured separated from the many that have not.

## The finding: an HTTP request per command breaks down before the network does

A control loop that waits for each response before sending the next command can only send
as often as the round trip allows. That is fine on a LAN and fine on good cellular. On a
slow link it collapses, and **no amount of tuning on the client fixes it, because the
client cannot send faster than the network answers.**

Measured with `gateway/bench_latency.py` — both transports through the same artificial
delay proxy, same gateway, same fake robot, same 200 ms command interval, same 500 ms TTL:

```
  round trip  transport   landed   cmd/s  watchdog fires   verdict
------------------------------------------------------------------------------
         0 ms       HTTP       30     5.0               0   smooth
         0 ms  WebSocket       30     5.0               0   smooth

        60 ms       HTTP       30     5.0               0   smooth
        60 ms  WebSocket       30     5.0               0   smooth

       300 ms       HTTP       20     3.3               0   smooth
       300 ms  WebSocket       30     5.0               0   smooth

       700 ms       HTTP        9     1.5               7   STUTTERS - stopped 7x mid-drive
       700 ms  WebSocket       27     4.5               0   smooth
```

Read the 700 ms row. The robot **stopped itself seven times in six seconds** while the
operator was holding the stick down the whole time. That is the watchdog doing exactly its
job — commands stopped arriving in time, so it stopped the motors — and it is what a bad
cellular link would feel like: lurch, stop, lurch, stop.

The 300 ms row is the quieter warning. Nothing stutters, but a third of the commands never
get sent, so the stick is being sampled at 3.3 Hz instead of 5. It feels mushy before it
feels broken.

**A WebSocket holds its rate at every delay tested**, because sending is not gated on
acknowledgement. Same safety rules, same watchdog, same TTL — only the transport changes.

*(Honest about the method: the delay proxy adds a fixed, symmetric delay with no jitter and
no packet loss. Real cellular has both, and loss is worse for a WebSocket than this test
shows, because TCP retransmission stalls the whole stream. The direction of the result is
right; treat the exact numbers as a floor.)*

## Where the time actually goes

```
finger  ──►  phone  ──Tailscale──►  PC  ──LAN──►  gateway ──►  RPC ──► serial ──► wheels
                     └─ the slow leg ─┘           └──── microseconds to low ms ────┘

wheels  ──►  camera ──►  MJPEG ──LAN──►  PC decode+re-encode ──Tailscale──►  phone
             └────────── the slow leg, and the one you steer by ──────────┘
```

Two things follow. **The robot-side legs are not the problem** — a motor command is a
13-byte serial write at 1 Mbaud. And **video is the half that matters**, because you steer
by what you see, not by when the wheels move.

## The plan, in order

| # | Change | Owner | Why it is in this position |
|---|---|---|---|
| 1 | **WebSocket control** (`/ws/drive`) | ✅ built | The table above. Also a better dead-man: a closed socket stops the robot immediately instead of after the TTL |
| 2 | **Measure the real numbers** | needs hardware | Every latency figure in this repo except the table above is an estimate. Twenty minutes with a stopwatch beats another day of reasoning |
| 3 | **go2rtc on the PC** — WebRTC to the phone | not started | Video is the slow half. WebRTC is reported at 100–250 ms against MJPEG's 500–2000 ms. Runs on the PC, so the robot stays off the tailnet and the original constraint holds |
| 4 | **Check Tailscale is direct** | one command | `tailscale ping <pc>` says `direct` or `via DERP`. A relay roughly doubles the round trip, and it is free to check |
| 5 | **Sonar guard** | ✅ built | Not latency, but it matters more the further away you are |
| 6 | go2rtc on the Pi, robot on the tailnet | decided: later | Removes the PC hop and its re-encode. Revisit with numbers from step 2, not before |

## What was deliberately not done

**Sending HTTP commands without waiting for the response.** It looks like the cheap fix and
it is worse: requests pile up, and on a lossy link they arrive out of order. A stale drive
command overtaking a newer one is a robot that moves the wrong way, which is a worse
failure than a stutter.

**Letting the client raise the TTL when latency is high.** That trades the stutter for a
longer dead-man switch — the robot keeps driving for longer after contact is lost. The
whole point of the TTL is that the client does not get to choose it.

**WebRTC data channels for control.** This is what serious teleoperation uses, because an
unreliable/unordered channel over UDP has no head-of-line blocking at all — a lost packet
does not stall the ones behind it, which is exactly the failure a WebSocket still has on a
lossy cellular link. It is the right eventual answer and the wrong next step: far more
moving parts, and we do not yet have a single measured number from real hardware to say it
is needed.

## Measurements to take, once the robot is on

1. **Control round trip, on the LAN.** The gateway echoes `seq` on every WebSocket ack for
   exactly this: send with a timestamp, subtract on the ack.
2. **The same from the phone over Tailscale**, at home and on cellular.
3. **Glass-to-glass video lag.** Film your hand and the screen together with a phone at
   30 fps and count frames. Each one is 33 ms.
4. **`tailscale ping`** from the phone to the PC — `direct` or `via DERP`.
5. **`SetBrushMotor` round trip** against `echo`, to separate the serial write from the
   HTTP overhead.

Numbers 1 and 3 decide whether step 3 in the plan is urgent or optional.
