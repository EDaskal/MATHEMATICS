"""Σελίδες PDF ως εικόνες, για να διαβαστούν όπως οι φωτογραφίες.

Η απόδοση γίνεται με το PDFium (η μηχανή PDF του Chrome, πακέτο pypdfium2) μέσα στην εφαρμογή,
χωρίς σύνδεση στο Internet. Κάθε σελίδα γίνεται εικόνα με τη μεγάλη πλευρά ~2000 px — όσο και οι
φωτογραφίες που στέλνονται για ανάγνωση.

Προφυλάξεις (το αρχείο μπορεί να έρθει από το κινητό):
- έλεγχος ότι είναι πράγματι PDF (υπογραφή «%PDF» στην αρχή) και όριο μεγέθους·
- όριο σελίδων (MAX_PAGES) και σταθερό μέγιστο μέγεθος εικόνας ανά σελίδα, όσο μεγάλη κι αν
  δηλώνει τη σελίδα το αρχείο·
- πρώτα μόνο μικρογραφίες (γρήγορα)· σε πλήρες μέγεθος αποδίδονται μόνο οι σελίδες που διαλέγει ο χρήστης·
- PDF με κωδικό ή κατεστραμμένο → κατανοητό μήνυμα, όχι σφάλμα της εφαρμογής.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

MAX_BYTES = 60 * 1024 * 1024
MAX_PAGES = 200  # σελίδες που εμφανίζονται για επιλογή
LONG_SIDE = 2000  # px, η μεγάλη πλευρά κάθε σελίδας
THUMB = 360  # px, η μεγάλη πλευρά της μικρογραφίας στο παράθυρο επιλογής σελίδων


class PdfError(Exception):
    """Σφάλμα με μήνυμα για τον χρήστη (στα ελληνικά)."""


@dataclass
class RenderedPdf:
    total: int  # σελίδες του αρχείου
    pages: list = field(default_factory=list)  # PIL.Image ανά σελίδα (έως MAX_PAGES)

    @property
    def truncated(self) -> bool:
        return self.total > len(self.pages)


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def _open(data: bytes):
    """Έλεγχοι και άνοιγμα με το PDFium. Επιστρέφει (pdfium, έγγραφο)."""
    if not data:
        raise PdfError("Το αρχείο είναι κενό.")
    if len(data) > MAX_BYTES:
        raise PdfError(f"Το PDF είναι πολύ μεγάλο (πάνω από {MAX_BYTES // (1024 * 1024)} MB).")
    if not looks_like_pdf(data):
        raise PdfError("Το αρχείο δεν είναι PDF.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover — λείπει από την εγκατάσταση
        raise PdfError("Λείπει το εξάρτημα ανάγνωσης PDF από την εγκατάσταση.") from exc
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise PdfError("Το PDF έχει κωδικό. Ανοίξτε το, αποθηκεύστε το χωρίς κωδικό και ξαναδοκιμάστε.") from exc
        raise PdfError("Το PDF δεν ανοίγει (ίσως είναι κατεστραμμένο).") from exc
    if len(doc) == 0:
        doc.close()
        raise PdfError("Το PDF δεν έχει σελίδες.")
    return pdfium, doc


def _render_one(pdfium, doc, i: int, long_side: int):
    page = doc[i]
    try:
        w, h = page.get_size()
        scale = min(long_side / max(w, h, 1.0), 6.0)  # μικρές σελίδες: όχι υπερβολική μεγέθυνση
        img = page.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
    except pdfium.PdfiumError as exc:
        raise PdfError(f"Η σελίδα {i + 1} του PDF δεν αποδίδεται.") from exc
    finally:
        page.close()
    if max(img.size) > long_side:  # ασφάλεια για παράξενα μεγέθη σελίδων
        img.thumbnail((long_side, long_side))
    return img


def render(data: bytes, max_pages: int = MAX_PAGES, long_side: int = LONG_SIDE) -> RenderedPdf:
    """Οι πρώτες `max_pages` σελίδες ως εικόνες (μεγάλη πλευρά `long_side` px)."""
    pdfium, doc = _open(data)
    try:
        out = RenderedPdf(total=len(doc))
        for i in range(min(out.total, max_pages)):
            out.pages.append(_render_one(pdfium, doc, i, long_side))
        return out
    finally:
        doc.close()


def render_pages(data: bytes, numbers: list[int], long_side: int = LONG_SIDE) -> dict:
    """{αριθμός σελίδας (από 1): εικόνα} μόνο για τις σελίδες που ζητήθηκαν."""
    pdfium, doc = _open(data)
    try:
        total = min(len(doc), MAX_PAGES)
        return {n: _render_one(pdfium, doc, n - 1, long_side) for n in numbers if 1 <= n <= total}
    finally:
        doc.close()


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def jpeg_data_url(img, width: int | None = None, quality: int = 85) -> str:
    import base64

    im = img
    if width and img.width > width:
        im = img.copy()
        im.thumbnail((width, width * 10))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
