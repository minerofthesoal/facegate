#!/usr/bin/env python3
import argparse
import datetime
import getpass
import os
import shutil
import sys
import time

from . import __version__, camera, config, hf_upload, recognizer, security

PAM_MARKER = "visagate-auth"
PAM_LINE = f"auth    sufficient   pam_exec.so quiet /usr/bin/{PAM_MARKER}\n"

# target PAM file -> vendor-default fallback to seed it from, if the target
# doesn't exist yet. Arch (and other systemd-based distros) ship many
# service defaults under /usr/lib/pam.d/ rather than /etc/pam.d/; PAM reads
# /etc/pam.d/<service> if present, otherwise falls back to the vendor copy.
# To add our line we need a real file under /etc/pam.d/, so if only the
# vendor copy exists we seed /etc/pam.d/<service> from it first.
#
# v0.2.0: added sddm (the login-manager screen you hit right after a
# restart) alongside the existing "kde" (Plasma's kscreenlocker password
# stack). v0.2.1: removed a "kscreenlocker-greet" entry that turned out
# not to be a real PAM service name on current Plasma -- "kde" is the
# correct/only one confirmed by KDE's own docs. Note that both "kde" and
# "sddm" are password stacks: PAM only evaluates them once a credential
# is actually submitted (Enter, even on an empty field), not proactively
# the instant the screen appears -- see SUBMIT_REQUIRED_TARGETS and
# EXPERIMENTAL_PAM_TARGETS below for the one avenue that can be proactive.
PAM_TARGETS = {
    "/etc/pam.d/sudo": None,
    "/etc/pam.d/login": None,
    "/etc/pam.d/kde": "/usr/lib/pam.d/kde",  # KDE Plasma's kscreenlocker, password stack
    "/etc/pam.d/sddm": "/usr/lib/pam.d/sddm",  # SDDM login screen, i.e. right after a restart
}

# IMPORTANT CAVEAT, learned the hard way: the two lock/login-screen entries
# above ("kde", "sddm") are the PASSWORD auth stacks. PAM only evaluates a
# stack when the calling app actually submits a credential through it --
# for kscreenlocker/SDDM that means pressing Enter (even on an empty
# password field), not the instant the lock screen appears. So face
# recognition here is "hit Enter, then get scanned," not a fully passive
# Windows-Hello-style scan. See EXPERIMENTAL_PAM_TARGETS below for the one
# avenue that can be genuinely proactive.
SUBMIT_REQUIRED_TARGETS = {"/etc/pam.d/kde", "/etc/pam.d/sddm"}

# EXPERIMENTAL, opt-in only (visagate kde-passive-unlock on): Plasma 6's
# kscreenlocker has a "multiauth" feature that proactively polls a
# SEPARATE PAM service per credential type -- "kde" for password, and
# "kde-fingerprint" for fingerprint readers -- without waiting for the
# password field to be submitted. If we put Visagate's line there instead
# of/alongside "kde", kscreenlocker may attempt face recognition the
# instant the screen locks, no Enter needed.
#
# Confirmed via Arch's kscreenlocker package: it ships kde-fingerprint as
# a vendor PAM file under /usr/lib/pam.d/, so the file existing usually
# isn't the blocker. What's NOT confirmed, and looks genuinely shaky per
# real KDE bug reports (e.g. bugs.kde.org #485124, people with real,
# correctly-enrolled fingerprint readers sometimes never seeing the
# prompt at all): kscreenlocker appears to decide whether to proactively
# poll this slot based on fprintd reporting an actual registered/enrolled
# device over D-Bus, not just on the PAM file existing. Without a real
# fprintd device, this may simply never fire, independent of anything
# Visagate does. Making the system believe a fingerprint device exists
# (an fprintd D-Bus shim) would be a much bigger, separate project with
# its own risks (conflicting with a real reader, poking at another
# daemon's identity) -- not implemented here; ask explicitly if that
# tradeoff is still worth it to you.
EXPERIMENTAL_PAM_TARGETS = {
    "/etc/pam.d/kde-fingerprint": "/usr/lib/pam.d/kde-fingerprint",
}

GREETER_SERVICES = {"sddm", "sddm-greeter", "kde", "kde-np", "kde-fingerprint"}


def detect_display_manager():
    """Best-effort name of the active display manager (sddm/gdm/lightdm/...),
    via the systemd display-manager.service symlink. Informational only --
    used to tell the user which lock-screen PAM file is actually relevant
    to them, not to gate anything. New in v0.2.0."""
    try:
        target = os.path.realpath("/etc/systemd/system/display-manager.service")
        name = os.path.basename(target).replace(".service", "")
        return name or None
    except OSError:
        return None


def require_root():
    if os.geteuid() != 0:
        print("This command must be run as root (use sudo).")
        sys.exit(1)


