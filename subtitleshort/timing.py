"""Word timing refinement with voice activity detection (VAD).

Whisper's word timestamps are good inside a sentence, but the first word of a
segment is often stretched back over the pause before it, and stray words get
parked in silences, so captions would pop up before the person talks. We find
where there is actual speech and move the word boundaries onto it.
"""
from __future__ import annotations

from .media import SAMPLE_RATE

MIN_WORD = 0.06  # seconds
MIN_PIECE = 0.12  # speech shorter than this inside a word may just be noise
MISPLACED_SECONDS = 0.15  # a word with less speech than this under it (and
MISPLACED_RATIO = 0.3  # less than this share of its duration) is misplaced

Region = tuple[float, float]


def speech_regions(audio) -> list[Region]:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=0.5, min_silence_duration_ms=120, speech_pad_ms=30, min_speech_duration_ms=60)
    return [(t["start"] / SAMPLE_RATE, t["end"] / SAMPLE_RATE) for t in get_speech_timestamps(audio, options)]


def drop_hallucinations(segments: list[dict], regions: list[Region], max_probability: float = 0.4) -> list[dict]:
    """Whisper sometimes "hears" words in silence (more so with hotwords):
    drop segments with no speech under them that it wasn't confident about."""
    kept = []
    for seg in segments:
        words = seg["words"]
        speech = sum(b - a for w in words for a, b in _pieces(w["start"], w["end"], regions))
        confidence = sum(w.get("probability", 1.0) for w in words) / max(1, len(words))
        if regions and speech < 0.05 and confidence < max_probability:
            continue
        kept.append(seg)
    return kept


def refine_words(segments: list[dict], regions: list[Region]) -> None:
    """Adjust the words of Whisper segments (dicts with 'word', 'start', 'end') in place."""
    if not regions:
        return
    for seg in segments:
        words = seg["words"]
        pieces = [_pieces(w["start"], w["end"], regions) for w in words]
        misplaced = [i for i, (w, p) in enumerate(zip(words, pieces))
                     if sum(b - a for a, b in p) < min(MISPLACED_SECONDS, MISPLACED_RATIO * (w["end"] - w["start"]))]
        if not words or len(misplaced) > len(words) / 2:
            continue  # the VAD doesn't hear this part (singing, shouting...): keep Whisper's timing
        for i, w in enumerate(words):
            if i not in misplaced and pieces[i]:
                _trim(w, pieces[i], first=i == 0, last=i == len(words) - 1)

        pending = set(misplaced)
        for i in reversed(misplaced):  # first words go next to the word that follows them
            if i + 1 < len(words) and i + 1 not in pending:
                _attach_before(words[i], words[i + 1], regions)
                pending.discard(i)
        for i in misplaced:  # last words go right after the previous one
            if i in pending and i > 0 and i - 1 not in pending:
                _attach_after(words[i], words[i - 1], regions)
                pending.discard(i)

    flat = [w for seg in segments for w in seg["words"]]
    for prev, cur in zip(flat, flat[1:]):
        cur["start"] = max(cur["start"], prev["start"])
    for w in flat:
        w["start"], w["end"] = round(w["start"], 3), round(max(w["end"], w["start"]), 3)
    for seg in segments:
        if seg["words"]:
            seg["start"], seg["end"] = seg["words"][0]["start"], seg["words"][-1]["end"]


def _trim(w: dict, pieces: list[Region], first: bool, last: bool) -> None:
    """Drop silence at the edges of a word. A segment's first word that spans a
    pause belongs after it (Whisper stretched it back), the last word before it."""
    longest = max(b - a for a, b in pieces)
    solid = [p for p in pieces if p[1] - p[0] >= min(MIN_PIECE, longest)]
    if first and last:
        w["start"], w["end"] = max(pieces, key=lambda p: p[1] - p[0])
    elif first:
        w["start"], w["end"] = solid[-1][0], pieces[-1][1]
    elif last:
        w["start"], w["end"] = pieces[0][0], solid[0][1]
    else:
        w["start"], w["end"] = pieces[0][0], pieces[-1][1]


def _attach_before(w: dict, nxt: dict, regions: list[Region]) -> None:
    anchor = nxt["start"]
    start = anchor - _plausible(w)
    region = _region_at(anchor, regions)
    if region and start < region[0]:
        start = min(region[0], anchor)
    if anchor - start >= MIN_WORD:
        w["start"], w["end"] = start, anchor
    else:  # no room before the next word: share its time
        w["start"] = start
        w["end"] = nxt["start"] = _split(start, max(nxt["end"], start + 2 * MIN_WORD), w, nxt)


def _attach_after(w: dict, prv: dict, regions: list[Region]) -> None:
    anchor = prv["end"]
    end = anchor + _plausible(w)
    region = _region_at(anchor, regions)
    if region and end > region[1]:
        end = max(region[1], anchor)
    if end - anchor >= MIN_WORD:
        w["start"], w["end"] = anchor, end
    else:  # no room after the previous word: share its time
        w["end"] = max(end, prv["start"] + 2 * MIN_WORD)
        w["start"] = prv["end"] = _split(prv["start"], w["end"], prv, w)


def _split(t0: float, t1: float, a: dict, b: dict) -> float:
    la, lb = _letters(a["word"]), _letters(b["word"])
    return t0 + (t1 - t0) * la / (la + lb)


def _plausible(w: dict) -> float:
    """How long a word probably takes to say (Whisper's own figure if shorter)."""
    return max(0.1, min(w["end"] - w["start"], 0.08 + 0.065 * _letters(w["word"])))


def _letters(text: str) -> int:
    return max(1, sum(c.isalnum() for c in text))


def _pieces(start: float, end: float, regions: list[Region]) -> list[Region]:
    return [(max(start, a), min(end, b)) for a, b in regions if a < end and b > start]


def _region_at(t: float, regions: list[Region], tolerance: float = 0.01):
    for a, b in regions:
        if a - tolerance <= t <= b + tolerance:
            return a, b
    return None
