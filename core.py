"""Moteur partagé : yt-dlp (infos, sous-titres, audio) et faster-whisper (chargement, transcription)."""

import ctypes
import gc
import glob
import json
import os
import re
import sysconfig
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

MODELS = {False: "large-v3", True: "large-v3-turbo"}  # clé : mode turbo
PARAGRAPH_GAP = 2.0  # pause (s) qui ouvre un nouveau paragraphe dans le .txt
PARAGRAPH_WORDS = 120  # au-delà, le paragraphe se ferme à la prochaine fin de phrase
PARAGRAPH_MAX_WORDS = 250  # sous-titres sans ponctuation : coupure forcée


class Cancelled(Exception):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str


# --- Environnement -----------------------------------------------------------

_cuda_exposed = False


def expose_cuda_libs():
    """Rend visibles les bibliothèques cuBLAS/cuDNN des wheels nvidia-* du venv (une fois par processus)."""
    global _cuda_exposed
    if _cuda_exposed:
        return
    _cuda_exposed = True
    nvidia = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    if os.name == "nt":
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            bin_dir = nvidia / sub / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        return
    # Linux : LD_LIBRARY_PATH n'est lu qu'au démarrage, on précharge donc les .so par chemin absolu.
    # Plusieurs passes : une bibliothèque échoue tant que ses dépendances ne sont pas chargées.
    pending = sorted(glob.glob(str(nvidia / "cublas" / "lib" / "lib*.so*")) + glob.glob(str(nvidia / "cudnn" / "lib" / "lib*.so*")))
    for _ in range(3):
        failed = []
        for so in pending:
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                failed.append(so)
        if not failed or failed == pending:
            break
        pending = failed


def js_runtimes():
    """Runtime JS exigé par yt-dlp pour YouTube : deno du venv (extra yt-dlp[deno]), sinon node du système."""
    deno = Path(sysconfig.get_paths()["scripts"]) / ("deno.exe" if os.name == "nt" else "deno")
    if deno.is_file():
        return {"deno": {"path": str(deno)}}
    return {"node": {"path": None}}


def cuda_available():
    expose_cuda_libs()
    import ctranslate2
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def model_is_cached(name):
    expose_cuda_libs()
    from faster_whisper.utils import download_model
    try:
        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False


# --- Modèle Whisper ----------------------------------------------------------

class WhisperEngine:
    """Garde au plus un modèle en mémoire ; chargement et déchargement explicites."""

    def __init__(self):
        self._lock = threading.Lock()
        self.model = None
        self.name = None
        self.device = None
        self.state = "déchargé"  # déchargé | chargement | chargé

    def load(self, name, device=None):
        with self._lock:
            if self.model is not None and self.name == name:
                return self.model
            self._release()
            self.state, self.name = "chargement", name
            try:
                expose_cuda_libs()
                from faster_whisper import WhisperModel
                device = device or ("cuda" if cuda_available() else "cpu")
                self.model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
                self.device, self.state = device, "chargé"
            except Exception:
                self._release()
                raise
            return self.model

    def unload(self):
        with self._lock:
            self._release()

    def _release(self):
        self.model = self.name = self.device = None
        self.state = "déchargé"
        gc.collect()


def transcribe_audio(model, audio, language=None, on_progress=None, should_stop=None):
    segments, info = model.transcribe(str(audio), language=language, beam_size=5, vad_filter=True)
    collected = []
    for seg in segments:
        if should_stop and should_stop():
            raise Cancelled()
        collected.append(Segment(seg.start, seg.end, seg.text.strip()))
        if on_progress and info.duration:
            on_progress(min(seg.end / info.duration, 1.0))
    return collected, info.language


# --- yt-dlp : infos, sous-titres, audio -----------------------------------------

def ydl_options(**extra):
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "js_runtimes": js_runtimes(),
        **extra,
    }


def extract_info(url):
    """Infos de la vidéo, ou liste d'URL si c'est une playlist."""
    with yt_dlp.YoutubeDL(ydl_options(extract_flat="in_playlist")) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        return None, [e.get("url") or e.get("webpage_url") for e in info.get("entries") or [] if e]
    return info, None


def pick_subtitles(info, accept_auto):
    """Sous-titres du créateur dans la langue parlée en priorité ; piste auto d'origine si accept_auto.

    Une piste dans une autre langue est une traduction (ou la transcription d'un doublage auto
    YouTube, qui a aussi sa piste « -orig ») : ignorée. Renvoie (origine, langue, pistes) ou None.
    """
    spoken = (info.get("language") or "").lower()
    wanted = [spoken.split("-")[0]] if spoken else ["fr", "en"]

    def same_lang(key, lang):
        return key.lower().removesuffix("-orig").split("-")[0] == lang

    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    for lang in wanted:
        for key, tracks in manual.items():
            if same_lang(key, lang):
                return "créateur", key, tracks
    if accept_auto:
        autos = {k: v for k, v in (info.get("automatic_captions") or {}).items() if k.endswith("-orig")}
        for lang in wanted:
            for key, tracks in autos.items():
                if same_lang(key, lang):
                    return "auto", key.removesuffix("-orig"), tracks
    return None