def cmd_autosetup(args):
    require_root()
    print("== Visagate autosetup ==")
    dm = detect_display_manager()
    print(f"Detected display manager: {dm or 'unknown'}")
    print("Scanning for Logitech camera devices (v4l2-ctl)...")
    rgb, ir, all_devs = camera.auto_detect()

    if not all_devs:
        print("No Brio/Logitech devices found.")
        print("Check that:")
        print("  - v4l-utils is installed (pacman -S v4l-utils)")
        print("  - the webcam is plugged in")
        print("  - `v4l2-ctl --list-devices` shows it at all")
        sys.exit(1)

    print("\nDevices found:")
    for d in all_devs:
        kind = "IR (guess)" if d["is_ir"] else "RGB (guess)"
        print(f"  {d['path']}  {d['width']}x{d['height']}  avg_sat={d['avg_saturation']}  -> {kind}")

    if not rgb and not ir:
        print("\nCould not confidently classify any stream as RGB or IR.")
        print("Edit /etc/visagate/config.json manually to set camera.rgb_device / camera.ir_device.")
        sys.exit(1)

    cfg = config.load()
    cfg["camera"]["rgb_device"] = rgb["path"] if rgb else None
    cfg["camera"]["ir_device"] = ir["path"] if ir else None
    cfg["camera"]["auto_detected"] = True
    config.save(cfg)

    security.ensure_verify_service()

    print(f"\nSelected RGB device: {cfg['camera']['rgb_device']}")
    print(f"Selected IR device:  {cfg['camera']['ir_device']}")
    if not ir:
        print("Note: no IR stream detected. Your Brio unit may not have IR, or it")
        print("needs a different capture mode. Visagate will run RGB-only.")

    used_paths = {cfg["camera"].get("rgb_device"), cfg["camera"].get("ir_device")}
    used_paths.discard(None)
    other_candidates = camera.probe_candidates(exclude_paths=used_paths)
    if other_candidates:
        print("\nFound another camera not used above:")
        for i, c in enumerate(other_candidates):
            guess = "IR (guess)" if c["is_ir"] else "RGB (guess)"
            print(f"  [{i}] {c['path']}  {c.get('description')}  {c['width']}x{c['height']}  -> {guess}")
        add_answer = input(
            "Add one of these as an additional camera for training/unlocking? [y/N]: "
        ).strip().lower()
        if add_answer == "y":
            choice = input("Pick a device by number: ").strip()
            try:
                chosen = other_candidates[int(choice)]
                kind = "ir" if chosen["is_ir"] else "rgb"
                cam_id = f"cam2_{kind}"
                threshold = 65 if kind == "ir" else 60
                cfg.setdefault("extra_cameras", []).append(
                    {"id": cam_id, "device": chosen["path"], "kind": kind, "threshold": threshold}
                )
                config.save(cfg)
                print(f"Added '{cam_id}' ({chosen['path']}). It'll be enrolled along with the rest below.")
            except (ValueError, IndexError):
                print("Invalid choice -- skipping. You can add one later with 'visagate camera add'.")

    hf_enabled_this_run = False
    print(
        "\nOptionally back up this first enrollment's face images to your Hugging Face "
        f"dataset repo ('{cfg['hf_upload']['repo_id']}'). This is OFF by default, uploads "
        "ONLY this one time (never for later re-enrollments), and blurs everything outside "
        "your face before uploading -- but it does upload real photos of your face to a "
        "remote service. Requires 'huggingface-cli login' already done on this machine."
    )
    hf_answer = input("Enable this? [y/N]: ").strip().lower()
    if hf_answer == "y":
        if not hf_upload.is_available():
            print("huggingface_hub isn't installed. Install it and re-run to use this:")
            print("  pip install --break-system-packages huggingface_hub")
        else:
            cfg["hf_upload"]["enabled"] = True
            config.save(cfg)
            hf_enabled_this_run = True
            print("Enabled. Your first enrollment below will be backed up.")

    answer = input("\nType 'yes' to begin face enrollment now: ").strip().lower()
    if answer != "yes":
        print("Setup paused. Run 'sudo visagate enroll' when you're ready.")
        return

    username = os.environ.get("SUDO_USER") or getpass.getuser()
    cfg = config.load()
    already_uploaded = username in cfg["hf_upload"].get("uploaded_users", [])
    should_collect = hf_enabled_this_run and not already_uploaded
    result = _do_enroll(username, cfg, collect_for_upload=should_collect)

    if should_collect and result.get("_raw_samples"):
        print("\nUploading blurred enrollment images to Hugging Face...")
        try:
            uploaded = hf_upload.save_and_upload(
                username, result["_raw_samples"], repo_id=cfg["hf_upload"]["repo_id"]
            )
            cfg = config.load()
            cfg["hf_upload"].setdefault("uploaded_users", [])
            if username not in cfg["hf_upload"]["uploaded_users"]:
                cfg["hf_upload"]["uploaded_users"].append(username)
            config.save(cfg)
            print(f"Uploaded {len(uploaded)} image(s).")
        except Exception as e:
            print(f"Hugging Face upload failed (enrollment itself still succeeded): {e}")

    attempts_input = input(
        "\nHow many face-recognition attempts before falling back to your "
        "password? [default 2]: "
    ).strip()
    try:
        attempts = int(attempts_input) if attempts_input else 2
    except ValueError:
        attempts = 2
    cfg = config.load()
    cfg["recognition"]["max_attempts"] = max(1, attempts)
    config.save(cfg)
    print(f"Will try face recognition {cfg['recognition']['max_attempts']} time(s) before asking for your password.")

    print("\nSet a PIN you can use later to disable face unlock without your full")
    print("sudo password (sudo password will also always work).")
    pin = getpass.getpass("New PIN: ")
    confirm = getpass.getpass("Confirm PIN: ")
    if pin != confirm:
        print("PINs did not match. Run 'sudo visagate set-pin' to try again.")
    else:
        security.set_pin(pin)
        print("PIN saved.")

    _install_pam(interactive=True)

    cfg = config.load()
    cfg["enabled"] = True
    config.save(cfg)
    print("\nVisagate is enabled. Test it with: sudo -k && sudo true")
    print("(If it doesn't recognize you, it silently falls back to your password.)")


