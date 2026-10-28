"""Έλεγχος αν μια νέα άσκηση υπάρχει ήδη (ίδια ή περίπου ίδια) στο έγγραφο Word της συλλογής (Corpus).

Βήματα:
1. Από το Word διαβάζονται οι υπάρχουσες ασκήσεις: η αρίθμησή τους (π.χ. 6.12), η ενότητα, τα ερωτήματα
   (a., b., …) και το περιεχόμενο — κείμενο, εξισώσεις MathType (βλ. mathtype.py) και εξισώσεις Word (OMML).
2. Κάθε άσκηση (νέα ή υπάρχουσα) γίνεται «δακτυλικό αποτύπωμα»: χωρίς τόνους, κεφαλαία/πεζά, κενά και
   στίξη, με ενιαία σύμβολα (−/-, \\vec/\\overrightarrow, \\frac{1}{2} ≈ 1/2 κ.λπ.).
3. Σύγκριση σε τρία επίπεδα: ίδια · ίδια διατύπωση με άλλα νούμερα · παρόμοια. Επιπλέον: ίδιο ερώτημα
   μέσα σε άλλη άσκηση, και ίδιες ασκήσεις μέσα στις νέες φωτογραφίες.

Όλα γίνονται τοπικά, χωρίς Internet.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from lxml import etree

from .mathtype import GREEK, SYMBOLS, ole_to_latex

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
O_NS = "urn:schemas-microsoft-com:office:office"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

LEVELS = ("same", "part", "numbers", "similar")

# ευαισθησία → (ίδια διατύπωση/άλλα νούμερα: ελάχιστο μήκος, παρόμοια: όριο ομοιότητας)
SENSITIVITY = {
    "off": None,
    "low": {"numbers_min": None, "similar": None},
    "normal": {"numbers_min": 40, "similar": 0.85},
    "high": {"numbers_min": 25, "similar": 0.75},
}
SAME = 0.97
PART_MIN = 30          # ελάχιστο μήκος αποτυπώματος για σύγκριση μεμονωμένων ερωτημάτων (και να έχει αριθμούς:
                       # τα γενικά «Να βρείτε τις συντεταγμένες του Γ» υπάρχουν παντού)
MIN_LEN = 12           # πιο σύντομα αποτυπώματα συγκρίνονται μόνο ως «ίδια»
MAX_MATCHES = 3


# --------------------------------------------------------------------------- OMML (εξισώσεις Word) → LaTeX

def _m(tag: str) -> str:
    return f"{{{M_NS}}}{tag}"


def _mval(el, path: str) -> str | None:
    x = el.find(path, {"m": M_NS})
    return x.get(_m("val")) if x is not None else None


def omml_latex(el) -> str:
    def kids(e, name):
        return [c for c in e if etree.QName(c).localname == name and etree.QName(c).namespace == M_NS]

    def one(e, name) -> str:
        k = kids(e, name)
        return conv(k[0]) if k else ""

    def conv(e) -> str:
        tag = etree.QName(e).localname
        if etree.QName(e).namespace != M_NS:
            return ""
        if tag == "r":
            txt = "".join(t.text or "" for t in e.iter(_m("t")))
            if _mval(e, "m:rPr/m:scr") == "double-struck":
                txt = "".join(_BB.get(ch, ch) for ch in txt)
            return "".join(_char_tex(ch) for ch in txt)
        if tag == "f":
            return "\\frac{" + one(e, "num") + "}{" + one(e, "den") + "}"
        if tag == "sSup":
            return "{" + one(e, "e") + "}^{" + one(e, "sup") + "}"
        if tag == "sSub":
            return "{" + one(e, "e") + "}_{" + one(e, "sub") + "}"
        if tag == "sSubSup":
            return "{" + one(e, "e") + "}_{" + one(e, "sub") + "}^{" + one(e, "sup") + "}"
        if tag == "sPre":
            return "{}_{" + one(e, "sub") + "}^{" + one(e, "sup") + "}" + one(e, "e")
        if tag == "rad":
            deg = one(e, "deg")
            return ("\\sqrt[" + deg + "]" if deg else "\\sqrt") + "{" + one(e, "e") + "}"
        if tag == "d":
            beg = _mval(e, "m:dPr/m:begChr")
            end = _mval(e, "m:dPr/m:endChr")
            sep = _mval(e, "m:dPr/m:sepChr")
            beg = "(" if beg is None else beg
            end = ")" if end is None else end
            sep = "|" if sep is None else sep
            inner = sep.join(conv(k) for k in kids(e, "e"))
            return _fence_tex(beg) + inner + _fence_tex(end)
        if tag == "acc":
            ch = _mval(e, "m:accPr/m:chr") or "\u0302"
            body = one(e, "e")
            if ch in ("\u20d7", "\u2192", "\u20d1"):
                return "\\overrightarrow{" + body + "}"
            if ch in ("\u0305", "\u00af", "\u203e"):
                return "\\overline{" + body + "}"
            if ch == "\u0303":
                return "\\widetilde{" + body + "}"
            if ch == "\u0307":
                return "\\dot{" + body + "}"
            return "\\widehat{" + body + "}"
        if tag == "bar":
            pos = _mval(e, "m:barPr/m:pos")
            return ("\\underline{" if pos == "bot" else "\\overline{") + one(e, "e") + "}"
        if tag == "func":
            return one(e, "fName") + " " + one(e, "e")
        if tag == "limLow":
            return one(e, "e") + "_{" + one(e, "lim") + "}"
        if tag == "limUpp":
            return one(e, "e") + "^{" + one(e, "lim") + "}"
        if tag == "nary":
            sym = _mval(e, "m:naryPr/m:chr") or "\u222b"
            return _char_tex(sym) + "_{" + one(e, "sub") + "}^{" + one(e, "sup") + "}" + one(e, "e")
        if tag == "eqArr":
            return "\\begin{array}{l}" + " \\\\ ".join(conv(k) for k in kids(e, "e")) + "\\end{array}"
        if tag == "m":
            rows = [" & ".join(conv(c) for c in kids(r, "e")) for r in kids(e, "mr")]
            return "\\begin{matrix}" + " \\\\ ".join(rows) + "\\end{matrix}"
        if tag in ("rPr", "ctrlPr", "fPr", "dPr", "accPr", "radPr", "sSupPr", "sSubPr", "naryPr", "funcPr",
                   "barPr", "eqArrPr", "mPr", "limLowPr", "limUppPr", "boxPr", "borderBoxPr", "groupChrPr",
                   "sSubSupPr", "sPrePr", "phantPr", "oMathParaPr"):
            return ""
        return "".join(conv(c) for c in e)

    return " ".join(conv(el).split())


def _char_tex(ch: str) -> str:
    if ch in GREEK:
        return GREEK[ch] + " "
    if ch in SYMBOLS:
        s = SYMBOLS[ch]
        return s + " " if s.startswith("\\") and s[-1].isalpha() else s
    return ch


def _fence_tex(ch: str) -> str:
    if ch in ("{", "}"):
        return "\\" + ch
    return _char_tex(ch).strip() if ch else ""


# --------------------------------------------------------------------------- γραμματοσειρά Symbol

_SYMBOL_FONT = dict(zip("abgdezhqiklmnxoprstufcywjvJ", "αβγδεζηθικλμνξοπρστυφχψωϕϖϑ"))
_SYMBOL_FONT.update(dict(zip("ABGDEZHQIKLMNXOPRSTUFCYW", "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")))
_SYMBOL_FONT.update({
    "\xb4": "×", "\xa3": "≤", "\xb3": "≥", "\xb9": "≠", "\xb1": "±", "\xae": "→", "\xde": "⇒", "\xdb": "⇔",
    "\xd7": "⋅", "\xb0": "°", "\xa5": "∞", "\xce": "∈", "\xcf": "∉", "\xd0": "∠", "^": "⊥", "\xba": "≡",
    "-": "−", "\xbb": "≈", "\xd6": "√", "\xe5": "∑", "\xf2": "∫", "\xc6": "∅", "\xc8": "∪", "\xc7": "∩",
    "\xa2": "′", "\xb2": "″", "\xbc": "…", "\xdc": "⇐", "\xac": "←", "\xab": "↔", "\xf4": "|",
})


def _symbol_char(ch: str) -> str:
    code = ord(ch)
    if 0xF000 <= code <= 0xF0FF:
        code -= 0xF000
    return _SYMBOL_FONT.get(chr(code), chr(code) if code < 0x80 else "")


# --------------------------------------------------------------------------- Word → ασκήσεις

_OLE_CACHE: dict[str, str | None] = {}  # εξισώσεις MathType που έχουν ήδη διαβαστεί (ανά περιεχόμενο)


class _Reader:
    """Διαβάζει παραγράφους του Word ως κείμενο με εξισώσεις σε $…$ (μορφή όπως της μεταγραφής)."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.rels: dict[str, str] = {}
        raw = files.get("word/_rels/document.xml.rels")
        if raw:
            for r in etree.fromstring(raw).findall(f"{{{PR_NS}}}Relationship"):
                self.rels[r.get("Id")] = r.get("Target", "")
        self._ole_cache: dict[str, str | None] = {}
        self.equations = 0
        self.unread = 0

    def ole(self, rid: str | None) -> str | None:
        target = self.rels.get(rid or "", "")
        if not target:
            return None
        name = "word/" + target.lstrip("/") if not target.startswith("word/") else target
        name = name.replace("word//", "word/").replace("/../", "/")
        if name not in self._ole_cache:
            blob = self.files.get(name)
            if not blob:
                self._ole_cache[name] = None
            else:
                import hashlib

                h = hashlib.sha1(blob).hexdigest()
                if h not in _OLE_CACHE:
                    if len(_OLE_CACHE) > 50000:
                        _OLE_CACHE.clear()
                    _OLE_CACHE[h] = ole_to_latex(blob)
                self._ole_cache[name] = _OLE_CACHE[h]
        return self._ole_cache[name]

    def para(self, p) -> tuple[str, bool]:
        """(κείμενο, έχει_εικόνα)."""
        out: list[str] = []
        fig = False

        def walk(el, symbol=False):
            nonlocal fig
            q = etree.QName(el)
            tag, ns = q.localname, q.namespace
            if ns == M_NS:
                if tag == "oMath":
                    self.equations += 1
                    out.append(" $" + omml_latex(el) + "$ ")
                    return
                if tag == "oMathPara":
                    for m in el.iter(_m("oMath")):
                        walk(m)
                    return
            if ns == W_NS:
                if tag in ("pPr", "rPr", "delText", "instrText", "del", "fldChar", "footnoteReference"):
                    return
                if tag == "r":
                    fonts = el.find(f"{{{W_NS}}}rPr/{{{W_NS}}}rFonts")
                    symbol = fonts is not None and (fonts.get(f"{{{W_NS}}}ascii") or "").lower() == "symbol"
                elif tag == "t":
                    txt = el.text or ""
                    out.append("".join(_symbol_char(c) for c in txt) if symbol else txt)
                    return
                elif tag == "sym":
                    out.append(_symbol_char(chr(int(el.get(f"{{{W_NS}}}char") or "20", 16))))
                    return
                elif tag in ("tab", "br", "cr"):
                    out.append(" ")
                    return
                elif tag == "object":
                    ole = el.find(f".//{{{O_NS}}}OLEObject")
                    if ole is not None:
                        self.equations += 1
                        tex = self.ole(ole.get(f"{{{R_NS}}}id"))
                        if tex:
                            out.append(" $" + tex + "$ ")
                        else:
                            self.unread += 1
                    else:
                        fig = True
                    return
                elif tag in ("drawing", "pict"):
                    fig = True
                    return
            for c in el:
                walk(c, symbol)

        walk(p)
        text = " ".join("".join(out).split())
        text = re.sub(r"\$\s*\$", "", text)
        return text, fig


