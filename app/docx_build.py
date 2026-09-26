"""Συναρμολόγηση του διαγωνίσματος: Markdown → pandoc (με πρότυπο) → τελικές διορθώσεις στο .docx.

Βήματα μετά το pandoc:
1. στοίχιση/έντονα στις ειδικές παραγράφους (τίτλος, «ΘΕΜΑΤΑ», μονάδες, σχήματα),
2. αφαίρεση περιγραμμάτων από τους πίνακες,
3. ενσωμάτωση των σχημάτων ως SVG (το PNG μένει ως εφεδρικό) → «Μετατροπή σε σχήμα» στο Word,
4. ίδια γραμματοσειρά/μέγεθος παντού και δική μας κεφαλίδα («Σελίδα X από Y» στο κέντρο)
   και υποσέλιδο (τάξη αριστερά, «Επιμέλεια: …» δεξιά), ανεξάρτητα από το πρότυπο,
5. έλεγχος round-trip: οι εξισώσεις διαβάζονται πίσω από το .docx.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import points as pts
from .paths import pandoc_path, resource

LETTERS = ["Α", "Β", "Γ", "Δ", "Ε", "ΣΤ", "Ζ", "Η", "Θ", "Ι"]
ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"]

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
}
W = "{%s}" % NS["w"]
SVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"
IMG_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

SPECIAL_STYLES = {
    "ExamTitle": ("center", True),
    "ExamSubtitle": ("center", False),
    "ExamCenter": ("center", None),
    "ExamFigure": ("center", None),
    "ExamPointsRight": ("right", None),
    "ExamPointsLeft": ("left", None),
}


class BuildError(RuntimeError):
    pass


@dataclass
class Figure:
    png: str
    svg: str | None
    width_cm: float


@dataclass
class BuildContext:
    figures: list[Figure] = field(default_factory=list)
    math_count: int = 0
    warnings: list[str] = field(default_factory=list)
    media_dir: Path | None = None  # τα σχήματα αντιγράφονται εδώ με απλά ονόματα (ασφαλές στα Windows)


# =========================================================================== Markdown

_MATH_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)


def _count_math(text: str) -> int:
    return len(_MATH_RE.findall(text or ""))


def _fix_refs(text: str, refmap: dict[str, str]) -> str:
    def rep(m):
        lab = m.group(1).strip()
        return refmap.get(lab, f"({lab})")

    return re.sub(r"\[\[([^\]]+)\]\]", rep, text or "")


def _clean(text: str) -> str:
    """Καθαρισμός κειμένου για pandoc: χωρίς κενές γραμμές στην αρχή/τέλος, $$ σε δική τους γραμμή."""
    t = (text or "").strip()
    t = re.sub(r"\s*\$\$(.+?)\$\$\s*", lambda m: "\n$$" + m.group(1).strip() + "$$\n", t, flags=re.S)
    return t.strip()


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join((pad + ln) if ln.strip() else "" for ln in text.split("\n"))


def _choices_table(choices: dict | None, indent: int = 0) -> str:
    if not choices or not choices.get("options"):
        return ""
    opts = choices["options"]
    cols = 4 if int(choices.get("columns", 2)) >= 4 else 2
    cells = [f"**{LETTERS[i] if i < len(LETTERS) else i + 1}:** {o.strip()}" for i, o in enumerate(opts)]
    while len(cells) % cols:
        cells.append(" ")
    rows = ["| " + " | ".join([" "] * cols) + " |", "|" + "|".join(["---"] * cols) + "|"]
    for r in range(0, len(cells), cols):
        rows.append("| " + " | ".join(c.replace("|", "\\|") for c in cells[r : r + cols]) + " |")
    return _indent("\n".join(rows), indent)


def _div(style: str, body: str) -> str:
    return f'::: {{custom-style="{style}"}}\n{body}\n:::'


def _figure_md(fig_info: dict | None, ctx: BuildContext) -> str:
    if not fig_info or not fig_info.get("png"):
        return ""
    src = Path(fig_info["png"])
    if not src.exists():
        ctx.warnings.append(f"Λείπει το αρχείο σχήματος {src.name}· το σχήμα παραλείφθηκε.")
        return ""
    ctx.figures.append(Figure(str(src), fig_info.get("svg"), float(fig_info.get("width_cm", 7))))
    if ctx.media_dir is not None:
        name = f"figure_{len(ctx.figures)}.png"
        shutil.copyfile(src, ctx.media_dir / name)
        ref = name
    else:
        ref = src.as_posix()
    return _div("ExamFigure", f"![]({ref}){{width={float(fig_info.get('width_cm', 7)):.1f}cm}}")


def _item_label(level: int, idx: int, theme_letter: str, sublevel_style: str) -> str:
    if level == 1:
        return f"{theme_letter}{idx + 1}."
    return ""


def _render_subitems(items: list[dict], level: int, refmap: dict, ctx: BuildContext, style: str, base_indent: int) -> list[str]:
    """Επίπεδο 2: λίστα i), ii) ή κουκκίδες. Επίπεδο 3: κουκκίδες."""
    out: list[str] = []
    for i, it in enumerate(items):
        text = _clean(_fix_refs(it.get("text", ""), refmap))
        ctx.math_count += _count_math(text)
        if level == 2 and style == "roman":
            marker = f"{ROMAN[i] if i < len(ROMAN) else i + 1})"
        else:
            marker = "-"
        marker_w = max(4, len(marker) + 1)
        first, *rest = (text or " ").split("\n")
        block = " " * base_indent + marker.ljust(marker_w) + first
        if rest:
            block += "\n" + _indent("\n".join(rest), base_indent + marker_w)
        parts = [block]
        tbl = _choices_table(it.get("choices"), base_indent + marker_w)
        if tbl:
            ctx.math_count += sum(_count_math(o) for o in it["choices"]["options"])
            parts.append(tbl)
        if it.get("items"):
            parts.append("\n".join(_render_subitems(it["items"], level + 1, refmap, ctx, style, base_indent + marker_w)))
        out.append("\n\n".join(parts))
    return out


def _refmap_single(items: list[dict], letter: str, style: str) -> dict[str, str]:
    m: dict[str, str] = {}
    for i, it in enumerate(items):
        m.setdefault(it.get("label", ""), f"{letter}{i + 1}")
        for j, sub in enumerate(it.get("items") or []):
            key = sub.get("label", "")
            if key and key not in m:
                m[key] = f"({ROMAN[j]})" if style == "roman" and j < len(ROMAN) else f"({key})"
    return m


def _refmap_nested(items: list[dict], style: str) -> dict[str, str]:
    m: dict[str, str] = {}
    for j, it in enumerate(items):
        key = it.get("label", "")
        if key:
            m[key] = f"({ROMAN[j]})" if style == "roman" and j < len(ROMAN) else f"({key})"
    return m


def render_theme(t_idx: int, theme: dict, ctx: BuildContext, style: str, align: str) -> str:
    letter = LETTERS[t_idx] if t_idx < len(LETTERS) else str(t_idx + 1)
    exercises = [e for e in theme.get("exercises", []) if e]
    blocks = [f"**ΘΕΜΑ {letter}**"]
    n_level1 = 0

    if len(exercises) == 1:
        ex = exercises[0]
        refmap = _refmap_single(ex.get("items") or [], letter, style)
        stem = _clean(_fix_refs(ex.get("stem", ""), refmap))
        if stem:
            ctx.math_count += _count_math(stem)
            blocks.append(stem)
        fig = _figure_md(ex.get("figure_render"), ctx)
        if fig:
            blocks.append(fig)
        for i, it in enumerate(ex.get("items") or []):
            text = _clean(_fix_refs(it.get("text", ""), refmap))
            ctx.math_count += _count_math(text)
            blocks.append(f"**{letter}{i + 1}.** {text}")
            tbl = _choices_table(it.get("choices"))
            if tbl:
                ctx.math_count += sum(_count_math(o) for o in it["choices"]["options"])
                blocks.append(tbl)
            if it.get("items"):
                blocks.append("\n\n".join(_render_subitems(it["items"], 2, refmap, ctx, style, 0)))
        n_level1 = len(ex.get("items") or [])
        closing = _clean(_fix_refs(ex.get("closing", ""), refmap))
        if closing:
            ctx.math_count += _count_math(closing)
            blocks.append(closing)
    else:
        for k, ex in enumerate(exercises):
            refmap = _refmap_nested(ex.get("items") or [], style)
            stem = _clean(_fix_refs(ex.get("stem", ""), refmap))
            ctx.math_count += _count_math(stem)
            blocks.append(f"**{letter}{k + 1}.** {stem}".rstrip())
            fig = _figure_md(ex.get("figure_render"), ctx)
            if fig:
                blocks.append(fig)
            if ex.get("items"):
                blocks.append("\n\n".join(_render_subitems(ex["items"], 2, refmap, ctx, style, 0)))
            closing = _clean(_fix_refs(ex.get("closing", ""), refmap))
            if closing:
                ctx.math_count += _count_math(closing)
                blocks.append(closing)
        n_level1 = len(exercises)

    values = theme.get("points") or []
    if values:
        if n_level1 and len(values) != n_level1:
            ctx.warnings.append(
                f"ΘΕΜΑ {letter}: οι μονάδες ({len(values)}) δεν αντιστοιχούν στα ερωτήματα ({n_level1})."
            )
        pstyle = "ExamPointsLeft" if align == "left" else "ExamPointsRight"
        blocks.append(_div(pstyle, f"**{pts.points_line(values)}**"))
    return "\n\n".join(blocks)


def exam_markdown(exam: dict, ctx: BuildContext, style: str = "roman", align: str = "right") -> str:
    blocks: list[str] = []
    if exam.get("title"):
        blocks.append(_div("ExamTitle", f"**{exam['title'].strip()}**"))
    if exam.get("subtitle"):
        blocks.append(_div("ExamSubtitle", exam["subtitle"].strip()))
    if exam.get("show_name_line", True):
        blocks.append("**ΟΝΟΜΑΤΕΠΩΝΥΜΟ: \\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_\\_**")
    date = (exam.get("date") or "").strip()
    blocks.append("**ΗΜΕΡΟΜΗΝΙΑ: " + (date if date else "\\_" * 24) + "**")
    blocks.append(_div("ExamCenter", "**ΘΕΜΑΤΑ**"))
    for i, theme in enumerate(exam.get("themes", [])):
        blocks.append(render_theme(i, theme, ctx, style, align))
    closing = exam.get("closing_text", "ΚΑΛΗ ΕΠΙΤΥΧΙΑ!")
    if closing:
        blocks.append(_div("ExamCenter", f"**{closing}**"))
    return "\n\n".join(blocks) + "\n"


# =========================================================================== DOCX

_MD_FORMAT = "markdown+tex_math_dollars+fancy_lists+pipe_tables-implicit_figures"


def _run_pandoc(md_path: Path, out_path: Path, template: Path, cwd: Path) -> None:
    cmd = [
        pandoc_path(),
        str(md_path),
        "-f", _MD_FORMAT,
        "-t", "docx",
        "--reference-doc", str(template),
        "-o", str(out_path),
    ]
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    if res.returncode != 0:
        raise BuildError("Σφάλμα pandoc:\n" + (res.stderr or res.stdout)[-2000:])


_PPR_ORDER = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
              "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
              "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
              "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
              "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
              "sectPr", "pPrChange"]
_RPR_ORDER = ["rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
              "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
              "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
              "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"]
_TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
                "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar",
                "tblLook", "tblCaption", "tblDescription", "tblPrChange"]


_STYLE_ORDER = ["name", "aliases", "basedOn", "next", "link", "autoRedefine", "hidden", "uiPriority",
                "semiHidden", "unhideWhenUsed", "qFormat", "locked", "personal", "personalCompose",
                "personalReply", "rsid", "pPr", "rPr", "tblPr", "trPr", "tcPr", "tblStylePr"]


def _get_or_insert(parent, local: str, order: list[str]):
    """Βρίσκει ή δημιουργεί παιδί στη θέση που ορίζει το σχήμα OOXML."""
    el = parent.find(W + local)
    if el is not None:
        return el
    el = etree.Element(W + local)
    my = order.index(local)
    for i, child in enumerate(parent):
        name = etree.QName(child).localname
        if name in order and order.index(name) > my:
            parent.insert(i, el)
            return el
    parent.append(el)
    return el


def _set_para_format(p, align: str, bold: bool | None) -> None:
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    _get_or_insert(ppr, "jc", _PPR_ORDER).set(W + "val", align)
    if bold:
        for r in p.iter(W + "r"):
            rpr = r.find(W + "rPr")
            if rpr is None:
                rpr = etree.Element(W + "rPr")
                r.insert(0, rpr)
            _get_or_insert(rpr, "b", _RPR_ORDER)


def _style_ids(styles_xml: bytes) -> dict[str, str]:
    """styleId ανά όνομα στυλ (το pandoc δίνει id από το όνομα, αλλά ας μην το υποθέτουμε)."""
    root = etree.fromstring(styles_xml)
    out = {}
    for st in root.findall(W + "style"):
        name = st.find(W + "name")
        if name is not None:
            out[name.get(W + "val")] = st.get(W + "styleId")
    return out


def _fix_document(doc_xml: bytes, style_ids: dict[str, str]) -> bytes:
    root = etree.fromstring(doc_xml)
    id_to_fmt = {style_ids[name]: fmt for name, fmt in SPECIAL_STYLES.items() if name in style_ids}
    for p in root.iter(W + "p"):
        ps = p.find(f"{W}pPr/{W}pStyle")
        if ps is not None and ps.get(W + "val") in id_to_fmt:
            align, bold = id_to_fmt[ps.get(W + "val")]
            _set_para_format(p, align, bold)
    # πίνακες χωρίς περιγράμματα
    for tbl in root.iter(W + "tbl"):
        tblpr = tbl.find(W + "tblPr")
        if tblpr is None:
            tblpr = etree.SubElement(tbl, W + "tblPr")
            tbl.insert(0, tblpr)
        for old in tblpr.findall(W + "tblBorders"):
            tblpr.remove(old)
        borders = _get_or_insert(tblpr, "tblBorders", _TBLPR_ORDER)
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            b = etree.SubElement(borders, W + side)
            b.set(W + "val", "none")
            b.set(W + "sz", "0")
            b.set(W + "space", "0")
            b.set(W + "color", "auto")
        for tc in tbl.iter(W + "tc"):
            tcpr = tc.find(W + "tcPr")
            if tcpr is not None:
                for old in tcpr.findall(W + "tcBorders"):
                    tcpr.remove(old)
        width = _get_or_insert(tblpr, "tblW", _TBLPR_ORDER)
        width.set(W + "type", "pct")
        width.set(W + "w", "5000")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _embed_svgs(files: dict[str, bytes], figures: list[Figure]) -> None:
    """Προσθήκη asvg:svgBlip μέσα στο a:blip για κάθε σχήμα που έχει SVG."""
    doc = etree.fromstring(files["word/document.xml"])
    rels = etree.fromstring(files["word/_rels/document.xml.rels"])
    blips = list(doc.iter("{%s}blip" % NS["a"]))
    if len(blips) != len(figures):
        return  # απρόβλεπτη αντιστοίχιση — μένουν τα PNG
    existing = {r.get("Id") for r in rels}
    n = 1
    for blip, fig in zip(blips, figures):
        if not fig.svg or not Path(fig.svg).exists():
            continue
        while f"rIdSvg{n}" in existing:
            n += 1
        rid = f"rIdSvg{n}"
        existing.add(rid)
        target = f"media/figure_{n}.svg"
        files[f"word/{target}"] = Path(fig.svg).read_bytes()
        rel = etree.SubElement(rels, "{%s}Relationship" % NS["rel"])
        rel.set("Id", rid)
        rel.set("Type", IMG_REL)
        rel.set("Target", target)
        ext_lst = blip.find("{%s}extLst" % NS["a"])
        if ext_lst is None:
            ext_lst = etree.SubElement(blip, "{%s}extLst" % NS["a"])
        ext = etree.SubElement(ext_lst, "{%s}ext" % NS["a"])
        ext.set("uri", SVG_EXT_URI)
        svgblip = etree.SubElement(ext, "{%s}svgBlip" % NS["asvg"], nsmap={"asvg": NS["asvg"]})
        svgblip.set("{%s}embed" % NS["r"], rid)
    files["word/document.xml"] = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)
    files["word/_rels/document.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    ct = etree.fromstring(files["[Content_Types].xml"])
    if not any(d.get("Extension", "").lower() == "svg" for d in ct.findall("{%s}Default" % NS["ct"])):
        d = etree.SubElement(ct, "{%s}Default" % NS["ct"])
        d.set("Extension", "svg")
        d.set("ContentType", "image/svg+xml")
        files["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)


# --------------------------------------------------------------------------- γραμματοσειρά

@dataclass
class PageSetup:
    """Ό,τι επιβάλλει η εφαρμογή ανεξάρτητα από το πρότυπο."""

    font_name: str = "Cambria"
    font_size: float = 12  # pt
    class_name: str = ""  # υποσέλιδο αριστερά
    editor: str = ""  # υποσέλιδο δεξιά: «Επιμέλεια: …»


def _set_run_fonts(rpr, font: str, half_points: int) -> None:
    rfonts = _get_or_insert(rpr, "rFonts", _RPR_ORDER)
    for attr in list(rfonts.attrib):
        if attr.lower().endswith("theme"):  # asciiTheme, cstheme… υπερισχύουν των ονομάτων — αφαιρούνται
            del rfonts.attrib[attr]
    for attr in ("ascii", "hAnsi", "cs", "eastAsia"):
        rfonts.set(W + attr, font)
    _get_or_insert(rpr, "sz", _RPR_ORDER).set(W + "val", str(half_points))
    _get_or_insert(rpr, "szCs", _RPR_ORDER).set(W + "val", str(half_points))


def _apply_fonts(styles_xml: bytes, font: str, size_pt: float) -> bytes:
    """Ίδια γραμματοσειρά και μέγεθος παντού: προεπιλογές εγγράφου και κάθε στυλ του προτύπου."""
    root = etree.fromstring(styles_xml)
    hp = int(round(size_pt * 2))
    defaults = root.find(W + "docDefaults")
    if defaults is None:
        defaults = etree.Element(W + "docDefaults")
        root.insert(0, defaults)
    rpr_default = defaults.find(W + "rPrDefault")
    if rpr_default is None:
        rpr_default = etree.SubElement(defaults, W + "rPrDefault")
        defaults.insert(0, rpr_default)
    rpr = rpr_default.find(W + "rPr")
    if rpr is None:
        rpr = etree.SubElement(rpr_default, W + "rPr")
    _set_run_fonts(rpr, font, hp)
    for st in root.findall(W + "style"):
        if st.get(W + "type") not in ("paragraph", "character", "table"):
            continue
        _set_run_fonts(_get_or_insert(st, "rPr", _STYLE_ORDER), font, hp)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _sort_children(el, order: list[str]) -> None:
    """Ταξινόμηση παιδιών κατά τη σειρά του σχήματος OOXML (όσα δεν είναι στη λίστα μένουν στο τέλος)."""
    kids = list(el)
    if len(kids) < 2:
        return
    rank = {n: i for i, n in enumerate(order)}
    kids_sorted = sorted(kids, key=lambda c: rank.get(etree.QName(c).localname, len(order)))
    if kids_sorted != kids:
        for c in kids:
            el.remove(c)
        el.extend(kids_sorted)


def _normalize_order(xml: bytes) -> bytes:
    root = etree.fromstring(xml)
    for rpr in root.iter(W + "rPr"):
        _sort_children(rpr, _RPR_ORDER)
    for ppr in root.iter(W + "pPr"):
        _sort_children(ppr, _PPR_ORDER)
    for tblpr in root.iter(W + "tblPr"):
        _sort_children(tblpr, _TBLPR_ORDER)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _apply_run_fonts(doc_xml: bytes, font: str, size_pt: float) -> bytes:
    """Και στα runs του κειμένου (μόνο όπου το pandoc έβαλε ρητή γραμματοσειρά/μέγεθος)."""
    root = etree.fromstring(doc_xml)
    hp = int(round(size_pt * 2))
    math_ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    for rpr in root.iter(W + "rPr"):
        parent = rpr.getparent()
        if parent is not None and etree.QName(parent).namespace == math_ns:
            continue  # οι εξισώσεις μένουν σε Cambria Math
        if rpr.find(W + "rFonts") is not None or rpr.find(W + "sz") is not None:
            _set_run_fonts(rpr, font, hp)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# --------------------------------------------------------------------------- κεφαλίδα / υποσέλιδο

HDR_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
FTR_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"
HDR_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"
FTR_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_W_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
         'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def _xml_escape(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rpr_xml(font: str, hp: int, bold: bool = False) -> str:
    f = _xml_escape(font)
    return (f'<w:rPr><w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:cs="{f}" w:eastAsia="{f}"/>'
            + ("<w:b/><w:bCs/>" if bold else "") + f'<w:sz w:val="{hp}"/><w:szCs w:val="{hp}"/></w:rPr>')


def _text_run(text: str, font: str, hp: int, bold: bool = False) -> str:
    return f'<w:r>{_rpr_xml(font, hp, bold)}<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def _field_runs(instr: str, font: str, hp: int, bold: bool = True) -> str:
    rp = _rpr_xml(font, hp, bold)
    return (f'<w:r>{rp}<w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r>{rp}<w:instrText xml:space="preserve"> {instr} </w:instrText></w:r>'
            f'<w:r>{rp}<w:fldChar w:fldCharType="separate"/></w:r>'
            f'<w:r>{rp}<w:t>1</w:t></w:r>'
            f'<w:r>{rp}<w:fldChar w:fldCharType="end"/></w:r>')


def header_xml(font: str, size_pt: float) -> bytes:
    """«Σελίδα X από Y» στο κέντρο της κεφαλίδας."""
    hp = int(round(size_pt * 2))
    body = (_text_run("Σελίδα ", font, hp) + _field_runs("PAGE", font, hp)
            + _text_run(" από ", font, hp) + _field_runs("NUMPAGES", font, hp))
    xml = (f'{_XML_HEAD}<w:hdr {_W_NS}><w:p><w:pPr><w:jc w:val="center"/>{_rpr_xml(font, hp)}</w:pPr>'
           f'{body}</w:p></w:hdr>')
    return xml.encode("utf-8")


def footer_xml(font: str, size_pt: float, class_name: str, editor: str, text_width_twips: int) -> bytes:
    """Τάξη αριστερά και «Επιμέλεια: …» δεξιά, στην ίδια γραμμή (στάση στηλοθέτη στο δεξί περιθώριο)."""
    hp = int(round(size_pt * 2))
    runs = _text_run(class_name, font, hp) if class_name else ""
    if editor:
        runs += f'<w:r>{_rpr_xml(font, hp)}<w:tab/></w:r>' + _text_run(f"Επιμέλεια: {editor}", font, hp)
    xml = (f'{_XML_HEAD}<w:ftr {_W_NS}><w:p><w:pPr><w:tabs><w:tab w:val="right" w:pos="{text_width_twips}"/></w:tabs>'
           f'{_rpr_xml(font, hp)}</w:pPr>{runs}</w:p></w:ftr>')
    return xml.encode("utf-8")


def _text_width(sect) -> int:
    pg, mar = sect.find(W + "pgSz"), sect.find(W + "pgMar")
    try:
        width = int(pg.get(W + "w")) if pg is not None else 11906
        left = int(mar.get(W + "left")) if mar is not None else 1134
        right = int(mar.get(W + "right")) if mar is not None else 1134
        return max(2000, width - left - right)
    except (TypeError, ValueError):
        return 9638


def _install_header_footer(files: dict[str, bytes], setup: PageSetup) -> None:
    """Αντικαθιστά κεφαλίδα/υποσέλιδο του προτύπου με τα δικά μας σε κάθε ενότητα του εγγράφου."""
    doc = etree.fromstring(files["word/document.xml"])
    rels = etree.fromstring(files["word/_rels/document.xml.rels"])
    ct = etree.fromstring(files["[Content_Types].xml"])
    existing = {r.get("Id") for r in rels}

    def new_rid(base: str) -> str:
        i = 1
        while f"{base}{i}" in existing:
            i += 1
        existing.add(f"{base}{i}")
        return f"{base}{i}"

    sects = list(doc.iter(W + "sectPr"))
    if not sects:
        body = doc.find(W + "body")
        sects = [etree.SubElement(body, W + "sectPr")]
    width = _text_width(sects[-1])
    files["word/header_exam.xml"] = header_xml(setup.font_name, setup.font_size)
    files["word/footer_exam.xml"] = footer_xml(setup.font_name, setup.font_size, setup.class_name, setup.editor, width)
    hid, fid = new_rid("rIdExamHdr"), new_rid("rIdExamFtr")
    for rid, typ, target in ((hid, HDR_REL, "header_exam.xml"), (fid, FTR_REL, "footer_exam.xml")):
        rel = etree.SubElement(rels, "{%s}Relationship" % NS["rel"])
        rel.set("Id", rid)
        rel.set("Type", typ)
        rel.set("Target", target)
    for part, ctype in (("/word/header_exam.xml", HDR_CT), ("/word/footer_exam.xml", FTR_CT)):
        ov = etree.SubElement(ct, "{%s}Override" % NS["ct"])
        ov.set("PartName", part)
        ov.set("ContentType", ctype)
    for sect in sects:
        for child in list(sect):
            if child.tag in (W + "headerReference", W + "footerReference", W + "titlePg"):
                sect.remove(child)
        f_ref = etree.Element(W + "footerReference")
        f_ref.set(W + "type", "default")
        f_ref.set("{%s}id" % NS["r"], fid)
        h_ref = etree.Element(W + "headerReference")
        h_ref.set(W + "type", "default")
        h_ref.set("{%s}id" % NS["r"], hid)
        sect.insert(0, f_ref)
        sect.insert(0, h_ref)  # οι αναφορές μπαίνουν πρώτες στο sectPr (σειρά σχήματος OOXML)
    files["word/document.xml"] = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)
    files["word/_rels/document.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    files["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)
    if "word/settings.xml" in files:  # χωρίς διαφορετική κεφαλίδα μονών/ζυγών σελίδων
        st = etree.fromstring(files["word/settings.xml"])
        for el in st.findall(W + "evenAndOddHeaders"):
            st.remove(el)
        files["word/settings.xml"] = etree.tostring(st, xml_declaration=True, encoding="UTF-8", standalone=True)


def postprocess(docx_path: Path, figures: list[Figure], setup: PageSetup | None = None) -> None:
    setup = setup or PageSetup()
    with zipfile.ZipFile(docx_path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    style_ids = _style_ids(files["word/styles.xml"])
    files["word/document.xml"] = _fix_document(files["word/document.xml"], style_ids)
    _embed_svgs(files, figures)
    files["word/styles.xml"] = _apply_fonts(files["word/styles.xml"], setup.font_name, setup.font_size)
    files["word/document.xml"] = _apply_run_fonts(files["word/document.xml"], setup.font_name, setup.font_size)
    _install_header_footer(files, setup)
    files["word/document.xml"] = _normalize_order(files["word/document.xml"])
    tmp = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        # το [Content_Types].xml πρώτο, όπως το θέλει το Office
        order = ["[Content_Types].xml"] + [n for n in files if n != "[Content_Types].xml"]
        for n in order:
            z.writestr(n, files[n])
    shutil.move(tmp, docx_path)


def _pandoc_json(src: Path, fmt: str) -> dict | None:
    import json

    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    res = subprocess.run(
        [pandoc_path(), str(src), "-f", fmt, "-t", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs,
    )
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def _count_math_nodes(node) -> int:
    if isinstance(node, dict):
        return (1 if node.get("t") == "Math" else 0) + sum(_count_math_nodes(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_math_nodes(v) for v in node)
    return 0


def math_count(src: Path, fmt: str) -> int:
    """Πλήθος εξισώσεων όπως τις βλέπει το pandoc (−1 αν αποτύχει η ανάγνωση)."""
    ast = _pandoc_json(src, fmt)
    return -1 if ast is None else _count_math_nodes(ast.get("blocks", []))


def default_template() -> Path:
    return resource("resources", "default_template.docx")


def build_docx(exam: dict, out_path: Path, template: str = "", sublevel_style: str = "roman",
               points_align: str = "right", setup: PageSetup | None = None) -> dict:
    """Παράγει το .docx. Επιστρέφει {"path", "warnings", "math": (αναμενόμενες, βρέθηκαν)}."""
    tpl = Path(template) if template else default_template()
    if not tpl.exists():
        raise BuildError(f"Δεν βρέθηκε το πρότυπο: {tpl}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        ctx = BuildContext(media_dir=tdp)
        md = exam_markdown(exam, ctx, sublevel_style, points_align)
        md_path = tdp / "exam.md"
        md_path.write_text(md, encoding="utf-8")
        # αντίγραφο του προτύπου: αν είναι ανοιχτό στο Word, το pandoc μπορεί να μην το διαβάσει
        tpl_copy = tdp / "template.docx"
        shutil.copyfile(tpl, tpl_copy)
        tmp_out = tdp / "out.docx"
        expected = math_count(md_path, _MD_FORMAT)
        _run_pandoc(md_path, tmp_out, tpl_copy, tdp)
        if setup is None:
            setup = PageSetup(class_name=exam.get("class_name", ""), editor=exam.get("editor", ""))
        postprocess(tmp_out, ctx.figures, setup)
        try:
            shutil.copyfile(tmp_out, out_path)
        except PermissionError as exc:
            raise BuildError(
                f"Δεν ήταν δυνατή η αποθήκευση στο {out_path}. Μήπως το αρχείο είναι ανοιχτό στο Word;"
            ) from exc
    found = math_count(out_path, "docx")
    if expected >= 0 and found >= 0 and found != expected:
        ctx.warnings.append(
            f"Έλεγχος εξισώσεων: γράφτηκαν {expected}, διαβάστηκαν πίσω από το Word {found}. "
            "Κάποια εξίσωση ίσως δεν μετατράπηκε — ελέγξτε το αρχείο."
        )
    return {"path": str(out_path), "warnings": ctx.warnings, "math": [expected, found], "markdown": md}
