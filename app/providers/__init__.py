"""Επιλογή provider από τις ρυθμίσεις. Για νέο τρόπο σύνδεσης προσθέστε κλάδο εδώ."""

from __future__ import annotations

from ..config import SECRET_API_KEY, SECRET_OAUTH, Settings, get_secret
from .base import ImageInput, Provider, ProviderError


def get_claude_provider(settings: Settings, kind: str | None = None) -> Provider:
    """Σύνδεση με τον Claude (συνδρομή ή κλειδί API)· `kind` αλλιώς από τις ρυθμίσεις."""
    kind = kind or settings.provider
    if kind == "api":
        from .anthropic_api import AnthropicApiProvider

        return AnthropicApiProvider(settings.api_model(), get_secret(SECRET_API_KEY), settings.timeout_sec)
    if kind == "agent_sdk":
        from .agent_sdk import AgentSdkProvider

        return AgentSdkProvider(settings.model, get_secret(SECRET_OAUTH), settings.timeout_sec)
    raise ProviderError(f"Άγνωστος τρόπος σύνδεσης: {kind}")


def get_provider(settings: Settings) -> Provider:
    if settings.provider == "local":  # 1.8.0: τοπικό μοντέλο, προαιρετικά με Claude για τα σχήματα
        from .local import HybridProvider, LocalProvider

        local = LocalProvider(settings.local_model, settings.timeout_sec, settings.local_threads)
        if settings.local_hybrid:
            return HybridProvider(local, lambda: get_claude_provider(settings, settings.local_claude))
        return local
    return get_claude_provider(settings)


__all__ = ["get_provider", "get_claude_provider", "Provider", "ProviderError", "ImageInput"]