def block_exercise(cdoc, reader: "_Reader", b: dict, labels: dict) -> dict:
    """Μία άσκηση του εγγράφου (μπλοκ από `CorpusDoc.exercise_blocks`) → {"label", "section", "parts", "figure"}."""
    ex_num = b["ex_num"]
    cur: dict | None = None
    for j in range(b["start"], b["end"]):
        el = cdoc.body[j]
        local = etree.QName(el).localname
        if local not in ("p", "tbl", "sdt"):
            continue
        np_ = cdoc.numpr(el) if local == "p" else None
        if cur is None:
            text, fig = reader.para(el)
            cur = {"label": b["label"], "section": b["section"], "parts": [{"label": "", "md": text, "level": 0}],
                   "figure": fig}
            continue
        if local == "p":
            text, fig = reader.para(el)
            cur["figure"] = cur["figure"] or fig
            if np_ and np_[0] == ex_num and np_[1] > 0:
                cur["parts"].append({"label": labels.get(j, "").strip(), "md": text, "level": np_[1]})
            elif text:
                last = cur["parts"][-1]
                last["md"] = (last["md"] + " " + text).strip()
        else:  # πίνακας (π.χ. ερωτήματα σε στήλες) / πλαίσιο: κάθε παράγραφος χωριστή γραμμή
            level = max(1, cur["parts"][-1].get("level", 0))
            for q in el.iter(f"{{{W_NS}}}p"):
                text, fig = reader.para(q)
                cur["figure"] = cur["figure"] or fig
                if text:
                    cur["parts"].append({"label": "", "md": text, "level": level})
    return cur or {"label": b["label"], "section": b["section"], "parts": [{"label": "", "md": "", "level": 0}],
                   "figure": False}


