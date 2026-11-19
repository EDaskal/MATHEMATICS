"""1.8.0 — τοπικό μοντέλο: καθαρισμός, δομή, λήψη, provider, μικτή λειτουργία, bridge.

Όλα με συνθετικά δεδομένα (καμία πραγματική άσκηση/εικόνα) και χωρίς πραγματική μηχανή:
το `local_llm.ocr` αντικαθίσταται με έτοιμο κείμενο, όπως το γράφει το LightOnOCR.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json

import pytest

from app import local_llm
from app.local_parse import clean_text, figure_region, parse_exercises, to_exercises

# έξοδος «όπως του μοντέλου»: λατινικό a, σ_{υν}, $\alpha)$, εξίσωση σε δύο γραμμές, σχήμα, HTML
RAW = r"""7.4 Δίνεται η ευθεία:

$$
\varepsilon: y = (3a - 1)x + a, \quad \mu\varepsilon \; a \in \mathbb{R}
$$

και η συνάρτηση $f(x) = 2\sigma_{\text{υν}}^2 x + \sin x$ .

$\alpha)$ Να βρείτε το $a$.

$\beta)$ Να λύσετε την εξίσωση:

$$
2\sigma uv x + \eta\mu x =
$$

$$
= 1
$$

<div style="text-align: center;">
  ![image](image_1.png)
