"""Vertical thumbnails (covers) for shorts.

1. Pick the moment:  python -m subtitleshort.thumbnail videos/clip.mp4 --sheet
2. Make the cover:   python -m subtitleshort.thumbnail videos/clip.mp4 --at 34.1 \\
                         --text "MI PLAN|*PERFECTO*" --face 50,16 --emoji calavera,risa

Covers are edited the way viral shorts are: the reaction zoomed in with manga
speed lines, the gameplay moment below, emoji stickers, a dramatic vignette and
big tilted text.
"""
from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import ConfigError, parse_color, parse_time
from .fonts import FontError, find_font
from .media import MediaError, VideoInfo, probe, require_ffmpeg

W, H = 1080, 1920
FACE_PANEL = 900  # height of the zoomed reaction; the gameplay fills the rest
SHEET_COLUMNS, SHEET_ROWS = 4, 3
SHEET_TILE_WIDTH = 270
EMOJI_DIR = Path(__file__).resolve().parent.parent / "assets" / "emoji"
EMOJI_NAMES = {
    "calavera": "💀", "risa": "😂", "carcajada": "🤣", "llorar": "😭", "grito": "😱", "fuego": "🔥",
    "payaso": "🤡", "mate": "🧉", "ojos": "👀", "explota": "🤯", "enojado": "😤", "sonrojado": "😳",
    "nervios": "😬", "ojos_en_blanco": "🙄", "aguantando": "🥹", "crack": "😎", "control": "🎮",
    "100": "💯", "x": "❌", "check": "✅", "pregunta": "❓",
}
# (centre x, centre y, size, rotation) for the 1st, 2nd and 3rd sticker
STICKER_SLOTS = ((W - 190, 250, 300, 14), (W - 200, H - 330, 280, -10), (180, 230, 240, -14))


@dataclass
class Cover:
    text: str
    face: Optional[tuple[float, float]] = None  # face centre, % of the frame: enables the reaction layout
    zoom: float = 1.7
    cam_bottom: float = 31  # % of the frame where the facecam ends
    game: tuple[float, float] = (50, 68)  # gameplay focus, % of the frame
    game_top: float = 37  # % of the frame where the gameplay starts
    emojis: tuple[str, ...] = ()
    lines: bool = True
    red: bool = True
    arrow: Optional[tuple[float, float]] = None  # what to point at, % of the frame
    circle: Optional[tuple[float, float]] = None
    y: Optional[float] = None  # text centre, % of the cover
    color: str = "#FFFFFF"
    highlight: str = "#FFE81F"
    tilt: float = 4  # degrees, rising to the right


@dataclass
class _Panel:
    src: tuple[float, float, float, float]  # box in the frame
    dst: tuple[int, int, int, int]  # box in the cover

    def map(self, x: float, y: float) -> Optional[tuple[float, float]]:
        sx0, sy0, sx1, sy1 = self.src
        if not (sx0 <= x <= sx1 and sy0 <= y <= sy1):
            return None
        dx0, dy0, dx1, dy1 = self.dst
        return dx0 + (x - sx0) * (dx1 - dx0) / (sx1 - sx0), dy0 + (y - sy0) * (dy1 - dy0) / (sy1 - sy0)


def sheet_times(duration: float, count: int = SHEET_COLUMNS * SHEET_ROWS) -> list[float]:
    """Evenly spread moments, skipping the very first and last instants."""
    return [round(duration * (0.04 + 0.92 * i / (count - 1)), 2) for i in range(count)]


def contact_sheet(info: VideoInfo, out: Path) -> list[float]:
    times = sheet_times(info.duration)
    with tempfile.TemporaryDirectory(prefix="subtitleshort-") as tmp:
        for i, t in enumerate(times):
            _ffmpeg(["-ss", f"{t:.3f}", "-i", str(info.path), "-frames:v", "1",
                     "-vf", f"scale={SHEET_TILE_WIDTH}:-2", str(Path(tmp) / f"f_{i:02d}.png")])
        _ffmpeg(["-i", str(Path(tmp) / "f_%02d.png"), "-frames:v", "1", "-q:v", "3",
                 "-vf", f"tile={SHEET_COLUMNS}x{SHEET_ROWS}:padding=6:color=white", str(out)])
    return times