def fetch_subtitles(tracks):
    """Télécharge une piste (json3 de préférence, sinon vtt/srt) et la convertit en segments."""
    by_ext = {t.get("ext"): t for t in tracks}
    track = by_ext.get("json3") or by_ext.get("vtt") or by_ext.get("srt")
    if track is None:
        raise ValueError(f"aucun format de sous-titres exploitable ({', '.join(by_ext)})")
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:
        raw = ydl.urlopen(track["url"]).read().decode("utf-8", errors="replace")
    segments = _parse_json3(raw) if track["ext"] == "json3" else _parse_cues(raw)
    if not segments:
        raise ValueError("piste de sous-titres vide")
    return segments


def _parse_json3(raw):
    segments = []
    for event in json.loads(raw).get("events", []):
        text = "".join(s.get("utf8", "") for s in event.get("segs", [])).replace("\n", " ").strip()
        if text:
            start = event.get("tStartMs", 0) / 1000
            segments.append(Segment(start, start + event.get("dDurationMs", 0) / 1000, text))
    return segments


def _cue_time(value):
    h, m, s = ([0, 0] + value.replace(",", ".").split(":"))[-3:]
    return int(h) * 3600 + int(m) * 60 + float(s)


def _parse_cues(raw):
    """VTT ou SRT. Les sous-titres auto « défilants » répètent la ligne précédente : dédoublonnée."""
    segments, last = [], None
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, end = (_cue_time(t.strip().split(" ")[0]) for t in lines[timing].split("-->"))
        for line in lines[timing + 1:]:
            text = re.sub(r"<[^>]+>", "", line).strip()
            if text and text != last:
                segments.append(Segment(start, end, text))
                last = text
    return segments


def download_audio(info, dest_dir, on_progress=None, should_stop=None, on_retry=None, attempts=3):
    """Télécharge la piste audio seule (pas de ffmpeg requis) et renvoie son chemin.

    YouTube refuse parfois un flux au hasard (403, environ 1 essai sur 3 observé) : chaque nouvel
    essai ré-extrait la vidéo, donc obtient de nouvelles URL.
    """
    def hook(d):
        if should_stop and should_stop():
            raise Cancelled()
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if on_progress and d.get("status") == "downloading" and total:
            on_progress(d.get("downloaded_bytes", 0) / total)

    opts = ydl_options(
        format="bestaudio[ext=m4a]/bestaudio/best",
        outtmpl=str(Path(dest_dir) / "%(id)s.%(ext)s"),
        progress_hooks=[hook],
    )
    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                # Ré-extraction dans l'instance qui télécharge : les URL de flux sont liées à la session qui les a obtenues
                result = ydl.extract_info(info["webpage_url"], download=True)
            return Path(result["requested_downloads"][0]["filepath"])
        except yt_dlp.utils.DownloadError as exc:
            if "403" not in str(exc) or attempt == attempts or (should_stop and should_stop()):
                raise
            if on_retry:
                on_retry(attempt + 1, attempts)
            time.sleep(2)


# --- Écriture -------------------------------------------------------------------

def output_stem(info):
    """Nom de fichier lisible : titre + ID."""
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", info.get("title") or "video").strip(" .")[:80]
    return f"{title} [{info['id']}]"


def srt_time(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_outputs(segments, out_base, header):
    paragraphs, current, words, last_end = [], [], 0, None
    for seg in segments:
        sentence_end = current and current[-1].endswith((".", "?", "!"))
        if current and (seg.start - last_end >= PARAGRAPH_GAP
                        or (sentence_end and words >= PARAGRAPH_WORDS)
                        or words >= PARAGRAPH_MAX_WORDS):
            paragraphs.append(" ".join(current))
            current, words = [], 0
        current.append(seg.text)
        words += len(seg.text.split())
        last_end = seg.end
    if current:
        paragraphs.append(" ".join(current))

    out_base = Path(out_base)
    txt, srt = out_base.with_name(out_base.name + ".txt"), out_base.with_name(out_base.name + ".srt")
    txt.write_text(header + "\n\n" + "\n\n".join(paragraphs) + "\n", encoding="utf-8")
    blocks = [f"{i}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{s.text}\n" for i, s in enumerate(segments, 1)]
    srt.write_text("\n".join(blocks), encoding="utf-8")
    return txt, srt
