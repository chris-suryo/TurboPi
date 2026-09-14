#!/usr/bin/env bash
#
# Find the lowest stick deflection that actually turns the wheels. Run ON THE PI.
#
#     bash find_min_duty.sh
#
# Why this exists: the first hardware run of the watchdog proof drove at vx 0.2, which is
# motor duty 7, and the wheels did not turn -- they hummed. That is static friction, not a
# fault. Every motor has a duty below which it buzzes and stalls instead of turning, and
# nobody knows where that threshold is on this robot. It matters because the drive UI's
# "Slow" mode is 0.4, and if the real threshold is above that, Slow is a dead stick.
#
# Pass "fine" to sweep duty one unit at a time through a narrow band, once the coarse
# run has told you roughly where the threshold is:
#
#     bash find_min_duty.sh fine 16 28
#
# WHEELS OFF THE GROUND. Each step spins them for about a second.

set -uo pipefail

GW=http://127.0.0.1:9031
TOKEN_FILE=/etc/turbopi/gateway-token
MAX_DUTY=35          # must match TURBOPI_MAX_DUTY in the service; 35 is the default

TOKEN="$(sudo cat "$TOKEN_FILE" 2>/dev/null)" || { echo "FAIL  cannot read $TOKEN_FILE"; exit 1; }
[ -n "$TOKEN" ] || { echo "FAIL  $TOKEN_FILE is empty"; exit 1; }
hdr=(-H "X-Robot-Token: $TOKEN" -H 'Content-Type: application/json')

stop() { curl -s -o /dev/null "${hdr[@]}" -X POST "$GW/stop"; }
trap 'echo; echo "interrupted - stopping"; stop; exit 130' INT

echo "Battery before:"
curl -s "$GW/health"; echo
echo
echo "WHEELS OFF THE GROUND. Each step runs for ~1 s. Ctrl-C aborts and stops."
echo "Watch the wheels and note the first step where they actually TURN,"
echo "rather than buzz or twitch."
sleep 5

MODE=${1:-coarse}
if [ "$MODE" = "fine" ]; then
  LO=${2:-16}; HI=${3:-28}
  # Ask for each whole duty value directly: vx = duty / MAX_DUTY.
  STEPS=$(python3 -c "print(' '.join(f'{d/$MAX_DUTY:.4f}' for d in range($LO, $HI+1)))")
  echo "Fine sweep: duty $LO to $HI, one at a time."
else
  STEPS="0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.50 0.60 0.80 1.00"
fi

for vx in $STEPS; do
  duty=$(python3 -c "print(round($vx * $MAX_DUTY))")
  printf '\n--- vx %-5s  duty %-3s  ' "$vx" "$duty"
  body=$(curl -s "${hdr[@]}" -X POST "$GW/drive" \
         -d "{\"vx\":$vx,\"vy\":0,\"omega\":0,\"ttl_ms\":800}")
  case "$body" in
    *'"ok":true'*) echo "sent" ;;
    *low_battery*) echo "REFUSED - battery too low. Charge and re-run."; stop; exit 1 ;;
    *obstacle*)    echo "REFUSED - sonar sees something ahead. Clear it and re-run."; stop; exit 1 ;;
    *)             echo "REFUSED - $body"; stop; exit 1 ;;
  esac
  sleep 1.4          # 800 ms of drive, then the watchdog stops it, then a pause
done

stop
echo
echo "Battery after:"
curl -s "$GW/health"; echo
echo
echo "Report the first DUTY where the wheels actually turned. That number sets three things:"
echo "  - the drive UI's Slow mode (currently 0.4)"
echo "  - whether TURBOPI_MAX_DUTY should go up from $MAX_DUTY"
echo "  - the floor below which the UI should send zero rather than a stalling hum,"
echo "    because a stalled motor draws full current and heats up"