def grab_frame(info: VideoInfo, at: float) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="subtitleshort-") as tmp:
        png = Path(tmp) / "frame.png"
        _ffmpeg(["-ss", f"{at:.3f}", "-i", str(info.path), "-frames:v", "1", str(png)])
        with Image.open(png) as img:
            return img.convert("RGB")


def compose(frame: Image.Image, c: Cover, font_path: Path, game_frame: Optional[Image.Image] = None) -> Image.Image:
    """`game_frame` (same size as `frame`) lets the gameplay panel come from another moment."""
    frame = frame.convert("RGB")
    sources = [frame, (game_frame or frame).convert("RGB")]
    sw, sh = frame.size
    panels: list[_Panel] = []
    if c.face:
        fx, fy = c.face[0] * sw / 100, c.face[1] * sh / 100
        cw = sw / c.zoom
        ch = cw * FACE_PANEL / W
        x0 = _clamp(fx - cw / 2, 0, sw - cw)
        y0 = _clamp(fy - ch / 2, 0, max(0.0, c.cam_bottom * sh / 100 - ch))
        panels.append(_Panel((x0, y0, x0 + cw, y0 + ch), (0, 0, W, FACE_PANEL)))
        gch = sw * (H - FACE_PANEL) / W
        top = c.game_top * sh / 100
        gy0 = _clamp(c.game[1] * sh / 100 - gch / 2, top, max(top, sh - gch))
        panels.append(_Panel((0, gy0, sw, gy0 + gch), (0, FACE_PANEL, W, H)))
    else:
        cw = min(sw, sh * W / H)
        ch = cw * H / W
        panels.append(_Panel(((sw - cw) / 2, (sh - ch) / 2, (sw + cw) / 2, (sh + ch) / 2), (0, 0, W, H)))

    canvas = Image.new("RGB", (W, H))
    for p, source in zip(panels, sources):
        size = (p.dst[2] - p.dst[0], p.dst[3] - p.dst[1])
        canvas.paste(source.resize(size, Image.LANCZOS, box=p.src), p.dst[:2])
    canvas = ImageEnhance.Contrast(canvas).enhance(1.12)
    canvas = ImageEnhance.Color(canvas).enhance(1.3)
    canvas = canvas.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2)).convert("RGBA")

    if c.face and c.lines:
        _speed_lines(canvas, panels[0].map(fx, fy) or (W / 2, FACE_PANEL / 2), FACE_PANEL)
    _vignette(canvas, c.red)
    if c.face:
        _divider(canvas, FACE_PANEL)
    for target, draw in ((c.circle, _circle), (c.arrow, _arrow)):
        if target:
            point = next((p.map(target[0] * sw / 100, target[1] * sh / 100) for p in panels
                          if p.map(target[0] * sw / 100, target[1] * sh / 100)), None)
            if point:
                draw(canvas, point)
    title_y = c.y * H / 100 if c.y is not None else (FACE_PANEL + 20 if c.face else H * 0.62)
    for (x, y, size, angle), spec in zip(STICKER_SLOTS, c.emojis):
        name, _, at = spec.partition("@")  # "calavera@85,40" places it at 85%, 40% of the cover
        if at:
            px, py = _point(at, "--emoji")
            x, y = px * W / 100, py * H / 100
        elif title_y < H * 0.3 and y < H / 2:
            y += 380  # keep clear of a title placed at the top
        _sticker(canvas, load_emoji(name), x, y, size, angle)
    _title(canvas, c.text, font_path, title_y, c.color, c.highlight, c.tilt)
    return canvas.convert("RGB")


def load_emoji(name: str) -> Image.Image:
    char = EMOJI_NAMES.get(name.strip().lower(), name.strip())
    file = EMOJI_DIR / ("emoji_u" + "_".join(f"{ord(ch):04x}" for ch in char if ch != "️") + ".png")
    if not file.is_file():
        names = ", ".join(f"{k} {v}" for k, v in EMOJI_NAMES.items())
        raise ConfigError(f"No tengo el emoji '{name}'. Disponibles: {names}")
    with Image.open(file) as img:
        return img.convert("RGBA")


