"""Command line: fetch -> transcribe -> (Claude writes clips.json) -> render."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .download import download
from .render import render_clip, slugify

WORK = Path.home() / "autoclip-work"
LOCAL_MODEL = Path(__file__).resolve().parent.parent / "models" / "whisper-small"
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".m4v")


def episode_dir(name):
    d = WORK / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_video(d):
    for ext in VIDEO_EXTS:
        p = d / f"source{ext}"
        if p.exists():
            return p
    sys.exit(f"no source video in {d}; run `fetch` first")


def cmd_fetch(args):
    d = episode_dir(args.name)
    src = args.source
    if src.startswith(("http://", "https://")):
        download(src, d, args.section)
    else:
        p = Path(src)
        if not p.exists():
            sys.exit(f"not found: {p}")
        # Uploads from the app arrive with no extension; ffmpeg sniffs the real format anyway.
        ext = p.suffix.lower() if p.suffix.lower() in VIDEO_EXTS else ".mp4"
        dest = d / f"source{ext}"
        print(f"copying {p} -> {dest}")
        shutil.copyfile(p, dest)
    print(f"ready: {source_video(d)}")


def cmd_transcribe(args):
    from .transcribe import transcribe

    d = episode_dir(args.name)
    model = args.model or (str(LOCAL_MODEL) if LOCAL_MODEL.exists() else "small")
    words, lines = transcribe(source_video(d), d, model)
    print(f"{len(words)} words, {len(lines)} lines -> {d / 'transcript.txt'}")


def cmd_render(args):
    d = episode_dir(args.name)
    video = source_video(d)
    words = json.loads((d / "transcript.json").read_text())["words"]
    clips = json.loads((d / "clips.json").read_text())["clips"]
    out = d / "out"
    out.mkdir(parents=True, exist_ok=True)
    only = {int(x) for x in args.only.split(",")} if args.only else None

    notes = [f"# Clips from {args.name}\n"]
    for n, clip in enumerate(clips, 1):
        if only and n not in only:
            continue
        name = f"{n:02d}-{slugify(clip.get('title', 'clip'))}"
        print(f"rendering {name} ({clip['start']}-{clip['end']}s)", flush=True)
        start, end = render_clip(video, words, clip, out / f"{name}.mp4", d,
                                 preview_path=d / f"{name}.preview.jpg")
        tags = " ".join(clip.get("hashtags", []))
        notes.append(
            f"## {name}.mp4 ({end - start:.0f}s, source {start:.1f}-{end:.1f}s)\n\n"
            f"{clip.get('caption', '').strip()}\n\n{tags}\n"
        )
    (out / "captions.md").write_text("\n".join(notes))
    # Keep the inputs with the clips so a later session can tweak and re-render without
    # transcribing again (this machine's disk does not survive between sessions).
    for f in ("clips.json", "transcript.json", "transcript.txt"):
        if (d / f).exists():
            shutil.copyfile(d / f, out / f)
    if args.out:
        # Render locally, then copy: the shared project folder is a slow network mount.
        dest = Path(args.out) / args.name
        shutil.copytree(out, dest, dirs_exist_ok=True)
        out = dest
    print(f"done -> {out}")


def main():
    p = argparse.ArgumentParser(prog="autoclip")
    sub = p.add_subparsers(required=True)

    f = sub.add_parser("fetch", help="download a Google Drive/YouTube link or copy a local video into the workspace")
    f.add_argument("source")
    f.add_argument("name", help="short episode name, e.g. solved-ep42")
    f.add_argument("--section", help="YouTube only: just this part, e.g. 00:10:00-00:40:00")
    f.set_defaults(func=cmd_fetch)

    t = sub.add_parser("transcribe", help="transcribe with word timestamps")
    t.add_argument("name")
    t.add_argument("--model", help="whisper size (tiny, base, small, medium) or model folder; "
                   "default: models/whisper-small from setup.sh")
    t.set_defaults(func=cmd_transcribe)

    r = sub.add_parser("render", help="render the clips listed in clips.json")
    r.add_argument("name")
    r.add_argument("--only", help="comma-separated clip numbers, e.g. 1,3")
    r.add_argument("--out", help="also copy finished clips to <out>/<name>/")
    r.set_defaults(func=cmd_render)

    args = p.parse_args()
    args.func(args)
