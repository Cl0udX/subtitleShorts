"""ASS subtitle generation (drawn by libass inside ffmpeg).

Every caption is drawn at an explicit position (\\pos) with centre alignment.
Backgrounds are vector drawings sized from HarfBuzz measurements of the text,
which is what lets them have rounded corners and real padding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .captions import Caption, Word
from .config import Style, parse_color
from .fonts import Font, TextMeasurer
from .media import VideoInfo

REFERENCE_WIDTH = 1080  # style sizes are given for a 1080x1920 short and scaled from there
POP = ((0, 0.78), (90, 1.06), (150, 1.0))  # (ms, scale) keyframes of the "pop" entrance
FADE_MS = 120
KAPPA = 0.5523  # control point distance of a quarter circle drawn with a cubic bezier
EDGE_PUNCTUATION = ".,;:…"


def ass_color(color: str, opacity: Optional[float] = None) -> str:
    """Style-line colour: &HAABBGGRR, where AA is transparency (00 = opaque)."""
    r, g, b, a = parse_color(color)
    if opacity is not None:
        a = round(a * max(0.0, min(1.0, opacity)))
    return f"&H{255 - a:02X}{b:02X}{g:02X}{r:02X}"


def inline_color(color: str) -> str:
    r, g, b, _ = parse_color(color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def inline_alpha(color: str, opacity: float = 1.0) -> str:
    a = round(parse_color(color)[3] * max(0.0, min(1.0, opacity)))
    return f"&H{255 - a:02X}&"


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rounded_rect(w: float, h: float, r: float) -> str:
    """ASS drawing of a w x h rectangle with corner radius r, from (0, 0)."""
    w, h = round(w), round(h)
    r = round(max(0.0, min(r, w / 2, h / 2)))
    if r == 0:
        return f"m 0 0 l {w} 0 l {w} {h} l 0 {h}"
    c = round(r * (1 - KAPPA))
    return (f"m {r} 0 l {w - r} 0 b {w - c} 0 {w} {c} {w} {r} "
            f"l {w} {h - r} b {w} {h - c} {w - c} {h} {w - r} {h} "
            f"l {r} {h} b {c} {h} 0 {h - c} 0 {h - r} "
            f"l 0 {r} b 0 {c} {c} 0 {r} 0")


def pop_tags(offset_ms: float) -> str:
    """Scale tags for the pop entrance, for an event starting `offset_ms` after
    the caption appeared (so word-by-word events continue the same animation)."""
    if offset_ms >= POP[-1][0]:
        return ""
    value = POP[0][1]
    for (t0, v0), (t1, v1) in zip(POP, POP[1:]):
        if offset_ms < t1:
            value = v0 + (v1 - v0) * (max(offset_ms, t0) - t0) / (t1 - t0)
            break
    tags = f"\\fscx{value * 100:.0f}\\fscy{value * 100:.0f}"
    for (t0, _), (t1, v1) in zip(POP, POP[1:]):
        if t1 > offset_ms:
            tags += f"\\t({max(t0, offset_ms) - offset_ms:.0f},{t1 - offset_ms:.0f},\\fscx{v1 * 100:.0f}\\fscy{v1 * 100:.0f})"
    return tags


def ycbcr_matrix(video: VideoInfo) -> str:
    """Tells ffmpeg which colour matrix to blend with, so colours are exact."""
    space = video.color_space or ""
    if space in ("bt470bg", "smpte170m") or (space in ("", "unknown") and min(video.width, video.height) < 720):
        matrix = "601"
    elif space in ("bt709", "", "unknown"):
        matrix = "709"
    else:
        return "None"  # e.g. BT.2020: blend in the video's own colour space
    return f"{'PC' if video.color_range == 'pc' else 'TV'}.{matrix}"


@dataclass
class Layout:
    texts: list[str]
    px: float  # em size after shrinking captions that are too wide
    width: float  # text width in px
    cap: float  # cap height in px
    cx: float  # centre of the text ink (and of the background box)
    cy: float
    text_y: float  # \pos y of the text: libass centres the line box, not the ink


class AssBuilder:
    def __init__(self, style: Style, video: VideoInfo, font: Font, measurer: TextMeasurer):
        self.style, self.video, self.font, self.measurer = style, video, font, measurer
        self.scale = min(video.width, video.height) / REFERENCE_WIDTH
        self.px = style.size * self.scale
        self.spacing = style.letter_spacing * self.scale
        bg = style.background
        self.box_pad = (bg.padding[0] * self.scale, bg.padding[1] * self.scale) if bg.enabled else (0.0, 0.0)
        self.pill_pad = (style.highlight.padding[0] * self.scale, style.highlight.padding[1] * self.scale)
        self.max_text_width = video.width * style.max_width / 100 - 2 * self.box_pad[0]

    # -- text ---------------------------------------------------------------

    def display(self, word: Word) -> str:
        text = word.text.strip()
        if self.style.remove_punctuation:
            text = text.strip(EDGE_PUNCTUATION)
        if self.style.uppercase:
            text = text.upper()
        return text

    def fits(self, texts: list[str]) -> bool:
        line = " ".join(texts)
        return len(line) <= self.style.max_chars and self._width(line, self.px) <= self.max_text_width

    def _width(self, text: str, px: float) -> float:
        return self.measurer.width(text, px, self.spacing * px / self.px)

    # -- layout -------------------------------------------------------------

    def position_at(self, t: float) -> tuple[float, float, str]:
        p = self.style.position
        x, y, anchor = p.x, p.y, p.anchor
        for change in self.style.position_changes:
            if change.start <= t < change.end:
                x = change.x if change.x is not None else x
                y = change.y if change.y is not None else y
                anchor = change.anchor or anchor
        return x, y, anchor

    def layout(self, caption: Caption) -> Layout:
        texts = [self.display(w) for w in caption.words]
        line = " ".join(texts)
        width = self._width(line, self.px)
        px = self.px * min(1.0, self.max_text_width / width) if width > 0 else self.px
        width = self._width(line, px)
        f = self.font
        cap = f.to_px(f.cap_height, px)
        box_w, box_h = width + 2 * self.box_pad[0], cap + 2 * self.box_pad[1]

        x, y, anchor = self.position_at(caption.start)
        cx = self.video.width * x / 100
        cy = self.video.height * y / 100
        if anchor == "top":
            cy += box_h / 2
        elif anchor == "bottom":
            cy -= box_h / 2
        # keep the whole subtitle inside the frame
        cx = min(max(cx, box_w / 2), self.video.width - box_w / 2) if box_w < self.video.width else self.video.width / 2
        cy = min(max(cy, box_h / 2), self.video.height - box_h / 2)
        text_y = cy + f.to_px(f.cap_height + f.descent - f.ascent, px) / 2
        return Layout(texts, px, width, cap, cx, cy, text_y)

    # -- output -------------------------------------------------------------

    def build(self, captions: list[Caption]) -> str:
        lines = self._header()
        for caption in captions:
            lines += self._events(caption)
        return "\n".join(lines) + "\n"

    def _header(self) -> list[str]:
        s, f, v = self.style, self.font, self.video
        primary = ass_color(s.color)
        box = ass_color(s.background.color, s.background.opacity)
        return [
            "[Script Info]",
            "; Generado por subtitleshort",
            "ScriptType: v4.00+",
            f"PlayResX: {v.width}",
            f"PlayResY: {v.height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "Kerning: yes",
            f"YCbCr Matrix: {ycbcr_matrix(v)}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Caption,{f.name},{f.ass_size(self.px):.2f},{primary},{primary},{ass_color(s.outline.color)},"
            f"{ass_color(s.shadow.color, s.shadow.opacity)},0,0,0,0,100,100,{self.spacing:.2f},0,1,"
            f"{s.outline.width * self.scale:.2f},{s.shadow.depth * self.scale:.2f},5,0,0,0,1",
            f"Style: Box,{f.name},{f.ass_size(self.px):.2f},{box},{box},&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

    def _events(self, caption: Caption) -> list[str]:
        s, lay = self.style, self.layout(caption)
        events = []
        if s.background.enabled:
            shape = rounded_rect(lay.width + 2 * self.box_pad[0], lay.cap + 2 * self.box_pad[1],
                                 s.background.radius * self.scale)
            events.append(_dialogue(0, caption.start, caption.end, "Box",
                                    f"{{\\an5\\pos({lay.cx:.1f},{lay.cy:.1f}){self._entrance(0)}\\p1}}{shape}"))

        words = [_escape(t) for t in lay.texts]
        if not s.highlight.enabled:
            events.append(_dialogue(2, caption.start, caption.end, "Caption", self._text_tags(lay, 0) + " ".join(words)))
            return events

        # One event per word: the word being spoken is highlighted.
        base, active = inline_color(s.color), inline_color(s.highlight.color)
        bounds = [caption.start] + [w.start for w in caption.words[1:]] + [caption.end]
        for i in range(1, len(bounds)):
            bounds[i] = min(max(bounds[i], bounds[i - 1]), caption.end)
        for i in range(len(words)):
            t0, t1 = bounds[i], bounds[i + 1]
            if t1 - t0 < 0.01:
                continue
            offset = (t0 - caption.start) * 1000
            text = " ".join(f"{{\\1c{active}}}{w}{{\\1c{base}}}" if j == i else w for j, w in enumerate(words))
            events.append(_dialogue(2, t0, t1, "Caption", self._text_tags(lay, offset) + text))
            if s.highlight.background:
                events += self._pill(lay, i, t0, t1, offset)
        return events

    def _pill(self, lay: Layout, i: int, t0: float, t1: float, offset: float) -> list[str]:
        """Rounded box behind the word being spoken."""
        if self.style.animation == "pop" and offset < POP[-1][0]:
            t0 += (POP[-1][0] - offset) / 1000  # appear once the pop settles: it scales around another centre
            if t1 - t0 < 0.01:
                return []
        hl = self.style.highlight
        prefix = " ".join(lay.texts[:i]) + (" " if i else "")
        left = lay.cx - lay.width / 2 + self._width(prefix, lay.px)
        word_w = self._width(lay.texts[i], lay.px)
        w, h = word_w + 2 * self.pill_pad[0], lay.cap + 2 * self.pill_pad[1]
        shape = rounded_rect(w, h, hl.radius * self.scale)
        tags = (f"\\an5\\pos({left + word_w / 2:.1f},{lay.cy:.1f})\\1c{inline_color(hl.background)}"
                f"\\1a{inline_alpha(hl.background, hl.background_opacity)}\\p1")
        return [_dialogue(1, t0, t1, "Box", f"{{{tags}}}{shape}")]

    def _text_tags(self, lay: Layout, offset: float) -> str:
        tags = f"\\an5\\pos({lay.cx:.1f},{lay.text_y:.1f})"
        if lay.px < self.px - 0.01:
            tags += f"\\fs{self.font.ass_size(lay.px):.2f}"
        return "{" + tags + self._entrance(offset) + "}"

    def _entrance(self, offset: float) -> str:
        if self.style.animation == "pop":
            return pop_tags(offset)
        if self.style.animation == "fade" and offset <= 0:
            return f"\\fad({FADE_MS},0)"
        return ""


def _dialogue(layer: int, start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def _escape(text: str) -> str:
    return re.sub(r"[{}\\]", lambda m: {"{": "(", "}": ")", "\\": "/"}[m.group()], text)
