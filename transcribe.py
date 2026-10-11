"""Transcription locale en ligne de commande d'une vidéo (ou d'un fichier audio) avec Whisper.

L'interface web (server.py) ajoute la file de liens, les sous-titres du créateur et la gestion du modèle.
"""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # python -I n'ajoute pas le dossier du script au sys.path
import core  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="URL de la vidéo ou chemin d'un fichier audio/vidéo local")
    parser.add_argument("-m", "--model", default="large-v3", help="modèle faster-whisper (large-v3, large-v3-turbo, medium...)")
    parser.add_argument("-l", "--language", default=None, help="code langue (fr, en...) ; détection auto si absent")
    parser.add_argument("-o", "--out", type=Path, default=ROOT / "transcripts", help="dossier de sortie")
    parser.add_argument("--device", choices=["cuda", "cpu"], default=None, help="GPU si disponible par défaut")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        if Path(args.source).is_file():
            audio = Path(args.source)
            title, url, stem = audio.stem, None, audio.stem
        else:
            info, entries = core.extract_info(args.source)
            if entries is not None:
                sys.exit("[ERREUR] Playlist : passe les vidéos une par une, ou utilise l'interface web (lancer.bat)")
            title, url, stem = info.get("title"), info.get("webpage_url") or args.source, info["id"]
            print(f"[DOWNLOAD] {title}")
            audio = core.download_audio(info, tmp)

        engine = core.WhisperEngine()
        model = engine.load(args.model, args.device)
        print(f"[MODELE] {args.model} sur {engine.device}")
        segments, language = core.transcribe_audio(
            model, audio, args.language,
            on_progress=lambda p: print(f"\r[TRANSCRIPTION] {p:6.1%}", end="", flush=True),
        )
        print(f"\n[LANGUE] {language}")

    header = "\n".join(filter(None, [
        f"Titre : {title}",
        f"URL : {url}" if url else None,
        f"Transcription : faster-whisper {args.model}, langue {language}",
    ]))
    txt, srt = core.write_outputs(segments, args.out / stem, header)
    print(f"[OK] {txt}\n[OK] {srt}")


if __name__ == "__main__":
    main()
