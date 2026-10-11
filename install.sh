#!/usr/bin/env bash
# Installe (ou met à jour) l'environnement de l'interface de transcription. Relancer = mettre à jour yt-dlp.
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Installation : yt-wlt (YouTube Whisper Local Transcribe) ==="

if ! command -v uv >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || { echo "[ERREUR] curl est requis pour installer uv (ex. : sudo apt install curl)"; exit 1; }
    echo "uv introuvable : installation via le script officiel https://astral.sh/uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "[1/3] Environnement Python 3.11 (.venv)"
uv venv --python 3.11 --allow-existing .venv

echo "[2/3] Dépendances : faster-whisper, yt-dlp, deno, bibliothèques CUDA (~2 Go au premier passage)"
uv pip install --python .venv/bin/python -U -r requirements.txt

echo "[3/3] Vérification"
.venv/bin/python -I -c "import sys; sys.path.insert(0, '.'); import core; print('GPU NVIDIA détecté' if core.cuda_available() else 'Pas de GPU NVIDIA : Whisper tournera sur CPU (lent)')"
chmod +x lancer.sh

echo
echo "Installation terminée. Lance l'interface avec ./lancer.sh"
echo "(le modèle Whisper, ~3 Go, se télécharge au premier usage)"
