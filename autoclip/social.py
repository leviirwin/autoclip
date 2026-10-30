"""The scheduled social job: post queued clips to Instagram, track every Reel's views, send
an alert when one passes 1,000 views, and write the dashboard with the links to submit.

Runs on GitHub Actions every 15 minutes (.github/workflows/social.yml) as
`python3 -m autoclip.social`. Standard library only, so it starts in seconds.

This repo is public, so the job keeps its data, the dashboard and the alerts in a private
repo: DATA_REPO (e.g. leviirwin/clip-dashboard, its default branch), reached with the
DATA_TOKEN secret. The public job logs say only what happened, never which account or
link. Without DATA_REPO it falls back to a `social` branch of this repo. The data files:

    config.json     campaigns, timezone, posting window, gap between posts
    queue.txt       what to post (you or Claude edit this)
    submitted.txt   links already submitted to Content Rewards (one per line)
    state.json      every Reel's stats and what this job posted (written by the job)
    tokens.enc      refreshed Instagram tokens, encrypted with the STATE_KEY secret
    README.md       the dashboard
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import instagram as ig

DATA_REPO = os.environ.get("DATA_REPO", "")
BRANCH = "main" if DATA_REPO else "social"
ROOT = Path(__file__).resolve().parent.parent
SYNC_EVERY = timedelta(minutes=55)
REFRESH_EVERY = timedelta(days=7)
MAX_ATTEMPTS = 3

DEFAULT_CONFIG = {
    "timezone": "UTC",
    "post_hours": [0, 24],
    "min_gap_hours": 3,
    "alert_views": 1000,
    "campaigns": [
        {"name": "Greg Lav", "rate": 0.75, "min_payout": 1.5,
         "page": "https://contentrewards.com/c/campaigns/a6e3a983-c8c7-4c4f-ac48-22022b391562",
         "releases": ["clips-icedcoffee-greglav"], "tags": ["@greglav", "@theicedcoffeehour"]},
        {"name": "Solved with Mark Manson", "rate": 1.25, "min_payout": 3,
         "page": "https://contentrewards.com/c/campaigns/6bdc8e4b-3ebf-44f1-aedb-067589ca9c89",
         "releases": ["clips-solved-"], "tags": ["@solvedpodcast", "@markmanson"]},
    ],
}

QUEUE_TEMPLATE = """\
# Clips to post, one per line:   account | release | clip number | when
#
#   @micdup.daily | clips-solved-shame | 4
#   @soundbite.club | clips-icedcoffee-greglav | 15 | now
#   @two.hours.in | clips-solved-shame | 6 | 2026-10-30 18:30
#
# "when" left out: the next free slot, at least min_gap_hours after that account's last
# Reel and inside post_hours (both in config.json). "now": the next run (within 15 minutes).
# A date and time is in the timezone set in config.json.
# An optional 5th field replaces the caption from the release's captions.md.
# The campaign's tags are added to the caption when they are missing.
# Posted lines are skipped, so you can leave them here or delete them.
"""


# ---------------------------------------------------------------- queue and scheduling

def parse_when(text, tz):
    text = text.strip().lower()
    if text in ("", "next", "auto"):
        return None
    if text == "now":
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz).astimezone(timezone.utc)
        except ValueError:
            pass
    raise ValueError(f'can\'t read the time "{text}" (use YYYY-MM-DD HH:MM)')


def parse_queue(text, tz):
    """Queue lines -> (items, problems). Each item has a key that marks it posted."""
    items, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        try:
            if len(parts) < 3:
                raise ValueError("needs at least account | release | clip")
            account = "@" + parts[0].lstrip("@").lower()
            release = parts[1]
            clip = int(re.sub(r"\D", "", parts[2]) or "x")
            when = parse_when(parts[3] if len(parts) > 3 else "", tz)
        except ValueError as e:
            problems.append(f"queue.txt line {n}: {e}")
            continue
        items.append({"account": account, "release": release, "clip": clip, "when": when,
                      "caption": "|".join(parts[4:]).strip() or None,
                      "key": f"{release}#{clip:02d}{account}"})
    return items, problems


def last_post(account, state):
    """When the account last got a Reel: posted by this job or by hand."""
    times = [p["at"] for p in state.get("posted", {}).values() if p["account"] == account]
    times += [m["posted"] for m in state.get("media", {}).values() if m["account"] == account]
    return max((datetime.fromisoformat(t) for t in times), default=None)


def due(items, state, cfg, now):
    """The items to post this run: at most one per account, in queue order."""
    tz = ZoneInfo(cfg["timezone"])
    start, end = cfg["post_hours"]
    in_window = start <= now.astimezone(tz).hour < end
    gap = timedelta(hours=cfg["min_gap_hours"])
    picked, busy = [], set()
    for it in items:
        if it["key"] in state["posted"] or it["account"] in busy:
            continue
        if state["failed"].get(it["key"], {}).get("attempts", 0) >= MAX_ATTEMPTS:
            continue
        if it["when"] is not None:
            ok = it["when"] <= now
        else:
            last = last_post(it["account"], state)
            ok = in_window and (last is None or now - last >= gap)
        busy.add(it["account"])  # later items for this account wait their turn
        if ok:
            picked.append(it)
    return picked


# ---------------------------------------------------------------- campaigns and captions

def campaign_for(cfg, release=None, caption=""):
    caption = (caption or "").lower()
    for c in cfg["campaigns"]:
        if release and any(release.startswith(r) for r in c.get("releases", [])):
            return c
    for c in cfg["campaigns"]:
        if any(t.lower() in caption for t in c.get("tags", [])):
            return c
    return None


def caption_from_notes(notes, clip):
    """The caption and hashtags for clip `clip` in a release's captions.md."""
    m = re.search(rf"^## {clip:02d}-[^\n]*\n(.*?)(?=^## |\Z)", notes, re.M | re.S)
    if not m:
        raise ValueError(f"clip {clip:02d} is not in captions.md")
    return m.group(1).strip()


