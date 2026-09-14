#!/usr/bin/env python3
"""
Measure what network latency does to each control path.

The question this answers: an HTTP client that waits for each response before sending the
next command can only send as often as the round trip allows. At what delay does that stop
being fast enough to keep the robot's watchdog quiet, and does a WebSocket fix it?

Both clients go through the same artificial-delay TCP proxy, so the comparison is honest:
same gateway, same fake robot, same command rate, same everything but the transport.

    python3 bench_latency.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx
import websockets

import fake_rpc

RPC_PORT, GW_PORT, PROXY_PORT = 19040, 19041, 19042
TOKEN = "bench-token"
INTERVAL = 0.200          # what their drive.js sends at
TTL_MS = 500              # what the gateway is told to hold for
RUN_SECONDS = 6.0


async def delay_proxy(listen_port: int, target_port: int, one_way_ms: float) -> asyncio.Server:
    """A TCP relay that holds every chunk for one_way_ms in each direction."""
    delay = one_way_ms / 1000.0

    async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while chunk := await reader.read(65536):
                if delay:
                    await asyncio.sleep(delay)
                writer.write(chunk)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            with_suppress(writer.close)

    async def handle(client_r, client_w) -> None:
        try:
            server_r, server_w = await asyncio.open_connection("127.0.0.1", target_port)
        except OSError:
            with_suppress(client_w.close)
            return
        await asyncio.gather(pipe(client_r, server_w), pipe(server_r, client_w))

    return await asyncio.start_server(handle, "127.0.0.1", listen_port)


def with_suppress(fn) -> None:
    try:
        fn()
    except Exception:
        pass


def robot_state() -> dict:
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{RPC_PORT}/__calls", timeout=5) as r:
        return json.loads(r.read())


def reset() -> None:
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{RPC_PORT}/__control",
        data=json.dumps({"reset_calls": True}).encode(), method="POST"), timeout=5)


def count_watchdog_stops(start: float, end: float) -> int:
    """
    All-zero motor commands that landed *while the client was still driving*.

    The window matters. Every run ends with a stop -- the watchdog after an HTTP run, the
    disconnect handler after a WebSocket one -- and counting those would score a clean run
    as a stutter. The fake robot timestamps with the same monotonic clock as this process,
    so the comparison is exact. Only a zero that arrives between the first and last
    command is a real mid-drive stop.
    """
    return sum(1 for c in robot_state()["calls"]
               if c["method"] == "SetBrushMotor"
               and c["params"] == [1, 0, 2, 0, 3, 0, 4, 0]
               and start < c["t"] < end)


async def run_http(base: str) -> dict:
    """One command in flight at a time -- the shape of a fetch()-per-command loop."""
    landed, deadline = 0, time.monotonic() + RUN_SECONDS
    first_send = last_send = 0.0
    async with httpx.AsyncClient(headers={"X-Robot-Token": TOKEN}, timeout=2.0) as client:
        next_send = time.monotonic()
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_send:
                await asyncio.sleep(min(0.005, next_send - now))
                continue
            next_send = now + INTERVAL
            first_send = first_send or now
            last_send = now
            try:
                r = await client.post(f"{base}/drive",
                                      json={"vx": 0.3, "vy": 0, "omega": 0, "ttl_ms": TTL_MS})
                if r.status_code == 200:
                    landed += 1
            except httpx.HTTPError:
                pass
            # The defining property: the next command waits for this response.
    return {"landed": landed, "first": first_send, "last": last_send}


async def run_ws(ws_url: str) -> dict:
    """Frames at a fixed cadence. Nothing waits for an acknowledgement."""
    landed = 0
    async with websockets.connect(ws_url, additional_headers={"X-Robot-Token": TOKEN}) as sock:
        async def reader() -> None:
            nonlocal landed
            try:
                async for raw in sock:
                    if json.loads(raw).get("type") == "ack":
                        landed += 1
            except Exception:
                pass

        pump = asyncio.create_task(reader())
        deadline = time.monotonic() + RUN_SECONDS
        seq = 0
        first_send = last_send = 0.0
        while time.monotonic() < deadline:
            seq += 1
            now = time.monotonic()
            first_send = first_send or now
            last_send = now
            await sock.send(json.dumps({"vx": 0.3, "vy": 0, "omega": 0,
                                        "ttl_ms": TTL_MS, "seq": seq}))
            await asyncio.sleep(INTERVAL)
        await asyncio.sleep(0.3)
        pump.cancel()
    return {"landed": landed, "sent": seq, "first": first_send, "last": last_send}


async def main() -> int:
    fake_rpc.serve(RPC_PORT)
    token_file = tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False)
    token_file.write(TOKEN)
    token_file.close()
    env = dict(os.environ, TURBOPI_GATEWAY_TOKEN_FILE=token_file.name,
               TURBOPI_RPC_URL=f"http://127.0.0.1:{RPC_PORT}/",
               TURBOPI_GATEWAY_HOST="127.0.0.1", TURBOPI_GATEWAY_PORT=str(GW_PORT),
               TURBOPI_SONAR_STOP_MM="0")
    gw = subprocess.Popen([sys.executable, "robot_gateway.py"], env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient(timeout=2.0) as probe:
            for _ in range(60):
                try:
                    if (await probe.get(f"http://127.0.0.1:{GW_PORT}/health")).json()["turbopi"]:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.25)

        expected = int(RUN_SECONDS / INTERVAL)
        print(f"\nSending a drive command every {INTERVAL*1000:.0f} ms for {RUN_SECONDS:.0f} s "
              f"({expected} commands), with the gateway holding each for {TTL_MS} ms.")
        print("'Watchdog fires' means the robot stopped itself because nothing arrived in time.\n")
        print(f"{'round trip':>12} {'transport':>10} {'landed':>8} {'cmd/s':>7} "
              f"{'watchdog fires':>15}   verdict")
        print("-" * 78)

        for rtt_ms in (0, 60, 300, 700):
            proxy = await delay_proxy(PROXY_PORT, GW_PORT, rtt_ms / 2)
            base = f"http://127.0.0.1:{PROXY_PORT}"
            for name, runner in (("HTTP", run_http(base)),
                                 ("WebSocket", run_ws(f"ws://127.0.0.1:{PROXY_PORT}/ws/drive"))):
                reset()
                result = await runner
                await asyncio.sleep(1.2)          # let the watchdog settle
                # The window is first command sent -> last command sent. Anything after
                # that is the run ending, not the robot stuttering: an HTTP run trails a
                # watchdog fire and a WebSocket run trails its disconnect stop, and
                # counting either would score a clean run as a bad one.
                fires = count_watchdog_stops(result["first"], result["last"])
                rate = result["landed"] / RUN_SECONDS
                verdict = "smooth" if fires == 0 else f"STUTTERS - stopped {fires}x mid-drive"
                print(f"{rtt_ms:>10} ms {name:>10} {result['landed']:>8} {rate:>7.1f} "
                      f"{fires:>15}   {verdict}")
            proxy.close()
            await proxy.wait_closed()
            print()
    finally:
        gw.terminate()
        gw.wait(timeout=10)
        os.unlink(token_file.name)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
