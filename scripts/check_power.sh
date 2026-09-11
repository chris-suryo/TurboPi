#!/usr/bin/env bash
# check_power.sh — Raspberry Pi 5 power and undervoltage verification.
#
# RUN THIS ON THE PI, over SSH. It does not need to be root for most checks.
#
#   ./check_power.sh            # snapshot
#   ./check_power.sh --stress   # snapshot, 120s CPU load, then re-check sticky flags
#   ./check_power.sh --stress 300
#
# Design rule: this script NEVER guesses. If a value cannot be determined it says
# "UNKNOWN" and explains why. A clean run proves the checks ran, not that your
# hardware is fine — read the VERDICT section at the end.

set -uo pipefail

STRESS=0
STRESS_SECS=120
case "${1:-}" in
  --stress) STRESS=1; [ -n "${2:-}" ] && STRESS_SECS="$2" ;;
  "") ;;
  *) echo "usage: $0 [--stress [seconds]]" >&2; exit 2 ;;
esac

# ---------- output helpers ----------
RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; BLD=$'\033[1m'; RST=$'\033[0m'
[ -t 1 ] || { RED=""; YEL=""; GRN=""; BLD=""; RST=""; }

hdr()  { printf '\n%s=== %s ===%s\n' "$BLD" "$1" "$RST"; }
ok()   { printf '%s  OK%s      %s\n' "$GRN" "$RST" "$1"; }
warn() { printf '%s  WARN%s    %s\n' "$YEL" "$RST" "$1"; }
bad()  { printf '%s  PROBLEM%s %s\n' "$RED" "$RST" "$1"; }
unk()  { printf '  UNKNOWN %s\n' "$1"; }
info() { printf '          %s\n' "$1"; }

VERDICT_LINES=()
verdict() { VERDICT_LINES+=("$1"); }

# Read a big-endian u32 from a device-tree property file.
# Prints the integer, or returns 1 if unreadable.
read_dt_u32() {
  local f="$1"
  [ -r "$f" ] || return 1
  local v
  v=$(od -An -tu4 --endian=big "$f" 2>/dev/null | tr -d ' \n')
  if [ -z "$v" ]; then
    v=$(python3 -c "
import struct,sys
d=open('$f','rb').read()
sys.stdout.write(str(struct.unpack('>I', d[:4])[0]) if len(d)>=4 else '')
" 2>/dev/null)
  fi
  [ -n "$v" ] || return 1
  printf '%s' "$v"
}

# ---------- 0. identity ----------
hdr "0. Board and OS identity"
MODEL="UNKNOWN"
if [ -r /proc/device-tree/model ]; then
  MODEL=$(tr -d '\0' < /proc/device-tree/model)
  info "Model:  $MODEL"
else
  unk "/proc/device-tree/model unreadable — is this a Raspberry Pi?"
fi
info "Kernel: $(uname -srm)"
if [ -r /etc/os-release ]; then
  info "OS:     $(. /etc/os-release && echo "$PRETTY_NAME")"
fi

IS_PI5=0
case "$MODEL" in
  *"Raspberry Pi 5"*) IS_PI5=1 ;;
esac
if [ "$IS_PI5" -eq 1 ]; then
  ok "Confirmed Raspberry Pi 5."
else
  warn "This does NOT report as a Raspberry Pi 5 (model: $MODEL)."
  warn "Pi 5 specific checks below (PMIC, USB current limit) will likely be unavailable."
  verdict "Board is not a Pi 5, or the model string is unreadable. Everything below is suspect."
fi

if ! command -v vcgencmd >/dev/null 2>&1; then
  bad "vcgencmd not found. Install with: sudo apt install -y libraspberrypi-bin"
  bad "Without it, undervoltage cannot be checked. Stopping."
  exit 1
fi

# ---------- 1. throttle / undervoltage flags ----------
hdr "1. Undervoltage and throttling flags (the headline check)"
THROT_RAW=$(vcgencmd get_throttled 2>/dev/null)
if [ -z "$THROT_RAW" ]; then
  unk "vcgencmd get_throttled returned nothing."
  verdict "Could not read throttle flags — undervoltage state is UNKNOWN."
