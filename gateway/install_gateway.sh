#!/usr/bin/env bash
#
# Install the TurboPi safety gateway as a systemd service.
#
# Copy the whole gateway/ directory to the Pi and run this from inside it. Everything
# the installer needs is in that one directory -- that is deliberate, so there is no
# "and also fetch this other file from somewhere else" step:
#
#     scp -r gateway pi@10.0.0.3:~/turbopi-gateway      # from your PC
#     ssh pi@10.0.0.3
#     cd ~/turbopi-gateway && bash install_gateway.sh
#
# What it installs, so you can keep your list accurate:
#   /home/pi/gateway-venv/           a venv holding fastapi, uvicorn, httpx, pydantic
#   /home/pi/robot_gateway.py        the service itself
#   /home/pi/turbopi-stop-motors.sh  systemd's last-resort motor stop
#   /etc/turbopi/gateway-token       the shared secret, 0600, owned by pi
#   /etc/systemd/system/turbopi-gateway.service
#
# Nothing from the robot's own software is touched, and nothing is installed
# system-wide: the venv keeps these four packages away from the robot's interpreter.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV=/home/pi/gateway-venv
TOKEN_FILE=/etc/turbopi/gateway-token
UNIT=/etc/systemd/system/turbopi-gateway.service

fail() { echo "FAIL  $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -un)" = "pi" ] || fail "run this as the pi user (the service runs as pi)"

for f in robot_gateway.py turbopi-gateway.service turbopi-stop-motors.sh requirements.txt \
         patch_getrunningfunc.py; do
  [ -f "$SRC_DIR/$f" ] || fail "missing $f next to this script -- copy the whole gateway/ directory over"
done

step "Checking the robot's RPC server is up"
if curl -s -m 3 -o /dev/null -X POST http://127.0.0.1:9030/ \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","method":"echo","params":["ping"],"id":1}'; then
  echo "    port 9030 answered"
else
  echo "    WARNING: port 9030 did not answer. The gateway will install and start anyway,"
  echo "    and will report turbopi=false on /health until TurboPi.py is running."
fi

step "Creating the venv at $VENV"
# Checked separately so the remedy is one obvious line rather than buried in ensurepip's
# traceback. Raspberry Pi OS usually ships python3-venv, but a minimal image may not.
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then
  fail "python3-venv is missing. Run this, then re-run the installer:
        sudo apt update && sudo apt install -y python3-venv"
fi
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
else
  echo "    already exists, reusing"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$SRC_DIR/requirements.txt"
"$VENV/bin/python" -c 'import fastapi, uvicorn, httpx, pydantic; print("    deps ok:",
    "fastapi", fastapi.__version__, "| httpx", httpx.__version__, "| pydantic", pydantic.VERSION)'

step "Installing the service files"
install -m 0755 "$SRC_DIR/robot_gateway.py"       /home/pi/robot_gateway.py
install -m 0755 "$SRC_DIR/turbopi-stop-motors.sh" /home/pi/turbopi-stop-motors.sh
echo "    /home/pi/robot_gateway.py"
echo "    /home/pi/turbopi-stop-motors.sh"

step "Shared secret"
sudo mkdir -p /etc/turbopi
if sudo test -s "$TOKEN_FILE"; then
  echo "    $TOKEN_FILE already exists -- leaving it alone"
else
  "$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))' \
    | sudo tee "$TOKEN_FILE" >/dev/null
  echo "    generated a new secret"
fi
sudo chown pi:pi "$TOKEN_FILE"
sudo chmod 0600 "$TOKEN_FILE"

step "Patching the robot's GetRunningFunc"
# Not optional: unpatched, it fails with E05 every time and the gateway cannot tell
# whether a built-in demo is driving, so the demo_running guard stays off. Doing it here
# means one fewer command to remember, and the gateway restart below picks it up.
if python3 "$SRC_DIR/patch_getrunningfunc.py"; then
  echo "    restarting TurboPi.py so it takes effect"
  sudo systemctl restart turbopi || echo "    WARNING: could not restart turbopi"
else
  echo "    WARNING: the patch did not apply. The gateway will run, but the"
  echo "    'do not drive while a demo is running' guard will be OFF and will say so."
fi

step "Installing the systemd unit"
sudo install -m 0644 "$SRC_DIR/turbopi-gateway.service" "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable turbopi-gateway.service >/dev/null
sudo systemctl restart turbopi-gateway.service

step "Waiting for the gateway to answer"
for _ in $(seq 1 20); do
  if curl -s -m 2 -o /dev/null http://127.0.0.1:9031/health; then break; fi
  sleep 0.5
done

echo
echo "================================================================"
if curl -s -m 3 http://127.0.0.1:9031/health; then
  echo
  echo
  echo "Gateway is up on port 9031, and will come back on boot."
else
  echo "Gateway did NOT answer /health. Look at:"
  echo "    sudo systemctl status turbopi-gateway"
  echo "    journalctl -u turbopi-gateway -n 50 --no-pager"
  exit 1
fi
echo "================================================================"
echo
echo "The shared secret -- put this in kona-tracker's .env:"
echo
echo "    ROBOT_TOKEN=$(sudo cat "$TOKEN_FILE")"
echo
echo "Test it from this Pi:"
echo "    curl -s http://127.0.0.1:9031/health"
echo "    curl -s -H \"X-Robot-Token: \$(sudo cat $TOKEN_FILE)\" http://127.0.0.1:9031/telemetry"
echo
echo "Logs:   journalctl -u turbopi-gateway -f"
