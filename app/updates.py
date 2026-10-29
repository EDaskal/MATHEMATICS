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
    """Σφάλμα με μήνυμα για τον χρήστη· `detail` = τεχνικές λεπτομέρειες (για το «Λεπτομέρειες»)."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


RELEASES_PAGE = "https://github.com/{repo}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """«v1.2.3» → (1, 2, 3). Ό,τι δεν είναι αριθμός αγνοείται."""
    nums = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


# --------------------------------------------------------------------------- σύνδεση

def _contexts() -> list:
    """Τρόποι επαλήθευσης της ασφαλούς σύνδεσης, με σειρά προτίμησης (1.7.7):

    1. **Windows** (`truststore`): ο ίδιος έλεγχος με τον Edge. Τα Windows κατεβάζουν μόνα τους
       πιστοποιητικά-ρίζες που λείπουν και εμπιστεύονται ό,τι έχει εγκρίνει ο υπολογιστής (π.χ. antivirus).
    2. **Python**: τα πιστοποιητικά που είναι ήδη αποθηκευμένα στα Windows (ο μόνος τρόπος ως την 1.7.6 —
       αποτύγχανε όταν έλειπε η ρίζα του GitHub από τον υπολογιστή).
    3. **certifi**: η λίστα πιστοποιητικών που έχει μέσα της η εφαρμογή (της Mozilla)."""
    import ssl

    out = []
    try:
        import truststore

        out.append(("Windows", truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
    except Exception:  # noqa: BLE001 — λείπει ή δεν υποστηρίζεται
        pass
    out.append(("Python", ssl.create_default_context()))
    try:
        import certifi

        out.append(("certifi", ssl.create_default_context(cafile=certifi.where())))
    except Exception:  # noqa: BLE001
        pass
    return out


def _reason(exc: BaseException) -> BaseException:
    r = getattr(exc, "reason", None)
    return r if isinstance(r, BaseException) else exc


def _is_tls(exc: BaseException) -> bool:
    import ssl

    r = _reason(exc)
    return isinstance(r, ssl.SSLError) or "CERTIFICATE" in str(r).upper() or "SSL" in type(r).__name__.upper()


def _open(req, timeout: int):
    """urlopen με διαδοχικούς τρόπους επαλήθευσης. Αλλάζουμε τρόπο μόνο όταν φταίει η ασφαλής σύνδεση
    (πιστοποιητικό)· αν δεν υπάρχει δίκτυο ή μπλοκάρει firewall, οι άλλοι τρόποι δεν θα βοηθούσαν."""
    tried, last = [], None
    for name, ctx in _contexts():
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)  # noqa: S310 — σταθερά https URL
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            tried.append(f"{name}: {type(_reason(exc)).__name__}: {_reason(exc)}")
            last = exc
            if not _is_tls(exc):
                break
    raise _explain(last, tried)


def _explain(exc: BaseException | None, tried: list[str]) -> UpdateError:
    """Κατανοητό μήνυμα για την πραγματική αιτία (πριν όλα έλεγαν «δεν υπάρχει σύνδεση»)."""
    import socket
    import ssl

    r = _reason(exc) if exc is not None else None
    text = str(r or "").lower()
    detail = " | ".join(tried) or repr(exc)
    if isinstance(r, ssl.SSLCertVerificationError) or "certificate verify failed" in text:
        if "not yet valid" in text or "expired" in text:
            msg = ("Η ημερομηνία ή η ώρα του υπολογιστή φαίνεται λάθος, γι' αυτό δεν επαληθεύεται η σύνδεση με το "
                   "GitHub. Ρυθμίστε την ώρα (δεξί κλικ στο ρολόι → Ρύθμιση ημερομηνίας/ώρας → Συγχρονισμός τώρα).")
        else:
            msg = ("Δεν επαληθεύτηκε το πιστοποιητικό ασφαλείας του GitHub. Συνήθως φταίει antivirus που ελέγχει "
                   "τις ασφαλείς συνδέσεις («HTTPS scanning» / «Web Shield»): προσθέστε το «Diagonismata» στις εξαιρέσεις του.")
    elif isinstance(r, ssl.SSLError):
        msg = ("Η ασφαλής σύνδεση με το GitHub διακόπηκε. Πιθανότατα την εμποδίζει antivirus ή firewall· "
               "προσθέστε το «Diagonismata» στις εξαιρέσεις του.")
    elif isinstance(r, socket.gaierror):
        msg = "Δεν βρέθηκε το GitHub: ο υπολογιστής δεν έχει internet ή υπάρχει πρόβλημα DNS."
    elif isinstance(r, (TimeoutError, socket.timeout)) or "timed out" in text:
        msg = ("Το GitHub δεν απάντησε εγκαίρως. Ίσως το internet είναι αργό ή κάποιο firewall κρατά τη σύνδεση· "
               "δοκιμάστε ξανά σε λίγο.")
    elif "proxy" in text or "407" in text:
        msg = "Η σύνδεση περνά από proxy που δεν την επέτρεψε. Ελέγξτε τις ρυθμίσεις proxy των Windows."
    elif isinstance(r, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, PermissionError)) \
            or "10013" in text or "forbidden by its access permissions" in text:
        msg = ("Η σύνδεση μπλοκαρίστηκε, πιθανότατα από firewall ή antivirus. Επιτρέψτε στο «Diagonismata» την "
               "πρόσβαση στο internet και δοκιμάστε ξανά.")
    else:
        msg = "Δεν υπάρχει σύνδεση με το GitHub (internet)."
    return UpdateError(msg, detail)


def _get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with _open(req, timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Δεν βρέθηκε δημοσιευμένη έκδοση (Release) στο GitHub.") from exc
        if exc.code == 403:
            raise UpdateError("Το GitHub περιόρισε προσωρινά τους ελέγχους. Δοκιμάστε αργότερα.") from exc
        raise UpdateError(f"Σφάλμα GitHub ({exc.code}).") from exc
    except ValueError as exc:
        raise UpdateError("Μη αναμενόμενη απάντηση από το GitHub.", str(exc)) from exc


def diagnose(repo: str) -> list[dict]:
    """Δοκιμή σύνδεσης με κάθε τρόπο επαλήθευσης χωριστά (για το «Λεπτομέρειες» της οθόνης)."""
    out = []
    url = API.format(repo=repo)
    for name, ctx in _contexts():
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:  # noqa: S310
                out.append({"method": name, "ok": True, "detail": f"HTTP {resp.status}"})
        except urllib.error.HTTPError as exc:
            out.append({"method": name, "ok": exc.code != 404, "detail": f"HTTP {exc.code}"})
        except Exception as exc:  # noqa: BLE001
            r = _reason(exc)
            out.append({"method": name, "ok": False, "detail": f"{type(r).__name__}: {r}"})
    return out


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


def is_release_asset(url: str, repo: str) -> bool:
    """Μόνο πρόγραμμα εγκατάστασης (.exe) από τα GitHub Releases του αποθετηρίου της εφαρμογής."""
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
    except ValueError:
        return False
    owner_repo = "/" + (repo or "").strip("/").lower() + "/releases/download/"
    return (u.scheme == "https" and u.hostname == "github.com" and not u.username and not u.password
            and (u.port in (None, 443)) and u.path.lower().startswith(owner_repo) and u.path.lower().endswith(".exe")
            and ".." not in u.path)


def download(url: str, progress: Callable[[int, int], None] | None = None) -> Path:
    if not url.startswith("https://"):
        raise UpdateError("Μη ασφαλής διεύθυνση λήψης.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1].split("?", 1)[0]) or "setup.exe"
    dest = Path(tempfile.gettempdir()) / name
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _open(req, 60) as resp, open(dest, "wb") as out:
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
    except UpdateError as exc:
        raise UpdateError("Η λήψη απέτυχε. " + str(exc), exc.detail) from exc
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
