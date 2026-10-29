"""Speech to text with word timestamps (faster-whisper, runs on CPU)."""
import json

# Lines in transcript.txt break at sentence ends, long pauses, or this many words.
MAX_LINE_WORDS = 22
PAUSE_BREAK = 0.8


def transcribe(video_path, out_dir, model_size="small", language="en"):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(video_path), language=language, word_timestamps=True, vad_filter=True,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": text})
        if seg.words:
            print(f"  transcribed to {seg.end / 60:5.1f} / {info.duration / 60:.1f} min", flush=True)

    lines = group_lines(words)
    (out_dir / "transcript.json").write_text(json.dumps({"words": words, "lines": lines}, indent=1))
    (out_dir / "transcript.txt").write_text(format_lines(lines))
    return words, lines


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
