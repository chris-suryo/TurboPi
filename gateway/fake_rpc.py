#!/usr/bin/env python3
"""
A stand-in for the robot's JSON-RPC server on port 9030.

Deliberately reproduces the real server's quirks, because the quirks are what the
gateway has to survive. Everything here was read out of RPCServer.py, Running.py and
ros_robot_controller_sdk.py rather than imagined:

  * the 3-element [success, data, method] envelope, and the 2-element [success, error]
    that some failure paths return instead
  * echo, which is a bare lambda and returns no envelope at all
  * GetRunningFunc, which passes a string where runbymainth wants a callable and so
    always fails with "E05 - Not callable"
  * StopFunc with no demo loaded, which raises inside the main-thread queue, never sets
    a result, and returns "E04 - Operation timeout!" after a full two seconds
  * GetBatteryVoltage returning raw millivolts, or None when the queue is empty
  * SetPWMServo's angle->pulse map: +90 -> 500 us, -90 -> 2500 us

Control it over HTTP: POST /__control {"battery_mv": 7968, "demo": 3, ...}
Read what it received:  GET /__calls
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E01 = "E01 - Invalid number of parameter!"
E02 = "E02 - Invalid parameter!"
E04 = "E04 - Operation timeout!"
E05 = "E05 - Not callable"


class Robot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.battery_mv: int | None = 7968
        self.sonar_mm: int = 412
        self.demo: int = 0
        self.getrunningfunc_patched = False   # default: the vendor bug
        self.fail_methods: list[str] = []     # force these to return an envelope failure
        self.slow_methods: dict = {}          # method -> seconds to stall before answering
        self.duties: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
        self.servo_pulses: dict[int, int] = {}
        self.calls: list[dict] = []

    def record(self, method: str, params) -> None:
        with self.lock:
            self.calls.append({"t": time.monotonic(), "method": method, "params": params})


robot = Robot()


def vendor_map(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def handle(method: str, params: list):
    robot.record(method, params)

    if method in robot.slow_methods:
        time.sleep(float(robot.slow_methods[method]))

    if method in robot.fail_methods:
        return [False, "E03 - Operation failed!", method]

    if method == "echo":
        return params[0] if params else None

    if method == "SetBrushMotor":
        if len(params) % 2 != 0:
            return [False, E01, "SetBrushMotor"]
        motors, speeds = params[0::2], params[1::2]
        for m in motors:
            if m < 1 or m > 4:
                return [False, E02, "SetBrushMotor"]
        with robot.lock:
            for m, s in zip(motors, speeds):
                robot.duties[m] = s
        return [True, [], "SetBrushMotor"]

    if method == "GetBatteryVoltage":
        return [True, robot.battery_mv, "GetBatteryVoltage"]

    if method == "GetSonarDistance":
        return [True, robot.sonar_mm, "GetSonarDistance"]

    if method == "GetRunningFunc":
        if not robot.getrunningfunc_patched:
            return [False, E05]              # note: two elements, not three
        # runbymainth returns ret[2] verbatim: (True, (RunningFunc,)) -- two elements.
        return [True, [robot.demo]]

    if method == "StopFunc":
        if robot.demo == 0:
            time.sleep(2.0)                  # the real 2 s main-thread timeout
            return [False, E04]
        return [True, [robot.demo], "StopFunc"]

    if method == "SetPWMServo":
        try:
            servos, angles = params[2::2], params[3::2]
            with robot.lock:
                for s, a in zip(servos, angles):
                    robot.servo_pulses[s] = int(vendor_map(a, 90, -90, 500, 2500))
        except Exception:
            return [False, "E03 - Operation failed!", "SetPWMServo"]
        return [True, [], "SetPWMServo"]

    return [False, "E05 - Not callable"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/__calls":
            with robot.lock:
                self._send({"calls": robot.calls, "duties": robot.duties,
                            "servo_pulses": robot.servo_pulses})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)

        if self.path == "/__control":
            settings = json.loads(raw or b"{}")
            for key, value in settings.items():
                if key == "reset_calls":
                    with robot.lock:
                        robot.calls.clear()
                else:
                    setattr(robot, key, value)
            self._send({"ok": True})
            return

        request = json.loads(raw)
        result = handle(request.get("method", ""), request.get("params", []))
        self._send({"jsonrpc": "2.0", "result": result, "id": request.get("id")})


def serve(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 9030)
    threading.Event().wait()