def with_tags(caption, campaign):
    missing = [t for t in (campaign or {}).get("tags", []) if t.lower() not in caption.lower()]
    return f"{caption}\n\n{' '.join(missing)}" if missing else caption


def earned(views, campaign):
    return views / 1000 * campaign["rate"] if campaign else 0.0


# ---------------------------------------------------------------- dashboard

def fmt_time(iso, tz):
    return datetime.fromisoformat(iso).astimezone(tz).strftime("%d %b %H:%M")


def dashboard(state, cfg, items, submitted, problems, now, repo=""):
    tz = ZoneInfo(cfg["timezone"])
    media = sorted(state["media"].values(), key=lambda m: m["posted"], reverse=True)
    by_name = {c["name"]: c for c in cfg["campaigns"]}
    total_views = sum(m.get("views", 0) for m in media)
    total_usd = sum(earned(m.get("views", 0), by_name.get(m.get("campaign"))) for m in media)
    edit = f"https://github.com/{repo}/edit/{BRANCH}" if repo else None
    out = ["# Clip dashboard", "",
           f"Updated {now.astimezone(tz):%d %b %H:%M} ({cfg['timezone']}). "
           f"{len(media)} Reels, {total_views:,} views, about ${total_usd:,.2f} by the campaign rates.",
           ""]
    if edit:
        out += [f"[Edit the post queue]({edit}/queue.txt) · [Mark links submitted]({edit}/submitted.txt)"
                f" · [Settings]({edit}/config.json)", ""]

    if problems:
        out += ["## Needs attention", ""] + [f"- {p}" for p in problems] + [""]

    waiting = [it for it in items if it["key"] not in state["posted"]]
    if waiting:
        out += ["## Up next", "", "| Account | Clip | When |", "|---|---|---|"]
        for it in waiting:
            when = it["when"].astimezone(tz).strftime("%d %b %H:%M") if it["when"] else "next free slot"
            if it["when"] and it["when"].year == 2000:
                when = "now"
            fail = state["failed"].get(it["key"])
            if fail:
                when += f" (failed {fail['attempts']}x: {fail['error']})"
            out.append(f"| {it['account']} | {it['release']} {it['clip']:02d} | {when} |")
        out.append("")

    accounts = {}
    for m in media:
        a = accounts.setdefault(m["account"], [0, 0, 0])
        a[0] += 1
        a[1] += m.get("views", 0)
        a[2] = max(a[2], m.get("views", 0))
    if accounts:
        out += ["## Accounts", "", "| Account | Reels | Views | Best |", "|---|---|---:|---:|"]
        for name, (n, views, best) in sorted(accounts.items(), key=lambda kv: -kv[1][1]):
            out.append(f"| {name} | {n} | {views:,} | {best:,} |")
        out.append("")

    groups = {}
    for m in media:
        groups.setdefault(m.get("campaign") or "Other", []).append(m)
    order = [c["name"] for c in cfg["campaigns"]] + ["Other"]

    out += ["## Links to submit", "",
            "Newest first. ⬜ not submitted yet, ✅ listed in submitted.txt. Content Rewards "
            "wants each link within 30 minutes of posting.", ""]
    for name in order:
        if name not in groups:
            continue
        c = by_name.get(name)
        recent = [m for m in groups[name]
                  if now - datetime.fromisoformat(m["posted"]) <= timedelta(days=30)]
        head = f"[{name}]({c['page']}) · ${c['rate']:.2f} per 1K views" if c else name
        out += [f"### {head}", ""]
        for m in recent:
            mark = "✅" if shortcode(m["permalink"]) in submitted else "⬜"
            out.append(f"- {mark} {m['permalink']} · {m['account']} · {fmt_time(m['posted'], tz)}")
        out.append("")

    out += ["## All Reels", "", f"🔥 = over {cfg['alert_views']:,} views.", ""]
    for name in order:
        if name not in groups:
            continue
        c = by_name.get(name)
        views = sum(m.get("views", 0) for m in groups[name])
        out += [f"### {name}: {views:,} views" + (f", about ${earned(views, c):,.2f}" if c else ""), "",
                "| Posted | Account | Clip | Views | Likes | Shares | |", "|---|---|---|---:|---:|---:|---|"]
        for m in groups[name]:
            fire = " 🔥" if m.get("views", 0) >= cfg["alert_views"] else ""
            out.append(f"| {fmt_time(m['posted'], tz)} | {m['account']} | {m.get('label', '')} | "
                       f"{m.get('views', 0):,}{fire} | {m.get('likes', 0):,} | {m.get('shares', 0):,} | "
                       f"[open]({m['permalink']}) |")
        out.append("")
    return "\n".join(out)