def _do_enroll(username, cfg, append=False, collect_for_upload=False):
    print(f"Enrolling face for user '{username}'.")
    if append:
        print("Appending new samples to the existing model (previous samples are kept).")
    print("Look directly at the camera and move your head slightly during capture.")
    try:
        result = recognizer.enroll_user(
            username,
            cfg["camera"]["rgb_device"],
            cfg["camera"]["ir_device"],
            append=append,
            extra_cameras=cfg.get("extra_cameras", []),
            collect_for_upload=collect_for_upload,
        )
    except RuntimeError as e:
        print(f"Enrollment failed: {e}")
        sys.exit(1)
    printable = {k: v for k, v in result.items() if k != "_raw_samples"}
    print(f"Enrollment complete: {printable}")
    if result.get("rgb_used_ir_fallback"):
        print(
            "NOTE: the RGB model was trained from IR-detected face crops because "
            "not enough RGB samples were captured. Two-stream verification is "
            "weaker for this user until you re-run 'sudo visagate enroll' with "
            "working RGB capture (check lighting / camera angle)."
        )
    return result


def cmd_enroll(args):
    require_root()
    cfg = config.load()
    username = args.user or os.environ.get("SUDO_USER") or getpass.getuser()
    _do_enroll(username, cfg, append=args.append)


def cmd_test(args):
    username = args.user or os.environ.get("SUDO_USER") or getpass.getuser()
    print(f"Testing recognition for '{username}' (this will NOT unlock or change anything)...")
    ok, info = recognizer.authenticate(username)
    print(f"Result: {'MATCH' if ok else 'NO MATCH'}")
    print(f"Raw LBPH confidences (lower = better match): "
          f"rgb={info.get('rgb_conf')} ir={info.get('ir_conf')} extra={info.get('extra_confs')}")
    excluded = info.get("excluded_streams") or []
    if excluded:
        print(
            f"NOTE: these streams never detected a face at all this attempt, so they "
            f"were excluded rather than counted as a failure: {excluded}"
        )
    considered = info.get("considered_streams") or []
    if considered:
        print(f"Streams that were actually used to decide the result: {considered}")


def _install_pam(interactive=True):
    installed_any = False
    for pam_file, vendor_fallback in PAM_TARGETS.items():
        if not os.path.exists(pam_file):
            if vendor_fallback and os.path.exists(vendor_fallback):
                if interactive:
                    print(f"\n{pam_file} doesn't exist yet; found vendor default at {vendor_fallback}.")
                    confirm = input(f"Create {pam_file} from that vendor default? [y/N]: ").strip().lower()
                    if confirm != "y":
                        print(f"Skipped {pam_file}.")
                        continue
                shutil.copy2(vendor_fallback, pam_file)
                print(f"Created {pam_file} from {vendor_fallback}.")
            else:
                continue  # no such service on this system, nothing to do

        with open(pam_file) as f:
            content = f.read()
        lines = content.splitlines(keepends=True)
        marker_idxs = [i for i, l in enumerate(lines) if PAM_MARKER in l]

        if marker_idxs:
            if all(lines[i] == PAM_LINE for i in marker_idxs):
                print(f"{pam_file}: already configured, skipping.")
                installed_any = True
                continue
            # A Visagate line exists but doesn't match the current PAM_LINE --
            # most likely an older version (e.g. the expose_authtok flag that
            # used to force a password prompt before face auth even ran).
            # Repair it in place rather than leaving the stale behavior.
            if interactive:
                print(f"\n{pam_file} has an outdated Visagate line:")
                for i in marker_idxs:
                    print("  - " + lines[i].strip())
                print("Replacing with:")
                print("  + " + PAM_LINE.strip())
                confirm = input("Proceed? [y/N]: ").strip().lower()
                if confirm != "y":
                    print(f"Skipped {pam_file}.")
                    continue
            backup = pam_file + f".visagate.bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(pam_file, backup)
            for i in marker_idxs:
                lines[i] = PAM_LINE
            with open(pam_file, "w") as f:
                f.writelines(lines)
            print(f"{pam_file} repaired. Backup: {backup}")
            installed_any = True
            continue

        if interactive:
            print(f"\nAbout to insert a Visagate auth line into {pam_file}:")
            print("  " + PAM_LINE.strip())
            confirm = input("Proceed? [y/N]: ").strip().lower()
            if confirm != "y":
                print(f"Skipped {pam_file}.")
                continue
        backup = pam_file + f".visagate.bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(pam_file, backup)
        lines.insert(0, PAM_LINE)
        with open(pam_file, "w") as f:
            f.writelines(lines)
        print(f"{pam_file} updated. Backup: {backup}")
        if pam_file in SUBMIT_REQUIRED_TARGETS:
            print(
                "  NOTE: this is the password stack, so it's only checked once you submit "
                "the field (press Enter, even with it blank) -- not the instant the screen "
                "appears. See 'visagate kde-passive-unlock' for an experimental proactive option."
            )
        installed_any = True
    if not installed_any:
        print("No PAM files were modified.")