else
  info "raw: $THROT_RAW"
  HEX=${THROT_RAW#throttled=}
  VAL=$((HEX))
  # bit 0..3 = happening right now; bit 16..19 = has happened since boot
  b() { echo $(( (VAL >> $1) & 1 )); }
  NOW_UV=$(b 0);  NOW_CAP=$(b 1);  NOW_THR=$(b 2);  NOW_TMP=$(b 3)
  EVR_UV=$(b 16); EVR_CAP=$(b 17); EVR_THR=$(b 18); EVR_TMP=$(b 19)

  printf '          %-38s %-10s %s\n' "condition" "NOW" "SINCE BOOT"
  printf '          %-38s %-10s %s\n' "under-voltage (<~4.63V at the SoC)" "$NOW_UV" "$EVR_UV"
  printf '          %-38s %-10s %s\n' "ARM frequency capped"               "$NOW_CAP" "$EVR_CAP"
  printf '          %-38s %-10s %s\n' "currently throttled"                "$NOW_THR" "$EVR_THR"
  printf '          %-38s %-10s %s\n' "soft temperature limit"             "$NOW_TMP" "$EVR_TMP"
  echo

  if [ "$NOW_UV" -eq 1 ]; then
    bad "UNDERVOLTAGE RIGHT NOW. The supply is not holding 5V under the current load."
    verdict "UNDERVOLTAGE ACTIVE — this charger is not adequate. Do not build on it."
  elif [ "$EVR_UV" -eq 1 ]; then
    bad "Undervoltage has occurred since boot (not happening this instant)."
    bad "This is the intermittent-failure signature. A sticky bit is still a failure."
    verdict "Undervoltage occurred since boot — charger is marginal. Replace it."
  else
    ok "No undervoltage now, and none since boot."
    verdict "No undervoltage seen (uptime $(cut -d. -f1 /proc/uptime)s). Meaningful only if the Pi was actually loaded — use --stress."
  fi

  if [ "$EVR_TMP" -eq 1 ] || [ "$NOW_TMP" -eq 1 ]; then
    warn "Thermal limiting seen — that is a cooling issue, not a power issue."
  fi
fi

info "Temperature: $(vcgencmd measure_temp 2>/dev/null || echo UNKNOWN)"

# ---------- 2. PMIC rails ----------
hdr "2. PMIC rail measurements (Pi 5 only — the real voltage numbers)"
PMIC=$(vcgencmd pmic_read_adc 2>/dev/null)
if [ -z "$PMIC" ] || echo "$PMIC" | grep -qi "error\|not supported"; then
  unk "vcgencmd pmic_read_adc unavailable on this firmware/board."
  unk "Not fatal — section 1 is the authoritative undervoltage check."
else
  EXT5V=$(echo "$PMIC" | grep -i 'EXT5V_V' | grep -oE '[0-9]+\.[0-9]+' | head -1)
  if [ -n "$EXT5V" ]; then
    info "EXT5V_V (voltage arriving from your charger): ${EXT5V} V"
    # bash has no floats; compare in milli-volts via awk
    MV=$(awk -v v="$EXT5V" 'BEGIN{printf "%d", v*1000}')
    if   [ "$MV" -lt 4750 ]; then bad  "Below 4.75V — out of spec. Charger or cable is inadequate."; verdict "Input rail ${EXT5V}V is below the 4.75V floor."
    elif [ "$MV" -lt 4900 ]; then warn "Low-ish (${EXT5V}V). Fine at idle, likely to sag under load. Re-check with --stress."
    else ok "Input rail healthy at ${EXT5V}V."
    fi
  else
    unk "Could not find EXT5V_V in pmic_read_adc output."
  fi
  info "(full PMIC dump below)"
  echo "$PMIC" | sed 's/^/          /'
fi

# ---------- 3. USB current budget ----------
hdr "3. USB peripheral current budget (this is the one that bites robots)"
DT=/proc/device-tree/chosen/power
USB_EN=$(read_dt_u32 "$DT/usb_max_current_enable") || USB_EN=""
MAX_MA=$(read_dt_u32 "$DT/max_current_ma")         || MAX_MA=""

if [ -n "$MAX_MA" ]; then
  info "Power supply claims it can deliver: ${MAX_MA} mA"
  if [ "$MAX_MA" -ge 5000 ]; then
    ok "Supply advertises >=5A. This is what the Pi 5 wants."
  elif [ "$MAX_MA" -eq 0 ]; then
    warn "Supply advertises 0 mA — it did not negotiate USB-PD at all."
    warn "Typical of: a non-PD charger, a USB-A brick with an A-to-C cable, or GPIO/HAT power."
  else
    warn "Supply advertises only ${MAX_MA} mA (Pi 5 wants 5000)."
  fi
else
  unk "$DT/max_current_ma not present — cannot read what the supply claims."
fi

if [ -n "$USB_EN" ]; then
  if [ "$USB_EN" -ne 0 ]; then
    ok "USB current limit is the HIGH one (1.6A total for peripherals)."
    verdict "USB budget: 1.6A (high). Camera + peripherals have headroom."
  else
    warn "USB current limit is the LOW one: 600mA total across ALL USB peripherals."
    warn "The Pi runs fine. Peripherals are what starve — intermittently, under load."
    warn "This is exactly the failure mode you wanted to catch."
    verdict "USB budget: 600mA (low). A USB camera plus anything else will be tight."
  fi
else
  unk "$DT/usb_max_current_enable not present — USB budget UNKNOWN."
  unk "Expected on Pi 5 with current firmware. On Pi 4 this file does not exist."
fi

CFG=/boot/firmware/config.txt
[ -r "$CFG" ] || CFG=/boot/config.txt
if [ -r "$CFG" ]; then
  info "config.txt in use: $CFG"
  if grep -qE '^\s*usb_max_current_enable' "$CFG"; then
    info "  usb_max_current_enable is set: $(grep -E '^\s*usb_max_current_enable' "$CFG" | tr -d ' ')"
  else
    info "  usb_max_current_enable is NOT set (firmware is auto-detecting)."
  fi
else
  unk "Could not read config.txt at /boot/firmware/config.txt or /boot/config.txt."
fi

# ---------- 4. kernel messages ----------
hdr "4. Kernel messages mentioning power"
DMESG=$(dmesg 2>/dev/null) || DMESG=""
if [ -z "$DMESG" ]; then
  unk "dmesg unreadable as this user. Re-run with: sudo $0 ${1:-}"
else
  HITS=$(echo "$DMESG" | grep -iE 'under-?voltage|over-?current|usb.*current|power supply|hwmon' | tail -20)
  if [ -n "$HITS" ]; then
    echo "$HITS" | sed 's/^/          /'
    if echo "$HITS" | grep -qi 'under-\?voltage'; then
      bad "Kernel logged an undervoltage event."
      verdict "Kernel log contains an undervoltage event."
    fi
  else
    ok "No power-related complaints in the kernel log."
  fi
fi

# ---------- 5. optional stress ----------
if [ "$STRESS" -eq 1 ]; then
  hdr "5. Load test (${STRESS_SECS}s) — idle testing is how people miss this"
  NPROC=$(nproc 2>/dev/null || echo 4)
  info "Loading $NPROC cores for ${STRESS_SECS}s..."
  PIDS=()
  if command -v stress-ng >/dev/null 2>&1; then
    stress-ng --cpu "$NPROC" --timeout "${STRESS_SECS}s" >/dev/null 2>&1 &
    PIDS+=($!)
  else
    info "(stress-ng not installed; using shell busy-loops. 'sudo apt install stress-ng' for a harder test.)"
    for _ in $(seq "$NPROC"); do
      ( while :; do :; done ) & PIDS+=($!)
    done
  fi
  for i in $(seq "$STRESS_SECS"); do
    if [ $((i % 10)) -eq 0 ]; then
      printf '          t=%3ss  temp=%s  throttled=%s\n' \
        "$i" \
        "$(vcgencmd measure_temp 2>/dev/null | cut -d= -f2)" \
        "$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"
    fi
    sleep 1
  done
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null
  sleep 2

  info "Post-load flags:"
  POST=$(vcgencmd get_throttled 2>/dev/null)
  info "  $POST"
  PVAL=$(( ${POST#throttled=} ))
  if [ $(( (PVAL >> 16) & 1 )) -eq 1 ]; then
    bad "Undervoltage occurred during the load test."
    verdict "LOAD TEST FAILED: undervoltage appeared under CPU load. Charger inadequate."
  else
    ok "No undervoltage under sustained CPU load."
    verdict "LOAD TEST PASSED: no undervoltage across ${STRESS_SECS}s of full CPU load."
  fi
  if command -v vcgencmd >/dev/null && vcgencmd pmic_read_adc >/dev/null 2>&1; then
    E2=$(vcgencmd pmic_read_adc 2>/dev/null | grep -i EXT5V_V | grep -oE '[0-9]+\.[0-9]+' | head -1)
    [ -n "$E2" ] && info "  EXT5V_V after load: ${E2} V"
  fi
else
  hdr "5. Load test — SKIPPED"
  info "Flags above reflect an essentially idle Pi, which proves very little."
  info "Re-run as: ./check_power.sh --stress"
  verdict "No load test was run. An idle pass is NOT a verified-good charger."
fi

# ---------- verdict ----------
hdr "VERDICT"
for l in "${VERDICT_LINES[@]}"; do printf '  - %s\n' "$l"; done
cat <<'TAIL'

  Paste this entire output back into the session for interpretation.
  Reminder on what these checks can and cannot tell you:
    * Section 1 sticky bits are the ground truth for undervoltage.
    * Section 3 is about PERIPHERAL current, not whether the Pi itself boots.
      A Pi with a 600mA USB budget boots perfectly and then misbehaves later,
      which is precisely the "weird intermittent failure" you set out to avoid.
TAIL
