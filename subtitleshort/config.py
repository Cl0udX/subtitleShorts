"""Style files (YAML): defaults, loading and friendly validation."""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional, get_type_hints

import yaml

ANCHORS = ("top", "center", "bottom")
ANIMATIONS = ("none", "pop", "fade")
ENCODERS = ("x264", "nvenc")
DEVICES = ("auto", "cuda", "cpu")

COLOR_NAMES = {
    "white": "FFFFFF", "blanco": "FFFFFF",
    "black": "000000", "negro": "000000",
    "yellow": "FFE81F", "amarillo": "FFE81F",
    "red": "FF3B30", "rojo": "FF3B30",
    "green": "22C55E", "verde": "22C55E",
    "blue": "3B82F6", "azul": "3B82F6",
    "purple": "8B5CF6", "morado": "8B5CF6",
    "orange": "FF8A00", "naranja": "FF8A00",
    "pink": "EC4899", "rosa": "EC4899",
    "cyan": "22D3EE", "cian": "22D3EE",
}


class ConfigError(ValueError):
    pass


@dataclass
class Outline:
    width: float = 6
    color: str = "#000000"


@dataclass
class Shadow:
    depth: float = 0
    color: str = "#000000"
    opacity: float = 0.6


@dataclass
class Background:
    enabled: bool = False
    color: str = "#000000"
    opacity: float = 0.6
    padding: Any = (28, 14)
    radius: float = 20


@dataclass
class Highlight:
    enabled: bool = True
    color: str = "#FFE81F"
    background: Optional[str] = None
    background_opacity: float = 1.0
    padding: Any = (14, 8)
    radius: float = 14


@dataclass
class Position:
    x: float = 50
    y: float = 70
    anchor: str = "center"


@dataclass
class PositionChange:
    start: Any = 0
    end: Any = None
    x: Optional[float] = None
    y: Optional[float] = None
    anchor: Optional[str] = None


@dataclass
class Transcription:
    model: str = "large-v3-turbo"
    language: Optional[str] = None
    vocabulary: Any = field(default_factory=list)
    device: str = "auto"


@dataclass
class VideoOutput:
    encoder: str = "x264"
    crf: float = 12
    preset: str = "slow"


@dataclass
class Style:
    font: str = "Montserrat-ExtraBold.ttf"
    size: float = 80
    uppercase: bool = True
    letter_spacing: float = 0
    color: str = "#FFFFFF"
    outline: Outline = field(default_factory=Outline)
    shadow: Shadow = field(default_factory=Shadow)
    background: Background = field(default_factory=Background)
    highlight: Highlight = field(default_factory=Highlight)
    position: Position = field(default_factory=Position)
    position_changes: list = field(default_factory=list)
    max_words: int = 3
    max_chars: int = 22
    max_width: float = 90
    animation: str = "pop"
    remove_punctuation: bool = True
    replacements: dict = field(default_factory=dict)
    transcription: Transcription = field(default_factory=Transcription)
    video: VideoOutput = field(default_factory=VideoOutput)


# YAML keys that differ from the attribute name ('from' is a Python keyword).
_ALIASES = {PositionChange: {"from": "start", "to": "end"}}


def load_style(path: Optional[Path]) -> Style:
    if path is None:
        return _validate(Style())
    if not path.is_file():
        raise ConfigError(f"No existe el archivo de estilo: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Error de formato en {path}:\n{e}") from None
    return _validate(_build(Style, data or {}, ""))


def parse_color(value: Any) -> tuple[int, int, int, int]:
    """'#RRGGBB', '#RRGGBBAA', '#RGB' or a color name -> (r, g, b, a)."""
    if value is None:
        raise ConfigError(
            'Color vacío. En YAML los colores van entre comillas: color: "#FFFFFF"'
        )
    text = str(value).strip()
    text = COLOR_NAMES.get(text.lower(), text).lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8}", text):
        raise ConfigError(f"Color inválido '{value}': usa el formato \"#RRGGBB\" (ej. \"#FFE81F\")")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) == 6:
        text += "FF"
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]