def shortcode(url):
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p)/([\w-]+)", url or "")
    return m.group(1) if m else (url or "").strip()


def label_for(caption):
    first = (caption or "").strip().splitlines()[0] if (caption or "").strip() else ""
    return (first[:47] + "...") if len(first) > 50 else first


# ---------------------------------------------------------------- tokens

def token_id(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def crypt(data, key, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-a", "-A", "-pass", "env:OPENSSL_PASS"]
    if decrypt:
        args.append("-d")
    r = subprocess.run(args, input=data, capture_output=True, text=True,
                       env=dict(os.environ, OPENSSL_PASS=key))
    if r.returncode:
        raise ValueError("could not decrypt tokens.enc (did STATE_KEY change?)")
    return r.stdout


def load_tokens(secret, blob, key):
    """Tokens from the IG_TOKENS secret (one per line), swapped for their refreshed versions
    from tokens.enc when there are some. Keyed by a hash of the token as pasted, so pasting a
    new token for an account replaces the stored one."""
    stored = {}
    if blob and key:
        try:
            stored = json.loads(crypt(blob, key, decrypt=True))
        except ValueError as e:
            print(f"warning: {e}", file=sys.stderr)
    out = {}
    for line in secret.split():
        t = line.strip()
        if t:
            out[token_id(t)] = stored.get(token_id(t)) or {"token": t, "refreshed": None}
    return out


# ---------------------------------------------------------------- the social branch

def data_env():
    """Environment for git and gh calls that touch the data repo. The token goes in as an
    auth header through git's GIT_CONFIG_* variables, never in a URL or on a command line."""
    env = dict(os.environ)
    token = os.environ.get("DATA_TOKEN")
    if DATA_REPO and token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}", GH_TOKEN=token,
                   SAVE_REMOTE=f"https://github.com/{DATA_REPO}.git")
    return env


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check,
                          env=data_env())


