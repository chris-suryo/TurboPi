# Flashing the SD card and headless first boot

No monitor, no micro-HDMI cable. Every command below is labelled with **which terminal** it
belongs in, because a command run in the wrong shell is a wasted hour.

- **PowerShell (PC)** — your Windows 11 machine
- **Terminal (Mac)** — your MacBook
- **SSH (Pi)** — a shell on the Raspberry Pi, reached over the network

### Use the MacBook for this, if it's to hand

Either machine works, but the Mac has genuinely less friction for this particular job:

| | macOS | Windows 11 |
|---|---|---|
| `ssh` / `scp` | Built in | Built in (OpenSSH client) |
| `turbopi.local` name resolution | **Bonjour, works reliably** | mDNS support is inconsistent — often needs the raw IP |
| Raspberry Pi Imager | Native `.dmg` | Native installer |

The `.local` hostname is the difference that matters: on the Mac you type `ssh pi@turbopi.local`
and it just resolves. On Windows you may end up hunting the Pi's IP in your router's DHCP list
every time. Nothing about the robot cares which machine you used — the SD card is the same
either way, and you can switch later.

Commands below are given for both. `ssh`, `scp` and the Imager GUI are identical on each; only
the shell prompt differs.

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

**Terminal (Mac):**
```bash
brew install --cask raspberry-pi-imager
```

Or download the installer for either OS from `raspberrypi.com/software`.

### 2. Configure it for headless boot — the important part

Insert the microSD via your USB reader, launch Imager, then:

- **Choose Device** → Raspberry Pi 5
- **Choose OS** → Raspberry Pi OS (64-bit)
- **Choose Storage** → your card (*check the size — this erases it*)
- **Next** → then enter customisation. **Do not skip it** — it is what makes the boot headless.

### Imager v2.x (sidebar wizard)

Recent Imager versions (v2.0+) split customisation into a left-hand sidebar instead of one
settings page. Work down it in order; **never click "SKIP CUSTOMISATION"**, which discards
everything and leaves you with an unreachable headless Pi.

| Sidebar section | What to set |
|---|---|
| **Hostname** | `turbopi` |
| **Localisation** | Time zone, keyboard — **and the Wi-Fi / WLAN country** (`US`). If the radio's country is unset the Pi may never bring Wi-Fi up |
| **User** | Username **`pi`** (not negotiable — the SDK hardcodes `/home/pi`), plus a password |
| **Wi-Fi** | SECURE NETWORK, your SSID **exactly**, password twice. Leave "Hidden SSID" unchecked unless yours really is hidden |
| **Remote access** | **Enable SSH**, password authentication. Without this there is no way in |
| **Raspberry Pi Connect** | **Skip.** It's Raspberry Pi's cloud remote-access service and needs an account. You only need LAN access |

Then continue to **Writing** and confirm the erase.

### Older Imager (single settings page)

Click **EDIT SETTINGS** at the "apply OS customisation" prompt, and set the same values —
General tab for hostname / user / Wi-Fi / locale, Services tab for SSH.

| Setting | Value | Why |
|---|---|---|
| Hostname | `turbopi` | Lets you use `turbopi.local` instead of hunting for an IP |
| Username | **`pi`** | TurboPi's SDK hardcodes `/home/pi/TurboPi` |
| Password | your choice | |
| Wi-Fi SSID / password | your network | Without this a headless Pi has no way to reach you |
| Wi-Fi country | your country | Omitting it can leave the radio disabled |
| **Enable SSH** | password authentication | Without this, there is no way in |

### Getting the SSID exactly right

A mistyped SSID means the Pi silently never joins, and you have no screen to debug it on. Some
routers include punctuation or band labels in the name itself, so read it rather than
remembering it.

**Terminal (Mac):**
```bash
networksetup -getairportnetwork en0
```

Prints `Current Wi-Fi Network: <ssid>`. Copy everything after the colon, verbatim — spaces,
parentheses and all. If `en0` isn't your Wi-Fi interface:

```bash
networksetup -listallhardwareports
```

and use the device listed under `Hardware Port: Wi-Fi`.

**2.4GHz vs 5GHz:** the Pi 5 is dual-band, so either works. If your router exposes both as
separate SSIDs, prefer **2.4GHz for the finished robot** — better range and wall penetration
for something that drives around. For bench testing next to the router, it makes no difference.

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

**PowerShell (PC)** or **Terminal (Mac)** — identical command:
```bash
ssh pi@turbopi.local
```

First connection asks you to accept the host key — type `yes`. You are now in an **SSH (Pi)**
shell, and everything below runs there.

### 5. Get the verification scripts onto the Pi

**PowerShell (PC)**, from this repo's directory:
```powershell
scp scripts\check_power.sh scripts\check_hardware.py pi@turbopi.local:~/
```

**Terminal (Mac)** — same thing, forward slashes:
```bash
scp scripts/check_power.sh scripts/check_hardware.py pi@turbopi.local:~/
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
