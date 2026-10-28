"""Corpus: εισαγωγή ασκήσεων μέσα σε ΥΠΑΡΧΟΝ έγγραφο Word της καθηγήτριας.

Διαφορά από το διαγώνισμα: εδώ δεν φτιάχνουμε νέο έγγραφο. Ανοίγουμε το δικό της αρχείο,
βρίσκουμε την ενότητα (π.χ. «6. ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ») και προσθέτουμε τις ασκήσεις στο τέλος της,
με τη ΔΙΚΗ ΤΗΣ μορφοποίηση:

- αρίθμηση: η αυτόματη λίστα που ήδη έχει η ενότητα (π.χ. 6.1, 6.2, … με υποερωτήματα a., b.)·
  αν η ενότητα δεν έχει ακόμα λίστα, δημιουργείται νέα στα πρότυπα της προηγούμενης ενότητας·
- στυλ παραγράφου, διάστιχο, στοίχιση, γραμματοσειρά και μέγεθος: αντιγράφονται από μια
  υπάρχουσα παράγραφο άσκησης («δότρια» παράγραφος)·
- κενές αριθμημένες παράγραφοι που έχει ετοιμάσει (θέσεις για ασκήσεις) αφαιρούνται και τη θέση
  τους παίρνουν οι πραγματικές ασκήσεις.

Τα υπόλοιπα μέρη του αρχείου (εικόνες, εξισώσεις MathType, κεφαλίδες, πίνακας περιεχομένων)
μένουν ανέγγιχτα: αλλάζουν μόνο document.xml, numbering.xml, οι σχέσεις και οι νέες εικόνες.
Οι εξισώσεις γράφονται ως εξισώσεις Word (OMML) μέσω pandoc.
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .paths import pandoc_path

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
}
W = "{%s}" % NS["w"]
M = "{%s}" % NS["m"]
R = "{%s}" % NS["r"]
IMG_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
MD_FORMAT = "markdown+tex_math_dollars-implicit_figures-fancy_lists-pipe_tables-simple_tables-multiline_tables-grid_tables"

# Ιδιότητες χαρακτήρων που κρατάμε από το pandoc (έντονα, πλάγια κ.λπ.)· τα υπόλοιπα από τη «δότρια».
_KEEP_RPR = ("b", "bCs", "i", "iCs", "u", "strike", "vertAlign")
_RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
              "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
              "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
              "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"]
_PPR_ORDER = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
              "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
              "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
              "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
              "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
              "sectPr", "pPrChange"]


class CorpusError(RuntimeError):
    pass


def _w(local: str) -> str:
    return W + local


def _val(el, path: str) -> str | None:
    if el is None:
        return None
    x = el.find(path, NS)
    return x.get(_w("val")) if x is not None else None


def _sort_children(el, order: list[str]) -> None:
    idx = {n: i for i, n in enumerate(order)}
    kids = list(el)
    kids.sort(key=lambda k: idx.get(etree.QName(k).localname, len(order)) if isinstance(k.tag, str) else len(order))
    for k in kids:
        el.remove(k)
    for k in kids:
        el.append(k)


def _xml(el) -> bytes:
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8", standalone=True)


def natural_key(name: str) -> list:
    """Ταξινόμηση ονομάτων αρχείων όπως τα διαβάζει ο άνθρωπος: 1.6.2 πριν από 1.6.10."""
    stem = Path(name).stem
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.casefold()) for t in re.split(r"(\d+)", stem) if t != ""]


# --------------------------------------------------------------------------- μορφές αρίθμησης

_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
          (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def _roman(n: int) -> str:
    out = ""
    for v, s in _ROMAN:
        while n >= v:
            out += s
            n -= v
    return out


def _letters(n: int, alphabet: str = "abcdefghijklmnopqrstuvwxyz") -> str:
    # Word: a…z, aa…zz, aaa… (επανάληψη του ίδιου γράμματος)
    k = len(alphabet)
    return alphabet[(n - 1) % k] * ((n - 1) // k + 1)


def format_number(n: int, fmt: str | None) -> str:
    fmt = fmt or "decimal"
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt in ("none", "bullet"):
        return ""
    return str(n)


# --------------------------------------------------------------------------- μπλοκ περιεχομένου

@dataclass
class Block:
    kind: str  # num | cont | display | figure | choices | sep
    level: int = 0
    md: str = ""
    svg: str | None = None
    ex_index: int = -1
    cols: list | None = None   # κείμενο ανά στήλη (αν το ερώτημα έχει στήλες ⇥)
    tabs: list | None = None   # θέσεις στηλοθετών (twips από την αρχή του κειμένου)


_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.S)
_GREEK_LETTERS = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"


def _escape_md_line(line: str) -> str:
    """Να μη διαβαστεί μια γραμμή κειμένου ως λίστα/επικεφαλίδα/παράθεση από το pandoc."""
    s = line
    m = re.match(r"^(\s*)(\d+|[A-Za-zΑ-Ωα-ω]{1,4})([.)])(\s)", s)
    if m:
        s = m.group(1) + m.group(2) + "\\" + m.group(3) + s[m.end(3):]
    s = re.sub(r"^(\s*)([-+*>#|])", lambda mm: mm.group(1) + "\\" + mm.group(2), s)
    s = re.sub(r"^(\s*)\((\w{1,4})\)(\s)", r"\1\\(\2)\3", s)
    return s


def _escape_md(text: str) -> str:
    return "\n".join(_escape_md_line(ln) for ln in text.split("\n"))


def _split_parts(text: str) -> list[tuple[str, str]]:
    """Κείμενο → [("text"|"display", …)]· τα $$…$$ γίνονται δικές τους παράγραφοι."""
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _DISPLAY_RE.finditer(text or ""):
        before = text[pos:m.start()].strip()
        if before:
            parts.append(("text", before))
        parts.append(("display", m.group(1).strip()))
        pos = m.end()
    rest = (text or "")[pos:].strip()
    if rest:
        parts.append(("text", rest))
    # κενές γραμμές μέσα σε κείμενο = νέα παράγραφος
    out: list[tuple[str, str]] = []
    for kind, t in parts:
        if kind == "text":
            out += [("text", p.strip()) for p in re.split(r"\n\s*\n", t) if p.strip()]
        else:
            out.append((kind, t))
    return out


MATH_CHAR_MAP = str.maketrans({"\u2212": "-", "\u2032": "'"})
TAB_MARK = "§TAB§"
COLSEP = "⇥"
_MATH_INLINE = re.compile(r"\$([^$]+)\$")
_SPACE_CMDS = {"quad": 1.0, "qquad": 2.0, ",": 0.17, ";": 0.28, ":": 0.22, " ": 0.3, "!": 0.0}


def _tex_width(tex: str) -> float:
    """Χοντρική εκτίμηση πλάτους LaTeX σε em (για την τοποθέτηση στηλοθετών)."""
    i, n, w = 0, len(tex), 0.0

    def group(j):
        if j < n and tex[j] == "{":
            depth, k = 0, j
            while k < n:
                if tex[k] == "{":
                    depth += 1
                elif tex[k] == "}":
                    depth -= 1
                    if depth == 0:
                        return tex[j + 1:k], k + 1
                k += 1
            return tex[j + 1:], n
        if j < n and tex[j] == "\\":
            m = re.match(r"\\[A-Za-z]+|\\.", tex[j:])
            return m.group(0), j + len(m.group(0))
        return (tex[j] if j < n else ""), j + 1

    while i < n:
        c = tex[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", tex[i:])
            cmd = m.group(1)
            i += len(m.group(0))
            if cmd == "frac":
                a, i = group(i)
                b, i = group(i)
                w += max(_tex_width(a), _tex_width(b)) + 0.2
            elif cmd == "sqrt":
                if i < n and tex[i] == "[":
                    k = tex.find("]", i)
                    i = k + 1 if k > 0 else i + 1
                a, i = group(i)
                w += _tex_width(a) + 0.8
            elif cmd in ("mathrm", "text", "textrm", "mathbf", "mathit", "operatorname"):
                a, i = group(i)
                w += _tex_width(a)
            elif cmd in ("left", "right"):
                if i < n and tex[i] in "()[]|.":
                    w += 0.35 if tex[i] != "." else 0
                    i += 1
            elif cmd in _SPACE_CMDS:
                w += _SPACE_CMDS[cmd]
            else:
                w += 0.6
        elif c in "^_":
            a, i = group(i + 1)
            w += 0.7 * _tex_width(a)
        elif c in "{} ":
            i += 1
        else:
            w += 0.3 if c in ",.;:()|'" else 0.55
            i += 1
    return w


def text_width_em(text: str) -> float:
    """Πλάτος κειμένου με εξισώσεις, σε em (χοντρικά)."""
    total, pos = 0.0, 0
    for m in _MATH_INLINE.finditer(text or ""):
        total += 0.5 * len(text[pos:m.start()].strip("* ")) + _tex_width(m.group(1))
        pos = m.end()
    return total + 0.5 * len((text or "")[pos:].strip("* "))


def _tab_stops(col_lists: list[list[str]], em_twips: float = 240.0) -> list[int]:
    """Θέσεις στηλοθετών κοινές για όλα τα ερωτήματα μιας άσκησης (ίδιο επίπεδο)."""
    ncols = max(len(c) for c in col_lists)
    stops, x = [], 0.0
    for k in range(ncols - 1):
        widest = max((text_width_em(c[k]) for c in col_lists if len(c) > k + 1), default=0.0)
        x += max(widest * em_twips + 400, 1134)  # τουλάχιστον 2 cm ανά στήλη
        x = math.ceil(x / 142) * 142             # σε βήματα 0,25 cm
        stops.append(int(x))
    return stops


def _fix_refs(text: str, refmap: dict[str, str]) -> str:
    return re.sub(r"\[\[([^\]]+)\]\]", lambda m: refmap.get(m.group(1).strip(), f"({m.group(1).strip()})"), text or "")


def build_blocks(exercises: list[dict], level_formats: list[str]) -> list[Block]:
    """Ασκήσεις (JSON της μεταγραφής) → σειρά παραγράφων με επίπεδα αρίθμησης."""
    blocks: list[Block] = []

    def fmt(level: int, n: int) -> str:
        f = level_formats[level] if level < len(level_formats) else "decimal"
        return format_number(n, f)

    for ei, ex in enumerate(exercises):
        if ei > 0:
            blocks.append(Block("sep", 0, ex_index=ei))
        refmap: dict[str, str] = {}
        for k, it in enumerate(ex.get("items") or []):
            if it.get("label"):
                refmap[str(it["label"]).strip()] = f"({fmt(1, k + 1)})"
            for kk, sub in enumerate(it.get("items") or []):
                if sub.get("label"):
                    refmap.setdefault(str(sub["label"]).strip(), f"({fmt(2, kk + 1)})")

        def add_text(text: str, level: int, numbered: bool) -> None:
            parts = _split_parts(_fix_refs(text, refmap))
            first = True
            if numbered and (not parts or parts[0][0] == "display"):
                blocks.append(Block("num", level, "", ex_index=ei))  # αριθμός μόνος του, μετά η εξίσωση
                first = False
            for kind, t in parts:
                if kind == "display":
                    blocks.append(Block("display", level, f"$${t}$$", ex_index=ei))
                    first = False
                    continue
                cols = [c.strip() for c in t.split(COLSEP)] if COLSEP in t else None
                md = f" {TAB_MARK} ".join(_escape_md(c) for c in cols) if cols else _escape_md(t)
                blocks.append(Block("num" if (numbered and first) else "cont", level, md, ex_index=ei, cols=cols))
                first = False

        add_text(ex.get("stem", ""), 0, True)
        fig = ex.get("figure_render")
        if fig and fig.get("png") and Path(fig["png"]).exists():
            w = float(fig.get("width_cm", 7))
            blocks.append(Block("figure", 0, f"![]({Path(fig['png']).as_posix()}){{width={w:.1f}cm}}",
                                svg=fig.get("svg"), ex_index=ei))

        def add_items(items: list[dict], level: int) -> None:
            for it in items:
                add_text(it.get("text", ""), level, True)
                ch = it.get("choices")
                if ch and ch.get("options"):
                    cols = 4 if int(ch.get("columns", 2)) >= 4 else 2
                    opts = [str(o).strip() for o in ch["options"]]
                    for r0 in range(0, len(opts), cols):
                        row = "  ".join(
                            f"**{_GREEK_LETTERS[i] if i < len(_GREEK_LETTERS) else i + 1}.** {opts[i]}"
                            for i in range(r0, min(r0 + cols, len(opts)))
                        )
                        blocks.append(Block("choices", level + 1, _escape_md(row), ex_index=ei))
                if it.get("items"):
                    add_items(it["items"], min(level + 1, 8))

        add_items(ex.get("items") or [], 1)
        if (ex.get("closing") or "").strip():
            add_text(ex["closing"], 0, False)

    # κοινοί στηλοθέτες ανά άσκηση και επίπεδο, ώστε οι στήλες να στοιχίζονται όπως στο βιβλίο
    groups: dict[tuple[int, int], list[Block]] = {}
    for b in blocks:
        if b.cols:
            groups.setdefault((b.ex_index, b.level), []).append(b)
    for bs in groups.values():
        stops = _tab_stops([b.cols for b in bs])
        for b in bs:
            b.tabs = stops
    return blocks


# --------------------------------------------------------------------------- pandoc → παράγραφοι

def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def render_blocks(blocks: list[Block], workdir: Path) -> tuple[list[list], dict[str, bytes], dict[str, str]]:
    """Ένα πέρασμα pandoc για όλα τα μπλοκ. Επιστρέφει (παράγραφοι ανά μπλοκ, αρχεία docx, σχέσεις)."""
    md = []
    for k, b in enumerate(blocks):
        body = b.md if b.md.strip() else "​"
        md.append(f'::: {{custom-style="CB{k}"}}\n{body}\n:::\n')
    src = workdir / "corpus.md"
    out = workdir / "corpus.docx"
    src.write_text("\n".join(md), encoding="utf-8")
    res = _run([pandoc_path(), str(src), "-f", MD_FORMAT, "-t", "docx", "-o", str(out)], workdir)
    if res.returncode != 0:
        raise CorpusError("Σφάλμα pandoc:\n" + (res.stderr or res.stdout)[-2000:])
    with zipfile.ZipFile(out) as z:
        files = {n: z.read(n) for n in z.namelist()}
    doc = etree.fromstring(files["word/document.xml"])
    styles = etree.fromstring(files["word/styles.xml"])
    id_by_name = {}
    for s in styles.findall("w:style", NS):
        nm = _val(s, "w:name")
        if nm:
            id_by_name[nm] = s.get(_w("styleId"))
    want = {id_by_name.get(f"CB{k}", f"CB{k}"): k for k in range(len(blocks))}
    paras: list[list] = [[] for _ in blocks]
    for p in doc.iter(_w("p")):
        sid = _val(p, "w:pPr/w:pStyle")
        if sid in want:
            paras[want[sid]].append(p)
    rels = {}
    if "word/_rels/document.xml.rels" in files:
        for r in etree.fromstring(files["word/_rels/document.xml.rels"]):
            rels[r.get("Id")] = r.get("Target")
    missing = [k for k, ps in enumerate(paras) if not ps]
    if missing:
        raise CorpusError(f"Αποτυχία μετατροπής {len(missing)} παραγράφων (pandoc).")
    return paras, files, rels


def _tabs_from_marks(p) -> None:
    """Το σημάδι στηλών στο κείμενο γίνεται πραγματικός στηλοθέτης (w:tab), χωρίς τα κενά γύρω του."""
    for r in list(p.iter(_w("r"))):
        for t in [t for t in r.findall("w:t", NS) if t.text and TAB_MARK in t.text]:
            parts = t.text.split(TAB_MARK)
            pos = list(r).index(t)
            r.remove(t)
            new = []
            for k, part in enumerate(parts):
                if k:
                    new.append(etree.Element(_w("tab")))
                if part:
                    nt = etree.Element(_w("t"))
                    nt.text = part
                    nt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    new.append(nt)
            for k, el in enumerate(new):
                r.insert(pos + k, el)
    # κόψιμο κενών δίπλα στον στηλοθέτη (σταματά σε εξίσωση)
    seq = []
    for ch in p:
        if not isinstance(ch.tag, str):
            continue
        q = etree.QName(ch)
        if q.namespace == NS["w"] and q.localname == "r":
            seq += [x for x in ch if isinstance(x.tag, str) and etree.QName(x).localname in ("t", "tab")]
        elif q.namespace == NS["m"]:
            seq.append(None)
    for i, x in enumerate(seq):
        if x is None or etree.QName(x).localname != "tab":
            continue
        for rng, strip in ((range(i - 1, -1, -1), str.rstrip), (range(i + 1, len(seq)), str.lstrip)):
            for j in rng:
                y = seq[j]
                if y is None or etree.QName(y).localname != "t":
                    break
                y.text = strip(y.text or "")
                if y.text:
                    break


# --------------------------------------------------------------------------- το έγγραφο

class CorpusDoc:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            with zipfile.ZipFile(self.path) as z:
                self.order = [i.filename for i in z.infolist()]
                self.files = {n: z.read(n) for n in self.order}
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise CorpusError("Το αρχείο δεν είναι έγγραφο Word (.docx) ή είναι κατεστραμμένο.") from exc
        if "word/document.xml" not in self.files:
            raise CorpusError("Το αρχείο δεν είναι έγγραφο Word (.docx).")
        self.doc = etree.fromstring(self.files["word/document.xml"])
        self.body = self.doc.find("w:body", NS)
        self.styles = etree.fromstring(self.files["word/styles.xml"]) if "word/styles.xml" in self.files else None
        self.numbering = etree.fromstring(self.files["word/numbering.xml"]) if "word/numbering.xml" in self.files else None
        self._style_cache: dict[str, tuple[int | None, tuple | None]] = {}
        self._index_numbering()
        self.changed: set[str] = set()

    # ----------------------------------------------------------------- αρίθμηση
    def _index_numbering(self) -> None:
        self.abstract: dict[str, etree._Element] = {}
        self.num_abs: dict[str, str] = {}
        if self.numbering is None:
            return
        for a in self.numbering.findall("w:abstractNum", NS):
            self.abstract[a.get(_w("abstractNumId"))] = a
        for n in self.numbering.findall("w:num", NS):
            self.num_abs[n.get(_w("numId"))] = _val(n, "w:abstractNumId")

    def _lvl(self, num_id: str, ilvl: int):
        a = self.abstract.get(self.num_abs.get(num_id, ""))
        if a is None:
            return None
        link = _val(a, "w:numStyleLink")
        if link is not None and self.styles is not None:  # λίστα ορισμένη μέσω στυλ
            st = self.styles.find(f"w:style[@w:styleId='{link}']", NS)
            nid = _val(st, "w:pPr/w:numPr/w:numId") if st is not None else None
            if nid and nid != num_id:
                return self._lvl(nid, ilvl)
        return a.find(f"w:lvl[@w:ilvl='{ilvl}']", NS)

    def level_formats(self, num_id: str) -> list[str]:
        return [(_val(self._lvl(num_id, i), "w:numFmt") or "decimal") for i in range(9)]

    def level_left(self, num_id: str | None, ilvl: int) -> int | None:
        if not num_id:
            return None
        lvl = self._lvl(num_id, ilvl)
        ind = lvl.find("w:pPr/w:ind", NS) if lvl is not None else None
        if ind is None:
            return None
        v = ind.get(_w("left")) or ind.get(_w("start"))
        return int(v) if v and v.lstrip("-").isdigit() else None

    # ----------------------------------------------------------------- παράγραφοι
    def _style_props(self, sid: str | None) -> tuple[int | None, tuple | None]:
        """(επίπεδο διάρθρωσης, (numId, ilvl)) από το στυλ, ακολουθώντας το basedOn."""
        if not sid or self.styles is None:
            return None, None
        if sid in self._style_cache:
            return self._style_cache[sid]
        outline, num = None, None
        seen = set()
        cur = sid
        while cur and cur not in seen:
            seen.add(cur)
            st = self.styles.find(f"w:style[@w:styleId='{cur}']", NS)
            if st is None:
                break
            if outline is None:
                o = _val(st, "w:pPr/w:outlineLvl")
                if o is not None and o.isdigit() and int(o) < 9:
                    outline = int(o)
            if num is None and st.find("w:pPr/w:numPr", NS) is not None:
                nid = _val(st, "w:pPr/w:numPr/w:numId")
                il = _val(st, "w:pPr/w:numPr/w:ilvl")
                if nid:
                    num = (nid, int(il or 0))
            cur = _val(st, "w:basedOn")
        self._style_cache[sid] = (outline, num)
        return outline, num

    def numpr(self, p) -> tuple[str, int] | None:
        np_ = p.find("w:pPr/w:numPr", NS)
        if np_ is not None:
            nid = _val(np_, "w:numId")
            if nid is None or nid == "0":
                return None
            return nid, int(_val(np_, "w:ilvl") or 0)
        _o, num = self._style_props(_val(p, "w:pPr/w:pStyle"))
        return num

    def outline_level(self, p) -> int | None:
        o = _val(p, "w:pPr/w:outlineLvl")
        if o is not None and o.isdigit():
            return int(o) if int(o) < 9 else None
        return self._style_props(_val(p, "w:pPr/w:pStyle"))[0]

    @staticmethod
    def text(p) -> str:
        return "".join(t.text or "" for t in p.iter(_w("t")))

    @staticmethod
    def has_content(el) -> bool:
        tag = etree.QName(el).localname
        if tag == "tbl" or tag == "sdt":
            return True
        if tag != "p":
            return False
        if any((t.text or "").strip() for t in el.iter(_w("t"))):
            return True
        for local in ("object", "drawing", "pict"):
            if el.find(f".//w:{local}", NS) is not None:
                return True
        return el.find(".//m:oMath", NS) is not None or el.find(".//m:oMathPara", NS) is not None

    def _ex_start(self, j: int, ex_num: str | None) -> bool:
        """Αρχή άσκησης: αριθμημένη παράγραφος επιπέδου 0 της αρίθμησης ασκήσεων, με περιεχόμενο — ή κενή αλλά
        ακολουθούμενη αμέσως από τα ερωτήματά της (άσκηση χωρίς εκφώνηση: «6.3» και από κάτω a., b.)."""
        el = self.body[j]
        if not ex_num or etree.QName(el).localname != "p":
            return False
        np_ = self.numpr(el)
        if not np_ or np_[0] != ex_num or np_[1] != 0:
            return False
        if self.has_content(el):
            return True
        nxt = self.body[j + 1] if j + 1 < len(self.body) else None
        if nxt is None or etree.QName(nxt).localname != "p":
            return False
        n2 = self.numpr(nxt)
        return bool(n2 and n2[0] == ex_num and n2[1] > 0)

    # ----------------------------------------------------------------- διάρθρωση
    def _walk_labels(self):
        """Για κάθε παράγραφο του σώματος: (θέση, στοιχείο, αριθμός λίστας όπως τον δείχνει το Word)."""
        counters: dict[str, list[int]] = {}
        started: set[str] = set()
        for idx, el in enumerate(self.body):
            label = ""
            if etree.QName(el).localname == "p":
                np_ = self.numpr(el)
                if np_:
                    nid, ilvl = np_
                    absid = self.num_abs.get(nid)
                    lvl = self._lvl(nid, ilvl)
                    if absid is not None and lvl is not None:
                        c = counters.setdefault(absid, [0] * 9)
                        if nid not in started:
                            started.add(nid)
                            num = self.numbering.find(f"w:num[@w:numId='{nid}']", NS)
                            for ov in num.findall("w:lvlOverride", NS) if num is not None else []:
                                so = _val(ov, "w:startOverride")
                                if so is not None:
                                    c[int(ov.get(_w("ilvl")) or 0)] = int(so) - 1
                        if c[ilvl] == 0:
                            c[ilvl] = int(_val(lvl, "w:start") or 1) - 1
                        c[ilvl] += 1
                        for d in range(ilvl + 1, 9):
                            c[d] = 0
                        txt = _val(lvl, "w:lvlText") or ""

                        def sub(m, c=c, nid=nid):
                            d = int(m.group(1)) - 1
                            f = _val(self._lvl(nid, d), "w:numFmt")
                            return format_number(max(1, c[d]), f)

                        label = re.sub(r"%([1-9])", sub, txt)
            yield idx, el, label

    def headings(self) -> list[dict]:
        out = []
        for idx, el, label in self._walk_labels():
            if etree.QName(el).localname != "p":
                continue
            lvl = self.outline_level(el)
            title = self.text(el).strip()
            if lvl is None or not title:
                continue
            np_ = self.numpr(el)
            out.append({"index": idx, "title": title, "label": label, "numbered": np_ is not None,
                        "num_id": np_[0] if np_ else None, "outline": lvl})
        return out

    def _end_index(self) -> int:
        last = self.body[-1] if len(self.body) else None
        if last is not None and etree.QName(last).localname == "sectPr":
            return len(self.body) - 1
        return len(self.body)

    def outline(self) -> list[dict]:
        hs = self.headings()
        mixed = any(h["numbered"] for h in hs) and any(not h["numbered"] for h in hs)
        levels = sorted({h["outline"] for h in hs})
        labels = {idx: lab for idx, _el, lab in self._walk_labels()}
        seen: dict[str, int] = {}
        for i, h in enumerate(hs):
            h["depth"] = (1 if h["numbered"] else 0) if mixed else levels.index(h["outline"])
            end = hs[i + 1]["index"] if i + 1 < len(hs) else self._end_index()
            h["end"] = end
            ex_num = self._exercise_num(h["index"] + 1, end, h["num_id"])
            count, last_label = 0, ""
            if ex_num:
                for j in range(h["index"] + 1, end):
                    if self._ex_start(j, ex_num):
                        count += 1
                        last_label = labels.get(j, "")
            h["exercises"] = count
            h["last_label"] = last_label
            # πώς θα αριθμηθούν οι νέες ασκήσεις (για την προεπισκόπηση στην οθόνη)
            prefix = re.sub(r"[^\d]", "", h["label"] or "")
            if ex_num:
                lvl0 = self._lvl(ex_num, 0)
                h["pattern"] = _val(lvl0, "w:lvlText") or "%1."
                h["formats"] = self.level_formats(ex_num)[:4]
                h["start"] = int(_val(lvl0, "w:start") or 1)
            else:
                h["pattern"] = f"{prefix}.%1" if prefix else "%1."
                h["formats"] = ["decimal", "lowerLetter", "lowerRoman", "decimal"]
                h["start"] = 1
            n = seen.get(h["title"], 0)
            seen[h["title"]] = n + 1
            h["key"] = {"title": h["title"], "nth": n}
            h["display"] = (h["label"] + " " if h["label"] else "") + h["title"]
        keys = ("key", "display", "title", "label", "depth", "exercises", "last_label", "pattern", "formats", "start")
        return [{k: h[k] for k in keys} for h in hs]

    def _find(self, key: dict) -> tuple[dict, int]:
        hs = self.headings()
        n = 0
        for i, h in enumerate(hs):
            if h["title"] == key.get("title"):
                if n == int(key.get("nth", 0)):
                    end = hs[i + 1]["index"] if i + 1 < len(hs) else self._end_index()
                    return h, end
                n += 1
        raise CorpusError(f"Δεν βρέθηκε η ενότητα «{key.get('title')}» στο έγγραφο. "
                          "Ίσως άλλαξε ο τίτλος — ανανεώστε τη λίστα ενοτήτων.")

    def _exercise_num(self, start: int, end: int, heading_num: str | None) -> str | None:
        for j in range(start, end):
            el = self.body[j]
            if etree.QName(el).localname != "p":
                continue
            np_ = self.numpr(el)
            if np_ and np_[1] == 0 and np_[0] != heading_num:
                return np_[0]
        return None

    # ----------------------------------------------------------------- «δότρια» παράγραφος
    def _donor(self, h: dict, start: int, end: int, ex_num: str | None):
        """Παράγραφος άσκησης από την οποία αντιγράφεται η μορφοποίηση."""
        if ex_num:
            for j in range(start, end):
                el = self.body[j]
                if etree.QName(el).localname == "p" and (self.numpr(el) or (None,))[0] == ex_num:
                    return el, ex_num
        # προηγούμενες ενότητες: η πιο κοντινή αριθμημένη άσκηση (επίπεδο 0) που δεν είναι τίτλος
        heading_nums = {x["num_id"] for x in self.headings() if x["num_id"]}
        for j in range(h["index"] - 1, -1, -1):
            el = self.body[j]
            if etree.QName(el).localname != "p" or not self.has_content(el):
                continue
            np_ = self.numpr(el)
            if np_ and np_[1] == 0 and np_[0] not in heading_nums and self.outline_level(el) is None:
                return el, np_[0]
        # καμία αριθμημένη άσκηση στο έγγραφο: μια απλή παράγραφος κειμένου (όχι τίτλος) για τη μορφοποίηση
        for j in list(range(start, end)) + list(range(len(self.body))):
            el = self.body[j]
            if (etree.QName(el).localname == "p" and self.has_content(el) and self.outline_level(el) is None
                    and self.numpr(el) is None):
                return el, None
        return None, None

    # ----------------------------------------------------------------- ομοιόμορφη αρίθμηση ερωτημάτων
    def _reference_num(self, h: dict, ex_num: str) -> str | None:
        """Η αρίθμηση ασκήσεων της πιο κοντινής προηγούμενης ενότητας που έχει ήδη ασκήσεις."""
        heading_nums = {x["num_id"] for x in self.headings() if x["num_id"]}
        for j in range(h["index"] - 1, -1, -1):
            el = self.body[j]
            if etree.QName(el).localname != "p" or not self.has_content(el):
                continue
            np_ = self.numpr(el)
            if (np_ and np_[1] == 0 and np_[0] != ex_num and np_[0] not in heading_nums
                    and self.outline_level(el) is None):
                return np_[0]
        return None

    def _lvl_bold(self, num_id: str, ilvl: int) -> bool:
        lvl = self._lvl(num_id, ilvl)
        return lvl is not None and lvl.find("w:rPr/w:b", NS) is not None

    def _harmonize_levels(self, ex_num: str, ref_num: str, levels: list[int]) -> list[int]:
        """Έντονα/κανονικά στους αριθμούς ερωτημάτων (a., b., i.) όπως στην προηγούμενη ενότητα.
        Αλλάζει μόνο αρίθμηση που χρησιμοποιεί αποκλειστικά η ενότητα-στόχος."""
        target_abs = self.num_abs.get(ex_num)
        if not target_abs or target_abs == self.num_abs.get(ref_num):
            return []
        if sum(1 for a in self.num_abs.values() if a == target_abs) > 1:
            return []
        changed = []
        for il in levels:
            lt, lr = self._lvl(ex_num, il), self._lvl(ref_num, il)
            if lt is None or lr is None:
                continue
            if lt.get(_w("tentative")) is not None:  # το Word το αφαιρεί όταν το επίπεδο χρησιμοποιείται
                del lt.attrib[_w("tentative")]
                self.changed.add("word/numbering.xml")
            want = self._lvl_bold(ref_num, il)
            if self._lvl_bold(ex_num, il) == want:
                continue
            rpr = lt.find("w:rPr", NS)
            if rpr is None:
                rpr = etree.SubElement(lt, _w("rPr"))
            for local in ("b", "bCs"):
                for x in rpr.findall(f"w:{local}", NS):
                    rpr.remove(x)
            if want:
                rpr.append(etree.Element(_w("b")))
                rpr.append(etree.Element(_w("bCs")))
            _sort_children(rpr, _RPR_ORDER)
            changed.append(il)
            self.changed.add("word/numbering.xml")
        return changed

    @staticmethod
    def _donor_rpr(p):
        for r in p.iter(_w("r")):
            rp = r.find("w:rPr", NS)
            if rp is not None and r.getparent() is not None and etree.QName(r.getparent()).namespace == NS["w"]:
                return copy.deepcopy(rp)
        rp = p.find("w:pPr/w:rPr", NS)
        return copy.deepcopy(rp) if rp is not None else etree.Element(_w("rPr"))

    # ----------------------------------------------------------------- νέα αρίθμηση
    def _new_numbering(self, source_num: str | None, section_label: str) -> str:
        if self.numbering is None:
            self.numbering = etree.Element(_w("numbering"), nsmap={"w": NS["w"]})
            self.files["word/numbering.xml"] = b""
            self._ensure_part("word/numbering.xml",
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
                              "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering",
                              "numbering.xml")
        abs_ids = [int(a) for a in self.abstract if a and a.isdigit()]
        num_ids = [int(n) for n in self.num_abs if n and n.isdigit()]
        new_abs = str(max(abs_ids, default=-1) + 1)
        new_num = str(max(num_ids, default=0) + 1)
        src = self.abstract.get(self.num_abs.get(source_num or "", ""))
        prefix = re.sub(r"[^\d]", "", section_label or "")
        if src is not None and _val(src, "w:numStyleLink") is None:
            a = copy.deepcopy(src)
            lvl0 = a.find("w:lvl[@w:ilvl='0']", NS)
            lt = lvl0.find("w:lvlText", NS) if lvl0 is not None else None
            if lt is not None and prefix and re.fullmatch(r"\d+\.%1", lt.get(_w("val")) or ""):
                lt.set(_w("val"), f"{prefix}.%1")
            st = lvl0.find("w:start", NS) if lvl0 is not None else None
            if st is not None:
                st.set(_w("val"), "1")
        else:
            lvl_txt = f"{prefix}.%1" if prefix else "%1."
            fmts = [("decimal", lvl_txt), ("lowerLetter", "%2."), ("lowerRoman", "%3."), ("decimal", "%4.")]
            a = etree.Element(_w("abstractNum"))
            etree.SubElement(a, _w("multiLevelType")).set(_w("val"), "hybridMultilevel")
            for i, (f, t) in enumerate(fmts):
                lvl = etree.SubElement(a, _w("lvl"))
                lvl.set(_w("ilvl"), str(i))
                etree.SubElement(lvl, _w("start")).set(_w("val"), "1")
                etree.SubElement(lvl, _w("numFmt")).set(_w("val"), f)
                etree.SubElement(lvl, _w("lvlText")).set(_w("val"), t)
                etree.SubElement(lvl, _w("lvlJc")).set(_w("val"), "left")
                ppr = etree.SubElement(lvl, _w("pPr"))
                ind = etree.SubElement(ppr, _w("ind"))
                ind.set(_w("left"), str(720 * (i + 1)))
                ind.set(_w("hanging"), "360")
                if i == 0:
                    etree.SubElement(etree.SubElement(lvl, _w("rPr")), _w("b"))
        a.set(_w("abstractNumId"), new_abs)
        nsid = a.find("w:nsid", NS)
        if nsid is not None:
            nsid.set(_w("val"), "%08X" % random.getrandbits(32))
        tmpl = a.find("w:tmpl", NS)
        if tmpl is not None:
            a.remove(tmpl)
        # σειρά στο numbering.xml: όλα τα abstractNum πριν από τα num
        first_num = self.numbering.find("w:num", NS)
        if first_num is not None:
            first_num.addprevious(a)
        else:
            self.numbering.append(a)
        n = etree.SubElement(self.numbering, _w("num"))
        n.set(_w("numId"), new_num)
        etree.SubElement(n, _w("abstractNumId")).set(_w("val"), new_abs)
        # το numIdMacAtCleanup (αν υπάρχει) πρέπει να μείνει τελευταίο
        mac = self.numbering.find("w:numIdMacAtCleanup", NS)
        if mac is not None:
            self.numbering.remove(mac)
            self.numbering.append(mac)
        self.abstract[new_abs] = a
        self.num_abs[new_num] = new_abs
        self.changed.add("word/numbering.xml")
        return new_num

    # ----------------------------------------------------------------- πακέτο
    def _ensure_part(self, part: str, ctype: str, rel_type: str, target: str) -> None:
        ct = etree.fromstring(self.files["[Content_Types].xml"])
        if not any(o.get("PartName") == "/" + part for o in ct.findall("ct:Override", NS)):
            o = etree.SubElement(ct, "{%s}Override" % NS["ct"])
            o.set("PartName", "/" + part)
            o.set("ContentType", ctype)
            self.files["[Content_Types].xml"] = _xml(ct)
        rels = self._rels()
        if not any(r.get("Type") == rel_type for r in rels):
            r = etree.SubElement(rels, "{%s}Relationship" % NS["rel"])
            r.set("Id", "rIdCorpusNum")
            r.set("Type", rel_type)
            r.set("Target", target)
            self._rels_el = rels

    def _rels(self):
        if getattr(self, "_rels_el", None) is None:
            self._rels_el = etree.fromstring(self.files["word/_rels/document.xml.rels"])
        return self._rels_el

    def _ensure_default(self, ext: str, ctype: str) -> None:
        ct = etree.fromstring(self.files["[Content_Types].xml"])
        if not any(d.get("Extension", "").lower() == ext for d in ct.findall("ct:Default", NS)):
            d = etree.SubElement(ct, "{%s}Default" % NS["ct"])
            d.set("Extension", ext)
            d.set("ContentType", ctype)
            self.files["[Content_Types].xml"] = _xml(ct)

    def _add_media(self, data: bytes, ext: str) -> str:
        name = f"media/corpus_{uuid.uuid4().hex[:10]}.{ext}"
        self.files["word/" + name] = data
        rels = self._rels()
        existing = {r.get("Id") for r in rels}
        rid = "rIdC" + uuid.uuid4().hex[:8]
        while rid in existing:
            rid = "rIdC" + uuid.uuid4().hex[:8]
        r = etree.SubElement(rels, "{%s}Relationship" % NS["rel"])
        r.set("Id", rid)
        r.set("Type", IMG_REL)
        r.set("Target", name)
        self._ensure_default(ext, {"png": "image/png", "svg": "image/svg+xml", "jpeg": "image/jpeg",
                                   "jpg": "image/jpeg", "gif": "image/gif"}.get(ext, "application/octet-stream"))
        return rid

    # ----------------------------------------------------------------- εισαγωγή
    def insert(self, key: dict, exercises: list[dict], workdir: Path, math_font: str = "Cambria Math",
               at: dict | None = None) -> dict:
        """Εισαγωγή ασκήσεων στο τέλος της ενότητας `key` ή, με `at={"side": "after"|"before", "el": <παράγραφος
        αρχής υπάρχουσας άσκησης>}`, ακριβώς πριν/μετά από εκείνη την άσκηση (στη δική της ενότητα και αρίθμηση)."""
        if not exercises:
            raise CorpusError("Δεν υπάρχουν ασκήσεις για εισαγωγή.")
        if at is not None:
            blk = self._block_of(at["el"])
            h = next(x for x in self.headings() if x["index"] == blk["head"])
            end = blk["sec_end"]
        else:
            h, end = self._find(key)
        start = h["index"] + 1
        ex_num = self._exercise_num(start, end, h["num_id"])
        if at is not None:
            donor, donor_num = at["el"], ex_num  # η μορφή της γειτονικής άσκησης
        else:
            donor, donor_num = self._donor(h, start, end, ex_num)
        warnings: list[str] = []
        if not ex_num:
            ex_num = self._new_numbering(donor_num, h["label"])
        src_ppr = donor.find("w:pPr", NS) if donor is not None else None
        donor_ppr = copy.deepcopy(src_ppr) if src_ppr is not None else etree.Element(_w("pPr"))
        # εσοχή της «δότριας» άσκησης στο επίπεδο 0 (π.χ. left=709), ώστε η νέα να στοιχίζεται με τις γειτονικές
        dnp = self.numpr(donor) if donor is not None else None
        dind = src_ppr.find("w:ind", NS) if src_ppr is not None else None
        self._donor_ind0 = copy.deepcopy(dind) if (dnp and dnp[1] == 0 and dind is not None) else None
        for local in ("outlineLvl", "pageBreakBefore", "sectPr", "pPrChange", "keepNext", "framePr"):
            for x in donor_ppr.findall(f"w:{local}", NS):
                donor_ppr.remove(x)
        ps = donor_ppr.find("w:pStyle", NS)
        if ps is not None and self._style_props(ps.get(_w("val")))[0] is not None:
            donor_ppr.remove(ps)  # ποτέ στυλ τίτλου στις ασκήσεις
        rpr_text = self._donor_rpr(donor) if donor is not None else etree.Element(_w("rPr"))
        for local in _KEEP_RPR + ("rStyle", "highlight", "rPrChange", "ins", "del"):
            for x in rpr_text.findall(f"w:{local}", NS):
                rpr_text.remove(x)
        rpr_math = copy.deepcopy(rpr_text)
        fonts = rpr_math.find("w:rFonts", NS)
        text_font = fonts.get(_w("ascii")) if fonts is not None else None
        if fonts is None:
            fonts = etree.SubElement(rpr_math, _w("rFonts"))
        mf = text_font if (text_font and "math" in text_font.lower()) else math_font
        for k in list(fonts.attrib):
            del fonts.attrib[k]
        for attr in ("ascii", "eastAsia", "hAnsi", "cs"):
            fonts.set(_w(attr), mf)
        _sort_children(rpr_math, _RPR_ORDER)

        # 1. κενές θέσεις που είχε ετοιμάσει (αριθμημένες κενές παράγραφοι) → αφαιρούνται
        removed = 0
        for j in range(end - 1, start - 1, -1):
            el = self.body[j]
            if etree.QName(el).localname == "p" and not self.has_content(el):
                np_ = self.numpr(el)
                if np_ and np_[0] == ex_num and not self._ex_start(j, ex_num):
                    self.body.remove(el)
                    removed += 1
        end -= removed

        # 2. σημείο εισαγωγής
        if at is not None:
            # πριν/μετά από συγκεκριμένη άσκηση: η νέα παίρνει τη θέση της στη σειρά της αρίθμησης
            if at["side"] == "before":
                anchor = list(self.body).index(at["el"]) - 1
            else:
                anchor = self._block_take_end(self._block_of(at["el"])) - 1
            lead_sep = not self._plain_empty(self.body[anchor]) and self.outline_level(self.body[anchor]) is None
        else:
            # μετά το τελευταίο περιεχόμενο της ενότητας
            last = None
            for j in range(start, end):
                if self.has_content(self.body[j]):
                    last = j
            if last is not None:
                anchor, lead_sep = last, True
            else:
                first = self.body[start] if start < end else None
                empty_first = (first is not None and etree.QName(first).localname == "p"
                               and not self.has_content(first) and self.numpr(first) is None)
                anchor, lead_sep = (start if empty_first else h["index"]), False
        after = self.body[anchor + 1] if anchor + 1 < len(self.body) else None
        trail_sep = not (after is not None and etree.QName(after).localname == "p"
                         and not self.has_content(after) and self.numpr(after) is None
                         and self.outline_level(after) is None)

        # 3. περιεχόμενο μέσω pandoc
        formats = self.level_formats(ex_num)
        blocks = build_blocks(exercises, formats)
        used_levels = sorted({b.level for b in blocks if b.kind == "num" and b.level > 0})
        ref = self._reference_num(h, ex_num)
        if ref and used_levels:
            changed = self._harmonize_levels(ex_num, ref, used_levels)
            if changed:
                warnings.append("Οι αριθμοί των ερωτημάτων (" + ", ".join(f"{format_number(1, formats[l])}." for l in changed)
                                + ") έγιναν " + ("έντονοι" if self._lvl_bold(ref, changed[0]) else "κανονικοί")
                                + ", όπως στις προηγούμενες ενότητες του αρχείου.")
        if lead_sep:
            blocks.insert(0, Block("sep", 0))
        if trail_sep:
            blocks.append(Block("sep", 0))
        paras, pfiles, prels = render_blocks(blocks, workdir)

        max_doc_pr = max([int(x.get("id")) for x in self.doc.iter("{%s}docPr" % NS["wp"])
                          if (x.get("id") or "").isdigit()] + [0])
        new_elems = []
        for b, plist in zip(blocks, paras):
            for pi, src in enumerate(plist):
                kind = b.kind if pi == 0 else ("cont" if b.kind == "num" else b.kind)
                new_elems.append(self._make_para(kind, b, src, donor_ppr, rpr_text, rpr_math, ex_num, pfiles, prels))
        for el in new_elems:
            for dp in el.iter("{%s}docPr" % NS["wp"]):
                max_doc_pr += 1
                dp.set("id", str(max_doc_pr))
        ref = self.body[anchor]
        for el in new_elems:
            ref.addnext(el)
            ref = el
        self.changed.add("word/document.xml")

        # αριθμοί που πήραν οι νέες ασκήσεις
        labels = {idx: lab for idx, _el, lab in self._walk_labels()}
        new_ids = {id(e) for e in new_elems}
        got = [labels.get(i, "") for i, el in enumerate(self.body)
               if id(el) in new_ids and (self.numpr(el) or (None, 9))[1] == 0 and (self.numpr(el) or ("",))[0] == ex_num]
        return {"inserted": len(exercises), "section": h["title"], "section_label": h["label"],
                "first_label": got[0] if got else "", "last_label": got[-1] if got else "",
                "removed_placeholders": removed, "warnings": warnings}

    def _make_para(self, kind, block, src, donor_ppr, rpr_text, rpr_math, ex_num, pfiles, prels):
        p = etree.Element(_w("p"))
        ppr = copy.deepcopy(donor_ppr)
        for x in ppr.findall("w:numPr", NS):
            ppr.remove(x)
        for x in ppr.findall("w:ind", NS):
            ppr.remove(x)
        level = max(0, block.level)
        dind0 = getattr(self, "_donor_ind0", None)
        if kind == "num":
            np_ = etree.SubElement(ppr, _w("numPr"))
            etree.SubElement(np_, _w("ilvl")).set(_w("val"), str(level))
            etree.SubElement(np_, _w("numId")).set(_w("val"), ex_num)
            if level == 0 and dind0 is not None:
                ppr.append(copy.deepcopy(dind0))
        elif kind != "sep":
            left = self.level_left(ex_num, level)
            if level == 0 and dind0 is not None and (dind0.get(_w("left")) or dind0.get(_w("start"))):
                left = int(dind0.get(_w("left")) or dind0.get(_w("start")))
            if left is not None:
                ind = etree.SubElement(ppr, _w("ind"))
                ind.set(_w("left"), str(left))
                ind.set(_w("firstLine"), "0")
        if kind in ("display", "figure"):
            for x in ppr.findall("w:jc", NS):
                ppr.remove(x)
            etree.SubElement(ppr, _w("jc")).set(_w("val"), "center")
        if kind == "choices":
            for x in ppr.findall("w:jc", NS):
                ppr.remove(x)
        if block.tabs:
            base = self.level_left(ex_num, level) or 0
            for x in ppr.findall("w:tabs", NS):
                ppr.remove(x)
            tabs = etree.SubElement(ppr, _w("tabs"))
            for pos in block.tabs:
                t = etree.SubElement(tabs, _w("tab"))
                t.set(_w("val"), "left")
                t.set(_w("pos"), str(base + pos))
            for x in ppr.findall("w:jc", NS):  # με στήλες, η πλήρης στοίχιση θα τέντωνε τα κενά
                ppr.remove(x)
        _sort_children(ppr, _PPR_ORDER)
        p.append(ppr)
        if kind == "sep":
            return p
        for child in list(src):
            if isinstance(child.tag, str) and etree.QName(child).localname == "pPr":
                continue
            p.append(copy.deepcopy(child))
        # χαρακτήρες: η μορφοποίηση της καθηγήτριας + έντονα/πλάγια του κειμένου
        for r in p.iter(_w("r")):
            old = r.find("w:rPr", NS)
            new = copy.deepcopy(rpr_text)
            if old is not None:
                for local in _KEEP_RPR:
                    for x in old.findall(f"w:{local}", NS):
                        new.append(copy.deepcopy(x))
                r.remove(old)
            _sort_children(new, _RPR_ORDER)
            r.insert(0, new)
        for mr in p.iter(M + "r"):
            for x in mr.findall("w:rPr", NS):
                mr.remove(x)
            pos = 1 if (len(mr) and etree.QName(mr[0]).localname == "rPr" and etree.QName(mr[0]).namespace == NS["m"]) else 0
            mr.insert(pos, copy.deepcopy(rpr_math))
        for mt in p.iter(M + "t"):  # − και ′ όπως τα γράφει το Word
            if mt.text:
                mt.text = mt.text.translate(MATH_CHAR_MAP)
        for cp in p.iter(M + "ctrlPr"):
            for x in cp.findall("w:rPr", NS):
                cp.remove(x)
            cp.insert(0, copy.deepcopy(rpr_math))
        if block.cols:
            _tabs_from_marks(p)
        # αφαίρεση του​ που μπαίνει σε κενά μπλοκ
        for t in p.iter(_w("t")):
            if t.text and "​" in t.text:
                t.text = t.text.replace("​", "")
        # εικόνες: μεταφορά των αρχείων στο έγγραφο
        for blip in p.iter("{%s}blip" % NS["a"]):
            rid = blip.get(R + "embed")
            target = prels.get(rid or "")
            data = pfiles.get("word/" + target) if target else None
            if data is None:
                continue
            ext = Path(target).suffix.lstrip(".").lower() or "png"
            blip.set(R + "embed", self._add_media(data, ext))
            if block.svg and Path(block.svg).exists():
                srid = self._add_media(Path(block.svg).read_bytes(), "svg")
                ext_lst = blip.find("a:extLst", NS)
                if ext_lst is None:
                    ext_lst = etree.SubElement(blip, "{%s}extLst" % NS["a"])
                e = etree.SubElement(ext_lst, "{%s}ext" % NS["a"])
                e.set("uri", SVG_EXT_URI)
                sb = etree.SubElement(e, "{%s}svgBlip" % NS["asvg"], nsmap={"asvg": NS["asvg"]})
                sb.set(R + "embed", srid)
        return p

    # ----------------------------------------------------------------- ασκήσεις ως μπλοκ
    def exercise_blocks(self) -> list[dict]:
        """Οι ασκήσεις του εγγράφου με τη σειρά: για καθεμία οι θέσεις [start, end) στο σώμα (ως την επόμενη
        άσκηση ή τον επόμενο τίτλο), ο αριθμός της (π.χ. «4.32»), η ενότητα και η αρίθμηση (numId)."""
        hs = self.headings()
        labels = {idx: lab for idx, _el, lab in self._walk_labels()}
        out: list[dict] = []
        for i, h in enumerate(hs):
            end = hs[i + 1]["index"] if i + 1 < len(hs) else self._end_index()
            ex_num = self._exercise_num(h["index"] + 1, end, h["num_id"])
            if not ex_num:
                continue
            section = (h["label"] + " " if h["label"] else "") + h["title"]
            starts = []
            for j in range(h["index"] + 1, end):
                if self._ex_start(j, ex_num):
                    starts.append(j)
            for k, st in enumerate(starts):
                out.append({"start": st, "end": starts[k + 1] if k + 1 < len(starts) else end,
                            "label": labels.get(st, "").strip(), "section": section, "ex_num": ex_num,
                            "head": h["index"], "sec_end": end, "el": self.body[st]})
        return out

    def _block_of(self, el) -> dict:
        for b in self.exercise_blocks():
            if b["el"] is el:
                return b
        raise CorpusError("Η άσκηση-σημείο αναφοράς δεν βρέθηκε στο έγγραφο.")

    def _plain_empty(self, el) -> bool:
        """Κενή παράγραφος-διαχωριστικό: χωρίς περιεχόμενο, αρίθμηση, τίτλο, αλλαγή σελίδας ή ενότητας."""
        if el is None or etree.QName(el).localname != "p" or self.has_content(el):
            return False
        if self.numpr(el) is not None or self.outline_level(el) is not None:
            return False
        if el.find("w:pPr/w:sectPr", NS) is not None or el.find("w:pPr/w:pageBreakBefore", NS) is not None:
            return False
        return not any(br.get(_w("type")) == "page" for br in el.iter(_w("br")))

    def _block_take_end(self, b: dict) -> int:
        """Τέλος (αποκλειστικό) του περιεχομένου μιας άσκησης: ως το τελευταίο στοιχείο με περιεχόμενο και το πολύ
        μία κενή παράγραφο-διαχωριστικό μετά. Ό,τι ακολουθεί (π.χ. αλλαγή σελίδας πριν από νέο τίτλο) μένει στη θέση του."""
        last = b["start"]
        for j in range(b["start"], b["end"]):
            if self.has_content(self.body[j]):
                last = j
        t = last + 1
        if t < b["end"] and self._plain_empty(self.body[t]):
            t += 1
        return t

    def _separator_like(self, near) -> etree._Element:
        """Νέα κενή παράγραφος-διαχωριστικό, αντίγραφο ενός υπάρχοντος διαχωριστικού της περιοχής."""
        el = near
        for _ in range(40):
            if el is None:
                break
            if self._plain_empty(el):
                sep = copy.deepcopy(el)
                for k in list(sep.attrib):
                    if k.startswith("{http://schemas.microsoft.com/office/word/2010/wordml}"):
                        del sep.attrib[k]
                return sep
            el = el.getprevious()
        return etree.Element(_w("p"))

    def _set_numid(self, p, num_id: str, ilvl: int) -> None:
        ppr = p.find("w:pPr", NS)
        if ppr is None:
            ppr = etree.Element(_w("pPr"))
            p.insert(0, ppr)
        np_ = ppr.find("w:numPr", NS)
        if np_ is None:
            np_ = etree.SubElement(ppr, _w("numPr"))
        for x in list(np_):
            np_.remove(x)
        etree.SubElement(np_, _w("ilvl")).set(_w("val"), str(ilvl))
        etree.SubElement(np_, _w("numId")).set(_w("val"), num_id)
        _sort_children(ppr, _PPR_ORDER)

    def move_exercise(self, src_i: int, anchor_i: int, side: str) -> dict:
        """Μετακίνηση της άσκησης `src_i` (δείκτης στη λίστα `exercise_blocks`) πριν/μετά από την `anchor_i`.
        Η άσκηση παίρνει την αρίθμηση της ενότητας-προορισμού· οι αριθμοί όλων των ασκήσεων ξαναϋπολογίζονται
        από το Word (αυτόματη αρίθμηση), οπότε μένουν συνεχόμενοι."""
        if side not in ("before", "after"):
            raise CorpusError("Άγνωστη θέση.")
        blocks = self.exercise_blocks()
        if not (0 <= src_i < len(blocks) and 0 <= anchor_i < len(blocks)):
            raise CorpusError("Η άσκηση δεν βρέθηκε στο έγγραφο.")
        if src_i == anchor_i:
            raise CorpusError("Μια άσκηση δεν μπορεί να μπει δίπλα στον εαυτό της.")
        src, anc = blocks[src_i], blocks[anchor_i]
        same = src["section"] == anc["section"]
        if same and ((side == "after" and src_i == anchor_i + 1) or (side == "before" and src_i == anchor_i - 1)):
            raise CorpusError(f"Η {src['label']} είναι ήδη {'μετά από' if side == 'after' else 'πριν από'} την {anc['label']}.")
        take_end = self._block_take_end(src)
        els = [self.body[j] for j in range(src["start"], take_end)]
        for el in els:
            if etree.QName(el).localname == "p" and (el.find("w:pPr/w:sectPr", NS) is not None
                                                      or self.outline_level(el) is not None):
                raise CorpusError(f"Η {src['label']} περιέχει αλλαγή ενότητας ή τίτλο και δεν μετακινείται αυτόματα.")
        # σημείο-στόχος: το στοιχείο πριν από το οποίο θα μπει η άσκηση
        if side == "before":
            target = anc["el"]
        else:
            t = self._block_take_end(anc)
            target = self.body[t] if t < len(self.body) else None
        if target is not None and any(target is e for e in els):
            raise CorpusError(f"Η {src['label']} είναι ήδη εκεί.")
        orig_next = self.body[take_end] if take_end < len(self.body) else None
        # σελιδοδείκτες που ανοίγουν/κλείνουν έξω από την άσκηση μένουν στη θέση τους
        starts = {b.get(_w("id")): b for el in els for b in el.iter(_w("bookmarkStart"))}
        ends = {b.get(_w("id")): b for el in els for b in el.iter(_w("bookmarkEnd"))}
        loose = [m for k, m in starts.items() if k not in ends] + [m for k, m in ends.items() if k not in starts]
        els = [e for e in els if not any(e is m for m in loose)]  # σελιδοδείκτης ως αυτόνομο στοιχείο: μένει
        for m in loose:
            if m.getparent() is self.body:
                continue
            m.getparent().remove(m)
            if orig_next is not None:
                orig_next.addprevious(m)
        for el in els:
            self.body.remove(el)
        if target is None:
            sect = self.body.find("w:sectPr", NS)
            target = sect
        for el in els:
            if target is not None:
                target.addprevious(el)
            else:
                self.body.append(el)
        # διαχωριστικά: μία κενή παράγραφος πριν και μετά, όπως στις υπόλοιπες ασκήσεις
        first, last = els[0], els[-1]
        prev = first.getprevious()
        if prev is not None and not self._plain_empty(prev) and self.outline_level(prev) is None:
            first.addprevious(self._separator_like(prev))
        nxt = last.getnext()
        if (not self._plain_empty(last) and nxt is not None and etree.QName(nxt).localname in ("p", "tbl")
                and not self._plain_empty(nxt)):
            last.addnext(self._separator_like(first.getprevious()))
        # αρίθμηση της ενότητας-προορισμού
        if src["ex_num"] != anc["ex_num"]:
            for el in els:
                for p in ([el] if etree.QName(el).localname == "p" else list(el.iter(_w("p")))):
                    np_ = self.numpr(p)
                    if np_ and np_[0] == src["ex_num"]:
                        self._set_numid(p, anc["ex_num"], np_[1])
        self.changed.add("word/document.xml")
        return {"moved": src["label"], "anchor": anc["label"], "side": side, "el": src["el"]}

    # ----------------------------------------------------------------- έλεγχοι ακεραιότητας
    def snapshot(self) -> dict:
        """Ό,τι πρέπει να μείνει ίδιο μετά από μια αλλαγή: τίτλοι, εξισώσεις, εικόνες, πίνακες, ασκήσεις."""
        from .duplicates import doc_exercises

        exs = doc_exercises(self)["exercises"]
        return {
            "headings": [(h["label"], h["title"]) for h in self.headings()],
            "objects": sum(1 for _ in self.doc.iter(_w("object"))),
            "omml": sum(1 for _ in self.doc.iter(M + "oMath")),
            "drawings": sum(1 for _ in self.doc.iter(_w("drawing"))) + sum(1 for _ in self.doc.iter(_w("pict"))),
            "tables": sum(1 for _ in self.doc.iter(_w("tbl"))),
            "exercises": exs,
            "issues": numbering_issues(exs),
            # κλειδί το ίδιο το στοιχείο (κρατιέται ζωντανό, άρα σταθερό)· ΟΧΙ id(): τα proxies του lxml ανακυκλώνονται
            "labels": {b["el"]: b["label"] for b in self.exercise_blocks()},
        }

    # ----------------------------------------------------------------- αποθήκευση
    def save(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        self.files["word/document.xml"] = _xml(self.doc)
        if self.numbering is not None and "word/numbering.xml" in self.changed:
            self.files["word/numbering.xml"] = _xml(self.numbering)
        if getattr(self, "_rels_el", None) is not None:
            self.files["word/_rels/document.xml.rels"] = _xml(self._rels_el)
        names = self.order + [n for n in self.files if n not in self.order]
        fd, tmp = tempfile.mkstemp(suffix=".docx", dir=str(out_path.parent))
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for n in names:
                    z.writestr(n, self.files[n])
            os.replace(tmp, out_path)
        except PermissionError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise CorpusError(f"Δεν ήταν δυνατή η αποθήκευση στο «{out_path.name}». "
                              "Μήπως είναι ανοιχτό στο Word; Κλείστε το και ξαναδοκιμάστε.") from exc
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return out_path


# --------------------------------------------------------------------------- αρίθμηση: έλεγχοι και σύνοψη

_LABEL_RE = re.compile(r"^(.*?)(\d+)\.?$")


def numbering_issues(exs: list[dict]) -> list[str]:
    """Κενά ή επαναλήψεις στην αρίθμηση ανά ενότητα (π.χ. «4.12 → 4.14»)."""
    issues: list[str] = []
    prev: dict[str, tuple[str, int, str]] = {}
    for e in exs:
        m = _LABEL_RE.match(e.get("label") or "")
        if not m:
            continue
        pre, n = m.group(1), int(m.group(2))
        p = prev.get(e["section"])
        if p is not None and (pre != p[0] or n != p[1] + 1):
            issues.append(f"{e['section']}: μετά την {p[2]} ακολουθεί η {e['label']}")
        prev[e["section"]] = (pre, n, e["label"])
    return issues


def renumber_summary(before: dict, blocks: list[dict], skip: set) -> list[str]:
    """Σύνοψη αλλαγών αριθμών: «4.52–4.78 → 4.53–4.79» (σε συνεχόμενες ομάδες)."""
    rows = [(before[b["el"]], b["label"], b["section"]) for b in blocks
            if b["el"] in before and b["el"] not in skip and before[b["el"]] != b["label"]]

    def num(lab):
        m = _LABEL_RE.match(lab or "")
        return int(m.group(2)) if m else None

    out, run = [], []

    def flush():
        if run:
            a, b = run[0], run[-1]
            out.append(f"{a[0]} → {a[1]}" if len(run) == 1 else f"{a[0]}–{b[0]} → {a[1]}–{b[1]} ({len(run)} ασκήσεις)")

    for r in rows:
        if run and r[2] == run[-1][2] and num(r[0]) is not None and num(run[-1][0]) is not None \
                and num(r[0]) == num(run[-1][0]) + 1 and num(r[1]) == num(run[-1][1]) + 1:
            run.append(r)
        else:
            flush()
            run = [r]
    flush()
    return out


_REF_RE = re.compile(r"(?<![\d.,])(\d{1,2}\.\d{1,3})(?![\d]|[.,]\d)")


def reference_warnings(exs: list[dict], changed: dict[str, str]) -> list[str]:
    """Ασκήσεις που γράφουν στο κείμενό τους αριθμό άλλης άσκησης που άλλαξε (π.χ. «όπως στην 4.33»)."""
    out = []
    for e in exs:
        text = " ".join(p["md"] for p in e["parts"])
        text = re.sub(r"\$[^$]*\$", " ", text)
        for ref in sorted(set(_REF_RE.findall(text))):
            if ref in changed and ref != e["label"]:
                out.append(f"Η άσκηση {e['label']} γράφει «{ref}»· η άσκηση που ήταν {ref} είναι τώρα η {changed[ref]}. "
                           "Ελέγξτε αν πρόκειται για παραπομπή και διορθώστε τη στο Word.")
    return out


def _verify(before: dict, after: dict, extra: int) -> list[str]:
    """Έλεγχοι ακεραιότητας μετά από αλλαγή. Επιστρέφει τα προβλήματα (κενή λίστα = όλα σωστά)."""
    from collections import Counter

    errs = []
    if before["headings"] != after["headings"]:
        errs.append("άλλαξαν οι τίτλοι κεφαλαίων/ενοτήτων")
    if after["objects"] != before["objects"]:
        errs.append(f"εξισώσεις MathType: {before['objects']} → {after['objects']}")
    if after["omml"] < before["omml"] or after["drawings"] < before["drawings"] or after["tables"] < before["tables"]:
        errs.append("χάθηκαν εξισώσεις, εικόνες ή πίνακες")
    if len(after["exercises"]) != len(before["exercises"]) + extra:
        errs.append(f"πλήθος ασκήσεων: {len(before['exercises'])} → {len(after['exercises'])} (αναμενόταν "
                    f"{len(before['exercises']) + extra})")
    missing = Counter(e["fp"] for e in before["exercises"]) - Counter(e["fp"] for e in after["exercises"])
    if missing:
        errs.append(f"{sum(missing.values())} ασκήσεις δεν βρέθηκαν ίδιες μετά την αλλαγή")
    if len(after["issues"]) > len(before["issues"]):
        errs.append("η αρίθμηση έπαψε να είναι συνεχόμενη: " + "; ".join(i for i in after["issues"] if i not in before["issues"]))
    return errs


# --------------------------------------------------------------------------- υψηλού επιπέδου

def _resolve(exs: list[dict], ref: dict) -> int:
    """Άσκηση από {fp, label}: πρώτα με το περιεχόμενο (fp), μετά με τον αριθμό."""
    cands = [i for i, e in enumerate(exs) if e["fp"] == ref.get("fp")]
    if len(cands) > 1:
        lab = [i for i in cands if exs[i]["label"] == ref.get("label")]
        cands = lab or cands
    if not cands:
        raise CorpusError(f"Η άσκηση {ref.get('label') or ''} δεν βρέθηκε όπως ήταν — ίσως άλλαξε το αρχείο στο Word. "
                          "Πατήστε «↻ Ανανέωση» και ξαναδοκιμάστε.")
    return cands[0]


def move_in(path: str | Path, src: dict, anchor: dict, side: str, make_backup: bool = True) -> dict:
    """Μετακίνηση άσκησης μέσα στο αρχείο, με ελέγχους ακεραιότητας πριν από την αποθήκευση."""
    path = Path(path)
    doc = CorpusDoc(path)
    before = doc.snapshot()
    si, ai = _resolve(before["exercises"], src), _resolve(before["exercises"], anchor)
    anchor_el = doc.exercise_blocks()[ai]["el"]
    info = doc.move_exercise(si, ai, side)
    after = doc.snapshot()
    errs = _verify(before, after, 0)
    blocks = doc.exercise_blocks()
    pos = {id(b["el"]): k for k, b in enumerate(blocks)}
    moved_new = pos.get(id(info["el"]))
    if moved_new is None or id(anchor_el) not in pos or pos[id(anchor_el)] + (1 if side == "after" else -1) != moved_new:
        errs.append("η άσκηση δεν βρέθηκε στη νέα θέση")
    if errs:
        raise CorpusError("Η μετακίνηση ακυρώθηκε γιατί δεν πέρασε τους ελέγχους ασφαλείας: " + "; ".join(errs)
                          + ". Το αρχείο σας δεν άλλαξε.")
    new_label = blocks[moved_new]["label"]
    changed = {before["labels"][b["el"]]: b["label"] for b in blocks
               if b["el"] in before["labels"] and before["labels"][b["el"]] != b["label"]}
    result = {"moved_from": info["moved"], "moved_to": new_label, "anchor": blocks[pos[id(anchor_el)]]["label"],
              "side": side, "section": blocks[moved_new]["section"],
              "renumbered": renumber_summary(before["labels"], blocks, {info["el"]}),
              "warnings": reference_warnings(after["exercises"], changed),
              "checks": _checks_text(after, before)}
    if make_backup:
        result["backup"] = str(backup(path))
    doc.save(path)
    result["path"] = str(path)
    result["sha"] = file_sha(path)
    return result


def _checks_text(after: dict, before: dict) -> list[str]:
    return [f"Τίτλοι κεφαλαίων/ενοτήτων: ίδιοι ({len(after['headings'])})",
            f"Ασκήσεις: {len(before['exercises'])} → {len(after['exercises'])}, όλες με το ίδιο περιεχόμενο",
            f"Εξισώσεις MathType: {after['objects']} (ίδιες) · εξισώσεις Word: {before['omml']} → {after['omml']}",
            f"Εικόνες/σχήματα: {before['drawings']} → {after['drawings']} · πίνακες: {before['tables']} → {after['tables']}",
            "Αρίθμηση: συνεχόμενη σε όλες τις ενότητες" if not after["issues"]
            else f"Αρίθμηση: {len(after['issues'])} σημεία που ήταν ήδη ασυνεχή πριν από την αλλαγή (δεν προστέθηκε κανένα)"]


def file_sha(path: str | Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def undo_change(path: str | Path, backup_path: str | Path, sha: str) -> dict:
    """Επαναφορά του αντιγράφου ασφαλείας — μόνο αν το αρχείο δεν άλλαξε από τότε (π.χ. στο Word)."""
    path, backup_path = Path(path), Path(backup_path)
    if not backup_path.exists():
        raise CorpusError("Δεν βρέθηκε το αντίγραφο ασφαλείας.")
    if file_sha(path) != sha:
        raise CorpusError("Το αρχείο άλλαξε από τότε (π.χ. στο Word), οπότε η αναίρεση δεν γίνεται αυτόματα. "
                          f"Το προηγούμενο αρχείο υπάρχει στο «{backup_path}».")
    fresh = backup(path)  # και η κατάσταση πριν από την αναίρεση κρατιέται
    shutil.copy2(backup_path, path)
    return {"restored": str(backup_path), "backup": str(fresh)}


def backup(path: Path, keep: int = 15) -> Path:
    """Αντίγραφο ασφαλείας πριν από κάθε αλλαγή: «Αντίγραφα ασφαλείας/όνομα - ΗΗΜΜ-ωωλλ.docx»."""
    from datetime import datetime

    folder = path.parent / "Αντίγραφα ασφαλείας"
    folder.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    dst = folder / f"{path.stem} - {stamp}{path.suffix}"
    k = 2
    while dst.exists():  # δύο αλλαγές στο ίδιο δευτερόλεπτο (π.χ. αλλαγή και αναίρεση): ποτέ αντικατάσταση
        dst = folder / f"{path.stem} - {stamp}-{k}{path.suffix}"
        k += 1
    shutil.copy2(path, dst)
    olds = sorted(folder.glob(f"{glob_escape(path.stem)} - *{path.suffix}"))
    for old in olds[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def insert_into(path: str | Path, key: dict, exercises: list[dict], workdir: Path, out_path: str | Path | None = None,
                math_font: str = "Cambria Math", make_backup: bool = True) -> dict:
    """Εισαγωγή ασκήσεων. Κάθε άσκηση μπορεί να έχει `place`: {"side": "after"|"before", "fp", "label"} — τότε
    μπαίνει δίπλα στη συγκεκριμένη άσκηση του εγγράφου· αλλιώς στο τέλος της ενότητας `key`.
    Πριν από την αποθήκευση γίνονται έλεγχοι ακεραιότητας (τίτλοι, εξισώσεις, παλιές ασκήσεις, αρίθμηση)."""
    path = Path(path)
    doc = CorpusDoc(path)
    before = doc.snapshot()
    blocks0 = doc.exercise_blocks()
    # ομάδες με την ίδια θέση, με τη σειρά που εμφανίζονται (ίδια θέση = συνεχόμενες, με τη σειρά της οθόνης)
    groups: list[tuple[tuple, list[dict]]] = []
    for ex in exercises:
        pl = ex.get("place") or None
        k = ("end",) if not pl else (pl.get("side"), pl.get("fp"), pl.get("label"))
        for gk, lst in groups:
            if gk == k:
                lst.append(ex)
                break
        else:
            groups.append((k, [ex]))
    infos, warnings, removed = [], [], 0
    for gk, lst in groups:
        at = None
        if gk[0] != "end":
            i = _resolve(before["exercises"], {"fp": gk[1], "label": gk[2]})
            at = {"side": gk[0], "el": blocks0[i]["el"]}
        sub = [{k: v for k, v in e.items() if k != "place"} for e in lst]
        inf = doc.insert(key, sub, workdir, math_font=math_font, at=at)
        inf["place"] = None if at is None else {"side": gk[0], "label": gk[2]}
        infos.append(inf)
        warnings += inf.get("warnings", [])
        removed += inf.get("removed_placeholders", 0)
    after = doc.snapshot()
    errs = _verify(before, after, len(exercises))
    if errs:
        raise CorpusError("Η εισαγωγή ακυρώθηκε γιατί δεν πέρασε τους ελέγχους ασφαλείας: " + "; ".join(errs)
                          + ". Το αρχείο σας δεν άλλαξε.")
    blocks = doc.exercise_blocks()
    new_labels = [b["label"] for b in blocks if b["el"] not in before["labels"]]
    changed = {before["labels"][b["el"]]: b["label"] for b in blocks
               if b["el"] in before["labels"] and before["labels"][b["el"]] != b["label"]}
    info = {"inserted": len(exercises), "section": infos[0]["section"], "section_label": infos[0]["section_label"],
            "first_label": infos[0]["first_label"], "last_label": infos[-1]["last_label"],
            "labels": new_labels, "groups": [{"place": x["place"], "first_label": x["first_label"],
                                              "last_label": x["last_label"], "section": x["section"]} for x in infos],
            "removed_placeholders": removed, "warnings": warnings + reference_warnings(after["exercises"], changed),
            "renumbered": renumber_summary(before["labels"], blocks, set()), "checks": _checks_text(after, before)}
    target = Path(out_path) if out_path else path
    if make_backup and target == path:
        info["backup"] = str(backup(path))
    doc.save(target)
    info["path"] = str(target)
    info["sha"] = file_sha(target)
    return info


def outline_of(path: str | Path) -> list[dict]:
    return CorpusDoc(path).outline()


if __name__ == "__main__":  # python -m app.corpus_doc έγγραφο.docx → λίστα ενοτήτων
    import sys

    print(json.dumps(outline_of(sys.argv[1]), ensure_ascii=False, indent=1))


def sample_document(out: Path, workdir: Path) -> Path:
    """Μικρό δοκιμαστικό έγγραφο συλλογής (για τον αυτοέλεγχο και τις δοκιμές)."""
    md = workdir / "sample.md"
    md.write_text("# ΚΕΦΑΛΑΙΟ 1: ΕΥΘΕΙΑ\n\n## ΕΞΙΣΩΣΗ ΕΥΘΕΙΑΣ\n\nΕισαγωγικό κείμενο με $x^2$.\n\n"
                  "## ΓΕΝΙΚΗ ΜΟΡΦΗ\n\n# ΚΕΦΑΛΑΙΟ 2: ΚΥΚΛΟΣ\n", encoding="utf-8")
    res = _run([pandoc_path(), str(md), "-f", MD_FORMAT, "-t", "docx", "-o", str(out)], workdir)
    if res.returncode != 0:
        raise CorpusError("Σφάλμα pandoc:\n" + (res.stderr or res.stdout)[-2000:])
    return out
