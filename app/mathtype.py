"""Ανάγνωση εξισώσεων MathType μέσα σε έγγραφα Word.

Κάθε εξίσωση MathType σε .docx είναι αντικείμενο OLE (`word/embeddings/oleObjectN.bin`) που κρατά,
εκτός από την εικόνα, το ίδιο το περιεχόμενο του τύπου στη ροή «Equation Native» (μορφή MTEF 5,
δημοσιευμένη από την Design Science / Wiris). Εδώ τη μετατρέπουμε σε LaTeX, ώστε:
- να συγκρίνονται οι ασκήσεις του εγγράφου με τις νέες (έλεγχος διπλότυπων)·
- να εμφανίζονται στην οθόνη με KaTeX.

Ο μετατροπέας είναι «ανεκτικός»: ό,τι δεν αναγνωρίζει το παραλείπει και δεν σταματά ποτέ με σφάλμα
(επιστρέφει None μόνο αν το αντικείμενο δεν είναι εξίσωση MathType).
"""

from __future__ import annotations

import io
import struct

# --------------------------------------------------------------------------- σύμβολα → LaTeX

GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ϵ": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi", "ϕ": r"\phi",
    "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi",
    "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}

SYMBOLS = {
    "−": "-", "×": r"\times", "⋅": r"\cdot", "·": r"\cdot", "∙": r"\cdot",
    "÷": r"\div", "±": r"\pm", "∓": r"\mp", "≤": r"\le", "≥": r"\ge",
    "≠": r"\ne", "≈": r"\approx", "≡": r"\equiv", "∞": r"\infty", "∈": r"\in",
    "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq", "∪": r"\cup", "∩": r"\cap",
    "∅": r"\varnothing", "→": r"\to", "←": r"\leftarrow", "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow", "↔": r"\leftrightarrow", "⇐": r"\Leftarrow", "∀": r"\forall",
    "∃": r"\exists", "∂": r"\partial", "∇": r"\nabla", "⊥": r"\perp", "∥": r"\parallel",
    "∠": r"\angle", "△": r"\triangle", "∆": r"\triangle", "°": r"^{\circ}", "∘": r"\circ",
    "′": "'", "″": "''", "…": r"\ldots", "⋯": r"\cdots", "ℝ": r"\mathbb{R}",
    "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    "∑": r"\sum", "∏": r"\prod", "∫": r"\int", "√": r"\sqrt{}", "‖": r"\|",
    "⟨": r"\langle", "⟩": r"\rangle", "〈": r"\langle", "〉": r"\rangle",
    "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil",
    "≅": r"\cong", "∼": r"\sim", "≃": r"\simeq", "∝": r"\propto", "∧": r"\wedge",
    "∨": r"\vee", "¬": r"\neg", "‖": r"\|", "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#",
    "&": r"\&", "$": r"\$", "_": r"\_", "~": r"\sim", "\\": r"\backslash", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ",
}

# ενσωματωμένα «στολίδια» χαρακτήρων (embellishments) → LaTeX
EMBELL = {
    2: r"\dot{%s}", 3: r"\ddot{%s}", 4: r"\dddot{%s}", 5: "%s'", 6: "%s''", 7: "'%s", 8: r"\tilde{%s}",
    9: r"\hat{%s}", 10: r"\not{%s}", 11: r"\vec{%s}", 12: r"\overleftarrow{%s}", 13: r"\overleftrightarrow{%s}",
    14: r"\vec{%s}", 15: r"\overleftarrow{%s}", 16: r"\bar{%s}", 17: r"\overline{%s}", 18: "%s'''",
    19: r"\overset{\frown}{%s}", 20: r"\overset{\smile}{%s}", 24: r"\ddot{%s}",
}

FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max", "min", "sup",
         "inf", "det", "deg", "arg", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan"}

# τυπογραφικές «οικογένειες» (typeface) του MathType
FN_TEXT, FN_FUNCTION, FN_VARIABLE, FN_LCGREEK, FN_UCGREEK, FN_SYMBOL, FN_VECTOR, FN_NUMBER = 1, 2, 3, 4, 5, 6, 7, 8
FN_EXPAND, FN_MARKER, FN_SPACE = 22, 23, 24


