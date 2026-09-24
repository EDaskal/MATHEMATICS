"""Σύνδεση με το Anthropic API (κλειδί από το Claude Console, χρέωση ανά χρήση)."""

from __future__ import annotations

import base64

from .base import ImageInput, Provider, ProviderError

_TOOL_NAME = "submit_transcription"


def _friendly(exc: Exception) -> str:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return "Το κλειδί API δεν είναι έγκυρο. Ελέγξτε το στις Ρυθμίσεις."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Το κλειδί API δεν έχει πρόσβαση σε αυτό το μοντέλο."
    if isinstance(exc, anthropic.NotFoundError):
        return "Το μοντέλο δεν βρέθηκε. Ελέγξτε το όνομα μοντέλου στις Ρυθμίσεις."
    if isinstance(exc, anthropic.RateLimitError):
        return "Όριο κλήσεων API. Δοκιμάστε ξανά σε λίγο."
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code == 400 and "credit" in str(exc).lower():
        return "Δεν υπάρχει υπόλοιπο στον λογαριασμό API (Claude Console → Billing)."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Δεν υπάρχει σύνδεση με το internet ή με την Anthropic."
    return str(exc)


class AnthropicApiProvider(Provider):
    key = "api"

    def __init__(self, model: str, api_key: str, timeout_sec: int = 300):
        super().__init__(model, timeout_sec)
        if not api_key:
            raise ProviderError("Δεν έχει οριστεί κλειδί API. Προσθέστε το στις Ρυθμίσεις.")
        self.api_key = api_key.strip()

    def _client(self):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Λείπει η βιβλιοθήκη anthropic από την εγκατάσταση.") from exc
        return anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout_sec, max_retries=2)

    async def extract_json(self, system, user_text, images: list[ImageInput], schema):
        content: list[dict] = []
        for img in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": user_text})
        client = self._client()
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": content}],
                tools=[
                    {
                        "name": _TOOL_NAME,
                        "description": "Υποβολή της μεταγραφής στη δομή που ζητήθηκε.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )
        except Exception as exc:
            raise ProviderError(_friendly(exc)) from exc
        finally:
            await client.close()
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and block.name == _TOOL_NAME:
                if isinstance(block.input, dict):
                    return block.input
        raise ProviderError("Ο Claude δεν επέστρεψε δομημένη απάντηση. Δοκιμάστε ξανά.")

    async def ping(self) -> str:
        client = self._client()
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=20,
                messages=[{"role": "user", "content": "Απάντησε μόνο με τη λέξη ΟΚ."}],
            )
        except Exception as exc:
            raise ProviderError(_friendly(exc)) from exc
        finally:
            await client.close()
        return "".join(getattr(b, "text", "") for b in resp.content).strip()[:80] or "OK"