def parse_time(value: Any) -> float:
    """Seconds (12.5) or clock strings ('1:12.5', '0:01:12')."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        total = 0.0
        for part in str(value).strip().replace(",", ".").split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        raise ConfigError(f"Tiempo inválido '{value}': usa segundos (12.5) o minutos:segundos (1:12.5)") from None


def _build(cls, data: Any, where: str):
    if not isinstance(data, dict):
        raise ConfigError(f"'{where.rstrip('.') or 'estilo'}' debe ser una sección de opciones (clave: valor)")
    hints = get_type_hints(cls)
    aliases = _ALIASES.get(cls, {})
    names = [f.name for f in fields(cls)]
    kwargs = {}
    for key, value in data.items():
        key = str(key)
        name = aliases.get(key, key.replace("-", "_"))
        if name not in names:
            options = [k for k in names if k not in aliases.values()] + list(aliases)
            close = difflib.get_close_matches(key, options, n=1)
            hint = f" ¿Quisiste decir '{close[0]}'?" if close else ""
            raise ConfigError(f"Opción desconocida '{where}{key}'.{hint}")
        target = hints[name]
        if isinstance(target, type) and is_dataclass(target):
            value = _build(target, value if value is not None else {}, f"{where}{key}.")
        kwargs[name] = value
    return cls(**kwargs)


def _validate(s: Style) -> Style:
    if not isinstance(s.font, str) or not s.font.strip():
        raise ConfigError("'font' debe ser el archivo de una fuente (ej. Montserrat-ExtraBold.ttf) o su nombre")
    # Sections left empty in the YAML (only comments under them) arrive as None.
    s.position_changes = s.position_changes or []
    s.replacements = s.replacements or {}
    for label, value in (
        ("color", s.color),
        ("outline.color", s.outline.color),
        ("shadow.color", s.shadow.color),
        ("background.color", s.background.color),
        ("highlight.color", s.highlight.color),
    ):
        _check(label, parse_color, value)
    if s.highlight.background:
        _check("highlight.background", parse_color, s.highlight.background)

    s.background.padding = _pair("background.padding", s.background.padding)
    s.highlight.padding = _pair("highlight.padding", s.highlight.padding)
    if s.animation in (None, False):
        s.animation = "none"
    _choice("position.anchor", s.position.anchor, ANCHORS)
    _choice("animation", s.animation, ANIMATIONS)
    _choice("video.encoder", s.video.encoder, ENCODERS)
    _choice("transcription.device", s.transcription.device, DEVICES)

    for label, value, low, high in (
        ("size", s.size, 1, 1000),
        ("max_words", s.max_words, 1, 50),
        ("max_chars", s.max_chars, 1, 500),
        ("max_width", s.max_width, 10, 100),
        ("position.x", s.position.x, 0, 100),
        ("position.y", s.position.y, 0, 100),
        ("video.crf", s.video.crf, 0, 51),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            raise ConfigError(f"'{label}' debe ser un número entre {low} y {high} (tiene: {value!r})")

    if not isinstance(s.position_changes, list):
        raise ConfigError("'position_changes' debe ser una lista (cada elemento empieza con '- from: ...')")
    changes = []
    for i, item in enumerate(s.position_changes):
        ch = _build(PositionChange, item, f"position_changes[{i + 1}].")
        ch.start = parse_time(ch.start)
        ch.end = math.inf if ch.end is None else parse_time(ch.end)
        if ch.anchor is not None:
            _choice(f"position_changes[{i + 1}].anchor", ch.anchor, ANCHORS)
        changes.append(ch)
    s.position_changes = changes

    if not isinstance(s.replacements, dict):
        raise ConfigError("'replacements' debe ser una lista de 'texto: reemplazo'")
    vocab = s.transcription.vocabulary
    if isinstance(vocab, str):
        vocab = [v.strip() for v in vocab.split(",")]
    s.transcription.vocabulary = [str(v) for v in vocab or [] if str(v).strip()]
    if s.transcription.language in ("", "auto"):
        s.transcription.language = None
    return s


def _check(label: str, fn, value) -> None:
    try:
        fn(value)
    except ConfigError as e:
        raise ConfigError(f"'{label}': {e}") from None


def _choice(label: str, value: Any, options: tuple) -> None:
    if value not in options:
        raise ConfigError(f"'{label}' debe ser uno de: {', '.join(options)} (tiene: {value!r})")


def _pair(label: str, value: Any) -> tuple[float, float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), float(value)
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
        return float(value[0]), float(value[1])
    raise ConfigError(f"'{label}' debe ser un número o [horizontal, vertical] (tiene: {value!r})")
