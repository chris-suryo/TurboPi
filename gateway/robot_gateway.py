#!/usr/bin/env python3
"""
TurboPi safety gateway.

Sits between a network client (kona-tracker) and the robot's own JSON-RPC server on
localhost:9030. The client never talks to 9030 directly and never sees motor ids.

Why this exists: nothing in Hiwonder's stack ever stops the motors on its own. A duty
value goes to the expansion board's microcontroller and is held indefinitely. If a
"drive" arrives and the matching "stop" is lost -- dropped Wi-Fi, a locked phone, a
crashed app -- the robot keeps going until it hits something. The watchdog in this file
is the thing that prevents that, and it runs on the robot, on the near side of every
link that can fail.

Run:  python3 robot_gateway.py
Env:  TURBOPI_GATEWAY_TOKEN_FILE, TURBOPI_RPC_URL, TURBOPI_GATEWAY_HOST/PORT,
      TURBOPI_MAX_DUTY, TURBOPI_LOW_BATTERY_V
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Union

import httpx
import uvicorn
from fastapi import (Depends, FastAPI, Header, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictFloat, StrictInt

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

TOKEN_FILE = os.environ.get("TURBOPI_GATEWAY_TOKEN_FILE", "/etc/turbopi/gateway-token")
RPC_URL = os.environ.get("TURBOPI_RPC_URL", "http://127.0.0.1:9030/")
HOST = os.environ.get("TURBOPI_GATEWAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("TURBOPI_GATEWAY_PORT", "9031"))

# Motor duty at full stick. 35 is deliberately gentle -- this robot is light and the
# floor is usually a table. Raise it once you trust the control loop.
MAX_DUTY = int(os.environ.get("TURBOPI_MAX_DUTY", "35"))

# The duty below which the motors hum instead of turning. Measured, not guessed: every
# geared motor has a static-friction threshold, and on this robot the four are not equal
# -- the left pair break away well before the right. Below the worst of them, a "drive
# forward" turns two wheels and stalls two, which is not slow driving, it is veering.
#
# Set this to the duty at which ALL FOUR turn and the whole stick becomes usable: the
# smallest input asks for the slowest speed the robot can actually do, instead of asking
# for a speed it cannot do and sitting there buzzing. 0 disables the floor.
MIN_DUTY = int(os.environ.get("TURBOPI_MIN_DUTY", "0"))

LOW_BATTERY_V = float(os.environ.get("TURBOPI_LOW_BATTERY_V", "7.0"))

TTL_MIN_MS, TTL_MAX_MS = 100, 2000

# Sonar guard. Refuses forward motion closer than this, in millimetres; 0 disables it.
# Never tested against the real sensor -- see docs/09-gateway.md before trusting it.
SONAR_STOP_MM = int(os.environ.get("TURBOPI_SONAR_STOP_MM", "250"))
SONAR_MAX_AGE_S = 0.75        # older than this and the guard has nothing to go on
# Must stay BELOW SONAR_MAX_AGE_S. It was 1.0 against a 0.75 s window, which meant the
# cached reading was stale more often than not while the robot sat still -- so the guard
# was silently off for the first drive command after any pause, which is exactly the
# moment someone pushes forward at something parked in front of the robot. Found by a
# test that flaked on timing rather than by reading the code.
SONAR_POLL_IDLE_S = 0.5
SONAR_POLL_DRIVING_S = 0.2    # only while a command is live; the guard needs fresh data
WS_TELEMETRY_S = 1.0
TELEMETRY_CACHE_S = 0.5          # contract: cache RPC answers for 500 ms
LIVENESS_WINDOW_S = 5.0          # contract: "answered echo() in the last 5 s"
PROBE_INTERVAL_S = 2.0
WATCHDOG_TICK_S = 0.025          # 25 ms -- 1/4 of the shortest legal TTL
BATTERY_STALE_S = 30.0           # beyond this we report battery as unknown, not stale-good
DRIVE_LOG_INTERVAL_S = 2.0

# Servo direction. The robot's SetPWMServo maps angle +90 -> 500 us and -90 -> 2500 us,
# i.e. inverted relative to the usual convention. We negate so that our pan_deg/tilt_deg
# follow the conventional sense. Which physical direction that turns out to be is a
# mechanical fact that has NOT been verified against the assembled robot -- if pan or
# tilt comes out mirrored, flip the sign here, restart, done.
# The two front RGBs live on the ultrasonic module at I2C 0x77, registers 2-8, NOT on the
# serial bus the motors use. HiwonderSDK/Sonar.py opens and closes SMBus per transaction
# rather than holding it, so a second process can drive them without fighting TurboPi.py
# for anything. That is why /led works when the robot's own software is down and /drive
# does not.
LED_I2C_BUS = int(os.environ.get("TURBOPI_LED_I2C_BUS", "1"))
LED_I2C_ADDR = int(os.environ.get("TURBOPI_LED_I2C_ADDR", "0x77"), 0)
LED_REG_MODE = 2           # 0 = manual colour; TurboPi.py sets this at startup
LED_REG_PIXEL = (3, 6)     # first register of each pixel; then +1 green, +2 blue
#: Test hook. When set, LED writes go to this file as JSON lines instead of the I2C bus,
#: so the HTTP contract can be exercised without hardware. Never set in production.
LED_TRACE = os.environ.get("TURBOPI_LED_TRACE", "")

PAN_SIGN = int(os.environ.get("TURBOPI_PAN_SIGN", "-1"))
TILT_SIGN = int(os.environ.get("TURBOPI_TILT_SIGN", "-1"))
SERVO_TILT, SERVO_PAN = 1, 2     # confirmed from Functions/ColorTracking.py: servo_x = servo2
LOOK_LIMIT_DEG = 45.0
LOOK_MOVE_MIN_MS, LOOK_MOVE_MAX_MS = 50, 2000
LOOK_MOVE_MS = 300

# RPC timeouts. Motor writes are a serial write and return fast. Anything routed through
# the robot's runbymainth() (demo control) can take up to 2 s before it times out
# internally, so it gets its own, longer budget and is never called from a hot path.
RPC_TIMEOUT_FAST_S = 1.0
RPC_TIMEOUT_SLOW_S = 3.0
# Background polls get a short leash. The robot's RPC server is single-threaded, so an
# in-flight poll delays the next stop by however long it takes; capping it at 400 ms
# caps that delay too. A missed poll costs nothing -- the next one is a second away.
RPC_TIMEOUT_POLL_S = 0.4

# How often the watchdog re-attempts a stop it could not deliver, and how often it says so.
STOP_RETRY_S = 0.25
STOP_RETRY_LOG_S = 5.0

log = logging.getLogger("gateway")

Number = Union[StrictFloat, StrictInt]   # rejects strings, bools and null


# --------------------------------------------------------------------------------------
# Talking to the robot
# --------------------------------------------------------------------------------------

class RobotError(RuntimeError):
    """The robot answered, and said no. Carries the robot's own error string."""


