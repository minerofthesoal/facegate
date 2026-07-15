# Changelog

## v0.2.4 -- renamed to Visagate

**Renamed the whole project from FaceGate to Visagate.** Turns out
`facegate-git` and `facegate-bin` already exist on the AUR -- a
completely unrelated, ONNX-based face-recognition tool. The plain
`facegate` pkgbase name happened to still be unclaimed, but shipping
under it next to an already-established, similarly-named tool would be
genuinely confusing, and if anyone ever had that other package installed,
its `/usr/bin/facegate` binary would collide with ours outright. Renamed
everything, not just the package name, since binary/path collisions don't
care what the AUR package itself is called:

- Python package: `facegate` -> `visagate`
- CLI commands: `facegate`/`facegate-auth` -> `visagate`/`visagate-auth`
- Paths: `/etc/facegate` -> `/etc/visagate`, `/var/log/facegate` ->
  `/var/log/visagate`
- PAM service names: `facegate-verify` -> `visagate-verify`; the marker
  string PAM lines are matched against is now `visagate-auth`
- Fish completions/shortcut function updated and brought current (they'd
  fallen behind -- missing `camera`, `hf-upload`, `kde-passive-unlock`,
  `doctor`, `log`); the shortcut function itself was renamed from `fg` to
  `vg` since `fg` shadows the shell's own job-control builtin
- `PKGBUILD`/`pyproject.toml`/GitHub Actions workflows all updated to match

An existing v0.2.3 install's config, models, and PIN are NOT
auto-migrated to the new paths -- this is a rename, not an upgrade path,
since `/etc/facegate` and `/etc/visagate` are different locations. Run
`sudo visagate autosetup` fresh after installing this version.

**Fixed a real `install.sh` bug found while testing the AUR build:**
`install.sh` was installing `python-pam` via `pip`, while the AUR
`PKGBUILD` (correctly) declares it as a pacman dependency -- Arch ships an
official `python-pam` package. Running both on the same machine meant
pacman refused to install its own copy over pip's untracked files
("conflicting files" error). Fixed by having `install.sh` install it via
pacman too, matching `PKGBUILD`.

**Also unified the two install layouts.** `install.sh` used to manually
copy the package to `/usr/lib/visagate/` and hand-write wrapper scripts at
`/usr/bin/visagate`/`/usr/bin/visagate-auth`, while the AUR package
installs properly into site-packages with auto-generated entry-point
scripts -- two different layouts for the same software, which is exactly
what caused the `/usr/bin/*` file conflict hit while testing the AUR
build. `install.sh` now just runs `pip install --no-deps .`, producing
the identical layout the AUR package does, so a machine that's used both
installation methods can no longer collide with itself.

## v0.2.3

**Root cause found and fixed: face unlock silently never worked outside `sudo`**

This is the actual explanation for "the KDE lock screen doesn't even try"
and "it did not work" -- confirmed directly from journalctl evidence:
`pam_exec(kde:auth)` and `pam_exec(kde-fingerprint:auth)` were both being
invoked correctly (kde-passive-unlock *is* proactively polled -- the
earlier fprintd-gating caution wasn't the blocker), but `visagate-auth`
failed instantly every time with **zero entries in Visagate's own log**,
even though `sudo` attempts logged perfectly.

Root cause: `visagate-auth` runs with whatever privileges the *calling*
PAM service has. `sudo` stays fully root through its whole auth phase, so
it inherits root and can read the old root-only (0700/0600) config and
model files fine. `kscreenlocker_greet`, on the other hand, runs as your
actual logged-in user -- it's just re-confirming you're still you, no
privilege escalation involved -- so `visagate-auth` inherited that
regular user's privileges instead, hit a `PermissionError` opening
`/etc/visagate` before `main()` ever reached a single logging call, and
crashed with exit code 1 and nothing written anywhere Visagate controls.

Fixed by reworking the permission model rather than papering over it:
- `/etc/visagate` and `/etc/visagate/models` are now `0755` (world-
  traversable), `config.json` and per-user model files are now `0644`
  (world-readable). None of that data is a secret -- camera device
  paths, thresholds, and LBPH texture models aren't passwords or photos.
  Only root can write them.
- The one genuinely sensitive value, the disable/uninstall PIN hash,
  now lives in a **separate** file, `/etc/visagate/pin.json`, which
  stays strictly root-only (`0600`). `pam_helper.py` never reads it --
  only the `visagate` CLI commands that already require root do. An
  existing PIN from an older install is migrated over automatically
  (and actually stripped out of the now-world-readable config.json on
  disk, not just in memory -- caught and fixed this exact ordering bug
  in testing before it shipped).
- `/var/log/visagate` and `visagate.log` are now world-writable (sticky
  bit, `1777`/`0666`) for the same reason -- a non-root invocation
  couldn't write to the old `0750`/`0640` log either, so even after the
  config fix, lock-screen attempts would have kept showing up in
  `journalctl -t visagate` (syslog, not filesystem-permission-dependent)
  but never in `visagate log` (the file-backed view), which is exactly
  the asymmetry that made this diagnosable in the first place.
- `install.sh` now also adds the invoking user to the `video` group if
  they aren't already a member (needed to even *open* `/dev/videoN` as
  a non-root PAM context) and says plainly that this needs a fresh login
  session to take effect -- group membership doesn't refresh for an
  already-running desktop session.
