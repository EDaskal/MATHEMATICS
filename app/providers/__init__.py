"""Επιλογή provider από τις ρυθμίσεις. Για νέο τρόπο σύνδεσης προσθέστε κλάδο εδώ."""

from __future__ import annotations

from ..config import SECRET_API_KEY, SECRET_OAUTH, Settings, get_secret
from .base import ImageInput, Provider, ProviderError


def get_provider(settings: Settings) -> Provider:
    if settings.provider == "api":
        from .anthropic_api import AnthropicApiProvider

        return AnthropicApiProvider(settings.api_model(), get_secret(SECRET_API_KEY), settings.timeout_sec)
    if settings.provider == "agent_sdk":
        from .agent_sdk import AgentSdkProvider

        return AgentSdkProvider(settings.model, get_secret(SECRET_OAUTH), settings.timeout_sec)
    raise ProviderError(f"Άγνωστος τρόπος σύνδεσης: {settings.provider}")


__all__ = ["get_provider", "Provider", "ProviderError", "ImageInput"]