def cmd_kde_passive_unlock(args):
    require_root()
    pam_file = "/etc/pam.d/kde-fingerprint"
    vendor_fallback = EXPERIMENTAL_PAM_TARGETS[pam_file]

    if args.state == "off":
        if not os.path.exists(pam_file):
            print(f"{pam_file} doesn't exist -- nothing to remove.")
            return
        with open(pam_file) as f:
            lines = f.readlines()
        new_lines = [l for l in lines if PAM_MARKER not in l]
        if new_lines != lines:
            with open(pam_file, "w") as f:
                f.writelines(new_lines)
            print(f"Removed Visagate's line from {pam_file}.")
        else:
            print(f"Visagate wasn't wired into {pam_file}.")
        return

    print(
        "EXPERIMENTAL: this wires Visagate into kscreenlocker's fingerprint-auth PAM\n"
        "service instead of the password one, so it MAY be checked proactively as soon\n"
        "as the screen locks -- no Enter required -- rather than only on submission.\n"
        "Caveats:\n"
        "  - The PAM file existing usually isn't the blocker (Arch's kscreenlocker\n"
        "    package ships it). What's NOT confirmed: kscreenlocker appears to decide\n"
        "    whether to proactively poll this slot based on fprintd reporting an\n"
        "    actual registered fingerprint device -- real KDE bug reports show even\n"
        "    people with genuine, correctly-enrolled fingerprint readers sometimes\n"
        "    never get the prompt. Without a real fprintd device, this may just never\n"
        "    fire, regardless of anything below succeeding.\n"
        "  - Mixing non-password auth into the lock screen can interact oddly with\n"
        "    KWallet's automatic-unlock-on-login, which assumes a real password login.\n"
        "  - After enabling, lock your screen and check 'sudo visagate log' or\n"
        "    'sudo journalctl -t visagate -e' to see whether it was invoked at all.\n"
    )
    confirm = input("Proceed anyway? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Not enabled.")
        return

    if not os.path.exists(pam_file):
        if not os.path.exists(vendor_fallback):
            print(
                f"Neither {pam_file} nor its vendor default {vendor_fallback} exist on "
                "this system -- your kscreenlocker version may not support the "
                "fingerprint multiauth slot. Nothing changed."
            )
            return
        shutil.copy2(vendor_fallback, pam_file)
        print(f"Created {pam_file} from {vendor_fallback}.")

    with open(pam_file) as f:
        content = f.read()
    if PAM_MARKER in content:
        print(f"{pam_file}: already configured.")
        return
    backup = pam_file + f".visagate.bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(pam_file, backup)
    lines = content.splitlines(keepends=True)
    lines.insert(0, PAM_LINE)
    with open(pam_file, "w") as f:
        f.writelines(lines)
    print(f"{pam_file} updated. Backup: {backup}")
    print("Lock your screen now and check 'sudo visagate log' to see if it triggered.")


def cmd_enable(args):
    require_root()
    security.ensure_verify_service()
    _install_pam(interactive=True)
    security.clear_lockout()
    cfg = config.load()
    cfg["enabled"] = True
    config.save(cfg)
    print("Face unlock enabled.")


def cmd_disable(args):
    require_root()
    if not security.confirm_privileged_action("Disabling face unlock requires confirmation."):
        print("Confirmation failed. Face unlock remains enabled.")
        sys.exit(1)
    cfg = config.load()
    cfg["enabled"] = False
    config.save(cfg)
    print("Face unlock disabled. Only your normal password will be accepted.")


def cmd_set_pin(args):
    require_root()
    if config.load().get("pin_hash"):
        if not security.confirm_privileged_action("Changing your PIN requires confirmation."):
            print("Confirmation failed.")
            sys.exit(1)
    pin = getpass.getpass("New PIN: ")
    confirm = getpass.getpass("Confirm PIN: ")
    if pin != confirm:
        print("PINs did not match.")
        sys.exit(1)
    security.set_pin(pin)
    print("PIN updated.")


def cmd_set_attempts(args):
    require_root()
    if args.count < 1:
        print("Attempts must be at least 1.")
        sys.exit(1)
    cfg = config.load()
    cfg["recognition"]["max_attempts"] = args.count
    config.save(cfg)
    print(f"Will try face recognition {args.count} time(s) before falling back to your password.")