- `visagate doctor` now checks all of the above directly (permission
  bits on config/models/log, video group membership) instead of only
  being discoverable by cross-referencing `visagate log` against
  `journalctl` by hand.

Trade-off worth knowing about, stated plainly rather than buried: any
local user can now read another local user's LBPH model file. It's
texture histograms, not a viewable photo, but it's still a real, if
low-value, information exposure -- accepted as the cost of a
pam_exec-based design (as opposed to a real compiled PAM module) working
at all outside `sudo`.

## v0.2.2

**Plasma 6.7.2 / kde-passive-unlock fixes**
- `visagate doctor` had a bug: its kde-fingerprint check only looked at
  `/etc/pam.d/kde-fingerprint` and never checked the vendor default at
  `/usr/lib/pam.d/kde-fingerprint`, so it reported "not present on this
  system" even though Arch's `kscreenlocker` package ships that vendor
  file (confirmed directly against the package's file list/PKGBUILD).
  `visagate kde-passive-unlock on` itself already checked the vendor
  path correctly -- only doctor's separate status line was wrong. Fixed;
  doctor now reports three distinct states: wired, vendor-default-exists-
  but-not-yet-enabled, or genuinely not shipped on this system.
- Found via real KDE bug reports (bugs.kde.org #485124 and others):
  kscreenlocker's decision to *proactively* poll the fingerprint PAM slot
  appears to depend on `fprintd` reporting an actual registered/enrolled
  device over D-Bus -- not just on the PAM file existing. This has been
  reported as unreliable even for people with genuine, correctly-enrolled
  fingerprint hardware. Practical effect: `kde-passive-unlock on` wires
  everything correctly, but kscreenlocker may still never proactively
  call it without a real fprintd device present. Making the system
  believe one exists (an fprintd D-Bus shim) would be a separate, bigger
  project with its own risks and hasn't been built -- the on-command's
  printed caveats now say this plainly instead of a generic "unverified."

## v0.2.1

**PAM targeting fix + the "doesn't even try" issue**
- Removed `/etc/pam.d/kscreenlocker-greet` from the PAM targets -- it
  turns out that's not a real PAM service name on current Plasma;
  `/etc/pam.d/kde` is the correct (and only) one, confirmed against KDE's
  own docs. Kept `/etc/pam.d/sddm` for the SDDM login screen.
- Root cause of "SDDM and the KDE lock screen don't even try to use
  face ID": both `kde` and `sddm` are the **password** PAM stacks. PAM
  only evaluates a stack once a credential is actually submitted through
  it (pressing Enter, even on an empty password field) -- it does not
  proactively scan the instant the lock screen appears, unlike a
  fingerprint reader. This was working as configured, just not as
  expected; `visagate enable`/`doctor` now print this caveat explicitly
  wherever it applies instead of leaving it to be discovered the hard way.
- Added `visagate kde-passive-unlock on|off`: **experimental**, opt-in
  only, never installed by default. Plasma 6's kscreenlocker has a
  "multiauth" feature that proactively polls a dedicated PAM service
  (`kde-fingerprint`) for fingerprint-style readers, independent of the
  password field. Wiring Visagate into that slot instead might get a
  genuinely proactive, no-Enter scan -- but whether kscreenlocker polls
  it at all without a real fingerprint reader registered is unverified,
  and mixing non-password auth into the lock screen can interact oddly
  with KWallet's automatic-unlock-on-login assumption. Command prints all
  of this before asking for confirmation.
- `visagate doctor` now distinguishes "PAM file doesn't exist and never
  will on this system" from "the vendor default exists but you haven't
  run `visagate enable` yet" instead of reporting both as a generic skip,
  plus reports the experimental kde-fingerprint wiring status separately.

**Declined: obfuscating the PAM auth code on `main`**
- Was asked to make the existing (side-branch-only) source obfuscation
  pipeline run against `main` instead, specifically for the PAM auth
  helper. Didn't do this: the obfuscation is XOR/zlib with the key
  shipped alongside the payload, so it provides no real secrecy or
  tamper-resistance, and moving it into the branch `get.sh` actually
  installs from would only cost auditability of the exact code that
  decides authentication, for no offsetting benefit. Left `main` as
  plain, readable source; the side-branch pipeline is untouched.

**Continued/incremental training**
- Already present as of v0.2.0 and unchanged here: `visagate enroll --append`
  adds new samples to an existing model via LBPH's `update()` instead of
  replacing it, so you can teach a second look (glasses, lighting, a new
  camera) without losing the original enrollment.

**Multi-camera support**
- You can now enroll and unlock with more than the primary RGB+IR pair.
  `visagate camera add` probes for additional Logitech devices (e.g. a
  second webcam with no IR sensor, like a C930), lets you pick one
  interactively or pass `--device` directly, and stores it under a new
  `extra_cameras` config list. Each extra camera gets its own LBPH model
  file and confidence threshold.
- `visagate camera list` / `visagate camera remove <id>` manage them.
- Recognition (`recognizer.authenticate`) was generalized from a
  hardcoded rgb/ir pair to loop over an arbitrary number of streams. The
  combining rule is unchanged in spirit and verified behavior-identical
  for existing 2-stream setups: a stream that never detects a face at
  all is excluded (camera/detection problem, not evidence of spoofing);
  of the streams that DID detect a face, all must match by default
  (`recognition.require_both`), or any one of them if that's set false.
- `visagate enroll [--append]` trains/updates every configured stream,
  primary and extra, in one pass.

**Optional Hugging Face backup (off by default)**
- New `visagate hf-upload on|off`. When enabled, and only for a given
  username's very first successful enrollment (never for later
  `--append` sessions), enrollment images from every configured stream
  are saved with everything outside the detected face blurred out, then
  uploaded to a Hugging Face dataset repo (default
  `ray0rf1re/faces`) via `huggingface_hub`. Visagate never stores or
  requests a token itself -- it relies on `huggingface-cli login`
  already being done, and the feature no-ops with instructions if
  `huggingface_hub` isn't installed (it's optional, not installed by
  `install.sh`). `visagate autosetup` offers this as an explicit opt-in
  prompt, defaulting to no.
