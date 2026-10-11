# youtube-whisper-local-transcribe (yt-wlt)

Transcription 100 % locale de vidéos YouTube, dans une petite page web : on colle une liste de liens, on récupère un `.txt` (paragraphes) et un `.srt` (horodaté) par vidéo.

Pour chaque vidéo, dans cet ordre :
1. **Sous-titres du créateur** dans la langue parlée, s'il y en a (instantané, fiables).
2. **Sous-titres auto YouTube**, seulement si la case est cochée (instantané, mais peu fiables et sans ponctuation).
3. **Whisper** en local (faster-whisper large-v3 sur GPU NVIDIA) : quelques minutes par vidéo, texte ponctué et fidèle.

Si Whisper échoue, les sous-titres auto servent de repli quand ils existent.

## Installation

| | Windows | Linux |
|---|---|---|
| Installer / mettre à jour | `install.bat` | `./install.sh` |
| Lancer l'interface | `lancer.bat` | `./lancer.sh` |

L'installeur installe [uv](https://docs.astral.sh/uv/) s'il manque, crée `.venv` (Python 3.11) et installe faster-whisper, yt-dlp, son runtime JavaScript (deno, embarqué) et les bibliothèques CUDA. Ni Node.js ni ffmpeg ne sont requis. **Relancer l'installeur met yt-dlp à jour** : c'est le geste à faire quand YouTube se met à renvoyer des erreurs 403.

Sans GPU NVIDIA, Whisper tourne sur CPU (lent). Le modèle se télécharge au premier usage dans le cache Hugging Face (~3 Go pour large-v3, ~1,6 Go pour turbo).

## L'interface (http://127.0.0.1:8765)

- **Vidéos** : un lien par ligne (vidéo ou playlist, une playlist est développée en ses vidéos), ou un ID de vidéo seul.
- **Turbo** : large-v3-turbo, environ 2× plus rapide, un peu moins précis.
- **Sous-titres auto YouTube** : les accepter au lieu de lancer Whisper.
- **Dossier de sortie** : *Choisir…* ouvre le sélecteur de dossiers du système ; le choix est mémorisé dans `settings.json` et vaut pour les vidéos ajoutées ensuite (sans sélecteur disponible, la page demande le chemin).
- **Mode auto** (par défaut) : le modèle est chargé au début du lot et déchargé à la fin. Décoché : boutons *Charger* / *Décharger*, le modèle reste en mémoire entre les lots.
- **File** : avancement par vidéo, source utilisée, liens *Texte* / *Sous-titres .srt*, *Copier le texte*, *Ouvrir le dossier*, *Arrêter*.

Le serveur n'écoute que sur la machine locale. Fermer la fenêtre de `lancer.bat` (ou Ctrl+C) l'arrête ; le relancer alors qu'il tourne déjà arrête l'ancienne instance (travail en cours compris) et en démarre une neuve. `kill_instances.bat` fait ce nettoyage seul, sans relance. Autre port : `lancer.bat --port 8766`.

Sorties dans le dossier choisi (par défaut `transcripts\`) : `<titre> [<id>].txt` et `.srt`.

## Ligne de commande

Whisper seul, sans file ni sous-titres (pratique pour l'appeler depuis un autre script) :

```bat
transcribe.bat <URL>                 :: sortie transcripts\<id>.txt et .srt
transcribe.bat <URL> -l fr           :: force la langue (sinon détection auto)
transcribe.bat <URL> -m large-v3-turbo
transcribe.bat C:\chemin\audio.m4a   :: fichier local
```

## Fichiers

- `core.py` : moteur partagé (yt-dlp, sous-titres, modèle Whisper, écriture des sorties).
- `server.py` + `web/index.html` : interface web (bibliothèque standard Python, aucune dépendance front).
- `transcribe.py` : ligne de commande.

## Pièges

- **HTTP 403 au téléchargement** : YouTube refuse parfois un flux au hasard ; l'outil retente jusqu'à 3 fois avec une nouvelle extraction (statut « essai 2/3 »). Si ça échoue systématiquement, yt-dlp est trop vieux → relancer l'installeur.
- **Vidéo doublée par YouTube** (pistes audio multiples) : la piste originale est transcrite, et seuls les sous-titres auto de la langue parlée sont acceptés (pas ceux du doublage).
- **DLL CUDA introuvable** (`cublas64_12.dll`) : wheels `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` absentes du venv → relancer l'installeur.
- **Décharger le modèle** rend l'essentiel de la VRAM ; quelques centaines de Mo (contexte CUDA) restent pris tant que le serveur tourne.

## Licence et crédits

Code sous licence [MIT](LICENSE). S'appuie sur [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT), les modèles [Whisper](https://github.com/openai/whisper) d'OpenAI (MIT) et [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense), installés par l'installeur et non redistribués ici.

Respecte les conditions d'utilisation de YouTube et les droits des créateurs : les transcriptions produites sont pour un usage personnel.