def cmd_relax(args):
    """Make matching more permissive: raise LBPH confidence thresholds
    (LBPH confidence is a distance -- lower is a better match -- so a
    HIGHER threshold accepts weaker/less-perfect matches) and/or lower
    min_face_size so faces are detected from farther away or at an angle.
    Trades some false-reject reduction for a slightly higher false-accept
    risk; this is a meaningful security/convenience tradeoff, not just a
    UX tweak, so it prints the before/after values rather than doing it
    silently.
    """
    require_root()
    cfg = config.load()
    rec = cfg["recognition"]
    before = dict(rec)

    if args.rgb_threshold is not None:
        rec["confidence_threshold_rgb"] = args.rgb_threshold
    if args.ir_threshold is not None:
        rec["confidence_threshold_ir"] = args.ir_threshold
    if args.min_face_size is not None:
        rec["min_face_size"] = args.min_face_size

    if not any([args.rgb_threshold, args.ir_threshold, args.min_face_size]):
        # No explicit values given -> apply a sensible one-step loosening.
        rec["confidence_threshold_rgb"] = min(100, before["confidence_threshold_rgb"] + 15)
        rec["confidence_threshold_ir"] = min(100, before["confidence_threshold_ir"] + 15)
        rec["min_face_size"] = max(40, before["min_face_size"] - 20)

    config.save(cfg)
    print("Recognition is now more permissive:")
    print(
        f"  confidence_threshold_rgb: {before['confidence_threshold_rgb']} -> {rec['confidence_threshold_rgb']}"
    )
    print(
        f"  confidence_threshold_ir:  {before['confidence_threshold_ir']} -> {rec['confidence_threshold_ir']}"
    )
    print(f"  min_face_size:            {before['min_face_size']} -> {rec['min_face_size']}")
    print(
        "\nNote: higher thresholds and smaller min_face_size make it easier for YOU to "
        "pass, but also easier for a false match. Run 'sudo visagate test' to sanity-check."
    )


def cmd_diag(args):
    import cv2

    print("Probing all detected Brio/Logitech devices for ~3 seconds each...\n")
    candidates = camera.find_brio_devices()
    if not candidates:
        print("No Brio/Logitech devices found via v4l2-ctl.")
        return
    cfg = config.load()
    min_face_size = cfg["recognition"].get("min_face_size", 80)
    detector = recognizer._detector()
    for path in candidates:
        print(f"=== {path} ===")
        cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        if not cap.isOpened():
            print("  Could not open device.\n")
            continue
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc = "".join(chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)) if fourcc_int else "?"
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames_read = 0
        detections = 0
        brightness_sum = 0.0
        start = time.time()
        while time.time() - start < 3:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            frames_read += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            brightness_sum += float(gray.mean())
            found = detector.detectMultiScale(gray, 1.2, 5, minSize=(min_face_size, min_face_size))
            if len(found):
                detections += 1
        cap.release()
        avg_brightness = brightness_sum / frames_read if frames_read else float("nan")
        print(f"  resolution: {w}x{h}   fourcc: {fourcc}")
        print(f"  frames read in 3s: {frames_read}")
        print(f"  avg brightness (0-255): {avg_brightness:.1f}")
        print(f"  frames with a detected face (min_face_size={min_face_size}): {detections}/{frames_read}")
        if frames_read == 0:
            print("  -> device isn't delivering frames at all.")
        elif detections == 0:
            print("  -> frames are fine but no face was ever detected here. Move closer / "
                  "improve lighting / lower recognition.min_face_size.")
        print()


def cmd_status(args):
    cfg = config.load()
    print(f"Enabled:      {cfg['enabled']}")
    print(f"RGB device:   {cfg['camera']['rgb_device']}")
    print(f"IR device:    {cfg['camera']['ir_device']}")
    extras = cfg.get("extra_cameras", [])
    if extras:
        print("Extra cameras:")
        for c in extras:
            print(f"  - {c['id']}: {c['device']} (kind={c.get('kind')}, threshold={c.get('threshold')})")
    else:
        print("Extra cameras: (none)")
    print(f"Require all detecting streams to match: {cfg['recognition']['require_both']}")
    print(f"Max attempts before password fallback: {cfg['recognition']['max_attempts']}")
    print(f"PIN set:      {bool(cfg.get('pin_hash'))}")
    hf = cfg.get("hf_upload", {})
    print(
        f"Hugging Face upload: enabled={hf.get('enabled', False)} repo={hf.get('repo_id')} "
        f"already-uploaded-users={hf.get('uploaded_users', [])}"
    )
    enrolled = []
    if os.path.isdir(config.MODEL_DIR):
        for fn in sorted(os.listdir(config.MODEL_DIR)):
            enrolled.append(fn)
    print(f"Model files:  {enrolled or '(none)'}")


