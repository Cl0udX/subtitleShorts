"""ffprobe / ffmpeg helpers: probing, audio decoding and burning subtitles."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .fonts import Font, copy_font

SAMPLE_RATE = 16000
# Audio codecs that can be copied into .mp4 untouched (no quality loss).
MP4_AUDIO = {"aac", "mp3", "opus", "ac3", "eac3", "alac", "flac"}
# Chroma layouts x264 can encode as-is; anything else is converted to 4:2:0.
X264_PIX_FMTS = {"yuv420p", "yuv422p", "yuv444p"}
X265_PIX_FMTS = {"yuv420p10le", "yuv422p10le", "yuv444p10le"}


class MediaError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    path: Path
    width: int  # as displayed (rotation applied)
    height: int
    duration: float
    fps: float
    codec: str
    pix_fmt: str
    bit_rate: Optional[int]
    color_space: Optional[str]
    color_transfer: Optional[str]
    color_primaries: Optional[str]
    color_range: Optional[str]
    audio_codecs: list[str]

    @property
    def ten_bit(self) -> bool:
        return any(depth in self.pix_fmt for depth in ("p10", "p12"))


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(
                f"No encontré '{tool}'. Instálalo (Windows: winget install Gyan.FFmpeg; "
                "macOS: brew install ffmpeg; Linux: apt install ffmpeg) y abre una terminal nueva."
            )


def probe(path: Path) -> VideoInfo:
    if not path.is_file():
        raise MediaError(f"No existe el archivo {path}")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise MediaError(f"No pude leer el video ({result.stderr.strip() or 'archivo dañado o incompleto'})")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise MediaError("El archivo no tiene pista de video")
    v = videos[0]
    width, height = int(v["width"]), int(v["height"])
    rotation = 0
    for side in v.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if "rotate" in v.get("tags", {}):
        rotation = int(v["tags"]["rotate"])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    bit_rate = v.get("bit_rate") or data.get("format", {}).get("bit_rate")
    return VideoInfo(
        path=path.resolve(),
        width=width,
        height=height,
        duration=float(data.get("format", {}).get("duration") or v.get("duration") or 0),
        fps=_ratio(v.get("avg_frame_rate")) or _ratio(v.get("r_frame_rate")) or 30.0,
        codec=v.get("codec_name", "?"),
        pix_fmt=v.get("pix_fmt", "yuv420p"),
        bit_rate=int(bit_rate) if bit_rate else None,
        color_space=v.get("color_space"),
        color_transfer=v.get("color_transfer"),
        color_primaries=v.get("color_primaries"),
        color_range=v.get("color_range"),
        audio_codecs=[s.get("codec_name", "?") for s in streams if s.get("codec_type") == "audio"],
    )


def load_audio(path: Path) -> np.ndarray:
    """Decode the audio track as 16 kHz mono float32, the input Whisper expects."""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise MediaError(f"No pude extraer el audio: {result.stderr.decode(errors='replace').strip()}")
    audio = np.frombuffer(result.stdout, np.float32)
    if audio.size == 0:
        raise MediaError("El video no tiene audio")
    return audio


def encoder_args(info: VideoInfo, encoder: str, crf: float, preset: str) -> list[str]:
    """Video encoding settings that keep the original quality, size, fps and colors."""
    if encoder == "nvenc":
        # NVENC's cq scale is shifted: cq 19 measured the same VMAF as x264 CRF 12,
        # so one "crf" setting gives the same quality with either encoder.
        cq = min(51.0, crf + 7)
        args = ["-c:v", "hevc_nvenc" if info.ten_bit else "h264_nvenc", "-preset", "p7", "-tune", "hq",
                "-rc", "vbr", "-cq", f"{cq:g}", "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1",
                "-pix_fmt", "p010le" if info.ten_bit else "yuv420p"]
    elif info.ten_bit:
        pix_fmt = info.pix_fmt if info.pix_fmt in X265_PIX_FMTS else "yuv420p10le"
        args = ["-c:v", "libx265", "-preset", preset, "-crf", f"{crf:g}", "-pix_fmt", pix_fmt, "-tag:v", "hvc1"]
    else:
        pix_fmt = info.pix_fmt if info.pix_fmt in X264_PIX_FMTS else "yuv420p"
        args = ["-c:v", "libx264", "-preset", preset, "-crf", f"{crf:g}", "-pix_fmt", pix_fmt]
    for option, value in (("-color_primaries", info.color_primaries), ("-color_trc", info.color_transfer),
                          ("-colorspace", info.color_space), ("-color_range", info.color_range)):
        if value and value not in ("unknown", "unspecified", "reserved"):
            args += [option, value]
    return args


def burn_subtitles(info: VideoInfo, ass_file: Path, font: Font, output: Path, *, encoder: str = "x264",
                   crf: float = 12, preset: str = "slow",
                   on_progress: Optional[Callable[[float], None]] = None) -> Path:
    """Render and return where the video was saved: `output`, or a numbered copy
    when `output` is open in a player (Windows doesn't let us overwrite it)."""
    audio = ["-c:a", "copy"] if all(c in MP4_AUDIO for c in info.audio_codecs) else ["-c:a", "aac", "-b:a", "320k"]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.part{output.suffix}")
    with _render_dir(ass_file, font) as cwd:
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(info.path), "-map", "0:v:0", "-map", "0:a?",
               "-vf", "ass=subs.ass:fontsdir=fonts", "-fps_mode", "passthrough",
               *encoder_args(info, encoder, crf, preset), *audio,
               "-map_metadata", "0", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", str(partial.resolve())]
        try:
            _run_with_progress(cmd, cwd, info.duration, on_progress)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    for n in range(1, 100):
        target = output if n == 1 else output.with_name(f"{output.stem} ({n}){output.suffix}")
        try:
            os.replace(partial, target)
            return target
        except PermissionError:
            continue
    raise MediaError(f"No pude guardar el video: cierra {output.name} en el reproductor y vuelve a intentarlo")


def render_frames(info: VideoInfo, ass_file: Path, font: Font, times: list[float], out_dir: Path) -> list[Path]:
    """Still frames with subtitles, to check style and position quickly."""
    outputs = []
    with _render_dir(ass_file, font) as cwd:
        for t in times:
            out = (out_dir / f"preview_{t:06.2f}s.png").resolve()
            # -copyts keeps the original timestamps so libass draws the subtitle of second `t`.
            cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-copyts", "-i", str(info.path),
                   "-vf", "ass=subs.ass:fontsdir=fonts", "-frames:v", "1", "-update", "1", str(out)]
            result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise MediaError(f"ffmpeg falló al generar la vista previa:\n{result.stderr.strip()}")
            outputs.append(out)
    return outputs


class _render_dir:
    """Temp folder holding the .ass and its font, so ffmpeg's filter gets plain
    relative paths (Windows paths need painful escaping inside -vf)."""

    def __init__(self, ass_file: Path, font: Font):
        self.ass_file, self.font = ass_file, font

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="subtitleshort-")
        cwd = Path(self._tmp.name)
        shutil.copy2(self.ass_file, cwd / "subs.ass")
        copy_font(self.font, cwd / "fonts")
        return cwd

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


def _run_with_progress(cmd: list[str], cwd: Path, duration: float,
                       on_progress: Optional[Callable[[float], None]]) -> None:
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=log,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit() and duration > 0 and on_progress:
                on_progress(min(1.0, int(value) / 1e6 / duration))
        if proc.wait() != 0:
            log.seek(0)
            raise MediaError("ffmpeg falló:\n" + log.read()[-3000:].strip())
    if on_progress:
        on_progress(1.0)


def _ratio(value: Optional[str]) -> float:
    try:
        num, _, den = (value or "").partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
