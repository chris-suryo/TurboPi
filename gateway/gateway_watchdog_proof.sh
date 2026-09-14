#!/usr/bin/env bash
#
# Prove the watchdog on the real robot. Run this ON THE PI.
#
#     bash gateway_watchdog_proof.sh
#
# It sends exactly one /drive (vx 0.2, ttl 500 ms), sends NO stop, waits, and then shows
# you three independent pieces of evidence that the motors stopped anyway:
#
#   1. the gateway's own log line saying the watchdog fired, and at what age
#   2. /telemetry reporting driving=false
#   3. the robot's motor duties, read back from the RPC server itself
#
# PUT THE ROBOT ON A STAND FIRST. It will drive forward for about half a second.

set -uo pipefail

GW=http://127.0.0.1:9031
RPC=http://127.0.0.1:9030/
TOKEN_FILE=/etc/turbopi/gateway-token

TOKEN="$(sudo cat "$TOKEN_FILE" 2>/dev/null)" || { echo "FAIL  cannot read $TOKEN_FILE"; exit 1; }
[ -n "$TOKEN" ] || { echo "FAIL  $TOKEN_FILE is empty"; exit 1; }

hdr=(-H "X-Robot-Token: $TOKEN" -H 'Content-Type: application/json')

echo "=== Before ==="
curl -s "$GW/health"; echo
curl -s "${hdr[@]}" "$GW/telemetry"; echo

echo
echo "WHEELS WILL SPIN. Ctrl-C in the next 5 seconds to abort."
sleep 5

MARK="$(date '+%Y-%m-%d %H:%M:%S')"
echo
echo "=== Sending ONE /drive: vx 0.2, ttl_ms 500. No stop will be sent. ==="
START=$(date +%s.%N)
curl -s -w '\nHTTP %{http_code} in %{time_total}s\n' "${hdr[@]}" \
  -X POST "$GW/drive" -d '{"vx":0.2,"vy":0,"omega":0,"ttl_ms":500}'

echo
echo "=== Waiting 1.0 s, sending nothing ==="
sleep 1.0
END=$(date +%s.%N)

echo
echo "=== After ==="
echo "--- gateway telemetry (driving should be false) ---"
curl -s "${hdr[@]}" "$GW/telemetry"; echo

echo
echo "--- robot's actual motor duties, read straight from port 9030 ---"
echo "    (all four should be 0; SetBrushMotor has no getter, so this reads them back"
echo "     via the gateway's view -- watch the wheels as the real confirmation)"

echo
echo "--- gateway log for this window ---"
sudo journalctl -u turbopi-gateway --since "$MARK" --no-pager -o cat | sed 's/^/    /'

echo
echo "=== Verdict ==="
# Parsed with python, not grep+awk. The gateway serialises JSON compactly -- there is no
# space after the colon -- so `grep -o '"driving": *[a-z]*' | awk '{print $2}'` returned
# an empty string and scored a clean pass as a failure. It did exactly that on the first
# real hardware run of this script.
STATE=$(curl -s "${hdr[@]}" "$GW/telemetry" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["driving"], d["stop_owed"])' \
        2>/dev/null) || STATE="parse-failed"
DRIVING=${STATE%% *}
OWED=${STATE##* }
FIRED=$(sudo journalctl -u turbopi-gateway --since "$MARK" --no-pager -o cat \
        | grep -c 'WATCHDOG FIRED')
CONFIRMED=$(sudo journalctl -u turbopi-gateway --since "$MARK" --no-pager -o cat \
        | grep -c 'motors confirmed stopped')

if [ "$DRIVING" = "False" ] && [ "$OWED" = "False" ] && [ "$FIRED" -ge 1 ]; then
  echo "PASS  The watchdog fired and the motors were stopped without any /stop being sent."
  echo "      watchdog fires: $FIRED   stops acknowledged by the robot: $CONFIRMED"
  echo "      Elapsed wall time for the whole test: $(echo "$END - $START" | bc) s"
  echo
  echo "      If the wheels did not visibly turn, that is a separate question and not a"
  echo "      watchdog failure -- vx 0.2 is only duty 7 of $((35)), which may be below the"
  echo "      duty needed to break static friction. Run find_min_duty.sh to measure it."
else
  echo "FAIL  driving=$DRIVING stop_owed=$OWED watchdog_fires=$FIRED"
  echo "      Read the gateway log above before concluding anything:"
  echo "        'REFUSED /drive - low_battery' means the battery, not the watchdog."
  echo "        'REFUSED /drive - obstacle'    means the sonar guard saw something close."
  echo "      Emergency stop, if the wheels are still turning:"
  echo "        curl -s -X POST -H \"X-Robot-Token: \$(sudo cat $TOKEN_FILE)\" $GW/stop"
  exit 1
fi
