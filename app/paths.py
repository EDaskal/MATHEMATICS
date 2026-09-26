"""Διαδρομές αρχείων: πόροι της εφαρμογής (και μέσα στο .exe) και φάκελος χρήστη."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME


def resource_dir() -> Path:
    """Φάκελος με τα αρχεία που συνοδεύουν την εφαρμογή (ui, resources)."""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
    return Path(__file__).resolve().parent


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def user_dir() -> Path:
    """Φάκελος ρυθμίσεων χρήστη (%APPDATA%\\Diagonismata στα Windows)."""
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir() -> Path:
    """Προσωρινός φάκελος εργασίας (εικόνες, σχήματα, ενδιάμεσα αρχεία)."""
    p = user_dir() / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home()
    return docs / "Διαγωνίσματα"


def pandoc_path() -> str:
    """Το pandoc που συνοδεύει την εφαρμογή· αλλιώς όποιο βρεθεί στο PATH."""
    exe = "pandoc.exe" if os.name == "nt" else "pandoc"
    bundled = resource("bin", exe)
    if bundled.exists():
        return str(bundled)
    found = shutil.which("pandoc")
    if not found:
        raise RuntimeError("Δεν βρέθηκε το pandoc. Επανεγκαταστήστε την εφαρμογή.")
    return found


def rules_path() -> Path:
    """Οι κανόνες μεταγραφής: αν υπάρχει δική της έκδοση στον φάκελο χρήστη, αυτή υπερισχύει."""
    custom = user_dir() / "rules.md"
    return custom if custom.exists() else resource("resources", "rules.md")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def doc_path(name: str) -> Path | None:
    """Έγγραφα που συνοδεύουν την εφαρμογή (CHANGELOG.md, docs/user-guide.md).
    Στο .exe βρίσκονται στο app/resources/docs· σε ανάπτυξη διαβάζονται από τη ρίζα του repository."""
    bundled = resource("resources", "docs", Path(name).name)
    if bundled.exists():
        return bundled
    dev = repo_root() / name
    return dev if dev.exists() else None


def build_info() -> dict:
    """Στοιχεία build (γράφονται από το GitHub Actions): commit, ημερομηνία, αριθμός build."""
    import json

    p = resource("resources", "build_info.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
