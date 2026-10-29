# autoclip

Turns a long podcast video into vertical, captioned clips for Reels, Shorts and TikTok.
It is built to be driven by a Claude Code session: the code does the downloading,
transcription, reframing and captions, and Claude reads the transcript and picks the
clips. See [CLAUDE.md](CLAUDE.md) for the full workflow.

```
./setup.sh
.venv/bin/python -m autoclip fetch <youtube-url-or-file> solved-ep42
.venv/bin/python -m autoclip transcribe solved-ep42
#   ...Claude writes ~/autoclip-work/solved-ep42/clips.json...
.venv/bin/python -m autoclip render solved-ep42 --out /mnt/project-files/autoclip/clips
```

Each clip is 1080x1920 with the speaker kept in frame, word-by-word captions, an optional
hook box at the top, and loudness normalised for phones.

Everything runs locally with free tools (ffmpeg, yt-dlp, faster-whisper, OpenCV). There
is no API cost: the clip choosing happens in the Claude session itself.

Network access needed: PyPI and npm (the Whisper model is fetched from an npm copy, since
Hugging Face is often blocked in cloud sessions), plus YouTube (`*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`)
or `drive.usercontent.google.com` for downloading from a link.
The npm model is a third-party redistribution of OpenAI's Whisper small; `setup.sh` checks it
against the checksum published with it.
