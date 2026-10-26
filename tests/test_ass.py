import re
from pathlib import Path

import pytest

from subtitleshort.ass import AssBuilder, ass_color, ass_time, pop_tags, rounded_rect
from subtitleshort.captions import Caption, Word
from subtitleshort.config import Style
from subtitleshort.fonts import TextMeasurer, find_font
from subtitleshort.media import VideoInfo

VIDEO = VideoInfo(Path("x.mp4"), 1080, 1920, 10.0, 60, "h264", "yuv420p", None, "bt709", "bt709", "bt709", "tv", [])


@pytest.fixture(scope="module")
def font():
    return find_font("Montserrat-ExtraBold.ttf")


def builder(font, **changes):
    style = Style()
    for key, value in changes.items():
        obj = style
        *path, last = key.split("__")
        for part in path:
            obj = getattr(obj, part)
        setattr(obj, last, value)
    return AssBuilder(style, VIDEO, font, TextMeasurer(font))


def caption(*words):
    ws = [Word(w, i * 0.4, i * 0.4 + 0.4) for i, w in enumerate(words)]
    return Caption(ws, ws[0].start, ws[-1].end)


def test_colors_and_times():
    assert ass_color("#FF0000") == "&H000000FF"
    assert ass_color("#000000", 0.6) == "&H66000000"
    assert ass_time(3725.456) == "1:02:05.46"


def test_rounded_rect_stays_inside_its_box():
    assert rounded_rect(100, 40, 0) == "m 0 0 l 100 0 l 100 40 l 0 40"
    numbers = [int(n) for n in re.findall(r"-?\d+", rounded_rect(100, 40, 30))]
    assert min(numbers) == 0 and max(numbers[0::2]) == 100 and max(numbers[1::2]) == 40


def test_pop_continues_across_word_events():
    assert pop_tags(0).startswith("\\fscx78")
    assert pop_tags(200) == ""
    assert "\\t(0," in pop_tags(100)


def test_one_event_per_word_with_highlight(font):
    ass = builder(font).build([caption("vamos", "a", "continuar")])
    events = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert len(events) == 3
    assert "{\\1c&H1FE8FF&}VAMOS{\\1c&HFFFFFF&} A CONTINUAR" in events[0]


def test_background_box_and_pill(font):
    b = builder(font, background__enabled=True, highlight__background="#7C3AED", animation="none")
    events = [line for line in b.build([caption("hola", "mundo")]).splitlines() if line.startswith("Dialogue")]
    assert sum(",Box," in e for e in events) == 3  # 1 box + 1 pill per word
    assert all("\\p1}m " in e for e in events if ",Box," in e)


def test_anchor_top_puts_the_top_edge_on_y(font):
    b = builder(font, position__y=33, position__anchor="top")
    lay = b.layout(caption("hola"))
    assert lay.cy - lay.cap / 2 == pytest.approx(1920 * 0.33, abs=0.5)


def test_too_wide_caption_is_shrunk_to_fit(font):
    b = builder(font)
    lay = b.layout(caption("ANTICONSTITUCIONALMENTE"))
    assert lay.width <= 1080 * 0.9 + 0.5 and lay.px < 80


def test_position_changes_apply_by_time(font):
    from subtitleshort.config import PositionChange

    b = builder(font, position_changes=[PositionChange(start=5, end=8, y=80)])
    assert b.position_at(1)[1] == 70 and b.position_at(6)[1] == 80 and b.position_at(9)[1] == 70
