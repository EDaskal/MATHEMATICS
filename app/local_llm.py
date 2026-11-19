"""Τοπικό μοντέλο ανάγνωσης (1.8.0): μηχανή llama.cpp μέσα στην εφαρμογή + μοντέλο από το Hugging Face.

Πώς δουλεύει:

- Η **μηχανή** (`llama-server.exe` της llama.cpp, ανοιχτός κώδικας MIT) έρχεται μέσα στην εγκατάσταση
  (φάκελος `bin/llama`). Τη χτίζει το GitHub Actions από συγκεκριμένη έκδοση της llama.cpp (`LLAMA_TAG`
  στο build.yml), ώστε να μη χρειάζεται τίποτα άλλο στον υπολογιστή.
- Το **μοντέλο** (αρχεία .gguf, ~1,1 GB για το LightOnOCR-2) κατεβαίνει **μία φορά** από το Hugging Face,
  από συγκεκριμένη έκδοση (commit) του αποθετηρίου, και ελέγχεται με SHA-256 πριν χρησιμοποιηθεί. Φυλάγεται
  στο `%LOCALAPPDATA%\\Diagonismata\\models` (όχι στο «περιαγόμενο» %APPDATA%, γιατί είναι μεγάλο).
- Η μηχανή ξεκινά **μόνο όταν χρειαστεί**, ακούει μόνο στο 127.0.0.1 σε τυχαία θύρα, με τυχαίο κλειδί σε
  κάθε εκκίνηση, και σταματά όταν κλείνει η εφαρμογή.

Η επιλογή μοντέλου προέκυψε από το benchmark του Νοεμβρίου 2026 (8 ασκήσεις του Project): το LightOnOCR-2
δεν έκανε κανένα κρίσιμο μαθηματικό λάθος· το Qwen3.5-4B έκανε ένα, είναι 3× μεγαλύτερο και 2× πιο αργό.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .paths import resource, user_dir

HF = "https://huggingface.co/{repo}/resolve/{rev}/{name}"

# Πρόταση προς μοντέλα «γενικής χρήσης» (το LightOnOCR δεν θέλει πρόταση: διαβάζει μόνο τη σελίδα).
OCR_PROMPT = (
    "Μετάγραψε ΑΚΡΙΒΩΣ το κείμενο της εικόνας (άσκηση μαθηματικών στα ελληνικά). Γράψε το ελληνικό κείμενο "
    "όπως είναι και όλα τα μαθηματικά σε LaTeX μέσα σε $...$ (ή $$...$$ για εξισώσεις σε ξεχωριστή γραμμή). "
    "Κράτα την αρίθμηση α), β), γ) και i), ii). Αν υπάρχει σχήμα, γράψε μόνο [Σχήμα] στη θέση του. "
    "Μην λύσεις την άσκηση, μην προσθέσεις σχόλια."
)

CATALOG: dict[str, dict] = {
    "lightonocr2": {
        "title": "LightOnOCR-2 (1,1 GB) — προτείνεται",
        "short": "LightOnOCR-2",
        "note": "Το πιο ακριβές στο benchmark (8/8 ασκήσεις χωρίς κρίσιμο λάθος) και γρήγορο. Άδεια Apache 2.0.",
        "repo": "ggml-org/LightOnOCR-2-1B-GGUF",
        "rev": "7d4daf70c2856a155b1413def00f4ae59843a9cf",
        "model": ("LightOnOCR-2-1B-Q8_0.gguf", 639443584,
                  "f4cfc0ea9765a4cac22a235191c48aefa94df223419f5ab658b6201f36206d58"),
        "mmproj": ("mmproj-LightOnOCR-2-1B-Q8_0.gguf", 449452288,
                   "e9b45eb85b0f0afb3cc138236eec7adb75879420130e5e2049ee9af3f24e337f"),
        "prompt": None,
        "max_side": 1540,  # η ανάλυση εκπαίδευσης του μοντέλου
        "ctx": 8192,
    },
    "qwen35-4b": {
        "title": "Qwen3.5 4B (3,4 GB) — εναλλακτικό",
        "short": "Qwen3.5 4B",
        "note": "Γενικό μοντέλο: ελαφρώς πιο πιστό στην ορθογραφία, αλλά 2× πιο αργό και έκανε 1 κρίσιμο λάθος "
                "(sin αντί για συν). Άδεια Apache 2.0.",
        "repo": "ggml-org/Qwen3.5-4B-GGUF",
        "rev": "8d43d974efc48399cb58957ad49c8b26bf5a109c",
        "model": ("Qwen3.5-4B-Q4_K_M.gguf", 3032091584,
                  "217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14"),
        "mmproj": ("mmproj-Qwen3.5-4B-Q8_0.gguf", 366894560,
                   "5a5ba634d46f9888599963bfc52194eadf099e1efdbee46d71957d911cf62654"),
        "prompt": OCR_PROMPT,
        "max_side": 1600,
        "ctx": 8192,
        "no_think": True,
    },
}
DEFAULT_MODEL = "lightonocr2"


class LocalError(RuntimeError):
    """Σφάλμα με μήνυμα για τον χρήστη (ελληνικά)."""


# ----------------------------------------------------------------------------- αρχεία

def models_dir() -> Path:
    if os.environ.get("DIAGONISMATA_MODELS"):
        p = Path(os.environ["DIAGONISMATA_MODELS"])
    elif os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "Diagonismata" / "models"
    else:
        p = user_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def server_path() -> Path | None:
    """Η μηχανή llama-server: μέσα στην εγκατάσταση, ή (για ανάπτυξη) από μεταβλητή περιβάλλοντος / PATH."""
    env = os.environ.get("DIAGONISMATA_LLAMA_SERVER")
    if env and Path(env).is_file():
        return Path(env)
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    p = resource("bin", "llama", exe)
    if p.is_file():
        return p
    found = shutil.which(exe)
    return Path(found) if found else None


def _entry(key: str) -> dict:
    if key not in CATALOG:
        raise LocalError(f"Άγνωστο τοπικό μοντέλο: {key}")
    return CATALOG[key]


def _files(key: str) -> list[tuple[str, int, str]]:
    e = _entry(key)
    return [e["model"], e["mmproj"]]


def status(key: str) -> dict:
    e = _entry(key)
    files = []
    for name, size, _sha in _files(key):
        p = models_dir() / key / name
        files.append({"name": name, "size": size, "present": p.is_file() and p.stat().st_size == size})
    total = sum(f["size"] for f in files)
    have = sum(f["size"] for f in files if f["present"])
    srv = server_path()
    return {
        "key": key, "title": e["title"], "short": e["short"], "note": e["note"],
        "installed": all(f["present"] for f in files), "size_mb": round(total / 1e6), "have_mb": round(have / 1e6),
        "engine": bool(srv), "running": _SERVER.running_key() == key,
    }


def catalog_view() -> list[dict]:
    return [status(k) for k in CATALOG]


def remove(key: str) -> None:
    if _SERVER.running_key() == key:
        _SERVER.stop()
    d = models_dir() / key
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------------------- λήψη

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def install(key: str, progress: Callable[[int, int, str], None] | None = None,
            cancel: threading.Event | None = None, opener=None) -> None:
    """Κατεβάζει ό,τι λείπει, με έλεγχο SHA-256. Συνεχίζει από όπου σταμάτησε (αρχείο .part)."""
    from .updates import UA, UpdateError, _open

    e = _entry(key)
    folder = models_dir() / key
    folder.mkdir(parents=True, exist_ok=True)
    files = _files(key)
    total = sum(s for _n, s, _h in files)
    done_before = 0
    free = shutil.disk_usage(folder).free
    need = sum(s for n, s, _h in files if not (folder / n).is_file())
    if need and free < need + 200_000_000:
        raise LocalError(f"Δεν υπάρχει αρκετός χώρος στον δίσκο: χρειάζονται περίπου {round(need / 1e9, 1)} GB.")
    for name, size, sha in files:
        dest = folder / name
        if dest.is_file() and dest.stat().st_size == size:
            done_before += size
            if progress:
                progress(done_before, total, name)
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        start = part.stat().st_size if part.is_file() else 0
        if start >= size:
            part.unlink(missing_ok=True)
            start = 0
        url = HF.format(repo=e["repo"], rev=e["rev"], name=name)
        headers = {"User-Agent": UA}
        if start:
            headers["Range"] = f"bytes={start}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = (opener or _open)(req, 60)
        except UpdateError as exc:
            raise LocalError("Η λήψη του μοντέλου απέτυχε: " + str(exc).replace("GitHub", "Hugging Face")) from exc
        except urllib.error.HTTPError as exc:
            raise LocalError(f"Η λήψη του μοντέλου απέτυχε (HTTP {exc.code}). Δοκιμάστε ξανά αργότερα.") from exc
        with resp:
            code = getattr(resp, "status", 200)
            mode = "ab" if (start and code == 206) else "wb"
            got = start if mode == "ab" else 0
            with open(part, mode) as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise LocalError("Η λήψη ακυρώθηκε. Θα συνεχίσει από εκεί που σταμάτησε την επόμενη φορά.")
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(done_before + got, total, name)
        if part.stat().st_size != size:
            raise LocalError("Η λήψη δεν ολοκληρώθηκε (διακοπή σύνδεσης). Πατήστε ξανά «Λήψη» για να συνεχίσει.")
        if _sha256(part) != sha:
            part.unlink(missing_ok=True)
            raise LocalError(f"Το αρχείο «{name}» δεν πέρασε τον έλεγχο ακεραιότητας (SHA-256) και διαγράφηκε. "
                             "Δοκιμάστε ξανά.")
        os.replace(part, dest)
        done_before += size
        if progress:
            progress(done_before, total, name)


# ----------------------------------------------------------------------------- μηχανή

def _pid_file() -> Path:
    return user_dir() / "local-model.pid"


def _kill_stale() -> None:
    """Αν η εφαρμογή είχε κλείσει απότομα, η μηχανή της προηγούμενης φοράς μπορεί να τρέχει ακόμα
    (κρατά μνήμη). Τη σταματάμε — μόνο αν το PID είναι πράγματι llama-server."""
    pf = _pid_file()
    try:
        pid = int(pf.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],  # noqa: S603,S607
                                 capture_output=True, text=True, timeout=10, creationflags=flags).stdout
            if "llama-server" in out.lower():
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10,  # noqa: S603,S607
                               creationflags=flags)
        else:
            cmd = Path(f"/proc/{pid}/cmdline")
            if cmd.exists() and b"llama-server" in cmd.read_bytes():
                os.kill(pid, 15)
    except Exception:  # noqa: BLE001
        pass
    try:
        pf.unlink()
    except OSError:
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 ποτέ μέσω proxy


class _Server:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._key: str | None = None
        self._port = 0
        self._token = ""
        self._log: Path | None = None
        self.threads = 0

    def running_key(self) -> str | None:
        p = self._proc
        return self._key if p is not None and p.poll() is None else None

    def _request(self, path: str, body: dict | None = None, timeout: float = 5):
        url = f"http://127.0.0.1:{self._port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self._token}"})
        with _NO_PROXY.open(req, timeout=timeout) as r:  # noqa: S310 — μόνο 127.0.0.1
            return json.loads(r.read().decode("utf-8") or "{}")

    def _log_tail(self) -> str:
        try:
            return self._log.read_text(encoding="utf-8", errors="replace")[-1500:] if self._log else ""
        except OSError:
            return ""

    def ensure(self, key: str, threads: int = 0, load_timeout: float = 240) -> None:
        with self._lock:
            if self.running_key() == key and threads == self.threads:
                return
            self._stop_locked()
            _kill_stale()
            st = status(key)
            if not st["installed"]:
                raise LocalError("Το τοπικό μοντέλο δεν έχει κατέβει ακόμα. Ρυθμίσεις → «Τοπικό μοντέλο» → «Λήψη».")
            exe = server_path()
            if not exe:
                raise LocalError("Λείπει η μηχανή του τοπικού μοντέλου (llama-server) από την εγκατάσταση. "
                                 "Εγκαταστήστε ξανά την εφαρμογή.")
            e = CATALOG[key]
            folder = models_dir() / key
            self._port = _free_port()
            self._token = secrets.token_hex(16)
            args = [str(exe), "-m", str(folder / e["model"][0]), "--mmproj", str(folder / e["mmproj"][0]),
                    "--host", "127.0.0.1", "--port", str(self._port), "-c", str(e["ctx"]), "-np", "1",
                    "--no-webui", "--api-key", self._token]
            if threads and threads > 0:
                args += ["-t", str(int(threads))]
            self._log = user_dir() / "local-model.log"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            logf = open(self._log, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            try:
                self._proc = subprocess.Popen(  # noqa: S603 — δικό μας εκτελέσιμο, χωρίς shell
                    args, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    cwd=str(exe.parent), creationflags=flags)
            except OSError as exc:
                logf.close()
                raise LocalError(f"Η μηχανή του τοπικού μοντέλου δεν ξεκίνησε: {exc}") from exc
            logf.close()
            self._key, self.threads = key, threads
            try:
                _pid_file().write_text(str(self._proc.pid), encoding="ascii")
            except OSError:
                pass
            t0 = time.time()
            while time.time() - t0 < load_timeout:
                if self._proc.poll() is not None:
                    tail = self._log_tail()
                    self._proc = None
                    self._key = None
                    hint = " (λίγη μνήμη RAM;)" if "alloc" in tail.lower() or "memory" in tail.lower() else ""
                    raise LocalError("Η μηχανή του τοπικού μοντέλου σταμάτησε κατά τη φόρτωση" + hint + ".\n" + tail[-600:])
                try:
                    self._request("/health", timeout=2)
                    return
                except (urllib.error.URLError, OSError, ValueError):
                    time.sleep(0.5)
            self._stop_locked()
            raise LocalError("Το τοπικό μοντέλο δεν φόρτωσε εγκαίρως. Ίσως ο υπολογιστής έχει λίγη μνήμη.")

    def _stop_locked(self) -> None:
        p = self._proc
        self._proc, self._key = None, None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def chat(self, key: str, png: bytes, prompt: str | None, timeout: float, threads: int = 0) -> str:
        self.ensure(key, threads)
        e = CATALOG[key]
        content: list[dict] = [{"type": "image_url",
                                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}}]
        if prompt:
            content.append({"type": "text", "text": prompt})
        body: dict = {"messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 3000}
        if e.get("no_think"):
            body["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            r = self._request("/v1/chat/completions", body, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                msg = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                msg = str(exc)
            raise LocalError(f"Το τοπικό μοντέλο απάντησε με σφάλμα: {msg}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if self.running_key() is None:
                raise LocalError("Η μηχανή του τοπικού μοντέλου σταμάτησε απρόσμενα.\n" + self._log_tail()[-400:]) from exc
            raise LocalError("Το τοπικό μοντέλο άργησε πολύ να απαντήσει. Δοκιμάστε ξανά ή αυξήστε το χρονικό όριο.") from exc
        try:
            return str(r["choices"][0]["message"].get("content") or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalError("Μη αναμενόμενη απάντηση από το τοπικό μοντέλο.") from exc


_SERVER = _Server()
atexit.register(_SERVER.stop)


def stop() -> None:
    _SERVER.stop()


def prepare(png_bytes: bytes, max_side: int) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    img.load()
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()
    return png_bytes


def ocr(key: str, png_bytes: bytes, timeout: float = 600, threads: int = 0) -> str:
    """Μία εικόνα → κείμενο (Markdown + LaTeX) από το τοπικό μοντέλο."""
    e = _entry(key)
    _SERVER.ensure(key, threads)  # πρώτα η μηχανή: καθαρό μήνυμα αν λείπει το μοντέλο
    return _SERVER.chat(key, prepare(png_bytes, e["max_side"]), e["prompt"], timeout, threads)


def engine_version() -> str:
    """`llama-server --version` (για τον αυτοέλεγχο)."""
    exe = server_path()
    if not exe:
        raise LocalError("Λείπει το llama-server.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60,  # noqa: S603
                       cwd=str(exe.parent), creationflags=flags)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 and "version" not in out.lower():
        raise LocalError(f"Το llama-server δεν τρέχει (κωδικός {r.returncode}): {out[-300:]}")
    return next((ln for ln in out.splitlines() if "version" in ln.lower()), out[:120])


__all__ = ["CATALOG", "DEFAULT_MODEL", "LocalError", "catalog_view", "install", "ocr", "remove", "status", "stop",
           "server_path", "models_dir", "engine_version"]