def doc_exercises(cdoc) -> dict:
    """Οι ασκήσεις του εγγράφου, με τη σειρά τους (ίδια σειρά/δείκτες με `CorpusDoc.exercise_blocks`).
    → {"exercises": [{"label", "section", "parts": [{"label", "md", "level"}], "figure", "fp"}], "equations", "unread"}"""
    reader = _Reader(cdoc.files)
    labels = {idx: lab for idx, _el, lab in cdoc._walk_labels()}
    out = []
    for b in cdoc.exercise_blocks():
        ex = block_exercise(cdoc, reader, b, labels)
        ex["fp"] = fingerprint(ex)
        out.append(ex)
    return {"exercises": out, "equations": reader.equations, "unread": reader.unread}


def fingerprint(ex: dict) -> str:
    """Σύντομο αναγνωριστικό περιεχομένου μιας άσκησης του εγγράφου (δεν αλλάζει με την αρίθμηση)."""
    import hashlib

    return hashlib.sha1(canon(" ".join(p["md"] for p in ex["parts"])).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- αποτύπωμα

_CMD_DROP = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "displaystyle",
             "textstyle", "quad", "qquad", "limits", "nolimits", "middle", "begin", "end", "frac", "dfrac", "tfrac",
             "cfrac", "mathrm", "text", "textrm", "mathit", "mathbf", "textbf", "textit", "operatorname", "rm",
             "it", "bf", "mbox", "hbox", "mathbb", "boldsymbol", "bm", "mathsf", "mathcal", "phantom", "hphantom",
             "vphantom", "displaylines", "tag"}
