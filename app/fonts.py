"""Εγκατεστημένες γραμματοσειρές: ποιες υποστηρίζουν ελληνικά και ποιες είναι γραμματοσειρές μαθηματικών.

Το Word δέχεται για εξισώσεις μόνο γραμματοσειρές OpenType με πίνακα «MATH» (π.χ. Cambria Math,
STIX Two Math). Διαβάζουμε κάθε αρχείο γραμματοσειράς με το fontTools και ελέγχουμε:
- το όνομα οικογένειας όπως το δείχνει το Word (name ID 1),
- αν έχει ελληνικούς χαρακτήρες (α, Α, ω),
- αν έχει πίνακα MATH.
Τα αποτελέσματα αποθηκεύονται σε cache (fonts_cache.json) ώστε η σάρωση να γίνεται μία φορά.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from .paths import user_dir

FONT_EXT = {".ttf", ".otf", ".ttc", ".otc"}
GREEK_TEST = (0x03B1, 0x0391, 0x03C9)  # α Α ω
CACHE_VERSION = 1
_SCAN_LOCK = threading.Lock()  # η σάρωση παρασκηνίου και το άνοιγμα των Ρυθμίσεων μπορεί να συμπέσουν

# Γνωστές δωρεάν γραμματοσειρές μαθηματικών με ελληνικά (για πρόταση όταν δεν είναι εγκατεστημένες).
RECOMMENDED_MATH = [
    {"name": "Cambria Math", "pair": "Cambria", "note": "Έρχεται με τα Windows/Office"},
    {"name": "STIX Two Math", "pair": "STIX Two Text / Times New Roman", "note": "Κλασικό ύφος βιβλίου"},
    {"name": "TeX Gyre Termes Math", "pair": "Times New Roman", "note": "Τύπου Times"},
    {"name": "TeX Gyre Pagella Math", "pair": "Palatino Linotype / Book Antiqua", "note": "Τύπου Palatino"},
    {"name": "Latin Modern Math", "pair": "Latin Modern Roman", "note": "Το ύφος του LaTeX"},
    {"name": "Libertinus Math", "pair": "Libertinus Serif", "note": "Κομψό, τύπου Garamond"},
    {"name": "XITS Math", "pair": "Times New Roman", "note": "Τύπου Times"},
    {"name": "Fira Math", "pair": "Fira Sans / Segoe UI", "note": "Χωρίς πατούρες (sans)"},
]


def font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")  # εγκατάσταση «μόνο για τον χρήστη»
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path.home() / ".local" / "share" / "fonts", Path("/usr/share/texmf/fonts/opentype")]
    return [d for d in dirs if d.exists()]


def _family(font) -> str:
    """Όνομα οικογένειας όπως το δείχνει το Word (name ID 1), κατά προτίμηση το αγγλικό της Microsoft."""
    name = font["name"]
    best = None
    for rec in name.names:
        if rec.nameID != 1:
            continue
        try:
            text = rec.toUnicode().strip()
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        if rec.platformID == 3 and rec.langID == 0x409:
            return text
        best = best or text
    return best or ""


def _inspect_file(path: Path) -> list[dict]:
    from fontTools.ttLib import TTCollection, TTFont

    faces = []
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            fonts = TTCollection(str(path), lazy=True).fonts
        else:
            fonts = [TTFont(str(path), lazy=True)]
    except Exception:  # noqa: BLE001 — κατεστραμμένο/μη υποστηριζόμενο αρχείο
        return faces
    for f in fonts:
        try:
            fam = _family(f)
            if not fam:
                continue
            cmap = f["cmap"].getBestCmap() or {}
            faces.append({
                "family": fam,
                "greek": all(cp in cmap for cp in GREEK_TEST),
                "math": "MATH" in f,
            })
        except Exception:  # noqa: BLE001
            continue
        finally:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass
    return faces


def _cache_path() -> Path:
    return user_dir() / "fonts_cache.json"


def scan(refresh: bool = False, dirs: list[Path] | None = None) -> dict:
    """Επιστρέφει {"text": [{"name", "greek"}], "math": [ονόματα], "count": πλήθος αρχείων}."""
    with _SCAN_LOCK:
        return _scan(refresh, dirs)


def _scan(refresh: bool, dirs: list[Path] | None) -> dict:
    cache: dict = {}
    if not refresh:
        try:
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            if data.get("version") == CACHE_VERSION:
                cache = data.get("files", {})
        except (OSError, ValueError):
            cache = {}
    files: dict[str, dict] = {}
    for d in dirs if dirs is not None else font_dirs():
        for root, _subdirs, names in os.walk(d):
            for n in names:
                p = Path(root) / n
                if p.suffix.lower() not in FONT_EXT:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                key = str(p)
                stamp = [int(st.st_mtime), st.st_size]
                if key in cache and cache[key].get("stamp") == stamp:
                    files[key] = cache[key]
                else:
                    files[key] = {"stamp": stamp, "faces": _inspect_file(p)}
    try:
        _cache_path().write_text(json.dumps({"version": CACHE_VERSION, "files": files}, ensure_ascii=False),
                                 encoding="utf-8")
    except OSError:
        pass

    text: dict[str, bool] = {}
    math: set[str] = set()
    for info in files.values():
        for face in info.get("faces", []):
            fam = face["family"]
            text[fam] = text.get(fam, False) or face["greek"]
            if face["math"]:
                math.add(fam)
    return {
        "text": [{"name": n, "greek": g} for n, g in sorted(text.items(), key=lambda kv: kv[0].casefold())],
        "math": sorted(math, key=str.casefold),
        "count": len(files),
        "recommended_math": RECOMMENDED_MATH,
    }
