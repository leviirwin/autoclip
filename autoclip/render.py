"""Cut a clip, reframe it to 9:16 around the speaker, and burn in captions."""
import bisect
import re
import subprocess

import cv2
import numpy as np

from .captions import H, W, build_ass
from .media import decode_frames, has_audio, probe
from .style import Timeline, keep_segments, resolve, zoom_changes
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


def render_clip(video, words, clip, out_path, work_dir, preview_path=None, style=None):
    """Render one clip. Returns the (start, end) source span and the output duration."""
    style = style or resolve()
    src_w, src_h, src_fps, _ = probe(video)
    start, end, inside = snap(words, float(clip["start"]), float(clip["end"]))
    duration = end - start
    fps = min(round(src_fps), MAX_FPS) or 30
    dec_fps = min(round(src_fps), 60) or 30

    max_pause = style["max_pause"] if style["pace"] == "fast" else None
    tl = Timeline(keep_segments(inside, start, end, max_pause), style["speed"])
    zooms = zoom_changes(inside, style["zoom_amount"]) if style["zoom"] else [(start, 1.0)]
    zoom_times = [t for t, _ in zooms]

    # Crop window: full height, 9:16 width. Portrait sources are just scaled.
    crop_w = min(src_w, int(round(src_h * 9 / 16 / 2)) * 2)
    track = Track(crop_path(face_centers(video, start, duration, src_w, src_h), src_w, crop_w))

    mapped = [{"word": w["word"], "start": tl.out(w["start"]), "end": tl.out(w["end"])}
              for w in inside]
    ass_path = work_dir / (out_path.stem + ".ass")
    ass_path.write_text(build_ass(mapped, 0.0, tl.duration, clip.get("hook"),
                                  clip.get("hook_seconds"), style["caption_words"],
                                  style["emphasis"]))

    vf = f"scale={W}:{H}:flags=lanczos,setsar=1,ass={ass_path}"
    if crop_w < src_h * 9 / 16 - 2:  # source narrower than 9:16: pad instead of stretching
        vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,ass={ass_path}"
    graph = [f"[0:v]{vf}[v]"]
    maps = ["-map", "[v]"]
    if has_audio(video):
        # Cut the same spans out of the audio, with tiny fades so the joins don't click.
        parts = []
        for i, (s, e) in enumerate(tl.segs):
            n = e - s
            parts.append(f"[1:a]atrim={s - start:.3f}:{e - start:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.01,afade=t=out:st={max(n - 0.01, 0):.3f}:d=0.01[a{i}]")
        joined = "".join(f"[a{i}]" for i in range(len(parts)))
        tempo = f"atempo={style['speed']:.3f}," if style["speed"] != 1 else ""
        graph += parts + [f"{joined}concat=n={len(parts)}:v=0:a=1,{tempo}"
                          f"loudnorm=I=-14:TP=-1.5:LRA=11[a]"]
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{crop_w}x{src_h}", "-r", str(fps), "-i", "-",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-filter_complex", ";".join(graph), *maps,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-t", f"{tl.duration:.3f}", str(out_path),
    ], stdin=subprocess.PIPE)

    n_out = int(tl.duration * fps)
    bar_h = max(int(src_h * 0.008), 4)
    frames = decode_frames(video, start, duration, dec_fps, src_w, src_h)
    frame, frame_t, k = None, -1.0, 0
    try:
        for n in range(n_out):
            # Pull source frames forward until we reach the one shown at this output time.
            need = tl.src(n / fps) - start
            while frame is None or frame_t + 0.5 / dec_fps < need:
                nxt = next(frames, None)
                if nxt is None:
                    break
                frame, frame_t, k = nxt, k / dec_fps, k + 1
            if frame is None:
                break
            z = zooms[max(bisect.bisect_right(zoom_times, need + start) - 1, 0)][1]
            cw, ch = int(crop_w / z), int(src_h / z)
            x0 = int(round(track(need) - cw / 2))
            x0 = min(max(x0, 0), src_w - cw)
            y0 = min(max(int(src_h * 0.42 - ch / 2), 0), src_h - ch)  # faces sit high in frame
            out = frame[y0:y0 + ch, x0:x0 + cw]
            if z != 1.0:
                out = cv2.resize(out, (crop_w, src_h), interpolation=cv2.INTER_LINEAR)
            else:
                out = out.copy()
            if style["progress_bar"]:
                out[:bar_h, :int(crop_w * (n + 1) / n_out)] = (0, 229, 255)
            enc.stdin.write(out.tobytes())
    finally:
        frames.close()
        enc.stdin.close()
        enc.wait()
    if enc.returncode:
        raise RuntimeError(f"ffmpeg failed rendering {out_path.name}")

    if preview_path:
        contact_sheet(out_path, [tl.duration * f for f in (0.05, 0.35, 0.65, 0.92)], preview_path)
    return start, end, tl.duration


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
