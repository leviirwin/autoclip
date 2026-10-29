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
