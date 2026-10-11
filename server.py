"""yt-wlt (YouTube Whisper Local Transcribe) — interface web locale : une file de liens YouTube → transcriptions (.txt + .srt).

Ordre par vidéo : sous-titres du créateur (langue parlée) > sous-titres auto YouTube si demandés > Whisper local.
"""

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # python -I n'ajoute pas le dossier du script au sys.path
import core  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_OUT = ROOT / "transcripts"
SETTINGS = ROOT / "settings.json"
PAGE = ROOT / "web" / "index.html"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Sélecteur de dossier natif, lancé dans un processus à part : Tk ne doit pas vivre dans les threads du serveur
FOLDER_DIALOG = (
    "import sys, tkinter, tkinter.filedialog as fd\n"
    "root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)\n"
    "print(fd.askdirectory(parent=root, initialdir=sys.argv[1], title='Dossier de sortie des transcriptions') or '')\n"
)


def clean_error(exc):
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip().removeprefix("ERROR:").strip()
    return (text.splitlines() or [type(exc).__name__])[0][:300]


class Job:
    FIELDS = ("id", "url", "turbo", "auto_subs", "title", "status", "progress", "source", "files", "error", "note")

    def __init__(self, url, turbo, auto_subs, out_dir):
        self.id = uuid.uuid4().hex[:8]
        self.url, self.turbo, self.auto_subs, self.out_dir = url, turbo, auto_subs, out_dir
        self.title = self.source = self.error = self.note = self.progress = None
        self.status = "en attente"
        self.files = []  # noms affichés
        self.paths = []  # chemins servis par /files/<id>/<index>

    def to_dict(self):
        return {f: getattr(self, f) for f in self.FIELDS}


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = []
        self.queue = queue.Queue()
        self.engine = core.WhisperEngine()
        self.auto_mode = True
        self.current = None
        self.stop_flag = threading.Event()
        self.model_error = None
        self._vram, self._vram_at = None, 0.0
        self.out_dir = self._load_out_dir()
        self._dialog_lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    # --- Dossier de sortie ---

    def _load_out_dir(self):
        try:
            return Path(json.loads(SETTINGS.read_text(encoding="utf-8"))["out_dir"])
        except (OSError, ValueError, KeyError):
            return DEFAULT_OUT

    def set_out_dir(self, path):
        path = Path(path).expanduser()
        if not path.is_absolute():
            return "chemin absolu attendu (ex. C:\\Users\\...\\Transcriptions)"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"dossier inutilisable : {exc.strerror or exc}"
        self.out_dir = path
        SETTINGS.write_text(json.dumps({"out_dir": str(path)}, ensure_ascii=False, indent=2), encoding="utf-8")
        return None

    def choose_out_dir(self):
        """Sélecteur natif sur la machine locale. Renvoie (dossier choisi ou None si annulé, erreur)."""
        if not self._dialog_lock.acquire(blocking=False):
            return None, "un sélecteur de dossier est déjà ouvert"
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-c", FOLDER_DIALOG, str(self.out_dir)],
                capture_output=True, text=True, encoding="utf-8", timeout=900,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, "unavailable"
        finally:
            self._dialog_lock.release()
        if result.returncode != 0:
            return None, "unavailable"
        chosen = result.stdout.strip()
        if not chosen:
            return None, None
        error = self.set_out_dir(chosen)
        return (None, error) if error else (str(self.out_dir), None)

    # --- File de travail ---

    def add_jobs(self, lines, turbo, auto_subs, out_dir=None):
        added = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if VIDEO_ID.match(line):
                line = f"https://www.youtube.com/watch?v={line}"
            job = Job(line, turbo, auto_subs, out_dir or self.out_dir)
            if not line.startswith(("http://", "https://")):
                job.status, job.error = "erreur", "lien invalide"
            with self.lock:
                self.jobs.append(job)
            if job.status == "en attente":
                self.queue.put(job)
                added += 1
        return added

    def stop(self):
        with self.lock:
            for job in self.jobs:
                if job.status == "en attente":
                    job.status = "annulé"
        if self.current:
            self.stop_flag.set()

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.status not in ("terminé", "erreur", "annulé")]

    def _worker(self):
        while True:
            job = self.queue.get()
            if job.status != "en attente":
                continue
            self.stop_flag.clear()
            self.current = job
            try:
                self._process(job)
            except Exception as exc:
                job.progress = None
                if self.stop_flag.is_set():
                    job.status = "annulé"
                else:
                    job.status, job.error = "erreur", clean_error(exc)
                    traceback.print_exc()
            finally:
                self.current = None
                if self.auto_mode and self.queue.empty():
                    self.engine.unload()

    def _process(self, job):
        job.status = "analyse"
        info, entries = core.extract_info(job.url)
        if entries is not None:
            job.title = f"Playlist : {len(entries)} vidéos ajoutées à la file"
            self.add_jobs(entries, job.turbo, job.auto_subs, job.out_dir)
            job.status = "terminé"
            return
        job.title = info.get("title")

        segments = None
        choice = core.pick_subtitles(info, job.auto_subs)
        if choice:
            origin, lang, tracks = choice
            job.status = "sous-titres"
            try:
                segments = core.fetch_subtitles(tracks)
                job.source = f"sous-titres {'du créateur' if origin == 'créateur' else 'auto YouTube'} ({lang})"
            except Exception as exc:
                job.note = f"sous-titres inutilisables ({clean_error(exc)}), repli sur Whisper"

        if segments is None:
            try:
                segments, lang, name = self._whisper(job, info)
                job.source = f"Whisper {name} ({lang})"
            except Exception as exc:
                fallback = core.pick_subtitles(info, True)
                if self.stop_flag.is_set() or job.auto_subs or not fallback or fallback[0] != "auto":
                    raise
                job.note = f"Whisper a échoué ({clean_error(exc)}), repli sur les sous-titres auto"
                print(f"[WARN] {job.url} : {job.note}", flush=True)
                segments = core.fetch_subtitles(fallback[2])
                job.source = f"sous-titres auto YouTube ({fallback[1]})"

        job.out_dir.mkdir(parents=True, exist_ok=True)
        header = "\n".join([
            f"Titre : {job.title}",
            f"URL : {info.get('webpage_url') or job.url}",
            f"Source : {job.source}",
        ])
        txt, srt = core.write_outputs(segments, job.out_dir / core.output_stem(info), header)
        job.paths = [txt, srt]
        job.files = [txt.name, srt.name]
        job.status, job.progress = "terminé", None

    def _whisper(self, job, info):
        name = core.MODELS[job.turbo]
        if self.engine.model is None or self.engine.name != name:
            job.status = "chargement du modèle" if core.model_is_cached(name) else "téléchargement du modèle (premier usage)"
        model = self.engine.load(name)

        def progress(p):
            job.progress = p

        with tempfile.TemporaryDirectory() as tmp:
            job.status, job.progress = "téléchargement audio", 0.0
            audio = core.download_audio(
                info, tmp, on_progress=progress, should_stop=self.stop_flag.is_set,
                on_retry=lambda n, total: setattr(job, "status", f"téléchargement audio, essai {n}/{total}"),
            )
            job.status, job.progress = "transcription", 0.0
            segments, lang = core.transcribe_audio(model, audio, on_progress=progress, should_stop=self.stop_flag.is_set)
        return segments, lang, name

    # --- Modèle ---

    def load_model(self, turbo):
        if self.current or self.engine.state == "chargement":
            return "occupé : attends la fin du travail en cours"

        def run():
            self.model_error = None
            try:
                self.engine.load(core.MODELS[turbo])
            except Exception as exc:
                self.model_error = clean_error(exc)
                traceback.print_exc()

        threading.Thread(target=run, daemon=True).start()
        return None

    def unload_model(self):
        if self.current or self.engine.state == "chargement":
            return "occupé : attends la fin du travail en cours"
        self.engine.unload()
        return None

    def set_auto_mode(self, enabled):
        self.auto_mode = enabled
        if enabled and not self.current and self.engine.state == "chargé":
            self.engine.unload()

    # --- État ---

    def vram(self):
        """[utilisée, totale] en Mo via nvidia-smi, rafraîchie toutes les 2 s."""
        if time.monotonic() - self._vram_at > 2:
            self._vram_at = time.monotonic()
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).stdout
                self._vram = [int(v) for v in out.splitlines()[0].split(",")]
            except Exception:
                self._vram = None
        return self._vram

    def job_file(self, job_id, index):
        with self.lock:
            job = next((j for j in self.jobs if j.id == job_id), None)
        if job and index.isdigit() and int(index) < len(job.paths) and job.paths[int(index)].is_file():
            return job.paths[int(index)]
        return None

    def state(self):
        with self.lock:
            jobs = [j.to_dict() for j in self.jobs]
        engine = self.engine
        return {
            "jobs": jobs,
            "busy": self.current is not None,
            "auto_mode": self.auto_mode,
            "out_dir": str(self.out_dir),
            "model": {"state": engine.state, "name": engine.name, "device": engine.device, "error": self.model_error},
            "vram": self.vram(),
        }