_CMD_MAP = {
    "vec": "⃗", "overrightarrow": "⃗", "overleftarrow": "⃗", "overleftrightarrow": "⃗", "widehat": "^̂",
    "hat": "^̂", "overline": "‾", "bar": "‾", "underline": "_̲", "widetilde": "~", "tilde": "~", "sqrt": "√",
    "cdot": "⋅", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "leqslant": "≤",
    "ge": "≥", "geq": "≥", "geqslant": "≥", "ne": "≠", "neq": "≠", "approx": "≈", "equiv": "≡", "infty": "∞",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "varnothing": "∅", "to": "→", "rightarrow": "→", "longrightarrow": "→", "leftarrow": "←", "gets": "←",
    "Rightarrow": "⇒", "implies": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⇔",
    "leftrightarrow": "↔", "forall": "∀", "exists": "∃", "perp": "⊥", "parallel": "∥", "angle": "∠",
    "triangle": "△", "circ": "°", "degree": "°", "prime": "'", "ldots": "…", "dots": "…", "cdots": "…",
    "sum": "∑", "prod": "∏", "int": "∫", "lim": "lim", "langle": "⟨", "rangle": "⟩", "lvert": "|", "rvert": "|",
    "vert": "|", "mid": "|", "|": "|", "lVert": "|", "rVert": "|", "Vert": "|", "uparrow": "↑", "downarrow": "↓",
    "upuparrows": "↑↑", "updownarrows": "↑↓", "cong": "≅", "sim": "∼", "simeq": "≃", "neg": "¬", "lnot": "¬",
    "wedge": "∧", "land": "∧", "vee": "∨", "lor": "∨", "%": "%", "#": "#", "&": "", "$": "", "_": "_",
    "{": "", "}": "", ",": "", ";": "", ":": "", "!": "", " ": "", "\\": "", "backslash": "\\",
    "R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "partial": "∂", "nabla": "∇", "triangleq": "≜",
    "overset": "", "underset": "", "stackrel": "", "xrightarrow": "→", "frown": "⌒", "smile": "⌣",
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉", "ddot": "̈", "dot": "̇", "not": "¬",
}
_GREEK_CMD = {v[1:]: k for k, v in GREEK.items()}
_GREEK_CMD.update({"epsilon": "ε", "varepsilon": "ε", "phi": "φ", "varphi": "φ", "theta": "θ",
                   "vartheta": "θ", "pi": "π", "varpi": "π", "rho": "ρ", "varrho": "ρ", "sigma": "σ",
                   "varsigma": "σ", "omicron": "ο", "Alpha": "Α", "Beta": "Β", "Epsilon": "Ε", "Zeta": "Ζ",
                   "Eta": "Η", "Iota": "Ι", "Kappa": "Κ", "Mu": "Μ", "Nu": "Ν", "Omicron": "Ο", "Rho": "Ρ",
                   "Tau": "Τ", "Chi": "Χ"})
_FUNCS = {"sin": "ημ", "cos": "συν", "tan": "εφ", "tg": "εφ", "cot": "σφ", "ctg": "σφ", "log": "log", "ln": "ln",
          "exp": "exp", "max": "max", "min": "min", "det": "det", "lg": "log"}

