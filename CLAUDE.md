# subtitleShort

Local, open-source auto-subtitles for vertical shorts: faster-whisper transcription → VAD-refined word timing → short captions → ASS rendered by libass inside ffmpeg. User-facing text (CLI messages, README, styles, .bat) is Spanish; code and comments are English.

## Run

- Python venv: `.venv/Scripts/python` (Python 3.13; GPU via `nvidia-cublas-cu12`, RTX 5070 Ti).
- Subtitle a video: `.venv/Scripts/python -m subtitleshort videos/X.mp4 -s styles/bajo-camara.yaml` (`--preview` = still frames only, `--skip-done`, `--retranscribe`, `--only-transcribe`).
- Thumbnails: `.venv/Scripts/python -m subtitleshort.thumbnail videos/X.mp4 --sheet | --at S --text "A|*B*" --face X,Y [--emoji ...]`.
- Tests: `.venv/Scripts/python -m pytest -q`.
- Windows helpers: `subtitular.bat` (drag videos / double click = whole `videos/`), `vista-previa.bat`, `instalar.bat`.

## Layout

- `subtitleshort/cli.py`: orchestration per video → `output/<stem>/` (`transcripcion.json`, editable `subtitulos.srt`, `subtitulos.ass`, `<stem>_subtitulado.mp4`).
- `transcribe.py` (faster-whisper, CUDA DLL setup) · `timing.py` (Silero VAD: fixes Whisper's stretched first words, drops hallucinations).
- `captions.py`: aligns the user's edited SRT back to word timestamps (difflib), splits phrases into captions (DP; avoids ending on weak words), retime.
- `ass.py`: layout and events; text measured with HarfBuzz (`fonts.py`), so backgrounds and highlight pills match libass exactly. libass sizes fonts by OS/2 win metrics (`Font.ass_size`).
- `media.py`: ffprobe/ffmpeg. Burning runs ffmpeg in a temp dir with relative paths (avoids Windows filter escaping). x264 CRF 12 slow, audio copied, `-fps_mode passthrough`.
- `thumbnail.py`: Pillow covers (zoomed face panel + gameplay panel, speed lines, Noto emoji stickers in `assets/emoji/`, vignette, tilted title).
- `config.py`: YAML styles in `styles/` with friendly validation.
- `.claude/agents/publicador-shorts.md`: writes `publicacion.txt` + `miniatura.jpg` per video in `output/`.

## User's setup

Gaming shorts 1080x1920 at 60 fps from streams. Facecam on top (to 31% of the height), PostInCloud logo bar (31-37%), gameplay below. Their captions style is `styles/bajo-camara.yaml` (y 38%, anchor top, just under the logo). They publish Tue/Thu/Sat/Sun at 12:00 (Colombia).
