"""Σύνδεση μέσω της συνδρομής Claude (Pro/Max) με το Claude Agent SDK.

Το SDK τρέχει το Claude Code CLI που είναι ενσωματωμένο στο πακέτο. Η πιστοποίηση
γίνεται με token που παράγεται μία φορά με `claude setup-token` και αποθηκεύεται
κρυπτογραφημένο στο Windows Credential Manager. Αν δεν έχει οριστεί token, το CLI
χρησιμοποιεί όποια σύνδεση υπάρχει ήδη στον υπολογιστή (π.χ. από το Claude Code).
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
from pathlib import Path

from .base import ImageInput, Provider, ProviderError

_CREATE_NO_WINDOW = 0x08000000
_patched = False


def _hide_console_windows() -> None:
    """Στα Windows, το CLI ανοίγει χωρίς να εμφανίζεται μαύρο παράθυρο κονσόλας."""
    global _patched
    if _patched or os.name != "nt":
        return
    import anyio

    original = anyio.open_process

    async def open_process(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return await original(*args, **kwargs)

    anyio.open_process = open_process
    _patched = True


def bundled_cli_path() -> str | None:
    """Η διαδρομή του Claude Code CLI που συνοδεύει το SDK (για το setup-token)."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    name = "claude.exe" if os.name == "nt" else "claude"
    p = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return str(p) if p.exists() else None


def _friendly(text: str) -> str:
    low = (text or "").lower()
    if any(s in low for s in ("login", "invalid api key", "oauth", "authentication", "unauthorized", "401")):
        return (
            "Δεν έγινε σύνδεση με τη συνδρομή Claude. Ανοίξτε τις Ρυθμίσεις → "
            "«Σύνδεση με συνδρομή» και επικολλήστε νέο token."
        )
    if "rate" in low and "limit" in low or "usage limit" in low or "429" in low:
        return "Εξαντλήθηκε προσωρινά το όριο χρήσης της συνδρομής. Δοκιμάστε ξανά αργότερα."
    return text or "Άγνωστο σφάλμα από τον Claude."


class AgentSdkProvider(Provider):
    key = "agent_sdk"

    def __init__(self, model: str, oauth_token: str = "", timeout_sec: int = 300):
        super().__init__(model, timeout_sec)
        self.oauth_token = oauth_token.strip()

    def _options(self, system: str, schema: dict | None, max_turns: int, errlog: list[str] | None = None):
        try:
            from claude_agent_sdk import ClaudeAgentOptions
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Λείπει το Claude Agent SDK από την εγκατάσταση.") from exc

        env = {
            "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # χωρίς τηλεμετρία/ενημερώσεις του CLI
        }
        if self.oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
            env["ANTHROPIC_API_KEY"] = ""  # να μη χρεωθεί κατά λάθος σε API key
        kwargs = dict(
            model=self.model or None,
            system_prompt=system,
            tools=[],
            allowed_tools=[],
            max_turns=max_turns,
            setting_sources=[],
            cwd=tempfile.gettempdir(),
            env=env,
            stderr=(errlog.append if errlog is not None else (lambda _line: None)),
        )
        if schema is not None:
            kwargs["output_format"] = {"type": "json_schema", "schema": schema}
        return ClaudeAgentOptions(**kwargs)

    async def _run(self, content: list[dict], options, errlog: list[str] | None = None) -> "object":
        from claude_agent_sdk import ResultMessage, query

        _hide_console_windows()

        async def prompts():
            yield {
                "type": "user",
                "message": {"role": "user", "content": content},
                "parent_tool_use_id": None,
            }

        result = None
        try:
            async for msg in query(prompt=prompts(), options=options):
                if isinstance(msg, ResultMessage):
                    result = msg
        except Exception as exc:  # CLI δεν ξεκίνησε, δίκτυο κ.λπ.
            detail = "\n".join((errlog or [])[-8:])
            raise ProviderError(_friendly(detail or str(exc)) + (f"\n\nΛεπτομέρειες: {detail}" if detail else "")) from exc
        if result is None:
            raise ProviderError("Ο Claude δεν επέστρεψε αποτέλεσμα.")
        if result.is_error:
            raise ProviderError(_friendly(result.result or result.subtype))
        return result

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
        errlog: list[str] = []
        options = self._options(system, schema, max_turns=4, errlog=errlog)
        try:
            result = await asyncio.wait_for(self._run(content, options, errlog), timeout=self.timeout_sec)
        except asyncio.TimeoutError as exc:
            raise ProviderError("Ο Claude άργησε υπερβολικά να απαντήσει. Δοκιμάστε ξανά.") from exc
        data = getattr(result, "structured_output", None)
        if not isinstance(data, dict):
            raise ProviderError("Ο Claude δεν επέστρεψε δομημένη απάντηση. Δοκιμάστε ξανά.")
        return data

    async def ping(self) -> str:
        options = self._options("Απάντησε μόνο με τη λέξη ΟΚ.", None, max_turns=1)
        content = [{"type": "text", "text": "Δοκιμή σύνδεσης."}]
        result = await asyncio.wait_for(self._run(content, options), timeout=120)
        return (result.result or "OK").strip()[:80]


def setup_token_command() -> list[str] | None:
    """Εντολή που ανοίγει νέα κονσόλα με `claude setup-token` για σύνδεση στη συνδρομή."""
    cli = bundled_cli_path()
    if not cli:
        return None
    return [cli, "setup-token"]


def launch_setup_token() -> None:
    import subprocess

    cmd = setup_token_command()
    if not cmd:
        raise ProviderError("Δεν βρέθηκε το ενσωματωμένο Claude Code CLI.")
    if os.name == "nt":
        # Νέα ορατή κονσόλα που μένει ανοιχτή (/k) ώστε να αντιγραφεί το token.
        # Τα εξωτερικά εισαγωγικά ""…"" είναι ο ασφαλής τρόπος για διαδρομές με κενά στο cmd.
        title = "Σύνδεση με συνδρομή Claude"
        subprocess.Popen(
            f'cmd.exe /k "chcp 65001 >nul & title {title} & echo. & echo Μόλις εμφανιστεί το token, επιλέξτε το με το ποντίκι, '
            f'πατήστε Ctrl+C και επικολλήστε το στην εφαρμογή. & echo. & "{cmd[0]}" setup-token"',
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal", cmd[0]])
    else:
        subprocess.Popen(["x-terminal-emulator", "-e", " ".join(cmd)])
