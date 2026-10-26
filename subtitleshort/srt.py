"""Minimal, forgiving SRT reader/writer (the file users edit to fix the transcript)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def format_time(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, cues: list[Cue]) -> None:
    blocks = [f"{i}\n{format_time(c.start)} --> {format_time(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def read_srt(path: Path) -> list[Cue]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    current = None
    for i, line in enumerate(lines):
        line = line.strip()
        if "-->" in line:
            left, _, right = line.partition("-->")
            a, b = _TIME.search(left), _TIME.search(right)
            if not (a and b):
                raise ValueError(f"{path.name}, línea {i + 1}: tiempo inválido '{line}'")
            current = Cue(_seconds(a), _seconds(b), "")
            cues.append(current)
        elif not line:
            continue
        elif line.isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1]:
            continue  # cue number
        elif current is not None:
            current.text = f"{current.text} {line}".strip()
    return [c for c in cues if c.text]


def _seconds(match: re.Match) -> float:
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")) / 1000