def load_branch():
    """{name: text} of the data files, or {} when the data branch doesn't exist yet."""
    remote = f"https://github.com/{DATA_REPO}.git" if DATA_REPO else "origin"
    if git("fetch", "-q", "--depth", "1", remote, f"+{BRANCH}:refs/remotes/data/{BRANCH}",
           check=False).returncode:
        return {}
    files = {}
    for name in ("config.json", "queue.txt", "submitted.txt", "state.json", "tokens.enc"):
        r = git("show", f"data/{BRANCH}:{name}", check=False)
        if r.returncode == 0:
            files[name] = r.stdout
    return files


def save_branch(files, message):
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, text in files.items():
            (Path(d) / name).write_text(text)
            paths.append(str(Path(d) / name))
        subprocess.run([str(ROOT / "tools" / "save_to_branch.sh"), BRANCH, ".", message, *paths],
                       cwd=ROOT, check=True, env=data_env())


# ---------------------------------------------------------------- alerts

def notify(text, state, link=None):
    """A comment on the "Clip alerts" issue mentioning the repo owner (GitHub emails it and
    the GitHub app pushes it), plus a phone push through ntfy.sh if NTFY_TOPIC is set."""
    print("sending an alert")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    env = data_env()
    if owner and env.get("GH_TOKEN"):
        body = f"@{owner} {text}"
        repo = ["--repo", DATA_REPO] if DATA_REPO else []
        num = state.get("alert_issue")
        ok = num and subprocess.run(["gh", "issue", "comment", str(num), *repo, "--body", body],
                                    cwd=ROOT, capture_output=True, env=env).returncode == 0
        if not ok:
            r = subprocess.run(["gh", "issue", "create", *repo, "--title", "Clip alerts", "--body",
                                f"Posting confirmations and view alerts from the social job.\n\n{body}"],
                               cwd=ROOT, capture_output=True, text=True, env=env)
            if r.returncode == 0:
                state["alert_issue"] = int(r.stdout.strip().rstrip("/").rsplit("/", 1)[-1])
            else:
                print("warning: could not post to the Clip alerts issue", file=sys.stderr)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        headers = {"Title": "Clips"}
        if link:
            headers["Click"] = link
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=text.encode(), headers=headers), timeout=20)
        except OSError as e:
            print(f"warning: ntfy failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------- the run

def release_files(release, clip, dest):
    """Download the clip's mp4 and the release's captions.md into dest."""
    subprocess.run(["gh", "release", "download", release, "--dir", str(dest), "--clobber",
                    "--pattern", f"{clip:02d}-*.mp4", "--pattern", "captions.md"],
                   cwd=ROOT, check=True, capture_output=True)
    video = next(Path(dest).glob(f"{clip:02d}-*.mp4"), None)
    if not video:
        raise ValueError(f"no clip {clip:02d} in release {release}")
    return video, (Path(dest) / "captions.md").read_text()


def post(item, account, cfg, state, now):
    campaign = campaign_for(cfg, release=item["release"])
    with tempfile.TemporaryDirectory() as d:
        video, notes = release_files(item["release"], item["clip"], d)
        caption = with_tags(item["caption"] or caption_from_notes(notes, item["clip"]), campaign)
        print("posting a clip", flush=True)
        media = ig.publish_reel(account["token"], account["user_id"], video, caption)
    posted = media.get("timestamp")
    posted = (datetime.strptime(posted, "%Y-%m-%dT%H:%M:%S%z").isoformat() if posted else now.isoformat())
    label = f"{item['release'].removeprefix('clips-')} {item['clip']:02d}"
    state["posted"][item["key"]] = {"account": item["account"], "at": posted, "id": media["id"],
                                    "permalink": media["permalink"], "label": label}
    state["media"][media["id"]] = {"account": item["account"], "posted": posted,
                                   "permalink": media["permalink"], "label": label,
                                   "campaign": campaign["name"] if campaign else None, "views": 0}
    state["failed"].pop(item["key"], None)
    deadline = (datetime.fromisoformat(posted) + timedelta(minutes=30)).astimezone(ZoneInfo(cfg["timezone"]))
    where = f" at {campaign['name']}: {campaign['page']}" if campaign else ""
    notify(f"Posted {label} on {item['account']}: {media['permalink']}\n\n"
           f"Submit by {deadline:%H:%M}{where}", state, link=media["permalink"])


def sync(accounts, cfg, state, now):
    """Refresh every Reel's stats and alert on the ones that crossed the threshold."""
    by_id = {p["id"]: p for p in state["posted"].values()}
    by_name = {c["name"]: c for c in cfg["campaigns"]}
    for name, acc in accounts.items():
        try:
            reels = ig.reels(acc["token"])
        except ig.ApiError as e:
            state["problems"].append(f"{name}: could not read Reels ({e})")
            continue
        for r in reels:
            old = state["media"].get(r["id"], {})
            mine = by_id.get(r["id"])
            campaign = old.get("campaign") or (campaign_for(cfg, caption=r.get("caption")) or {}).get("name")
            state["media"][r["id"]] = {
                "account": name, "permalink": r["permalink"], "campaign": campaign,
                "posted": datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%S%z").isoformat(),
                "label": mine["label"] if mine else old.get("label") or label_for(r.get("caption")),
                "views": r.get("views", 0), "likes": r.get("likes", r.get("like_count", 0)),
                "comments": r.get("comments", r.get("comments_count", 0)),
                "shares": r.get("shares", 0), "saved": r.get("saved", 0), "reach": r.get("reach", 0),
            }
            m = state["media"][r["id"]]
            if m["views"] >= cfg["alert_views"] and r["id"] not in state["alerted"]:
                state["alerted"].append(r["id"])
                usd = earned(m["views"], by_name.get(campaign))
                notify(f"🔥 {m['label'] or 'A Reel'} on {name} passed {cfg['alert_views']:,} views "
                       f"({m['views']:,}, about ${usd:.2f}): {m['permalink']}", state, link=m["permalink"])
    state["synced"] = now.isoformat()


def main():
    p = argparse.ArgumentParser(prog="autoclip.social", description=__doc__.split("\n\n")[0])
    p.add_argument("--sync", action="store_true", help="refresh view counts even if done recently")
    p.add_argument("--no-post", action="store_true", help="don't post anything this run")
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    if DATA_REPO and not os.environ.get("DATA_TOKEN"):
        sys.exit(f"Add the DATA_TOKEN secret: a token that can write to {DATA_REPO}.")

    files = load_branch()
    cfg = dict(DEFAULT_CONFIG, **json.loads(files.get("config.json") or "{}"))
    state = json.loads(files.get("state.json") or "{}")
    for k, v in (("posted", {}), ("media", {}), ("failed", {}), ("alerted", []), ("tokens", {})):
        state.setdefault(k, v)
    state["problems"] = []
    out = {"state.json": None, "README.md": None}
    if "config.json" not in files:
        out["config.json"] = json.dumps(DEFAULT_CONFIG, indent=2) + "\n"
    if "queue.txt" not in files:
        out["queue.txt"] = QUEUE_TEMPLATE
    if "submitted.txt" not in files:
        out["submitted.txt"] = "# Links already submitted to Content Rewards, one per line.\n"
    tz = ZoneInfo(cfg["timezone"])

    # Tokens: refresh weekly so they never hit their 60-day expiry.
    key = os.environ.get("STATE_KEY", "")
    tokens = load_tokens(os.environ.get("IG_TOKENS", ""), files.get("tokens.enc"), key)
    if not tokens:
        state["problems"].append("No Instagram accounts connected: add the IG_TOKENS secret.")
    if tokens and not key:
        state["problems"].append("STATE_KEY secret is missing, so refreshed tokens can't be "
                                 "saved and will expire 60 days after you pasted them.")
    accounts = {}
    tokens_changed = bool(tokens) and "tokens.enc" not in files
    for tid, entry in tokens.items():
        print(f"::add-mask::{entry['token']}")
        next_try = entry.get("next_refresh")
        if key and (not next_try or now >= datetime.fromisoformat(next_try)):
            tokens_changed = True
            try:
                entry["token"] = ig.refresh(entry["token"])
                entry["refreshed"] = now.isoformat()
                entry["next_refresh"] = (now + REFRESH_EVERY).isoformat()
                print(f"::add-mask::{entry['token']}")
            except ig.ApiError as e:
                # A token under 24 hours old can't be refreshed yet; try again tomorrow.
                entry["next_refresh"] = (now + timedelta(days=1)).isoformat()
                print(f"warning: token refresh failed: {e}", file=sys.stderr)
        try:
            info = ig.me(entry["token"])
        except ig.ApiError as e:
            state["problems"].append(f"An Instagram token stopped working ({e}). "
                                     "Generate a new one and replace it in IG_TOKENS.")
            continue
        accounts["@" + info["username"].lower()] = dict(entry, user_id=info["user_id"])
    if key and tokens_changed:
        out["tokens.enc"] = crypt(json.dumps(tokens), key) + "\n"
    state["tokens"] = {a: (v.get("refreshed") or "") for a, v in accounts.items()}

    items, bad = parse_queue(files.get("queue.txt", ""), tz)
    state["problems"] += bad
    for it in [] if args.no_post else due(items, state, cfg, now):
        if it["account"] not in accounts:
            state["problems"].append(f"{it['account']} is in the queue but not connected.")
            continue
        try:
            post(it, accounts[it["account"]], cfg, state, now)
        except (ig.ApiError, ValueError, subprocess.CalledProcessError) as e:
            err = str(e) if not isinstance(e, subprocess.CalledProcessError) else (
                (e.stderr or b"").decode(errors="replace").strip() or str(e))
            f = state["failed"].setdefault(it["key"], {"attempts": 0})
            f["attempts"] += 1
            f["error"] = err[:200]
            print("posting a clip failed (details on the dashboard)", file=sys.stderr)
            if f["attempts"] >= MAX_ATTEMPTS:
                notify(f"Gave up posting {it['key']} after {MAX_ATTEMPTS} tries: {err[:200]}", state)
            continue
        # Save straight away so a crash later in the run can't post the same clip twice.
        save_branch({"state.json": json.dumps(state, indent=1) + "\n",
                     **({"tokens.enc": out["tokens.enc"]} if out.get("tokens.enc") else {})},
                    f"Posted {it['key']}")

    synced = state.get("synced")
    did_sync = args.sync or not synced or now - datetime.fromisoformat(synced) >= SYNC_EVERY
    if did_sync:
        sync(accounts, cfg, state, now)

    submitted = {shortcode(line) for line in files.get("submitted.txt", "").splitlines()
                 if line.strip() and not line.startswith("#")}
    out["README.md"] = dashboard(state, cfg, items, submitted, state["problems"], now,
                                 DATA_REPO or os.environ.get("GITHUB_REPOSITORY", ""))
    out["state.json"] = json.dumps(state, indent=1) + "\n"
    # Between view syncs, only commit when something changed (a post, a failure, a new
    # problem), so the branch gets about a commit an hour rather than one per run.
    if did_sync or out["state.json"] != files.get("state.json") or len(out) > 2:
        save_branch({k: v for k, v in out.items() if v is not None}, f"Social update {now:%Y-%m-%d %H:%M}")
    if state["problems"]:
        print(f"::warning::{len(state['problems'])} problem(s): see Needs attention on the dashboard")


if __name__ == "__main__":
    main()
