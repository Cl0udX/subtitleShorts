"""From word timestamps (and the user's edited SRT) to short, well-timed captions."""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from typing import Callable, Optional

from .srt import Cue

SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)\]]*$")
CLAUSE_END = re.compile(r"[,;:]+[\"'»”’)\]]*$")
BREAK_MARK = "|"  # typed in the SRT to force a caption break
MIN_WORD = 0.08  # seconds given to an inserted word when there is no room for it

# Words a caption reads badly ending on: "EL CAMINO POR / ABAJO" vs "EL CAMINO / POR ABAJO".
WEAK_ENDINGS = set(
    "el la los las un una unos unas lo al del de a en con por para sin sobre entre hasta desde "
    "y e o u ni que se me te le les nos mi tu su mis tus sus muy más "
    "the a an of to in on at and or but for with from my your his her its our their".split()
)


@dataclass
class Word:
    text: str
    start: float
    end: float
    break_after: bool = False


@dataclass
class Caption:
    words: list[Word]
    start: float
    end: float


def transcript_words(transcript: dict) -> list[Word]:
    words = [Word(w["word"], float(w["start"]), float(w["end"]))
             for seg in transcript["segments"] for w in seg["words"]]
    for prev, cur in zip(words, words[1:]):
        cur.start = max(cur.start, prev.start)
        cur.end = max(cur.end, cur.start)
    return words


def transcript_to_cues(transcript: dict) -> list[Cue]:
    """One SRT cue per Whisper segment (a sentence or so): comfortable to edit."""
    return [Cue(seg["words"][0]["start"], seg["words"][-1]["end"], " ".join(w["word"] for w in seg["words"]))
            for seg in transcript["segments"] if seg["words"]]


def apply_replacements(text: str, replacements: Optional[dict]) -> str:
    for source, target in (replacements or {}).items():
        target = "" if target is None else str(target)
        text = re.sub(rf"(?<!\w){re.escape(str(source))}(?!\w)", lambda _: target, text, flags=re.IGNORECASE)
    return text


def align_cues(cues: list[Cue], words: list[Word], replacements: Optional[dict] = None) -> list[list[Word]]:
    """Timed words for every cue. Words the user didn't touch keep Whisper's
    timestamps; corrected or added words get times interpolated around them."""
    buckets: list[list[Word]] = [[] for _ in cues]
    if cues:
        for w in words:
            mid = (w.start + w.end) / 2
            buckets[min(range(len(cues)), key=lambda i: _distance(cues[i], mid))].append(w)
    return [_align_cue(cue, candidates, apply_replacements(cue.text, replacements))
            for cue, candidates in zip(cues, buckets)]


def build_captions(cue_words: list[list[Word]], *, display: Callable[[Word], str],
                   fits: Callable[[list[str]], bool], max_words: int, pause: float = 0.45) -> list[Caption]:
    captions = []
    for words in cue_words:
        visible: list[Word] = []
        for w in words:
            if display(w):
                visible.append(w)
            elif visible:  # a lone punctuation mark still ends the phrase
                visible[-1].break_after |= bool(SENTENCE_END.search(w.text) or CLAUSE_END.search(w.text))
        for phrase in _phrases(visible, pause):
            for chunk in _split_phrase(phrase, [display(w) for w in phrase], fits, max_words):
                captions.append(Caption(chunk, chunk[0].start, chunk[-1].end))
    return captions


def retime(captions: list[Caption], *, duration: float = math.inf, linger: float = 0.25,
           gap_fill: float = 0.6, min_duration: float = 0.4) -> None:
    """Captions appear on their first word, stay through short pauses (no
    flicker) and never overlap the next one."""
    for cap in captions:
        cap.start = cap.words[0].start
    for i, cap in enumerate(captions):
        nxt = captions[i + 1].start if i + 1 < len(captions) else duration
        end = cap.words[-1].end
        end = nxt if nxt - end <= gap_fill else min(end + linger, nxt)
        if end - cap.start < min_duration:
            end = min(cap.start + min_duration, nxt)
        cap.end = max(min(end, duration), cap.start)


def _distance(cue: Cue, t: float) -> float:
    return 0.0 if cue.start <= t <= cue.end else min(abs(t - cue.start), abs(t - cue.end))


def _norm(token: str) -> str:
    return re.sub(r"[^\w]+", "", token.casefold())


