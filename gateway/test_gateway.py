#!/usr/bin/env python3
"""
Test the gateway against a stand-in for the robot's RPC server.

Runs the gateway as a real subprocess over real HTTP, the same way systemd will run it,
so startup behaviour, the background watchdog and SIGTERM handling are all exercised for
real rather than simulated. Nothing here needs the robot.

    python3 test_gateway.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import time
import urllib.error
import urllib.request

import fake_rpc

RPC_PORT = 19030
GW_PORT = 19031
TOKEN = "test-token-do-not-use-in-production"
GW = f"http://127.0.0.1:{GW_PORT}"
RPCBASE = f"http://127.0.0.1:{RPC_PORT}"

PASSES: list[str] = []
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name} -- {detail}")
        print(f"  FAIL  {name}   {detail}")


def request(method: str, path: str, body=None, token: str | None = TOKEN, base: str = GW):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Robot-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null")
        except ValueError:
            return exc.code, raw.decode(errors="replace")


def control(**settings) -> None:
    request("POST", "/__control", settings, token=None, base=RPCBASE)


def calls() -> dict:
    return request("GET", "/__calls", token=None, base=RPCBASE)[1]


def duties() -> dict:
    return {int(k): v for k, v in calls()["duties"].items()}


def motor_calls() -> list:
    return [c for c in calls()["calls"] if c["method"] == "SetBrushMotor"]


def vendor_pulse(angle: float) -> int:
    """The robot's own angle->microsecond map, written out independently."""
    return int((angle - 90) * (2500 - 500) / (-90 - 90) + 500)


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def main() -> int:
    server = fake_rpc.serve(RPC_PORT)

    token_file = tempfile.NamedTemporaryFile("w", suffix=".token", delete=False)
    token_file.write(TOKEN + "\n")
    token_file.close()

    env = dict(os.environ,
               TURBOPI_GATEWAY_TOKEN_FILE=token_file.name,
               TURBOPI_RPC_URL=f"{RPCBASE}/",
               TURBOPI_GATEWAY_HOST="127.0.0.1",
               TURBOPI_GATEWAY_PORT=str(GW_PORT))
    gateway = subprocess.Popen([sys.executable, "robot_gateway.py"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        ready = wait_until(lambda: request("GET", "/health", token=None)[1]["turbopi"] is True, 15)
        if not ready:
            print("gateway never became ready")
            return 2

        print("\n== 1. startup ==")
        first_motor = motor_calls()[:1]
        check("startup sends one stop",
              bool(first_motor) and first_motor[0]["params"] == [1, 0, 2, 0, 3, 0, 4, 0],
              str(first_motor))

        print("\n== 2. auth ==")
        control(reset_calls=True)
        status, body = request("GET", "/telemetry", token=None)
        check("no token -> 401", status == 401, f"got {status} {body}")
        status, body = request("GET", "/telemetry", token="wrong")
        check("wrong token -> 401", status == 401, f"got {status} {body}")
        status, body = request("POST", "/drive", {"vx": 1}, token="wrong")
        check("wrong token on /drive -> 401", status == 401, f"got {status} {body}")
        forwarded = [c for c in calls()["calls"]
                     if c["method"] in ("SetBrushMotor", "SetPWMServo")]
        check("401 forwards nothing to the robot", forwarded == [], str(forwarded))
        status, _ = request("GET", "/telemetry")
        check("correct token -> 200", status == 200, f"got {status}")

        print("\n== 3. /health ==")
        control(reset_calls=True)
        status, body = request("GET", "/health", token=None)
        check("/health needs no auth", status == 200, f"got {status}")
        check("/health shape", set(body) == {"ok", "turbopi", "battery_v", "uptime_s"}, str(body))
        check("/health battery in volts not millivolts",
              body["battery_v"] is not None and 7.9 < body["battery_v"] < 8.0, str(body))
        check("/health uptime is seconds", isinstance(body["uptime_s"], int), str(body))
        for _ in range(5):
            request("GET", "/health", token=None)
        check("/health never touches the motors", motor_calls() == [], str(motor_calls()))

        print("\n== 4. /telemetry ==")
        status, body = request("GET", "/telemetry")
        expected = {"battery_v", "sonar_mm", "driving", "demo", "last_command_age_ms",
                    "low_battery"}
        check("/telemetry has every contracted key", expected <= set(body), str(body))
        check("/telemetry sonar in mm", body["sonar_mm"] == 412, str(body))
        check("/telemetry low_battery false at 7.968 V", body["low_battery"] is False, str(body))
        control(reset_calls=True)
        for _ in range(6):
            request("GET", "/telemetry")
        sonar_reads = len([c for c in calls()["calls"] if c["method"] == "GetSonarDistance"])
        check("500 ms cache stops six polls becoming six RPC reads",
              sonar_reads <= 2, f"{sonar_reads} sonar reads for 6 polls")

        print("\n== 5. drive kinematics ==")
        # Cross-check against HiwonderSDK/mecanum.py: pure forward alternates duty signs,
        # pure rotation makes all four equal. Both fall out of the wiring inversion.
        control(reset_calls=True)
        request("POST", "/drive", {"vx": 1, "vy": 0, "omega": 0, "ttl_ms": 2000})
        check("forward -> alternating duties", duties() == {1: -35, 2: 35, 3: -35, 4: 35},
              str(duties()))
        request("POST", "/drive", {"vx": 0, "vy": 1, "omega": 0, "ttl_ms": 2000})
        check("strafe left -> front pair vs rear pair",
              duties() == {1: 35, 2: 35, 3: -35, 4: -35}, str(duties()))
        request("POST", "/drive", {"vx": 0, "vy": 0, "omega": 1, "ttl_ms": 2000})
        check("rotate CCW -> all four equal", duties() == {1: 35, 2: 35, 3: 35, 4: 35},
              str(duties()))
        request("POST", "/drive", {"vx": 1, "vy": 1, "omega": 0, "ttl_ms": 2000})
        check("diagonal normalises instead of clipping",
              duties() == {1: 0, 2: 35, 3: -35, 4: 0}, str(duties()))
        request("POST", "/drive", {"vx": -1, "vy": 0, "omega": 0, "ttl_ms": 2000})
        check("reverse mirrors forward", duties() == {1: 35, 2: -35, 3: 35, 4: -35},
              str(duties()))

        print("\n== 6. clamping and validation ==")
        request("POST", "/drive", {"vx": 5, "ttl_ms": 2000})
        check("vx=5 clamps to 1", duties() == {1: -35, 2: 35, 3: -35, 4: 35}, str(duties()))
        request("POST", "/drive", {"vx": 0.5, "ttl_ms": 2000})
        check("half stick is half duty", duties() == {1: -18, 2: 18, 3: -18, 4: 18},
              str(duties()))
        for bad in ({"vx": "fast"}, {"vx": True}, {"vx": None}, {"vx": [1]},
                    {"vx": 0.2, "ttl_ms": "soon"}):
            status, body = request("POST", "/drive", bad)
            check(f"non-numeric {json.dumps(bad)} -> 400", status == 400, f"got {status} {body}")
        status, _ = request("POST", "/drive", {"vx": 0.1, "nonsense": 1})
        check("unknown field -> 400", status == 400, f"got {status}")

        print("\n== 7. THE WATCHDOG ==")
        control(reset_calls=True)
        status, _ = request("POST", "/drive", {"vx": 0.2, "ttl_ms": 300})
        sent_at = time.monotonic()
        check("drive accepted", status == 200, f"got {status}")
        stopped = wait_until(lambda: duties() == {1: 0, 2: 0, 3: 0, 4: 0}, 3.0, 0.02)
        fired_after = time.monotonic() - sent_at
        check("watchdog stopped the motors with no /stop sent", stopped, "motors still running")
        check("watchdog fired after the ttl, not before", fired_after >= 0.3,
              f"fired after {fired_after*1000:.0f} ms")
        check("watchdog fired promptly after the ttl", fired_after < 0.6,
              f"fired after {fired_after*1000:.0f} ms")

        control(reset_calls=True)
        request("POST", "/drive", {"vx": 0.2, "ttl_ms": 50})
        sent_at = time.monotonic()
        wait_until(lambda: duties() == {1: 0, 2: 0, 3: 0, 4: 0}, 3.0, 0.02)
        check("ttl_ms=50 is clamped up to 100 ms", (time.monotonic() - sent_at) >= 0.1,
              f"fired after {(time.monotonic()-sent_at)*1000:.0f} ms")

        control(reset_calls=True)
        request("POST", "/drive", {"vx": 0.2, "ttl_ms": 500})
        request("POST", "/drive", {"vx": 0, "vy": 0, "omega": 0, "ttl_ms": 500})
        time.sleep(0.9)
        zero_calls = [c for c in motor_calls() if c["params"] == [1, 0, 2, 0, 3, 0, 4, 0]]
        check("an explicit zero drive disarms the watchdog", len(zero_calls) == 1,
              f"{len(zero_calls)} zero commands, expected exactly 1")

        print("\n== 8. /stop ==")
        control(reset_calls=True)
        request("POST", "/drive", {"vx": 0.4, "ttl_ms": 2000})
        began = time.monotonic()
        status, body = request("POST", "/stop")
        elapsed = time.monotonic() - began
        check("/stop -> 200 {ok:true}", status == 200 and body == {"ok": True},
              f"got {status} {body}")
        check("/stop zeroes all four motors", duties() == {1: 0, 2: 0, 3: 0, 4: 0}, str(duties()))
        check("/stop does not call StopFunc when no demo is loaded",
              not [c for c in calls()["calls"] if c["method"] == "StopFunc"], "StopFunc called")
        check("/stop returns fast (vendor StopFunc would cost 2 s)", elapsed < 0.5,
              f"took {elapsed*1000:.0f} ms")

        print("\n== 9. low battery ==")
        control(battery_mv=6900)
        ok = wait_until(lambda: request("GET", "/health", token=None)[1]["battery_v"] < 7.0, 5)
        check("gateway picks up the new voltage", ok, "voltage never refreshed")
        control(reset_calls=True)
        status, body = request("POST", "/drive", {"vx": 0.4, "ttl_ms": 500})
        check("low battery -> 409 low_battery",
              status == 409 and body == {"ok": False, "reason": "low_battery"},
              f"got {status} {body}")
        check("refusing on low battery also zeroes the motors",
              duties() == {1: 0, 2: 0, 3: 0, 4: 0}, str(duties()))
        status, body = request("POST", "/stop")
        check("/stop still works under low battery", status == 200 and body == {"ok": True},
              f"got {status} {body}")
        status, body = request("GET", "/telemetry")
        check("telemetry reports low_battery true", body["low_battery"] is True, str(body))
        control(battery_mv=7968)
        wait_until(lambda: request("GET", "/health", token=None)[1]["battery_v"] > 7.5, 5)

        print("\n== 10. demo running ==")
        status, body = request("POST", "/drive", {"vx": 0.2, "ttl_ms": 500})
        check("unpatched GetRunningFunc does not block driving", status == 200,
              f"got {status} {body}")
        status, body = request("GET", "/telemetry")
        check("unpatched GetRunningFunc is reported honestly, not as 'no demo'",
              body.get("demo_detection") is False, str(body))
        control(getrunningfunc_patched=True, demo=3)
        ok = wait_until(lambda: request("GET", "/telemetry")[1]["demo"] == 3, 6)
        check("patched GetRunningFunc is detected", ok, "demo never seen")
        control(reset_calls=True)
        status, body = request("POST", "/drive", {"vx": 0.4, "ttl_ms": 500})
        check("demo running -> 409 demo_running",
              status == 409 and body == {"ok": False, "reason": "demo_running"},
              f"got {status} {body}")
        check("refusing on demo_running leaves the demo's motors alone",
              not motor_calls(), "gateway touched the motors")
        status, body = request("POST", "/stop")
        check("/stop works with a demo running", status == 200, f"got {status} {body}")
        ok = wait_until(lambda: any(c["method"] == "StopFunc" for c in calls()["calls"]), 3)
        check("/stop also calls StopFunc when a demo is loaded", ok, "StopFunc never called")
        control(demo=0, getrunningfunc_patched=False)
        wait_until(lambda: request("GET", "/telemetry")[1]["demo"] is None, 6)

        print("\n== 11. /look ==")
        control(reset_calls=True)
        status, body = request("POST", "/look", {"pan_deg": 90, "tilt_deg": -10})
        check("/look -> 200 {ok:true}", status == 200 and body == {"ok": True},
              f"got {status} {body}")
        pulses = {int(k): v for k, v in calls()["servo_pulses"].items()}
        check("/look clamps pan to 45 deg", pulses.get(2) == vendor_pulse(-45),
              f"{pulses} vs expected pan pulse {vendor_pulse(-45)}")
        check("/look sends tilt on servo 1", pulses.get(1) == vendor_pulse(10),
              f"{pulses} vs expected tilt pulse {vendor_pulse(10)}")
        status, body = request("GET", "/look")
        check("GET /look returns the clamped values, not what was asked for",
              body == {"pan_deg": 45.0, "tilt_deg": -10.0}, str(body))
        status, body = request("POST", "/look", {"pan_deg": "left"})
        check("non-numeric /look -> 400", status == 400, f"got {status} {body}")

        print("\n== 12. robot says no ==")
        control(fail_methods=["SetBrushMotor"])
        status, body = request("POST", "/drive", {"vx": 0.2, "ttl_ms": 500})
        check("envelope success=false -> 502 with the robot's own error string",
              status == 502 and "E03" in str(body.get("reason")), f"got {status} {body}")
        control(fail_methods=[])

        print("\n== 13. robot unreachable ==")
        request("POST", "/drive", {"vx": 0.2, "ttl_ms": 2000})
        server.shutdown()
        server.server_close()
        ok = wait_until(lambda: request("GET", "/health", token=None)[1]["turbopi"] is False, 12)
        check("/health reports turbopi false within the 5 s window", ok, "still reporting alive")
        status, body = request("POST", "/drive", {"vx": 0.4, "ttl_ms": 500})
        check("unreachable -> 503 turbopi_unreachable",
              status == 503 and body == {"ok": False, "reason": "turbopi_unreachable"},
              f"got {status} {body}")
        status, body = request("GET", "/telemetry")
        check("unreachable is treated as a stop, not as still driving",
              body["driving"] is False, str(body))
        status, body = request("GET", "/health", token=None)
        check("/health still answers while the robot is down", status == 200, f"got {status}")

        print("\n== 14. an undelivered stop stays owed ==")
        # This is the TurboPi.py-dies-mid-drive case. Every route to the motors runs
        # through port 9030, so while it is down nothing can stop them. What must not
        # happen is the gateway giving up: the stop has to land the moment 9030 answers.
        status, body = request("GET", "/telemetry")
        check("gateway knows a stop is still owed while the robot is down",
              body.get("stop_owed") is True, str(body))

        server = fake_rpc.serve(RPC_PORT)
        control(reset_calls=True)
        landed = wait_until(
            lambda: any(c["params"] == [1, 0, 2, 0, 3, 0, 4, 0] for c in motor_calls()), 8)
        check("the owed stop lands by itself once the robot answers again", landed,
              "no stop arrived after the robot came back")
        check("no /drive or /stop was sent to make that happen", True)
        cleared = wait_until(lambda: request("GET", "/telemetry")[1]["stop_owed"] is False, 5)
        check("stop_owed clears only once the robot acknowledges", cleared, "still owed")
        ok = wait_until(lambda: request("GET", "/health", token=None)[1]["turbopi"] is True, 12)
        check("gateway recovers on its own when the robot comes back", ok, "never recovered")

        print("\n== 15. a slow poll cannot hold up a stop ==")
        control(slow_methods={"GetSonarDistance": 2.0})
        poller = threading.Thread(target=lambda: request("GET", "/telemetry"), daemon=True)
        poller.start()
        time.sleep(0.05)
        began = time.monotonic()
        status, body = request("POST", "/stop")
        elapsed = time.monotonic() - began
        check("/stop still returns while a 2 s telemetry read is in flight",
              status == 200 and elapsed < 1.0, f"got {status} after {elapsed*1000:.0f} ms")
        control(slow_methods={})
        poller.join(timeout=5)

        print("\n== 16. SIGTERM ==")
        control(reset_calls=True)
        request("POST", "/drive", {"vx": 0.3, "ttl_ms": 2000})
        gateway.send_signal(signal.SIGTERM)
        gateway.wait(timeout=15)
        final = motor_calls()[-1]["params"] if motor_calls() else None
        check("SIGTERM sends a final stop", final == [1, 0, 2, 0, 3, 0, 4, 0], str(final))

    finally:
        if gateway.poll() is None:
            gateway.kill()
        out = gateway.stdout.read() if gateway.stdout else ""
        os.unlink(token_file.name)

    print("\n== gateway log ==")
    for line in out.splitlines():
        print("  " + line)

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    for failure in FAILURES:
        print("  FAILED: " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