class RobotUnreachable(RuntimeError):
    """The robot did not answer at all."""


class PriorityLock:
    """
    Serialises RPC calls, letting urgent ones (stops) jump the queue.

    The robot's RPC server is werkzeug's run_simple() with threading off, so it handles
    exactly one request at a time. Piling concurrent requests onto it just moves the
    queue into the kernel, where we can no longer reorder it -- so we keep the queue
    here instead, and make sure a stop is never stuck behind a telemetry poll.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._urgent_waiting = 0

    @contextlib.asynccontextmanager
    async def hold(self, urgent: bool = False):
        if urgent:
            self._urgent_waiting += 1
        try:
            while True:
                if not urgent and self._urgent_waiting:
                    await asyncio.sleep(0.005)
                    continue
                await self._lock.acquire()
                if not urgent and self._urgent_waiting:
                    self._lock.release()
                    await asyncio.sleep(0.005)
                    continue
                break
            try:
                yield
            finally:
                self._lock.release()
        finally:
            if urgent:
                self._urgent_waiting -= 1


class RobotRPC:
    """
    Thin JSON-RPC client for the robot, with the vendor envelope unpacked away.

    Every method on the robot returns [success, data, method_name] inside `result` --
    except `echo`/`add`, which are lambdas registered directly and return a bare value.
    Some failure paths return only a 2-element [success, error]. Both shapes are handled
    here so that nothing above this class ever sees an envelope.
    """

    NO_ENVELOPE = {"echo", "add", "map"}

    def __init__(self, url: str) -> None:
        self._url = url
        self._id = 0
        self.lock = PriorityLock()
        # Connection: close on every request, and no keep-alive pooling. A kept-alive
        # connection to a single-threaded werkzeug server can hold that server open and
        # block every other caller, including our own watchdog.
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            headers={"Connection": "close"},
            timeout=httpx.Timeout(RPC_TIMEOUT_FAST_S),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: list | None = None, *,
                   urgent: bool = False, timeout: float = RPC_TIMEOUT_FAST_S) -> Any:
        async with self.lock.hold(urgent=urgent):
            return await self._call_unlocked(method, params, timeout)

    async def _call_unlocked(self, method: str, params: list | None, timeout: float) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": self._id}
        try:
            resp = await self._client.post(self._url, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            raise RobotUnreachable(f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise RobotUnreachable(f"HTTP {resp.status_code} from robot")
        try:
            body = resp.json()
        except ValueError as exc:
            raise RobotUnreachable("robot returned non-JSON") from exc
        if "error" in body:
            raise RobotError(str(body["error"]))
        if "result" not in body:
            raise RobotUnreachable("robot response had no result")
        return self._unpack(method, body["result"])

    @staticmethod
    def _unpack(method: str, result: Any) -> Any:
        if method in RobotRPC.NO_ENVELOPE:
            return result
        if not isinstance(result, list) or len(result) < 2:
            raise RobotError(f"unexpected envelope from {method}: {result!r}")
        ok, data = result[0], result[1]
        if not ok:
            raise RobotError(str(data))
        return data

    # -- the specific calls we make ----------------------------------------------------

    async def echo(self) -> Any:
        return await self.call("echo", ["ping"], timeout=RPC_TIMEOUT_POLL_S)

    async def set_motor_duty(self, duties: dict[int, int], *, urgent: bool = False) -> None:
        params: list[int] = []
        for motor in sorted(duties):
            params.extend([motor, int(duties[motor])])
        await self.call("SetBrushMotor", params, urgent=urgent)

    async def stop_motors(self, *, urgent: bool = True) -> None:
        await self.set_motor_duty({1: 0, 2: 0, 3: 0, 4: 0}, urgent=urgent)

    async def battery_mv(self) -> int | None:
        # Returns raw millivolts, or None. None is normal and not an error: get_battery()
        # pops from a queue that TurboPi.py's own voltageDetection thread is also draining
        # once a second, so some reads legitimately come back empty.
        return await self.call("GetBatteryVoltage", timeout=RPC_TIMEOUT_POLL_S)

    async def sonar_mm(self) -> int | None:
        return await self.call("GetSonarDistance", timeout=RPC_TIMEOUT_POLL_S)

    async def running_func(self) -> Any:
        return await self.call("GetRunningFunc", timeout=RPC_TIMEOUT_POLL_S)

    async def stop_func(self) -> Any:
        return await self.call("StopFunc", urgent=True, timeout=RPC_TIMEOUT_SLOW_S)

    async def set_servos(self, pairs: list[tuple[int, float]],
                         move_ms: int = LOOK_MOVE_MS) -> None:
        # SetPWMServo(use_time_ms, <ignored>, servo, angle, servo, angle, ...).
        # args[1] is skipped by the server's own argument slicing; pass 1.
        params: list[Any] = [move_ms, 1]
        for servo, angle in pairs:
            params.extend([servo, angle])
        await self.call("SetPWMServo", params)


# --------------------------------------------------------------------------------------
# Mecanum kinematics
# --------------------------------------------------------------------------------------

def wheel_duties(vx: float, vy: float, omega: float, max_duty: int = MAX_DUTY,
                 min_duty: int = MIN_DUTY) -> dict[int, int]:
    """
    Body velocity -> the four motor duties, wiring inversion included.

    Frame: vx forward, vy left, omega counter-clockwise, each already clamped to [-1, 1].
    Motors: 1 front-left, 2 front-right, 3 rear-left, 4 rear-right.

    Wheel velocities (not duties):
        w1 = vx - vy - omega     w2 = vx + vy + omega
        w3 = vx + vy - omega     w4 = vx - vy + omega

    This is HiwonderSDK/mecanum.py's set_velocity() re-expressed in our frame. Theirs is
    polar and uses the opposite sense for both the lateral axis and rotation, so the
    signs on vy and omega are flipped relative to the source. Their (a+b) factor is a
    physical-units term; our inputs are normalised, so rotation authority is just omega.

    The left-hand motors are wired inverted, which is why mecanum.py sends
    [[1, -v1], [2, v2], [3, -v3], [4, v4]]. Same negation applied here.
    """
    w1 = vx - vy - omega
    w2 = vx + vy + omega
    w3 = vx + vy - omega
    w4 = vx - vy + omega

    # Normalise rather than clip: clipping a saturated combination changes the direction
    # the robot actually travels, which is worse than travelling slower.
    peak = max(abs(w1), abs(w2), abs(w3), abs(w4))
    if peak == 0.0:
        return {1: 0, 2: 0, 3: 0, 4: 0}
    if peak > 1.0:
        w1, w2, w3, w4 = w1 / peak, w2 / peak, w3 / peak, w4 / peak
        peak = 1.0

    # Lift the whole command above the static-friction floor, if one is configured.
    #
    # The lift is applied to the *magnitude* and every wheel is scaled by the same factor,
    # so the ratios between the four -- which are what set the direction a mecanum chassis
    # travels in -- come through untouched. Give each motor its own floor instead and you
    # bend the robot's path at low speed, trading a stall for a drift.
    span = max_duty * peak if min_duty <= 0 else min_duty + (max_duty - min_duty) * peak
    factor = span / peak

    return {
        1: int(round(-w1 * factor)),
        2: int(round(w2 * factor)),
        3: int(round(-w3 * factor)),
        4: int(round(w4 * factor)),
    }


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


# --------------------------------------------------------------------------------------
# Shared state
# --------------------------------------------------------------------------------------

@dataclass
class State:
    started_at: float = field(default_factory=time.monotonic)

    last_echo_ok: float = 0.0
    echo_seen: bool = False

    battery_v: float | None = None
    battery_at: float = 0.0
    sonar_mm: int | None = None
    sonar_at: float = 0.0

    demo: int | None = None
    demo_at: float = 0.0
    demo_probe_ok: bool = False       # is GetRunningFunc usable at all?
    demo_probe_logged: bool = False

    # Drive / watchdog
    last_drive_at: float | None = None
    ttl_s: float = 0.5
    motors_live: bool = False    # an unexpired drive command is in effect
    stop_owed: bool = False      # the board may be holding non-zero duty, unconfirmed
    last_stop_try: float = 0.0
    last_stop_fail_log: float = 0.0
    last_drive_log: float = 0.0

    # None until this gateway has commanded a position. Deliberately not 0.0: on a
    # restart the servos physically hold wherever they were, and reporting 0 would be
    # the gateway inventing a pose it has no way to know. The board CAN be asked -- the
    # SDK has pwm_servo_read_position -- but RPCServer does not expose it, and the read
    # is a blocking queue.get() with no timeout, so a board that never answers would
    # wedge the single-threaded RPC server for every caller, permanently. Not a trade
    # worth making to avoid saying "unknown".
    pan_deg: float | None = None
    tilt_deg: float | None = None

    ws_clients: int = 0

    # Last colour this gateway asked for. None until it has asked for one -- on a restart
    # the LEDs physically hold whatever they were showing, same as the servos.
    led: dict | None = None

    def alive(self) -> bool:
        return self.echo_seen and (time.monotonic() - self.last_echo_ok) <= LIVENESS_WINDOW_S


state = State()
rpc = RobotRPC(RPC_URL)
TOKEN: str | None = None


def read_token(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        value = handle.read().strip()
    if not value:
        raise ValueError(f"token file {path} is empty")
    return value


async def note_battery(mv: Any) -> None:
    if isinstance(mv, (int, float)) and not isinstance(mv, bool):
        volts = float(mv) / 1000.0
        if 3.0 < volts < 9.5:          # anything outside this is a bad read, not a battery
            state.battery_v = round(volts, 3)
            state.battery_at = time.monotonic()


def battery_fresh() -> float | None:
    if state.battery_v is None:
        return None
    if (time.monotonic() - state.battery_at) > BATTERY_STALE_S:
        return None
    return state.battery_v


def sonar_fresh() -> int | None:
    """
    A distance we are willing to act on, or None.

    Two readings mean "no idea", not "very close", and treating either as an obstacle
    would make the robot undrivable:

      99999  the vendor's getDistance() returns this when the I2C read throws
          0  no echo came back -- on an ultrasonic sensor that usually means nothing is
             in range at all, i.e. the opposite of near

    Anything at or above the vendor's own 5000 mm ceiling is simply far.
    """
    if state.sonar_mm is None:
        return None
    if (time.monotonic() - state.sonar_at) > SONAR_MAX_AGE_S:
        return None
    if state.sonar_mm <= 0 or state.sonar_mm >= 99999:
        return None
    return state.sonar_mm


# --------------------------------------------------------------------------------------
# Background tasks
# --------------------------------------------------------------------------------------

async def watchdog_task() -> None:
    """
    The reason this service exists.

    Not request-scoped, not a timer armed by the client, not a cancellation callback --
    a loop that runs regardless of what the rest of the process is doing. If the last
    /drive is older than the TTL it carried, the motors go to zero.

    Two separate facts are tracked, and conflating them is a bug we already made once:

      motors_live  an unexpired drive command is in effect       -> when to fire
      stop_owed    the board may be holding non-zero duty and    -> when to keep trying
                   we have not had a zero acknowledged

    A stop that could not be delivered does not clear stop_owed, so the watchdog keeps
    re-attempting it. That is what covers the case where TurboPi.py itself dies while the
    wheels are turning: every route to the motors runs through port 9030, so nothing can
    stop them while it is down -- but systemd restarts it, and the moment it answers
    again the pending stop lands. Without the retry the robot would simply keep going.
    """
    while True:
        await asyncio.sleep(WATCHDOG_TICK_S)
        now = time.monotonic()

        if state.motors_live and state.last_drive_at is not None:
            age = now - state.last_drive_at
            if age > state.ttl_s:
                state.motors_live = False
                log.warning("WATCHDOG FIRED - no /drive for %.0f ms (ttl %.0f ms), "
                            "stopping motors", age * 1000, state.ttl_s * 1000)

        if not state.stop_owed or state.motors_live:
            continue
        if (now - state.last_stop_try) < STOP_RETRY_S:
            continue
        state.last_stop_try = now
        try:
            await rpc.stop_motors()
            state.stop_owed = False
            log.info("motors confirmed stopped")
        except (RobotUnreachable, RobotError) as exc:
            if (now - state.last_stop_fail_log) >= STOP_RETRY_LOG_S:
                state.last_stop_fail_log = now
                log.error("STOP NOT DELIVERED - %s. The board may still be driving; "
                          "retrying every %.0f ms until it answers.", exc, STOP_RETRY_S * 1000)


async def liveness_task() -> None:
    while True:
        try:
            await rpc.echo()
            state.last_echo_ok = time.monotonic()
            state.echo_seen = True
        except (RobotUnreachable, RobotError):
            pass
        await asyncio.sleep(PROBE_INTERVAL_S)


async def demo_probe_task() -> None:
    """
    Track whether a built-in demo is loaded.

    Vendor bug: RPCServer.GetRunningFunc calls runbymainth("GetRunningFunc", ...) with a
    string where a callable is required, so it always fails with "E05 - Not callable".
    patch_getrunningfunc.py points it at Running.getLoadedFunc instead. Without
    that patch we cannot see demo state, and the demo_running guard cannot be enforced --
    which we say out loud rather than pretending the guard is active.
    """
    while True:
        try:
            value = await rpc.running_func()
            if isinstance(value, list) and value:
                value = value[0]
            state.demo = int(value) if isinstance(value, (int, float)) and int(value) > 0 else None
            state.demo_at = time.monotonic()
            if not state.demo_probe_ok:
                state.demo_probe_ok = True
                log.info("GetRunningFunc works - demo_running guard is active")
        except RobotError as exc:
            state.demo_probe_ok = False
            if not state.demo_probe_logged:
                state.demo_probe_logged = True
                log.warning(
                    "GetRunningFunc unusable (%s) - demo detection OFF. The 409 demo_running "
                    "guard cannot be enforced until patch_getrunningfunc.py is applied "
                    "and TurboPi.py restarted.", exc)
        except RobotUnreachable:
            pass
        await asyncio.sleep(PROBE_INTERVAL_S)


async def telemetry_refresh() -> None:
    """Refresh the cached battery/sonar values if they are older than the cache window."""
    now = time.monotonic()
    if (now - state.battery_at) > TELEMETRY_CACHE_S:
        try:
            await note_battery(await rpc.battery_mv())
        except (RobotUnreachable, RobotError):
            pass
    if (now - state.sonar_at) > TELEMETRY_CACHE_S:
        try:
            value = await rpc.sonar_mm()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                state.sonar_mm = int(value)
                state.sonar_at = time.monotonic()
        except (RobotUnreachable, RobotError):
            pass


async def sonar_poll_task() -> None:
    """
    Keep a distance reading fresh enough for the guard to use.

    Polls hard only while a drive command is live. The robot's RPC server takes one
    request at a time, so a 5 Hz poll running all the time would be competing with the
    drive commands for the thing that matters most.
    """
    while True:
        try:
            value = await rpc.sonar_mm()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                state.sonar_mm = int(value)
                state.sonar_at = time.monotonic()
        except (RobotUnreachable, RobotError):
            pass
        await asyncio.sleep(SONAR_POLL_DRIVING_S if state.motors_live else SONAR_POLL_IDLE_S)


async def battery_poll_task() -> None:
    """
    Keep a battery reading warm even when nobody is polling /telemetry.

    Without this the low-battery guard would have nothing to check on the first /drive
    after a quiet period, and reads come back empty often enough (see battery_mv) that
    a single on-demand read is not dependable.
    """
    while True:
        try:
            await note_battery(await rpc.battery_mv())
        except (RobotUnreachable, RobotError):
            pass
        await asyncio.sleep(1.0)


# --------------------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------------------

def write_leds(on: bool, red: int, green: int, blue: int) -> None:
    """
    Set both front RGBs. Raises RobotUnreachable if the bus is not there.

    Runs in a worker thread via asyncio.to_thread -- SMBus is blocking, and blocking the
    event loop would delay the watchdog, which is the one thing that must never wait.
    """
    red, green, blue = (0, 0, 0) if not on else (red, green, blue)

    if LED_TRACE:
        with open(LED_TRACE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"on": on, "r": red, "g": green, "b": blue}) + "\n")
        return

    try:
        from smbus2 import SMBus
    except ImportError as exc:
        raise RobotUnreachable("smbus2 is not installed in the gateway venv") from exc

    try:
        with SMBus(LED_I2C_BUS) as bus:
            bus.write_byte_data(LED_I2C_ADDR, LED_REG_MODE, 0)   # manual colour mode
            for first in LED_REG_PIXEL:
                bus.write_byte_data(LED_I2C_ADDR, first, red)
                bus.write_byte_data(LED_I2C_ADDR, first + 1, green)
                bus.write_byte_data(LED_I2C_ADDR, first + 2, blue)
    except OSError as exc:
        raise RobotUnreachable(f"I2C write failed: {exc}") from exc


async def require_token(x_robot_token: str | None = Header(default=None)) -> None:
    if TOKEN is None:
        raise HTTPException(status_code=503, detail="no_token_configured")
    if not x_robot_token or not secrets.compare_digest(x_robot_token, TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


class DriveBody(BaseModel):
    model_config = {"extra": "forbid"}
    vx: Number = 0
    vy: Number = 0
    omega: Number = 0
    ttl_ms: Number = 500


class LookBody(BaseModel):
    model_config = {"extra": "forbid"}
    pan_deg: Number = 0
    tilt_deg: Number = 0
    #: How long the board should take to sweep there, milliseconds, clamped [50, 2000].
    #: The default suits a discrete "look over there". A pad that sends a new angle as a
    #: thumb moves wants this much shorter -- each command re-targets a sweep already in
    #: progress, so a long duration means the servo never arrives before being told
    #: something new, and the camera lags the thumb by the sweep time.
    move_ms: Number = LOOK_MOVE_MS


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global TOKEN
    try:
        TOKEN = read_token(TOKEN_FILE)
        log.info("token loaded from %s (%d chars)", TOKEN_FILE, len(TOKEN))
    except OSError as exc:
        log.error("CANNOT READ TOKEN FILE %s (%s) - every authenticated request will 503",
                  TOKEN_FILE, exc)

    # Contract: send /stop once on start. A gateway restart after a crash is exactly when
    # the motors might still be spinning from the previous process.
    # A gateway restart after a crash is exactly when the motors might still be spinning
    # from the previous process, so the stop is owed until the robot acknowledges it.
    state.stop_owed = True
    try:
        await rpc.stop_motors()
        state.stop_owed = False
        log.info("startup stop sent")
    except (RobotUnreachable, RobotError) as exc:
        log.warning("startup stop could not be delivered (%s) - will retry until it lands",
                    exc)

    tasks = [asyncio.create_task(coro()) for coro in
             (watchdog_task, liveness_task, demo_probe_task, battery_poll_task,
              sonar_poll_task)]
    log.info("gateway listening on %s:%d -> %s (duty %d..%d, low battery %.1f V)",
             HOST, PORT, RPC_URL, MIN_DUTY, MAX_DUTY, LOW_BATTERY_V)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        # Contract: send /stop once on SIGTERM. uvicorn turns SIGTERM into a clean
        # shutdown, which runs this block.
        try:
            await rpc.stop_motors()
            log.info("shutdown stop sent")
        except (RobotUnreachable, RobotError) as exc:
            log.error("SHUTDOWN STOP FAILED (%s) - motors may still be running", exc)
        await rpc.aclose()


app = FastAPI(title="TurboPi gateway", version="1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    # Contract asks for 400 on a non-numeric body; FastAPI's default is 422.
    log.info("REFUSED %s - invalid body", request.url.path)
    return JSONResponse(status_code=400, content={"ok": False, "reason": "invalid_body",
                                                  "detail": exc.errors()[:4]})


@app.exception_handler(HTTPException)
async def on_http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        log.warning("REFUSED %s - bad or missing X-Robot-Token", request.url.path)
    body = exc.detail if isinstance(exc.detail, dict) else {"ok": False, "reason": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.get("/health")
async def health() -> dict:
    # No auth, and it must never touch the motors. Reads cached values only.
    return {
        "ok": True,
        "turbopi": state.alive(),
        "battery_v": battery_fresh(),
        "uptime_s": int(time.monotonic() - state.started_at),
    }


@app.get("/telemetry", dependencies=[Depends(require_token)])
async def telemetry() -> dict:
    await telemetry_refresh()
    age_ms = (None if state.last_drive_at is None
              else int((time.monotonic() - state.last_drive_at) * 1000))
    battery = battery_fresh()
    return {
        "battery_v": battery,
        "sonar_mm": state.sonar_mm,
        "driving": state.motors_live,
        "demo": state.demo,
        "last_command_age_ms": age_ms,
        "low_battery": bool(battery is not None and battery < LOW_BATTERY_V),
        # Extra, additive keys -- ignore them if you do not want them.
        "battery_age_ms": (None if state.battery_v is None
                           else int((time.monotonic() - state.battery_at) * 1000)),
        "demo_detection": state.demo_probe_ok,
        "stop_owed": state.stop_owed,
        "sonar_guard_mm": SONAR_STOP_MM,
        "max_duty": MAX_DUTY,
        "min_duty": MIN_DUTY,
        "pan_deg": state.pan_deg,
        "tilt_deg": state.tilt_deg,
        "sonar_usable": sonar_fresh() is not None,
    }


def refusal(status: int, reason: str, path: str = "/drive", **extra: Any) -> JSONResponse:
    log.warning("REFUSED %s - %s%s", path, reason,
                f" ({extra})" if extra else "")
    return JSONResponse(status_code=status, content={"ok": False, "reason": reason, **extra})


async def apply_drive(vx: Any, vy: Any, omega: Any, ttl_ms: Any) -> tuple[int, dict]:
    """
    The whole drive path, shared by POST /drive and the WebSocket.

    Returns (status, body) rather than a response so the socket can report the same
    outcomes as the HTTP route without either one growing its own copy of the safety
    rules. There has to be exactly one place that decides whether the robot may move.
    """
    vx = clamp(float(vx), -1.0, 1.0)
    vy = clamp(float(vy), -1.0, 1.0)
    omega = clamp(float(omega), -1.0, 1.0)
    ttl_ms = clamp(float(ttl_ms), TTL_MIN_MS, TTL_MAX_MS)

    if not state.alive():
        # Treat unreachable as a stop: forget that we are driving, so the watchdog does
        # not keep hammering a dead socket, and try one best-effort zero anyway.
        state.motors_live = False
        with contextlib.suppress(RobotUnreachable, RobotError):
            await rpc.stop_motors()
            state.stop_owed = False
        return 503, {"ok": False, "reason": "turbopi_unreachable"}

    battery = battery_fresh()
    if battery is not None and battery < LOW_BATTERY_V:
        state.motors_live = False
        with contextlib.suppress(RobotUnreachable, RobotError):
            await rpc.stop_motors()
            state.stop_owed = False
        return 409, {"ok": False, "reason": "low_battery"}

    if state.demo_probe_ok and state.demo:
        # Do not stop the motors here: the demo is legitimately driving them, and
        # yanking them out from under it would be a surprise, not a safety measure.
        return 409, {"ok": False, "reason": "demo_running"}

    # The sonar guard blocks forward motion only. Reversing, strafing and rotating are
    # how you get out of a corner, so blocking them would strand the robot against a wall
    # with no way back. Unknown distance does not block -- see sonar_fresh().
    if SONAR_STOP_MM > 0 and vx > 0:
        distance = sonar_fresh()
        if distance is not None and distance < SONAR_STOP_MM:
            state.motors_live = False
            with contextlib.suppress(RobotUnreachable, RobotError):
                await rpc.stop_motors()
                state.stop_owed = False
            return 409, {"ok": False, "reason": "obstacle", "sonar_mm": distance}

    duties = wheel_duties(vx, vy, omega)
    try:
        await rpc.set_motor_duty(duties)
    except RobotUnreachable as exc:
        state.motors_live = False
        log.error("REFUSED drive - robot unreachable mid-command: %s", exc)
        return 503, {"ok": False, "reason": "turbopi_unreachable"}
    except RobotError as exc:
        state.motors_live = False
        log.error("REFUSED drive - robot said no: %s", exc)
        return 502, {"ok": False, "reason": str(exc)}

    state.last_drive_at = time.monotonic()
    state.ttl_s = ttl_ms / 1000.0
    moving = any(duty != 0 for duty in duties.values())
    state.motors_live = moving
    # An acknowledged all-zero is the only thing that clears the debt.
    state.stop_owed = moving

    now = time.monotonic()
    if (now - state.last_drive_log) >= DRIVE_LOG_INTERVAL_S:
        state.last_drive_log = now
        log.info("drive vx=%.2f vy=%.2f w=%.2f ttl=%.0fms duties=%s",
                 vx, vy, omega, ttl_ms, [duties[m] for m in (1, 2, 3, 4)])

    return 200, {"ok": True, "battery_v": battery}


@app.post("/drive", dependencies=[Depends(require_token)])
async def drive(body: DriveBody) -> Any:
    status, payload = await apply_drive(body.vx, body.vy, body.omega, body.ttl_ms)
    if status != 200:
        log.warning("REFUSED /drive - %s", payload.get("reason"))
        return JSONResponse(status_code=status, content=payload)
    return payload


@app.post("/stop", dependencies=[Depends(require_token)])
async def stop() -> Any:
    """
    Always works. Not gated by battery, demo state, or anything else.

    Motors go to zero synchronously -- that is the part that matters. StopFunc is fired
    afterwards and only if a demo is actually loaded, because the vendor's StopFunc
    blocks for two full seconds and then fails when nothing is running, and the robot's
    RPC server is single-threaded: a pointless StopFunc would hold up the next real stop.
    """
    state.motors_live = False
    log.info("STOP requested")
    try:
        await rpc.stop_motors()
        state.stop_owed = False
    except RobotUnreachable as exc:
        log.error("STOP COULD NOT BE DELIVERED - robot unreachable: %s", exc)
        return JSONResponse(status_code=503,
                            content={"ok": False, "reason": "turbopi_unreachable"})
    except RobotError as exc:
        log.error("STOP refused by robot: %s", exc)
        return JSONResponse(status_code=502, content={"ok": False, "reason": str(exc)})

    if state.demo_probe_ok and state.demo:
        async def stop_demo() -> None:
            try:
                await rpc.stop_func()
                log.info("STOP also stopped demo %s", state.demo)
            except (RobotUnreachable, RobotError) as exc:
                log.warning("STOP could not stop demo: %s", exc)
        asyncio.create_task(stop_demo())

    return {"ok": True}


@app.post("/look", dependencies=[Depends(require_token)])
async def look_set(body: LookBody) -> Any:
    pan = clamp(float(body.pan_deg), -LOOK_LIMIT_DEG, LOOK_LIMIT_DEG)
    tilt = clamp(float(body.tilt_deg), -LOOK_LIMIT_DEG, LOOK_LIMIT_DEG)
    move_ms = int(clamp(float(body.move_ms), LOOK_MOVE_MIN_MS, LOOK_MOVE_MAX_MS))
    try:
        await rpc.set_servos([(SERVO_TILT, TILT_SIGN * tilt), (SERVO_PAN, PAN_SIGN * pan)],
                             move_ms=move_ms)
    except RobotUnreachable:
        return refusal(503, "turbopi_unreachable", "/look")
    except RobotError as exc:
        log.error("REFUSED /look - robot said no: %s", exc)
        return JSONResponse(status_code=502, content={"ok": False, "reason": str(exc)})
    state.pan_deg, state.tilt_deg = pan, tilt
    return {"ok": True}


class LedBody(BaseModel):
    model_config = {"extra": "forbid"}
    on: bool = True
    r: Number = 0
    g: Number = 0
    b: Number = 0


@app.post("/led", dependencies=[Depends(require_token)])
async def led_set(body: LedBody) -> Any:
    red, green, blue = (int(clamp(float(v), 0, 255)) for v in (body.r, body.g, body.b))
    on = bool(body.on)
    try:
        await asyncio.to_thread(write_leds, on, red, green, blue)
    except RobotUnreachable as exc:
        log.warning("REFUSED /led - %s", exc)
        return JSONResponse(status_code=503,
                            content={"ok": False, "reason": "led_unavailable"})
    # The requested colour is remembered even when switched off, so a UI can restore it
    # on the next toggle rather than coming back black.
    state.led = {"on": on, "r": red, "g": green, "b": blue}
    log.info("led on=%s rgb=(%d,%d,%d)", on, red, green, blue)
    return {"ok": True}


@app.get("/led", dependencies=[Depends(require_token)])
async def led_get() -> dict:
    return state.led or {"on": None, "r": None, "g": None, "b": None}


@app.get("/look", dependencies=[Depends(require_token)])
async def look_get() -> dict:
    return {"pan_deg": state.pan_deg, "tilt_deg": state.tilt_deg}


# --------------------------------------------------------------------------------------
# WebSocket control
# --------------------------------------------------------------------------------------

@app.websocket("/ws/drive")
async def ws_drive(ws: WebSocket) -> None:
    """
    The low-latency control path. Same safety rules, without a request per command.

    Why it exists: an HTTP client that waits for each response before sending the next
    command can only send as often as the round trip allows. On a link whose round trip
    exceeds the TTL -- which cellular does, routinely -- the watchdog fires between every
    pair of commands and the robot stutters, and no amount of tuning on the client fixes
    it, because the client cannot send faster than the network answers. One socket, with
    commands as frames, decouples send rate from round-trip time entirely.

    It also gives a better dead-man switch than the TTL: a closed socket is a released
    stick, and TCP tells us within milliseconds on a LAN rather than after 500 ms of
    silence.

    Client -> server, as often as it likes (200 ms is plenty):
        {"vx": 0.4, "vy": 0, "omega": -0.2, "seq": 41}        drive
        {"stop": true}                                         stop now
    ttl_ms is accepted and clamped, but a client should not send one.

    Server -> client:
        {"type":"ack","seq":41,"ok":true,"battery_v":7.97}
        {"type":"ack","seq":42,"ok":false,"reason":"obstacle","sonar_mm":180}
        {"type":"error","reason":"invalid_body"}               frame ignored, socket lives
        {"type":"telemetry", ...}                              once a second

    `seq` is echoed untouched so the client can measure its own round trip and show it.
    Knowing the number is most of fixing it.
    """
    token = ws.headers.get("x-robot-token")
    if TOKEN is None or not token or not secrets.compare_digest(token, TOKEN):
        # Refused before accept, so it fails the handshake rather than opening a socket
        # we then have to distrust.
        log.warning("REFUSED /ws/drive - bad or missing X-Robot-Token")
        await ws.close(code=1008)
        return

    await ws.accept()
    state.ws_clients += 1
    log.info("/ws/drive connected (%d open)", state.ws_clients)
    send_lock = asyncio.Lock()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def telemetry_frames() -> None:
        while True:
            await asyncio.sleep(WS_TELEMETRY_S)
            await telemetry_refresh()
            with contextlib.suppress(Exception):
                await send({"type": "telemetry", **(await telemetry())})

    pump = asyncio.create_task(telemetry_frames())
    try:
        while True:
            raw = await ws.receive_json()
            if not isinstance(raw, dict):
                await send({"type": "error", "reason": "invalid_body"})
                continue

            if raw.get("stop") is True:
                state.motors_live = False
                log.info("STOP requested over websocket")
                try:
                    await rpc.stop_motors()
                    state.stop_owed = False
                    await send({"type": "ack", "seq": raw.get("seq"), "ok": True})
                except (RobotUnreachable, RobotError) as exc:
                    await send({"type": "ack", "seq": raw.get("seq"), "ok": False,
                                "reason": str(exc)})
                continue

            values = [raw.get("vx", 0), raw.get("vy", 0), raw.get("omega", 0),
                      raw.get("ttl_ms", 500)]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                # A bad frame is not a reason to drop the control link -- that would turn
                # one typo into a stopped robot. Say so and keep listening.
                await send({"type": "error", "reason": "invalid_body"})
                continue

            status, payload = await apply_drive(*values)
            await send({"type": "ack", "seq": raw.get("seq"),
                        **{k: v for k, v in payload.items() if k != "ok"},
                        "ok": status == 200})
    except WebSocketDisconnect:
        log.info("/ws/drive disconnected")
    except Exception as exc:                       # noqa: BLE001 - see the stop below
        log.warning("/ws/drive error: %s: %s", type(exc).__name__, exc)
    finally:
        pump.cancel()
        state.ws_clients -= 1
        # A dropped control socket means nobody is holding the stick. Do not wait out
        # the TTL for something we already know.
        if state.motors_live or state.stop_owed:
            state.motors_live = False
            log.info("/ws/drive closed while driving - stopping")
            try:
                await rpc.stop_motors()
                state.stop_owed = False
            except (RobotUnreachable, RobotError) as exc:
                log.error("STOP ON DISCONNECT FAILED (%s) - watchdog will keep retrying",
                          exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    # httpx logs one INFO line per request. At ~3 RPC calls a second that is journald
    # noise that buries the lines which matter (refusals, watchdog fires).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
