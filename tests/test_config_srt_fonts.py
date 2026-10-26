from pathlib import Path

import pytest

from subtitleshort.config import ConfigError, load_style, parse_color, parse_time
from subtitleshort.fonts import TextMeasurer, find_font
from subtitleshort.srt import Cue, read_srt, write_srt

STYLES = Path(__file__).resolve().parent.parent / "styles"


@pytest.mark.parametrize("path", sorted(STYLES.glob("*.yaml")), ids=lambda p: p.name)
def test_bundled_styles_load(path):
    style = load_style(path)
    find_font(style.font, [STYLES])


def test_unknown_option_suggests_the_right_one(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("posicion:\n  y: 30\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="position"):
        load_style(f)


def test_unquoted_color_explains_the_quotes(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("color: #FFFFFF\n", encoding="utf-8")  # YAML reads this as a comment
    with pytest.raises(ConfigError, match="comillas"):
        load_style(f)


def test_position_changes_and_times(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("position_changes:\n  - from: 1:05.5\n    y: 80\n  - from: '0:10'\n    to: 12\n    x: 20\n",
                 encoding="utf-8")
    first, second = load_style(f).position_changes
    assert (first.start, first.end, first.y) == (65.5, float("inf"), 80)
    assert (second.start, second.end, second.x) == (10.0, 12.0, 20)


def test_colors():
    assert parse_color("#fff") == (255, 255, 255, 255)
    assert parse_color("amarillo")[:3] == (255, 232, 31)
    assert parse_color("#00000080")[3] == 128
    assert parse_time("1:12.5") == 72.5


def test_srt_roundtrip_and_forgiving_parser(tmp_path):
    f = tmp_path / "a.srt"
    write_srt(f, [Cue(1.5, 2.25, "hola mundo"), Cue(3, 4, "¿qué tal?")])
    assert read_srt(f) == [Cue(1.5, 2.25, "hola mundo"), Cue(3.0, 4.0, "¿qué tal?")]
    # Windows line endings, BOM, missing blank line, '.' for milliseconds, text on two lines
    f.write_bytes("﻿1\r\n00:00:01.5 --> 00:00:02,000\r\nuno\r\ndos\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\ntres\r\n"
                  .encode("utf-8"))
    assert read_srt(f) == [Cue(1.5, 2.0, "uno dos"), Cue(3.0, 4.0, "tres")]


def test_fonts_by_file_or_name_and_metrics():
    by_file = find_font("Montserrat-Black.ttf")
    by_name = find_font("Montserrat Black")
    assert by_file.path == by_name.path and by_file.name == "Montserrat Black"
    # libass sizes Montserrat by its Windows metrics (1109 + 453 units per 1000 em)
    assert by_file.ass_size(100) == pytest.approx(156.2)
    m = TextMeasurer(by_file)
    assert 0 < m.width("HOLA", 80) < m.width("HOLA MUNDO", 80)
    assert m.width("", 80) == 0