def cmd_camera_list(args):
    cfg = config.load()
    print("Primary pair:")
    print(f"  rgb: {cfg['camera']['rgb_device']}")
    print(f"  ir:  {cfg['camera']['ir_device']}")
    extras = cfg.get("extra_cameras", [])
    print(f"Extra cameras ({len(extras)}):")
    for c in extras:
        print(f"  - id={c['id']}  device={c['device']}  kind={c.get('kind')}  threshold={c.get('threshold')}")
    if not extras:
        print("  (none)")


def cmd_camera_add(args):
    require_root()
    cfg = config.load()
    used_paths = {cfg["camera"].get("rgb_device"), cfg["camera"].get("ir_device")}
    used_paths |= {c["device"] for c in cfg.get("extra_cameras", [])}
    used_paths.discard(None)

    device = args.device
    kind = args.kind

    if not device:
        print("Probing for additional Logitech devices not already in use...")
        candidates = camera.probe_candidates(exclude_paths=used_paths)
        if not candidates:
            print("No unused Logitech devices found. Plug in the camera and try again, "
                  "or pass --device /dev/videoN directly.")
            sys.exit(1)
        for i, c in enumerate(candidates):
            guess = "IR (guess)" if c["is_ir"] else "RGB (guess)"
            print(f"  [{i}] {c['path']}  {c.get('description')}  {c['width']}x{c['height']}  -> {guess}"
                  f"  (classified_by={c['classified_by']})")
        choice = input("Pick a device by number: ").strip()
        try:
            chosen = candidates[int(choice)]
        except (ValueError, IndexError):
            print("Invalid choice.")
            sys.exit(1)
        device = chosen["path"]
        if kind is None:
            kind = "ir" if chosen["is_ir"] else "rgb"

    if kind is None:
        kind = "rgb"

    default_id = f"cam{len(cfg.get('extra_cameras', [])) + 2}_{kind}"
    cam_id = args.id or default_id
    existing_ids = {c["id"] for c in cfg.get("extra_cameras", [])} | {"rgb", "ir"}
    if cam_id in existing_ids:
        print(f"id '{cam_id}' is already in use. Pick a different --id.")
        sys.exit(1)

    threshold = args.threshold if args.threshold is not None else (65 if kind == "ir" else 60)
    entry = {"id": cam_id, "device": device, "kind": kind, "threshold": threshold}
    cfg.setdefault("extra_cameras", []).append(entry)
    config.save(cfg)
    print(f"Added camera '{cam_id}': {device} (kind={kind}, threshold={threshold}).")
    print(f"Run 'sudo visagate enroll --append' to train this camera without disturbing "
          f"your existing enrolled streams.")


def cmd_camera_remove(args):
    require_root()
    cfg = config.load()
    cam_id = args.id
    if cam_id in ("rgb", "ir"):
        cfg["camera"][f"{cam_id}_device"] = None
        config.save(cfg)
        print(f"Cleared the primary '{cam_id}' device from config. "
              f"(Its model file, if any, is left in {config.MODEL_DIR}.)")
        return
    extras = cfg.get("extra_cameras", [])
    remaining = [c for c in extras if c["id"] != cam_id]
    if len(remaining) == len(extras):
        print(f"No extra camera with id '{cam_id}' found. Run 'visagate camera list' to check.")
        sys.exit(1)
    cfg["extra_cameras"] = remaining
    config.save(cfg)
    print(f"Removed camera '{cam_id}' from config. Model files under {config.MODEL_DIR} "
          f"matching '*_{cam_id}.yml' are left in place -- delete manually if you want them gone.")


def cmd_hf_upload(args):
    require_root()
    cfg = config.load()
    if args.state == "on":
        if not hf_upload.is_available():
            print("huggingface_hub isn't installed. Install it first:")
            print("  pip install --break-system-packages huggingface_hub")
            print("...and make sure you're logged in: huggingface-cli login")
            sys.exit(1)
        cfg["hf_upload"]["enabled"] = True
        config.save(cfg)
        print("Hugging Face upload enabled.")
        print("This only ever uploads images from a user's very first successful enrollment --")
        print("run 'sudo visagate enroll' for any username not yet in hf_upload.uploaded_users")
        print("(check with 'visagate status') to trigger it; later --append sessions never upload.")
    else:
        cfg["hf_upload"]["enabled"] = False
        config.save(cfg)
        print("Hugging Face upload disabled.")


def cmd_uninstall(args):
    require_root()
    if not security.confirm_privileged_action("Uninstalling Visagate requires confirmation."):
        sys.exit(1)
    for pam_file in PAM_TARGETS:
        if not os.path.exists(pam_file):
            continue
        with open(pam_file) as f:
            lines = f.readlines()
        new_lines = [l for l in lines if PAM_MARKER not in l]
        if new_lines != lines:
            with open(pam_file, "w") as f:
                f.writelines(new_lines)
            print(f"Removed Visagate line from {pam_file}")
    print("PAM integration removed.")
    print(f"Face model files are still in {config.MODEL_DIR} -- delete manually if you want them gone too.")


