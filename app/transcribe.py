"""Μεταγραφή φωτογραφιών θεμάτων σε δομημένο JSON μέσω του επιλεγμένου provider."""

from __future__ import annotations

import asyncio
import io
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
                "confidence": {"type": "string", "enum": ["high", "low"]},
            },
            "required": ["description", "spec_json", "crop", "confidence"],
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


def load_rules() -> str:
    return rules_path().read_text(encoding="utf-8")


# --------------------------------------------------------------------------- εικόνες

MAX_SIDE = 2000  # px — αρκετό για ανάγνωση, μικρότερο κόστος/χρόνος


def prepare_image(data: bytes) -> ImageInput:
    """Κανονικοποίηση εικόνας: PNG/JPEG, όχι υπερβολικά μεγάλη."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.load()
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

def _norm_item(it: dict, depth: int = 1) -> dict:
    ch = it.get("choices")
    if isinstance(ch, dict) and ch.get("options"):
        cols = int(ch.get("columns") or 2)
        ch = {"columns": 4 if cols >= 4 else 2, "options": [str(o) for o in ch["options"]]}
    else:
        ch = None
    return {
        "label": str(it.get("label", "")).strip().strip(").").strip(),
        "text": str(it.get("text", "")),
        "choices": ch,
        "items": [_norm_item(x, depth + 1) for x in (it.get("items") or [])] if depth < 3 else [],
    }


def normalize(data: dict) -> dict:
    fig = data.get("figure")
    if isinstance(fig, dict) and (fig.get("spec_json") or fig.get("description")):
        crop = fig.get("crop") or [0, 0, 1, 1]
        if not (isinstance(crop, list) and len(crop) == 4):
            crop = [0, 0, 1, 1]
        fig = {
            "description": str(fig.get("description", "")),
            "spec_json": str(fig.get("spec_json", "")),
            "crop": [min(1.0, max(0.0, float(c))) for c in crop],
            "confidence": fig.get("confidence", "high"),
            "use_original": False,
        }
    else:
        fig = None
    return {
        "source_label": str(data.get("source_label", "")),
        "stem": str(data.get("stem", "")),
        "items": [_norm_item(x) for x in (data.get("items") or [])],
        "closing": str(data.get("closing", "")),
        "figure": fig,
        "uncertain": [str(u) for u in (data.get("uncertain") or []) if str(u).strip()],
    }


# --------------------------------------------------------------------------- εκτέλεση

async def transcribe_one(provider: Provider, image_bytes: bytes, rules: str | None = None) -> dict:
    img = prepare_image(image_bytes)
    rules = rules or load_rules()
    last_exc: Exception | None = None
    for _attempt in range(2):  # μία επανάληψη για προσωρινά σφάλματα
        try:
            data = await provider.extract_json(rules, USER_TEXT, [img], EXERCISE_SCHEMA)
            return normalize(data)
        except ProviderError as exc:
            last_exc = exc
            msg = str(exc)
            if "σύνδεση" in msg or "κλειδί" in msg or "όριο" in msg.lower():
                break
    raise last_exc if last_exc else ProviderError("Αποτυχία μεταγραφής.")


async def transcribe_many(
    provider: Provider,
    jobs: list[tuple[str, bytes]],
    max_parallel: int = 3,
    on_done: Callable[[str, dict | None, str | None], Awaitable[None] | None] | None = None,
) -> dict[str, dict | str]:
    """Μεταγράφει πολλές εικόνες παράλληλα. Επιστρέφει {id: αποτέλεσμα ή μήνυμα σφάλματος}."""
    rules = load_rules()
    sem = asyncio.Semaphore(max(1, max_parallel))
    results: dict[str, dict | str] = {}

    async def run(job_id: str, data: bytes):
        async with sem:
            try:
                res = await transcribe_one(provider, data, rules)
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
