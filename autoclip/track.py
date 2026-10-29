"""Find where to put a 9:16 window over a landscape video by following faces."""
import cv2
import numpy as np

from .media import decode_frames

SAMPLE_FPS = 5
ANALYSIS_WIDTH = 960
# A new face position must hold this long before the crop jumps to it (avoids flicker
# when the detector briefly picks the wrong face).
SWITCH_HOLD = 0.6
# Moves smaller than this fraction of the frame width glide instead of cutting.
GLIDE_FRACTION = 0.12


def _detector():
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def _profile_detector():
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")


def _profile_faces(det, gray, min_size):
    """Side-on faces (a wide angle of someone turned to their co-host). The cascade only
    finds faces looking one way, so also search the mirrored frame."""
    w = gray.shape[1]
    kw = dict(scaleFactor=1.1, minNeighbors=6, minSize=(min_size, min_size))
    faces = list(det.detectMultiScale(gray, **kw))
    faces += [(w - fx - fw, fy, fw, fh) for fx, fy, fw, fh in det.detectMultiScale(cv2.flip(gray, 1), **kw)]
    return faces


def face_centers(video, start, duration, src_w, src_h):
    """Return a list of (t, x_center_in_source_px or None) sampled at SAMPLE_FPS."""
    aw = ANALYSIS_WIDTH
    ah = int(round(src_h * aw / src_w / 2)) * 2
    scale = src_w / aw
    det, profile = _detector(), _profile_detector()
    out = []
    for i, frame in enumerate(decode_frames(video, start, duration, SAMPLE_FPS, aw, ah)):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)  # no equalizeHist: it wrecks dark studio shots
        faces = det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                     minSize=(int(ah * 0.06), int(ah * 0.06)))
        if not len(faces):
            faces = _profile_faces(profile, gray, int(ah * 0.06))
        x = None
        if len(faces):
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            x = (fx + fw / 2) * scale
        out.append((i / SAMPLE_FPS, x))
    return out


def crop_path(samples, src_w, crop_w):
    """Turn noisy face samples into a steady crop-centre track: [(t, x_center, is_cut)]."""
    half = crop_w / 2
    clamp = lambda x: float(min(max(x, half), src_w - half))
    known = [x for _, x in samples if x is not None]
    if not known:
        return [(0.0, src_w / 2, False)]

    current = clamp(known[0])
    pending, pending_since = None, None
    track = []
    for t, x in samples:
        if x is not None:
            x = clamp(x)
            if abs(x - current) < GLIDE_FRACTION * src_w:
                current += 0.25 * (x - current)  # gentle follow
                pending = None
            elif pending is not None and abs(x - pending) < GLIDE_FRACTION * src_w:
                if t - pending_since >= SWITCH_HOLD:
                    current, pending = x, None  # hard cut to the new speaker
                    # Back-date the cut so it lands when the face actually appeared.
                    track = [p for p in track if p[0] < pending_since]
                    track.append((pending_since, current, True))
            else:
                pending, pending_since = x, t
        if not track or track[-1][0] < t:
            track.append((t, current, False))
    return track


class Track:
    """Per-frame crop centre: glides between samples, but cuts land instantly."""

    def __init__(self, track):
        self.times = np.array([p[0] for p in track])
        self.xs = np.array([p[1] for p in track])
        self.cuts = np.array([p[2] for p in track])

    def __call__(self, t):
        i = int(np.searchsorted(self.times, t, side="right")) - 1
        if i < 0:
            return float(self.xs[0])
        if i + 1 >= len(self.times) or self.cuts[i + 1]:
            return float(self.xs[i])
        t0, t1 = self.times[i], self.times[i + 1]
        a = (t - t0) / (t1 - t0)
        return float(self.xs[i] + a * (self.xs[i + 1] - self.xs[i]))
