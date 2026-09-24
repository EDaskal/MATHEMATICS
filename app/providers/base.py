"""Κοινή διεπαφή για κάθε τρόπο σύνδεσης με τον Claude.

Κάθε provider κάνει ένα πράγμα: παίρνει οδηγίες, κείμενο και εικόνες και επιστρέφει
ένα αντικείμενο JSON που ακολουθεί το δοσμένο σχήμα. Όλη η υπόλοιπη λογική της
εφαρμογής δεν ξέρει ποιος provider χρησιμοποιείται.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderError(RuntimeError):
    """Σφάλμα με μήνυμα κατάλληλο για τον χρήστη (στα ελληνικά)."""


@dataclass
class ImageInput:
    data: bytes
    media_type: str  # image/png, image/jpeg, image/webp, image/gif


class Provider:
    key: str = ""

    def __init__(self, model: str, timeout_sec: int = 300):
        self.model = model
        self.timeout_sec = timeout_sec

    async def extract_json(
        self,
        system: str,
        user_text: str,
        images: list[ImageInput],
        schema: dict,
    ) -> dict:
        raise NotImplementedError

    async def ping(self) -> str:
        """Σύντομη δοκιμή σύνδεσης. Επιστρέφει κείμενο επιβεβαίωσης ή σηκώνει ProviderError."""
        raise NotImplementedError
