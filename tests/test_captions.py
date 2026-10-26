import math

from subtitleshort.captions import (
    Caption,
    Word,
    align_cues,
    apply_replacements,
    build_captions,
    retime,
    transcript_to_cues,
    transcript_words,
)
from subtitleshort.srt import Cue


def make_transcript(*segments):
    """segments: lists of (word, start, end)."""
    return {"segments": [{"words": [{"word": w, "start": s, "end": e} for w, s, e in seg]} for seg in segments]}


TRANSCRIPT = make_transcript(
    [("Es", 0.5, 0.6), ("que", 0.6, 0.7), ("claro,", 0.7, 1.0), ("el", 1.1, 1.2), ("camino", 1.2, 1.6),
     ("por", 1.6, 1.8), ("abajo", 1.8, 2.2)],
    [("vamos", 5.0, 5.3), ("a", 5.3, 5.4), ("continuar", 5.4, 6.0)],
)


def texts(captions):
    return [" ".join(w.text for w in c.words) for c in captions]


def build(cue_words, max_words=3, max_chars=40):
    return build_captions(cue_words, display=lambda w: w.text.strip(".,"),
                          fits=lambda t: len(" ".join(t)) <= max_chars, max_words=max_words)


def test_unedited_srt_keeps_whisper_timing():
    cues = transcript_to_cues(TRANSCRIPT)
    assert [c.text for c in cues] == ["Es que claro, el camino por abajo", "vamos a continuar"]
    words = align_cues(cues, transcript_words(TRANSCRIPT))
    assert [(w.text, w.start, w.end) for w in words[1]] == [("vamos", 5.0, 5.3), ("a", 5.3, 5.4), ("continuar", 5.4, 6.0)]


def test_corrected_word_takes_the_time_of_the_word_it_replaces():
    cues = [Cue(5.0, 6.0, "vamos a continuarrr")]
    words = align_cues(cues, transcript_words(TRANSCRIPT))[0]
    assert [w.text for w in words] == ["vamos", "a", "continuarrr"]
    assert (words[2].start, words[2].end) == (5.4, 6.0)


def test_inserted_and_deleted_words():
    cues = [Cue(5.0, 6.0, "ahora vamos continuar")]
    words = align_cues(cues, transcript_words(TRANSCRIPT))[0]
    assert [w.text for w in words] == ["ahora", "vamos", "continuar"]
    assert words[0].start == 5.0 and words[0].end <= words[1].end
    assert all(a.start <= b.start for a, b in zip(words, words[1:]))
    assert words[2].start == 5.4  # "a" was deleted, "continuar" keeps its time


def test_replacements_merge_words_and_keep_timing():
    transcript = make_transcript([("visiten", 0.0, 0.4), ("post", 0.5, 0.7), ("in", 0.7, 0.8), ("cloud", 0.8, 1.2)])
    cues = transcript_to_cues(transcript)
    words = align_cues(cues, transcript_words(transcript), {"post in cloud": "PostInCloud"})[0]
    assert [w.text for w in words] == ["visiten", "PostInCloud"]
    assert (words[1].start, words[1].end) == (0.5, 1.2)


def test_empty_replacement_deletes_word():
    assert apply_replacements("ehm vamos, ehm", {"ehm": ""}).split() == ["vamos,"]


def test_phrases_split_on_punctuation_and_avoid_weak_endings():
    words = align_cues(transcript_to_cues(TRANSCRIPT), transcript_words(TRANSCRIPT))
    caps = build(words)
    # "claro," ends a phrase; "el camino por abajo" is not cut after the preposition
    assert texts(caps) == ["Es que claro,", "el camino", "por abajo", "vamos a continuar"]


def test_break_mark_forces_a_new_caption():
    cues = [Cue(5.0, 6.0, "vamos | a continuar")]
    caps = build(align_cues(cues, transcript_words(TRANSCRIPT)))
    assert texts(caps) == ["vamos", "a continuar"]


def test_long_pause_splits_and_width_limit_is_respected():
    words = [Word("uno", 0.0, 0.3), Word("dos", 0.3, 0.6), Word("tres", 2.0, 2.3)]
    assert texts(build([words])) == ["uno dos", "tres"]
    words = [Word(w, i * 0.2, i * 0.2 + 0.2) for i, w in enumerate("aaaa bbbb cccc dddd".split())]
    assert all(len(t) <= 9 for t in texts(build([words], max_words=4, max_chars=9)))


def test_auto_preview_times_land_on_subtitles():
    from subtitleshort.cli import _preview_times

    caps = [Caption([Word(str(i), i * 2.0, i * 2.0 + 1.0)], i * 2.0, i * 2.0 + 1.0) for i in range(10)]
    times = _preview_times(caps)
    assert len(times) == 3 and times == sorted(times)
    assert all(any(c.start < t < c.end for c in caps) for t in times)
    assert _preview_times(caps[:1]) == [0.3] and _preview_times([]) == [1.0]


def test_retime_fills_short_gaps_and_never_overlaps():
    caps = [Caption([Word("a", 0.0, 0.5)], 0, 0), Caption([Word("b", 0.8, 1.0)], 0, 0),
            Caption([Word("c", 3.0, 3.1)], 0, 0)]
    retime(caps, duration=3.3)
    assert caps[0].end == 0.8  # 0.3 s gap: stays until the next one
    assert math.isclose(caps[1].end, 1.25)  # long pause: lingers 0.25 s
    assert caps[2].end == 3.3  # min duration, clipped to the video length
    assert all(a.end <= b.start for a, b in zip(caps, caps[1:]))
