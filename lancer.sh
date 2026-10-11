#!/usr/bin/env bash
# Lance l'interface web de transcription (http://127.0.0.1:8765). Ctrl+C arrête le serveur.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "[ERREUR] Installation absente : lance d'abord ./install.sh"; exit 1; }
exec .venv/bin/python -I server.py "$@"
