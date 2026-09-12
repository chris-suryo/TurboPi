# Flashing the SD card and headless first boot

No monitor, no micro-HDMI cable. Every command below is labelled with **which terminal** it
belongs in, because a command run in the wrong shell is a wasted hour.

- **PowerShell (PC)** — your Windows 11 machine
- **SSH (Pi)** — a shell on the Raspberry Pi, reached over the network

---

## First: what you're actually doing

### What an "image" is

A disk image is a **byte-for-byte copy of an entire configured disk** — bootloader,
filesystem, OS, installed packages, config files, everything. It is not an installer. You
don't run it; you *write* it onto the SD card, and the card then **becomes** that disk.

That's why hardware vendors ship them. Rather than documenting forty setup steps, Hiwonder
configure one Pi until the robot works, clone the whole card to a `.img` file, and hand you
the clone. Convenient, but opaque — you inherit their choices without seeing them, and the
clone is frozen at whatever date they made it.

Raspberry Pi OS is distributed as an image too. The difference is that **Raspberry Pi Imager
customises it as it writes** — you give it a hostname, username, Wi-Fi credentials and SSH
setting, and it injects those into the image before the card is ejected. That single feature
is what makes a headless first boot possible: the Pi comes up already knowing your Wi-Fi and
already accepting SSH, so it never needs a monitor.

One practical consequence worth knowing: **stock Raspberry Pi OS is not board-specific.** The
same arm64 image boots a Pi 3, 4 or 5, so the card is largely portable if you change boards
later. Vendor images usually *are* board-specific — which is exactly why Hiwonder maintain
separate Pi 4B and Pi 5 builds with different passwords.

### The four layers you're assembling

Setting up a Pi is not one step, it's four. Keeping them separate makes failures much easier
to diagnose, because you'll know which layer broke.

| Layer | What it is | Where |
|---|---|---|
| **1. The card** | The SD card is the Pi's hard drive. Flashing = installing the OS | `03` (this doc) |
| **2. The OS** | Raspberry Pi OS Bookworm, 64-bit — Debian for arm64 | `03` |
| **3. System config** | Turning on the hardware interfaces. I2C and UART are **off by default**, and a serial console has to be moved out of the robot's way | `07` |
| **4. The application** | Python packages, then TurboPi's own source | `07` |

Layer 3 is the one that surprises people coming from normal Linux: on a Pi, the physical
buses are disabled until you ask for them. Nothing is broken — it just isn't switched on yet.

### Roughly how long

| Step | Time | Notes |
|---|---|---|
| Install Imager, flash the card | 10–15 min | Mostly waiting on the write |
| First boot + SSH in | 5–10 min | First boot resizes the filesystem and reboots itself |
| Power verification | 10–15 min | Plus the stress test's own runtime |
| System config + reboot | 10 min | I2C, UART, serial console |
| Install dependencies | 20–45 min | `mediapipe` is the wildcard |
| Clone source + verify | 10 min | |

**Call it 1.5–2 hours of actual work** if nothing fights you, spread over as many sittings as
you like. Mechanical assembly is separate — budget a couple of hours for that, unhurried.

The long pole is dependency installation, and it's unattended waiting rather than work.

---

## Flash stock Raspberry Pi OS (this card becomes the robot's OS)

We deliberately start with **stock Raspberry Pi OS Bookworm 64-bit**, not the Hiwonder image.
Reason: right now we are testing *hardware*. A known-good, widely-documented OS keeps the
variable count down. If something misbehaves we want to suspect the charger, not a vendor
image. It is also the base the robot itself will run on — see
`07-build-from-clean-os.md`. You flash once; this card becomes the robot's OS.

### 1. Install Raspberry Pi Imager

**PowerShell (PC):**
```powershell
winget install RaspberryPiFoundation.RaspberryPiImager
```

### 2. Configure it for headless boot — the important part

Insert the microSD via your USB reader, launch Imager, then:

- **Choose Device** → Raspberry Pi 5
- **Choose OS** → Raspberry Pi OS (64-bit)
- **Choose Storage** → your card (*check the size — this erases it*)
- Click **Next**, then **EDIT SETTINGS**. Do not skip this. This is what makes it headless:

