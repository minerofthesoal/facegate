# FaceGate

A Howdy-style face-unlock utility for Arch Linux, built around the Logitech
Brio's RGB **and** IR camera streams.



## How it's different from Howdy

Howdy ships a compiled PAM module (`pam_python`-based). FaceGate instead
uses **`pam_exec.so`**, which already ships with `pam` on every Arch
install -- no compiling a PAM module against your kernel/libc, no DKMS-like
breakage on updates. `pam_exec` just runs an external program and reads its
exit code: `0` = authenticated, anything else = "fall through to the next
line in the stack" (i.e. your normal password prompt).

## What it does

- Detects Logitech webcam `/dev/videoN` nodes via `v4l2-ctl` and classifies
  them as RGB or IR (known-device table first, live color-saturation probe
  as a fallback for anything not in that table).
- Trains a separate OpenCV LBPH face model per stream at enrollment.
- At auth time, checks every configured stream and (by default) **requires
  all of them to match** before allowing the login through. A stream that
  never detects a face at all during an attempt is treated as a
  camera/detection problem and excluded rather than counted as a failure.
- **Multi-camera support (v0.2.1):** beyond the primary RGB+IR pair, you can
  add further cameras -- e.g. a second, IR-less webcam like a C930 --  as an
  extra independent RGB check via `facegate camera add`. Each gets its own
  model file and threshold; `facegate enroll` trains/updates all of them
  in one pass.
- `facegate enroll --append` adds new samples to an existing model
  (LBPH `update()`) instead of overwriting it, so you can teach a second
  look -- glasses, new lighting, a newly-added camera -- without losing
  the original enrollment.
- Wires into `/etc/pam.d/sudo` (so plain `sudo <anything>`, not just
  `facegate` commands, tries face recognition first), `/etc/pam.d/login`,
  and KDE's `kscreenlocker`/SDDM services where present.
- Gives up and falls back to your normal password after a configurable
  number of failed recognition attempts (`facegate set-attempts N`,
  default 2) -- it won't sit there indefinitely retrying.
- **Optional Hugging Face backup (v0.2.1, OFF by default):**
  `facegate hf-upload on` lets a user's very first successful enrollment
  (never later `--append` re-enrollments) be backed up -- background
  blurred outside the detected face -- to a Hugging Face dataset repo
  (default `ray0rf1re/faces`). Needs `huggingface_hub` installed and
  `huggingface-cli login` already done; FaceGate never stores or asks for
  a token. See the security note in that section below before turning it on.
- CLI (`facegate`) for setup, enrollment, testing, enabling/disabling.
- Disabling or uninstalling requires your real account password **or** a
  separate FaceGate PIN you set during setup. This check runs through its
  own isolated PAM service (`facegate-verify`, containing nothing but
  `pam_unix.so`) specifically so a spoofed or successfully-recognized face
  can never be used to satisfy "prove you know the password" and disable
  FaceGate itself -- see the security note below.



## Install
```curl -fsSL https://raw.githubusercontent.com/minerofthesoal/facegate/main/get.sh | bash```

or

```bash
git clone <this repo, or just unzip the files>
cd facegate
sudo ./install.sh
sudo facegate autosetup
```

`autosetup` will:
1. Find and classify your primary camera's RGB/IR streams.
2. Offer to add a second camera (e.g. a C930) if one's detected and unused.
3. Offer the optional, off-by-default Hugging Face backup (see above).
4. Ask you to type `yes` before capturing your face.
5. Ask you to set a PIN for disabling later.
6. Show you the exact PAM line it wants to add to `/etc/pam.d/sudo` (and
   `/etc/pam.d/login`/greeter services if present), take a timestamped
   backup of each file, and ask for confirmation before touching it.

## CLI reference

