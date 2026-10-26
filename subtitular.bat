@echo off
setlocal
rem Arrastra uno o varios videos sobre este archivo. Con doble clic procesa
rem la carpeta videos\ (solo lo nuevo o lo que corregiste en el .srt).
rem Para usar otro estilo, cambia STYLE.
set "ROOT=%~dp0"
set "STYLE=%ROOT%styles\bajo-camara.yaml"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Primero ejecuta instalar.bat
  pause
  exit /b 1
)

if "%~1"=="" goto carpeta
"%PY%" -m subtitleshort %* --style "%STYLE%" --out "%ROOT%output"
goto fin

:carpeta
echo Procesando la carpeta videos\ (lo que ya esta al dia se salta)...
"%PY%" -m subtitleshort "%ROOT%videos" --style "%STYLE%" --out "%ROOT%output" --skip-done

:fin
if errorlevel 1 (
  echo.
  echo [!] Algo fallo: el detalle esta arriba.
)
echo.
pause
