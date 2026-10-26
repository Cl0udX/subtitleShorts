"""Font lookup, metrics and text measurement.

Text is measured with HarfBuzz, the same shaper libass uses to draw it, so
background boxes and highlight pills line up with the rendered text.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTCollection, TTFont

FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".otc"}
PROJECT_FONTS = Path(__file__).resolve().parent.parent / "fonts"


class FontError(ValueError):
    pass


@dataclass(frozen=True)
class Font:
    path: Path
    index: int  # face inside a .ttc collection
    name: str  # full name (name ID 4); libass matches it exactly
    upem: int
    ascent: int  # the vertical metrics libass sizes the font with
    descent: int
    cap_height: int

    def ass_size(self, px: float) -> float:
        """ASS font size that renders an em square of `px` pixels.

        libass (like GDI) scales a font so that ascent + descent equals the
        requested size, while `px` is the usual design-tool font size."""
        return px * (self.ascent + self.descent) / self.upem

    def to_px(self, units: float, px: float) -> float:
        return units * px / self.upem


class TextMeasurer:
    def __init__(self, font: Font):
        self.font = font
        face = hb.Face(hb.Blob(font.path.read_bytes()), font.index)
        self._hb = hb.Font(face)
        self._hb.scale = (face.upem, face.upem)
        self._cache: dict[str, int] = {}

    def advance(self, text: str) -> int:
        """Advance width in font units, kerned like libass with 'Kerning: yes'."""
        if text not in self._cache:
            buf = hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            hb.shape(self._hb, buf, {"kern": True})
            self._cache[text] = sum(pos.x_advance for pos in buf.glyph_positions or ())
        return self._cache[text]

    def width(self, text: str, px: float, spacing: float = 0.0) -> float:
        return self.advance(text) * px / self.font.upem + spacing * len(text)


def find_font(spec: str, search_dirs: list[Path] = ()) -> Font:
    """Resolve a font file name ('Montserrat-ExtraBold.ttf') or a font name
    ('Montserrat ExtraBold', 'Impact') to a font on disk."""
    spec = str(spec).strip()
    dirs = [d for d in (*search_dirs, Path.cwd() / "fonts", PROJECT_FONTS) if d.is_dir()]
    path = Path(spec).expanduser()
    if path.suffix.lower() in FONT_EXTENSIONS:
        candidates = [path] if path.is_absolute() else [Path.cwd() / path, *(d / path for d in dirs)]
        candidates += [d / path.name for d in system_font_dirs()]
        for candidate in candidates:
            if candidate.is_file():
                return load_font(candidate)
        raise FontError(f"No encontré la fuente '{spec}'. Copia el archivo a la carpeta fonts/ del proyecto.")

    key = _norm(spec)
    for directory in dirs + system_font_dirs():
        files = directory.rglob("*") if directory in dirs else directory.iterdir()
        for file in sorted(files):
            if file.suffix.lower() not in FONT_EXTENSIONS:
                continue
            try:
                for index, tt in _faces(file):
                    if key in _names(tt):
                        return _info(file, index, tt)
            except Exception:  # broken or unsupported font files are skipped
                continue
    raise FontError(
        f"No encontré una fuente llamada '{spec}'. Pon el archivo .ttf/.otf en fonts/ "
        "y escribe su nombre de archivo en el estilo (ej. font: MiFuente-Bold.ttf)."
    )


def load_font(path: Path, index: int = 0) -> Font:
    try:
        for i, tt in _faces(path):
            if i == index:
                return _info(path, i, tt)
    except Exception as e:
        raise FontError(f"No pude leer la fuente {path}: {e}") from None
    raise FontError(f"La fuente {path} no tiene la cara {index}")


def copy_font(font: Font, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(font.path, directory / font.path.name)


def system_font_dirs() -> list[Path]:
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                Path.home() / ".local" / "share" / "fonts", Path.home() / ".fonts"]
    return [d for d in dirs if d.is_dir()]


def _faces(path: Path):
    if path.suffix.lower() in (".ttc", ".otc"):
        yield from enumerate(TTCollection(str(path), lazy=True).fonts)
    else:
        yield 0, TTFont(str(path), lazy=True)


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).casefold()


def _names(tt: TTFont) -> set[str]:
    table = tt["name"]
    names = set()
    for record in table.names:
        if record.nameID in (1, 4, 6, 16):
            try:
                names.add(_norm(record.toUnicode()))
            except UnicodeDecodeError:
                pass
    family, subfamily = table.getDebugName(16), table.getDebugName(17)
    if family and subfamily:
        names.add(_norm(f"{family} {subfamily}"))
    return names


def _info(path: Path, index: int, tt: TTFont) -> Font:
    table = tt["name"]
    name = (table.getDebugName(4) or table.getDebugName(1) or path.stem).replace(",", " ")
    os2 = tt["OS/2"] if "OS/2" in tt else None
    hhea = tt["hhea"]
    # Same order libass uses: Windows metrics, then typographic, then hhea.
    if os2 is not None and os2.usWinAscent + os2.usWinDescent:
        ascent, descent = os2.usWinAscent, os2.usWinDescent
    elif os2 is not None and os2.sTypoAscender - os2.sTypoDescender:
        ascent, descent = os2.sTypoAscender, -os2.sTypoDescender
    else:
        ascent, descent = hhea.ascent, -hhea.descent
    cap = getattr(os2, "sCapHeight", 0) if os2 is not None else 0
    if not cap:
        cap = _glyph_top(tt, "H") or round(ascent * 0.7)
    return Font(path.resolve(), index, name, tt["head"].unitsPerEm, ascent, descent, cap)


def _glyph_top(tt: TTFont, char: str) -> int:
    glyph = tt.getBestCmap().get(ord(char))
    if glyph is None:
        return 0
    glyphs = tt.getGlyphSet()
    pen = BoundsPen(glyphs)
    glyphs[glyph].draw(pen)
    return round(pen.bounds[3]) if pen.bounds else 0
