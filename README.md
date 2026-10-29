# autoclip

Turns a long podcast video into vertical, captioned clips for Reels, Shorts and TikTok.
It is built to be driven by a Claude Code session: the code does the downloading,
transcription, reframing and captions, and Claude reads the transcript and picks the
clips. See [CLAUDE.md](CLAUDE.md) for the full workflow.

Day to day it runs on GitHub Actions, driven from a Claude session:

1. `transcribe` workflow: give it a YouTube link and an episode name. It downloads and
   transcribes on GitHub's runners and saves the transcript to the `episodes` branch.
2. Claude reads the transcript and saves `episodes/<name>/clips.json`.
3. `render` workflow: crops, captions and publishes the clips as the release
   `clips-<name>`, ready to download on your phone.

You can also trigger both workflows by hand from the repo's Actions tab, and everything
runs locally too:

```
./setup.sh
.venv/bin/python -m autoclip fetch <youtube-url-or-file> solved-ep42
.venv/bin/python -m autoclip transcribe solved-ep42
#   ...write ~/autoclip-work/solved-ep42/clips.json...
.venv/bin/python -m autoclip render solved-ep42 --out clips/
```

Each clip is 1080x1920 with the speaker kept in frame, word-by-word captions, an optional
hook box at the top, and loudness normalised for phones. Styles (`clean`, `fast`, `hype`)
add jump cuts over pauses, a slight speed-up, punch-in zooms, a progress bar and
highlighted key words; see the Style section of [CLAUDE.md](CLAUDE.md).

Everything uses free tools (ffmpeg, yt-dlp, faster-whisper, OpenCV). There is no API cost:
the clip choosing happens in the Claude session itself. A private repo gets 2,000 free
Actions minutes a month; an hour-long episode uses very roughly 40 to 70 of them.

Running locally needs network access to PyPI and npm (the Whisper model is fetched from an npm copy, since
Hugging Face is often blocked in cloud sessions), plus YouTube (`*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`)
or `drive.usercontent.google.com` for downloading from a link.
The npm model is a third-party redistribution of OpenAI's Whisper small; `setup.sh` checks it
against the checksum published with it.