```
sudo facegate autosetup          # full guided first-time setup
sudo facegate enroll [--user X] [--append]  # (re)register a face; --append adds to existing model
facegate test [--user X]         # dry-run recognition, changes nothing
sudo facegate enable             # turn face unlock back on
sudo facegate disable            # turn it off (needs sudo password or PIN)
sudo facegate set-pin            # change the disable/uninstall PIN
sudo facegate set-attempts N     # attempts before falling back to password (default 2)
sudo facegate relax              # loosen thresholds if legitimate matches keep failing
facegate camera list             # show all configured cameras
sudo facegate camera add [--device PATH] [--id NAME] [--kind rgb|ir]
sudo facegate camera remove ID   # 'rgb'/'ir' clear the primary pair; other ids drop an extra camera
sudo facegate hf-upload on|off   # optional first-enrollment-only Hugging Face backup
sudo facegate kde-passive-unlock on|off  # EXPERIMENTAL: proactive no-Enter KDE unlock, see below
facegate status                  # show current config
facegate diag                    # probe cameras: frames read, brightness, face-detection rate
sudo facegate doctor              # health check: camera, PAM wiring, models, lockout, logs
facegate log [-n N]               # show recent auth attempts from the FaceGate log
sudo facegate uninstall           # strip PAM integration (needs sudo pw or PIN)
```

## Multi-camera setup

If you have a second webcam -- most usefully one with no IR sensor, like a
Logitech C930 -- you can add it as a third independent check:

```bash
sudo facegate camera add          # interactively pick from unused Logitech devices
sudo facegate enroll --append     # train the new camera without disturbing existing models
facegate camera list              # confirm it's there
```

By default all configured streams (primary RGB, primary IR, and every
extra camera) must match (`recognition.require_both`, despite the name,
now applies to however many streams are configured). Set it to `false` in
`/etc/facegate/config.json` if you'd rather any one matching stream be
enough.

## Optional Hugging Face backup

`facegate hf-upload on` turns on a one-time-per-user backup of enrollment
images to a Hugging Face dataset repo (default `ray0rf1re/faces`):

```bash
huggingface-cli login             # do this first -- FaceGate never stores a token
sudo facegate hf-upload on
sudo facegate enroll              # first-ever enrollment for this user gets uploaded
```

**Security/privacy note:** this uploads real photos of your face to a
remote repository. Blurring everything outside the detected face bounding
box reduces incidental background exposure, but it is not anonymization --
if the target repo is public, your face is public. It's off by default for
this reason, only ever fires once per username no matter how many times
you re-run `enroll --append` afterward, and `autosetup` asks explicitly
rather than assuming yes.

## Tuning

`facegate test` prints raw LBPH confidence numbers (**lower = better
match**). If you're getting false rejects or accepts, edit the thresholds
in `/etc/facegate/config.json` under `recognition.confidence_threshold_rgb`
/ `confidence_threshold_ir`, or flip `require_both` to `false` if your unit
turned out to have no usable IR stream.

If enrollment fails with "not enough face samples," run:

```bash
facegate diag
```

This probes every detected Brio device for a few seconds and reports
resolution, actual frame format, how many frames were read, and how many
of those frames had a detectable face -- so you can tell whether the
problem is "camera isn't delivering frames" vs. "frames are fine but
you're too far/dark for the detector." In the latter case, lower
`recognition.min_face_size` in `/etc/facegate/config.json` (default `80`)
and re-run enrollment.

## Using it beyond `sudo`

`facegate enable`/`autosetup` also wire into `/etc/pam.d/login` (if present),
`/etc/pam.d/sddm` (the login screen you see right after a restart), and
`/etc/pam.d/kde` -- KDE's `kscreenlocker` authenticates against a PAM
service literally called `kde`, whose vendor default on Arch ships at
`/usr/lib/pam.d/kde` rather than `/etc/pam.d/kde`. If `/etc/pam.d/kde`
doesn't exist yet, FaceGate will offer to create it by copying that vendor
default first (so you keep normal password auth as a fallback line) before
adding its own line on top.

