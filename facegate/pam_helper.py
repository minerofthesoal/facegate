"""Entry point invoked by pam_exec.so at auth time.

Exit code 0  -> tell PAM the user authenticated.
Exit code != 0 -> tell PAM to fall through to the next stack entry
                  (normally the real password prompt).

pam_exec sets PAM_USER in the environment for the account being
authenticated, which is what we key the enrolled model off of.

Every attempt is logged to syslog (facility LOG_AUTH, ident "facegate")
so a silent fall-through to password can actually be diagnosed after the
fact instead of just being a mystery exit code 1 -- check with:
    sudo journalctl -t facegate -e
"""
import os
import sys
import syslog

from . import config, recognizer


def _log(message):
    try:
        syslog.openlog(ident="facegate", facility=syslog.LOG_AUTH)
        syslog.syslog(syslog.LOG_INFO, message)
    except Exception:
        pass  # logging must never be the reason auth fails closed


def main():
    cfg = config.load()
    if not cfg.get("enabled"):
        sys.exit(1)  # face unlock turned off -> always fall back to password

    username = os.environ.get("PAM_USER")
    if not username:
        _log("no PAM_USER in environment; falling back to password")
        sys.exit(1)

    try:
        ok, info = recognizer.authenticate(username)
    except Exception as e:
        # Any camera/model error must fail closed, not crash PAM.
        _log(f"user={username} EXCEPTION during authenticate(): {type(e).__name__}: {e}")
        sys.exit(1)

    _log(f"user={username} result={'MATCH' if ok else 'NO MATCH'} info={info}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
