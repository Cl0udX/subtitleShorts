@echo off
setlocal
rem Arrastra un video sobre este archivo: guarda 3 imagenes con los subtitulos
rem tal como quedarian (sin renderizar el video) y abre la carpeta.
rem Sirve para probar cambios de posicion o de estilo en segundos.
set "ROOT=%~dp0"
set "STYLE=%ROOT%styles\bajo-camara.yaml"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Primero ejecuta instalar.bat
  pause
  exit /b 1
)
if "%~1"=="" goto ayuda

"%PY%" -m subtitleshort "%~1" --style "%STYLE%" --out "%ROOT%output" --preview
if errorlevel 1 goto error
start "" "%ROOT%output\%~n1"
goto fin

:ayuda
echo Arrastra un video sobre este archivo (no se abre con doble clic).
goto fin

:error
echo.
echo [!] Algo fallo: el detalle esta arriba.

:fin
echo.
pause
