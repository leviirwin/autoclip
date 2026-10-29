"""Speech to text with word timestamps (faster-whisper, runs on CPU)."""
import json
import subprocess

import numpy as np

# Lines in transcript.txt break at sentence ends, long pauses, or this many words.
MAX_LINE_WORDS = 22
PAUSE_BREAK = 0.8


def transcribe(video_path, out_dir, model_size="small", language="en", names=None):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        load_audio(video_path), language=language, word_timestamps=True, vad_filter=True,
        # Whisper copies the spelling of names it has seen in the prompt.
        initial_prompt=f"A podcast with {names}." if names else None,
        hotwords=names or None,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": text})
        if seg.words:
            print(f"  transcribed to {seg.end / 60:5.1f} / {info.duration / 60:.1f} min", flush=True)

    words = join_fragments(words)
    lines = group_lines(words)
    (out_dir / "transcript.json").write_text(json.dumps({"words": words, "lines": lines}, indent=1))
    (out_dir / "transcript.txt").write_text(format_lines(lines))
    return words, lines


def load_audio(path, sr=16000):
    """Decode to 16 kHz mono float32 with ffmpeg (faster-whisper's own PyAV decoder breaks
    with newer PyAV releases)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.float32)


def join_fragments(words):
    """Glue pieces Whisper splits off a word back on: "$15" ",000" -> "$15,000", "2" ".4" ->
    "2.4", "e" "-commerce" -> "e-commerce", "95" "%" -> "95%"."""
    out = []
    for w in words:
        if out and w["word"][0] in ".,%-" and len(w["word"]) > 1 or out and w["word"] == "%":
            prev = out[-1]
            if prev["word"][-1].isalnum() and w["start"] - prev["end"] < 0.3:
                out[-1] = dict(prev, word=prev["word"] + w["word"], end=w["end"])
                continue
        out.append(w)
    return out


def group_lines(words):
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        sentence_end = w["word"][-1] in ".?!"
        pause = nxt is not None and nxt["start"] - w["end"] > PAUSE_BREAK
        if nxt is None or sentence_end or pause or len(cur) >= MAX_LINE_WORDS:
            lines.append({
                "start": cur[0]["start"], "end": cur[-1]["end"],
                "text": " ".join(x["word"] for x in cur),
            })
            cur = []
    return lines


def fmt_time(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def format_lines(lines):
    """One line per sentence: `[mm:ss.s | 123.4-127.9] text` (seconds are what clips.json uses)."""
    return "\n".join(
        f"[{fmt_time(l['start'])} | {l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in lines
    ) + "\n"
