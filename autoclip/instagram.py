"""Instagram API with Instagram Login: list an account's Reels with their views, publish a
Reel, refresh the 60-day token. Standard library only, so the scheduled job needs no
installs. Tokens never go into an exception message or a log line (the repo is public)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VERSION = "v23.0"
API = f"https://graph.instagram.com/{VERSION}"
UPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSION}"
METRICS = ("views", "reach", "likes", "comments", "shares", "saved")


class ApiError(Exception):
    pass


def call(path, token, params=None, method="GET", base=API):
    params = dict(params or {}, access_token=token)
    url = path if path.startswith("https://") else f"{base}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(err)
        except ValueError:
            msg = f"HTTP {e.code}"
        raise ApiError(f"{path.split('?')[0].rsplit('/', 1)[-1]}: {msg}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"network error: {e.reason}") from None


def me(token):
    """{"user_id": ..., "username": ...} of the professional account the token belongs to."""
    return call("me", token, {"fields": "user_id,username"})


def refresh(token):
    """A fresh 60-day token. The old one must be at least 24 hours old and still valid."""
    r = call("https://graph.instagram.com/refresh_access_token", token,
             {"grant_type": "ig_refresh_token"})
    return r["access_token"]


def metrics(media_id, token):
    """Insights for one Reel: views, reach, likes, comments, shares, saved."""
    try:
        data = call(f"{media_id}/insights", token, {"metric": ",".join(METRICS)})["data"]
    except ApiError:
        # A metric the API no longer offers fails the whole call; views alone still works.
        data = call(f"{media_id}/insights", token, {"metric": "views"})["data"]
    out = {}
    for m in data:
        if "total_value" in m:
            out[m["name"]] = m["total_value"].get("value", 0)
        elif m.get("values"):
            out[m["name"]] = m["values"][-1].get("value", 0)
    return out


def reels(token, days=60):
    """The account's Reels from the last `days` days, newest first, with their metrics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    fields = "id,caption,media_product_type,permalink,timestamp,like_count,comments_count"
    page = call("me/media", token, {"fields": fields, "limit": 50})
    out = []
    while True:
        for m in page.get("data", []):
            when = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            if when < cutoff:
                return out
            if m.get("media_product_type") == "REELS":
                m.update(metrics(m["id"], token))
                out.append(m)
        nxt = page.get("paging", {}).get("next")
        if not nxt:
            return out
        # The next link already carries the token; call() adds it again, which is harmless.
        page = call(nxt, token)


def publish_reel(token, user_id, video, caption, wait=600):
    """Upload a local video file as a Reel and publish it. Returns {"id", "permalink"}."""
    container = call(f"{user_id}/media", token, {
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true"}, method="POST")["id"]
    body = open(video, "rb").read()
    req = urllib.request.Request(f"{UPLOAD}/{container}", data=body, method="POST", headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(body))})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"upload: HTTP {e.code} {e.read()[:300].decode(errors='replace')}") from None
    deadline = time.time() + wait
    while True:
        status = call(container, token, {"fields": "status_code,status"})
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") in ("ERROR", "EXPIRED"):
            raise ApiError(f"processing failed: {status.get('status')}")
        if time.time() > deadline:
            raise ApiError("processing timed out")
        time.sleep(10)
    media = call(f"{user_id}/media_publish", token, {"creation_id": container}, method="POST")
    return call(media["id"], token, {"fields": "id,permalink,timestamp"})
