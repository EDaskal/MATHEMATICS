"""Η γέφυρα ανάμεσα στην οθόνη (HTML/JS) και στον πυρήνα Python.

Κάθε δημόσια μέθοδος της κλάσης Api καλείται από τη JavaScript ως
`await api.method(...)`. Οι μακροχρόνιες εργασίες (μεταγραφή) τρέχουν σε νήμα και
στέλνουν γεγονότα στην οθόνη μέσω `emit`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from . import APP_TITLE, __version__
from . import points as pts
from .config import MODELS, PROVIDERS, SECRET_API_KEY, SECRET_OAUTH, Settings, get_secret, set_secret
from .paths import resource, rules_path, user_dir, work_dir

_DATA_URL = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.S)


def _data_url(path: str | Path, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", text or "").strip()
    return re.sub(r"\s+", " ", text)[:120] or "Διαγώνισμα"


def open_path(path: str | Path) -> None:
    path = str(path)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Api:
    def __init__(self):
        self._settings = Settings.load()
        self._window = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._images: dict[str, Path] = {}
        self._session_dir = work_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_sessions()

    # ------------------------------------------------------------ υποδομή

    def attach_window(self, window) -> None:
        self._window = window

    def emit(self, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        if self._window is not None:
            js = f"window.__onPyEvent({json.dumps(payload, ensure_ascii=False)})"
            try:
                self._window.evaluate_js(js)
                return
            except Exception:
                pass
        self._events.put(payload)

    def _poll(self) -> list[dict]:
        """Για τη λειτουργία browser: η οθόνη ρωτάει περιοδικά για νέα γεγονότα."""
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def _cleanup_old_sessions(self, keep: int = 5) -> None:
        try:
            dirs = sorted((d for d in work_dir().iterdir() if d.is_dir()), key=lambda d: d.name)
            for d in dirs[:-keep]:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    # ------------------------------------------------------------ κατάσταση & ρυθμίσεις

    def get_state(self) -> dict:
        s = self._settings
        return {
            "app_title": APP_TITLE,
            "version": __version__,
            "settings": s.to_dict(),
            "providers": PROVIDERS,
            "models": MODELS,
            "has_oauth": bool(get_secret(SECRET_OAUTH)),
            "has_api_key": bool(get_secret(SECRET_API_KEY)),
            "rules_custom": (user_dir() / "rules.md").exists(),
            "template_exists": (not s.template_path) or Path(s.template_path).exists(),
            "today": datetime.now().strftime("%d/%m/%Y"),
        }

    def save_settings(self, data: dict) -> dict:
        self._settings.update(data or {})
        self._settings.save()
        return self.get_state()

    def set_secret(self, kind: str, value: str) -> dict:
        name = {"oauth": SECRET_OAUTH, "api_key": SECRET_API_KEY}.get(kind)
        if not name:
            return {"ok": False, "error": "Άγνωστο στοιχείο."}
        try:
            set_secret(name, (value or "").strip())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "state": self.get_state()}

    def choose_template(self) -> dict:
        path = self._file_dialog(open_=True, types=("Έγγραφα Word (*.docx)",))
        if path:
            self._settings.template_path = path
            self._settings.save()
        return self.get_state()

    def reset_template(self) -> dict:
        self._settings.template_path = ""
        self._settings.save()
        return self.get_state()

    def choose_output_dir(self) -> dict:
        path = self._file_dialog(folder=True)
        if path:
            self._settings.output_dir = path
            self._settings.save()
        return self.get_state()

    def _file_dialog(self, open_: bool = False, folder: bool = False, types=()) -> str:
        if self._window is None:
            return ""
        import webview

        fd = getattr(webview, "FileDialog", None)
        if folder:
            kind = fd.FOLDER if fd else webview.FOLDER_DIALOG
            res = self._window.create_file_dialog(kind)
        else:
            kind = fd.OPEN if fd else webview.OPEN_DIALOG
            res = self._window.create_file_dialog(kind, allow_multiple=False, file_types=types)
        if not res:
            return ""
        return res[0] if isinstance(res, (list, tuple)) else str(res)

    def open_rules(self) -> dict:
        custom = user_dir() / "rules.md"
        if not custom.exists():
            shutil.copyfile(resource("resources", "rules.md"), custom)
        open_path(custom)
        return {"ok": True, "path": str(custom)}

    def reset_rules(self) -> dict:
        custom = user_dir() / "rules.md"
        if custom.exists():
            custom.unlink()
        return {"ok": True}

    def launch_setup_token(self) -> dict:
        try:
            from .providers.agent_sdk import launch_setup_token

            launch_setup_token()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def test_connection(self) -> dict:
        from .providers import get_provider

        try:
            provider = get_provider(self._settings)
            text = asyncio.run(provider.ping())
            return {"ok": True, "message": f"Η σύνδεση λειτουργεί ({self._settings.provider}, {self._settings.model}): {text}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------ εικόνες & μεταγραφή

    def add_image(self, data_url: str) -> dict:
        m = _DATA_URL.match(data_url or "")
        if not m:
            return {"ok": False, "error": "Μη αναγνωρίσιμη εικόνα."}
        raw = base64.b64decode(m.group(2))
        try:
            from .transcribe import prepare_image

            img = prepare_image(raw)  # έλεγχος ότι ανοίγει και κανονικοποίηση
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "Η εικόνα δεν ανοίγει. Δοκιμάστε PNG ή JPG."}
        image_id = uuid.uuid4().hex[:12]
        path = self._session_dir / f"img_{image_id}.png"
        path.write_bytes(img.data)
        self._images[image_id] = path
        return {"ok": True, "image_id": image_id}

    def transcribe(self, jobs: list[dict]) -> dict:
        """jobs: [{"job_id", "image_ids": [σελίδα 1, σελίδα 2, …]}] (δεκτό και το παλιό "image_id").
        Τα αποτελέσματα έρχονται ως γεγονότα «transcribed»."""
        valid = []
        for j in jobs:
            ids = j.get("image_ids") or ([j["image_id"]] if j.get("image_id") else [])
            pages = [self._images[i].read_bytes() for i in ids if i in self._images]
            if pages:
                valid.append((j["job_id"], pages))
        if not valid:
            return {"ok": False, "error": "Δεν υπάρχουν εικόνες για μεταγραφή."}
        threading.Thread(target=self._transcribe_worker, args=(valid,), daemon=True).start()
        return {"ok": True, "count": len(valid)}

    def _transcribe_worker(self, jobs: list[tuple[str, list[bytes]]]) -> None:
        from .providers import get_provider
        from .transcribe import transcribe_many

        try:
            provider = get_provider(self._settings)
        except Exception as exc:  # noqa: BLE001
            for job_id, _ in jobs:
                self.emit("transcribed", {"job_id": job_id, "error": str(exc)})
            return

        def done(job_id, result, error):
            if result is not None:
                fig = result.get("figure")
                if fig:
                    result["figure_render"] = self._render(job_id, fig.get("spec_json", ""))
            self.emit("transcribed", {"job_id": job_id, "result": result, "error": error})

        try:
            asyncio.run(transcribe_many(provider, jobs, self._settings.max_parallel, done))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            for job_id, _ in jobs:
                self.emit("transcribed", {"job_id": job_id, "error": f"Απρόσμενο σφάλμα: {exc}"})

    # ------------------------------------------------------------ σχήματα

    def _render(self, key: str, spec_json: str) -> dict:
        from .figures import FigureError, render_to_files

        base = self._session_dir / f"fig_{key}_{uuid.uuid4().hex[:6]}"
        try:
            info = render_to_files(spec_json, base, font=self._settings.font_name)
            info["png_data"] = _data_url(info["png"])
            info["error"] = None
            return info
        except FigureError as exc:
            return {"error": str(exc), "warnings": []}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Αποτυχία σχεδίασης: {exc}", "warnings": []}

    def render_figure(self, key: str, spec_json: str) -> dict:
        return self._render(key, spec_json)

    def crop_figure(self, key: str, image_id: str, crop: list[float]) -> dict:
        from .figures import crop_original

        path = self._images.get(image_id)
        if not path:
            return {"error": "Δεν βρέθηκε η εικόνα."}
        out = self._session_dir / f"crop_{key}_{uuid.uuid4().hex[:6]}.png"
        info = crop_original(path.read_bytes(), crop, out)
        info["png_data"] = _data_url(info["png"])
        info["error"] = None
        return info

    # ------------------------------------------------------------ μονάδες

    def propose_points(self, n_themes: int) -> list[int]:
        return pts.theme_points(int(n_themes))

    def split_points(self, total: int, n: int) -> list[int]:
        return pts.item_points(int(total), int(n))

    # ------------------------------------------------------------ παραγωγή

    def build(self, exam: dict) -> dict:
        """exam: στοιχεία + θέματα. Στα σχήματα, η οθόνη στέλνει {"mode", "spec_json", "png", "svg", "width_cm"}·
        τα επανασχεδιασμένα ξανασχεδιάζονται εδώ με την τρέχουσα γραμματοσειρά."""
        from .docx_build import BuildError, PageSetup, build_docx
        from .figures import FigureError, render_to_files

        s = self._settings
        s.last_title = exam.get("title", s.last_title)
        s.last_subtitle = exam.get("subtitle", s.last_subtitle)
        s.last_class = exam.get("class_name", "")
        s.last_editor = exam.get("editor", "")
        s.remember("classes", s.last_class)
        s.remember("editors", s.last_editor)
        s.save()

        warnings: list[str] = []
        for ti, theme in enumerate(exam.get("themes", [])):
            for ei, ex in enumerate(theme.get("exercises", [])):
                fr = ex.get("figure_render")
                if fr and fr.get("mode") == "redraw" and fr.get("spec_json"):
                    try:
                        info = render_to_files(fr["spec_json"], self._session_dir / f"final_{ti}_{ei}_{uuid.uuid4().hex[:4]}",
                                               font=s.font_name)
                        ex["figure_render"] = info
                    except FigureError as exc:
                        warnings.append(f"Σχήμα θέματος {ti + 1}: {exc}")
                        ex["figure_render"] = None

        name = _safe_name(" ".join(x for x in (exam.get("title"), exam.get("subtitle"), exam.get("date", "").replace("/", ".")) if x))
        out_dir = Path(s.output_dir or ".")
        out = out_dir / f"{name}.docx"
        i = 2
        while out.exists():
            out = out_dir / f"{name} ({i}).docx"
            i += 1
        setup = PageSetup(font_name=s.font_name or "Cambria", font_size=float(s.font_size or 12),
                          class_name=exam.get("class_name", ""), editor=exam.get("editor", ""))
        try:
            res = build_docx(exam, out, template=s.template_path, sublevel_style=s.sublevel_style,
                             points_align=s.points_align, setup=setup)
        except BuildError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return {"ok": False, "error": f"Απρόσμενο σφάλμα: {exc}"}
        try:
            (self._session_dir / "last_exam.json").write_text(json.dumps(exam, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        res.pop("markdown", None)
        res["warnings"] = warnings + res.get("warnings", [])
        return {"ok": True, **res, "state": self.get_state()}

    # ------------------------------------------------------------ έκδοση & ενημερώσεις

    def get_about(self) -> dict:
        from .paths import build_info, doc_path

        def read(name):
            p = doc_path(name)
            return p.read_text(encoding="utf-8") if p else ""

        return {
            "version": __version__,
            "build": build_info(),
            "changelog": read("CHANGELOG.md"),
            "guide": read("docs/user-guide.md"),
            "repo": self._settings.update_repo,
        }

    def check_updates(self) -> dict:
        from .updates import UpdateError, check

        try:
            return {"ok": True, **check(self._settings.update_repo, __version__)}
        except UpdateError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Αποτυχία ελέγχου: {exc}"}

    def install_update(self, url: str) -> dict:
        """Κατεβάζει το νέο πρόγραμμα εγκατάστασης (γεγονότα «update_progress»), το τρέχει και κλείνει την εφαρμογή."""
        threading.Thread(target=self._update_worker, args=(url,), daemon=True).start()
        return {"ok": True}

    def _update_worker(self, url: str) -> None:
        from .updates import UpdateError, download, launch_installer

        def progress(done, total):
            self.emit("update_progress", {"done": done, "total": total})

        try:
            path = download(url, progress)
            self.emit("update_progress", {"done": 1, "total": 1, "stage": "install"})
            launch_installer(path)
        except UpdateError as exc:
            self.emit("update_progress", {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self.emit("update_progress", {"error": f"Αποτυχία ενημέρωσης: {exc}"})
            return
        # το πρόγραμμα εγκατάστασης θα αντικαταστήσει τα αρχεία: κλείνουμε
        def _quit():
            try:
                if self._window is not None:
                    self._window.destroy()
            finally:
                os._exit(0)

        threading.Timer(1.5, _quit).start()

    def forget_value(self, list_name: str, value: str) -> dict:
        """Αφαίρεση τιμής από αποθηκευμένη λίστα (τάξεις/επιμελητές)."""
        if list_name in ("classes", "editors"):
            lst = getattr(self._settings, list_name)
            if value in lst:
                lst.remove(value)
                self._settings.save()
        return self.get_state()

    def open_file(self, path: str) -> dict:
        try:
            open_path(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_folder(self, path: str) -> dict:
        p = Path(path)
        return self.open_file(str(p if p.is_dir() else p.parent))
