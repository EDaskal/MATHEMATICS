"""Δοκιμές πυρήνα (χωρίς κλήσεις στον Claude). Τρέχουν και στο GitHub πριν από το build."""

import json
import re
import zipfile
from pathlib import Path

import pytest

from app import points
from app.docx_build import BuildContext, build_docx, exam_markdown
from app.figures import parse_spec, render_to_files, run_checks
from app.transcribe import EXERCISE_SCHEMA, normalize

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return normalize(json.loads((FIX / name).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ μονάδες

def test_points_presets_sum_to_100():
    for n in range(1, 7):
        assert sum(points.theme_points(n)) == 100


def test_split_even_remainder_first():
    assert points.split_even(25, 3) == [9, 8, 8]
    assert points.item_points(30, 2) == [15, 15]


def test_points_line():
    assert points.points_line([9, 8, 8]) == "Μονάδες 9 + 8 + 8"


# ------------------------------------------------------------------ σχήμα JSON

def test_schema_has_no_open_objects():
    """Κάθε αντικείμενο του σχήματος κλείνει με additionalProperties=false (απαίτηση structured outputs)."""

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(EXERCISE_SCHEMA)


def test_normalize_limits_depth_and_choices():
    raw = {"stem": "x", "items": [{"label": "α)", "text": "t", "choices": {"columns": 3, "options": ["a", "b"]},
                                   "items": [{"label": "i", "text": "u", "choices": None,
                                              "items": [{"label": "1", "text": "v", "choices": None,
                                                         "items": [{"label": "deep", "text": "w", "choices": None, "items": []}]}]}]}],
           "figure": None}
    n = normalize(raw)
    assert n["items"][0]["label"] == "α"
    assert n["items"][0]["choices"]["columns"] == 2
    assert n["items"][0]["items"][0]["items"][0]["items"] == []


# ------------------------------------------------------------------ σχήματα

def test_figure_checks_pass_for_correct_intersection():
    ex = load("lines_figure.json")
    spec = parse_spec(ex["figure"]["spec_json"])
    assert run_checks(spec) == []


def test_figure_checks_catch_wrong_point():
    spec = {"axes": {"x": [-3, 3], "y": [-2, 7]}, "elements": [
        {"type": "line", "name": "ε", "through": [[-2, 0], [0, 2]]},
        {"type": "line", "name": "ζ", "through": [[0, 6], [2, 0]]},
        {"type": "point", "name": "M", "at": [2, 2]}],
        "checks": [{"type": "intersection", "lines": ["ε", "ζ"], "point": "M"}]}
    warnings = run_checks(spec)
    assert warnings and "(1.00, 3.00)" in warnings[0]


def test_render_figure_files(tmp_path):
    ex = load("lines_figure.json")
    info = render_to_files(ex["figure"]["spec_json"], tmp_path / "fig")
    svg = Path(info["svg"]).read_text(encoding="utf-8")
    assert "<svg" in svg and Path(info["png"]).stat().st_size > 1000
    assert "ε" in svg  # το κείμενο μένει κείμενο (επεξεργάσιμο)


# ------------------------------------------------------------------ Word

def _exam(tmp_path):
    lines = load("lines_figure.json")
    lines["figure_render"] = render_to_files(lines["figure"]["spec_json"], tmp_path / "f1")
    return {
        "title": "ΔΙΑΓΩΝΙΣΜΑ", "subtitle": "ΔΟΚΙΜΗ", "date": "",
        "themes": [
            {"exercises": [load("three_levels.json")], "points": [13, 12]},
            {"exercises": [load("crossref.json"), load("choices.json")], "points": [15, 15]},
            {"exercises": [lines], "points": [10, 10, 10]},
        ],
    }


def test_markdown_numbering_and_refs(tmp_path):
    ctx = BuildContext()
    md = exam_markdown(_exam(tmp_path), ctx)
    assert "**ΘΕΜΑ Α**" in md and "**Α1.**" in md and "**Α2.**" in md
    assert "**Β1.**" in md and "**Β2.**" in md and "**Γ3.**" in md
    assert "[[" not in md  # οι παραπομπές αντικαταστάθηκαν
    assert "του ερωτήματος (i)" in md  # Β1: το (α) έγινε i)
    assert "Μονάδες 13 + 12" in md
    assert re.search(r"^i\)\s", md, re.M)


def test_build_docx_end_to_end(tmp_path):
    from app.docx_build import PageSetup

    out = tmp_path / "exam.docx"
    res = build_docx(_exam(tmp_path), out, setup=PageSetup("Cambria", 12, "Β’ ΛΥΚΕΙΟΥ", "Δοκιμή Δοκιμίδου"))
    expected, found = res["math"]
    assert expected > 40 and expected == found, res["warnings"]
    with zipfile.ZipFile(out) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        assert "svgBlip" in doc
        assert 'w:val="none"' in doc  # πίνακες χωρίς περιγράμματα
        assert "<m:oMath" in doc
        footer = z.read("word/footer_exam.xml").decode()
        assert "Β’ ΛΥΚΕΙΟΥ" in footer and "Επιμέλεια: Δοκιμή Δοκιμίδου" in footer and "<w:tab/>" in footer
        assert 'w:val="right"' in footer  # στάση στηλοθέτη στο δεξί περιθώριο
        header = z.read("word/header_exam.xml").decode()
        assert 'w:val="center"' in header and "PAGE" in header and "NUMPAGES" in header
        assert "rIdExamHdr" in doc and "rIdExamFtr" in doc
        styles = z.read("word/styles.xml").decode()
        assert 'w:ascii="Cambria"' in styles and 'w:sz w:val="24"' in styles
        assert "asciiTheme" not in styles
        ct = z.read("[Content_Types].xml").decode()
        assert "/word/header_exam.xml" in ct


def test_build_with_missing_template_fails(tmp_path):
    from app.docx_build import BuildError

    with pytest.raises(BuildError):
        build_docx({"themes": []}, tmp_path / "x.docx", template=str(tmp_path / "nope.docx"))


def test_header_footer_replace_template_with_first_page_header(tmp_path):
    """Πρότυπο με διαφορετική κεφαλίδα πρώτης σελίδας: αντικαθίσταται παντού από τη δική μας."""
    from docx import Document

    from app.docx_build import PageSetup, default_template

    tpl = tmp_path / "tpl.docx"
    d = Document(str(default_template()))
    sec = d.sections[0]
    sec.different_first_page_header_footer = True
    sec.first_page_header.paragraphs[0].text = "ΠΡΩΤΗ ΣΕΛΙΔΑ"
    d.save(str(tpl))
    out = tmp_path / "o.docx"
    build_docx({"title": "Τ", "themes": [{"exercises": [load("crossref.json")], "points": [50, 50]}]},
               out, template=str(tpl), setup=PageSetup("Georgia", 11, "Α", "Β"))
    with zipfile.ZipFile(out) as z:
        doc = z.read("word/document.xml").decode()
        sect = re.findall(r"<w:sectPr.*?</w:sectPr>", doc, re.S)[-1]
        assert "titlePg" not in sect
        assert sect.count("headerReference") == 1 and "rIdExamHdr" in sect
        assert 'w:sz w:val="22"' in z.read("word/header_exam.xml").decode()


def test_multi_page_prompt_and_crop_page():
    from app.transcribe import user_text

    assert "ΔΙΑΔΟΧΙΚΕΣ ΣΕΛΙΔΕΣ" in user_text(2) and "2 εικόνες" in user_text(2)
    assert "ΔΙΑΔΟΧΙΚΕΣ" not in user_text(1)
    raw = {"stem": "", "items": [], "figure": {"description": "d", "spec_json": "{}", "crop": [0, 0, 1, 1],
                                               "crop_page": 5, "confidence": "high"}}
    assert normalize(raw, n_pages=2)["figure"]["crop_page"] == 1


def test_versions_and_changelog():
    from app import __version__
    from app.updates import is_newer, parse_version

    assert parse_version("v1.10.2") == (1, 10, 2)
    assert is_newer("v1.2.0", "1.1.9") and not is_newer("1.1.0", "1.1.0") and is_newer("2.0", "1.9.9")
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "scripts/changelog_section.py", __version__],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and out.stdout.strip(), "Λείπει η τρέχουσα έκδοση από το CHANGELOG.md"


def test_settings_lists_and_types(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.config import Settings

    s = Settings()
    s.update({"font_size": "11.5", "classes": ["Α", " ", "Β"], "check_updates": 0, "max_parallel": "4"})
    assert s.font_size == 11.5 and s.classes == ["Α", "Β"] and s.check_updates is False and s.max_parallel == 4
    s.remember("editors", "Χ")
    s.remember("editors", "Χ")
    assert s.editors == ["Χ"]


def test_update_check_parses_release(monkeypatch):
    from app import updates

    fake = {"tag_name": "v9.0.0", "body": "### Νέα\n- κάτι", "html_url": "https://github.com/x/y/releases/tag/v9.0.0",
            "published_at": "2030-01-02T10:00:00Z",
            "assets": [{"name": "notes.txt", "browser_download_url": "https://x/notes.txt", "size": 1},
                       {"name": "Diagonismata-Setup-9.0.0.exe", "browser_download_url": "https://x/Setup.exe", "size": 123}]}
    monkeypatch.setattr(updates, "_get_json", lambda url, timeout=10: fake)
    r = updates.check("EDaskal/MATHEMATICS", "1.1.0")
    assert r["newer"] and r["latest"] == "9.0.0" and r["url"] == "https://x/Setup.exe" and r["published"] == "2030-01-02"
    fake["tag_name"] = "v1.1.0"
    assert not updates.check("EDaskal/MATHEMATICS", "1.1.0")["newer"]


def test_download_rejects_insecure_url():
    import pytest as _pytest

    from app.updates import UpdateError, download

    with _pytest.raises(UpdateError):
        download("http://example.com/x.exe")


def test_math_font_in_settings_and_runs(tmp_path):
    from app.docx_build import PageSetup

    out = tmp_path / "m.docx"
    build_docx({"title": "Τ", "themes": [{"exercises": [load("three_levels.json")], "points": [50, 50]}]},
               out, setup=PageSetup("Cambria", 12, "Α", "Β", "STIX Two Math", 11))
    with zipfile.ZipFile(out) as z:
        settings = z.read("word/settings.xml").decode()
        doc = z.read("word/document.xml").decode()
    assert 'm:mathFont m:val="STIX Two Math"' in settings
    # κάθε τμήμα εξίσωσης έχει τη γραμματοσειρά και το μέγεθος μαθηματικών (11pt = 22 μισά σημεία)
    assert doc.count("<m:r>") > 0
    assert doc.count("<m:r>") == doc.count('<w:rFonts w:ascii="STIX Two Math"') - doc.count("<m:ctrlPr>")
    assert re.search(r'<m:r><w:rPr><w:rFonts w:ascii="STIX Two Math"[^>]*/><w:sz w:val="22"/>', doc)
    # σειρά μέσα στο m:r: m:rPr πριν από w:rPr
    assert not re.search(r"<m:r><w:rPr>(?:(?!</w:rPr>).)*</w:rPr><m:rPr>", doc)


def test_figure_font_and_size(tmp_path):
    ex = load("lines_figure.json")
    info = render_to_files(ex["figure"]["spec_json"], tmp_path / "f", font="STIX Two Math", size=14)
    svg = Path(info["svg"]).read_text(encoding="utf-8")
    assert "STIX Two Math" in svg and "14px" in svg


def test_font_scan_detects_math_and_greek(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.fonts import scan

    candidates = [Path("/usr/share/texmf/fonts/opentype/public/lm-math"), Path("/usr/share/fonts/truetype/dejavu")]
    dirs = [d for d in candidates if d.exists()]
    if not dirs:
        pytest.skip("δεν υπάρχουν δοκιμαστικές γραμματοσειρές σε αυτό το σύστημα")
    r = scan(refresh=True, dirs=dirs)
    assert r["count"] > 0 and r["text"]
    assert any(f["greek"] for f in r["text"])
    assert r["math"], "αναμενόταν τουλάχιστον μία γραμματοσειρά μαθηματικών"
    assert scan(dirs=dirs) == r  # από την cache


# ------------------------------------------------------------------ Corpus: εισαγωγή σε υπάρχον Word

CORPUS_EX = {
    "source_label": "", "stem": "Δίνονται τα σημεία $\\mathrm{Α}(1,0)$ και $\\mathrm{Β}(-2,3)$. Να βρείτε:",
    "items": [
        {"label": "i", "text": "την εξίσωση της ευθείας $\\mathrm{ΑΒ}$", "choices": None, "items": []},
        {"label": "ii", "text": "τον συντελεστή διεύθυνσης\n$$\\lambda=\\frac{y_2-y_1}{x_2-x_1}$$", "choices": None, "items": []},
        {"label": "iii", "text": "1. αν η ευθεία περνά από το $\\mathrm{Ο}$ (όπως στο [[i]])", "choices": None, "items": []},
    ],
    "closing": "", "figure": None, "uncertain": [],
}


def _corpus_doc(tmp_path):
    from app.corpus_doc import sample_document

    return sample_document(tmp_path / "corpus.docx", tmp_path)


def test_corpus_outline(tmp_path):
    from app.corpus_doc import outline_of

    hs = outline_of(_corpus_doc(tmp_path))
    assert [h["title"] for h in hs] == ["ΚΕΦΑΛΑΙΟ 1: ΕΥΘΕΙΑ", "ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ", "ΓΕΝΙΚΗ ΜΟΡΦΗ", "ΚΕΦΑΛΑΙΟ 2: ΚΥΚΛΟΣ"]
    assert [h["depth"] for h in hs] == [0, 1, 1, 0]
    assert all(h["exercises"] == 0 for h in hs)


def test_corpus_insert_numbering_continues(tmp_path):
    from app.corpus_doc import CorpusDoc, insert_into, outline_of

    doc = _corpus_doc(tmp_path)
    key = {"title": "ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ", "nth": 0}
    info = insert_into(doc, key, [CORPUS_EX, CORPUS_EX], tmp_path, make_backup=True)
    assert (info["first_label"], info["last_label"]) == ("1.", "2.")
    assert Path(info["backup"]).exists()
    info = insert_into(doc, key, [CORPUS_EX], tmp_path, make_backup=False)
    assert info["last_label"] == "3."
    hs = {h["title"]: h for h in outline_of(doc)}
    assert hs["ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ"]["exercises"] == 3
    assert hs["ΓΕΝΙΚΗ ΜΟΡΦΗ"]["exercises"] == 0  # οι ασκήσεις δεν έγιναν τίτλοι, η επόμενη ενότητα έμεινε ίδια
    with zipfile.ZipFile(doc) as z:
        xml = z.read("word/document.xml").decode()
    # 3 ασκήσεις × (2 εξισώσεις εκφώνησης + 1 + 1 ερωτημάτων + 1 σε δική της γραμμή + 1)
    assert xml.count("<m:oMath>") + xml.count("<m:oMathPara>") >= 3 * 6
    assert "[[" not in xml and "(a)" in xml  # παραπομπή [[i]] → (a)
    assert "1. αν η ευθεία" in xml  # το «1.» στην αρχή κειμένου δεν έγινε λίστα
    d = CorpusDoc(doc)
    texts = [d.text(p) for p in d.body if etree_local(p) == "p"]
    assert texts.index("ΓΕΝΙΚΗ ΜΟΡΦΗ") > max(i for i, t in enumerate(texts) if t.startswith("Δίνονται τα σημεία"))


def etree_local(el):
    from lxml import etree

    return etree.QName(el).localname


def test_corpus_unknown_section(tmp_path):
    from app.corpus_doc import CorpusError, insert_into

    with pytest.raises(CorpusError):
        insert_into(_corpus_doc(tmp_path), {"title": "ΔΕΝ ΥΠΑΡΧΕΙ", "nth": 0}, [CORPUS_EX], tmp_path)


def test_corpus_natural_order():
    from app.corpus_doc import natural_key

    names = ["1.6.10.png", "1.6.2.png", "1.6.1.1.png", "1.6.1.png", "1.6.1.10.png", "1.6.1.2.png"]
    assert sorted(names, key=natural_key) == ["1.6.1.png", "1.6.1.1.png", "1.6.1.2.png", "1.6.1.10.png", "1.6.2.png", "1.6.10.png"]


def test_corpus_schema_has_no_open_objects():
    from app.transcribe import CORPUS_SCHEMA

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n.get("additionalProperties") is False
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(CORPUS_SCHEMA)
    from app.transcribe import corpus_text

    assert "{pages}" not in corpus_text(1)
    assert "ΔΙΑΔΟΧΙΚΕΣ" in corpus_text(2) and "ΔΙΑΔΟΧΙΚΕΣ" not in corpus_text(1)


# ------------------------------------------------------------------ κινητό (QR)

def test_phone_server_roundtrip():
    import base64
    import io
    import urllib.request

    from PIL import Image

    from app.phone import PhoneServer, qr_svg

    got = []
    srv = PhoneServer(lambda data, name, join: (got.append((name, join, data[:22])) or {"ok": True}))
    port = srv.start(port=0)
    try:
        base = f"http://127.0.0.1:{port}/m/{srv.token}"
        page = urllib.request.urlopen(base).read().decode("utf-8")
        assert "Φωτογράφιση" in page
        srv.target = "Corpus · 6. ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ"
        st = json.loads(urllib.request.urlopen(base + "/status").read())
        assert st["target"] == srv.target and st["count"] == 0
        buf = io.BytesIO()
        Image.new("RGB", (40, 30), "white").save(buf, format="JPEG")
        body = json.dumps({"name": "a.jpg", "join": True,
                           "data": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}).encode()
        req = urllib.request.Request(base + "/upload", data=body, method="POST", headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req).read())
        assert res["ok"] and res["count"] == 1
        assert got == [("a.jpg", True, "data:image/jpeg;base64")]
        with pytest.raises(urllib.error.HTTPError) as e:  # λάθος κλειδί → 404
            urllib.request.urlopen(f"http://127.0.0.1:{port}/m/wrongtoken/status")
        assert e.value.code == 404
    finally:
        srv.stop()
    assert qr_svg("http://192.168.1.10:8766/m/x").startswith("<svg")


# ------------------------------------------------------------------ 1.4.1: στήλες, σύμβολα, αρίθμηση

def test_column_separator_normalized():
    from app.transcribe import normalize

    d = normalize({"stem": "x", "items": [{"label": "1", "text": "$A(1,2)$,⇥$B(3,4)$", "choices": None},
                                          {"label": "2", "text": "$A(1,2)$\t$\\theta$", "choices": None}]})
    assert d["items"][0]["text"] == "$A(1,2)$, ⇥ $B(3,4)$"
    assert d["items"][1]["text"] == "$A(1,2)$ ⇥ $\\theta$"  # το \theta δεν πειράζεται


def test_corpus_columns_become_aligned_tabs(tmp_path):
    from app.corpus_doc import insert_into

    ex = {"stem": "Να βρεθεί ο συντελεστής διεύθυνσης όταν:", "closing": "", "figure": None, "items": [
        {"label": "1", "text": "$\\mathrm{Α}(3,4)$, ⇥ $\\mathrm{Β}(6,7)$", "choices": None, "items": []},
        {"label": "2", "text": "$\\mathrm{Α}(3\\kappa+1,2\\kappa+1)$, ⇥ $\\mathrm{Β}(-1,-3)$", "choices": None, "items": []}]}
    doc = _corpus_doc(tmp_path)
    insert_into(doc, {"title": "ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ", "nth": 0}, [ex], tmp_path, make_backup=False)
    with zipfile.ZipFile(doc) as z:
        xml = z.read("word/document.xml").decode()
    stops = re.findall(r'<w:tab w:val="left" w:pos="(\d+)"/>', xml)
    assert len(stops) == 2 and stops[0] == stops[1]  # ίδιος στηλοθέτης και στα δύο ερωτήματα
    assert xml.count("<w:tab/>") == 2 and "§TAB§" not in xml and "⇥" not in xml
    assert "−" not in xml and "<m:t>-" in xml  # μείον όπως το γράφει το Word


def test_exam_columns_and_minus(tmp_path):
    ex = load("three_levels.json")
    ex["items"][0]["text"] = "$\\mathrm{Α}(-1,2)$, ⇥ $\\mathrm{Β}(3,-4)$"
    out = tmp_path / "e.docx"
    build_docx({"title": "Τ", "themes": [{"exercises": [ex], "points": [50, 50]}]}, out)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode()
    assert "  " in xml and "⇥" not in xml and "−" not in xml


# ------------------------------------------------------------------ 1.4.2: η οθόνη χωρίς παλιά cache

def test_ui_server_no_store_and_token(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app import __version__
    from app.bridge import Api
    from app.main import _start_ui_server

    url = _start_ui_server(Api())
    assert f"v={__version__}" in url and "&k=" in url
    r = urllib.request.urlopen(url)
    assert "no-store" in r.headers.get("Cache-Control", "")
    assert 'data-mode="corpus"' in r.read().decode("utf-8")
    base = url.split("/index.html")[0]
    token = url.split("&k=")[1]
    with pytest.raises(urllib.error.HTTPError) as e:  # χωρίς κλειδί: απαγορεύεται
        urllib.request.urlopen(urllib.request.Request(base + "/api/get_state", data=b"[]", method="POST"))
    assert e.value.code == 403
    req = urllib.request.Request(base + "/api/get_state", data=b"[]", method="POST", headers={"X-Api-Token": token})
    assert json.loads(urllib.request.urlopen(req).read())["result"]["version"] == __version__
