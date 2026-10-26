@echo off
setlocal
cd /d "%~dp0"
echo == subtitleShort: instalacion ==
echo.

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] No encontre ffmpeg. Instalalo con:  winget install Gyan.FFmpeg
  echo     y vuelve a ejecutar este archivo en una ventana nueva.
  pause
  exit /b 1
)

rem Whisper (CTranslate2/onnxruntime) publica paquetes para Python 3.10 a 3.13.
set "PY="
for %%v in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>nul && set "PY=py -%%v"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [!] Necesitas Python 3.10, 3.11, 3.12 o 3.13: https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando el entorno virtual con %PY%...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [!] No pude crear el entorno virtual.
    pause
    exit /b 1
  )
)

set "EXTRA="
where nvidia-smi >nul 2>nul && set "EXTRA=[gpu]"
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e ".%EXTRA%"
if errorlevel 1 (
  echo [!] Fallo la instalacion de dependencias.
  pause
  exit /b 1
)

echo.
echo Listo. Arrastra uno o varios videos sobre subtitular.bat
echo (la primera vez se descarga el modelo de Whisper, unos 1.6 GB).
pause
