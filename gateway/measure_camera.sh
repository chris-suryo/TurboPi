#!/usr/bin/env bash
#
# Measure what the camera is actually doing. Run ON THE PI.
#
#     bash measure_camera.sh [seconds]        default 10
#
# Reports: what the USB camera can do, what the pipeline is configured for, and what
# comes out the other end -- resolution, frame rate, bytes per frame, and the CPU the
# robot's process is burning to produce it.
#
# Read this before changing anything: TurboPi does NOT run mjpg-streamer. The stream is
# MjpgServer.py, a Python ThreadingHTTPServer inside TurboPi.py, fed by Camera.py, which
# JPEG-encodes every frame with OpenCV. So "resolution" is set in two places that do not
# have to agree, and the CPU cost is Python's, not a C daemon's.

set -uo pipefail
SECONDS_TO_SAMPLE=${1:-10}
STREAM=http://127.0.0.1:8080/
SRC=/home/pi/TurboPi/Camera.py

echo "=== What the USB camera can do ==="
if command -v v4l2-ctl >/dev/null; then
  v4l2-ctl --list-formats-ext -d /dev/video0 2>/dev/null \
    | grep -E "Size|Interval" | sed 's/^/  /' | head -40
  echo
  echo "  Currently negotiated format:"
  v4l2-ctl --get-fmt-video -d /dev/video0 2>/dev/null | sed 's/^/    /'
else
  echo "  v4l2-ctl not installed. Install with: sudo apt install -y v4l-utils"
fi

echo
echo "=== What the pipeline is configured for ==="
echo "  Camera.py resize target:"
grep -n "def __init__(self, resolution" "$SRC" 2>/dev/null | sed 's/^/    /' \
  || echo "    could not read $SRC"
echo "  Capture properties actually set on the device:"
grep -n "cap.set(" "$SRC" 2>/dev/null | sed 's/^/    /'
echo "  Resize filter:"
grep -n "cv2.resize" "$SRC" 2>/dev/null | sed 's/^/    /'
echo "  JPEG quality (MjpgServer.py):"
grep -n "IMWRITE_JPEG_QUALITY" /home/pi/TurboPi/MjpgServer.py 2>/dev/null | sed 's/^/    /'

echo
echo "=== What actually comes out, over ${SECONDS_TO_SAMPLE}s ==="
PID=$(systemctl show -p MainPID --value turbopi 2>/dev/null)
read_cpu() { awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null; }
TICKS=$(getconf CLK_TCK)

CPU0=$(read_cpu "$PID")
TMP=$(mktemp)
curl -s --max-time "$SECONDS_TO_SAMPLE" "$STREAM" -o "$TMP"
CPU1=$(read_cpu "$PID")

BYTES=$(stat -c%s "$TMP")
# Each part starts with the boundary MjpgServer.py writes.
FRAMES=$(grep -c -- "--boundarydonotcross" "$TMP" 2>/dev/null | tr -cd '0-9')
FRAMES=${FRAMES:-0}
rm -f "$TMP"

if [ "$FRAMES" -gt 0 ]; then
  python3 - "$FRAMES" "$BYTES" "$SECONDS_TO_SAMPLE" "${CPU0:-0}" "${CPU1:-0}" "$TICKS" <<'PY'
import sys
frames, total, secs, c0, c1, ticks = (float(x) for x in sys.argv[1:7])
print(f"  frames        : {int(frames)}")
print(f"  frame rate    : {frames/secs:.1f} fps")
print(f"  bytes / frame : {total/frames:,.0f}")
print(f"  bandwidth     : {total*8/secs/1e6:.1f} Mbit/s")
if c1 > c0:
    print(f"  TurboPi.py CPU: {(c1-c0)/ticks/secs*100:.0f}% of one core")
PY
else
  echo "  No frames. Is TurboPi.py running and the camera open?"
fi

echo
echo "  A snapshot, for comparison (quality 100 rather than 70):"
curl -s -o /tmp/_snap.jpg -w "    %{size_download} bytes in %{time_total}s\n" \
  "${STREAM}?action=snapshot"
if command -v identify >/dev/null; then
  identify /tmp/_snap.jpg 2>/dev/null | sed 's/^/    /'
else
  python3 -c "
import struct, sys
d = open('/tmp/_snap.jpg','rb').read()
i = 2
while i < len(d):
    if d[i] != 0xFF: break
    m = d[i+1]
    if m in (0xC0, 0xC1, 0xC2):
        h, w = struct.unpack('>HH', d[i+5:i+9]); print(f'    actual image: {w}x{h}'); break
    i += 2 + struct.unpack('>H', d[i+2:i+4])[0]
" 2>/dev/null
fi
rm -f /tmp/_snap.jpg
