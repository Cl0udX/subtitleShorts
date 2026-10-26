"""Command line: subtitleshort video.mp4 [--style estilo.yaml]."""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from . import __version__
from .ass import AssBuilder
from .captions import align_cues, build_captions, retime, transcript_to_cues, transcript_words
from .config import ConfigError, Style, load_style, parse_time
from .fonts import Font, FontError, TextMeasurer, find_font
from .media import MediaError, burn_subtitles, probe, render_frames, require_ffmpeg
from .srt import read_srt, write_srt
from .transcribe import transcribe

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
OUTPUT_SUFFIX = "_subtitulado"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    try:
        require_ffmpeg()
        style = load_style(args.style)
        _apply_overrides(style, args)
        font_dirs = [args.style.parent, args.style.parent.parent / "fonts"] if args.style else []
        font = find_font(style.font, font_dirs)
        measurer = TextMeasurer(font)
        videos = _collect(args.inputs)
    except (ConfigError, FontError, MediaError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if not videos:
        print("No encontré videos para procesar.", file=sys.stderr)
        return 2

    failures, done = 0, Counter()
    for video in videos:
        try:
            done[_process(video, args, style, font, measurer)] += 1
        except (ConfigError, FontError, MediaError, ValueError) as e:
            failures += 1
            print(f"   Error: {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nCancelado.", file=sys.stderr)
            return 130
    if len(videos) > 1:
        parts = [f"{n} {status}" for status, n in done.items()] + ([f"{failures} con error"] if failures else [])
        print(f"\nResumen: {', '.join(parts)}")
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subtitleshort",
        description="Subtítulos automáticos para shorts: transcribe con Whisper (local) y renderiza con ffmpeg.",
    )
    p.add_argument("inputs", nargs="+", type=Path, help="videos o carpetas con videos")
    p.add_argument("-s", "--style", type=Path, help="archivo de estilo .yaml (ver carpeta styles/)")
    p.add_argument("-o", "--out", type=Path, default=Path("output"), help="carpeta de salida (por defecto: output)")
    p.add_argument("--preview", metavar="SEGUNDOS", nargs="?", const="auto",
                   help="en vez de renderizar, guarda imágenes de prueba: solo --preview elige 3 momentos con "
                        "subtítulos; también puedes decir cuáles (ej. --preview 3,10.5,20)")
    p.add_argument("--only-transcribe", action="store_true",
                   help="solo transcribe y genera el .srt, para corregirlo antes de renderizar")
    p.add_argument("--retranscribe", action="store_true",
                   help="vuelve a transcribir aunque ya exista (el .srt anterior se guarda como .srt.bak)")
    p.add_argument("--skip-done", action="store_true",
                   help="salta los videos ya renderizados que no cambiaron (ni el video, ni el .srt, ni el estilo)")
    p.add_argument("--model", help="modelo Whisper: tiny, base, small, medium, large-v3, large-v3-turbo")
    p.add_argument("--language", help="idioma del audio, ej. es, en (por defecto: autodetectar)")
    p.add_argument("--device", choices=["auto", "cuda", "cpu"], help="dónde corre Whisper (por defecto: auto)")
    p.add_argument("--encoder", choices=["x264", "nvenc"], help="x264 (máxima calidad) o nvenc (GPU NVIDIA, más rápido)")
    p.add_argument("--crf", type=float, help="calidad del video, menor = mejor (por defecto 12, visualmente idéntico al original)")
    p.add_argument("--preset", help="preset de x264 (por defecto slow)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _apply_overrides(style: Style, args: argparse.Namespace) -> None:
    for attr, target, key in (("model", style.transcription, "model"), ("language", style.transcription, "language"),
                              ("device", style.transcription, "device"), ("encoder", style.video, "encoder"),
                              ("crf", style.video, "crf"), ("preset", style.video, "preset")):
        if getattr(args, attr) is not None:
            setattr(target, key, getattr(args, attr))


def _collect(inputs: list[Path]) -> list[Path]:
    videos = []
    for path in inputs:
        if path.is_dir():
            videos += sorted(f for f in path.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS
                             and not f.stem.endswith(OUTPUT_SUFFIX) and f.stat().st_size > 0)
        else:
            videos.append(path)
    return videos


def _preview_times(captions: list) -> list[float]:
    """Three moments spread over the video, each one showing a subtitle."""
    if not captions:
        return [1.0]
    picks = sorted({min(len(captions) - 1, int(len(captions) * f)) for f in (0.15, 0.5, 0.85)})
    return [captions[i].start + min(0.3, (captions[i].end - captions[i].start) / 2) for i in picks]


def _process(video: Path, args: argparse.Namespace, style: Style, font: Font, measurer: TextMeasurer) -> str:
    started = time.time()
    print(f"\n==> {video.name}")
    info = probe(video)
    print(f"   {info.width}x{info.height} · {info.fps:.3g} fps · {info.duration:.1f} s · {info.codec}")
    work = args.out / video.stem
    work.mkdir(parents=True, exist_ok=True)
    transcript_path, srt_path = work / "transcripcion.json", work / "subtitulos.srt"

    if args.retranscribe or not transcript_path.exists():
        tr = style.transcription
        print(f"   [1/3] Transcribiendo con Whisper ({tr.model})...")
        transcript = transcribe(video, model=tr.model, language=tr.language, device=tr.device,
                                vocabulary=tr.vocabulary)
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
        if srt_path.exists():
            srt_path.replace(srt_path.with_suffix(".srt.bak"))
        write_srt(srt_path, transcript_to_cues(transcript))
    else:
        print("   [1/3] Usando la transcripción guardada (--retranscribe para rehacerla)")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        if not srt_path.exists():
            write_srt(srt_path, transcript_to_cues(transcript))

    if args.only_transcribe:
        print(f"   Revisa y corrige el texto en: {srt_path}\n   Luego vuelve a ejecutar el mismo comando sin --only-transcribe.")
        return "transcrito(s)"

    output = work / f"{video.stem}{OUTPUT_SUFFIX}.mp4"
    if args.skip_done and not args.preview and output.exists():
        sources = [video, srt_path] + ([args.style] if args.style else [])
        if output.stat().st_mtime > max(p.stat().st_mtime for p in sources):
            print(f"   Ya está al día (no cambió nada desde el último render): {output}")
            return "ya al día"

    builder = AssBuilder(style, info, font, measurer)
    cue_words = align_cues(read_srt(srt_path), transcript_words(transcript), style.replacements)
    captions = build_captions(cue_words, display=builder.display, fits=builder.fits, max_words=style.max_words)
    retime(captions, duration=info.duration)
    ass_path = work / "subtitulos.ass"
    ass_path.write_text(builder.build(captions), encoding="utf-8")
    print(f"   [2/3] {len(captions)} subtítulos · fuente {font.name}")

    if args.preview:
        if args.preview == "auto":
            times = _preview_times(captions)
        else:
            times = [parse_time(t) for t in args.preview.split(",") if t.strip()]
        for old in work.glob("preview_*.png"):
            old.unlink()
        for image in render_frames(info, ass_path, font, times, work):
            print(f"   Vista previa: {image}")
        return "con vista previa"

    v = style.video
    label = f"{v.encoder}, crf {v.crf:g}" + (f", {v.preset}" if v.encoder == "x264" else "")
    saved = burn_subtitles(info, ass_path, font, output, encoder=v.encoder, crf=v.crf, preset=v.preset,
                           on_progress=lambda f: print(f"\r   [3/3] Renderizando ({label})... {f:4.0%}", end="",
                                                       flush=True))
    print(f"\n   Listo en {time.time() - started:.0f} s: {saved}")
    if saved != output:
        print(f"   Ojo: {output.name} está abierto en otro programa (reproductor o VS Code) y Windows no deja "
              f"reemplazarlo, así que guardé el nuevo como {saved.name}.")
    return "renderizado(s)"
