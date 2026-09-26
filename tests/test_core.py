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
