"""Download a source video from a Google Drive share link or anything yt-dlp handles."""
import re
import subprocess
import sys
import urllib.request

DRIVE_ID = re.compile(r"(?:/file/d/|[?&]id=)([\w-]{20,})")
CHUNK = 1 << 20


def drive_id(url):
    if "drive.google.com" not in url and "drive.usercontent.google.com" not in url:
        return None
    m = DRIVE_ID.search(url)
    return m.group(1) if m else None


def download_drive(file_id, dest):
    """The file must be shared as "Anyone with the link". confirm=t skips Drive's
    can't-scan-for-viruses page that large files otherwise get."""
    url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
    with urllib.request.urlopen(url) as resp:
        if resp.headers.get_content_type() == "text/html":
            sys.exit("Drive returned a web page, not the video. Set the file's sharing to "
                     "'Anyone with the link' and try again.")
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while chunk := resp.read(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if done % (50 * CHUNK) < CHUNK:
                    print(f"  {done / CHUNK:.0f} / {total / CHUNK:.0f} MB", flush=True)
    return dest


def download(url, d):
    file_id = drive_id(url)
    if file_id:
        return download_drive(file_id, d / "source.mp4")
    subprocess.run([
        sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4", "-o", str(d / "source.%(ext)s"), url,
    ], check=True)