</div>
"""


def test_clean_text_fixes_systematic_errors():
    t = clean_text(RAW)
    assert "(3\\alpha - 1)x + \\alpha," in t and " a " not in t
    assert "\\mathrm{συν}^2 x" in t and "\\mathrm{ημ} x" in t and "\\sin" not in t
    assert "σuv" not in t and "\\sigma" not in t
    assert "\\text{ με }" in t
    assert "<div" not in t and "⟦ΣΧΗΜΑ⟧" in t
    assert "$." in t  # χωρίς κενό πριν από την τελεία
    assert "α) Να βρείτε" in t and "β) Να λύσετε" in t


def test_clean_text_keeps_correct_latex_and_words():
    t = clean_text(r"Να βρείτε το $\max(a, 3)$ και $\log_a x$ στο $\mathrm{συν}\, x$ $< 2$ όταν $0 < a < 1$.")
    assert "\\max(\\alpha, 3)" in t and "\\log_\\alpha x" in t
    assert t.count("\\mathrm{συν}") == 1
    assert "0 < \\alpha < 1" in t  # το «<» δεν θεωρείται ετικέτα HTML


def test_theme_header_and_quad_items():
    raw = "$\\Theta \\acute{\\epsilon} \\mu \\alpha \\ 1 \\circ$\n\nα) Πότε μια συνάρτηση $f$: $\\quad$ i) είναι άρτια; $\\quad$ ii) είναι περιττή;\n\nβ) Αν $\\eta$ συνάρτηση $f$ είναι αὐξουσα"
    ex = to_exercises(raw)[0]
    assert ex["source_label"] == "Θέμα 1ο"
    a = ex["items"][0]
    assert a["label"] == "α" and [i["label"] for i in a["items"]] == ["i", "ii"]
    assert ex["items"][1]["text"].startswith("Αν η συνάρτηση") and "αυξουσα" in ex["items"][1]["text"]
    assert to_exercises("Θέμα 20\n\nα) κάτι")[0]["source_label"] == "Θέμα 2ο"


def test_parse_structure_merges_equation_and_marks_figure():
    ex = to_exercises(RAW)[0]
    assert ex["source_label"] == "7.4"
    assert ex["stem"].startswith("Δίνεται η ευθεία:\n$$")
    labels = [i["label"] for i in ex["items"]]
    assert labels == ["α", "β"]
    assert "$$2\\mathrm{συν} x + \\mathrm{ημ} x = 1$$" in ex["items"][1]["text"]
    assert ex["_fig"] is True


def test_parse_multiple_choice_and_latin_labels():
    raw = "a) Ποιο είναι σωστό;\n\nA: $1$  \nB: $2$\n\n$\\Gamma$: $3$  \n$\\Delta$: $4$\n\nb) Δεύτερο"
    ex = to_exercises(raw)[0]
    assert [i["label"] for i in ex["items"]] == ["α", "β"]
    ch = ex["items"][0]["choices"]
    assert ch["options"] == ["$1$", "$2$", "$3$", "$4$"] and ch["columns"] == 4
    one_line = to_exercises("α) Επιλέξτε: \n\nΑ: $x$ Β: $y$ Γ: $z$ Δ: $w$")[0]
    assert one_line["items"][0]["choices"]["options"] == ["$x$", "$y$", "$z$", "$w$"]


def test_parse_corpus_splits_by_book_number():
    raw = "3.1 Να λύσετε την $x+1=2$.\n\n3.2 Δίνεται η $f$.\n\nα) Πεδίο ορισμού.\n\nβ) Μονοτονία."
    exs = parse_exercises(clean_text(raw), corpus=True)
    assert [e["source_label"] for e in exs] == ["3.1", "3.2"]
    assert exs[1]["items"][1]["label"] == "β"
    single = parse_exercises(clean_text(raw), corpus=False)
    assert len(single) == 1  # στο διαγώνισμα όλη η εικόνα είναι μία άσκηση


def test_parse_never_fails_on_garbage():
    for raw in ["", "   ", "))))", "$$ $$", "α)", "⟦ΣΧΗΜΑ⟧"]:
        exs = to_exercises(raw)
        assert isinstance(exs, list) and len(exs) == 1


def _page_with_figure(text_side: bool = True) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 500), "white")
    d = ImageDraw.Draw(img)
    for y in range(40, 460, 30):  # «γραμμές κειμένου» αριστερά
        for x in range(30, 360, 22):
            d.rectangle([x, y, x + 12, y + 14], fill="black")
    d.line([(500, 60), (500, 440)], fill="black", width=3)  # άξονας y
    d.line([(420, 300), (760, 300)], fill="black", width=3)  # άξονας x
    d.line([(430, 420), (740, 80)], fill=(150, 150, 150), width=2)  # αχνή ευθεία
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_figure_region_finds_the_drawing_not_the_text():
    x0, y0, x1, y1 = figure_region(_page_with_figure())
    assert x0 > 0.45 and x1 > 0.9 and y0 < 0.2 and y1 > 0.8
    assert figure_region(b"not an image") == [0.0, 0.0, 1.0, 1.0]


# ------------------------------------------------------------------ λήψη μοντέλου

class _Resp(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_install_verifies_sha_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGONISMATA_MODELS", str(tmp_path))
    blob_a, blob_b = b"A" * 1000, b"B" * 600
    entry = {"title": "t", "short": "T", "note": "", "repo": "x/y", "rev": "abc", "prompt": None, "max_side": 100, "ctx": 512,
             "model": ("m.gguf", len(blob_a), hashlib.sha256(blob_a).hexdigest()),
             "mmproj": ("p.gguf", len(blob_b), hashlib.sha256(blob_b).hexdigest())}
    monkeypatch.setitem(local_llm.CATALOG, "tst", entry)
    urls = []

    def opener(req, timeout):
        urls.append((req.full_url, req.headers.get("Range")))
        name = req.full_url.rsplit("/", 1)[-1]
        data = blob_a if name == "m.gguf" else blob_b
        rng = req.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return _Resp(data[start:], 206)
        return _Resp(data)

    # μισοκατεβασμένο αρχείο από προηγούμενη φορά → συνεχίζει με Range
    (tmp_path / "tst").mkdir()
    (tmp_path / "tst" / "m.gguf.part").write_bytes(blob_a[:400])
    seen = []
    local_llm.install("tst", lambda d, t, n: seen.append((d, t)), opener=opener)
    assert (tmp_path / "tst" / "m.gguf").read_bytes() == blob_a
    assert urls[0] == ("https://huggingface.co/x/y/resolve/abc/m.gguf", "bytes=400-")
    assert seen[-1] == (1600, 1600)
    assert local_llm.status("tst")["installed"]

    # αλλοιωμένο αρχείο → απορρίπτεται και διαγράφεται
    local_llm.remove("tst")
    assert not local_llm.status("tst")["installed"]

    def bad(req, timeout):
        return _Resp(b"X" * 1000)

    with pytest.raises(local_llm.LocalError, match="SHA-256"):
        local_llm.install("tst", opener=bad)
    assert not (tmp_path / "tst" / "m.gguf").exists() and not (tmp_path / "tst" / "m.gguf.part").exists()


def test_catalog_is_pinned():
    for key, e in local_llm.CATALOG.items():
        assert len(e["rev"]) == 40, key  # συγκεκριμένο commit, όχι «main»
        for name, size, sha in (e["model"], e["mmproj"]):
            assert name.endswith(".gguf") and size > 100_000_000 and len(sha) == 64


def test_ensure_without_model_gives_clear_message(monkeypatch, tmp_path):
    monkeypatch.setenv("DIAGONISMATA_MODELS", str(tmp_path))
    with pytest.raises(local_llm.LocalError, match="Λήψη"):
        local_llm.ocr("lightonocr2", b"x")


# ------------------------------------------------------------------ provider & μικτή λειτουργία

def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_local_provider_exercise_and_corpus(monkeypatch):
    from app.providers.local import LocalProvider
    from app.transcribe import transcribe_many

    texts = iter([RAW, "3.1 Πρώτη $x=1$.\n\n3.2 Δεύτερη.\n\nα) ερώτημα"])
    monkeypatch.setattr(local_llm, "ocr", lambda key, png, timeout=0, threads=0: next(texts))
    prov = LocalProvider("lightonocr2")
    res = asyncio.run(transcribe_many(prov, [("j1", [_png()])], 2, None))["j1"]
    assert res["review"] is True and any("περικοπή" in u for u in res["uncertain"])
    assert res["figure"]["use_original"] is True and res["figure"]["spec_json"] == ""
    assert [i["label"] for i in res["items"]] == ["α", "β"]
    cor = asyncio.run(transcribe_many(prov, [("j2", [_png()])], 2, None, corpus=True))["j2"]
    assert [e["source_label"] for e in cor] == ["3.1", "3.2"] and all(e["review"] for e in cor)


def test_local_provider_refuses_figure_questions(monkeypatch):
    from app.figure_check import CHECK_SCHEMA
    from app.providers import ProviderError
    from app.providers.local import LocalProvider

    with pytest.raises(ProviderError, match="Μικτή λειτουργία"):
        asyncio.run(LocalProvider("lightonocr2").extract_json("κ", "τ", [], CHECK_SCHEMA))


class _FakeClaude:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def extract_json(self, system, user_text, images, schema, count_images=None):
        self.calls.append(user_text)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    async def ping(self):
        return "OK"


def test_hybrid_redraws_figures_with_claude(monkeypatch):
    from app.providers.local import HybridProvider, LocalProvider
    from app.transcribe import transcribe_many

    monkeypatch.setattr(local_llm, "ocr", lambda key, png, timeout=0, threads=0: RAW)
    spec = json.dumps({"axes": None, "elements": [{"type": "segment", "from": [0, 0], "to": [2, 1]}]})
    claude = _FakeClaude([
        {"found": True, "crop_page": 0, "crop": [0.5, 0, 1, 1], "description": "ευθεία", "why": ""},
        {"notes": "", "changes": "", "description": "ευθεία", "confidence": "high", "spec_json": spec}])
    hp = HybridProvider(LocalProvider("lightonocr2"), lambda: claude)
    res = asyncio.run(transcribe_many(hp, [("j", [_png()])], 1, None, check_figures=True))["j"]
    assert res["figure"]["spec_json"] == spec and res["figure"]["use_original"] is False
    assert res["figure"]["checked"] is True  # δεν ξαναελέγχεται (ήδη σε πλέγμα)
    assert len(claude.calls) == 2 and "7.4" in claude.calls[0]
    assert res["review"] is True  # το κείμενο είναι τοπικό → «Προς έλεγχο»


def test_hybrid_keeps_original_when_claude_unavailable(monkeypatch):
    from app.providers import ProviderError
    from app.providers.local import HybridProvider, LocalProvider
    from app.transcribe import transcribe_many

    monkeypatch.setattr(local_llm, "ocr", lambda key, png, timeout=0, threads=0: RAW)

    def no_claude():
        raise ProviderError("Δεν έχει οριστεί κλειδί API.")

    hp = HybridProvider(LocalProvider("lightonocr2"), no_claude)
    res = asyncio.run(transcribe_many(hp, [("j", [_png()])], 1, None))["j"]
    assert res["figure"]["use_original"] is True
    assert any("Claude δεν ήταν διαθέσιμος" in u for u in res["uncertain"])
    offline = HybridProvider(LocalProvider("lightonocr2"), lambda: _FakeClaude([ProviderError("Δεν υπάρχει σύνδεση")]))
    res = asyncio.run(transcribe_many(offline, [("j", [_png()])], 1, None))["j"]
    assert res["figure"]["use_original"] is True and res["items"]


def test_get_provider_routes(monkeypatch):
    from app.config import Settings
    from app.providers import get_provider
    from app.providers.local import HybridProvider, LocalProvider

    s = Settings()
    s.provider, s.local_hybrid = "local", False
    assert isinstance(get_provider(s), LocalProvider)
    s.local_hybrid = True
    assert isinstance(get_provider(s), HybridProvider)


# ------------------------------------------------------------------ bridge

def test_bridge_local_state_install_and_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
    monkeypatch.setenv("DIAGONISMATA_MODELS", str(tmp_path / "models"))
    from app.bridge import Api

    api = Api()
    st = api.local_state()
    assert [m["key"] for m in st["models"]] == ["lightonocr2", "qwen35-4b"] and not st["models"][0]["installed"]
    assert api.local_install("άγνωστο")["ok"] is False
    stopped = []
    monkeypatch.setattr(local_llm, "stop", lambda: stopped.append(1))
    state = api.save_settings({"provider": "local", "local_model": "κακό", "local_claude": "x"})
    assert state["settings"]["local_model"] == "lightonocr2" and state["settings"]["local_claude"] == "agent_sdk"
    assert stopped  # αλλαγή σε τοπικό → η μηχανή ξεκινά ξανά όταν χρειαστεί
    assert "local" in state["providers"]
    v = api.usage_state()
    assert v["local"] is True and v["provider"] == "agent_sdk"  # μικτή: η χρήση αφορά τη συνδρομή
    api.save_settings({"local_hybrid": False})
    assert api.usage_state()["provider"] == "local"
    assert api.usage_refresh()["ok"] is False


def test_bridge_figure_view_crops_original(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.bridge import Api

    api = Api()
    view = api._figure_view("k", {"use_original": True, "crop": [0.5, 0, 1, 1], "crop_page": 0}, [_page_with_figure()])
    assert view["original"] and view["png_data"].startswith("data:image/png")
    from PIL import Image

    assert Image.open(view["png"]).size == (400, 500)
