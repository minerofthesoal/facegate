"""Face enrollment / recognition.

Uses OpenCV's LBPH face recognizer (opencv-contrib's cv2.face module) on
both the RGB and IR streams separately, storing one model file per stream
per user under /etc/facegate/models. At auth time both streams are
checked and (by default) both must agree, which is a meaningfully higher
bar than a single RGB camera check since a printed photo or phone screen
generally will not read correctly on the IR stream.

This is NOT structured-light depth sensing. It will not stop every
spoofing attempt a $30 Windows Hello depth camera would. See README.md
for the honest threat model.
"""
import os
import time

import cv2
import numpy as np

from . import config

# Prefer the copy we ship (works regardless of how the local OpenCV package
# lays out its data dir -- on Arch the pacman `opencv` package and the pip
# `opencv-contrib-python` wheel don't agree on where haarcascades live, and
# sometimes cv2.data.haarcascades points at a path with nothing in it).
_BUNDLED_CASCADE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "haarcascade_frontalface_default.xml"
)


def _cascade_path():
    candidates = [_BUNDLED_CASCADE]
    try:
        candidates.append(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception:
        pass
    candidates += [
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/share/OpenCV/haarcascades/haarcascade_frontalface_default.xml",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise RuntimeError(
        "Could not locate haarcascade_frontalface_default.xml anywhere "
        "(checked bundled copy + cv2.data + common system paths). "
        "Reinstall FaceGate or place the file at "
        f"{_BUNDLED_CASCADE}"
    )


def _detector():
    path = _cascade_path()
    clf = cv2.CascadeClassifier(path)
    if clf.empty():
        raise RuntimeError(f"OpenCV failed to load cascade file at {path} (file may be corrupt).")
    return clf


def _grab_faces(device_path, num_samples, timeout, min_face_size=80, verbose=True, collect_raw=False, max_raw=8):
    """Capture up to `num_samples` face crops for training.

    If `collect_raw` is True, also returns up to `max_raw` (full_frame,
    bbox) pairs alongside the training crops -- used only by the optional
    Hugging Face backup feature (v0.2.1) to have something to blur and
    upload; the training crops themselves (grayscale, tightly cropped)
    are never uploaded anywhere.
    """
    cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera device {device_path}")
    detector = _detector()
    faces = []
    raw_samples = []
    frames_read = 0
    detections_seen = 0
    start = time.time()
    last_report = start
    try:
        while len(faces) < num_samples and time.time() - start < timeout:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            frames_read += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detected = detector.detectMultiScale(
                gray, 1.2, 5, minSize=(min_face_size, min_face_size)
            )
            if len(detected):
                detections_seen += 1
            for (x, y, w, h) in detected:
                crop = cv2.resize(gray[y : y + h, x : x + w], (200, 200))
                faces.append(crop)
                if collect_raw and len(raw_samples) < max_raw:
                    raw_samples.append((frame.copy(), (x, y, w, h)))
                break
            if verbose and time.time() - last_report > 2:
                print(
                    f"    ...{frames_read} frames read, face seen in {detections_seen} of them, "
                    f"{len(faces)}/{num_samples} samples collected"
                )
                last_report = time.time()
    finally:
        cap.release()
    if verbose and frames_read == 0:
        print(f"    WARNING: 0 frames were successfully read from {device_path}.")
    elif verbose and detections_seen == 0:
        print(
            f"    WARNING: {frames_read} frames read but no face was ever detected. "
            f"Move closer to the camera, improve lighting, or lower "
            f"recognition.min_face_size in /etc/facegate/config.json (currently {min_face_size})."
        )
    return faces, raw_samples


MIN_SAMPLES = 5


def _train_or_update(path, faces, append=False):
    """Write an LBPH model to `path`. If `append` is True and a model
    already exists there, load it and add `faces` via update() (which
    keeps everything the model already learned); otherwise train a fresh
    model from scratch with just `faces`."""
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    labels = np.array([0] * len(faces))
    if append and os.path.exists(path):
        recognizer.read(path)
        recognizer.update(faces, labels)
    else:
        recognizer.train(faces, labels)
    recognizer.write(path)


def enroll_user(username, rgb_device, ir_device, samples=25, timeout=25, append=False,
                 extra_cameras=None, collect_for_upload=False):
    """Capture face samples from whichever device(s) are configured and
    train + save an LBPH model per stream. Returns a summary dict.

    If the RGB stream can't come up with enough samples (bad lighting, the
    RGB sensor just not picking up a face, etc.) but the IR stream did, the
    IR-detected face crops are reused to train the RGB model too rather than
    failing enrollment outright -- both crops are already normalized to
    grayscale 200x200 for LBPH, so an IR crop is a legitimate substitute.
    This does weaken the "two independent streams" guarantee for that user
    until they re-enroll with working RGB capture; we surface that in the
    result dict so callers can warn about it.

    If `append` is True and a model already exists for this user, the new
    samples are added to it via LBPH's update() instead of replacing it via
    train(). This is the right tool for "my face looks different sometimes"
    cases -- most commonly glasses vs. no glasses, since LBPH's local
    texture features around the eyes are sensitive to lens glare/frames,
    so a model that's only ever seen you one way can under-match the
    other. Run enroll normally once, then again with --append while
    wearing (or not wearing) glasses to teach the model both looks,
    rather than one appearance overwriting the other.

    `extra_cameras` (v0.2.1): list of {"id", "device", "kind", ...} dicts
    for additional cameras beyond the primary rgb/ir pair (see
    config.DEFAULTS["extra_cameras"]). Each gets its own model file,
    `{username}_{id}.yml`, trained/updated the same way as rgb/ir but
    without the IR-fallback trick (that's specific to the primary pair).

    `collect_for_upload` (v0.2.1): if True, also collects a handful of
    raw (frame, bbox) samples per stream and returns them under the
    `"_raw_samples"` key for the caller to optionally hand to
    hf_upload.save_and_upload(). This function itself never uploads or
    touches the network -- that's the caller's decision, gated on the
    hf_upload config and done outside of enrollment's critical path.
    """
    if not rgb_device and not ir_device and not extra_cameras:
        raise RuntimeError("No camera devices configured. Run 'facegate autosetup' first.")

    cfg = config.load()
    min_face_size = cfg["recognition"].get("min_face_size", 80)
    result = {}
    raw_samples = {}

    rgb_faces = []
    if rgb_device:
        print(f"  Capturing RGB samples from {rgb_device}...")
        rgb_faces, rgb_raw = _grab_faces(
            rgb_device, samples, timeout, min_face_size=min_face_size, collect_raw=collect_for_upload
        )
        if collect_for_upload and rgb_raw:
            raw_samples["rgb"] = rgb_raw

    ir_faces = []
    if ir_device:
        print(f"  Capturing IR samples from {ir_device}...")
        ir_faces, ir_raw = _grab_faces(
            ir_device, samples, timeout, min_face_size=min_face_size, collect_raw=collect_for_upload
        )
        if collect_for_upload and ir_raw:
            raw_samples["ir"] = ir_raw
        if len(ir_faces) < MIN_SAMPLES:
            raise RuntimeError(
                "Not enough IR face samples captured. Make sure nothing is "
                "covering the IR sensor and try again."
            )

    if rgb_device:
        rgb_source = rgb_faces
        used_ir_fallback = False
        if len(rgb_faces) < MIN_SAMPLES:
            if len(ir_faces) >= MIN_SAMPLES:
                print(
                    f"  WARNING: only {len(rgb_faces)}/{MIN_SAMPLES} RGB samples captured; "
                    "falling back to IR-detected face crops for the RGB model."
                )
                rgb_source = ir_faces
                used_ir_fallback = True
            else:
                raise RuntimeError(
                    "Not enough RGB face samples captured. Face the camera directly "
                    "in good, even lighting and try again."
                )
        path = os.path.join(config.MODEL_DIR, f"{username}_rgb.yml")
        _train_or_update(path, rgb_source, append=append)
        _lock_down(path)
        result["rgb_samples"] = len(rgb_source)
        result["rgb_used_ir_fallback"] = used_ir_fallback
        result["rgb_appended"] = append and os.path.exists(path)

    if ir_device:
        path = os.path.join(config.MODEL_DIR, f"{username}_ir.yml")
        _train_or_update(path, ir_faces, append=append)
        _lock_down(path)
        result["ir_samples"] = len(ir_faces)
        result["ir_appended"] = append and os.path.exists(path)

    for extra in extra_cameras or []:
        cam_id = extra["id"]
        device = extra["device"]
        print(f"  Capturing '{cam_id}' samples from {device}...")
        faces, raw = _grab_faces(
            device, samples, timeout, min_face_size=min_face_size, collect_raw=collect_for_upload
        )
        if collect_for_upload and raw:
            raw_samples[cam_id] = raw
        if len(faces) < MIN_SAMPLES:
            raise RuntimeError(
                f"Not enough face samples captured from '{cam_id}' ({device}). "
                "Face that camera directly in good, even lighting and try again, "
                f"or run 'facegate camera remove {cam_id}' to drop it."
            )
        path = os.path.join(config.MODEL_DIR, f"{username}_{cam_id}.yml")
        _train_or_update(path, faces, append=append)
        _lock_down(path)
        result[f"{cam_id}_samples"] = len(faces)
        result[f"{cam_id}_appended"] = append and os.path.exists(path)

    if collect_for_upload:
        result["_raw_samples"] = raw_samples

    return result


def _lock_down(path):
    """Root-owned, world-readable (see config.py's v0.2.2 docstring for
    why): facegate-auth needs to read this when invoked as whatever
    non-root user a PAM service like kscreenlocker runs as, not just when
    running as root under sudo. Model files aren't secrets in the same
    way the disable PIN is."""
    try:
        os.chmod(path, 0o644)
    except PermissionError:
        pass


def _authenticate_stream(device_path, model_path, threshold, max_attempts, timeout, min_face_size=80):
    """Try up to `max_attempts` distinct passes at recognizing a face,
    each given an even slice of `timeout` seconds. Returns as soon as one
    pass matches; gives up (returns False) once attempts are exhausted so
    the caller/PAM can fall back to password auth quickly and predictably.

    Returns (matched, best_conf, detected_any). `detected_any` is True if
    the detector ever found a face-shaped region in a frame, regardless of
    whether it matched the enrolled model -- this lets the caller tell
    "this stream never saw a face" (a camera/detection problem) apart from
    "this stream saw a face but it didn't match" (a real non-match)."""
    if not device_path or not os.path.exists(model_path):
        return False, None, False
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(model_path)
    cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
    if not cap.isOpened():
        return False, None, False
    detector = _detector()
    best_conf = None
    detected_any = False
    max_attempts = max(1, max_attempts)
    slice_seconds = max(1.0, timeout / max_attempts)
    try:
        for _attempt in range(max_attempts):
            deadline = time.time() + slice_seconds
            matched = False
            while time.time() < deadline:
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                detected = detector.detectMultiScale(
                    gray, 1.2, 5, minSize=(min_face_size, min_face_size)
                )
                for (x, y, w, h) in detected:
                    detected_any = True
                    crop = cv2.resize(gray[y : y + h, x : x + w], (200, 200))
                    _label, conf = recognizer.predict(crop)
                    if best_conf is None or conf < best_conf:
                        best_conf = conf
                    if conf <= threshold:
                        matched = True
                        break
                if matched:
                    break
            if matched:
                return True, best_conf, detected_any
    finally:
        cap.release()
    return False, best_conf, detected_any


def _combine_results(stream_results, require_all):
    """Generic N-stream version of the "require both to match, but a
    stream that never saw a face doesn't count against you" rule.

    `stream_results`: list of {"id", "ok", "conf", "detected"} dicts, one
    per configured stream (primary rgb/ir + any extra cameras).

    A stream that never detected a face at all (`detected` False) is
    treated as a camera/detection problem, not evidence of spoofing, so
    it's excluded from the requirement rather than counted as a failure.
    If require_all, every stream that DID detect a face must also have
    matched; otherwise any one matching stream is enough. If literally no
    stream ever detected a face, this fails closed.

    This is a faithful generalization of the original two-stream (rgb/ir)
    logic -- with exactly those two streams configured it reduces to
    exactly the same behavior as before. New in v0.2.1.
    """
    considered = [s for s in stream_results if s["detected"]]
    excluded = [s["id"] for s in stream_results if not s["detected"]]
    if not considered:
        return False, [], excluded
    if require_all:
        success = all(s["ok"] for s in considered)
    else:
        success = any(s["ok"] for s in considered)
    return success, [s["id"] for s in considered], excluded


def authenticate(username, timeout_override=None):
    """Run recognition against whichever streams are configured for this
    user -- the primary rgb/ir pair plus any extra_cameras (v0.2.1).
    Returns (bool success, info dict with raw confidences).

    `timeout_override`, new in v0.2.0: lets callers (specifically
    pam_helper, for greeter/lock-screen PAM services) use a shorter time
    budget than the configured recognition.timeout_seconds, since a
    lock-screen sitting unresponsive for the full sudo-context timeout
    reads as broken rather than "still checking."

    If a stream never detected a face at all during the whole attempt
    window -- as opposed to detecting a face and failing to match it --
    it's excluded rather than treated as a failure; see
    _combine_results(). A stream that DID detect a face but didn't match
    still fails the whole check, since that's a real non-match.
    """
    cfg = config.load()
    cam = cfg["camera"]
    rec = cfg["recognition"]
    min_face_size = rec.get("min_face_size", 80)
    timeout_seconds = timeout_override or rec["timeout_seconds"]
    require_all = rec.get("require_both", True)

    stream_results = []
    rgb_conf = None
    ir_conf = None

    if cam.get("rgb_device"):
        rgb_ok, rgb_conf, rgb_detected = _authenticate_stream(
            cam["rgb_device"],
            os.path.join(config.MODEL_DIR, f"{username}_rgb.yml"),
            rec["confidence_threshold_rgb"],
            rec["max_attempts"],
            timeout_seconds,
            min_face_size=min_face_size,
        )
        stream_results.append({"id": "rgb", "ok": rgb_ok, "conf": rgb_conf, "detected": rgb_detected})

    if cam.get("ir_device"):
        ir_ok, ir_conf, ir_detected = _authenticate_stream(
            cam["ir_device"],
            os.path.join(config.MODEL_DIR, f"{username}_ir.yml"),
            rec["confidence_threshold_ir"],
            rec["max_attempts"],
            timeout_seconds,
            min_face_size=min_face_size,
        )
        stream_results.append({"id": "ir", "ok": ir_ok, "conf": ir_conf, "detected": ir_detected})

    extra_confs = {}
    for extra in cfg.get("extra_cameras", []):
        cam_id = extra["id"]
        default_threshold = 65 if extra.get("kind") == "ir" else 60
        threshold = extra.get("threshold", default_threshold)
        e_ok, e_conf, e_detected = _authenticate_stream(
            extra["device"],
            os.path.join(config.MODEL_DIR, f"{username}_{cam_id}.yml"),
            threshold,
            rec["max_attempts"],
            timeout_seconds,
            min_face_size=min_face_size,
        )
        stream_results.append({"id": cam_id, "ok": e_ok, "conf": e_conf, "detected": e_detected})
        extra_confs[cam_id] = e_conf

    success, considered, excluded = _combine_results(stream_results, require_all)

    used_single_stream_fallback = None
    if len(stream_results) >= 2 and len(considered) == 1:
        used_single_stream_fallback = considered[0]

    return success, {
        "rgb_conf": rgb_conf,
        "ir_conf": ir_conf,
        "extra_confs": extra_confs,
        "considered_streams": considered,
        "excluded_streams": excluded,
        "used_single_stream_fallback": used_single_stream_fallback,
    }