def _speed_lines(canvas: Image.Image, center: tuple[float, float], height: int, count: int = 76) -> None:
    """Manga focus lines converging on the face, leaving it clear."""
    layer = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rnd = random.Random(7)
    cx, cy = center
    far = math.hypot(W, height)
    clear = min(W, height) * 0.42
    for i in range(count):
        a = 2 * math.pi * (i + rnd.random() * 0.7) / count
        spread = rnd.uniform(0.004, 0.013)
        r = clear * rnd.uniform(1.0, 1.4)
        draw.polygon([(cx + r * math.cos(a), cy + r * math.sin(a)),
                      (cx + far * math.cos(a - spread), cy + far * math.sin(a - spread)),
                      (cx + far * math.cos(a + spread), cy + far * math.sin(a + spread))],
                     fill=(255, 255, 255, rnd.randint(110, 190)))
    canvas.alpha_composite(layer)


def _vignette(canvas: Image.Image, red: bool) -> None:
    gradient = Image.radial_gradient("L").resize(canvas.size, Image.BILINEAR)  # 0 centre, 255 edges
    for color, start, gain in (((0, 0, 0), 90, 1.1), ((215, 25, 25), 125, 1.0)) if red else (((0, 0, 0), 90, 1.1),):
        layer = Image.new("RGBA", canvas.size, (*color, 0))
        layer.putalpha(gradient.point(lambda v: int(min(255, max(0, v - start) * gain))))
        canvas.alpha_composite(layer)


def _divider(canvas: Image.Image, y: int) -> None:
    """Tilted comic-style band hiding the seam between both panels."""
    draw = ImageDraw.Draw(canvas)
    for color, half in (((0, 0, 0, 255), 21), ((255, 255, 255, 255), 15)):
        draw.polygon([(0, y + 8 - half), (W, y - 8 - half), (W, y - 8 + half), (0, y + 8 + half)], fill=color)


def _circle(canvas: Image.Image, p: tuple[float, float], r: int = 125) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y = p
    draw.ellipse((x - r - 5, y - r - 5, x + r + 5, y + r + 5), outline=(255, 255, 255, 255), width=24)
    draw.ellipse((x - r, y - r, x + r, y + r), outline=(232, 24, 24, 255), width=14)


def _arrow(canvas: Image.Image, p: tuple[float, float]) -> None:
    """Fat red arrow coming from the centre of the cover towards p."""
    tx, ty = p
    dx, dy = W / 2 - tx, H / 2 - ty
    if math.hypot(dx, dy) < 1:
        dx, dy = 1.0, 1.0
    norm = math.hypot(dx, dy)
    ux, uy = dx / norm, dy / norm  # from the tip towards the tail
    draw = ImageDraw.Draw(canvas)
    for color, grow in (((255, 255, 255, 255), 9), ((232, 24, 24, 255), 0)):
        tip = (tx + ux * (40 - grow), ty + uy * (40 - grow))
        draw.polygon(_arrow_shape(tip, (ux, uy), 300 + grow, 17 + grow, 58 + grow, 105 + grow), fill=color)


def _arrow_shape(tip, direction, length, shaft, head_half, head_len):
    ux, uy = direction
    px, py = -uy, ux  # perpendicular
    tx, ty = tip
    neck = (tx + ux * head_len, ty + uy * head_len)
    tail = (tx + ux * length, ty + uy * length)
    return [tip,
            (neck[0] + px * head_half, neck[1] + py * head_half), (neck[0] + px * shaft, neck[1] + py * shaft),
            (tail[0] + px * shaft, tail[1] + py * shaft), (tail[0] - px * shaft, tail[1] - py * shaft),
            (neck[0] - px * shaft, neck[1] - py * shaft), (neck[0] - px * head_half, neck[1] - py * head_half)]


