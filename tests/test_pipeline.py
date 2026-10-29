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
