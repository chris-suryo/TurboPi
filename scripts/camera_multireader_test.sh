#!/usr/bin/env bash
# camera_multireader_test.sh - can the robot's camera be read from another
# machine, and by more than one reader at once?
#
# RUN THIS FROM THE MACHINE THAT WILL CONSUME THE STREAM (the Mac, or the
# Windows PC running the app) - NOT on the robot. The whole point is to prove
# the picture can leave the robot.
#
#   ./camera_multireader_test.sh 10.0.0.3
#
# Requires only curl. TurboPi.py must be running on the robot.

set -uo pipefail
ROBOT="${1:-10.0.0.3}"
PORT=8080
URL="http://${ROBOT}:${PORT}/"
SNAP="http://${ROBOT}:${PORT}/?action=snapshot"
SECS="${2:-10}"

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
[ -t 1 ] || { RED=""; GRN=""; YEL=""; BLD=""; RST=""; }
hdr(){ printf '\n%s=== %s ===%s\n' "$BLD" "$1" "$RST"; }
ok(){ printf '%s  PASS%s  %s\n' "$GRN" "$RST" "$1"; }
bad(){ printf '%s  FAIL%s  %s\n' "$RED" "$RST" "$1"; }
warn(){ printf '%s  WARN%s  %s\n' "$YEL" "$RST" "$1"; }
info(){ printf '        %s\n' "$1"; }

echo "Robot: ${ROBOT}:${PORT}   sample window: ${SECS}s"

# --- 1. reachability -------------------------------------------------------
hdr "1. Is the stream reachable from this machine?"
if curl -s --max-time 5 -o /dev/null -w '%{http_code}' "$SNAP" | grep -q 200; then
  ok "HTTP 200 from ${SNAP}"
else
  bad "No HTTP 200 from ${SNAP}"
  info "Is TurboPi.py running on the robot? Is the IP right?"
  info "Try:  ping ${ROBOT}"
  exit 1
fi

# --- 2. is it really a JPEG ------------------------------------------------
hdr "2. Is the snapshot a real JPEG?"
TMP=$(mktemp); curl -s --max-time 8 "$SNAP" -o "$TMP"
SIZE=$(wc -c < "$TMP" | tr -d ' ')
MAGIC=$(od -An -tx1 -N2 "$TMP" | tr -d ' \n')
if [ "$MAGIC" = "ffd8" ]; then
  ok "Valid JPEG, ${SIZE} bytes (magic ffd8)"
else
  bad "Not a JPEG - first bytes were '${MAGIC}', expected ffd8"
fi
rm -f "$TMP"

# --- 3. single reader frame rate -------------------------------------------
# Each MJPEG frame is delimited by the boundary string, so counting the
# boundary counts the frames. grep -a treats the binary stream as text.
# curl exits non-zero when --max-time expires (that is the normal end of a
# sampling window), and `set -o pipefail` propagates it. grep -c also exits 1
# on zero matches while still printing "0". So the exit status is meaningless
# here - take grep's stdout and ignore the status entirely.
count_frames() {
  local n
  n=$( { curl -s --max-time "$2" "$1" 2>/dev/null || true; } \
        | grep -ac 'boundarydonotcross' || true )
  n=${n//[^0-9]/}
  printf '%s' "${n:-0}"
}

hdr "3. Single reader - frame rate over ${SECS}s"
N1=$(count_frames "$URL" "$SECS")
FPS1=$(awk -v n="$N1" -v s="$SECS" 'BEGIN{printf "%.1f", n/s}')
if [ "$N1" -gt 0 ]; then
  ok "${N1} frames in ${SECS}s  =  ${FPS1} fps"
  info "The server sleeps 50ms per frame, so ~20 fps is the ceiling."
else
  bad "Zero frames. The stream opened but delivered nothing."
  info "This is the 'black frames' failure - the camera is held elsewhere."
  exit 1
fi

# --- 4. THE TEST THAT MATTERS: two readers at once -------------------------
hdr "4. TWO readers at once - the question that decides everything"
info "Starting reader A, then reader B 2s later. Both run ${SECS}s."
A_OUT=$(mktemp); B_OUT=$(mktemp)
( count_frames "$URL" "$SECS" > "$A_OUT" ) &
APID=$!
sleep 2
( count_frames "$URL" $((SECS-2)) > "$B_OUT" ) &
BPID=$!
wait $APID $BPID
NA=$(cat "$A_OUT"); NB=$(cat "$B_OUT"); rm -f "$A_OUT" "$B_OUT"
FPSA=$(awk -v n="$NA" -v s="$SECS" 'BEGIN{printf "%.1f", n/s}')
FPSB=$(awk -v n="$NB" -v s="$((SECS-2))" 'BEGIN{printf "%.1f", n/s}')

info "Reader A: ${NA} frames (${FPSA} fps)"
info "Reader B: ${NB} frames (${FPSB} fps)"
echo
if [ "$NA" -gt 0 ] && [ "$NB" -gt 0 ]; then
  ok "BOTH readers received video simultaneously."
  info "The stream is multi-reader. Your app can consume it while you also"
  info "watch in a browser, and a second viewer does not kill the first."
  DROP=$(awk -v a="$FPSA" -v b="$FPS1" 'BEGIN{printf "%.0f", (b>0)?(1-a/b)*100:0}')
  if [ "$DROP" -gt 40 ] 2>/dev/null; then
    warn "Reader A lost ~${DROP}% of its frame rate once B joined."
    info "Expect some degradation - they share one encoder and one Wi-Fi link."
  fi
elif [ "$NA" -gt 0 ]; then
  bad "Reader B got nothing. SINGLE-READER stream."
elif [ "$NB" -gt 0 ]; then
  bad "Reader A died when B connected. The second viewer STEALS the stream."
  info "Single-reader, and worse: connecting kicks off the existing viewer."
else
  bad "Both readers failed. Something changed mid-test."
fi

# --- 5. snapshot while streaming -------------------------------------------
hdr "5. Snapshot while a stream is open"
( curl -s --max-time 6 "$URL" > /dev/null 2>&1 ) &
SPID=$!
sleep 2
T2=$(mktemp); curl -s --max-time 4 "$SNAP" -o "$T2"
S2=$(wc -c < "$T2" | tr -d ' '); M2=$(od -An -tx1 -N2 "$T2" | tr -d ' \n')
kill $SPID 2>/dev/null; wait $SPID 2>/dev/null
if [ "$M2" = "ffd8" ] && [ "$S2" -gt 500 ]; then
  ok "Snapshot works while streaming (${S2} bytes)"
  info "Useful: your app can poll snapshots without holding a stream open."
else
  warn "Snapshot during streaming returned ${S2} bytes, magic '${M2}'"
fi
rm -f "$T2"

hdr "SUMMARY"
cat <<TAIL
  Stream URL     : ${URL}
  Snapshot URL   : ${SNAP}
  Credentials    : none
  Single reader  : ${FPS1} fps
  Two readers    : A ${FPSA} fps / B ${FPSB} fps

  Paste this whole output back.

  Latency must be measured by a human - the script cannot see the robot:
    1. Point the camera at yourself with the stream open in a browser.
    2. Wave sharply and watch the screen.
    3. Repeat ~10 times. Estimate the delay between your hand and the picture.
       If it is hard to judge, film both your hand and the screen together
       with a phone, then count frames between the real wave and the
       on-screen wave.
TAIL
