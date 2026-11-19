"""Τοπικό μοντέλο ως «provider» (1.8.0) και μικτή λειτουργία (τοπικό + Claude για τα σχήματα).

Η υπόλοιπη εφαρμογή ζητά από κάθε provider «JSON με αυτό το σχήμα». Το τοπικό μοντέλο ξέρει μόνο να
διαβάζει μια σελίδα σε κείμενο, οπότε:

- για **μεταγραφή άσκησης / Corpus** (`EXERCISE_SCHEMA`, `CORPUS_SCHEMA`): διαβάζει κάθε σελίδα, καθαρίζει
  και χτίζει τη δομή με το `local_parse`. Όπου το μοντέλο σημείωσε σχήμα, μπαίνει η **πρωτότυπη εικόνα**
  (περικοπή που εκτιμάται αυτόματα) — εκτός αν είναι ενεργή η μικτή λειτουργία, οπότε ο Claude
  ξανασχεδιάζει το σχήμα όπως πριν·
- για κάθε άλλη ερώτηση (έλεγχος σχήματος με πλέγμα, «σχήμα από τη λύση»): μόνο με Claude.

Κάθε άσκηση που μεταγράφηκε τοπικά έχει `review: True` → η οθόνη τη δείχνει «Προς έλεγχο».
"""

from __future__ import annotations

import asyncio
from typing import Callable

from .base import ImageInput, Provider, ProviderError

NEEDS_CLAUDE = ("Αυτό χρειάζεται τον Claude (σύνδεση στο internet). Με το τοπικό μοντέλο μόνο, τα σχήματα "
                "μπαίνουν ως πρωτότυπη εικόνα. Ενεργοποιήστε τη «Μικτή λειτουργία» στις Ρυθμίσεις.")

REVIEW_NOTE = "Μεταγραφή με τοπικό μοντέλο: ελέγξτε τύπους και ορθογραφία πριν από το Word."


def _kind(schema: dict) -> str | None:
    from ..transcribe import CORPUS_SCHEMA, EXERCISE_SCHEMA

    if schema is EXERCISE_SCHEMA:
        return "exercise"
    if schema is CORPUS_SCHEMA:
        return "corpus"
    props = (schema or {}).get("properties") or {}
    if set(props) == {"exercises"}:
        return "corpus"
    if {"stem", "items", "source_label"} <= set(props):
        return "exercise"
    return None


