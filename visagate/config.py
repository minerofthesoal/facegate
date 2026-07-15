"""Configuration storage for Visagate.

v0.2.2 permission model change, and why: visagate-auth is invoked by
pam_exec running with whatever privileges the CALLING PAM service has --
that's root for `sudo` (which stays root through its whole auth phase
before dropping privileges), but it's your regular logged-in user for
KDE's kscreenlocker (which just re-confirms you're still you, no
escalation needed), and possibly a dedicated system account for other
greeters. A visagate-auth run as a non-root user could not read the old
0700/0600 root-only config+model files at all, and failed instantly
before it ever reached a logging call -- which is exactly why lock-screen
attempts silently never showed up in `visagate log`.

Fix: config.json and the per-user model files are now root-owned but
WORLD-READABLE (0644 file / 0755 dirs) -- none of that data is a secret
(camera device paths, thresholds, and LBPH texture models aren't
passwords or photos). Only root can write them. The one genuinely
sensitive value, the disable/uninstall PIN hash, lives in a SEPARATE file
(pin.json) that stays strictly root-only (0600) -- pam_helper.py never
needs to read it, only the `visagate` CLI commands that already require
root do.

Trade-off worth knowing: any local user can now read another local
user's LBPH model file. That's a real, if low-value, information
exposure (it's texture histograms, not a viewable photo) -- accepted as
the cost of a pam_exec-based design working at all outside `sudo`.
"""
import json
import os

CONFIG_DIR = "/etc/visagate"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PIN_FILE = os.path.join(CONFIG_DIR, "pin.json")
MODEL_DIR = os.path.join(CONFIG_DIR, "models")

DEFAULTS = {
    "enabled": False,
    "camera": {
        "rgb_device": None,
        "ir_device": None,
        "auto_detected": False,
    },
    # Seconds to wait/retry for the camera devices to enumerate before
    # giving up. USB webcams (especially ones behind a hub) don't always
    # show up in v4l2-ctl the instant the kernel finishes booting, so a
    # PAM check that runs right at the SDDM login screen after a cold
    # boot can otherwise race the device and silently fail closed. New in
    # v0.2.0.
    "camera_wait_seconds": 5,
    "recognition": {
        # LBPH: LOWER confidence value = better match. These thresholds are
        # conservative starting points; `visagate test` will show you real
        # numbers for your face/lighting so you can tune them.
        "confidence_threshold_rgb": 60,
        "confidence_threshold_ir": 65,
        "require_both": True,
        # How many distinct face-match attempts to make (each attempt gets
        # its own short time slice) before giving up and letting PAM fall
        # through to the normal password prompt. Configurable via
        # `visagate set-attempts N`.
        "max_attempts": 2,
        "timeout_seconds": 8,
        # Shorter budget used specifically for greeter/lock-screen PAM
        # services (sddm, kde, kde-fingerprint). Those UIs read as
        # "broken" if the screen just sits there for 16s (max_attempts x
        # timeout_seconds) before a face check gives up, so lock-screen
        # contexts get a tighter timeout than an interactive sudo prompt
        # where you're already sitting there anyway. New in v0.2.0.
        "timeout_seconds_greeter": 6,
        # Minimum face bounding-box size (pixels) the Haar cascade will
        # accept. Lower this if you're sitting far from the camera and
        # enrollment/recognition can't find a face at all.
        "min_face_size": 80,
    },
    # Brute-force / repeated-spoofing-attempt protection. After
    # max_failed_attempts consecutive failures (across ALL PAM contexts,
    # counted process-wide via /run/visagate/lockout.json), face auth is
    # skipped for cooldown_seconds and PAM falls straight to password --
    # not just for the current login attempt, but until the cooldown
    # expires. State lives under /run (tmpfs) so a lockout doesn't
    # survive a reboot. New in v0.2.0.
    "lockout": {
        "max_failed_attempts": 5,
        "cooldown_seconds": 300,
    },
    # Additional cameras beyond the primary camera.rgb_device/ir_device
    # pair -- e.g. a second webcam with no IR sensor, used purely as a
    # third independent RGB check. Each entry:
    #   {"id": "c930c_rgb", "device": "/dev/videoN", "kind": "rgb",
    #    "threshold": 60}
    # `id` must be unique and is used to name that stream's model file
    # (visagate_{username}_{id}.yml) and in diagnostics. `kind` is "rgb"
    # or "ir" (informational + picks the default threshold when one isn't
    # given). Managed via `visagate camera add/remove/list`. New in v0.2.1.
    "extra_cameras": [],
    # Optional, OFF BY DEFAULT backup of the very first successful
    # enrollment's face images (all configured streams, background
    # blurred) to a Hugging Face dataset repo. Never uploads anything
    # from later re-enrollments or `--append` sessions -- only the first
    # time a given username is ever enrolled while this is turned on.
    # Requires `huggingface_hub` installed and `huggingface-cli login`
    # already run (Visagate never stores or asks for a token itself).
    # New in v0.2.1.
    "hf_upload": {
        "enabled": False,
        "repo_id": "ray0rf1re/faces",
        "uploaded_users": [],
    },
    # These two are always sourced from/written to the separate root-only
    # PIN_FILE (see load_pin/save_pin below), never from config.json --
    # kept here only so DEFAULTS has the keys for callers that expect them.
    "pin_hash": None,
    "pin_salt": None,
}


def ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o755)
        os.chmod(MODEL_DIR, 0o755)
    except PermissionError:
        pass
    # Self-healing: retroactively fix permissions on anything left over
    # from before v0.2.2's permission model change, every time this runs
    # (cheap, idempotent), so upgrading doesn't require a manual chmod.
    #
    # Ordering matters here: never relax config.json to 0644 while it
    # might still contain an un-migrated pin_hash from an old install --
    # that would briefly make the PIN world-readable before load()'s
    # migration step gets a chance to strip it out. Only relax it once
    # we've confirmed there's no PIN sitting in it.
    try:
        if os.path.exists(CONFIG_FILE):
            safe_to_relax = True
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                if existing.get("pin_hash"):
                    safe_to_relax = False  # load()'s migration will handle this
            except (json.JSONDecodeError, OSError):
                safe_to_relax = False
            if safe_to_relax:
                os.chmod(CONFIG_FILE, 0o644)
        if os.path.isdir(MODEL_DIR):
            for fn in os.listdir(MODEL_DIR):
                try:
                    os.chmod(os.path.join(MODEL_DIR, fn), 0o644)
                except PermissionError:
                    pass
        if os.path.exists(PIN_FILE):
            os.chmod(PIN_FILE, 0o600)
    except PermissionError:
        pass


def _read_pin_file():
    if not os.path.exists(PIN_FILE):
        return {"pin_hash": None, "pin_salt": None}
    with open(PIN_FILE) as f:
        data = json.load(f)
    return {"pin_hash": data.get("pin_hash"), "pin_salt": data.get("pin_salt")}


def load_pin():
    """Read the PIN hash/salt from the strictly root-only PIN_FILE."""
    ensure_dirs()
    return _read_pin_file()


def save_pin(pin_hash, pin_salt):
    """Write the PIN hash/salt to PIN_FILE, mode 0600 -- never to config.json."""
    ensure_dirs()
    with open(PIN_FILE, "w") as f:
        json.dump({"pin_hash": pin_hash, "pin_salt": pin_salt}, f)
    try:
        os.chmod(PIN_FILE, 0o600)
    except PermissionError:
        pass


def load():
    ensure_dirs()
    if not os.path.exists(CONFIG_FILE):
        save(dict(DEFAULTS))
        raw = {}
    else:
        with open(CONFIG_FILE) as f:
            raw = json.load(f)

    # One-time migration: older versions stored pin_hash/pin_salt directly
    # in config.json (which used to be mode 0600). If we find them there
    # and haven't already got a PIN_FILE, move them over -- and actually
    # rewrite config.json on disk without them, not just the in-memory
    # copy, since config.json is about to become world-readable and the
    # PIN must not still be sitting in it.
    if raw.get("pin_hash") and not os.path.exists(PIN_FILE):
        save_pin(raw["pin_hash"], raw.get("pin_salt"))
        raw = {k: v for k, v in raw.items() if k not in ("pin_hash", "pin_salt")}
        with open(CONFIG_FILE, "w") as f:
            json.dump(raw, f, indent=2)
        try:
            os.chmod(CONFIG_FILE, 0o644)
        except PermissionError:
            pass

    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    _deep_update(merged, raw)
    merged.pop("pin_hash", None)
    merged.pop("pin_salt", None)

    pin = _read_pin_file()
    merged["pin_hash"] = pin["pin_hash"]
    merged["pin_salt"] = pin["pin_salt"]
    return merged


def save(cfg):
    ensure_dirs()
    # pin_hash/pin_salt never get written to config.json (which is now
    # world-readable) -- they only ever live in the root-only PIN_FILE,
    # written via save_pin() (see security.py's set_pin()).
    to_write = {k: v for k, v in cfg.items() if k not in ("pin_hash", "pin_salt")}
    with open(CONFIG_FILE, "w") as f:
        json.dump(to_write, f, indent=2)
    try:
        os.chmod(CONFIG_FILE, 0o644)
    except PermissionError:
        pass


def _deep_update(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
