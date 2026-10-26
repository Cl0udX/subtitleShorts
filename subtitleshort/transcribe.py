"""Local speech-to-text with word timestamps (faster-whisper)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from .media import SAMPLE_RATE, load_audio
from .timing import drop_hallucinations, refine_words, speech_regions

_models: dict[tuple[str, str], object] = {}


def transcribe(video: Path, *, model: str, language: Optional[str], device: str = "auto",
               vocabulary: Optional[list[str]] = None, log: Callable[[str], None] = print) -> dict:
    audio = load_audio(video)
    devices = ["cuda", "cpu"] if device == "auto" and _cuda_available() else [device if device != "auto" else "cpu"]
    for dev in devices:
        try:
            return _transcribe(audio, model, language, dev, vocabulary or [], log)
        except Exception as e:  # missing CUDA libraries, out of GPU memory, ...
            if dev == devices[-1]:
                raise
            log(f"      GPU no disponible ({str(e).strip()[:120]}); sigo con CPU")
    raise AssertionError("unreachable")


def _transcribe(audio, model_name: str, language: Optional[str], device: str,
                vocabulary: list[str], log: Callable[[str], None]) -> dict:
    model = _load_model(model_name, device)
    segments, info = model.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=True,  # skips silences/music, the main source of made-up text
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
        hotwords=", ".join(vocabulary) or None,
    )
    log(f"      idioma: {info.language} · dispositivo: {'GPU' if device == 'cuda' else 'CPU'}")
    result = []
    for seg in segments:
        words = [
            {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
             "probability": round(w.probability, 3)}
            for w in seg.words or [] if w.word.strip()
        ]
        if not words:
            continue
        result.append({"start": words[0]["start"], "end": words[-1]["end"], "text": seg.text.strip(), "words": words})
        log(f"      [{_clock(seg.start)} → {_clock(seg.end)}] {seg.text.strip()}")
    regions = speech_regions(audio)
    kept = drop_hallucinations(result, regions)
    if len(kept) < len(result):
        log(f"      descartados {len(result) - len(kept)} segmento(s) sin voz real (alucinaciones de Whisper)")
    refine_words(kept, regions)
    result = kept
    return {
        "model": model_name,
        "language": info.language,
        "device": device,
        "duration": round(len(audio) / SAMPLE_RATE, 3),
        "timing": "whisper+vad",
        "segments": result,
    }


def _load_model(name: str, device: str):
    key = (name, device)
    if key not in _models:
        if device == "cuda":
            _add_pip_cuda_libraries()
        from faster_whisper import WhisperModel

        _models[key] = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
    return _models[key]


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _add_pip_cuda_libraries() -> None:
    """Make CUDA libraries installed with pip (nvidia-cublas-cu12, ...) loadable."""
    try:
        import nvidia  # namespace package shared by the nvidia-* wheels
    except ImportError:
        return
    for base in getattr(nvidia, "__path__", []):
        for package in Path(base).iterdir():
            libdir = package / ("bin" if sys.platform == "win32" else "lib")
            if not libdir.is_dir():
                continue
            if sys.platform == "win32":
                os.add_dll_directory(str(libdir))
                os.environ["PATH"] = str(libdir) + os.pathsep + os.environ.get("PATH", "")
            else:
                import ctypes

                for lib in sorted(libdir.glob("lib*.so*")):
                    try:
                        ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"