app = App()


class Handler(BaseHTTPRequestHandler):
    allowed_origins = set()

    def log_message(self, *args):
        pass

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(app.state())
        elif path.startswith("/files/"):
            # /files/<id du job>/<index> : seuls les fichiers produits par un job sont servis
            parts = unquote(path.removeprefix("/files/")).split("/")
            target = app.job_file(*parts) if len(parts) == 2 else None
            if target:
                self._send(200, target.read_bytes(), "text/plain; charset=utf-8")
            else:
                self._json({"error": "fichier introuvable"}, 404)
        else:
            self._json({"error": "introuvable"}, 404)

    def do_POST(self):
        # Une page web tierce ouverte dans le navigateur ne doit pas pouvoir piloter le serveur
        origin = self.headers.get("Origin")
        if origin and origin not in self.allowed_origins:
            return self._json({"error": "origine refusée"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "JSON attendu"}, 415)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json({"error": "JSON invalide"}, 400)

        path = urlparse(self.path).path
        error = None
        if path == "/api/jobs":
            app.add_jobs(str(data.get("urls", "")).splitlines(), bool(data.get("turbo")), bool(data.get("auto_subs")))
        elif path == "/api/stop":
            app.stop()
        elif path == "/api/clear":
            app.clear_finished()
        elif path == "/api/model/load":
            error = app.load_model(bool(data.get("turbo")))
        elif path == "/api/model/unload":
            error = app.unload_model()
        elif path == "/api/settings":
            if "auto_mode" in data:
                app.set_auto_mode(bool(data["auto_mode"]))
            if "out_dir" in data:
                error = app.set_out_dir(str(data["out_dir"]))
        elif path == "/api/choose-folder":
            chosen, error = app.choose_out_dir()
            if error == "unavailable":
                return self._json({"error": "sélecteur de dossier indisponible", "unavailable": True}, 409)
            if not error:
                return self._json({"ok": True, "out_dir": chosen})  # out_dir None : choix annulé
        elif path == "/api/open-folder":
            app.out_dir.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(app.out_dir)
            else:
                subprocess.Popen(["xdg-open", str(app.out_dir)])
        else:
            return self._json({"error": "introuvable"}, 404)
        self._json({"error": error} if error else {"ok": True}, 409 if error else 200)


class Server(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR laisserait une 2e instance écouter sur le même port
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="yt-wlt : interface web locale de transcription YouTube")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    args = parser.parse_args()
    url = f"http://{HOST}:{args.port}/"
    Handler.allowed_origins = {f"http://{HOST}:{args.port}", f"http://localhost:{args.port}"}

    try:
        server = Server((HOST, args.port), Handler)
    except OSError:
        try:
            urlopen(url + "api/state", timeout=3).read()
        except Exception:
            sys.exit(f"[ERREUR] Le port {args.port} est pris par un autre programme : relance avec --port 8766")
        print(f"[INFO] Déjà lancé : {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    print(f"[OK] yt-wlt : {url}")
    print("[INFO] Fermer cette fenêtre (ou Ctrl+C) arrête le serveur.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