def _friendly(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


class LocalProvider(Provider):
    key = "local"
    _lock: asyncio.Lock | None = None  # μία σελίδα τη φορά (η μηχανή δουλεύει σειριακά)

    def __init__(self, model_key: str, timeout_sec: int = 300, threads: int = 0):
        super().__init__(model_key, max(timeout_sec, 600))
        self.threads = threads

    def _get_lock(self) -> asyncio.Lock:
        # ένα lock ανά event loop (κάθε μεταγραφή τρέχει σε δικό της asyncio.run)
        loop = asyncio.get_running_loop()
        lk = getattr(loop, "_diag_local_lock", None)
        if lk is None:
            lk = asyncio.Lock()
            setattr(loop, "_diag_local_lock", lk)
        return lk

    async def read_pages(self, images: list[ImageInput]) -> list[str]:
        from .. import local_llm

        out = []
        for img in images:
            async with self._get_lock():
                try:
                    out.append(await asyncio.to_thread(local_llm.ocr, self.model, img.data, self.timeout_sec, self.threads))
                except local_llm.LocalError as exc:
                    raise ProviderError(_friendly(exc)) from exc
        return out

    async def transcribe(self, images: list[ImageInput], corpus: bool) -> list[dict]:
        """Σελίδες → ασκήσεις (μορφή πριν από το `normalize`), με σημάδι `_fig_page` όπου υπάρχει σχήμα."""
        from ..local_parse import figure_region, to_exercises

        texts = await self.read_pages(images)
        exs: list[dict] = []
        for page_i, txt in enumerate(texts):
            page_exs = to_exercises(txt, corpus=corpus)
            for ex in page_exs:
                ex["_fig_page"] = page_i if ex.get("_fig") else None
            if not corpus and exs:
                # ίδια άσκηση σε πολλές σελίδες: ενώνουμε τη συνέχεια
                base, more = exs[0], page_exs[0]
                if more.get("stem"):
                    if base["items"]:
                        base["items"][-1]["text"] = (base["items"][-1]["text"] + " " + more["stem"]).strip()
                    else:
                        base["stem"] = (base["stem"] + "\n" + more["stem"]).strip()
                base["items"] += more["items"]
                if more.get("_fig") and base.get("_fig_page") is None:
                    base["_fig"], base["_fig_page"] = True, page_i
                continue
            exs += page_exs
        if not corpus and not exs:
            exs = [{"source_label": "", "stem": "", "items": [], "closing": "", "figure": None, "uncertain": []}]
        for ex in exs:
            ex["review"] = True  # η οθόνη δείχνει «Προς έλεγχο» (REVIEW_NOTE)
            ex.setdefault("uncertain", [])
            if ex.get("_fig"):
                page = ex.get("_fig_page") or 0
                crop = await asyncio.to_thread(figure_region, images[page].data)
                ex["figure"] = {
                    "description": "Σχήμα από την εικόνα (πρωτότυπη περικοπή — ελέγξτε τα όρια).",
                    "spec_json": "", "crop": crop, "crop_page": page, "confidence": "high", "use_original": True,
                }
                ex["uncertain"].append("Το σχήμα μπήκε ως πρωτότυπη εικόνα· ελέγξτε την περικοπή.")
            for k in ("_fig", "_fig_page"):
                ex.pop(k, None)
        return exs

    async def extract_json(self, system, user_text, images: list[ImageInput], schema, count_images=None):
        kind = _kind(schema)
        if kind is None:
            raise ProviderError(NEEDS_CLAUDE)
        exs = await self.transcribe(images, corpus=kind == "corpus")
        return {"exercises": exs} if kind == "corpus" else exs[0]

    async def ping(self) -> str:
        from .. import local_llm

        def run():
            import io

            from PIL import Image, ImageDraw

            img = Image.new("RGB", (420, 80), "white")
            ImageDraw.Draw(img).text((10, 30), "x + 2 = 5", fill="black")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return local_llm.ocr(self.model, buf.getvalue(), 300, self.threads)

        try:
            await asyncio.to_thread(run)
        except local_llm.LocalError as exc:
            raise ProviderError(_friendly(exc)) from exc
        return f"τοπικό μοντέλο {local_llm.CATALOG[self.model]['short']} έτοιμο"


class HybridProvider(Provider):
    """Μικτή λειτουργία: κείμενο τοπικά· σχήματα (και ό,τι άλλο) με τον Claude, αν είναι διαθέσιμος."""

    key = "hybrid"

    def __init__(self, local: LocalProvider, claude_factory: Callable[[], Provider]):
        super().__init__(local.model, local.timeout_sec)
        self.local = local
        self._factory = claude_factory
        self._claude: Provider | None = None
        self._claude_error = ""

    def claude(self) -> Provider:
        if self._claude is None:
            try:
                self._claude = self._factory()
            except ProviderError as exc:
                self._claude_error = str(exc)
                raise
        return self._claude

    async def _redraw(self, ex: dict, images: list[ImageInput], system: str) -> None:
        from ..figure_check import exercise_text, read_figure

        fig = ex.get("figure") or {}
        if not fig.get("use_original"):
            return
        label = ex.get("source_label") or ""
        request = f"το σχήμα της άσκησης {label}".strip() if label else "το σχήμα της άσκησης"
        try:
            claude = self.claude()
            got = await read_figure(claude, [im.data for im in images], request,
                                    exercise_text({**ex, "figure": None}), system)
        except Exception as exc:  # noqa: BLE001 — χωρίς internet / όριο: μένει η πρωτότυπη εικόνα
            ex["uncertain"].append(f"Ο Claude δεν ήταν διαθέσιμος για επανασχεδίαση του σχήματος ({exc}).")
            return
        if got.get("error"):
            ex["uncertain"].append("Ο Claude δεν κατάφερε να ξανασχεδιάσει το σχήμα: " + got["error"])
            return
        ex["figure"] = got
        ex["uncertain"] = [u for u in ex["uncertain"] if "περικοπή" not in u]

    async def extract_json(self, system, user_text, images: list[ImageInput], schema, count_images=None):
        kind = _kind(schema)
        if kind is None:  # έλεγχος σχήματος, σχήμα από τη λύση κ.λπ.
            return await self.claude().extract_json(system, user_text, images, schema, count_images=count_images)
        exs = await self.local.transcribe(images, corpus=kind == "corpus")
        for ex in exs:
            await self._redraw(ex, images, system)
        return {"exercises": exs} if kind == "corpus" else exs[0]

    async def ping(self) -> str:
        text = await self.local.ping()
        try:
            c = await self.claude().ping()
            return f"{text} · Claude για τα σχήματα: {c}"
        except ProviderError as exc:
            return f"{text} · Claude για τα σχήματα: μη διαθέσιμος ({exc})"

    async def refresh_usage(self):
        c = self.claude()
        if hasattr(c, "refresh_usage"):
            return await c.refresh_usage()
        raise ProviderError("Τα όρια φαίνονται μόνο με σύνδεση «συνδρομή Claude».")