def cmd_doctor(args):
    """Health check: camera, PAM wiring, enrolled models, lockout state,
    logging. New in v0.2.0."""
    require_root()
    from . import logging_setup

    print("== Visagate doctor ==")
    ok_all = True

    def check(label, ok, detail=""):
        nonlocal ok_all
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            ok_all = False

    cfg = config.load()
    check("face unlock enabled", cfg.get("enabled"), "" if cfg.get("enabled") else "run 'sudo visagate enable'")

    dm = detect_display_manager()
    print(f"     display manager: {dm or 'unknown'}")

    devs = camera.list_video_devices()
    logi = sorted({d for d, desc in devs.items() if desc and "logitech" in desc.lower()})
    check("Logitech camera(s) detected", bool(logi), ", ".join(logi) if logi else "none found via v4l2-ctl")

    rgb = cfg["camera"].get("rgb_device")
    ir = cfg["camera"].get("ir_device")
    check("RGB device configured", bool(rgb), rgb or "none -- run 'sudo visagate autosetup'")
    check("IR device configured", bool(ir), ir or "none (RGB-only mode -- fine if your webcam has no IR sensor)")

    username = os.environ.get("SUDO_USER") or getpass.getuser()
    rgb_model = os.path.join(config.MODEL_DIR, f"{username}_rgb.yml")
    ir_model = os.path.join(config.MODEL_DIR, f"{username}_ir.yml")
    if rgb:
        check(f"RGB model enrolled ({username})", os.path.exists(rgb_model))
    if ir:
        check(f"IR model enrolled ({username})", os.path.exists(ir_model))

    # v0.2.2: visagate-auth runs as whatever user the calling PAM service
    # runs as -- root for sudo, but the actual logged-in user for
    # kscreenlocker. If config/models/log aren't world-readable/writable,
    # non-root invocations fail before ever reaching a log call, which
    # looks exactly like "silently does nothing" from the lock screen.
    import stat

    def _other_perm_ok(path, need_write=False):
        try:
            mode = os.stat(path).st_mode
        except OSError:
            return None
        bit = stat.S_IWOTH if need_write else stat.S_IROTH
        return bool(mode & bit)

    perm_checks = [
        (config.CONFIG_DIR, False, "world-traversable (0755)"),
        (config.CONFIG_FILE, False, "world-readable (0644)"),
        (config.MODEL_DIR, False, "world-traversable (0755)"),
    ]
    for path, need_write, label in perm_checks:
        result = _other_perm_ok(path, need_write)
        if result is None:
            continue
        check(f"{path} is {label}", result,
              "" if result else "run 'sudo visagate enable' or 'sudo visagate doctor' again to self-heal")
    for fn in (os.listdir(config.MODEL_DIR) if os.path.isdir(config.MODEL_DIR) else []):
        p = os.path.join(config.MODEL_DIR, fn)
        if not _other_perm_ok(p):
            check(f"model file world-readable: {p}", False, "run 'sudo visagate doctor' again to self-heal")
    if os.path.isdir(logging_setup.LOG_DIR):
        check(f"{logging_setup.LOG_DIR} is world-writable (sticky 1777)",
              bool(_other_perm_ok(logging_setup.LOG_DIR, need_write=True)))

    if username != "root":
        try:
            import grp
            video_members = grp.getgrnam("video").gr_mem
            in_video = username in video_members
        except (KeyError, ImportError):
            in_video = None
        if in_video is not None:
            check(f"{username} is in the 'video' group", in_video,
                  "" if in_video else f"run: sudo usermod -aG video {username}  (then log out/in)")

    for pam_file, vendor_fallback in PAM_TARGETS.items():
        if not os.path.exists(pam_file):
            if vendor_fallback and os.path.exists(vendor_fallback):
                print(f"[FAIL] PAM wired: {pam_file} -- not created yet, run 'sudo visagate enable'")
                ok_all = False
            else:
                print(f"[skip] PAM file not present: {pam_file} (not applicable on this system)")
            continue
        with open(pam_file) as f:
            wired = PAM_MARKER in f.read()
        note = " (password stack -- only checked once submitted, see README)" if pam_file in SUBMIT_REQUIRED_TARGETS else ""
        check(f"PAM wired: {pam_file}{note}", wired)

    exp_file = "/etc/pam.d/kde-fingerprint"
    exp_vendor = EXPERIMENTAL_PAM_TARGETS[exp_file]
    if os.path.exists(exp_file):
        with open(exp_file) as f:
            exp_wired = PAM_MARKER in f.read()
        print(f"[{'OK  ' if exp_wired else 'off '}] experimental kde-passive-unlock: {exp_file}"
              + (" -- wired" if exp_wired else " -- not enabled ('visagate kde-passive-unlock on' to try it)"))
    elif os.path.exists(exp_vendor):
        print(f"[off ] experimental kde-passive-unlock: vendor default exists at {exp_vendor} but "
              f"{exp_file} hasn't been created yet -- 'visagate kde-passive-unlock on' to try it")
    else:
        print(f"[skip] experimental kde-passive-unlock: neither {exp_file} nor {exp_vendor} exist "
              f"on this system (kscreenlocker version may not ship it)")

    locked, remaining = security.is_locked_out()
    check("not currently locked out", not locked, f"{remaining}s remaining" if locked else "")

    try:
        os.makedirs(logging_setup.LOG_DIR, exist_ok=True)
        check("log directory writable", os.access(logging_setup.LOG_DIR, os.W_OK))
    except PermissionError:
        check("log directory writable", False)

    print("\nAll checks passed." if ok_all else "\nSome checks failed -- see above.")