def char_latex(ch: str) -> str:
    if ch in GREEK:
        return GREEK[ch] + " "
    if ch in SYMBOLS:
        s = SYMBOLS[ch]
        return s + " " if s.startswith("\\") and s[-1].isalpha() else s
    return ch


# --------------------------------------------------------------------------- δέντρο MTEF

class _Node:
    __slots__ = ("kind", "children", "data")

    def __init__(self, kind: str, data=None):
        self.kind = kind          # line / char / tmpl / pile / matrix
        self.children: list[_Node] = []
        self.data = data or {}


class _Parser:
    def __init__(self, buf: bytes):
        self.b = buf
        self.i = 0

    # -- βασικά
    def u8(self) -> int:
        v = self.b[self.i]
        self.i += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.b, self.i)[0]
        self.i += 2
        return v

    def cstr(self) -> str:
        j = self.b.index(b"\x00", self.i)
        s = self.b[self.i:j].decode("latin-1")
        self.i = j + 1
        return s

    def nudge(self) -> None:
        dx, dy = self.u8(), self.u8()
        if dx == 128 and dy == 128:
            self.i += 4

    def dims(self) -> None:
        """Πίνακας διαστάσεων σε «nibbles» (EQN_PREFS): count, και κάθε τιμή τελειώνει με 0xF."""
        count = self.u8()
        seen = 0
        hi = True
        while seen < count:
            byte = self.b[self.i]
            nib = (byte >> 4) if hi else (byte & 0x0F)
            if not hi:
                self.i += 1
            hi = not hi
            if nib == 0xF:
                seen += 1
        if not hi:  # στοίχιση σε byte
            self.i += 1

    def ruler(self) -> None:
        n = self.u8()
        self.i += 3 * n

    # -- εγγραφές
    def header(self) -> None:
        version = self.u8()
        if version != 5:
            raise ValueError(f"MTEF {version}")
        self.i += 4           # platform, product, version, subversion
        self.cstr()           # application key, π.χ. "DSMT6"
        self.u8()             # equation options

    def skip_record(self, tag: int) -> bool:
        """Εγγραφές χωρίς περιεχόμενο για εμάς. True αν αναγνωρίστηκε."""
        if tag in (10, 11, 12, 13, 14):          # FULL, SUB, SUB2, SYM, SUBSYM
            return True
        if tag == 9:                              # SIZE
            lsize = self.u8()
            if lsize == 101:
                self.i += 2
            elif lsize == 100:
                self.i += 3
            else:
                self.i += 1
            return True
        if tag == 8:                              # FONT_STYLE_DEF
            self.i += 2
            return True
        if tag == 15:                             # COLOR
            self.i += 1
            return True
        if tag == 16:                             # COLOR_DEF
            opts = self.u8()
            self.i += 8 if opts & 1 else 6
            if opts & 4:
                self.cstr()
            return True
        if tag == 17:                             # FONT_DEF
            self.u8()
            self.cstr()
            return True
        if tag == 18:                             # EQN_PREFS
            self.u8()
            self.dims()
            self.dims()
            n = self.u8()
            for _ in range(n):
                if self.u8():
                    self.u8()
            return True
        if tag == 19:                             # ENCODING_DEF
            self.cstr()
            return True
        if tag == 7:                              # RULER
            self.ruler()
            return True
        if tag >= 100:                            # FUTURE
            n = self.u16()
            self.i += n
            return True
        return False

    def obj_list(self, parent: _Node) -> None:
        while self.i < len(self.b):
            tag = self.u8()
            if tag == 0:
                return
            node = self.record(tag)
            if node is not None:
                parent.children.append(node)

    def record(self, tag: int) -> _Node | None:
        if tag == 1:
            return self.line()
        if tag == 2:
            return self.char()
        if tag == 3:
            return self.tmpl()
        if tag == 4:
            return self.pile()
        if tag == 5:
            return self.matrix()
        if tag == 6:                              # EMBELL εκτός χαρακτήρα (σπάνιο)
            opts = self.u8()
            if opts & 0x08:
                self.nudge()
            self.u8()
            return None
        if self.skip_record(tag):
            return None
        raise ValueError(f"άγνωστη εγγραφή {tag} στη θέση {self.i - 1}")

    def line(self) -> _Node:
        opts = self.u8()
        node = _Node("line")
        if opts & 0x08:
            self.nudge()
        if opts & 0x04:
            self.i += 2                           # line spacing
        if opts & 0x02:
            if self.u8() != 7:
                raise ValueError("αναμενόταν RULER")
            self.ruler()
        if not opts & 0x01:                       # όχι «κενή γραμμή»
            self.obj_list(node)
        return node

    def char(self) -> _Node:
        opts = self.u8()
        if opts & 0x08:
            self.nudge()
        tf = self.u8() - 128
        code = None
        if not opts & 0x20:
            code = self.u16()
        if opts & 0x04:
            self.i += 1
        if opts & 0x10:
            self.i += 2
        node = _Node("char", {"tf": tf, "ch": chr(code) if code is not None else "", "func": bool(opts & 0x02),
                              "emb": []})
        if opts & 0x01:
            while True:
                tag = self.u8()
                if tag == 0:
                    break
                if tag == 6:
                    eo = self.u8()
                    if eo & 0x08:
                        self.nudge()
                    node.data["emb"].append(self.u8())
                elif not self.skip_record(tag):
                    raise ValueError(f"άγνωστη εγγραφή {tag} σε στολίδι")
        return node

    def tmpl(self) -> _Node:
        opts = self.u8()
        if opts & 0x08:
            self.nudge()
        sel = self.u8()
        var = self.u8()
        if var & 0x80:
            var = (var & 0x7F) | (self.u8() << 8)
        self.u8()                                 # template-specific options
        node = _Node("tmpl", {"sel": sel, "var": var})
        self.obj_list(node)
        return node

    def pile(self) -> _Node:
        opts = self.u8()
        if opts & 0x08:
            self.nudge()
        self.i += 2                               # halign, valign
        if opts & 0x02:
            if self.u8() != 7:
                raise ValueError("αναμενόταν RULER")
            self.ruler()
        node = _Node("pile")
        self.obj_list(node)
        return node

    def matrix(self) -> _Node:
        opts = self.u8()
        if opts & 0x08:
            self.nudge()
        self.i += 3                               # valign, h_just, v_just
        rows, cols = self.u8(), self.u8()
        self.i += ((rows + 1) * 2 + 7) // 8
        self.i += ((cols + 1) * 2 + 7) // 8
        node = _Node("matrix", {"rows": rows, "cols": cols})
        self.obj_list(node)
        return node

    def parse(self) -> _Node:
        self.header()
        root = _Node("line")
        self.obj_list(root)
        return root


