"""Word-by-word burned-in captions, written as an ASS subtitle file for ffmpeg/libass."""

W, H = 1080, 1920
MAX_CHUNK_WORDS = 3
MAX_CHUNK_CHARS = 18
FONT = "Inter ExtraBold"
HIGHLIGHT = "&H0000E5FF&"  # ASS colours are &HAABBGGRR: this is a warm yellow
EMPHASIS = "&H0084DC3D&"   # green, for key words
WHITE = "&H00FFFFFF&"

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{FONT},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1
Style: Hook,{FONT},64,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(t):
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean(word):
    return word.upper().replace("{", "(").replace("}", ")").replace("\\", "/")


def key(word):
    return "".join(c for c in word.lower() if c.isalnum() or c == "'")


def chunk(words, max_words=MAX_CHUNK_WORDS):
    """Group words into short on-screen phrases, breaking after punctuation."""
    chunks, cur = [], []
    for w in words:
        text = " ".join(x["word"] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(text) > MAX_CHUNK_CHARS):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["word"][-1] in ".,?!;:":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words, clip_start, clip_end, hook=None, hook_seconds=None,
              max_words=MAX_CHUNK_WORDS, emphasis=()):
    """words: transcript words inside the clip (absolute times). Returns ASS text.
    emphasis: words always drawn bigger in green."""
    emphasis = {key(e) for e in emphasis}
    events = []
    if hook:
        end = clip_end - clip_start if hook_seconds is None else hook_seconds
        events.append(f"Dialogue: 1,{ts(0)},{ts(end)},Hook,,0,0,0,,{clean(hook)}")

    groups = chunk(words, max_words)
    for g, group in enumerate(groups):
        next_start = groups[g + 1][0]["start"] if g + 1 < len(groups) else None
        for i, w in enumerate(group):
            start = w["start"] - clip_start
            # Hold each word until the next one starts so captions never blink off mid-phrase.
            if i + 1 < len(group):
                end = group[i + 1]["start"] - clip_start
            else:
                end = w["end"] + 0.25
                if next_start is not None:
                    end = min(end, next_start)
                end = min(end, clip_end) - clip_start
            parts = []
            for j, x in enumerate(group):
                word = clean(x["word"])
                big = key(x["word"]) in emphasis
                if j == i or big:
                    colour = HIGHLIGHT if j == i else EMPHASIS
                    size = "\\fscx118\\fscy118" if big else ""
                    reset = "\\fscx100\\fscy100" if big else ""
                    word = f"{{\\c{colour}{size}}}{word}{{\\c{WHITE}{reset}}}"
                parts.append(word)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            events.append(f"Dialogue: 0,{ts(start)},{ts(end)},Word,,0,0,0,,{pop}{' '.join(parts)}")
    return HEADER + "\n".join(events) + "\n"