**Important: "kde" and "sddm" are the password stacks.** PAM only
evaluates a stack once a credential is actually submitted through it --
for kscreenlocker/SDDM that means pressing Enter, even with the password
field left blank -- not the instant the lock/login screen appears. So out
of the box this is "hit Enter, get scanned, password prompt appears only
if it didn't match," not a fully passive Windows-Hello-style scan. If it
feels like "it's not even trying," this is almost always why: try hitting
Enter on the empty field and watching for the camera light.

### Experimental: proactive (no-Enter) unlock on KDE

Plasma 6's `kscreenlocker` has a "multiauth" feature that can proactively
poll a *separate* PAM service for fingerprint-style readers
(`kde-fingerprint`), independent of whether you've touched the password
field. `facegate kde-passive-unlock on` wires FaceGate into that slot
instead, which may get you a genuinely proactive scan.

This is genuinely experimental, not a guaranteed fix:
- The PAM file existing usually isn't the blocker -- Arch's `kscreenlocker`
  package ships `kde-fingerprint` as a vendor default under `/usr/lib/pam.d/`.
- What's NOT confirmed: kscreenlocker appears to decide whether to
  proactively poll this slot based on `fprintd` reporting an actual
  registered fingerprint device over D-Bus, not just on the PAM file
  existing. Real KDE bug reports (e.g. bugs.kde.org #485124) show even
  people with genuine, correctly-enrolled fingerprint readers sometimes
  never get the prompt at all. Without a real fprintd device, this may
  simply never fire on your system, regardless of the PAM wiring being
  correct. Making the system believe a fingerprint device exists (an
  fprintd D-Bus shim) would be a materially bigger, separate project --
  it isn't built here, since it has its own risks (conflicting with a
  real fingerprint reader if you ever add one, impersonating another
  daemon's identity). Ask explicitly if that tradeoff is worth pursuing.
- Mixing non-password auth into the lock screen can interact oddly with
  KWallet's automatic-unlock-on-login, which assumes a real password login.
- After enabling, lock your screen and check `sudo facegate log` or
  `sudo journalctl -t facegate -e` to see whether it was invoked at all --
  that's the fastest way to tell if it's actually doing anything on your
  setup.

```bash
sudo facegate kde-passive-unlock on   # prints the caveats above, asks to confirm
sudo facegate kde-passive-unlock off  # revert
```

For other lockers (GNOME's `gdm-password`, `swaylock`, `hyprlock`, `i3lock`,
etc.), FaceGate doesn't touch them automatically -- add the same
`pam_exec.so` line manually to the right file under `/etc/pam.d/` for that
service. Check `journalctl` after a failed unlock attempt if you're not
sure of the exact service name in use; PAM logs which service name it
looked up.

## Files

```
/etc/facegate/config.json       # settings (root-owned, world-readable, 0644)
/etc/facegate/pin.json          # disable/uninstall PIN hash+salt (root-only, 0600)
/etc/facegate/models/*.yml      # per-user, per-stream LBPH models (root-owned, world-readable, 0644)
/var/log/facegate/facegate.log  # rotating log (world-writable, sticky bit, 1777/0666)
/usr/lib/facegate/facegate/     # the package
/usr/bin/facegate               # CLI
/usr/bin/facegate-auth          # PAM-exec entry point (do not run manually as auth)
```

**Why config/models/log are world-readable/writable (v0.2.3):**
`facegate-auth` runs with whatever privileges the *calling* PAM service
has -- root for `sudo` (which stays root through its whole auth phase),
but your actual logged-in user for `kscreenlocker` (it's just
re-confirming you're still you, not escalating). A non-root invocation
couldn't read the old root-only files at all, which is why lock-screen
attempts used to fail instantly with nothing logged anywhere. None of
that data is a secret on its own -- camera paths, thresholds, and LBPH
texture models aren't passwords or photos -- so it's now readable by any
local user, which is a real (if low-value) trade-off worth knowing about.
The one genuinely sensitive value, the PIN, lives separately in
`pin.json` and stays strictly root-only; `facegate-auth` never reads it.
