"""Rotating file logger for Visagate, in addition to syslog.

syslog is the source of truth (works even if /var/log is unwritable, e.g.
read-only root), but journalctl -t visagate isn't discoverable for
everyone, and it doesn't give you a simple `visagate log` view. This
writes the same events to /var/log/visagate/visagate.log so `visagate log`
has something to read directly, with rotation so it can't grow unbounded.

New in v0.2.0.

v0.2.2: made the log dir/file world-writable (not just root-readable).
visagate-auth runs as whatever user the calling PAM service runs as --
root for `sudo`, but the actual logged-in user for kscreenlocker -- and a
0750/0640 log couldn't be written to by a non-root invocation at all, so
it silently fell back to NullHandler and lock-screen attempts never
showed up in `visagate log` even after they started reaching this code
(they'd still show in `journalctl -t visagate` via syslog, which isn't
filesystem-permission-dependent, which is how this asymmetry got noticed).
Log contents are usernames/services/confidence numbers, not secrets, so
world-writable here is a reasonable trade-off for consistent logging
regardless of which user context is invoking visagate-auth.
"""
import logging
import logging.handlers
import os

LOG_DIR = "/var/log/visagate"
LOG_FILE = os.path.join(LOG_DIR, "visagate.log")

_logger = None


def get_logger(name="visagate"):
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # Sticky bit (01777) so any user's invocation can create/append
        # log data, while still preventing one user from deleting another
        # user's files out from under them.
        os.chmod(LOG_DIR, 0o1777)
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=5
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        if os.path.exists(LOG_FILE):
            os.chmod(LOG_FILE, 0o666)
        for i in range(1, 6):
            backup = f"{LOG_FILE}.{i}"
            if os.path.exists(backup):
                os.chmod(backup, 0o666)
    except (PermissionError, OSError):
        # Not root, or /var/log/visagate isn't writable yet. Syslog (in
        # pam_helper._log) remains the record of truth in that case -- this
        # file log is a convenience, not the only copy.
        logger.addHandler(logging.NullHandler())

    _logger = logger
    return logger
