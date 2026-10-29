"""Cut a clip, reframe it to 9:16 around the speaker, and burn in captions."""
import bisect
import re
import subprocess

import cv2
import numpy as np

from .captions import H, W, build_ass
from .media import decode_frames, grab_frame, has_audio, probe
from .style import Timeline, keep_segments, resolve, zoom_changes
from .broll import box_window, broll_flags, clip_thumbs, find_box
from .track import SAMPLE_FPS, Track, crop_path, face_centers

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


def render_clip(video, words, clip, out_path, work_dir, preview_path=None, style=None,
                studio=None, report=None):
    """Render one clip. Returns the (start, end) source span and the output duration.
    studio: thumbnails of the episode's studio shots (see broll.py); frames that match none
    of them are B-roll, and the insert they show fills the screen instead of the face."""
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
    faces = face_centers(video, start, duration, src_w, src_h)
    broll = np.zeros(len(faces), bool)
    windows = [None] * len(faces)  # per sample: the 9:16 crop of the B-roll insert on screen
    if studio is not None and crop_w < src_w:
        broll, dist = broll_flags(clip_thumbs(video, start, duration, SAMPLE_FPS), studio, SAMPLE_FPS)
        if len(broll) < len(faces):  # the two decodes can differ by a frame at the end
            broll = np.concatenate([broll, np.zeros(len(faces) - len(broll), bool)])
        broll = broll[:len(faces)]
        kept = []
        for a, b in _runs(broll, SAMPLE_FPS):
            # One crop per B-roll shot, from the bordered box around the insert. No box (or
            # a small graphic beside the speaker) means stay on the face.
            box = find_box(grab_frame(video, start + (a + b) / 2))
            win = box and box_window(box, src_w, src_h)
            i, j = int(round(a * SAMPLE_FPS)), int(round(b * SAMPLE_FPS))
            if win:
                windows[i:j] = [win] * (j - i)
                kept.append((a, b))
            else:
                broll[i:j] = False
        # Faces inside B-roll shouldn't steer the crop.
        faces = [(t, None if b else x) for (t, x), b in zip(faces, broll)]
        if report is not None:
            report.append(f"{out_path.stem}: dist p10/50/90/max {np.percentile(dist, [10, 50, 90, 100]).round(1).tolist()}"
                          f" broll {[(round(a, 1), round(b, 1)) for a, b in kept]}")
    track = Track(crop_path(faces, src_w, crop_w))

    mapped = [{"word": w["word"], "start": tl.out(w["start"]), "end": tl.out(w["end"])}
              for w in inside]
    ass_path = work_dir / (out_path.stem + ".ass")
    ass_path.write_text(build_ass(mapped, 0.0, tl.duration, clip.get("hook"),
                                  clip.get("hook_seconds"), style["caption_words"],
                                  style["emphasis"]))

    narrow = crop_w < src_h * 9 / 16 - 2  # source narrower than 9:16: pad instead of stretching
    graph = [f"[0:v]setsar=1,ass={ass_path}[v]"]
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
                          f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,asetpts=N/SR/TB[a]"]
        # loudnorm works at 192 kHz and its first frames come out with bunched-up
        # timestamps (seconds of audio stamped into the first half second, then a gap).
        # Desktop players ignore that; phones and Instagram stutter at the start. So
        # resample to 48 kHz and restamp every sample from its position.
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video),
        "-filter_complex", ";".join(graph), *maps,
        # Phone-safe H.264: High@4.1, a keyframe every 2 s, constant frame rate.
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level:v", "4.1", "-g", str(fps * 2), "-r", str(fps),
        "-movflags", "+faststart", "-t", f"{tl.duration:.3f}", str(out_path),
    ], stdin=subprocess.PIPE)

    n_out = int(tl.duration * fps)
    bar_h = 14
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
            win = windows[min(int(need * SAMPLE_FPS), len(windows) - 1)] if windows else None
            z = 1.0 if win else zooms[max(bisect.bisect_right(zoom_times, need + start) - 1, 0)][1]
            cw, ch = int(crop_w / z), int(src_h / z)
            x0 = int(round(track(need) - cw / 2))
            x0 = min(max(x0, 0), src_w - cw)
            y0 = min(max(int(src_h * 0.42 - ch / 2), 0), src_h - ch)  # faces sit high in frame
            if win:
                x0, y0, cw, ch = win
            crop = frame[y0:y0 + ch, x0:x0 + cw]
            if narrow and not win:
                out = np.zeros((H, W, 3), np.uint8)
                fw = int(round(H * cw / ch / 2)) * 2
                out[:, (W - fw) // 2:(W - fw) // 2 + fw] = cv2.resize(crop, (fw, H), interpolation=cv2.INTER_CUBIC)
            else:
                out = cv2.resize(crop, (W, H), interpolation=cv2.INTER_CUBIC)
            if style["progress_bar"]:
                out[:bar_h, :int(W * (n + 1) / n_out)] = (0, 229, 255)
            enc.stdin.write(out.tobytes())
    finally:
        frames.close()
        enc.stdin.close()
        enc.wait()
    if enc.returncode:
        raise RuntimeError(f"ffmpeg failed rendering {out_path.name}")

    if preview_path:
        times = [tl.duration * f for f in (0.05, 0.35, 0.65, 0.92)]
        spans = _runs(broll, SAMPLE_FPS)
        if spans:  # also show the longest B-roll moment, to check it was handled
            a, b = max(spans, key=lambda s: s[1] - s[0])
            times.append(min(tl.out(start + (a + b) / 2), tl.duration - 0.1))
            times.sort()
        contact_sheet(out_path, times, preview_path)
    return start, end, tl.duration


def _runs(flags, fps):
    """[(start_s, end_s)] of the True runs in a per-sample bool array."""
    spans, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j < len(flags) and flags[j]:
                j += 1
            spans.append((i / fps, j / fps))
            i = j
        else:
            i += 1
    return spans


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