| Setting | Value | Why |
|---|---|---|
| Hostname | `turbopi` | Lets you use `turbopi.local` instead of hunting for an IP |
| Username | **`pi`** | TurboPi's SDK hardcodes `/home/pi/TurboPi` — save yourself the pain |
| Password | your choice | |
| Wi-Fi SSID / password | your network | Without this a headless Pi has no way to reach you |
| Wi-Fi country | your country | Omitting it can leave the radio disabled |
| Locale / timezone | yours | |
| **Services tab → Enable SSH** | **Use password authentication** | Without this, there is no way in |

Then **Save** → **Yes** → **Yes** to confirm the erase.

> Use `pi` as the username even on this throwaway install. It costs nothing now and avoids a
> class of path bugs later.

### 3. First boot

Card into the Pi, power in, wait ~90 seconds. First boot resizes the filesystem and reboots
once — so if it seems to restart on you, that's expected.

### 4. Find it and connect

**PowerShell (PC):**
```powershell
ping turbopi.local
```

If mDNS doesn't resolve (some Windows setups and some routers), find it by IP instead:

**PowerShell (PC):**
```powershell
arp -a | Select-String "b8-27-eb|dc-a6-32|e4-5f-01|d8-3a-dd|2c-cf-67"
```

Those are Raspberry Pi Foundation MAC prefixes. Your router's DHCP client list also works.

**PowerShell (PC):**
```powershell
ssh pi@turbopi.local
```

First connection asks you to accept the host key — type `yes`. You are now in an **SSH (Pi)**
shell, and everything below runs there.

### 5. Get the verification scripts onto the Pi

**PowerShell (PC)**, from this repo's directory:
```powershell
scp scripts\check_power.sh scripts\check_hardware.py pi@turbopi.local:~/
```

**SSH (Pi):**
```bash
chmod +x ~/check_power.sh ~/check_hardware.py
sudo apt update && sudo apt install -y stress-ng i2c-tools gpiod v4l-utils
./check_power.sh --stress
```

`stress-ng` is what makes the load test meaningful; the others let the hardware probe identify
things rather than reporting UNKNOWN. Paste the output back.

---

## Optional: the Hiwonder TurboPi image

**You don't need this** — `02-turbopi-pi5-compatibility.md` explains why, and
`07-build-from-clean-os.md` is the path we're taking. Keep this section for the case where
Hiwonder do send you the Pi 5 image and you want it on a second card as a reference.

Their image is a `.img` file rather than an Imager-managed OS, so Imager's settings screen
does not apply. **Use Imager's "Use custom" option** and skip the customisation prompt.

Their docs suggest Win32DiskImager; Raspberry Pi Imager writes a raw `.img` just as correctly
and you already have it installed.

### Connecting to the Hiwonder image

The Hiwonder image behaves differently from stock — it comes up as its own **access point**:

1. The Pi broadcasts a Wi-Fi network named `HW-...`
2. Join it from your PC. Password: `hiwonder`
3. The Pi is at **`192.168.149.1`**
4. Log in with **`pi` / `raspberrypi`** (the **Pi 5** credentials; the Pi 4B image uses
   `pi` / `raspberry` — which is a quick way to tell which image you were actually sent)

**PowerShell (PC):**
```powershell
ssh pi@192.168.149.1
```

### Switching it onto your LAN

AP mode means your PC must leave your normal network to talk to the robot, which gets old fast.
Hiwonder ships a toolbox to switch it to joining your network instead:

**SSH (Pi):**
```bash
cd ~/hiwonder-toolbox
```

The Wi-Fi config there has an `HW-WIFI-MODE` setting: **`1` = AP mode**, **`2` = join an
existing network**. Set mode 2 plus your SSID and password, then reboot. After that the robot
is just another host on your LAN and you can SSH to it normally.

We will walk through the exact file contents when you have the image in hand — the layout
varies between image revisions, and guessing at it from here would be exactly the kind of
confident-sounding output you asked me not to give you.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `turbopi.local` won't resolve | mDNS blocked — use the IP from your router |
| SSH connection refused | SSH wasn't enabled in Imager settings — re-flash, it's faster than fixing it |
| Pi never joins Wi-Fi | Wrong Wi-Fi country, or a 5GHz-only SSID; check the band |
| Boots, then dies under load | Power. Go back to `01-pi5-power-verification.md` |
| Green LED flashes in a pattern | Boot failure code — count the flashes and look it up |