_BB = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ"}

# λατινικά κεφαλαία που μοιάζουν με ελληνικά (ονόματα σημείων): γίνονται ελληνικά
_LATIN_TO_GREEK = str.maketrans("ABEZHIKMNOPTYX", "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ")
_PUNCT = set(" \t\n.,;:·\u0387!?«»\"“”‘’'`*~$&{}\u00a0\u2003\u2002\u2009\u200a\u200b⇥")
_UNI = {"\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2032": "'", "\u00d7": "×", "\u22c5": "⋅", "\u2219": "⋅",
        "\u2264": "≤", "\u2265": "≥", "\u2a7d": "≤", "\u2a7e": "≥", "\u2260": "≠", "\u2192": "→", "\u21d2": "⇒",
        "\u21d4": "⇔", "\u2223": "|", "\u2016": "|", "\u2225": "∥", "\u2026": "…", "\u03f5": "ε", "\u03d5": "φ",
"\u211d": "ℝ", "\u2206": "△", "\u25b3": "△", "\u2220": "∠", "\u00b0": "°", "\u2218": "°"}


def _latex_tokens(s: str, keep_slash: bool = False) -> str:
    """LaTeX/Markdown → σειρά συμβόλων «όπως διαβάζονται» (χωρίς εντολές, άγκιστρα και $)."""
    s = s.replace("⇥", " ")
    s = re.sub(r"\[\[[^\]]*\]\]", " ", s)                        # παραπομπές σε ερωτήματα
    s = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})?", " ", s)     # περιβάλλοντα (array{l} κ.λπ.)
    s = re.sub(r"\\sqrt\[([^\]]*)\]", r"√\1", s)
    s = re.sub(r"\\mathbb\s*\{?\s*([A-Z])\s*\}?", lambda m: _BB.get(m.group(1), m.group(1)), s)

    def cmd(m: re.Match) -> str:
        name = m.group(1)
        if name in _GREEK_CMD:
            return _GREEK_CMD[name]
        if name in _FUNCS:
            return _FUNCS[name]
        if name in _CMD_MAP:
            return _CMD_MAP[name]
        if name in _CMD_DROP:
            return ""
        return name

    s = re.sub(r"\\([A-Za-z]+|.)", cmd, s)
    if not keep_slash:
        s = s.replace("/", "")  # 1/2 ≈ \frac{1}{2}
    return s


def _base(md: str) -> str:
    s = _latex_tokens(md or "")
    s = "".join(_UNI.get(c, c) for c in s)
    s = s.translate(_LATIN_TO_GREEK)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn" or c in "\u20d7\u0302")
    return unicodedata.normalize("NFC", s).lower().replace("ς", "σ")


def canon(md: str) -> str:
    """Το «δακτυλικό αποτύπωμα» ενός κειμένου άσκησης."""
    s = _base(md)
    s = "".join(c for c in s if c not in _PUNCT and not c.isspace())
    s = re.sub(r"^(\d+|[α-ωa-z])[.)]", "", s)  # χειρόγραφη αρίθμηση στην αρχή
    return s


def mask_numbers(c: str) -> str:
    return re.sub(r"\d+", "#", c)


