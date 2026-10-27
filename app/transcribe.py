"""Μεταγραφή φωτογραφιών θεμάτων σε δομημένο JSON μέσω του επιλεγμένου provider."""

from __future__ import annotations

import asyncio
import io
import re
from typing import Awaitable, Callable

from .paths import rules_path
from .providers import ImageInput, Provider, ProviderError

# --------------------------------------------------------------------------- σχήμα JSON

_CHOICES = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "columns": {"type": "integer"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["columns", "options"],
            "additionalProperties": False,
        },
    ]
}


def _item(depth: int) -> dict:
    props = {
        "label": {"type": "string"},
        "text": {"type": "string"},
        "choices": _CHOICES,
    }
    required = ["label", "text", "choices"]
    if depth > 1:
        props["items"] = {"type": "array", "items": _item(depth - 1)}
        required.append("items")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


FIGURE_SCHEMA = {
    "anyOf": [
        {"type": "null"},
        {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "spec_json": {"type": "string"},
                "crop": {"type": "array", "items": {"type": "number"}},
                "crop_page": {"type": "integer"},
                "confidence": {"type": "string", "enum": ["high", "low"]},
            },
            "required": ["description", "spec_json", "crop", "crop_page", "confidence"],
            "additionalProperties": False,
        },
    ]
}

