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
import logging
import os
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Union

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
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

LOW_BATTERY_V = float(os.environ.get("TURBOPI_LOW_BATTERY_V", "7.0"))

TTL_MIN_MS, TTL_MAX_MS = 100, 2000
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
PAN_SIGN = int(os.environ.get("TURBOPI_PAN_SIGN", "-1"))
TILT_SIGN = int(os.environ.get("TURBOPI_TILT_SIGN", "-1"))
SERVO_TILT, SERVO_PAN = 1, 2     # confirmed from Functions/ColorTracking.py: servo_x = servo2
LOOK_LIMIT_DEG = 45.0
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

    async def set_servos(self, pairs: list[tuple[int, float]]) -> None:
        # SetPWMServo(use_time_ms, <ignored>, servo, angle, servo, angle, ...).
        # args[1] is skipped by the server's own argument slicing; pass 1.
        params: list[Any] = [LOOK_MOVE_MS, 1]
        for servo, angle in pairs:
            params.extend([servo, angle])
        await self.call("SetPWMServo", params)


# --------------------------------------------------------------------------------------
# Mecanum kinematics
# --------------------------------------------------------------------------------------

def wheel_duties(vx: float, vy: float, omega: float, max_duty: int = MAX_DUTY) -> dict[int, int]:
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
    if peak > 1.0:
        w1, w2, w3, w4 = w1 / peak, w2 / peak, w3 / peak, w4 / peak

    return {
        1: int(round(-w1 * max_duty)),
        2: int(round(w2 * max_duty)),
        3: int(round(-w3 * max_duty)),
        4: int(round(w4 * max_duty)),
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

    pan_deg: float = 0.0
    tilt_deg: float = 0.0

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
    scripts/patch_getrunningfunc.py points it at Running.getLoadedFunc instead. Without
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
                    "guard cannot be enforced until scripts/patch_getrunningfunc.py is applied "
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
             (watchdog_task, liveness_task, demo_probe_task, battery_poll_task)]
    log.info("gateway listening on %s:%d -> %s (max duty %d, low battery %.1f V)",
             HOST, PORT, RPC_URL, MAX_DUTY, LOW_BATTERY_V)
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
    }


def refusal(status: int, reason: str, path: str = "/drive") -> JSONResponse:
    log.warning("REFUSED %s - %s", path, reason)
    return JSONResponse(status_code=status, content={"ok": False, "reason": reason})


@app.post("/drive", dependencies=[Depends(require_token)])
async def drive(body: DriveBody) -> Any:
    vx = clamp(float(body.vx), -1.0, 1.0)
    vy = clamp(float(body.vy), -1.0, 1.0)
    omega = clamp(float(body.omega), -1.0, 1.0)
    ttl_ms = clamp(float(body.ttl_ms), TTL_MIN_MS, TTL_MAX_MS)

    if not state.alive():
        # Treat unreachable as a stop: forget that we are driving, so the watchdog does
        # not keep hammering a dead socket, and try one best-effort zero anyway.
        state.motors_live = False
        with contextlib.suppress(RobotUnreachable, RobotError):
            await rpc.stop_motors()
            state.stop_owed = False
        return refusal(503, "turbopi_unreachable")

    battery = battery_fresh()
    if battery is not None and battery < LOW_BATTERY_V:
        state.motors_live = False
        with contextlib.suppress(RobotUnreachable, RobotError):
            await rpc.stop_motors()
            state.stop_owed = False
        return refusal(409, "low_battery")

    if state.demo_probe_ok and state.demo:
        # Do not stop the motors here: the demo is legitimately driving them, and
        # yanking them out from under it would be a surprise, not a safety measure.
        return refusal(409, "demo_running")

    duties = wheel_duties(vx, vy, omega)
    try:
        await rpc.set_motor_duty(duties)
    except RobotUnreachable as exc:
        state.motors_live = False
        log.error("REFUSED /drive - robot unreachable mid-command: %s", exc)
        return JSONResponse(status_code=503,
                            content={"ok": False, "reason": "turbopi_unreachable"})
    except RobotError as exc:
        state.motors_live = False
        log.error("REFUSED /drive - robot said no: %s", exc)
        return JSONResponse(status_code=502, content={"ok": False, "reason": str(exc)})

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

    return {"ok": True, "battery_v": battery}


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
    try:
        await rpc.set_servos([(SERVO_TILT, TILT_SIGN * tilt), (SERVO_PAN, PAN_SIGN * pan)])
    except RobotUnreachable:
        return refusal(503, "turbopi_unreachable", "/look")
    except RobotError as exc:
        log.error("REFUSED /look - robot said no: %s", exc)
        return JSONResponse(status_code=502, content={"ok": False, "reason": str(exc)})
    state.pan_deg, state.tilt_deg = pan, tilt
    return {"ok": True}


@app.get("/look", dependencies=[Depends(require_token)])
async def look_get() -> dict:
    return {"pan_deg": state.pan_deg, "tilt_deg": state.tilt_deg}


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