def _group(s: str, i: int) -> tuple[str, int] | None:
    """Το περιεχόμενο του {…} που ξεκινά στη θέση i (μετά από κενά) και η θέση μετά το «}»."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return None
    if s[i] != "{":
        if s[i] == "\\":
            m = re.match(r"\\[A-Za-z]+|\\.", s[i:])
            return (m.group(0), i + len(m.group(0))) if m else None
        return s[i], i + 1
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


_PRETTY_ONE = {
    "overrightarrow": lambda a: a + "\u20d7", "vec": lambda a: a + "\u20d7",
    "widehat": lambda a: ("∡" + a) if ("," in a or len(a) > 3) else a + "\u0302", "hat": lambda a: a + "\u0302", "overline": lambda a: a + "\u0305", "bar": lambda a: a + "\u0305",
    "sqrt": lambda a: "√" + (a if len(a) <= 1 else f"({a})"), "mathrm": str, "text": str, "textrm": str,
    "mathit": str, "mathbf": str, "operatorname": str, "boldsymbol": str, "underline": str,
    "mathbb": lambda a: _BB.get(a.strip(), a),
}


def pretty(md: str) -> str:
    """LaTeX/Markdown → απλό κείμενο για ανάγνωση (π.χ. «\\overrightarrow{ΑΒ}=(1,2)» → «ΑΒ⃗=(1,2)»)."""
    s = (md or "").replace("$", " ").replace("⇥", " ")
    out: list[str] = []
    i = 0
    while i < len(s):
        m = re.match(r"\\([A-Za-z]+)", s[i:])
        if m and (m.group(1) in _PRETTY_ONE or m.group(1) in ("frac", "dfrac", "tfrac")):
            name = m.group(1)
            g1 = _group(s, i + len(m.group(0)))
            if g1 is None:
                i += len(m.group(0))
                continue
            a, j = pretty(g1[0]).strip(), g1[1]
            if name in ("frac", "dfrac", "tfrac"):
                g2 = _group(s, j)
                b, j = (pretty(g2[0]).strip(), g2[1]) if g2 else ("", j)
                wrap = lambda x: x if len(x) <= 1 or x.isalnum() else f"({x})"  # noqa: E731
                out.append(f"{wrap(a)}/{wrap(b)}")
            else:
                out.append(_PRETTY_ONE[name](a))
            i = j
            continue
        if s[i] in "^_" and i + 1 < len(s):
            g = _group(s, i + 1)
            if g:
                a = pretty(g[0]).strip()
                out.append(s[i] + (a if len(a) <= 1 else f"({a})"))
                i = g[1]
                continue
        out.append(s[i])
        i += 1
    t = _latex_tokens("".join(out), keep_slash=True).replace("{", "").replace("}", "").replace("^°", "°")
    t = "".join(_UNI.get(c, c) for c in t)
    return " ".join(t.split())


def _readable(md: str) -> list[str]:
    """Λέξεις/σύμβολα για την εμφάνιση των διαφορών."""
    return re.findall(r"[\w'\u0300-\u036f\u20d0-\u20ff]+|[^\w\s]", pretty(md))


def differences(a_md: str, b_md: str, limit: int = 8) -> list[dict]:
    """Τα σημεία όπου διαφέρουν δύο ασκήσεις: [{"old", "new"}] (old = του εγγράφου, new = η νέα)."""
    a, b = _readable(a_md), _readable(b_md)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        ctx_l = a[max(0, i1 - 2):i1]
        ctx_r = a[i2:i2 + 1] if (i1 == i2 or j1 == j2) else []
        old, new = _join(ctx_l + a[i1:i2] + ctx_r), _join(ctx_l + b[j1:j2] + ctx_r)
        if canon(old) == canon(new):
            continue
        out.append({"old": _tidy(old), "new": _tidy(new)})
        if len(out) >= limit:
            break
    return out


def _join(tokens: list[str]) -> str:
    """Λέξεις με κενό ανάμεσα, σύμβολα κολλητά: «Α ( - 3 , 2 )» → «Α(-3,2)»."""
    out = ""
    for t in tokens:
        if out and out[-1].isalnum() and t[:1].isalnum():
            out += " "
        out += t
    return out


def _tidy(s: str) -> str:
    s = re.sub(r"\s+([,.;:)\]}'])", r"\1", s)
    s = re.sub(r"([(\[{])\s+", r"\1", s)
    return s.strip()


# --------------------------------------------------------------------------- σύγκριση

def _trigrams(s: str) -> set[str]:
    return {s[k:k + 3] for k in range(max(1, len(s) - 2))} if s else set()


def _dice(a: set, b: set) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


class _Entry:
    __slots__ = ("ref", "md", "c", "m", "tc", "tm", "parts")

    def __init__(self, ref: dict, md: str, parts: list[tuple[str, str]]):
        self.ref = ref
        self.md = md
        self.c = canon(md)
        self.m = mask_numbers(self.c)
        self.tc = _trigrams(self.c)
        self.tm = _trigrams(self.m)
        self.parts = [(lab, canon(p)) for lab, p in parts]


def new_exercise_parts(ex: dict) -> list[tuple[str, str]]:
    """Νέα άσκηση (JSON της μεταγραφής) → [(θέση, κείμενο)] — η εκφώνηση (θέση "") και κάθε ερώτημα
    (θέση "0", "0.1", … : δείκτες μέσα στα items, ώστε η οθόνη να δείξει τον αριθμό που θα πάρει)."""
    out = [("", ex.get("stem") or "")]

    def walk(items, prefix):
        for k, it in enumerate(items or []):
            text = it.get("text") or ""
            ch = it.get("choices") or {}
            if ch.get("options"):
                text += " " + " ".join(str(o) for o in ch["options"])
            path = f"{prefix}.{k}" if prefix else str(k)
            out.append((path, text))
            walk(it.get("items"), path)

    walk(ex.get("items"), "")
    if (ex.get("closing") or "").strip():
        out[-1] = (out[-1][0], out[-1][1] + " " + ex["closing"])
    return out


_NUM = re.compile(r"\d+")


def _compare(new: _Entry, old: _Entry, cfg: dict) -> tuple[str, float] | None:
    n_len, o_len = len(new.c), len(old.c)
    if not n_len or not o_len:
        return None
    if new.c == old.c:
        return "same", 1.0
    short = min(n_len, o_len) < MIN_LEN
    # γρήγορο φίλτρο με τριγράμματα πριν από την ακριβή (και πιο αργή) σύγκριση
    dc, dm = _dice(new.tc, old.tc), _dice(new.tm, old.tm)
    if dc < 0.55 and dm < 0.8:
        return None
    r = _ratio(new.c, old.c)
    # «ίδια» μόνο αν ταυτίζονται και οι αριθμοί· αλλιώς είναι ίδια διατύπωση με άλλα νούμερα
    if r >= SAME and _NUM.findall(new.c) == _NUM.findall(old.c):
        return "same", r
    nmin = cfg.get("numbers_min")
    if nmin is not None and dm >= 0.8 and (r >= SAME or (not short and min(len(new.m), len(old.m)) >= nmin)):
        rm = _ratio(new.m, old.m)
        if rm >= SAME:
            return "numbers", rm
    if short:
        return None
    sim = cfg.get("similar")
    if sim is not None and min(n_len, o_len) >= 30 and r >= sim:
        return "similar", r
    return None


def check(new_exs: list[dict], existing: list[dict], sensitivity: str = "normal") -> list[list[dict]]:
    """Για κάθε νέα άσκηση, οι πιθανές αντιστοιχίες (το πολύ MAX_MATCHES, οι σοβαρότερες πρώτες).

    Κάθε αντιστοιχία: {"level": same|part|numbers|similar, "score", "kind": doc|batch, "label", "section",
                       "parts", "part": ετικέτα νέου ερωτήματος, "other_part": ετικέτα του άλλου, "index",
                       "diffs"}."""
    cfg = SENSITIVITY.get(sensitivity, SENSITIVITY["normal"])
    if cfg is None:
        return [[] for _ in new_exs]
    olds = [_Entry({"kind": "doc", "label": e["label"], "section": e["section"], "parts": e["parts"], "fp": e.get("fp", "")},
                   " ".join(p["md"] for p in e["parts"]), [(p["label"], p["md"]) for p in e["parts"]])
            for e in existing]
    news = []
    for i, ex in enumerate(new_exs):
        parts = new_exercise_parts(ex)
        news.append(_Entry({"kind": "batch", "index": i}, " ".join(t for _, t in parts), parts))

    # ευρετήριο ερωτημάτων του εγγράφου: αποτύπωμα → [(άσκηση, ετικέτα)]
    part_index: dict[str, list[tuple[int, str]]] = {}
    for oi, o in enumerate(olds):
        for lab, c in o.parts[1:] if len(o.parts) > 1 else []:
            if len(c) >= PART_MIN and re.search(r"\d", c):
                part_index.setdefault(c, []).append((oi, lab))

    rank = {lv: k for k, lv in enumerate(LEVELS)}
    results: list[list[dict]] = []
    for i, n in enumerate(news):
        found: list[dict] = []
        matched_docs: set[int] = set()
        for oi, o in enumerate(olds):
            res = _compare(n, o, cfg)
            if res:
                matched_docs.add(oi)
                found.append({"level": res[0], "score": round(res[1], 3), **o.ref,
                              "diffs": [] if res[0] == "same" and res[1] == 1.0 else differences(o.md, n.md)})
        # ίδιο ερώτημα σε άλλη άσκηση του εγγράφου
        for lab, c in n.parts[1:]:
            if len(c) < PART_MIN or not re.search(r"\d", c):
                continue
            for oi, olab in part_index.get(c, []):
                if oi in matched_docs:
                    continue
                matched_docs.add(oi)
                found.append({"level": "part", "score": 1.0, **olds[oi].ref, "part": lab, "other_part": olab,
                              "diffs": []})
        # ίδια άσκηση μέσα στις νέες φωτογραφίες (μόνο με τις προηγούμενες, για να μη διπλομετρά)
        for k in range(i):
            res = _compare(n, news[k], cfg)
            if res:
                found.append({"level": res[0], "score": round(res[1], 3), "kind": "batch", "index": k,
                              "diffs": [] if res[0] == "same" and res[1] == 1.0 else differences(news[k].md, n.md)})
        found.sort(key=lambda f: (rank[f["level"]], -f["score"]))
        results.append(found[:MAX_MATCHES])
    return results


# --------------------------------------------------------------------------- έλεγχος ολόκληρου αρχείου

MAX_PAIRS = 800


def _doc_entry(i: int, e: dict) -> _Entry:
    return _Entry({"i": i}, " ".join(p["md"] for p in e["parts"]), [(p["label"], p["md"]) for p in e["parts"]])


def _part_index(entries: list[_Entry]) -> dict[str, list[tuple[int, str]]]:
    idx: dict[str, list[tuple[int, str]]] = {}
    for k, en in enumerate(entries):
        for lab, c in en.parts[1:]:
            if len(c) >= PART_MIN and re.search(r"\d", c):
                idx.setdefault(c, []).append((k, lab))
    return idx


def audit(exs_a: list[dict], exs_b: list[dict] | None = None, sensitivity: str = "normal",
          progress=None) -> dict:
    """Έλεγχος ολόκληρου αρχείου: ζεύγη ίδιων/παρόμοιων ασκήσεων μέσα στο Α και, αν δοθεί, ανάμεσα σε Β και Α.

    → {"pairs": [{"level", "score", "a": δείκτης στο Α, "b": δείκτης (στο Α ή στο Β), "cross": bool,
                  "part", "other_part", "diffs"}], "truncated": bool}
    Στα ζεύγη «part»: `part` = ερώτημα της άσκησης `b`, `other_part` = ερώτημα της άσκησης `a`."""
    cfg = SENSITIVITY.get(sensitivity, SENSITIVITY["normal"]) or SENSITIVITY["normal"]
    ea = [_doc_entry(i, e) for i, e in enumerate(exs_a)]
    eb = [_doc_entry(i, e) for i, e in enumerate(exs_b or [])]
    pidx_a = _part_index(ea)
    pairs: list[dict] = []
    total = len(ea) + len(eb)

    def add(level, score, ia, ib, cross, a_md, b_md, part="", other_part=""):
        pairs.append({"level": level, "score": round(score, 3), "a": ia, "b": ib, "cross": cross,
                      "part": part, "other_part": other_part,
                      "diffs": [] if level in ("same", "part") and score == 1.0 else differences(a_md, b_md)})

    def scan(n: _Entry, nb: int, against: list[_Entry], start: int, cross: bool):
        matched = set()
        for k in range(start, len(against)):
            o = against[k]
            res = _compare(n, o, cfg)
            if res:
                matched.add(k)
                add(res[0], res[1], k, nb, cross, o.md, n.md)
        for lab, c in n.parts[1:]:
            if len(c) < PART_MIN or not re.search(r"\d", c):
                continue
            for k, olab in pidx_a.get(c, []):
                if k in matched or (not cross and k <= nb):
                    continue
                matched.add(k)
                add("part", 1.0, k, nb, cross, against[k].md, n.md, part=lab, other_part=olab)

    # μέσα στο Α: κάθε ζεύγος μία φορά (a < b)
    for i, n in enumerate(ea):
        scan(n, i, ea, i + 1, False)
        if progress and i % 25 == 0:
            progress(i, total)
    # το Β απέναντι στο Α
    for j, n in enumerate(eb):
        scan(n, j, ea, 0, True)
        if progress and j % 25 == 0:
            progress(len(ea) + j, total)

    # μέσα στο Α το «a» είναι πάντα η προηγούμενη άσκηση· τα «part» προέκυψαν με ανάποδη σειρά
    for pr in pairs:
        if not pr["cross"] and pr["a"] > pr["b"]:
            pr["a"], pr["b"] = pr["b"], pr["a"]
            pr["part"], pr["other_part"] = pr["other_part"], pr["part"]
    rank = {lv: k for k, lv in enumerate(LEVELS)}
    pairs.sort(key=lambda p: (rank[p["level"]], -p["score"], p["cross"], p["a"], p["b"]))
    return {"pairs": pairs[:MAX_PAIRS], "truncated": len(pairs) > MAX_PAIRS, "total": len(pairs)}


# --------------------------------------------------------------------------- ευρετήριο εγγράφου (με cache)

_CACHE: dict[str, tuple[tuple, dict]] = {}


def index_of(path: str | Path) -> dict:
    """Οι ασκήσεις του αρχείου — ξαναδιαβάζεται μόνο αν το αρχείο άλλαξε."""
    from .corpus_doc import CorpusDoc

    p = Path(path)
    st = p.stat()
    key = str(p.resolve())
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    data = doc_exercises(CorpusDoc(p))
    _CACHE.pop(key, None)
    while len(_CACHE) >= 4:  # λίγα αρχεία στη μνήμη (η συλλογή και όσα συγκρίνονται μαζί της)
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (sig, data)
    return data
