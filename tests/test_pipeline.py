from autoclip.captions import build_ass, chunk
from autoclip.render import snap
from autoclip.track import Track, crop_path


def words_from(text, start=0.0, step=0.4):
    return [{"start": start + i * step, "end": start + i * step + 0.3, "word": w}
            for i, w in enumerate(text.split())]


def test_snap_keeps_whole_words():
    words = words_from("one two three four five six")
    s, e, inside = snap(words, 0.5, 1.5)  # cuts into "two" and "four"
    assert [w["word"] for w in inside] == ["two", "three", "four"]
    assert s < inside[0]["start"] and e > inside[-1]["end"]


def test_chunks_break_on_punctuation_and_length():
    groups = chunk(words_from("Hello, this is a fairly long sentence indeed."))
    assert [len(g) for g in groups][0] == 1
    assert all(len(g) <= 3 for g in groups)


def test_ass_has_one_event_per_word_plus_hook():
    words = words_from("so here is the thing", start=10)
    ass = build_ass(words, 9.9, 12.5, hook="watch this")
    assert ass.count("Dialogue:") == len(words) + 1


def test_crop_ignores_blips_and_cuts_to_sustained_speaker():
    w, crop = 1920, 608
    samples = [(i / 5, 500.0) for i in range(10)]                 # speaker A, 2s
    samples += [(2.0, 1400.0)]                                    # one-frame blip
    samples += [(2.2 + i / 5, 500.0) for i in range(5)]
    samples += [(3.2 + i / 5, 1400.0) for i in range(10)]         # speaker B, 2s
    track = Track(crop_path(samples, w, crop))
    assert abs(track(2.1) - 500) < 20      # blip ignored
    assert abs(track(3.25) - 1400) < 1     # cut lands when B appeared
    assert abs(track(3.1) - 500) < 20      # and not before


def test_crop_centres_when_no_faces():
    track = Track(crop_path([(0, None), (0.2, None)], 1920, 608))
    assert track(0.1) == 960


def test_drive_id_from_share_links():
    from autoclip.download import drive_id
    fid = "1L1pvBHYsg_-nmpwEub1GZvNiz7Q-59wR"
    assert drive_id(f"https://drive.google.com/file/d/{fid}/view?usp=drivesdk") == fid
    assert drive_id(f"https://drive.google.com/open?id={fid}") == fid
    assert drive_id("https://www.youtube.com/watch?v=abc") is None


def test_fast_pace_trims_pauses_and_maps_time_both_ways():
    from autoclip.style import Timeline, keep_segments
    words = [{"start": 0.0, "end": 1.0, "word": "a"}, {"start": 3.0, "end": 4.0, "word": "b"}]
    segs = keep_segments(words, 0.0, 4.2, max_pause=0.35)
    assert len(segs) == 2 and segs[0][1] < 2.0 < segs[1][0]
    tl = Timeline(segs, speed=1.0)
    assert tl.duration < 2.6
    assert abs(tl.src(tl.out(3.5)) - 3.5) < 1e-9
    assert abs(tl.out(2.0) - tl.out(segs[1][0])) < 1e-9  # a time inside a cut lands on the join
    assert abs(Timeline([(0, 10)], speed=2.0).duration - 5) < 1e-9


def test_style_presets_merge_and_reject_typos():
    import pytest
    from autoclip.style import resolve
    s = resolve("fast", {"speed": 1.0})
    assert s["pace"] == "fast" and s["speed"] == 1.0 and s["zoom"]
    assert resolve()["pace"] == "normal"
    with pytest.raises(ValueError):
        resolve({"sped": 2})


def test_emphasis_words_are_coloured():
    words = words_from("this changed everything for me", start=0)
    ass = build_ass(words, 0, 3, emphasis=["Everything"])
    assert "&H0084DC3D&" in ass
