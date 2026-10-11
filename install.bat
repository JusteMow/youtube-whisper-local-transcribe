@echo off
rem Installe (ou met a jour) l'environnement de l'interface de transcription. Relancer = mettre a jour yt-dlp.
setlocal
cd /d "%~dp0"
echo === Installation : yt-wlt (YouTube Whisper Local Transcribe) ===

where uv >nul 2>nul
if errorlevel 1 (
    echo uv introuvable : installation via le script officiel https://astral.sh/uv
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 goto erreur
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo [1/3] Environnement Python 3.11 (.venv)
uv venv --python 3.11 --allow-existing .venv
if errorlevel 1 goto erreur

echo [2/3] Dependances : faster-whisper, yt-dlp, deno, bibliotheques CUDA (~2 Go au premier passage)
uv pip install --python .venv\Scripts\python.exe -U -r requirements.txt
if errorlevel 1 goto erreur

echo [3/3] Verification
.venv\Scripts\python.exe -I -c "import sys; sys.path.insert(0, '.'); import core; print('GPU NVIDIA detecte' if core.cuda_available() else 'Pas de GPU NVIDIA : Whisper tournera sur CPU (lent)')"
if errorlevel 1 goto erreur

echo.
echo Installation terminee. Lance l'interface avec lancer.bat
echo (le modele Whisper, ~3 Go, se telecharge au premier usage)
pause
exit /b 0

:erreur
echo.
echo [ERREUR] L'installation a echoue : voir le message ci-dessus.
pause
exit /b 1