# --------------------------------------------------------------------------- δέντρο → LaTeX

def _brace(s: str) -> str:
    return "{" + s.strip() + "}"


def _lines(node: _Node) -> list[_Node]:
    return [c for c in node.children if c.kind in ("line", "pile", "matrix")]


def _chars(node: _Node) -> list[_Node]:
    return [c for c in node.children if c.kind == "char"]


def _fence(ch: str) -> str:
    if not ch or 0xE000 <= ord(ch) <= 0xF8FF:   # φράκτης του MT Extra (π.χ. |·| μέτρου): προεπιλογή
        return ""
    if ch in ("{", "}"):
        return "\\" + ch
    return char_latex(ch).strip() or ch


_DEFAULT_FENCES = {0: ("\\langle ", "\\rangle "), 1: ("(", ")"), 2: ("\\{", "\\}"), 3: ("[", "]"), 4: ("|", "|"),
                   5: ("\\|", "\\|"), 6: ("\\lfloor ", "\\rfloor "), 7: ("\\lceil ", "\\rceil "), 8: ("[", "]"),
                   9: ("[", "]")}


def _emit_seq(nodes: list[_Node]) -> str:
    out: list[str] = []
    i = 0
    while i < len(nodes):
        n = nodes[i]
        if n.kind == "char" and n.data["tf"] in (FN_TEXT, FN_FUNCTION):
            # συνεχόμενοι χαρακτήρες κειμένου / ονόματος συνάρτησης (ημ, συν, ln, lim, …)
            tf = n.data["tf"]
            j = i
            word = ""
            while (j < len(nodes) and nodes[j].kind == "char" and nodes[j].data["tf"] == tf
                   and not nodes[j].data["emb"] and (j == i or not nodes[j].data["func"])):
                word += nodes[j].data["ch"]
                j += 1
            if j == i:  # χαρακτήρας με στολίδι
                out.append(_emit(n))
                i += 1
                continue
            stripped = word.strip()
            if tf == FN_FUNCTION and stripped in FUNCS:
                out.append("\\" + stripped + " ")
            elif tf == FN_FUNCTION and stripped and not any(c.isalpha() for c in stripped):
                out.append("".join(char_latex(c) for c in stripped))
            elif tf == FN_FUNCTION and stripped:
                out.append("\\mathrm{" + stripped + "}")
            elif stripped:
                out.append("\\text{" + word.replace("{", "\\{").replace("}", "\\}") + "}")
            else:
                out.append(" ")
            i = j
            continue
        out.append(_emit(n))
        i += 1
    return "".join(out)


