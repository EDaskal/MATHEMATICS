"""Αυτοέλεγχος εγκατάστασης: χτίζει ένα μικρό διαγώνισμα χωρίς κλήση στον Claude.

Ελέγχει ότι στο πακέτο υπάρχουν και λειτουργούν: pandoc, matplotlib (σχήματα),
μετατροπή εξισώσεων, ενσωμάτωση SVG, το Claude Code CLI του Agent SDK και το keyring.

    Diagonismata.exe --selftest=C:\\path\\report.txt
"""

from __future__ import annotations

import json
import tempfile
import traceback
import zipfile
from pathlib import Path


def run(report_path: str | None = None) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, fn):
        nonlocal ok
        try:
            res = fn()
            lines.append(f"OK   {name}: {res}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL {name}: {exc}")
            lines.append(traceback.format_exc())

    from .paths import pandoc_path, resource

    check("pandoc", lambda: pandoc_path())
    check("rules", lambda: len(resource("resources", "rules.md").read_text(encoding="utf-8")))
    check("template", lambda: resource("resources", "default_template.docx").stat().st_size)
    check("ui", lambda: resource("ui", "index.html").exists() and resource("ui", "vendor", "katex", "katex.min.js").exists())

    def cli():
        from .providers.agent_sdk import bundled_cli_path

        p = bundled_cli_path()
        if not p:
            raise RuntimeError("λείπει το ενσωματωμένο Claude Code CLI")
        return p

    check("claude-cli", cli)

    def keyring_backend():
        import os

        import keyring

        kr = keyring.get_keyring()
        name = f"{type(kr).__module__}.{type(kr).__name__}"
        if os.name == "nt" and "fail" in name.lower():
            raise RuntimeError(f"δεν βρέθηκε ασφαλής αποθήκευση κωδικών ({name})")
        return name

    check("keyring", keyring_backend)

    def anthropic_sdk():
        import anthropic

        return anthropic.__version__

    check("anthropic", anthropic_sdk)

    def build():
        from .docx_build import build_docx
        from .figures import render_to_files

        exam = json.loads(resource("resources", "selftest_exam.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            for ti, theme in enumerate(exam["themes"]):
                for ei, ex in enumerate(theme["exercises"]):
                    spec = ex.pop("figure_spec", None)
                    if spec:
                        info = render_to_files(spec, tdp / f"fig{ti}{ei}")
                        if info["warnings"]:
                            raise RuntimeError("έλεγχος σχήματος: " + "; ".join(info["warnings"]))
                        ex["figure_render"] = info
            out = tdp / "selftest.docx"
            res = build_docx(exam, out, footer_text=exam["footer"])
            expected, found = res["math"]
            if expected <= 0 or expected != found:
                raise RuntimeError(f"εξισώσεις: γράφτηκαν {expected}, διαβάστηκαν {found}")
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
                if not any(n.endswith(".svg") for n in names):
                    raise RuntimeError("δεν ενσωματώθηκε SVG")
                doc = z.read("word/document.xml").decode("utf-8")
                if "svgBlip" not in doc:
                    raise RuntimeError("λείπει το svgBlip")
                footer = "".join(z.read(n).decode("utf-8") for n in names if n.startswith("word/footer"))
                if "Αυτοέλεγχος" not in footer:
                    raise RuntimeError("δεν άλλαξε το υποσέλιδο")
            return f"{expected} εξισώσεις, {len(res['warnings'])} προειδοποιήσεις"

    check("build-docx", build)

    lines.append("RESULT: " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if ok else 1
