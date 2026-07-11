"""Guards for privileged FaceGate actions (disable / uninstall / change PIN).

Two independent unlock paths are supported, matching the request: the
account's real password, or a separate FaceGate-only PIN. Either is
accepted so a PIN can be handed out for quick toggling without sharing
the real system password.

IMPORTANT: password verification here deliberately does NOT go through
the system `sudo`/`login`/`kde` PAM stacks, because FaceGate itself adds
a `pam_exec.so ... sufficient` face-auth line to those. If we verified
"do you know the password" via regular `sudo`, a successfully spoofed (or
just successfully recognized) face would satisfy that check too, letting
face recognition alone disable face recognition. Instead we maintain our
own tiny PAM service, `facegate-verify`, containing nothing but
`pam_unix.so` -- no face auth is ever added to it -- so this check can
only ever be satisfied by the real account password.
"""
import getpass
import hashlib
import os
import subprocess

from . import config

VERIFY_SERVICE_NAME = "facegate-verify"
VERIFY_SERVICE_FILE = f"/etc/pam.d/{VERIFY_SERVICE_NAME}"


def ensure_verify_service():
    """Create the dedicated password-only PAM service if it doesn't exist yet."""
    if os.path.exists(VERIFY_SERVICE_FILE):
        return
    content = (
        "# Managed by FaceGate.\n"
        "# Do NOT add pam_exec/face-auth lines to this file. It exists\n"
        "# specifically so FaceGate's internal 'confirm your real password'\n"
        "# check can never be satisfied by face recognition.\n"
        "auth     required   pam_unix.so\n"
        "account  required   pam_unix.so\n"
    )
    with open(VERIFY_SERVICE_FILE, "w") as f:
        f.write(content)
    os.chmod(VERIFY_SERVICE_FILE, 0o644)


def verify_sudo_password():
    """Verify the real account password via the isolated facegate-verify
    PAM service (falls back to `sudo -S` only if python-pam isn't
    installed -- that fallback path IS subject to the face-auth caveat
    above, so python-pam is installed by default in install.sh)."""
    ensure_verify_service()
    username = os.environ.get("SUDO_USER") or getpass.getuser()
    pw = getpass.getpass("Enter your account password to confirm: ")
    try:
        import pam as pam_module
    except ImportError:
        print("(python-pam not installed -- falling back to `sudo -S`, which")
        print(" is satisfied by face recognition too. Run install.sh again")
        print(" to install python-pam and close this gap.)")
        proc = subprocess.run(
            ["sudo", "-S", "-k", "true"], input=pw + "\n", text=True, capture_output=True
        )
        return proc.returncode == 0
    p = pam_module.pam()
    return bool(p.authenticate(username, pw, service=VERIFY_SERVICE_NAME))


def hash_pin(pin, salt_hex=None):
    if salt_hex is None:
        salt_hex = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), bytes.fromhex(salt_hex), 200_000
    ).hex()
    return digest, salt_hex


def set_pin(pin):
    digest, salt = hash_pin(pin)
    cfg = config.load()
    cfg["pin_hash"] = digest
    cfg["pin_salt"] = salt
    config.save(cfg)


def verify_pin(pin):
    cfg = config.load()
    if not cfg.get("pin_hash") or not cfg.get("pin_salt"):
        return False
    digest, _ = hash_pin(pin, cfg["pin_salt"])
    return digest == cfg["pin_hash"]


def confirm_privileged_action(prompt="This action requires confirmation."):
    print(prompt)
    print("  1) Verify with sudo password")
    print("  2) Verify with custom PIN")
    choice = input("Choose [1/2]: ").strip()
    if choice == "1":
        return verify_sudo_password()
    if choice == "2":
        pin = getpass.getpass("Enter PIN: ")
        return verify_pin(pin)
    return False
