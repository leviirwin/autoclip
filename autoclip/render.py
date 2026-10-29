"""Cut a clip, reframe it to 9:16 around the speaker, and burn in captions."""
import re
import subprocess

import cv2
import numpy as np

from .captions import H, W, build_ass
from .media import decode_frames, probe
from .track import Track, crop_path, face_centers

MAX_FPS = 30
LEAD_IN = 0.12   # seconds kept before the first word
TAIL = 0.45      # seconds kept after the last word


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "clip"


def snap(words, start, end):
    """Snap a requested [start, end] to whole words. Returns (start, end, words_inside)."""
    inside = [w for w in words if w["end"] > start + 0.05 and w["start"] < end - 0.05]
    if not inside:
        raise ValueError(f"no speech between {start} and {end}")
    first, last = inside[0], inside[-1]
    i = words.index(first)
    prev_end = words[i - 1]["end"] if i > 0 else 0.0
    s = max(first["start"] - LEAD_IN, (prev_end + first["start"]) / 2)
    j = words.index(last)
    next_start = words[j + 1]["start"] if j + 1 < len(words) else last["end"] + TAIL
    e = min(last["end"] + TAIL, (last["end"] + next_start) / 2 + 0.1)
    return s, e, inside


def render_clip(video, words, clip, out_path, work_dir, preview_path=None):
    src_w, src_h, src_fps, _ = probe(video)
    start, end, inside = snap(words, float(clip["start"]), float(clip["end"]))
    duration = end - start
    fps = min(round(src_fps), MAX_FPS) or 30

    # Crop window: full height, 9:16 width. Portrait sources are just scaled.
    crop_w = min(src_w, int(round(src_h * 9 / 16 / 2)) * 2)
    track = Track(crop_path(face_centers(video, start, duration, src_w, src_h), src_w, crop_w))

    ass_path = work_dir / (out_path.stem + ".ass")
    ass_path.write_text(build_ass(inside, start, end, clip.get("hook"), clip.get("hook_seconds")))

    vf = f"scale={W}:{H}:flags=lanczos,setsar=1,ass={ass_path}"
    if crop_w < src_h * 9 / 16 - 2:  # source narrower than 9:16: pad instead of stretching
        vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,ass={ass_path}"
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{crop_w}x{src_h}", "-r", str(fps), "-i", "-",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-map", "0:v", "-map", "1:a?", "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags", "+faststart", "-shortest", str(out_path),
    ], stdin=subprocess.PIPE)

    stills, still_times = [], [duration * f for f in (0.05, 0.35, 0.65, 0.92)]
    try:
        for n, frame in enumerate(decode_frames(video, start, duration, fps, src_w, src_h)):
            t = n / fps
            x0 = int(round(track(t) - crop_w / 2))
            x0 = min(max(x0, 0), src_w - crop_w)
            enc.stdin.write(np.ascontiguousarray(frame[:, x0:x0 + crop_w]).tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode:
        raise RuntimeError(f"ffmpeg failed rendering {out_path.name}")

    if preview_path:
        contact_sheet(out_path, still_times, preview_path)
    return start, end


def contact_sheet(clip_path, times, out_path):
    """Save a strip of frames from the finished clip so the result can be checked by eye."""
    frames = []
    for t in times:
        tmp = out_path.with_suffix(f".{len(frames)}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(clip_path),
                        "-frames:v", "1", "-vf", "scale=360:640", str(tmp)], check=True)
        img = cv2.imread(str(tmp))
        tmp.unlink()
        if img is not None:
            frames.append(img)
    if frames:
        cv2.imwrite(str(out_path), np.hstack(frames))