EXERCISE_SCHEMA = {
    "type": "object",
    "properties": {
        "source_label": {"type": "string"},
        "stem": {"type": "string"},
        "items": {"type": "array", "items": _item(3)},
        "closing": {"type": "string"},
        "figure": FIGURE_SCHEMA,
        "uncertain": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["source_label", "stem", "items", "closing", "figure", "uncertain"],
    "additionalProperties": False,
}

USER_TEXT = (
    "Μετάγραψε την άσκηση της εικόνας σύμφωνα με τους κανόνες. "
    "Επίστρεψε μόνο τη δομή JSON που ζητείται."
)

USER_TEXT_PAGES = (
    "Οι {n} εικόνες είναι ΔΙΑΔΟΧΙΚΕΣ ΣΕΛΙΔΕΣ της ΙΔΙΑΣ άσκησης, με τη σειρά που δίνονται "
    "(σελίδα 1, σελίδα 2, …). Το κείμενο της κάθε σελίδας συνεχίζει εκεί που σταμάτησε η "
    "προηγούμενη — μια πρόταση, εξίσωση ή ερώτημα μπορεί να κόβεται στην αλλαγή σελίδας. "
    "Μετάγραψε ολόκληρη την άσκηση ως ΕΝΙΑΙΟ κείμενο σύμφωνα με τους κανόνες, χωρίς να "
    "επαναλάβεις ή να παραλείψεις τίποτα στα σημεία ένωσης. Αγνόησε κεφαλίδες/υποσέλιδα/αριθμούς "
    "σελίδων του βιβλίου και ό,τι ανήκει σε άλλη άσκηση. Επίστρεψε μόνο τη δομή JSON που ζητείται."
)


def user_text(n_pages: int) -> str:
    return USER_TEXT if n_pages <= 1 else USER_TEXT_PAGES.format(n=n_pages)


# --------------------------------------------------------------------------- Corpus: πολλές ασκήσεις ανά εικόνα

CORPUS_SCHEMA = {
    "type": "object",
    "properties": {"exercises": {"type": "array", "items": EXERCISE_SCHEMA}},
    "required": ["exercises"],
    "additionalProperties": False,
}

CORPUS_TEXT = (
    "Οι ασκήσεις θα μπουν σε ΣΥΛΛΟΓΗ ΑΣΚΗΣΕΩΝ (corpus) της καθηγήτριας. {pages}"
    "Η εικόνα μπορεί να περιέχει ΜΙΑ ή ΠΕΡΙΣΣΟΤΕΡΕΣ αυτοτελείς ασκήσεις: επίστρεψε μία εγγραφή στο "
    "`exercises` για καθεμία, με τη σειρά ανάγνωσης (από πάνω προς τα κάτω, πρώτα η αριστερή στήλη).\n"
    "ΠΡΟΣΟΧΗ στον διαχωρισμό: αριθμημένες περιπτώσεις (1, 2, 3, … ή α, β, … ή i, ii, …) που μοιράζονται "
    "την ΙΔΙΑ εκφώνηση (π.χ. «Να βρεθεί ο συντελεστής διεύθυνσης όταν:» και από κάτω 16 περιπτώσεις) "
    "είναι ΕΡΩΤΗΜΑΤΑ (`items`) της ΙΔΙΑΣ άσκησης — όχι χωριστές ασκήσεις. Νέα άσκηση ξεκινά μόνο όταν "
    "υπάρχει νέα, δική της εκφώνηση (συνήθως με δικό της αριθμό άσκησης του βιβλίου).\n"
    "Όταν μια περίπτωση έχει στοιχεία σε ΣΤΗΛΕΣ (π.χ. «Α(3, 4),      Β(6, 7)» ή «Α(3, 2)      x−3y+5=0»), "
    "γράψ' τα στο ίδιο ερώτημα και βάλε ανάμεσα στις στήλες το σύμβολο ⇥ (μία φορά, έξω από τα $): "
    "«$\\mathrm{Α}(3,4)$, ⇥ $\\mathrm{Β}(6,7)$». Κράτησε τα σημεία στίξης όπως είναι στο πρωτότυπο "
    "(π.χ. το κόμμα μετά την πρώτη στήλη, αν υπάρχει). Αν ανάμεσα υπάρχει λέξη (π.χ. «και»), δεν είναι "
    "στήλες: κράτησέ τη ακριβώς όπως είναι, χωρίς ⇥.\n"
    "Ονόματα σημείων/ευθειών πάντα με ΕΛΛΗΝΙΚΑ κεφαλαία (Α, Β, Γ, Ο, Ρ, Ε…), ποτέ λατινικά.\n"
    "Αγνόησε τίτλους ενοτήτων του βιβλίου, κεφαλίδες, αριθμούς σελίδων και ό,τι είναι μισοκομμένο στις "
    "άκρες της εικόνας (αν κόβεται κομμάτι άσκησης, γράψε ό,τι φαίνεται και σημείωσέ το στο `uncertain`). "
    "Αν η εικόνα δεν περιέχει καμία άσκηση, επίστρεψε κενό `exercises`. Επίστρεψε μόνο τη δομή JSON που ζητείται."
)


def corpus_text(n_pages: int) -> str:
    pages = (
        ""
        if n_pages <= 1
        else f"Οι {n_pages} εικόνες είναι ΔΙΑΔΟΧΙΚΕΣ ΣΕΛΙΔΕΣ, με τη σειρά που δίνονται· μια άσκηση μπορεί "
        "να συνεχίζει από τη μία σελίδα στην επόμενη — τότε είναι ΜΙΑ άσκηση, ενιαία. "
    )
    return CORPUS_TEXT.replace("{pages}", pages)


async def transcribe_corpus(provider: Provider, pages: list[bytes] | bytes, rules: str | None = None) -> list[dict]:
    """Μεταγραφή μιας εικόνας (ή διαδοχικών σελίδων) που μπορεί να έχει πολλές ασκήσεις."""
    if isinstance(pages, (bytes, bytearray)):
        pages = [bytes(pages)]
    images = [prepare_image(p) for p in pages]
    rules = rules or load_rules()
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            data = await provider.extract_json(rules, corpus_text(len(images)), images, CORPUS_SCHEMA)
            exs = data.get("exercises") if isinstance(data, dict) else None
            if not isinstance(exs, list):
                raise ProviderError("Μη αναμενόμενη απάντηση από τον Claude.")
            return [normalize(e, len(images)) for e in exs if isinstance(e, dict)]
        except ProviderError as exc:
            last_exc = exc
            msg = str(exc)
            if "σύνδεση" in msg or "κλειδί" in msg or "όριο" in msg.lower():
                break
    raise last_exc if last_exc else ProviderError("Αποτυχία μεταγραφής.")


def load_rules() -> str:
    return rules_path().read_text(encoding="utf-8")


# --------------------------------------------------------------------------- εικόνες

MAX_SIDE = 2000  # px — αρκετό για ανάγνωση, μικρότερο κόστος/χρόνος


def prepare_image(data: bytes) -> ImageInput:
    """Κανονικοποίηση εικόνας: PNG/JPEG, όχι υπερβολικά μεγάλη."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img.load()
    try:  # φωτογραφίες κινητού: περιστροφή σύμφωνα με τον προσανατολισμό EXIF
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        pass
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img.convert("RGB"))
        img = bg
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return ImageInput(out.getvalue(), "image/png")


# --------------------------------------------------------------------------- κανονικοποίηση

COLSEP = "⇥"  # διαχωριστικό στηλών μέσα σε ερώτημα (στο Word γίνεται στηλοθέτης)
_COLSEP_RE = re.compile(r"[ \t]*(?:⇥|\t)[ \t]*")


def norm_text(text) -> str:
    """Στήλες: ⇥ ή TAB → « ⇥ » (ορατό και επεξεργάσιμο στην οθόνη ελέγχου)."""
    return _COLSEP_RE.sub(f" {COLSEP} ", str(text or ""))


def _norm_item(it: dict, depth: int = 1) -> dict:
    ch = it.get("choices")
    if isinstance(ch, dict) and ch.get("options"):
        cols = int(ch.get("columns") or 2)
        ch = {"columns": 4 if cols >= 4 else 2, "options": [str(o) for o in ch["options"]]}
    else:
        ch = None
    return {
        "label": str(it.get("label", "")).strip().strip(").").strip(),
        "text": norm_text(it.get("text", "")),
        "choices": ch,
        "items": [_norm_item(x, depth + 1) for x in (it.get("items") or [])] if depth < 3 else [],
    }


def normalize(data: dict, n_pages: int = 1) -> dict:
    fig = data.get("figure")
    if isinstance(fig, dict) and (fig.get("spec_json") or fig.get("description")):
        crop = fig.get("crop") or [0, 0, 1, 1]
        if not (isinstance(crop, list) and len(crop) == 4):
            crop = [0, 0, 1, 1]
        fig = {
            "description": str(fig.get("description", "")),
            "spec_json": str(fig.get("spec_json", "")),
            "crop": [min(1.0, max(0.0, float(c))) for c in crop],
            "crop_page": min(max(0, int(fig.get("crop_page") or 0)), max(0, n_pages - 1)),
            "confidence": fig.get("confidence", "high"),
            "use_original": False,
        }
    else:
        fig = None
    return {
        "source_label": str(data.get("source_label", "")),
        "stem": norm_text(data.get("stem", "")),
        "items": [_norm_item(x) for x in (data.get("items") or [])],
        "closing": norm_text(data.get("closing", "")),
        "figure": fig,
        "uncertain": [str(u) for u in (data.get("uncertain") or []) if str(u).strip()],
    }


# --------------------------------------------------------------------------- εκτέλεση

async def transcribe_one(provider: Provider, pages: list[bytes] | bytes, rules: str | None = None) -> dict:
    """Μεταγραφή μιας άσκησης· `pages` = μία ή περισσότερες διαδοχικές σελίδες (εικόνες)."""
    if isinstance(pages, (bytes, bytearray)):
        pages = [bytes(pages)]
    images = [prepare_image(p) for p in pages]
    rules = rules or load_rules()
    last_exc: Exception | None = None
    for _attempt in range(2):  # μία επανάληψη για προσωρινά σφάλματα
        try:
            data = await provider.extract_json(rules, user_text(len(images)), images, EXERCISE_SCHEMA)
            return normalize(data, len(images))
        except ProviderError as exc:
            last_exc = exc
            msg = str(exc)
            if "σύνδεση" in msg or "κλειδί" in msg or "όριο" in msg.lower():
                break
    raise last_exc if last_exc else ProviderError("Αποτυχία μεταγραφής.")


async def transcribe_many(
    provider: Provider,
    jobs: list[tuple[str, list[bytes] | bytes]],
    max_parallel: int = 3,
    on_done: Callable[[str, dict | None, str | None], Awaitable[None] | None] | None = None,
    corpus: bool = False,
) -> dict[str, dict | str]:
    """Μεταγράφει πολλές ασκήσεις παράλληλα. Επιστρέφει {id: αποτέλεσμα ή μήνυμα σφάλματος}.

    Με `corpus=True` κάθε αποτέλεσμα είναι λίστα ασκήσεων (μία εικόνα → πολλές ασκήσεις).
    """
    rules = load_rules()
    sem = asyncio.Semaphore(max(1, max_parallel))
    results: dict[str, dict | str] = {}
    fn = transcribe_corpus if corpus else transcribe_one

    async def run(job_id: str, data):
        async with sem:
            try:
                res = await fn(provider, data, rules)
                results[job_id] = res
                err = None
            except Exception as exc:  # noqa: BLE001 — κάθε σφάλμα πάει στον χρήστη
                res, err = None, str(exc) or exc.__class__.__name__
                results[job_id] = err
            if on_done:
                r = on_done(job_id, res, err)
                if asyncio.iscoroutine(r):
                    await r

    await asyncio.gather(*(run(j, d) for j, d in jobs))
    return results
