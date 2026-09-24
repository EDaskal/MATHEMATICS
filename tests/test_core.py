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
    out = tmp_path / "exam.docx"
    res = build_docx(_exam(tmp_path), out, footer_text="Υποσέλιδο\tΔοκιμή")
    expected, found = res["math"]
    assert expected > 40 and expected == found, res["warnings"]
    with zipfile.ZipFile(out) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        assert "svgBlip" in doc
        assert 'w:val="none"' in doc  # πίνακες χωρίς περιγράμματα
        assert "<m:oMath" in doc
        footer = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("word/footer"))
        assert "Υποσέλιδο" in footer and "<w:tab/>" in footer


def test_build_with_missing_template_fails(tmp_path):
    from app.docx_build import BuildError

    with pytest.raises(BuildError):
        build_docx({"themes": []}, tmp_path / "x.docx", template=str(tmp_path / "nope.docx"))