def _emit(n: _Node) -> str:
    if n.kind == "line":
        return _emit_seq(n.children)
    if n.kind == "char":
        tf, ch = n.data["tf"], n.data["ch"]
        if tf in (FN_SPACE, FN_MARKER) or not ch:
            return ""
        if 0xE000 <= ord(ch) <= 0xF8FF:       # ιδιωτικοί χαρακτήρες του MathType (κενά κ.λπ.)
            return ""
        if 0x0300 <= ord(ch) <= 0x036F or 0x20D0 <= ord(ch) <= 0x20FF:  # συνδυαστικά (βέλη/τόνοι προτύπων)
            return ""
        s = char_latex(ch)
        for e in n.data["emb"]:
            fmt = EMBELL.get(e)
            if fmt:
                s = fmt % s.strip()
        return s
    if n.kind == "pile":
        rows = [_emit(c).strip() for c in _lines(n)]
        if len(rows) <= 1:
            return rows[0] if rows else ""
        return "\\begin{array}{l}" + " \\\\ ".join(rows) + "\\end{array}"
    if n.kind == "matrix":
        cells = [_emit(c).strip() for c in _lines(n)]
        cols = max(1, n.data["cols"])
        rows = [" & ".join(cells[k:k + cols]) for k in range(0, len(cells), cols)]
        return "\\begin{matrix}" + " \\\\ ".join(rows) + "\\end{matrix}"
    if n.kind == "tmpl":
        return _emit_tmpl(n)
    return ""


