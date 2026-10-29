# autoclip: runbook for the clipping session

You are the clip editor. The code here does the mechanical work (download, transcribe,
reframe, caption). Your job is the part that needs judgement: reading the transcript and
choosing the moments worth posting. The goal is short-form clips of podcasts (currently
*Solved with Mark Manson*) for one Instagram Reels account, about 2 posted a day.

## How it runs

The heavy work (download, transcription, rendering) runs on **GitHub Actions** in this repo,
because the Claude cloud session can't reach YouTube. You only read the transcript, write
`clips.json`, and check preview images. All per-episode data lives on the data-only
`episodes` branch under `episodes/<name>/`.

`<name>` is short and unique, e.g. `solved-ep42`.

1. **Transcribe.** Trigger the `transcribe` workflow (GitHub MCP `actions_run_trigger`,
   workflow `transcribe.yml`, ref `main`) with inputs `url` (YouTube link, or a Google Drive
   link shared as "Anyone with the link"), `name`, and optionally `section`
   (`HH:MM:SS-HH:MM:SS`) to fetch only part of a long episode, and `names`: the guest,
   hosts, companies and other proper nouns in the episode, comma separated, so Whisper
   spells them right. An hour of audio takes
   roughly 30 to 60 minutes on GitHub's runners. Don't poll in a loop: check the run once
   after a sensible wait, or schedule a reminder.
2. **Read.** `git fetch origin episodes` then
   `git show FETCH_HEAD:episodes/<name>/transcript.txt`. Read it in full.
3. **Choose.** Write `clips.json` (format below) and save it:
   `tools/save_to_episodes.sh <name> "Clips: <name>" path/to/clips.json`
4. **Render.** Trigger the `render` workflow (`render.yml`, ref `main`) with input `name`
   (and optionally `style` to override clips.json, see Style below).
5. **Check.** When it finishes, fetch `episodes` again and look at
   `episodes/<name>/*.preview.jpg` (4 frames per clip): the speaker's face is in frame,
   captions are readable and don't cover the face, the hook text fits. To fix a clip, edit
   `clips.json`, save it again and re-run `render`.
6. **Deliver.** The clips and `captions.md` are on the release
   `https://github.com/leviirwin/autoclip/releases/tag/clips-<name>`. Send the user that link.

If a YouTube download fails on GitHub with "Sign in to confirm you're not a bot", YouTube
is blocking GitHub's servers. The fix is the `YT_COOKIES` repo secret: the contents of a
cookies.txt exported from a browser signed in to a spare Google account (not the user's
main one). Until then, ask the user to put the file in Google Drive, share it as "Anyone
with the link", and use that link.

### Running locally instead

The same commands work in any environment that can reach the source:
`./setup.sh`, then `.venv/bin/python -m autoclip fetch <url-or-path> <name>`,
`transcribe <name>`, write `~/autoclip-work/<name>/clips.json`, and
`render <name> --out <folder>`. `setup.sh` fetches the Whisper model from npm because
Hugging Face is blocked in Claude cloud sessions.

## Picking clips

Pick 3 to 5 candidates per episode so the user can choose the best 2. Each one must:

- **Open on the hook.** The first sentence alone has to make a scroller stop: a bold
  claim, a surprising number, a question, a contrarian take, or a vivid story opening.
  Never start on "so", "yeah", "and", a filler answer, or the end of the host's question.
- **Stand alone.** Someone who has not heard the episode understands it with no setup.
  No "like I said earlier", no unexplained "he" or "that".
- **Pay off.** End on the punchline, the conclusion, or a line that lands, not mid-thought.
- **Be 30 to 60 seconds** by default (the user asked for longer clips), 35 to 50 is the
  sweet spot. Pick a span that holds one complete story or argument, not a padded one.
  If the user asks for short clips, use 15 to 30; for long ones, 60 to 90. With the
  `fast` or `hype` style the finished clip runs about 10 to 20% shorter than the span
  you choose, because pauses are cut and playback is sped up.
- **Not overlap** another chosen clip.

Prefer moments with emotion, humour, a concrete example, or advice people will want to
save and share. Avoid ad reads, sponsor segments, and inside references.

Times come from the `[... | 123.4-127.9]` seconds in `transcript.txt`. You don't need to
be exact: the renderer snaps to whole words and adds a small lead-in and tail.

## clips.json

```json
{
  "style": "fast",
  "clips": [
    {
      "start": 1234.5,
      "end": 1262.0,
      "title": "Happiness is a side effect",
      "hook": "Nobody tells you this about happiness",
      "caption": "One or two lines for the Instagram post, in a natural voice. Credit: @markmanson",
      "hashtags": ["#markmanson", "#selfimprovement", "#podcast"],
      "emphasis": ["happiness", "never"],
      "why": "Contrarian opener, clear payoff at the end"
    }
  ]
}
```