def _align_cue(cue: Cue, candidates: list[Word], text: str) -> list[Word]:
    tokens: list[str] = []
    breaks: set[int] = set()
    for raw in text.split():
        if raw == BREAK_MARK:
            if tokens:
                breaks.add(len(tokens) - 1)
        elif not _norm(raw) and tokens:
            tokens[-1] += raw  # "hola ," -> "hola,"
        else:
            tokens.append(raw)
    if not tokens:
        return []

    times: list[Optional[tuple[float, float]]] = [None] * len(tokens)
    matcher = difflib.SequenceMatcher(None, [_norm(w.text) for w in candidates], [_norm(t) for t in tokens],
                                      autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[j1 + k] = (candidates[i1 + k].start, candidates[i1 + k].end)
        elif tag == "replace":
            _spread(times, tokens, j1, j2, candidates[i1].start, candidates[i2 - 1].end)
    _fill_missing(times, tokens, cue.start, cue.end)

    result = []
    for k, (token, span) in enumerate(zip(tokens, times)):
        start = min(max(span[0], cue.start), cue.end)
        end = min(max(span[1], start), cue.end)
        if result and start < result[-1].start:
            start = result[-1].start
        result.append(Word(token, start, max(end, start), k in breaks))
    return result


def _spread(times: list, tokens: list[str], j1: int, j2: int, t0: float, t1: float) -> None:
    """Share [t0, t1] among tokens[j1:j2] in proportion to their length."""
    weights = [len(_norm(t)) + 1 for t in tokens[j1:j2]]
    total, acc = sum(weights), 0
    for k, weight in zip(range(j1, j2), weights):
        start = t0 + (t1 - t0) * acc / total
        acc += weight
        times[k] = (start, t1 if k == j2 - 1 else t0 + (t1 - t0) * acc / total)


def _fill_missing(times: list, tokens: list[str], lo: float, hi: float) -> None:
    n, k = len(times), 0
    while k < n:
        if times[k] is not None:
            k += 1
            continue
        j = k
        while j < n and times[j] is None:
            j += 1
        t0 = times[k - 1][1] if k > 0 else lo
        t1 = times[j][0] if j < n else hi
        if t1 - t0 < MIN_WORD * (j - k):
            # No gap to put the new words in: share the neighbour word's time.
            if j < n:
                t1 = times[j][1]
                j += 1
            elif k > 0:
                k -= 1
                t0 = times[k][0]
            t1 = max(t1, t0 + MIN_WORD * (j - k))
        _spread(times, tokens, k, j, t0, t1)
        k = j


def _phrases(words: list[Word], pause: float):
    phrase: list[Word] = []
    for i, w in enumerate(words):
        phrase.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (nxt is None or w.break_after or SENTENCE_END.search(w.text) or CLAUSE_END.search(w.text)
                or nxt.start - w.end >= pause):
            yield phrase
            phrase = []


def _split_phrase(phrase: list[Word], texts: list[str], fits: Callable[[list[str]], bool],
                  max_words: int) -> list[list[Word]]:
    """Split a phrase into the fewest captions that fit, as even as possible and
    avoiding captions that end on articles/prepositions."""
    n = len(phrase)
    memo: dict[tuple[int, int], bool] = {}

    def ok(p: int, i: int) -> bool:
        if (p, i) not in memo:  # a single word always "fits": it is scaled down when drawn
            memo[(p, i)] = i - p == 1 or (i - p <= max_words and fits(texts[p:i]))
        return memo[(p, i)]

    for k in range(math.ceil(n / max_words), n + 1):
        target = n / k
        cost = [[math.inf] * (n + 1) for _ in range(k + 1)]
        back = [[0] * (n + 1) for _ in range(k + 1)]
        cost[0][0] = 0.0
        for j in range(1, k + 1):
            for i in range(j, n + 1):
                for p in range(max(j - 1, i - max_words), i):
                    if cost[j - 1][p] == math.inf or not ok(p, i):
                        continue
                    c = cost[j - 1][p] + (i - p - target) ** 2
                    if i < n and _norm(texts[i - 1]) in WEAK_ENDINGS:
                        c += 1.5
                    if c < cost[j][i]:
                        cost[j][i], back[j][i] = c, p
        if cost[k][n] < math.inf:
            chunks, i = [], n
            for j in range(k, 0, -1):
                chunks.append(phrase[back[j][i]:i])
                i = back[j][i]
            return chunks[::-1]
    return [[w] for w in phrase]
