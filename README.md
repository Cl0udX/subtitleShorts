# subtitleShort

Subtítulos automáticos para videos cortos (Shorts, Reels, TikTok). Open source y 100% local: transcribe con Whisper en tu PC, sincroniza palabra por palabra y renderiza el video sin perder calidad. Sin suscripciones y sin subir tu video a ningún servicio.

- **Transcripción local** con [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (GPU NVIDIA o CPU).
- **Buen ritmo**: tiempos por palabra corregidos con detección de voz. El subtítulo aparece cuando hablas, no antes, y se divide en frases cortas que no terminan en "el", "de" o "por".
- **Estilo a tu gusto**: tus propias fuentes (.ttf/.otf), colores, borde, sombra, caja de fondo con esquinas redondeadas y la palabra que estás diciendo resaltada (en color o con una "pastilla").
- **Posición libre** en porcentajes, y puede cambiar durante el video.
- **Calidad**: misma resolución, fps y fotogramas; el audio se copia sin tocarlo.
- **Correcciones fáciles**: editas el texto en un `.srt` y vuelves a renderizar, sin transcribir otra vez.

## Instalación

Necesitas **Python 3.10 a 3.13** y **ffmpeg** (`winget install Gyan.FFmpeg` / `brew install ffmpeg` / `sudo apt install ffmpeg`).

**Windows:** doble clic en `instalar.bat`. Crea el entorno `.venv` e instala todo, incluido el soporte de GPU si tienes una NVIDIA.

**Manual (cualquier sistema):**

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e .                  # con GPU NVIDIA: pip install -e ".[gpu]"
```

La primera transcripción descarga el modelo de Whisper (unos 1.6 GB).

## Uso

**Lo más fácil (Windows):**

- `subtitular.bat`: arrastra uno o varios videos encima. Con doble clic procesa toda la carpeta `videos/`, solo lo que sea nuevo o haya cambiado.
- `vista-previa.bat`: arrastra un video encima y en segundos te muestra 3 imágenes con los subtítulos tal como quedarían, sin renderizar. Úsalo para probar cambios de posición o de estilo.

**Desde la terminal:**

```bash
subtitleshort videos/mi-video.mp4 -s styles/bajo-camara.yaml
subtitleshort videos/ -s styles/bajo-camara.yaml        # todos los videos de una carpeta
```

Todo queda en `output/<nombre-del-video>/`:

| Archivo | Qué es |
| --- | --- |
| `<nombre>_subtitulado.mp4` | el video final |
| `subtitulos.srt` | el texto, para corregirlo |
| `transcripcion.json` | cada palabra con sus tiempos |
| `subtitulos.ass` | los subtítulos con estilo (se pueden abrir en Aegisub) |

### Probar estilo y posición sin renderizar todo

```bash
subtitleshort videos/mi-video.mp4 -s styles/bajo-camara.yaml --preview          # elige 3 momentos con subtítulos
subtitleshort videos/mi-video.mp4 -s styles/bajo-camara.yaml --preview 3,12.5   # o los segundos que digas
```

Guarda en segundos imágenes `preview_*.png` en la carpeta del video.

### Corregir lo que entendió Whisper

1. Abre `output/<video>/subtitulos.srt` y corrige el texto. También puedes borrar bloques enteros. Con `--only-transcribe` obtienes el `.srt` sin renderizar.
2. Vuelve a ejecutar el mismo comando. Usa tu texto, no transcribe otra vez. Las palabras que no tocaste mantienen su tiempo exacto y las corregidas se acomodan solas.

Trucos:

- Escribe `|` donde quieras forzar un corte de subtítulo.
- Si un error se repite en todos tus videos, agrégalo en `replacements` del estilo. Para nombres propios usa `vocabulary`, así Whisper los reconoce desde el principio.
- `--retranscribe` transcribe de nuevo. Tu `.srt` anterior se guarda como `subtitulos.srt.bak`.

## Miniaturas y publicación

Con Claude Code, pide *"prepara la publicación de los videos nuevos"*. El agente `publicador-shorts` (`.claude/agents/`) revisa `output/` y, por cada video que todavía no la tenga, genera:

- `publicacion.txt`: título, descripción, hashtags y fecha sugerida.
- `miniatura.jpg`: miniatura vertical editada, con tu reacción en zoom, líneas de impacto, stickers y texto.

La herramienta de miniaturas también se puede usar a mano:

```bash
python -m subtitleshort.thumbnail videos/mi-video.mp4 --sheet     # 12 fotogramas para elegir el momento
python -m subtitleshort.thumbnail videos/mi-video.mp4 --at 34.1 --text "MI PLAN|*PERFECTO*" --face 32,10 --emoji calavera
```

`*palabra*` la resalta y `|` parte la línea. Con `--help` ves todas las opciones: stickers, flecha, círculo, tomar el juego de otro segundo, etc.

## Estilos

Son archivos YAML en `styles/`:

- `default.yaml`: todas las opciones, explicadas.
- `bajo-camara.yaml`: texto debajo de la cámara y del logo, al 40% desde arriba.
- `caja.yaml`: caja oscura redondeada y una pastilla de color en la palabra activa.

Lo más usado:

```yaml
font: Montserrat-ExtraBold.ttf
size: 80                 # px, como en Photoshop o CapCut (video de 1080x1920)
color: "#FFFFFF"         # colores siempre entre comillas
background:
  enabled: true
  opacity: 0.6
  radius: 20
highlight:
  color: "#FFE81F"
position:
  x: 50                  # % desde la izquierda
  y: 33                  # % desde arriba
  anchor: top            # el borde superior del subtítulo queda en y (top | center | bottom)
```

### Posición que cambia durante el video

```yaml
position: {x: 50, y: 33, anchor: top}
position_changes:
  - from: 12.5           # segundos, o "1:05.2"
    to: 18
    y: 75
  - from: "0:40"         # sin "to": hasta el final
    y: 85
    anchor: bottom
```

Cada cambio se aplica a los subtítulos que empiezan dentro de su rango. Fuera de esos rangos se vuelve a `position`.

### Fuentes

Copia el `.ttf`/`.otf` en `fonts/` y escribe su nombre de archivo en `font:`. También funciona el nombre de una fuente instalada (`font: Impact`). Usa fuentes estáticas (un archivo por grosor), no las "variables". Incluye Montserrat ExtraBold y Black (licencia OFL).

## Calidad del video

Quemar subtítulos obliga a re-codificar el video, así que esto es lo que se hace para no perder calidad:

- Se conservan la resolución, los fps (fotograma a fotograma), el formato de píxel y las etiquetas de color.
- El audio se copia bit a bit, sin re-codificar.
- El video se codifica con x264, CRF 12 y preset slow. En un short de 1080x1920 a 60 fps, midiendo la zona sin subtítulos contra el original, dio PSNR 50.7 dB y VMAF 97.5 de media a 17 Mbps (el original tenía 26 Mbps). Un PSNR por encima de ~45 dB se considera visualmente idéntico.
- Los videos de 10 bits se codifican en HEVC de 10 bits.
- `crf` más bajo da más calidad y un archivo más pesado. `video.encoder: nvenc` usa la GPU NVIDIA: es más rápido, pero algo menos eficiente.

## Cómo funciona

1. ffmpeg extrae el audio y faster-whisper lo transcribe con tiempos por palabra.
2. Silero VAD detecta dónde hay voz y corrige esos tiempos. Whisper suele adelantar la primera palabra de cada frase hasta el silencio anterior, y a veces "escucha" palabras donde no hay voz.
3. Las palabras se agrupan en subtítulos cortos: máximo de palabras, ancho real del texto medido con HarfBuzz, cortes en la puntuación y en las pausas.
4. Se genera un `.ass` con estilo, posición, animación y palabra activa, y ffmpeg lo dibuja con libass.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

## Licencia

MIT. La fuente Montserrat usa la SIL Open Font License (`fonts/OFL-Montserrat.txt`).