- This is a real privacy tradeoff, not just a toggle -- read the prompt.
  Blurring the background reduces incidental exposure, not the fact that
  it's a photo of your face going to a remote repo you don't control the
  infrastructure of.

## v0.2.0

**Broader webcam support**
- Detection no longer assumes "Brio" specifically -- any Logitech device
  is probed, same as before, but classification is now backed by a known
  IR-capable/non-IR-capable model table (Brio 4K/500/505/MX Brio vs.
  Brio 300/100, C920/C922/C930/C925, StreamCam, BCC950, etc.) instead of
  relying purely on the saturation heuristic, which could misclassify a
  dim RGB feed as IR on hardware that never had an IR sensor. Unknown
  models still fall back to the saturation probe as before.

**Lock screen / login screen**
- Added `/etc/pam.d/sddm` as a PAM target -- this is the login screen you
  hit right after a restart, previously not wired at all.
- Added `/etc/pam.d/kscreenlocker-greet` alongside the existing `kde`
  target, since Plasma's lock screen uses different PAM service names
  across distros/versions -- both are wired (safe no-op if a given file
  doesn't apply to your system).
- New `recognition.timeout_seconds_greeter` (default 6s), used instead of
  the sudo-context `timeout_seconds` for greeter/lock-screen PAM
  services, so the lock screen doesn't sit unresponsive for the full
  sudo-context budget.
- Added a camera-busy guard (flock) so a lock screen and a sudo prompt
  triggering face checks at the same moment don't collide on the video
  device.
- Added a boot-readiness retry (`camera_wait_seconds`, default 5s) so a
  USB webcam that hasn't enumerated yet right after a cold boot gets a
  few seconds' grace instead of failing closed on the first try.

**Security**
- New lockout/cooldown: after `lockout.max_failed_attempts` (default 5)
  consecutive failures, face auth is skipped for `lockout.cooldown_seconds`
  (default 300s) and PAM falls straight to password. State lives on
  tmpfs (`/run/visagate`) and resets on reboot. `sudo visagate enable`
  clears any active cooldown.

**Logging**
- New `visagate/logging_setup.py`: auth attempts are now written to
  `/var/log/visagate/visagate.log` (rotating, 5x2MB) in addition to
  syslog, and `visagate log` shows recent entries directly without
  needing `journalctl` syntax.

**New commands**
- `visagate doctor` -- one-shot health check: camera detected, RGB/IR
  configured, models enrolled, PAM wiring, lockout state, log dir.
- `visagate log [-n N]` -- show recent auth attempts.
- `visagate --version`.
- `autosetup`/`enable` now print the detected display manager.

**Known limitation, stated honestly:** the lock-screen/login-screen PAM
wiring above targets the PAM service names these greeters *should* use;
whether `pam_exec` actually fires cleanly from a given greeter still
depends on your specific Plasma/SDDM version and distro packaging, and
isn't something that can be verified without testing on the target
machine. Run `sudo visagate doctor` after `enable`, then test an actual
lock/restart cycle and check `sudo visagate log` / `sudo journalctl -t
visagate -e` if it doesn't fire.
