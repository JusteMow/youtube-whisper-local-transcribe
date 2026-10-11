@echo off
rem Lance l'interface web de transcription (http://127.0.0.1:8765). Fermer la fenetre arrete le serveur.
rem Une instance deja en ecoute sur le port est arretee d'abord (kill_instances.bat).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Installation absente : lance d'abord install.bat
    pause
    exit /b 1
)
set "PORT=8765"
if /i "%~1"=="--port" set "PORT=%~2"
call "%~dp0kill_instances.bat" internal %PORT%
title yt-wlt
".venv\Scripts\python.exe" -I server.py %*
if errorlevel 1 pause
