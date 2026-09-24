"""Ρυθμίσεις εφαρμογής (settings.json) και μυστικά (Windows Credential Manager μέσω keyring)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from .paths import default_output_dir, user_dir

KEYRING_SERVICE = "Diagonismata"
SECRET_OAUTH = "claude_oauth_token"
SECRET_API_KEY = "anthropic_api_key"

# Τρόποι σύνδεσης. Για νέο τρόπο: νέα κλάση στο app/providers και μια γραμμή εδώ.
PROVIDERS = {
    "agent_sdk": "Συνδρομή Claude (Agent SDK)",
    "api": "Anthropic API (κλειδί API)",
}

# Προτεινόμενα μοντέλα. Τα σύντομα ονόματα (opus/sonnet/haiku) ακολουθούν αυτόματα το νεότερο
# μοντέλο κάθε κατηγορίας στη συνδρομή. Το πεδίο δέχεται και πλήρες όνομα μοντέλου.
MODELS = ["opus", "sonnet", "haiku", "claude-opus-5-5", "claude-sonnet-5"]

# Για το API χρειάζεται πλήρες όνομα· τα σύντομα μεταφράζονται εδώ. Αλλάζει από τις ρυθμίσεις.
DEFAULT_API_ALIASES = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}


@dataclass
class Settings:
    provider: str = "agent_sdk"
    model: str = "opus"
    api_aliases: dict = field(default_factory=lambda: dict(DEFAULT_API_ALIASES))
    template_path: str = ""  # κενό = το ενσωματωμένο πρότυπο
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    footer_text: str = ""  # κενό = κρατάει το υποσέλιδο του προτύπου
    last_title: str = ""
    last_subtitle: str = ""
    sublevel_style: str = "roman"  # "roman" → i), ii) | "bullet" → κουκκίδες
    points_align: str = "right"  # right | left
    max_parallel: int = 3
    timeout_sec: int = 300

    @classmethod
    def load(cls) -> "Settings":
        path = user_dir() / "settings.json"
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = user_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def api_model(self) -> str:
        return self.api_aliases.get(self.model, self.model)

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, data: dict) -> None:
        known = {f.name: f for f in fields(self)}
        for k, v in data.items():
            if k in known:
                if isinstance(getattr(self, k), bool):
                    v = bool(v)
                elif isinstance(getattr(self, k), int):
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        continue
                setattr(self, k, v)


# ---------------------------------------------------------------- μυστικά

def _keyring():
    try:
        import keyring  # noqa: WPS433

        return keyring
    except Exception:  # pragma: no cover - keyring λείπει μόνο σε δοκιμές
        return None


def get_secret(name: str) -> str:
    kr = _keyring()
    if kr is None:
        return ""
    try:
        return kr.get_password(KEYRING_SERVICE, name) or ""
    except Exception:
        return ""


def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    if kr is None:
        raise RuntimeError("Δεν είναι διαθέσιμη η ασφαλής αποθήκευση κωδικών.")
    if value:
        kr.set_password(KEYRING_SERVICE, name, value)
    else:
        try:
            kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass
