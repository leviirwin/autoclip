# autoclip: runbook for the clipping session

You are the clip editor. The code here does the mechanical work (download, transcribe,
reframe, caption). Your job is the part that needs judgement: reading the transcript and
choosing the moments worth posting. The goal is short-form clips of podcasts (currently
*Solved with Mark Manson*) for one Instagram Reels account, about 2 posted a day.

## Folders

- `/mnt/project-files/autoclip/inbox/` and `/mnt/project-files/uploads/hearth/`: where the
  user leaves source videos (uploads have no file extension).
- `~/autoclip-work/<name>/`: scratch workspace for one episode. Lost when the session ends.
- `/mnt/project-files/autoclip/clips/<name>/`: finished clips, `captions.md`, plus the
  transcript and `clips.json` so a later session can re-render without re-transcribing.

## Steps

1. `./setup.sh` once per session (installs deps; fetches the Whisper model from npm, since
   Hugging Face is blocked in this project's cloud sessions).
2. `.venv/bin/python -m autoclip fetch <url-or-path> <name>`. `<name>` is short and
   unique, e.g. `solved-ep42`. If a YouTube download is refused, ask the user to upload the
   file instead. If `/mnt/project-files/autoclip/clips/<name>/transcript.json` already
   exists, copy it into the workspace and skip step 3.
3. `.venv/bin/python -m autoclip transcribe <name>`. The default `small` model runs on CPU;
   use `--model base` if it is too slow for a long episode.
4. Read `~/autoclip-work/<name>/transcript.txt` in full and write
   `~/autoclip-work/<name>/clips.json` (format below).
5. `.venv/bin/python -m autoclip render <name> --out /mnt/project-files/autoclip/clips`
6. Look at every `~/autoclip-work/<name>/*.preview.jpg` (4 frames per clip). Check the
   speaker's face is in frame, captions are readable and not covering the face, and the
   hook text fits. Fix `clips.json` and re-render with `--only N` if needed.
7. Give the user the clips and `captions.md` from the shared folder.

## Picking clips

Pick 3 to 5 candidates per episode so the user can choose the best 2. Each one must:

- **Open on the hook.** The first sentence alone has to make a scroller stop: a bold
  claim, a surprising number, a question, a contrarian take, or a vivid story opening.
  Never start on "so", "yeah", "and", a filler answer, or the end of the host's question.
- **Stand alone.** Someone who has not heard the episode understands it with no setup.
  No "like I said earlier", no unexplained "he" or "that".
- **Pay off.** End on the punchline, the conclusion, or a line that lands, not mid-thought.
- **Be 15 to 45 seconds.** 20 to 35 is the sweet spot. A tighter clip beats a complete one.
- **Not overlap** another chosen clip.

Prefer moments with emotion, humour, a concrete example, or advice people will want to
save and share. Avoid ad reads, sponsor segments, and inside references.

Times come from the `[... | 123.4-127.9]` seconds in `transcript.txt`. You don't need to
be exact: the renderer snaps to whole words and adds a small lead-in and tail.

## clips.json

```json
{
  "clips": [
    {
      "start": 1234.5,
      "end": 1262.0,
      "title": "Happiness is a side effect",
      "hook": "Nobody tells you this about happiness",
      "caption": "One or two lines for the Instagram post, in a natural voice. Credit: @markmanson",
      "hashtags": ["#markmanson", "#selfimprovement", "#podcast"],
      "why": "Contrarian opener, clear payoff at the end"
    }
  ]
}
```

- `title`: used for the file name.
- `hook`: on-screen text box shown at the top for the whole clip. Under 8 words, in
  the user's voice, and it should add curiosity rather than repeat the first line. Leave
  it out for no box; add `"hook_seconds": 3` to show it only at the start.
- `caption` / `hashtags`: written to `captions.md` for the user to paste. 3 to 6 hashtags.
- Always credit the creator in the caption; check the user's clipping-program rules for
  required tags.

## Code map

- `autoclip/transcribe.py`: faster-whisper, word timestamps, builds `transcript.txt`.
- `autoclip/track.py`: Haar face detection at 5 fps, then a steady crop path (glides for
  small moves, hard cut when a different face holds the frame for 0.6 s).
- `autoclip/captions.py`: ASS subtitles, 1 to 3 words on screen, active word in yellow.
- `autoclip/render.py`: snaps times to words, crops frames, encodes 1080x1920 H.264 with
  loudness normalised to -14 LUFS, and saves preview strips.
- `tests/`: `.venv/bin/python -m pytest -q tests`