def _sticker(canvas: Image.Image, emoji: Image.Image, x: float, y: float, size: int, angle: float) -> None:
    pad = size // 6
    art = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    art.alpha_composite(emoji.resize((size, size), Image.LANCZOS), (pad, pad))
    border = art.getchannel("A").filter(ImageFilter.GaussianBlur(size / 28)).point(lambda v: 255 if v > 6 else 0)
    sticker = Image.new("RGBA", art.size, (255, 255, 255, 0))
    sticker.putalpha(border.filter(ImageFilter.GaussianBlur(1)))
    sticker.alpha_composite(art)
    sticker = sticker.rotate(angle, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", sticker.size, (0, 0, 0, 0))
    shadow.putalpha(sticker.getchannel("A").filter(ImageFilter.GaussianBlur(size / 24)).point(lambda v: v * 6 // 10))
    _paste(canvas, shadow, x - sticker.width / 2 + 10, y - sticker.height / 2 + 14)
    _paste(canvas, sticker, x - sticker.width / 2, y - sticker.height / 2)


def _title(canvas: Image.Image, text: str, font_path: Path, cy: float, color: str, highlight: str,
           tilt: float) -> None:
    lines = [line.strip().upper() for line in text.split("|") if line.strip()]
    if not lines:
        raise ConfigError("El texto de la miniatura está vacío")
    size = 150
    font = ImageFont.truetype(str(font_path), size)
    widest = max(font.getlength(line.replace("*", "")) for line in lines)
    if widest > W * 0.86:
        size = int(size * W * 0.86 / widest)
        font = ImageFont.truetype(str(font_path), size)
    stroke = max(8, round(size * 0.095))
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * 0.9)
    layer = Image.new("RGBA", (W + 200, line_h * len(lines) + 4 * stroke + 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rgba = {False: parse_color(color), True: parse_color(highlight)}
    for outline_pass in (True, False):  # all outlines first so they never cover a neighbouring fill
        for i, line in enumerate(lines):
            parts = line.split("*")
            x = (layer.width - sum(font.getlength(part) for part in parts)) / 2
            y = 2 * stroke + 20 + i * line_h
            for j, part in enumerate(parts):
                if part and outline_pass:
                    draw.text((x, y), part, font=font, fill=(0, 0, 0, 255), stroke_width=stroke,
                              stroke_fill=(0, 0, 0, 255))
                elif part:
                    draw.text((x, y), part, font=font, fill=rgba[j % 2 == 1])
                x += font.getlength(part)
    layer = layer.crop(layer.getbbox() or (0, 0, 1, 1)).rotate(tilt, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(layer.getchannel("A").filter(ImageFilter.GaussianBlur(10)).point(lambda v: v * 7 // 10))
    cy = _clamp(cy, layer.height / 2 + 10, H - layer.height / 2 - 10)
    _paste(canvas, shadow, (W - layer.width) / 2 + 12, cy - layer.height / 2 + 16)
    _paste(canvas, layer, (W - layer.width) / 2, cy - layer.height / 2)


def _paste(canvas: Image.Image, layer: Image.Image, x: float, y: float) -> None:
    """alpha_composite that tolerates layers sticking out of the canvas."""
    x, y = round(x), round(y)
    left, top = max(0, -x), max(0, -y)
    if left or top:
        layer = layer.crop((left, top, layer.width, layer.height))
        x, y = x + left, y + top
    if layer.width > 0 and layer.height > 0 and x < canvas.width and y < canvas.height:
        canvas.alpha_composite(layer, (x, y))


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _point(text: Optional[str], label: str) -> Optional[tuple[float, float]]:
    if text is None:
        return None
    try:
        x, y = (float(v) for v in text.split(","))
        return x, y
    except ValueError:
        raise ConfigError(f"{label} debe ser X,Y en % del video (ej. 50,16), no '{text}'") from None


def _ffmpeg(args: list[str], cwd: Optional[Path] = None) -> None:
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], cwd=cwd,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise MediaError(f"ffmpeg falló:\n{result.stderr.strip()}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="python -m subtitleshort.thumbnail",
                                description="Miniaturas verticales editadas para shorts.")
    p.add_argument("video", type=Path, help="video original (sin subtítulos)")
    p.add_argument("--sheet", action="store_true", help="hoja con 12 fotogramas para elegir el momento")
    p.add_argument("--at", help="segundo del fotograma (ej. 12.4 o 0:12.4)")
    p.add_argument("--text", help='texto: *palabra* la resalta y | parte la línea (ej. "MI PLAN|*PERFECTO*")')
    p.add_argument("--face", help="centro de tu cara en %% del video (ej. 50,16): cara en zoom arriba y juego abajo")
    p.add_argument("--zoom", type=float, default=1.7, help="zoom de la cara (por defecto 1.7)")
    p.add_argument("--game", default="50,68", help="qué parte del juego mostrar, en %% del video (por defecto 50,68)")
    p.add_argument("--game-at", help="tomar el juego de otro segundo (ej. la reacción en --at y el meme en --game-at)")
    p.add_argument("--emoji", action="append", default=[],
                   help="sticker (hasta 3), por nombre o emoji: --emoji calavera,risa  o  --emoji risa@18,88 "
                        "para ubicarlo en %% de la miniatura (se puede repetir)")
    p.add_argument("--arrow", help="flecha roja apuntando a X,Y (%% del video)")
    p.add_argument("--circle", help="círculo rojo alrededor de X,Y (%% del video)")
    p.add_argument("--no-lines", action="store_true", help="sin líneas de impacto alrededor de la cara")
    p.add_argument("--no-red", action="store_true", help="viñeta oscura sin el tono rojo dramático")
    p.add_argument("--y", type=float, help="altura del centro del texto en %% de la miniatura")
    p.add_argument("--tilt", type=float, default=4, help="inclinación del texto en grados (por defecto 4)")
    p.add_argument("--font", default="Montserrat-Black.ttf", help="fuente (archivo en fonts/ o nombre)")
    p.add_argument("--color", default="#FFFFFF", help="color del texto")
    p.add_argument("--highlight", default="#FFE81F", help="color de las palabras entre *asteriscos*")
    p.add_argument("--cam-bottom", type=float, default=31, help="%% de la altura donde termina tu cámara")
    p.add_argument("--game-top", type=float, default=37, help="%% de la altura donde empieza el juego")
    p.add_argument("-o", "--out", type=Path, default=Path("output"), help="carpeta de salida (por defecto: output)")
    args = p.parse_args(argv)
    try:
        require_ffmpeg()
        info = probe(args.video)
        work = args.out / args.video.stem
        work.mkdir(parents=True, exist_ok=True)
        if args.sheet:
            out = work / "fotogramas.jpg"
            times = contact_sheet(info, out)
            print(f"Hoja de fotogramas: {out}")
            for row in range(SHEET_ROWS):
                cells = times[row * SHEET_COLUMNS:(row + 1) * SHEET_COLUMNS]
                print(f"  fila {row + 1}: " + "  ".join(f"{t:6.2f}s" for t in cells))
            return 0
        if args.at is None or not args.text:
            p.error("usa --sheet, o --at y --text")
        emojis: list[str] = []
        for value in args.emoji:  # "risa@18,88" is one sticker; "calavera,risa" are two
            emojis += [value] if "@" in value else [e for e in value.split(",") if e.strip()]
        cover = Cover(
            text=args.text, face=_point(args.face, "--face"), zoom=args.zoom, cam_bottom=args.cam_bottom,
            game=_point(args.game, "--game"), game_top=args.game_top,
            emojis=tuple(emojis)[:3], lines=not args.no_lines,
            red=not args.no_red, arrow=_point(args.arrow, "--arrow"), circle=_point(args.circle, "--circle"),
            y=args.y, color=args.color, highlight=args.highlight, tilt=args.tilt,
        )
        font = find_font(args.font)
        game_frame = grab_frame(info, parse_time(args.game_at)) if args.game_at else None
        image = compose(grab_frame(info, parse_time(args.at)), cover, font.path, game_frame)
        out = work / "miniatura.jpg"
        image.save(out, quality=92)
        print(f"Miniatura: {out}")
        return 0
    except (ConfigError, FontError, MediaError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        print(f"Error: no pude escribir {e.filename}: ¿está abierto en otro programa?", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
