"""Configuration storage for FaceGate.

Everything lives under /etc/facegate, root-owned, mode 0700/0600, since it
holds face model files and a PIN hash.
"""
import json
import os

CONFIG_DIR = "/etc/facegate"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
MODEL_DIR = os.path.join(CONFIG_DIR, "models")

DEFAULTS = {
    "enabled": False,
    "camera": {
        "rgb_device": None,
        "ir_device": None,
        "auto_detected": False,
    },
    "recognition": {
        # LBPH: LOWER confidence value = better match. These thresholds are
        # conservative starting points; `facegate test` will show you real
        # numbers for your face/lighting so you can tune them.
        "confidence_threshold_rgb": 60,
        "confidence_threshold_ir": 65,
        "require_both": True,
        # How many distinct face-match attempts to make (each attempt gets
        # its own short time slice) before giving up and letting PAM fall
        # through to the normal password prompt. Configurable via
        # `facegate set-attempts N`.
        "max_attempts": 2,
        "timeout_seconds": 8,
        # Minimum face bounding-box size (pixels) the Haar cascade will
        # accept. Lower this if you're sitting far from the camera and
        # enrollment/recognition can't find a face at all.
        "min_face_size": 80,
    },
    "pin_hash": None,
    "pin_salt": None,
}


def ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
        os.chmod(MODEL_DIR, 0o700)
    except PermissionError:
        pass


def load():
    ensure_dirs()
    if not os.path.exists(CONFIG_FILE):
        save(dict(DEFAULTS))
        return dict(DEFAULTS)
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    _deep_update(merged, cfg)
    return merged


def save(cfg):
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except PermissionError:
        pass


def _deep_update(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
