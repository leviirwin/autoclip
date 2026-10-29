"""Spot B-roll: moments where the episode cuts away from the studio cameras.

A podcast's studio angles repeat all episode long, while B-roll (inserts, screenshots,
clips that slide in) is mostly one-off. So: take a tiny thumbnail of every keyframe in the
episode, call a thumbnail "studio" when similar ones turn up at several other points in
the episode, and treat any clip frame that matches no studio thumbnail as B-roll.
"""
import subprocess

import numpy as np

TW, TH = 32, 18
SAME = 20.0          # RMS difference (0-255) below which two thumbnails show the same shot
MIN_REPEATS = 2      # a studio shot recurs at least this many times...
MIN_GAP = 30.0       # ...at least this many seconds apart
BROLL = 26.0         # a clip frame this far from every studio thumbnail is B-roll
MIN_RUN = 0.4        # ignore B-roll or studio runs shorter than this (seconds)


def _thumbs(cmd):
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, TH * TW).astype(np.float32)


def keyframe_thumbs(video):
    """Thumbnail of every keyframe in the video (fast: other frames are not decoded)."""
    return _thumbs(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", str(video),
                    "-vf", f"scale={TW}:{TH}:flags=area,format=gray", "-fps_mode", "passthrough",
                    "-f", "rawvideo", "-"])


def clip_thumbs(video, start, duration, fps):
    return _thumbs(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                    "-i", str(video), "-vf", f"fps={fps},scale={TW}:{TH}:flags=area,format=gray",
                    "-f", "rawvideo", "-"])


def rms_dist(a, b):
    """Pairwise RMS pixel difference between rows of a and rows of b."""
    d2 = (a * a).sum(1)[:, None] + (b * b).sum(1)[None, :] - 2 * a @ b.T
    return np.sqrt(np.maximum(d2, 0) / a.shape[1])


def studio_refs(video, duration, cache=None):
    """Thumbnails of the shots that keep coming back (the studio cameras)."""
    if cache and cache.exists():
        return np.load(cache)
    th = keyframe_thumbs(video)
    times = np.linspace(0, duration, len(th), endpoint=False)  # keyframes are roughly even
    keep = np.zeros(len(th), bool)
    for i in range(0, len(th), 512):
        d = rms_dist(th[i:i + 512], th)
        far = np.abs(times[i:i + 512, None] - times[None, :]) >= MIN_GAP
        keep[i:i + 512] = ((d < SAME) & far).sum(1) >= MIN_REPEATS
    refs = th[keep]
    if cache:
        np.save(cache, refs)
    return refs


def broll_flags(thumbs, refs, fps):
    """One bool per clip thumbnail: True where the frame matches no studio shot."""
    if not len(refs):
        return np.zeros(len(thumbs), bool), np.zeros(len(thumbs))
    dist = rms_dist(thumbs, refs).min(1)
    flags = dist > BROLL
    # Drop runs too short to matter (a detector blip, or the first frame of a slide-in).
    n = max(int(round(MIN_RUN * fps)), 1)
    i = 0
    while i < len(flags):
        j = i
        while j < len(flags) and flags[j] == flags[i]:
            j += 1
        if j - i < n and i > 0:
            flags[i:j] = flags[i - 1]
        i = j
    return flags, dist


def find_box(frame):
    """(x, y, w, h) of the largest bordered rectangle in the frame (how this show frames its
    B-roll inserts), or None. A frame flagged as B-roll with no box is a studio shot the
    flags got wrong, e.g. a wide angle that rarely appears."""
    import cv2

    h, w = frame.shape[:2]
    s = 640 / w
    g = cv2.cvtColor(cv2.resize(frame, (640, int(h * s)), interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY)
    H, W = g.shape
    e = cv2.dilate(cv2.Canny(g, 40, 120), np.ones((3, 3), np.uint8))
    # Keep only long straight edges, then look for ones that close into a rectangle.
    v = cv2.morphologyEx(e, cv2.MORPH_OPEN, np.ones((int(H * 0.2), 1), np.uint8))
    hz = cv2.morphologyEx(e, cv2.MORPH_OPEN, np.ones((1, int(W * 0.12)), np.uint8))
    lines = cv2.dilate(v | hz, np.ones((5, 5), np.uint8))
    best = None
    for c in cv2.findContours(lines, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)[0]:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < W * 0.15 or bh < H * 0.3 or bw * bh > W * H * 0.92:
            continue
        sides = [lines[y:y + bh, x:x + 6].any(1).mean(), lines[y:y + bh, x + bw - 6:x + bw].any(1).mean(),
                 lines[y:y + 6, x:x + bw].any(0).mean(), lines[y + bh - 6:y + bh, x:x + bw].any(0).mean()]
        if min(sides) >= 0.6 and (best is None or bw * bh > best[2] * best[3]):
            best = (x, y, bw, bh)
    return None if best is None else tuple(int(round(t / s)) for t in best)


def box_window(box, src_w, src_h):
    """9:16 crop (x, y, w, h) that fills the screen with the inside of a B-roll box, or
    None for a small landscape box (a graphic beside the speaker: a 9:16 slice of it would
    cut the text, so stay on the speaker's face instead)."""
    x, y, bw, bh = box
    if bw > bh * 1.2 and bh < src_h * 0.7:
        return None
    m = int(min(bw, bh) * 0.03)  # step inside the border
    x, y, bw, bh = x + m, y + m, bw - 2 * m, bh - 2 * m
    ch = max(bh, src_h * 0.5)
    cw = ch * 9 / 16
    if cw > bw:  # tall, narrow insert: fit its width instead
        cw = bw
        ch = min(cw * 16 / 9, src_h)
        cw = ch * 9 / 16
    cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
    cx, cy = x + bw / 2, y + bh / 2
    x0 = int(min(max(cx - cw / 2, 0), src_w - cw))
    y0 = int(min(max(cy - ch / 2, 0), src_h - ch))
    return x0, y0, cw, ch