def _emit_tmpl(n: _Node) -> str:
    sel = n.data["sel"]
    ls = [_emit(c) for c in _lines(n)]
    cs = [c.data["ch"] for c in _chars(n)]
    s = lambda k: ls[k].strip() if k < len(ls) else ""  # noqa: E731
    if sel <= 9:                                  # φράκτες: () [] {} || ⟨⟩ κ.λπ.
        dl, dr = _DEFAULT_FENCES.get(sel, ("(", ")"))
        var = n.data["var"]
        cs = [c for c in cs if _fence(c)] if any(_fence(c) for c in cs) else []
        if cs:
            if sel == 9 or len(cs) >= 2:
                dl, dr = _fence(cs[0]), _fence(cs[1]) if len(cs) > 1 else dr
            elif var & 0x0001 and not var & 0x0002:
                dl, dr = _fence(cs[0]), ""
            elif var & 0x0002 and not var & 0x0001:
                dl, dr = "", _fence(cs[0])
            else:
                dl = _fence(cs[0])
        else:
            if var and not var & 0x0001:
                dl = ""
            if var and not var & 0x0002:
                dr = ""
        if dl and not dr:
            return "\\left" + (dl.strip() or ".") + " " + s(0) + "\\right."
        if dr and not dl:
            return "\\left. " + s(0) + "\\right" + dr.strip()
        return dl + s(0) + dr
    if sel == 10:                                 # ρίζα
        return "\\sqrt[" + s(1) + "]" + _brace(s(0)) if s(1) else "\\sqrt" + _brace(s(0))
    if sel == 11:                                 # κλάσμα
        return "\\frac" + _brace(s(0)) + _brace(s(1))
    if sel == 12:
        return "\\underline" + _brace(s(0))
    if sel == 13:
        return "\\overline" + _brace(s(0))
    if sel == 14:                                 # βέλος με κείμενο
        return "\\xrightarrow" + _brace(s(0) or s(1))
    if sel in (15, 16, 17, 18, 19, 20, 21, 22):   # ολοκληρώματα / αθροίσματα / γινόμενα
        sym = {15: "\\int", 16: "\\sum", 17: "\\prod", 18: "\\coprod", 19: "\\bigcup", 20: "\\bigcap",
               21: "\\int", 22: "\\sum"}[sel]
        if cs:
            sym = char_latex(cs[0]).strip() or sym
        out = sym
        if s(1):
            out += "_" + _brace(s(1))
        if s(2):
            out += "^" + _brace(s(2))
        return out + " " + s(0)
    if sel == 23:                                 # όριο
        out = s(0) or "\\lim"
        if s(1):
            out += "_" + _brace(s(1))
        if s(2):
            out += "^" + _brace(s(2))
        return out + " "
    if sel in (24, 25):
        return ("\\underbrace" if sel == 24 else "\\underline") + _brace(s(0)) + (("_" + _brace(s(1))) if s(1) else "")
    if sel == 27:                                 # δείκτης
        return "_" + _brace(s(0))
    if sel == 28:                                 # εκθέτης
        return "^" + _brace(s(1) or s(0))
    if sel == 29:
        out = ""
        if s(0):
            out += "_" + _brace(s(0))
        if s(1):
            out += "^" + _brace(s(1))
        return out
    if sel == 31:                                 # διάνυσμα
        var = n.data["var"]
        cmd = "\\overleftarrow" if (var & 0x0001 and not var & 0x0002) else (
            "\\overleftrightarrow" if (var & 0x0001 and var & 0x0002) else "\\overrightarrow")
        return cmd + _brace(s(0))
    if sel == 32:
        return "\\widetilde" + _brace(s(0))
    if sel == 33:
        return "\\widehat" + _brace(s(0))
    if sel == 34:
        return "\\overset{\\frown}" + _brace(s(0))
    return " ".join(x.strip() for x in ls if x.strip())


# --------------------------------------------------------------------------- δημόσιες συναρτήσεις

def mtef_to_latex(data: bytes) -> str | None:
    """Η ροή «Equation Native» (με την κεφαλίδα OLE των 28 bytes ή χωρίς) → LaTeX."""
    if len(data) < 12:
        return None
    off = 0
    hdr = struct.unpack_from("<H", data, 0)[0]
    if hdr == 28 and data[28:29] == b"\x05":
        off = 28
    p = _Parser(data[off:])
    root = _Node("line")
    try:
        p.header()
        p.obj_list(root)
    except Exception:  # noqa: BLE001  — ό,τι διαβάστηκε μέχρι το σφάλμα μένει
        if not root.children:
            return None
    tex = _emit(root)
    return " ".join(tex.split())


def ole_to_latex(blob: bytes) -> str | None:
    """Αρχείο oleObjectN.bin του Word → LaTeX (None αν δεν είναι εξίσωση MathType)."""
    try:
        import olefile
    except ImportError:  # pragma: no cover
        return None
    try:
        ole = olefile.OleFileIO(io.BytesIO(blob))
        try:
            if not ole.exists("Equation Native"):
                return None
            data = ole.openstream("Equation Native").read()
        finally:
            ole.close()
    except Exception:  # noqa: BLE001
        return None
    return mtef_to_latex(data)