def cmd_log(args):
    """Show recent auth attempts from the Visagate log file. New in v0.2.0."""
    from . import logging_setup

    try:
        with open(logging_setup.LOG_FILE) as f:
            lines = f.readlines()[-args.n :]
    except FileNotFoundError:
        print(f"No log file yet at {logging_setup.LOG_FILE}.")
        print("Either nothing has run through PAM yet, or you're on syslog-only:")
        print("  sudo journalctl -t visagate -e")
        return
    except PermissionError:
        print(f"Permission denied reading {logging_setup.LOG_FILE} -- try with sudo.")
        return
    for line in lines:
        print(line.rstrip())



def main():
    parser = argparse.ArgumentParser(
        prog="visagate",
        description="Face unlock for Logitech webcams on Arch Linux (Howdy-style, RGB+IR where available).",
    )
    parser.add_argument("--version", action="version", version=f"visagate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("autosetup", help="Detect camera, enroll your face, wire up PAM").set_defaults(
        func=cmd_autosetup
    )

    p = sub.add_parser("enroll", help="(Re)register a face")
    p.add_argument("--user", help="username to enroll (default: current user)")
    p.add_argument(
        "--append",
        action="store_true",
        help="Add new samples to the existing model instead of replacing it "
        "(e.g. re-run wearing glasses to teach both looks)",
    )
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("test", help="Test recognition without touching auth/config")
    p.add_argument("--user")
    p.set_defaults(func=cmd_test)

    sub.add_parser(
        "diag", help="Probe all detected camera devices and report frame/face-detection stats"
    ).set_defaults(func=cmd_diag)

    sub.add_parser("enable", help="(Re)enable face unlock and PAM hook").set_defaults(func=cmd_enable)
    sub.add_parser(
        "disable", help="Disable face unlock (requires sudo password or PIN)"
    ).set_defaults(func=cmd_disable)
    sub.add_parser("set-pin", help="Set or change the disable/uninstall PIN").set_defaults(
        func=cmd_set_pin
    )
    sub.add_parser("status", help="Show current configuration").set_defaults(func=cmd_status)

    p = sub.add_parser(
        "set-attempts", help="Set how many face-match attempts before falling back to password"
    )
    p.add_argument("count", type=int)
    p.set_defaults(func=cmd_set_attempts)

    p = sub.add_parser(
        "relax",
        help="Make matching more permissive (raise confidence thresholds, lower min_face_size)",
    )
    p.add_argument("--rgb-threshold", type=int, default=None, help="New confidence_threshold_rgb (higher = looser)")
    p.add_argument("--ir-threshold", type=int, default=None, help="New confidence_threshold_ir (higher = looser)")
    p.add_argument("--min-face-size", type=int, default=None, help="New min_face_size in pixels (lower = looser)")
    p.set_defaults(func=cmd_relax)

    cam_parser = sub.add_parser("camera", help="Manage cameras beyond the primary rgb/ir pair")
    cam_sub = cam_parser.add_subparsers(dest="camera_command", required=True)

    cam_sub.add_parser("list", help="Show all configured cameras").set_defaults(func=cmd_camera_list)

    p = cam_sub.add_parser("add", help="Add an extra camera (e.g. a second webcam with no IR)")
    p.add_argument("--device", help="e.g. /dev/video6 (default: interactively pick from unused devices)")
    p.add_argument("--id", help="unique name for this camera (default: auto-generated)")
    p.add_argument("--kind", choices=["rgb", "ir"], default=None, help="default: auto-detected")
    p.add_argument("--threshold", type=int, default=None, help="LBPH confidence threshold (default: 60 rgb / 65 ir)")
    p.set_defaults(func=cmd_camera_add)

    p = cam_sub.add_parser("remove", help="Remove a camera by id ('rgb' and 'ir' clear the primary pair)")
    p.add_argument("id")
    p.set_defaults(func=cmd_camera_remove)

    p = sub.add_parser(
        "hf-upload",
        help="Enable/disable optional first-enrollment backup to your Hugging Face dataset repo",
    )
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_hf_upload)

    p = sub.add_parser(
        "kde-passive-unlock",
        help="EXPERIMENTAL: try proactive (no-Enter) face check via kscreenlocker's fingerprint slot",
    )
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_kde_passive_unlock)

    sub.add_parser("uninstall", help="Remove PAM integration").set_defaults(func=cmd_uninstall)

    sub.add_parser(
        "doctor", help="Run health checks: camera, PAM wiring, enrolled models, lockout, logs"
    ).set_defaults(func=cmd_doctor)

    p = sub.add_parser("log", help="Show recent auth attempts from the Visagate log")
    p.add_argument("-n", type=int, default=20, help="number of lines to show (default 20)")
    p.set_defaults(func=cmd_log)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
