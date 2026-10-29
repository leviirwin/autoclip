import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from autoclip import social

UTC = timezone.utc
NOW = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)


def fresh_state():
    return {"posted": {}, "media": {}, "failed": {}, "alerted": [], "problems": []}


def test_queue_lines_and_times():
    text = """# comment
@MicdUp.Daily | clips-solved-shame | 4
soundbite.club | clips-icedcoffee-greglav | 15 | now
@two.hours.in | clips-solved-shame | 06 | 2026-10-30 18:30 | Custom caption
@bad | only-two
"""
    items, problems = social.parse_queue(text, ZoneInfo("America/New_York"))
    assert [i["account"] for i in items] == ["@micdup.daily", "@soundbite.club", "@two.hours.in"]
    assert items[0]["when"] is None and items[0]["key"] == "clips-solved-shame#04@micdup.daily"
    assert items[1]["when"] < NOW
    assert items[2]["when"] == datetime(2026, 10, 30, 22, 30, tzinfo=UTC)  # 18:30 EDT
    assert items[2]["caption"] == "Custom caption"
    assert len(problems) == 1 and "line 5" in problems[0]


def test_due_spaces_posts_and_keeps_queue_order():
    cfg = dict(social.DEFAULT_CONFIG, min_gap_hours=3)
    items, _ = social.parse_queue("""a | r | 1
a | r | 2
b | r | 3
c | r | 4 | 2026-10-30 19:00
""", UTC)
    state = fresh_state()
    state["media"]["x"] = {"account": "@b", "posted": (NOW - timedelta(hours=1)).isoformat()}
    picked = social.due(items, state, cfg, NOW)
    # one per account; @b posted by hand an hour ago; @c is scheduled later
    assert [i["key"] for i in picked] == ["r#01@a"]
    state["posted"]["r#01@a"] = {"account": "@a", "at": NOW.isoformat()}
    assert social.due(items, state, cfg, NOW) == []
    later = NOW + timedelta(hours=3)
    assert [i["key"] for i in social.due(items, state, cfg, later)] == ["r#02@a", "r#03@b", "r#04@c"]


def test_due_respects_posting_window_and_failures():
    cfg = dict(social.DEFAULT_CONFIG, post_hours=[9, 17])
    items, _ = social.parse_queue("a | r | 1\nb | r | 2 | now", UTC)
    state = fresh_state()
    assert [i["key"] for i in social.due(items, state, cfg, NOW)] == ["r#02@b"]  # 18:00 is outside
    state["failed"]["r#02@b"] = {"attempts": 3, "error": "x"}
    assert social.due(items, state, cfg, NOW) == []


def test_captions_and_campaign_tags():
    notes = "# Clips\n\n## 01-a.mp4 (30s)\n\nFirst one.\n\n#a #b\n\n## 02-b.mp4 (40s)\n\nSecond @markmanson.\n"
    assert social.caption_from_notes(notes, 1) == "First one.\n\n#a #b"
    cfg = social.DEFAULT_CONFIG
    solved = social.campaign_for(cfg, release="clips-solved-shame")
    assert solved["name"].startswith("Solved")
    assert social.with_tags(social.caption_from_notes(notes, 2), solved).endswith("@solvedpodcast")
    assert social.campaign_for(cfg, caption="great clip @GregLav")["name"] == "Greg Lav"
    assert social.campaign_for(cfg, caption="nothing") is None
    with pytest.raises(ValueError):
        social.caption_from_notes(notes, 3)


def test_shortcode_matches_any_link_form():
    assert social.shortcode("https://www.instagram.com/reel/DAbc-1_x/?igsh=1") == "DAbc-1_x"
    assert social.shortcode("https://www.instagram.com/micdup.daily/reel/DAbc-1_x/") == "DAbc-1_x"


def test_tokens_round_trip_and_replacement():
    key = "k" * 20
    blob = social.crypt(json.dumps({social.token_id("old"): {"token": "refreshed", "refreshed": "t"}}), key)
    assert "refreshed" not in blob
    tokens = social.load_tokens("old\nnew\n", blob, key)
    assert [t["token"] for t in tokens.values()] == ["refreshed", "new"]
    assert list(social.load_tokens("old", blob, "wrong-key").values())[0]["token"] == "old"


def test_dashboard_groups_links_by_campaign():
    cfg = social.DEFAULT_CONFIG
    state = fresh_state()
    state["media"] = {
        "1": {"account": "@a", "posted": NOW.isoformat(), "campaign": "Greg Lav", "label": "x",
              "permalink": "https://www.instagram.com/reel/AAA/", "views": 2500},
        "2": {"account": "@b", "posted": NOW.isoformat(), "campaign": None, "label": "y",
              "permalink": "https://www.instagram.com/reel/BBB/", "views": 10},
    }
    md = social.dashboard(state, cfg, [], {"AAA"}, ["something broke"], NOW, "me/repo")
    assert "## Needs attention" in md and "something broke" in md
    assert f"### [Greg Lav]({cfg['campaigns'][0]['page']})" in md
    assert "- ✅ https://www.instagram.com/reel/AAA/" in md
    assert "- ⬜ https://www.instagram.com/reel/BBB/" in md
    assert "2,500 🔥" in md and "$1.88" in md


def test_full_run_posts_syncs_and_alerts(monkeypatch, tmp_path):
    files = {"queue.txt": "@acct | clips-solved-shame | 2 | now\n"}
    saved, alerts, published = [], [], []
    monkeypatch.setattr(social, "load_branch", lambda: dict(files))
    monkeypatch.setattr(social, "save_branch", lambda f, msg: saved.append((msg, f)))
    monkeypatch.setattr(social, "notify", lambda text, state, link=None: alerts.append(text))

    video = tmp_path / "02-x.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(social, "release_files",
                        lambda r, c, d: (video, "## 02-x.mp4 (40s)\n\nA caption.\n"))
    ig = social.ig
    monkeypatch.setattr(ig, "refresh", lambda t: t + "-r")
    monkeypatch.setattr(ig, "me", lambda t: {"user_id": "17", "username": "Acct"})

    def publish(token, uid, path, caption):
        published.append(caption)
        return {"id": "m1", "permalink": "https://www.instagram.com/reel/NEW/",
                "timestamp": "2026-10-30T18:00:00+0000"}
    monkeypatch.setattr(ig, "publish_reel", publish)
    monkeypatch.setattr(ig, "reels", lambda t: [
        {"id": "m1", "permalink": "https://www.instagram.com/reel/NEW/", "caption": "A caption.",
         "timestamp": "2026-10-30T18:00:00+0000", "views": 1500, "likes": 9}])
    monkeypatch.setenv("IG_TOKENS", "IGtoken123")
    monkeypatch.setenv("STATE_KEY", "secret-key")
    monkeypatch.setattr("sys.argv", ["social"])

    social.main()
    assert published == ["A caption.\n\n@solvedpodcast @markmanson"]
    assert saved[0][0] == "Posted clips-solved-shame#02@acct"
    final = saved[-1][1]
    state = json.loads(final["state.json"])
    assert state["media"]["m1"]["views"] == 1500 and state["media"]["m1"]["label"] == "solved-shame 02"
    assert "IGtoken" not in final["tokens.enc"] and set(final) >= {"README.md", "config.json"}
    assert len(alerts) == 2 and "Submit by" in alerts[0] and "passed 1,000" in alerts[1]

    # Next run straight after: nothing due, no sync yet, nothing to commit.
    files.update({k: v for k, v in final.items()})
    saved.clear()
    social.main()
    assert saved == []