- `title`: used for the file name.
- `hook`: on-screen text box shown at the top for the whole clip. Under 8 words, in
  the user's voice, and it should add curiosity rather than repeat the first line. Leave
  it out for no box; add `"hook_seconds": 3` to show it only at the start.
- `emphasis`: 2 to 5 key words from the clip (a number, the topic, a strong word) that
  show bigger and in green whenever they are on screen. Optional.
- `corrections` (top level, optional): `{"misheard": "Right"}` single-word fixes applied
  to the captions. Check names and numbers in the transcript and fix any Whisper got
  wrong; campaigns reject clips with wrong names.
- `caption` / `hashtags`: written to `captions.md` for the user to paste. 3 to 6 hashtags.
- Always credit the creator in the caption; check the user's clipping-program rules for
  required tags.

## Style

The top-level `style` sets the edit for every clip; a clip can override it with its own
`"style"`. Use a preset name, or a dict of settings merged over a preset, e.g.
`{"preset": "fast", "zoom": false}`.

| preset | what it does |
|---|---|
| `clean` (default) | straight cut, 3-word captions |
| `fast` | trims pauses longer than 0.35 s (jump cuts), 1.08x speed, punch-in zoom on alternate sentences, progress bar at the top, 2-word captions |
| `hype` | `fast` but tighter: 0.2 s pauses, 1.12x, stronger zoom, one word at a time |

Settings: `pace` (`normal`/`fast`), `max_pause`, `speed`, `zoom`, `zoom_amount`,
`progress_bar`, `caption_words`. Use `fast` unless the user asks otherwise; use `clean`
for slow, emotional moments where jump cuts would feel wrong. The `render` workflow and
the `render` command take an optional `style` that overrides clips.json for a quick
comparison.

## Posting and views (social job)

The `social` workflow runs every 15 minutes on GitHub Actions (`autoclip/social.py`). It
posts what is due in the post queue, refreshes every Reel's views hourly, comments on the
"Clip alerts" issue (mentioning the user) when a clip is posted or passes 1,000 views, and
rewrites the dashboard: `https://github.com/leviirwin/autoclip/tree/social` (README.md).
Its files live on the data-only `social` branch:

- `queue.txt`: one post per line, `account | release | clip | when`, e.g.
  `@micdup.daily | clips-solved-shame | 4`. Leave `when` out to let the job space posts
  (`min_gap_hours`, `post_hours` in `config.json`), or use `now` or `YYYY-MM-DD HH:MM`.
  The caption comes from the release's captions.md and the campaign's tags are added.
  When the user asks to post a batch, append lines (one clip per account, different cuts
  per account) and save with
  `tools/save_to_branch.sh social . "Queue: <what>" path/to/queue.txt` (fetch the current
  file first and append; never drop lines the user wrote). Trigger `social.yml` to post
  `now` lines straight away.
- `submitted.txt`: links already submitted to Content Rewards, one per line.
- `config.json`: campaigns (name, page, rate, release prefixes, required tags), timezone,
  posting window. Add a campaign here when the user starts a new one.
- `state.json`, `tokens.enc`, `README.md`: written by the job; don't edit.

Secrets: `IG_TOKENS` (one Instagram API token per line, one per account), `STATE_KEY`
(any long random string; encrypts refreshed tokens), optional `NTFY_TOPIC` for phone
pushes through the ntfy app.

## Code map

- `autoclip/transcribe.py`: faster-whisper, word timestamps, builds `transcript.txt`.
- `autoclip/track.py`: Haar face detection at 5 fps, then a steady crop path (glides for
  small moves, hard cut when a different face holds the frame for 0.6 s).
- `autoclip/captions.py`: ASS subtitles, 1 to 3 words on screen, active word in yellow.
- `.github/workflows/transcribe.yml`, `render.yml`: the GitHub Actions steps above.
- `tools/save_to_episodes.sh`: commits files into `episodes/<name>/` on the `episodes` branch
  (a wrapper around `tools/save_to_branch.sh`).
- `autoclip/instagram.py`, `autoclip/social.py`, `.github/workflows/social.yml`: the social job above.
- `autoclip/style.py`: style presets, pause trimming (`Timeline` maps source time to
  output time), and zoom punches.
- `autoclip/render.py`: snaps times to words, crops frames, encodes 1080x1920 H.264 with
  loudness normalised to -14 LUFS, and saves preview strips.
- `tests/`: `.venv/bin/python -m pytest -q tests`
