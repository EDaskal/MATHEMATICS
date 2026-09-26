"""Έλεγχος και εγκατάσταση ενημερώσεων από τα GitHub Releases του repository.

Κάθε νέα έκδοση δημοσιεύεται ως Release με tag «vX.Y.Z» και συνημμένο το
«Diagonismata-Setup-X.Y.Z.exe» (το φτιάχνει αυτόματα το GitHub Actions).
Η εφαρμογή συγκρίνει τον αριθμό έκδοσης, κατεβάζει το πρόγραμμα εγκατάστασης
και το τρέχει σιωπηλά· το Inno Setup αντικαθιστά την παλιά έκδοση και ξανανοίγει την εφαρμογή.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

API = "https://api.github.com/repos/{repo}/releases/latest"
UA = f"Diagonismata/{__version__}"


class UpdateError(RuntimeError):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    """«v1.2.3» → (1, 2, 3). Ό,τι δεν είναι αριθμός αγνοείται."""
    nums = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def _get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — σταθερό https URL
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Δεν βρέθηκε δημοσιευμένη έκδοση (Release) στο GitHub.") from exc
        if exc.code == 403:
            raise UpdateError("Το GitHub περιόρισε προσωρινά τους ελέγχους. Δοκιμάστε αργότερα.") from exc
        raise UpdateError(f"Σφάλμα GitHub ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Δεν υπάρχει σύνδεση με το GitHub (internet).") from exc


def check(repo: str, current: str = __version__) -> dict:
    """Επιστρέφει {"current", "latest", "newer", "notes", "url", "size", "page", "published"}."""
    data = _get_json(API.format(repo=repo))
    tag = data.get("tag_name") or data.get("name") or ""
    asset = next(
        (a for a in data.get("assets") or [] if a.get("name", "").lower().endswith(".exe")),
        None,
    )
    return {
        "current": current,
        "latest": tag.lstrip("v"),
        "newer": bool(tag) and is_newer(tag, current),
        "notes": data.get("body") or "",
        "url": asset.get("browser_download_url") if asset else "",
        "size": asset.get("size", 0) if asset else 0,
        "page": data.get("html_url", ""),
        "published": (data.get("published_at") or "")[:10],
    }


def download(url: str, progress: Callable[[int, int], None] | None = None) -> Path:
    if not url.startswith("https://"):
        raise UpdateError("Μη ασφαλής διεύθυνση λήψης.")
    dest = Path(tempfile.gettempdir()) / url.rsplit("/", 1)[-1]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:  # noqa: S310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Η λήψη απέτυχε: {exc}") from exc
    if total and dest.stat().st_size != total:
        raise UpdateError("Η λήψη δεν ολοκληρώθηκε. Δοκιμάστε ξανά.")
    return dest


def launch_installer(path: Path) -> None:
    """Σιωπηλή εγκατάσταση πάνω από την τρέχουσα· το Inno Setup ξανανοίγει την εφαρμογή στο τέλος."""
    if os.name != "nt":
        raise UpdateError("Η αυτόματη εγκατάσταση γίνεται μόνο στα Windows.")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(  # noqa: S603
        [str(path), "/SILENT", "/SP-", "/NOCANCEL", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=flags,
        close_fds=True,
    )
