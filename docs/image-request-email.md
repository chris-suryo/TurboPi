# Requesting the TurboPi system image from Hiwonder

Hiwonder do not publish the TurboPi OS image. Their own `resources_download.md` says:

> "System Image & Source Code: If you want to get the system image and source code, please
> email us at support@hiwonder.com, and share your order number :)"

Buying at Micro Center means a retail receipt, not a Hiwonder order number — so the email
below pre-empts that rather than waiting for them to ask for something you don't have.

**Send this early.** It's the only part of this project with external lead time, and nothing
else waits on it.

---

**To:** support@hiwonder.com
**Subject:** TurboPi system image request — Raspberry Pi 5 (retail purchase, Micro Center)

> Hello,
>
> I recently purchased a TurboPi kit (the version sold without a Raspberry Pi included) from
> Micro Center in the United States, so I have a retail receipt rather than a Hiwonder order
> number. I can provide a photo of the receipt and of the kit packaging if that helps verify
> the purchase.
>
> I am using a **Raspberry Pi 5 (8GB)**. Could you please send me:
>
> 1. The TurboPi system image **for the Raspberry Pi 5** (not the Pi 4B image), and
> 2. The matching source code package.
>
> If the download requires a specific purchase reference, please let me know what you need
> from the retail receipt and I will send it.
>
> Thank you,
> [your name]

---

## Why it's worded that way

- **It names the Pi 5 explicitly.** Hiwonder maintain two separate images — the differing
  default passwords (Pi 4B `pi`/`raspberry`, Pi 5 `pi`/`raspberrypi`) prove it. Asking for
  "the TurboPi image" invites the wrong one.
- **It raises the order-number problem up front** and offers the receipt, instead of waiting a
  round-trip to be asked.
- **It asks for the source package too.** The application source is already public at
  `github.com/Hiwonder/turbopi`, but their package may include image-specific extras — the
  `hiwonder-toolbox` Wi-Fi manager in particular has no public equivalent.

## When the image arrives

Check which one you actually got before assuming: try `pi`/`raspberrypi` first. If that fails
and `pi`/`raspberry` works, they sent the **Pi 4B** image — go back to them.

## If they don't come through

`docs/02-turbopi-pi5-compatibility.md` documents the fallback: clean Raspberry Pi OS Bookworm
64-bit plus the public source. The dependency list is enumerated there.
