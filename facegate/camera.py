"""Camera discovery for the Logitech Brio.

The Brio (and similar Windows-Hello-capable webcams) expose more than one
/dev/videoN node: typically an RGB stream and, on IR-capable units, a
monochrome IR stream. We enumerate nodes via `v4l2-ctl --list-devices`,
filter to ones whose description mentions Logitech/Brio, then probe each
by grabbing a few live frames and looking at color saturation -- IR
streams read back as near-grayscale even though the device may still
report a color pixel format.
"""
import subprocess
import cv2


def list_video_devices():
    """Return {device_path: description} for all v4l2 devices on the system."""
    devices = {}
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"], text=True, stderr=subprocess.DEVNULL
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return devices
    current = None
    for line in out.splitlines():
        if line and not line.startswith(("\t", " ")):
            current = line.split("(")[0].strip()
        elif line.strip().startswith("/dev/video"):
            devices[line.strip()] = current
    return devices


def find_brio_devices():
    """Return sorted list of /dev/videoN paths belonging to a Logitech/Brio device."""
    devs = list_video_devices()
    matches = [
        d
        for d, desc in devs.items()
        if desc and ("brio" in desc.lower() or "logitech" in desc.lower())
    ]
    return sorted(matches)


def probe_device(path, samples=5):
    """Open a device, grab a few frames, return resolution + an IR guess."""
    cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    sat_values = []
    frame = None
    for _ in range(samples):
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        sat_values.append(float(hsv[:, :, 1].mean()))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if frame is None:
        return None
    avg_sat = sum(sat_values) / len(sat_values) if sat_values else 255.0
    is_ir = avg_sat < 12.0  # near-grayscale => almost certainly the IR stream
    return {
        "path": path,
        "width": w,
        "height": h,
        "avg_saturation": round(avg_sat, 2),
        "is_ir": is_ir,
    }


def auto_detect():
    """Return (rgb_info_or_None, ir_info_or_None, all_probed_devices)."""
    candidates = find_brio_devices()
    results = []
    for path in candidates:
        info = probe_device(path)
        if info:
            results.append(info)
    rgb = next((r for r in results if not r["is_ir"]), None)
    ir = next((r for r in results if r["is_ir"]), None)
    return rgb, ir, results
