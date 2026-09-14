#!/usr/bin/env bash
# install_turbopi_service.sh - run TurboPi.py as a systemd service.
#
# RUN ON THE PI:
#   bash ~/install_turbopi_service.sh
#   bash ~/install_turbopi_service.sh --uninstall
#
# WHY
#   TurboPi.py runs in the foreground. Started from an SSH session it dies when
#   that session closes, when the laptop sleeps, or on any Ctrl-C - so the
#   camera and the control API vanish without warning. As a service it starts on
#   boot, survives disconnects, and restarts itself if it crashes.

set -uo pipefail

UNIT=/etc/systemd/system/turbopi.service
PYTHON=/home/pi/turbopi-venv/bin/python
APPDIR=/home/pi/TurboPi
APP="$APPDIR/TurboPi.py"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
[ -t 1 ] || { RED=""; GRN=""; YEL=""; BLD=""; RST=""; }
hdr(){ printf '\n%s=== %s ===%s\n' "$BLD" "$1" "$RST"; }
ok(){ printf '%s  OK%s    %s\n' "$GRN" "$RST" "$1"; }
bad(){ printf '%s  FAIL%s  %s\n' "$RED" "$RST" "$1"; }
warn(){ printf '%s  WARN%s  %s\n' "$YEL" "$RST" "$1"; }
info(){ printf '        %s\n' "$1"; }

if [ "${1:-}" = "--uninstall" ]; then
  hdr "Removing the service"
  sudo systemctl disable --now turbopi.service 2>/dev/null
  sudo rm -f "$UNIT"
  sudo systemctl daemon-reload
  ok "Removed. Start it by hand again with:"
  info "cd $APPDIR && $PYTHON TurboPi.py"
  exit 0
fi

hdr "1. Preflight"
for f in "$PYTHON" "$APP"; do
  if [ -x "$f" ] || [ -f "$f" ]; then ok "found $f"; else bad "missing $f"; exit 1; fi
done

# A foreground copy would hold ports 8080/9030 and the service would crash-loop.
if pgrep -af "TurboPi.py" | grep -v install_turbopi >/dev/null 2>&1; then
  warn "TurboPi.py is already running:"
  pgrep -af "TurboPi.py" | grep -v install_turbopi | sed 's/^/        /'
  info "Stopping it so the service can own the ports..."
  pkill -f "TurboPi.py" 2>/dev/null
  sleep 2
  pkill -9 -f "TurboPi.py" 2>/dev/null
  sleep 1
  ok "Stopped."
fi

hdr "2. Writing $UNIT"
sudo tee "$UNIT" >/dev/null <<UNIT_EOF
[Unit]
Description=TurboPi robot control and camera server
# The robot is useless without the network, and the MJPEG/RPC servers bind
# to all interfaces at startup, so wait for a usable address.
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=$APPDIR
ExecStart=$PYTHON $APP

# Restart on any exit. The camera or serial port can be transiently busy right
# after boot; a retry loop is more useful than a dead robot.
Restart=always
RestartSec=5

# Don't let a persistent fault hammer the hardware forever.
StartLimitIntervalSec=300
StartLimitBurst=10

# Unbuffered so logs appear in journalctl immediately rather than in blocks.
Environment=PYTHONUNBUFFERED=1

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT_EOF
ok "Unit written"

hdr "3. Enabling and starting"
sudo systemctl daemon-reload
sudo systemctl enable turbopi.service >/dev/null 2>&1 && ok "Enabled at boot"
sudo systemctl restart turbopi.service && ok "Started"

hdr "4. Verifying"
sleep 6
if systemctl is-active --quiet turbopi.service; then
  ok "Service is active"
else
  bad "Service is NOT active. Recent log:"
  sudo journalctl -u turbopi.service -n 25 --no-pager | sed 's/^/        /'
  exit 1
fi

# The real test is not "the process exists" but "the ports answer".
for port in 8080 9030; do
  if curl -s --max-time 5 -o /dev/null "http://127.0.0.1:${port}/" 2>/dev/null \
     || nc -z 127.0.0.1 "$port" 2>/dev/null; then
    ok "port ${port} is listening"
  else
    warn "port ${port} not answering yet - give it a few more seconds"
  fi
done

IP=$(hostname -I | awk '{print $1}')
hdr "DONE"
cat <<TAIL
  Camera stream : http://${IP}:8080/
  Snapshot      : http://${IP}:8080/?action=snapshot
  Control API   : http://${IP}:9030/   (JSON-RPC 2.0)

  It now starts on boot and restarts if it crashes. Closing SSH no longer
  kills it.

  Useful commands:
    sudo systemctl status turbopi      # is it healthy?
    sudo journalctl -u turbopi -f      # live logs (Ctrl-C to stop watching)
    sudo systemctl restart turbopi     # after changing code
    sudo systemctl stop turbopi        # free the camera and serial port

  NOTE: while this service runs it OWNS the camera and the serial port. To run
  a demo script or bringup.py by hand, stop the service first.
TAIL
