"""Optional, OFF BY DEFAULT backup of first-enrollment face images to a
Hugging Face dataset repo.

Scope, matching what was asked for:
 - Disabled by default; only ever turned on via an explicit opt-in prompt
   during `facegate autosetup` (or later with `facegate hf-upload on`).
 - Only ever uploads images from the very first successful enrollment for
   a given username. `facegate enroll` / `facegate enroll --append`
   sessions after that never upload anything, even if left enabled --
   config.py tracks this per-user in hf_upload.uploaded_users.
 - Every uploaded image has everything outside the detected face bounding
   box blurred first. This is a privacy nicety, not a guarantee -- it
   still uploads a real photo of your face to a remote service you don't
   control the infrastructure of. Think about that before turning it on,
   especially if the target repo is public.
 - FaceGate never stores or asks for a Hugging Face token. It relies on
   you already being logged in (`huggingface-cli login`) and shells out
   to the `huggingface_hub` library's normal token discovery.
"""
import os
import tempfile
import time

import cv2

DEFAULT_REPO_ID = "ray0rf1re/faces"


def blur_background(frame, bbox, blur_kernel=61):
    """Return a copy of `frame` with everything outside `bbox` Gaussian-
    blurred and the (x, y, w, h) region itself left sharp."""
    x, y, w, h = bbox
    k = blur_kernel | 1  # kernel size must be odd
    blurred = cv2.GaussianBlur(frame, (k, k), 0)
    result = blurred.copy()
    result[y : y + h, x : x + w] = frame[y : y + h, x : x + w]
    return result


def is_available():
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def save_and_upload(username, stream_samples, repo_id=DEFAULT_REPO_ID):
    """`stream_samples`: {"rgb": [(frame, bbox), ...], "ir": [...], "<cam_id>": [...]}

    Blurs the background of every sample, writes them to a scratch temp
    dir, uploads each to `{username}/{timestamp}/{stream}_{n}.png` under
    the dataset repo, then cleans up the local temp files regardless of
    outcome. Raises on failure -- callers decide whether that's fatal to
    the enrollment flow (it shouldn't be; see cli.py).
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub is not installed. Install it with: "
            "pip install --break-system-packages huggingface_hub"
        ) from e

    api = HfApi()
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    uploaded = []
    with tempfile.TemporaryDirectory(prefix="facegate-hf-") as tmp:
        for stream_id, samples in stream_samples.items():
            for i, (frame, bbox) in enumerate(samples):
                blurred = blur_background(frame, bbox)
                local_path = os.path.join(tmp, f"{stream_id}_{i}.png")
                cv2.imwrite(local_path, blurred)
                remote_path = f"{username}/{ts}/{stream_id}_{i}.png"
                api.upload_file(
                    path_or_fileobj=local_path,
                    path_in_repo=remote_path,
                    repo_id=repo_id,
                    repo_type="dataset",
                )
                uploaded.append(remote_path)
    return uploaded
