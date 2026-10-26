from subtitleshort.timing import drop_hallucinations, refine_words


def seg(*words):
    return {"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}


def times(segment):
    return [(w["word"], round(w["start"], 2), round(w["end"], 2)) for w in segment["words"]]


def test_first_word_stretched_over_a_pause_starts_with_the_voice():
    s = seg(("Va", 52.01, 52.97), ("a", 52.97, 53.05), ("ser", 53.05, 53.13))
    refine_words([s], [(51.27, 52.13), (52.83, 53.79)])
    assert times(s)[0] == ("Va", 52.83, 52.97)


def test_word_parked_in_silence_moves_next_to_its_sentence():
    s = seg(("Te", 45.08, 45.66), ("lo", 46.33, 46.65), ("juro", 46.65, 46.85))
    refine_words([s], [(43.75, 45.12), (46.53, 48.57)])
    te, lo, juro = times(s)
    assert te[1] == 46.53 and te[2] <= lo[1] and lo[2] == 46.65 and juro == ("juro", 46.65, 46.85)


def test_trailing_silence_is_trimmed_from_the_last_word():
    s = seg(("en", 25.52, 25.70), ("la", 25.70, 26.06))
    refine_words([s], [(25.19, 25.89)])
    assert times(s)[-1] == ("la", 25.70, 25.89)


def test_segment_the_vad_cannot_hear_is_left_alone():
    s = seg(("la", 10.0, 10.5), ("la", 10.5, 11.0), ("la", 11.0, 11.5))
    before = times(s)
    refine_words([s], [(0.0, 1.0)])
    assert times(s) == before


def test_unsure_words_in_silence_are_dropped():
    real = seg(("Así", 2.78, 3.0), ("que", 3.0, 3.2))
    real["words"][0]["probability"] = 0.09  # unsure, but there is voice under it
    ghost = seg(("PostInCloud", 83.83, 84.09))
    ghost["words"][0]["probability"] = 0.0
    assert drop_hallucinations([real, ghost], [(2.7, 3.3), (82.4, 83.74)]) == [real]


def test_segment_bounds_follow_the_words():
    s = seg(("Vale,", 5.73, 6.21), ("vamos", 6.27, 6.55))
    refine_words([s], [(6.08, 8.19)])
    assert s["start"] == s["words"][0]["start"] >= 6.08
