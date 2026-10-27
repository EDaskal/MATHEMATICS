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

    def version_docs():
        from . import __version__
        from .paths import build_info, doc_path
        from .updates import parse_version

        missing = [n for n in ("CHANGELOG.md", "docs/user-guide.md") if not doc_path(n)]
        if missing:
            raise RuntimeError("λείπουν: " + ", ".join(missing))
        if parse_version(__version__) == (0, 0, 0):
            raise RuntimeError("άκυρος αριθμός έκδοσης")
        return f"{__version__}, build {build_info().get('run', 'τοπικό')}"

    check("version-docs", version_docs)

    def fonts():
        import os

        from .fonts import font_dirs, scan

        r = scan(refresh=True)
        if r["count"] == 0 or not r["text"]:
            raise RuntimeError(f"δεν βρέθηκαν γραμματοσειρές στους φακέλους {font_dirs()}")
        if os.name == "nt" and not any(f["greek"] for f in r["text"]):
            raise RuntimeError("καμία γραμματοσειρά με ελληνικά — πρόβλημα στην ανάγνωση των γραμματοσειρών")
        cm = "ναι" if any(n.lower() == "cambria math" for n in r["math"]) else "όχι"
        return f"{len(r['text'])} οικογένειες, {len(r['math'])} μαθηματικών (Cambria Math: {cm})"

    check("fonts", fonts)

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
            from .docx_build import PageSetup

            out = tdp / "selftest.docx"
            res = build_docx(exam, out, setup=PageSetup("Cambria", 12, "Β’ ΛΥΚΕΙΟΥ ΑΛΓΕΒΡΑ", "Αυτοέλεγχος",
                                                        "Cambria Math", 12))
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
                footer = z.read("word/footer_exam.xml").decode("utf-8")
                if "Επιμέλεια: Αυτοέλεγχος" not in footer or "Β’ ΛΥΚΕΙΟΥ" not in footer:
                    raise RuntimeError("λάθος υποσέλιδο")
                header = z.read("word/header_exam.xml").decode("utf-8")
                if "NUMPAGES" not in header or 'w:val="center"' not in header:
                    raise RuntimeError("λάθος κεφαλίδα")
                styles = z.read("word/styles.xml").decode("utf-8")
                if 'w:ascii="Cambria"' not in styles:
                    raise RuntimeError("δεν εφαρμόστηκε η γραμματοσειρά")
                settings = z.read("word/settings.xml").decode("utf-8")
                if 'm:mathFont m:val="Cambria Math"' not in settings or 'w:ascii="Cambria Math"' not in doc:
                    raise RuntimeError("δεν εφαρμόστηκε η γραμματοσειρά μαθηματικών")
            return f"{expected} εξισώσεις, {len(res['warnings'])} προειδοποιήσεις"

    check("build-docx", build)

    def corpus():
        from .corpus_doc import insert_into, outline_of, sample_document

        ex = {"stem": "Δίνεται η ευθεία $y=2x+1$. Να βρείτε:", "closing": "", "figure": None,
              "items": [{"label": "α", "text": "τον συντελεστή $\\lambda$", "choices": None, "items": []}]}
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            doc = sample_document(tdp / "corpus.docx", tdp)
            key = {"title": "ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ", "nth": 0}
            insert_into(doc, key, [ex, ex], tdp)
            info = insert_into(doc, key, [ex], tdp)
            counts = {h["title"]: h["exercises"] for h in outline_of(doc)}
            if counts.get("ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ") != 3 or info["last_label"] != "3." or not info.get("backup"):
                raise RuntimeError(f"λάθος εισαγωγή: {counts}, {info}")
            return f"3 ασκήσεις, αρίθμηση έως {info['last_label']}, αντίγραφο ασφαλείας OK"

    check("corpus", corpus)

    def phone():
        import urllib.request

        from .phone import PhoneServer, lan_ips, qr_svg

        if not qr_svg("http://192.168.1.10:8766/m/test").startswith("<svg"):
            raise RuntimeError("δεν δημιουργήθηκε QR")
        srv = PhoneServer(lambda *_: {"ok": True})
        port = srv.start(port=0)
        try:
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/m/{srv.token}", timeout=10).read().decode("utf-8")
            if "Φωτογράφιση" not in page:
                raise RuntimeError("λείπει η σελίδα του κινητού")
        finally:
            srv.stop()
        return f"QR OK, σελίδα κινητού OK, διευθύνσεις δικτύου: {', '.join(lan_ips()) or 'καμία (εκτός δικτύου)'}"

    check("phone", phone)

    lines.append("RESULT: " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if ok else 1
