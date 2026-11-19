"""Τοπικό μοντέλο (1.8.0): από το κείμενο που βγάζει το μοντέλο OCR στη δομή άσκησης της εφαρμογής.

Το τοπικό μοντέλο δεν επιστρέφει JSON· γράφει την άσκηση σε Markdown με LaTeX, όπως τη βλέπει. Εδώ:

1. **καθαρισμός** (`clean_text`): διορθώνει τα συστηματικά λάθη που βρήκε το benchmark του Νοεμβρίου 2026
   — λατινικό `a` αντί για `α`, «συν» γραμμένο `σν`/`σuv`/`σ_{υν}`, `\\sin`/`\\cos` αντί για ημ/συν,
   ετικέτες `a)`/`b)` αντί για α)/β), «Θέμα 10» αντί για «Θέμα 1ο»·
2. **δομή** (`parse_exercises`): χωρίζει εκφώνηση, ερωτήματα (α, β, … και i, ii, …), επιλογές Α–Δ
   πολλαπλής επιλογής και, στο Corpus, τις χωριστές ασκήσεις μιας σελίδας·
3. **σχήμα** (`figure_region`): αν το μοντέλο σημείωσε σχήμα, βρίσκει περίπου πού είναι στην εικόνα
   (για την «πρωτότυπη εικόνα» όταν δεν υπάρχει Claude για επανασχεδίαση).

Όλα είναι ανεκτικά: ποτέ σφάλμα — στη χειρότερη περίπτωση όλο το κείμενο μπαίνει στην εκφώνηση.
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------------------- μαθηματικά: χωρισμός

_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)

GREEK_OF_LATIN = {"a": "α", "b": "β", "c": "γ", "d": "δ", "e": "ε", "f": "στ", "g": "ζ", "h": "η"}
ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")
_GREEK_CMD = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ",
}


def _map_math(text: str, fn) -> str:
    """Εφαρμόζει `fn` μόνο στο περιεχόμενο των $…$ / $$…$$."""
    def rep(m):
        if m.group(1) is not None:
            return "$$" + fn(m.group(1)) + "$$"
        return "$" + fn(m.group(2)) + "$"
    return _MATH_RE.sub(rep, text)


def _map_text(text: str, fn) -> str:
    """Εφαρμόζει `fn` μόνο στο κείμενο ΕΞΩ από τα μαθηματικά."""
    out, pos = [], 0
    for m in _MATH_RE.finditer(text):
        out.append(fn(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


# ----------------------------------------------------------------------------- καθαρισμός

_TRIG = [  # (μοτίβο, αντικατάσταση) — με αυτή τη σειρά
    (r"\\sigma\s*_\s*\{\s*(?:\\text\{)?\s*(?:υν|uv|\\upsilon\s*\\nu)\s*\}?\s*\}", "συν"),
    (r"\\sigma\s*\\upsilon\s*\\nu(?![a-zA-Z])", "συν"),
    (r"\\sigma\s*u\s*v(?![a-zA-Z])", "συν"),
    (r"\\sigma\s*\\nu(?![a-zA-Z])", "συν"),
    (r"\\sigma\s*v(?![a-zA-Z])", "συν"),
    (r"σ\s*u\s*v|σ\s*υ\s*ν|σuv|σv(?![a-zA-Z])", "συν"),
    (r"\\eta\s*\\mu(?![a-zA-Z])", "ημ"),
    (r"η\s*μ(?=[\s\\^({_]|$)", "ημ"),
    (r"\\(?:var)?epsilon\s*\\(?:var)?phi(?![a-zA-Z])", "εφ"),
    (r"\\sigma\s*\\(?:var)?phi(?![a-zA-Z])", "σφ"),
    (r"\\sin(?![a-zA-Z])", "ημ"),
    (r"\\cos(?![a-zA-Z])", "συν"),
    (r"\\tan(?![a-zA-Z])", "εφ"),
    (r"\\cot(?![a-zA-Z])", "σφ"),
]
_TRIG_RE = [(re.compile(p), r) for p, r in _TRIG]
_PROTECT_RE = re.compile(r"\\(?:mathrm|text|operatorname|textrm|mathit)\s*\{[^{}]*\}|\\[A-Za-z]+")


def _fix_trig(m: str) -> str:
    # προστατεύουμε ό,τι είναι ήδη σωστό (\mathrm{συν} κ.λπ.)
    keep: list[str] = []

    def hold(x):
        keep.append(x.group(0))
        return f"\x00{len(keep) - 1}\x00"

    m = re.sub(r"\\(?:mathrm|text|operatorname)\s*\{\s*(?:ημ|συν|εφ|σφ)\s*\}", hold, m)
    for rx, name in _TRIG_RE:
        def put(_x, n=name):
            keep.append("\\mathrm{" + n + "}")
            return f"\x00{len(keep) - 1}\x00"
        m = rx.sub(put, m)
    # \mathrm{συν} x — κενό ανάμεσα σε συνάρτηση και όρισμα για καθαρό LaTeX
    m = re.sub(r"\x00(\d+)\x00", lambda x: keep[int(x.group(1))], m)
    return re.sub(r"(\\mathrm\{(?:ημ|συν|εφ|σφ)\})(?=[A-Za-zα-ω0-9])", r"\1 ", m)


def _fix_alpha(m: str) -> str:
    """Λατινικό `a` ως μεταβλητή → \\alpha (στα ελληνικά βοηθήματα το λατινικό a δεν χρησιμοποιείται)."""
    keep: list[str] = []

    def hold(x):
        keep.append(x.group(0))
        return f"\x00{len(keep) - 1}\x00"

    m = _PROTECT_RE.sub(hold, m)
    m = re.sub(r"(?<![A-Za-z\x00])a(?=[xyzt](?![A-Za-z])|(?![A-Za-z]))",
               lambda x: "\\alpha " if x.end() < len(m) and m[x.end()].isalpha() else "\\alpha", m)
    return re.sub(r"\x00(\d+)\x00", lambda x: keep[int(x.group(1))], m)


def _monotonic(text: str) -> str:
    """Πολυτονικά σημάδια που «βλέπει» καμιά φορά το μοντέλο (αὐξουσα) → μονοτονικό."""
    import unicodedata

    d = unicodedata.normalize("NFD", text)
    d = re.sub("[\u0313\u0314\u0345]", "", d).replace("\u0300", "\u0301").replace("\u0342", "\u0301")
    return unicodedata.normalize("NFC", d)


def _fix_math(m: str) -> str:
    m = re.sub(r"\\mu\s*\\(?:var)?epsilon(?![a-zA-Z])", r"\\text{ με }", m)
    m = m.replace("\\left\\{", "\\left\\{")
    m = re.sub(r"\\(?:mathbb|Bbb)\s*R\b", r"\\mathbb{R}", m)
    m = m.replace("ℝ", "\\mathbb{R}")
    m = _fix_trig(m)
    m = _fix_alpha(m)
    return m.strip() if m.strip() else m


def _label_fix(text: str) -> str:
    # $\alpha)$ / $a)$ στην αρχή γραμμής → α)
    def greek_label(mm):
        name = mm.group(1)
        if name in _GREEK_CMD:
            return _GREEK_CMD[name] + ")"
        return GREEK_OF_LATIN.get(name, name) + ")"
    text = re.sub(r"(?m)^\s*\$\s*\\?(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|[a-h])\s*\)\s*\$", greek_label, text)
    text = re.sub(r"(?m)^\s*\$\s*\\?(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|[a-h])\s*\$\s*\)", greek_label, text)
    return text


def _theme_fix(text: str) -> str:
    text = re.sub(r"\$\s*\\Theta\s*(?:\\acute\s*\{?\s*)?\\?(?:epsilon|varepsilon|ε)\s*\}?\s*\\mu\s*\\alpha\s*\\?[;, ]?\s*(\d)0?\s*\^?\s*\{?\s*(?:\\circ|o|ο)?\s*\}?\s*\$",
                  lambda m: f"Θέμα {m.group(1)}ο", text)
    # «Θέμα 10» (το «ο» διαβάστηκε 0) → «Θέμα 1ο»
    text = re.sub(r"(?m)^(\s*Θέμα\s+)([1-9])0\s*$", r"\1\2ο", text)
    text = re.sub(r"(?m)^(\s*Θέμα\s+)([1-9])\s*(?:\$\s*\^\s*\{?\\circ\}?\s*\$|°)\s*$", r"\1\2ο", text)
    return text


def clean_text(raw: str) -> str:
    """Καθαρισμός της εξόδου του μοντέλου πριν από τη δομή."""
    t = str(raw or "")
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    if "<channel|>" in t:
        t = t.split("<channel|>")[-1]
    t = re.sub(r"```[a-zA-Z]*", "", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # σχήματα: placeholder εικόνας ή [Σχήμα: …] → σημάδι
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "\n⟦ΣΧΗΜΑ⟧\n", t)
    t = re.sub(r"\[Σχήμα[^\]]*\]", "\n⟦ΣΧΗΜΑ⟧\n", t, flags=re.S)
    t = re.sub(r"</?[A-Za-z][^<>\n]{0,200}>", "", t)  # HTML (π.χ. <div style=…>)
    # \( \) και \[ \] → $ και $$
    t = re.sub(r"\\\[(.+?)\\\]", lambda m: "$$" + m.group(1) + "$$", t, flags=re.S)
    t = re.sub(r"\\\((.+?)\\\)", lambda m: "$" + m.group(1) + "$", t, flags=re.S)
    t = t.replace("**", "")
    t = re.sub(r"(?m)^#{1,6}\s*", "", t)
    t = _theme_fix(t)
    # ερωτήματα στην ίδια γραμμή, χωρισμένα με $\quad$ → χωριστές γραμμές
    t = re.sub(r"[ \t]*\$\s*\\q?quad\s*\$[ \t]*(?=(?:i{1,3}|iv|vi{0,3}|ix|x|[α-ω]{1,2})\)\s)", "\n", t)
    # το άρθρο «η» γραμμένο ως μαθηματικό $\eta$ ανάμεσα σε λέξεις
    t = re.sub(r"(?<=[\u0370-\u03ff\u1f00-\u1fff] )\$\s*\\eta\s*\$(?= )", "η", t)
    t = re.sub(r"\$\s*\\mu\s*\\(?:var)?epsilon\s*\$", "με", t)
    t = _map_text(t, _monotonic)
    # επιλογές γραμμένες ως μαθηματικά: «$\Gamma$:» → «Γ:»
    t = re.sub(r"\$\s*\\?(Alpha|Beta|Gamma|Delta|A|B|Γ|Δ)\s*\$\s*:",
               lambda m: {"Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ"}.get(m.group(1), m.group(1)) + ":", t)
    t = _label_fix(t)
    t = _map_math(t, _fix_math)
    # κενά πριν από σημεία στίξης μετά από μαθηματικά: «$4x^2-3$ .» → «$4x^2-3$.»
    t = re.sub(r"\$[ \t]+([.,;:·])", r"$\1", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


# ----------------------------------------------------------------------------- δομή

_NUM_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){1,2})\.?(?=\s|$)")
_THEME_RE = re.compile(r"^\s*(Θέμα\s+\S+|ΘΕΜΑ\s+\S+)\s*$")
_LABEL_RE = re.compile(r"^\s*\(?\s*([α-ω]{1,2}|[Α-ΩA-H]|[a-h]|i{1,3}|iv|vi{0,3}|ix|x)\s*\)\s*(.*)$")
_CHOICE_SPLIT = re.compile(r"(?:(?<=\s)|^)([ΑΒΓΔABCD])\s*[:.]\s+")


def _lines(text: str) -> list[str]:
    """Γραμμές με ενωμένα τα $$…$$ που απλώνονται σε πολλές γραμμές."""
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: "$$" + " ".join(m.group(1).split()) + "$$", text, flags=re.S)
    out: list[str] = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        # εξίσωση σε δύο γραμμές: «$$A =$$» + «$$= B$$» → «$$A = B$$»
        if out and ln.startswith("$$") and out[-1].endswith("$$") and out[-1].startswith("$$"):
            a, b = out[-1][2:-2].rstrip(), ln[2:-2].strip() if ln.endswith("$$") else ln[2:].strip()
            if a.endswith("=") and b.startswith("="):
                out[-1] = "$$" + a + " " + b[1:].strip() + "$$"
                continue
        out.append(ln)
    return out


def _choices(line: str) -> list[str] | None:
    """«Α: … Β: … Γ: … Δ: …» → λίστα επιλογών (ή None)."""
    parts = _CHOICE_SPLIT.split(" " + line.strip())
    if len(parts) < 3 or parts[0].strip():
        return None
    opts = [p.strip() for p in parts[2::2]]
    return [o for o in opts if o] or None


def _join(a: str, b: str) -> str:
    if not a:
        return b
    if b.startswith("$$") or a.endswith("$$"):
        return a + "\n" + b
    return a + " " + b


def _new_ex() -> dict:
    return {"source_label": "", "stem": "", "items": [], "closing": "", "figure": None, "uncertain": [],
            "_fig": False}


def parse_exercises(text: str, corpus: bool = False) -> list[dict]:
    """Καθαρισμένο κείμενο → ασκήσεις στη μορφή της εφαρμογής (πριν από το `transcribe.normalize`).
    Χωρίς `corpus` όλη η εικόνα είναι μία άσκηση."""
    exs: list[dict] = []
    ex = _new_ex()
    level1_kind: str | None = None  # "greek" | "roman"
    cur1 = cur2 = None
    pending_choices: list[str] = []

    def target():
        return cur2 or cur1

    def flush_choices():
        nonlocal pending_choices
        if pending_choices:
            t = target()
            if t is not None:
                short = all(len(re.sub(r"\$|\\[a-z]+|[{}]", "", o)) <= 18 for o in pending_choices)
                t["choices"] = {"columns": 4 if short and len(pending_choices) == 4 else 2, "options": pending_choices[:]}
            pending_choices = []

    def has_content(e):
        return bool(e["stem"] or e["items"] or e["_fig"])

    for ln in _lines(text):
        if ln == "⟦ΣΧΗΜΑ⟧":
            ex["_fig"] = True
            continue
        num = _NUM_RE.match(ln)
        theme = _THEME_RE.match(ln)
        if (num or theme) and corpus and has_content(ex):
            flush_choices()
            exs.append(ex)
            ex, level1_kind, cur1, cur2 = _new_ex(), None, None, None
        if num and not ex["source_label"] and not ex["items"]:
            ex["source_label"] = num.group(1)
            ln = ln[num.end():].strip()
            if not ln:
                continue
        elif theme and not ex["source_label"] and not ex["items"] and not ex["stem"]:
            ex["source_label"] = theme.group(1)
            continue
        lab = _LABEL_RE.match(ln)
        if lab:
            label, rest = lab.group(1), lab.group(2).strip()
            kind = "roman" if label in ROMAN else "greek"
            if kind == "greek" and len(label) == 1 and label in GREEK_OF_LATIN:
                label = GREEK_OF_LATIN[label]
            if kind == "greek" and label in "ABCDEFGH" and len(label) == 1:
                label = "ΑΒΓΔΕΖΗΘ"["ABCDEFGH".index(label)]
            flush_choices()
            item = {"label": label, "text": "", "choices": None, "items": []}
            if level1_kind is None:
                level1_kind = kind
            if kind == level1_kind or cur1 is None:
                ex["items"].append(item)
                cur1, cur2 = item, None
            else:
                cur1["items"].append(item)
                cur2 = item
            ch = _choices(rest)
            if ch:
                pending_choices += ch
            else:
                item["text"] = rest
            continue
        ch = _choices(ln)
        if ch and target() is not None:
            pending_choices += ch
            continue
        t = target()
        if pending_choices and t is not None:
            # κείμενο μετά από επιλογές: συνέχεια της τελευταίας επιλογής (π.χ. κλάσμα σε δική του γραμμή)
            pending_choices[-1] = _join(pending_choices[-1], ln)
            continue
        if t is None:
            ex["stem"] = _join(ex["stem"], ln)
        else:
            t["text"] = _join(t["text"], ln)
    flush_choices()
    if has_content(ex) or not exs:
        exs.append(ex)
    return exs


def to_exercises(raw: str, corpus: bool = False) -> list[dict]:
    """Ολόκληρη η διαδρομή: έξοδος μοντέλου → λίστα ασκήσεων με σημάδι σχήματος `_fig`."""
    return parse_exercises(clean_text(raw), corpus=corpus)


# ----------------------------------------------------------------------------- σχήμα: πού είναι στην εικόνα

def figure_region(image_bytes: bytes) -> list[float]:
    """Περίπου η περιοχή του σχήματος στη σελίδα, [x0, y0, x1, y1] σε 0–1.

    Χωρίς Claude δεν ξέρουμε πού είναι το σχήμα. Τα γράμματα είναι μικρά «κομμάτια» μελανιού σε
    γραμμές· ένα σχήμα έχει μακριές γραμμές (άξονες, ευθείες) που απλώνονται σε πολλές γραμμές
    κειμένου. Βρίσκουμε τα μεγάλα συνεκτικά κομμάτια και κρατάμε το πλαίσιό τους. Αν δεν βρεθεί
    τίποτα σίγουρο → όλη η σελίδα (η καθηγήτρια ορίζει την περικοπή από την οθόνη).
    """
    try:
        import io

        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("L")
        w0, h0 = img.size
        scale = 400 / max(w0, h0)
        small = img.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))))
        a = np.asarray(small, dtype=np.uint8)
        bg = float(np.median(a))
        ink = a < min(190, bg - 50)
        # διακεκομμένες/αχνές γραμμές σχήματος: «ένωση» γειτονικών κουκκίδων (διαστολή 3×3)
        d = ink.copy()
        d[1:, :] |= ink[:-1, :]
        d[:-1, :] |= ink[1:, :]
        d[:, 1:] |= ink[:, :-1]
        d[:, :-1] |= ink[:, 1:]
        ink = d
        h, w = ink.shape
        lab = np.zeros((h, w), dtype=np.int32)
        boxes = []
        n = 0
        ys, xs = np.nonzero(ink)
        for y0, x0 in zip(ys.tolist(), xs.tolist()):
            if lab[y0, x0]:
                continue
            n += 1
            stack = [(y0, x0)]
            lab[y0, x0] = n
            miny = maxy = y0
            minx = maxx = x0
            cnt = 0
            while stack:
                y, x = stack.pop()
                cnt += 1
                miny, maxy, minx, maxx = min(miny, y), max(maxy, y), min(minx, x), max(maxx, x)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w and ink[yy, xx] and not lab[yy, xx]:
                            lab[yy, xx] = n
                            stack.append((yy, xx))
            boxes.append((minx, miny, maxx, maxy, cnt))
        if not boxes:
            return [0.0, 0.0, 1.0, 1.0]
        heights = sorted(b[3] - b[1] + 1 for b in boxes if b[4] >= 3)
        line_h = heights[len(heights) // 2] if heights else 8
        big = [b for b in boxes if (b[3] - b[1] + 1) > 3.5 * line_h and (b[2] - b[0] + 1) > 3 * line_h]
        if not big:
            return [0.0, 0.0, 1.0, 1.0]
        x0 = min(b[0] for b in big)
        y0 = min(b[1] for b in big)
        x1 = max(b[2] for b in big)
        y1 = max(b[3] for b in big)
        # ετικέτες γύρω από το σχήμα (x, y, O, αριθμοί αξόνων): μικρά κομμάτια κοντά στο πλαίσιο
        pad_x, pad_y = 1.5 * line_h, 2.5 * line_h  # οριζόντια πιο σφιχτά: δίπλα συχνά υπάρχει στήλη κειμένου
        fx0, fy0, fx1, fy1 = x0, y0, x1, y1
        for b in boxes:
            small_piece = (b[3] - b[1] + 1) <= 2 * line_h and (b[2] - b[0] + 1) <= 3 * line_h
            if small_piece and b[0] >= fx0 - pad_x and b[2] <= fx1 + pad_x and b[1] >= fy0 - pad_y and b[3] <= fy1 + pad_y:
                x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
        m = line_h * 0.6
        box = [max(0, x0 - m) / w, max(0, y0 - m) / h, min(w, x1 + m) / w, min(h, y1 + m) / h]
        if (box[2] - box[0]) * (box[3] - box[1]) > 0.85:
            return [0.0, 0.0, 1.0, 1.0]
        return [round(v, 3) for v in box]
    except Exception:  # noqa: BLE001 — ποτέ σφάλμα· όλη η σελίδα
        return [0.0, 0.0, 1.0, 1.0]
