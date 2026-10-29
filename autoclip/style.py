"""Edit style: pacing, zoom punches, progress bar and caption options for a clip.

clips.json can set a top-level "style" (a preset name or a dict) and each clip can
override it with its own "style". Dicts are merged over the preset they name.
"""
import bisect

DEFAULTS = {
    "pace": "normal",      # "fast" trims pauses between words (jump cuts)
    "max_pause": 0.35,     # fast pace: pauses longer than this are trimmed
    "speed": 1.0,          # playback speed, pitch preserved
    "zoom": False,         # punch in and out on alternate sentences
    "zoom_amount": 1.18,
    "progress_bar": False,
    "caption_words": 3,    # most words on screen at once
    "emphasis": [],        # words drawn bigger in green (Claude picks them per clip)
}

PRESETS = {
    "clean": {},
    "fast": {"pace": "fast", "speed": 1.08, "zoom": True, "progress_bar": True, "caption_words": 2},
    "hype": {"pace": "fast", "max_pause": 0.2, "speed": 1.12, "zoom": True, "zoom_amount": 1.25,
             "progress_bar": True, "caption_words": 1},
}


def _expand(style):
    if not style:
        return {}
    if isinstance(style, str):
        if style not in PRESETS:
            raise ValueError(f"unknown style {style!r}; presets: {', '.join(PRESETS)}")
        return dict(PRESETS[style])
    out = _expand(style.get("preset"))
    out.update({k: v for k, v in style.items() if k != "preset"})
    return out


def resolve(*styles):
    """Merge styles left to right (e.g. file-wide, then clip, then command-line override)."""
    out = dict(DEFAULTS)
    for s in styles:
        out.update(_expand(s))
    unknown = set(out) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown style keys: {', '.join(sorted(unknown))}")
    return out


class Timeline:
    """Maps source time to output time when pauses are cut and speed changes."""

    def __init__(self, segments, speed=1.0):
        self.segs = segments  # [(src_start, src_end)] in source seconds, ascending
        self.speed = speed
        self.offsets = [0.0]  # output time (before speed) at the start of each segment
        for s, e in segments:
            self.offsets.append(self.offsets[-1] + (e - s))
        self.duration = self.offsets[-1] / speed

    def out(self, t):
        """Source time -> output time. Times inside a cut snap to the next kept segment."""
        i = bisect.bisect_right([s for s, _ in self.segs], t) - 1
        if i < 0:
            return 0.0
        s, e = self.segs[i]
        return (self.offsets[i] + min(t, e) - s) / self.speed

    def src(self, o):
        """Output time -> source time."""
        o = min(max(o * self.speed, 0.0), self.offsets[-1])
        i = min(bisect.bisect_right(self.offsets, o) - 1, len(self.segs) - 1)
        return self.segs[i][0] + o - self.offsets[i]


def keep_segments(words, start, end, max_pause=None):
    """Source spans to keep: the whole clip, or with long pauses between words trimmed."""
    if max_pause is None:
        return [(start, end)]
    segs, seg_start = [], start
    for a, b in zip(words, words[1:]):
        if b["start"] - a["end"] > max_pause:
            # Keep a little air after the last word and before the next one.
            segs.append((seg_start, a["end"] + max_pause * 0.6))
            seg_start = b["start"] - max_pause * 0.4
    segs.append((seg_start, end))
    return segs


def zoom_changes(words, amount, min_len=1.2):
    """[(src_time, zoom)] switching between 1 and `amount` at each sentence of min_len+ seconds."""
    changes, level, sent_start = [], 1.0, None
    for i, w in enumerate(words):
        if sent_start is None:
            sent_start = w["start"]
        last = i + 1 == len(words)
        if w["word"][-1] in ".?!" or last:
            if not changes or sent_start - changes[-1][0] >= min_len:
                changes.append((sent_start, level))
                level = amount if level == 1.0 else 1.0
            sent_start = None
    return changes or [(0.0, 1.0)]
