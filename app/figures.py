"""Σχεδίαση σχημάτων από τη δομημένη περιγραφή (spec) σε SVG + PNG, με αριθμητικούς ελέγχους."""

from __future__ import annotations

import io
import json
import math
import re
import threading
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Arc, Circle, FancyArrowPatch, Polygon  # noqa: E402

CM = 1 / 2.54
_SUB = str.maketrans("0123456789+-", "₀₁₂₃₄₅₆₇₈₉₊₋")

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "DejaVu Serif"],
        "font.size": 11,
        "svg.fonttype": "none",  # κείμενο ως κείμενο → επεξεργάσιμο στο Word
        "axes.unicode_minus": True,
    }
)
warnings.filterwarnings("ignore", message="findfont")
import logging  # noqa: E402

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)


class FigureError(ValueError):
    pass


def parse_spec(spec_json: str) -> dict:
    try:
        spec = json.loads(spec_json)
    except ValueError as exc:
        raise FigureError(f"Η περιγραφή του σχήματος δεν είναι έγκυρο JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise FigureError("Η περιγραφή του σχήματος πρέπει να είναι αντικείμενο JSON.")
    return spec


def _label(text) -> str:
    """ε_1 → ε₁, x' → x′ (δείκτες και τόνοι σε Unicode ώστε να μένουν επεξεργάσιμο κείμενο)."""
    s = str(text or "")
    if "_" in s:
        base, _, sub = s.partition("_")
        s = base + sub.strip("{}").translate(_SUB)
    return s.replace("'", "′")


_VEC = re.compile(r"\\+(?:vec|overrightarrow)\s*(?:\{([^{}]*)\}|(\S))")


def _vec_parts(text) -> tuple[str, list[tuple[int, int]]]:
    """«\\vec{AB} + \\vec{ΒΓ}» → («AB + ΒΓ», [(0, 2), (5, 7)]): το απλό κείμενο και ποια κομμάτια
    του θέλουν βέλος από πάνω. Το βέλος σχεδιάζεται χωριστά, ώστε το κείμενο να μένει κείμενο
    στη γραμματοσειρά των ρυθμίσεων (και επεξεργάσιμο στο Word)."""
    raw = str(text or "")
    out, spans, pos = "", [], 0
    for m in _VEC.finditer(raw):
        out += _label(raw[pos:m.start()])
        inner = _label(m.group(1) if m.group(1) is not None else m.group(2))
        spans.append((len(out), len(out) + len(inner)))
        out += inner
        pos = m.end()
    out += _label(raw[pos:])
    return out, spans


def _pt(v) -> tuple[float, float]:
    return float(v[0]), float(v[1])


# --------------------------------------------------------------------------- γεωμετρία

def _line_coeffs(el: dict) -> tuple[float, float, float] | None:
    """Ευθεία ως a·x + b·y = c."""
    if "through" in el:
        (x1, y1), (x2, y2) = _pt(el["through"][0]), _pt(el["through"][1])
        a, b = y2 - y1, x1 - x2
        return a, b, a * x1 + b * y1
    if "vertical" in el:
        return 1.0, 0.0, float(el["vertical"])
    if "slope" in el:
        m, k = float(el["slope"]), float(el.get("intercept", 0))
        return -m, 1.0, k
    return None


def _clip_line(a, b, c, box) -> list[tuple[float, float]]:
    xmin, xmax, ymin, ymax = box
    pts = []
    if abs(b) > 1e-12:
        for x in (xmin, xmax):
            y = (c - a * x) / b
            if ymin - 1e-9 <= y <= ymax + 1e-9:
                pts.append((x, y))
    if abs(a) > 1e-12:
        for y in (ymin, ymax):
            x = (c - b * y) / a
            if xmin - 1e-9 <= x <= xmax + 1e-9:
                pts.append((x, y))
    uniq = []
    for p in pts:
        if all(math.dist(p, q) > 1e-9 for q in uniq):
            uniq.append(p)
    if len(uniq) < 2:
        return []
    # τα δύο πιο απομακρυσμένα
    best = max(((p, q) for i, p in enumerate(uniq) for q in uniq[i + 1 :]), key=lambda pq: math.dist(*pq))
    return list(best)


def run_checks(spec: dict) -> list[str]:
    """Αριθμητικοί έλεγχοι γεωμετρίας. Επιστρέφει προειδοποιήσεις στα ελληνικά."""
    els = spec.get("elements") or []
    points = _named_points(spec)
    lines = {}
    for e in els:
        if e.get("type") == "line" and e.get("name"):
            if "through" in e:
                try:
                    e = {**e, "through": [points[q] if isinstance(q, str) else q for q in e["through"]]}
                except KeyError:
                    pass
            lines[e.get("name")] = e
    ax = spec.get("axes") or {}
    span = max(
        (ax.get("x", [0, 10])[1] - ax.get("x", [0, 10])[0]) if ax else 10,
        (ax.get("y", [0, 10])[1] - ax.get("y", [0, 10])[0]) if ax else 10,
    )
    tol = 0.02 * span
    out: list[str] = []

    def resolve_point(p):
        if isinstance(p, str):
            return points.get(p), p
        try:
            q = _pt(p)
            return q, f"({q[0]:g}, {q[1]:g})"
        except (TypeError, ValueError, IndexError):
            return None, str(p)

    for chk in spec.get("checks") or []:
        kind = chk.get("type")
        try:
            if kind == "intersection":
                n1, n2 = chk["lines"][:2]
                l1, l2 = _line_coeffs(lines.get(n1, {})), _line_coeffs(lines.get(n2, {}))
                p, pname = resolve_point(chk.get("point"))
                if not l1 or not l2 or p is None:
                    out.append(f"Έλεγχος σχήματος: δεν βρέθηκαν τα στοιχεία {n1}, {n2}, {pname}.")
                    continue
                (a1, b1, c1), (a2, b2, c2) = l1, l2
                det = a1 * b2 - a2 * b1
                if abs(det) < 1e-12:
                    out.append(f"Σχήμα: οι ευθείες {n1} και {n2} είναι παράλληλες, δεν τέμνονται.")
                    continue
                x = (c1 * b2 - c2 * b1) / det
                y = (a1 * c2 - a2 * c1) / det
                if math.dist((x, y), p) > tol:
                    out.append(
                        f"Σχήμα: οι ευθείες {n1}, {n2} τέμνονται στο ({x:.2f}, {y:.2f}), "
                        f"όχι στο {pname} = ({p[0]:g}, {p[1]:g}). Ελέγξτε το σχήμα."
                    )
            elif kind == "on_line":
                ln = _line_coeffs(lines.get(chk.get("line"), {}))
                p, pname = resolve_point(chk.get("point"))
                if not ln or p is None:
                    out.append(f"Έλεγχος σχήματος: δεν βρέθηκαν τα στοιχεία {chk.get('line')}, {pname}.")
                    continue
                a, b, c = ln
                d = abs(a * p[0] + b * p[1] - c) / max(1e-12, math.hypot(a, b))
                if d > tol:
                    out.append(f"Σχήμα: το σημείο {pname} δεν ανήκει στην ευθεία {chk.get('line')} (απόσταση {d:.2f}).")
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            out.append(f"Έλεγχος σχήματος αγνοήθηκε ({kind}): {exc}")
    return out


# --------------------------------------------------------------------------- σχεδίαση

_SAFE = {
    k: getattr(np, k)
    for k in ("sin", "cos", "tan", "sqrt", "exp", "log", "log10", "log2", "abs", "pi", "e", "arcsin", "arccos", "arctan", "floor", "ceil", "sign")
}
_SAFE.update({"ln": np.log, "ημ": np.sin, "συν": np.cos, "εφ": np.tan})

_OFFS = {
    "n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0),
    "ne": (0.8, 0.8), "nw": (-0.8, 0.8), "se": (0.8, -0.8), "sw": (-0.8, -0.8),
}


# Χρώματα για εκτύπωση: λίγο σκουρότερα από τα «καθαρά», ώστε να διαβάζονται και σε ασπρόμαυρη φωτοτυπία.
COLORS = {
    "black": "#000000", "gray": "#737373", "grey": "#737373",
    "red": "#C62828", "blue": "#1565C0", "green": "#2E7D32", "orange": "#E65100",
    "purple": "#6A1B9A", "magenta": "#AD1457", "brown": "#6D4C41", "cyan": "#00838F", "yellow": "#B8860B",
    "κόκκινο": "#C62828", "μπλε": "#1565C0", "πράσινο": "#2E7D32", "πορτοκαλί": "#E65100", "μωβ": "#6A1B9A",
    "καφέ": "#6D4C41", "γκρι": "#737373", "μαύρο": "#000000",
}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
COLOR_MODE = "original"  # "original": όπως το πρωτότυπο · "bw": ασπρόμαυρα (το χρώμα γίνεται έντονη μαύρη γραμμή)


def _color(name) -> tuple[str, bool]:
    """(χρώμα για το matplotlib, αν ήταν «πραγματικό» χρώμα — όχι μαύρο/γκρι)."""
    raw = str(name or "black").strip().lower()
    c = COLORS.get(raw) or (raw if _HEX.match(raw) else "#000000")
    colored = c not in ("#000000", "#737373")
    if colored and COLOR_MODE == "bw":
        return "#000000", True
    return c, colored


def _style(el: dict) -> dict:
    ls = {"solid": "-", "dashed": (0, (4, 3)), "dotted": (0, (1, 2))}.get(el.get("style", "solid"), "-")
    color, colored = _color(el.get("color"))
    width = float(el.get("width", 1.0 if el.get("style", "solid") == "solid" else 0.8))
    # έμφαση: το στοιχείο που ξεχωρίζει στο πρωτότυπο (πιο παχύ, ή έγχρωμο σε ασπρόμαυρη εκτύπωση)
    if el.get("emphasis") or (colored and COLOR_MODE == "bw"):
        width *= 1.8
    return {"linestyle": ls, "color": color, "linewidth": width}


def _label_color(el: dict) -> str:
    """Οι ετικέτες μένουν μαύρες, εκτός αν το πρωτότυπο τις έχει χρωματιστές (`label_color`)."""
    return _color(el.get("label_color"))[0] if el.get("label_color") else "#000000"


def _named_points(spec: dict) -> dict:
    """Σημεία με όνομα (ή ετικέτα), ώστε τα άλλα στοιχεία να γράφουν "from": "A" αντί για συντεταγμένες."""
    out = {}
    for el in spec.get("elements") or []:
        if el.get("type") == "point" and "at" in el:
            try:
                xy = _pt(el["at"])
            except (TypeError, ValueError, IndexError):
                continue
            for key in ("label", "name"):
                if el.get(key) and isinstance(el[key], str):
                    out.setdefault(el[key], xy)
    return out


def _resolver(spec: dict):
    names = _named_points(spec)

    def P(v) -> tuple[float, float]:
        if isinstance(v, str):
            if v not in names:
                raise FigureError(f"Δεν υπάρχει σημείο με όνομα «{v}» στο σχήμα.")
            return names[v]
        return _pt(v)

    return P


def _bounds(spec: dict) -> tuple[float, float, float, float]:
    ax = spec.get("axes")
    if ax:
        (x0, x1), (y0, y1) = ax.get("x", [-5, 5]), ax.get("y", [-5, 5])
        return float(x0), float(x1), float(y0), float(y1)
    P = _resolver(spec)
    xs, ys = [], []
    for el in spec.get("elements") or []:
        for key in ("at", "from", "to", "center", "vertex", "label_at"):
            if key in el and el[key] not in (None, ""):
                x, y = P(el[key])
                xs.append(x)
                ys.append(y)
                if key == "center":
                    r = float(el.get("radius", 0))
                    xs += [x - r, x + r]
                    ys += [y - r, y + r]
        for key in ("points", "through"):
            for p in el.get(key) or []:
                x, y = P(p)
                xs.append(x)
                ys.append(y)
    if not xs:
        return -5, 5, -5, 5
    pad = 0.12 * max(max(xs) - min(xs), max(ys) - min(ys), 1)
    return min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad


_LOCK = threading.Lock()  # το pyplot δεν είναι ασφαλές για ταυτόχρονη χρήση από πολλά νήματα
FS = 11.0  # μέγεθος ετικετών (pt)· υπολογίζεται σε κάθε σχεδίαση (label_size_pt)
FS_SMALL = 9.0  # αριθμοί αξόνων, ετικέτες γωνιών
CAP = 0.68  # ύψος κεφαλαίου γράμματος ως κλάσμα του μεγέθους της γραμματοσειράς (Cambria/Times)
FS_MIN, FS_MAX = 6.0, 20.0


def _content_frac(spec: dict, ymin: float, ymax: float) -> float:
    """Τι μέρος του ύψους της εικόνας πιάνει το ίδιο το σχέδιο (χωρίς τα περιθώρια γύρω του)."""
    if spec.get("axes"):
        return 0.92
    span = ymax - ymin
    xmin, xmax, _, _ = _bounds(spec)
    pad = 0.12 * max(xmax - xmin, span, 1) / 1.24  # το περιθώριο που πρόσθεσε το _bounds (βλ. εκεί)
    return 0.92 * max(0.3, (span - 2 * pad) / span)


def _box(spec: dict) -> tuple[float, float, float, float, float, float, bool]:
    """Όρια, πλάτος/ύψος σε cm και αν η κλίμακα είναι ίση στους δύο άξονες."""
    xmin, xmax, ymin, ymax = _bounds(spec)
    if xmax <= xmin or ymax <= ymin:
        raise FigureError("Μη έγκυρο εύρος αξόνων στο σχήμα.")
    width_cm = float(spec.get("width_cm", 7))
    width_cm = min(max(width_cm, 3), 16)
    ratio = (ymax - ymin) / (xmax - xmin)
    equal = 0.45 <= ratio <= 1.7 and spec.get("equal", True)
    height_cm = width_cm * min(max(ratio, 0.45), 1.7)
    return xmin, xmax, ymin, ymax, width_cm, height_cm, equal


def label_size_pt(spec: dict, width_cm: float, height_cm: float, frac: float = 1.0) -> float:
    """Μέγεθος ετικετών (pt), ανεξάρτητο από τις ρυθμίσεις — όσο στο πρωτότυπο:

    - `label_pt`: ρητό μέγεθος σε pt (το βάζουν τα κουμπιά Α− / Α+ της εφαρμογής)·
    - `label_size`: το ύψος ενός κεφαλαίου γράμματος-ετικέτας στο πρωτότυπο ως κλάσμα του ύψους του
      σχεδίου (το εκτιμά ο Claude, π.χ. 0.05)· μετατρέπεται σε pt για το ύψος που θα έχει το σχέδιο
      εδώ (`height_cm` × `frac`, όπου `frac` το μέρος της εικόνας χωρίς τα περιθώρια)·
    - αλλιώς: αυτόματο, ανάλογα με το μέγεθος του σχήματος (≈10,5 pt για πλάτος 7 cm)."""
    try:
        if spec.get("label_pt") not in (None, ""):
            return round(min(max(float(spec["label_pt"]), FS_MIN), FS_MAX), 1)
        r = spec.get("label_size")
        if r not in (None, "") and 0 < float(r) < 0.5:
            pt = float(r) * height_cm * frac * 72 / 2.54 / CAP
            return round(min(max(pt, 7.0), 16.0), 1)
    except (TypeError, ValueError):
        pass
    return round(min(max(10.5 * math.sqrt(width_cm / 7), 8.0), 12.0), 1)


def render(spec: dict, font: str | None = None, size: float | None = None,
           colors: str = "original") -> tuple[bytes, bytes, float]:
    """Επιστρέφει (svg, png, πλάτος σε cm).

    `font`: γραμματοσειρά των ετικετών (η «γραμματοσειρά μαθηματικών» των ρυθμίσεων). Στο SVG το κείμενο
    μένει κείμενο με αυτή τη γραμματοσειρά, οπότε το Word το δείχνει σωστά ακόμα κι αν η Python δεν τη
    βρει (π.χ. η Cambria Math είναι μέσα στο cambria.ttc). Το **μέγεθος** των ετικετών δεν έρχεται από
    τις ρυθμίσεις (από την 1.7.5) αλλά από το ίδιο το σχήμα — βλ. `label_size_pt`· το `size` αγνοείται."""
    global FS, FS_SMALL, COLOR_MODE
    serif = ([font] if font else []) + ["Cambria Math", "Cambria", "Times New Roman", "DejaVu Serif"]
    with _LOCK:
        COLOR_MODE = "bw" if colors == "bw" else "original"
        box = _box(spec)
        FS = label_size_pt(spec, box[4], box[5], _content_frac(spec, box[2], box[3]))
        FS_SMALL = round(max(FS_MIN, FS * 0.82), 1)
        with plt.rc_context({"font.serif": serif, "font.size": FS}):
            return _render(spec, box)


def _arrow_style(lw: float, scale: float = 1.0) -> str:
    hl = 0.42 * scale * max(1.0, lw) ** 0.5
    return f"-|>,head_length={hl:.2f},head_width={hl * 0.42:.2f}"


def _render(spec: dict, box6=None) -> tuple[bytes, bytes, float]:
    xmin, xmax, ymin, ymax, width_cm, height_cm, equal = box6 or _box(spec)
    P = _resolver(spec)

    fig = plt.figure(figsize=(width_cm * CM, height_cm * CM))
    ax = fig.add_axes([0.04, 0.04, 0.92, 0.92])
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    if equal:
        ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    unit = max(xmax - xmin, ymax - ymin) / 30  # μικρή απόσταση για ετικέτες
    # πόσες μονάδες του σχήματος είναι 1 pt στο χαρτί (για αποστάσεις ανάλογες με τα γράμματα)
    w_pt, h_pt = width_cm * CM * 72 * 0.92, height_cm * CM * 72 * 0.92
    pt_data = max((xmax - xmin) / w_pt, (ymax - ymin) / h_pt) if equal else (xmax - xmin) / w_pt
    lunit = unit * max(0.9, FS / 11)  # απόσταση ετικέτας από το στοιχείο (μεγαλώνει με τα γράμματα)
    box = (xmin, xmax, ymin, ymax)
    dots = [P(e["at"]) for e in spec.get("elements") or []
            if e.get("type") == "point" and "at" in e and e.get("show", True)]
    vec_labels: list = []  # (Text, spans) — τα βελάκια πάνω από τα γράμματα μπαίνουν στο τέλος

    def text(x, y, label, fontsize=None, **kw):
        plain, spans = _vec_parts(label)
        t = ax.text(x, y, plain, fontsize=fontsize or FS, **kw)
        if spans:
            vec_labels.append((t, spans))
        return t

    axes = spec.get("axes")
    if axes:
        if axes.get("grid"):
            for gx in range(math.ceil(xmin), math.floor(xmax) + 1):
                ax.plot([gx, gx], [ymin, ymax], color="0.85", linewidth=0.5, zorder=0)
            for gy in range(math.ceil(ymin), math.floor(ymax) + 1):
                ax.plot([xmin, xmax], [gy, gy], color="0.85", linewidth=0.5, zorder=0)
        arrow = dict(arrowstyle="-|>,head_length=0.5,head_width=0.22", color="black", linewidth=0.9, shrinkA=0, shrinkB=0)
        if ymin <= 0 <= ymax:
            ax.annotate("", xy=(xmax, 0), xytext=(xmin, 0), arrowprops=arrow, zorder=2)
        if xmin <= 0 <= xmax:
            ax.annotate("", xy=(0, ymax), xytext=(0, ymin), arrowprops=arrow, zorder=2)
        if axes.get("labels", True):
            ax.text(xmax, -1.3 * lunit, "x", ha="right", va="top", fontsize=FS)
            ax.text(xmin, 0.9 * lunit, "x′", ha="left", va="bottom", fontsize=FS)
            ax.text(0.9 * lunit, ymax, "y", ha="left", va="top", fontsize=FS)
            ax.text(0.9 * lunit, ymin, "y′", ha="left", va="bottom", fontsize=FS)
        origin = axes.get("origin", "O")
        if origin:
            ax.text(-0.7 * lunit, -0.7 * lunit, origin, ha="right", va="top", fontsize=FS)
        tk = 0.45 * unit
        for t in axes.get("ticks_x") or []:
            t = float(t)
            ax.plot([t, t], [-tk, tk], color="black", linewidth=0.8)
            ax.text(t, -1.2 * lunit, f"{t:g}".replace("-", "−"), ha="center", va="top", fontsize=FS_SMALL, zorder=4)
        for t in axes.get("ticks_y") or []:
            t = float(t)
            ax.plot([-tk, tk], [t, t], color="black", linewidth=0.8)
            side = 1 if axes.get("ticks_y_side") == "right" else -1
            ax.text(side * 1.0 * lunit, t, f"{t:g}".replace("-", "−"), ha="left" if side > 0 else "right",
                    va="center", fontsize=FS_SMALL, zorder=4)

    def side_label(x1, y1, x2, y2, el, default_side="left"):
        """Θέση ετικέτας δίπλα στο μέσο ενός τμήματος/διανύσματος (αριστερά ή δεξιά της φοράς του)."""
        if el.get("label_at"):
            return P(el["label_at"])
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1.0
        sgn = 1 if el.get("label_side", default_side) == "left" else -1
        # μισό ύψος γραμμάτων + βελάκι από πάνω + κενό, ώστε η ετικέτα να μην πέφτει στη γραμμή
        off = max(1.5 * lunit, 1.05 * FS * pt_data)
        return (x1 + x2) / 2 - sgn * dy / n * off, (y1 + y2) / 2 + sgn * dx / n * off

    def marks(x1, y1, x2, y2, count):
        """Γραμμούλες ισότητας τμημάτων στο μέσο (1–3)."""
        count = max(0, min(int(count or 0), 3))
        if not count:
            return
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1.0
        ux, uy, px, py = dx / n, dy / n, -dy / n, dx / n
        h, gap = 0.55 * unit, 0.35 * unit
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        for k in range(count):
            o = (k - (count - 1) / 2) * gap
            cx, cy = mx + ux * o, my + uy * o
            ax.plot([cx - px * h, cx + px * h], [cy - py * h, cy + py * h], color="black", linewidth=0.8, zorder=4)

    for el in spec.get("elements") or []:
        kind = el.get("type")
        st = _style(el)
        try:
            if kind == "line":
                co = _line_coeffs({**el, "through": [P(q) for q in el["through"]]} if "through" in el else el)
                if co:
                    seg = _clip_line(*co, box)
                    if seg:
                        ax.plot([seg[0][0], seg[1][0]], [seg[0][1], seg[1][1]], zorder=3, **st)
                        if el.get("label"):
                            lx, ly = P(el["label_at"]) if el.get("label_at") else seg[1]
                            text(lx, ly, el["label"], ha="center", va="center", color=_label_color(el))
            elif kind == "segment":
                (x1, y1), (x2, y2) = P(el["from"]), P(el["to"])
                ax.plot([x1, x2], [y1, y2], zorder=3, **st)
                marks(x1, y1, x2, y2, el.get("marks"))
                if el.get("label"):
                    lx, ly = P(el["label_at"]) if el.get("label_at") else ((x1 + x2) / 2, (y1 + y2) / 2)
                    text(lx, ly, el["label"], ha="center", va="center", color=_label_color(el))
            elif kind == "vector":
                (x1, y1), (x2, y2) = P(el["from"]), P(el["to"])
                head = el.get("head", "end")
                lw = st["linewidth"] * (1.15 if el.get("style", "solid") == "solid" else 1.0)
                # η αιχμή σταματά στην άκρη της τελείας ενός σημείου, όχι κάτω από αυτήν
                shrink = {q: (1.9 if any(math.dist(q, d) < 1e-9 for d in dots) else 0) for q in ((x1, y1), (x2, y2))}
                common = dict(color=st["color"], linewidth=lw, zorder=4, shrinkA=shrink[(x1, y1)], shrinkB=shrink[(x2, y2)],
                              mutation_scale=10 * float(el.get("head_size", 1)))
                if head == "mid":
                    ax.plot([x1, x2], [y1, y2], zorder=3, **{**st, "linewidth": lw})
                    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                    ex, ey = (x2 - x1) * 0.04, (y2 - y1) * 0.04
                    ax.add_patch(FancyArrowPatch((mx - ex, my - ey), (mx + ex, my + ey), arrowstyle=_arrow_style(lw),
                                                 **{**common, "shrinkA": 0, "shrinkB": 0}))
                else:
                    style = {"end": "-|>", "start": "<|-", "both": "<|-|>", "none": "-"}.get(head, "-|>")
                    hl = 0.42 * max(1.0, lw) ** 0.5
                    astyle = style + (f",head_length={hl:.2f},head_width={hl * 0.42:.2f}" if style != "-" else "")
                    if el.get("style", "solid") == "solid" or style == "-":
                        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=astyle, linestyle=st["linestyle"],
                                                     joinstyle="miter", capstyle="butt", **common))
                    else:  # διακεκομμένο: η γραμμή διακεκομμένη, οι αιχμές συμπαγείς
                        ax.plot([x1, x2], [y1, y2], zorder=3, **{**st, "linewidth": lw})
                        one = f"-|>,head_length={hl:.2f},head_width={hl * 0.42:.2f}"
                        dx, dy = (x2 - x1) * 0.01, (y2 - y1) * 0.01
                        if style in ("-|>", "<|-|>"):
                            ax.add_patch(FancyArrowPatch((x2 - dx, y2 - dy), (x2, y2), arrowstyle=one,
                                                         **{**common, "shrinkA": 0}))
                        if style in ("<|-", "<|-|>"):
                            ax.add_patch(FancyArrowPatch((x1 + dx, y1 + dy), (x1, y1), arrowstyle=one,
                                                         **{**common, "shrinkA": 0, "shrinkB": common["shrinkA"]}))
                marks(x1, y1, x2, y2, el.get("marks"))
                if el.get("label"):
                    lab = str(el["label"])
                    if el.get("label_arrow", True) and "\\" not in lab:
                        lab = "\\vec{" + lab + "}"
                    lx, ly = side_label(x1, y1, x2, y2, el)
                    text(lx, ly, lab, ha="center", va="center", zorder=6, color=_label_color(el))
            elif kind == "polyline":
                pts = np.array([P(q) for q in el["points"]])
                ax.plot(pts[:, 0], pts[:, 1], zorder=3, **st)
            elif kind == "polygon":
                pts = [P(q) for q in el["points"]]
                fill = el.get("fill")
                face = "0.9"
                if isinstance(fill, str) and fill.lower() not in ("true", "false"):
                    fc, fcol = _color(fill)
                    face = (fc, 0.18) if fcol and COLOR_MODE != "bw" else "0.9"
                ax.add_patch(
                    Polygon(pts, closed=True, fill=bool(fill) and str(fill).lower() != "false", facecolor=face,
                            edgecolor=st["color"], linewidth=st["linewidth"], linestyle=st["linestyle"], zorder=3)
                )
            elif kind == "circle":
                ax.add_patch(
                    Circle(P(el["center"]), float(el["radius"]), fill=False, edgecolor=st["color"],
                           linewidth=st["linewidth"], linestyle=st["linestyle"], zorder=3)
                )
            elif kind == "function":
                a, b = el.get("domain") or [xmin, xmax]
                xs = np.linspace(float(a), float(b), 800)
                with np.errstate(all="ignore"):
                    ys = eval(str(el["expr"]).replace("^", "**"), {"__builtins__": {}}, {**_SAFE, "x": xs})  # noqa: S307
                ys = np.asarray(ys, dtype=float) * np.ones_like(xs)
                ys[(ys < ymin - (ymax - ymin)) | (ys > ymax + (ymax - ymin))] = np.nan
                jumps = np.abs(np.diff(ys)) > (ymax - ymin) / 2
                ys[1:][jumps] = np.nan
                ax.plot(xs, ys, zorder=3, **st)
                if el.get("label"):
                    lx, ly = P(el["label_at"]) if el.get("label_at") else (xs[-1], ys[-1])
                    text(lx, ly, el["label"], ha="left", va="bottom", color=_label_color(el))
            elif kind == "angle":
                vx, vy = P(el["vertex"])
                fx, fy = P(el["from"])
                tx, ty = P(el["to"])
                a1 = math.degrees(math.atan2(fy - vy, fx - vx))
                a2 = math.degrees(math.atan2(ty - vy, tx - vx))
                sweep = (a2 - a1) % 360
                t1, t2 = (a1, a2) if sweep <= 180 else (a2, a1)
                r = float(el.get("radius", 3 * unit))
                if el.get("right"):  # ορθή γωνία: τετραγωνάκι
                    u1 = ((fx - vx), (fy - vy))
                    u2 = ((tx - vx), (ty - vy))
                    n1, n2 = math.hypot(*u1) or 1, math.hypot(*u2) or 1
                    q = 0.75 * r
                    a = (vx + u1[0] / n1 * q, vy + u1[1] / n1 * q)
                    c = (vx + u2[0] / n2 * q, vy + u2[1] / n2 * q)
                    b = (a[0] + c[0] - vx, a[1] + c[1] - vy)
                    ax.plot([a[0], b[0], c[0]], [a[1], b[1], c[1]], color=st["color"], linewidth=0.9, zorder=3)
                else:
                    ax.add_patch(Arc((vx, vy), 2 * r, 2 * r, theta1=t1, theta2=t2, color=st["color"], linewidth=0.9, zorder=3))
                if el.get("label"):
                    mid = math.radians(t1 + ((t2 - t1) % 360) / 2)
                    lr = r + 2.2 * lunit
                    text(vx + lr * math.cos(mid), vy + lr * math.sin(mid), el["label"],
                         fontsize=FS_SMALL, ha="center", va="center")
            elif kind == "point":
                x, y = P(el["at"])
                filled = el.get("filled", True)
                if el.get("show", True):
                    pc = _color(el.get("color"))[0]
                    ax.plot([x], [y], marker="o", markersize=3.6, color=pc,
                            markerfacecolor=pc if filled else "white", zorder=5)
                if el.get("label"):
                    if el.get("label_at"):
                        lx, ly = P(el["label_at"])
                    else:  # απόσταση ανάλογη με το μέγεθος των γραμμάτων, ώστε να μην πέφτει πάνω στις γραμμές
                        dx, dy = _OFFS.get(el.get("label_pos", "ne"), (0.8, 0.8))
                        off = max(1.6 * lunit, 0.95 * FS * pt_data)
                        lx, ly = x + off * dx, y + off * dy
                    text(lx, ly, el["label"], ha="center", va="center", zorder=6,
                         color=_label_color(el))
            elif kind == "text":
                x, y = P(el["at"])
                if el.get("scale"):  # σε σχέση με τις άλλες ετικέτες (0.8 = μικρότερο)
                    fs = FS * min(max(float(el["scale"]), 0.4), 2.5)
                else:  # παλιό πεδίο: pt
                    fs = float(el.get("size", FS))
                text(x, y, el.get("text", ""), fontsize=fs, ha="center", va="center",
                     color=_color(el["color"])[0] if el.get("color") else "#000000")
        except FigureError:
            plt.close(fig)
            raise
        except (KeyError, TypeError, ValueError, IndexError, SyntaxError, NameError) as exc:
            plt.close(fig)
            raise FigureError(f"Πρόβλημα στο στοιχείο «{kind}» του σχήματος: {exc}") from exc

    if vec_labels:
        _draw_vec_arrows(fig, ax, vec_labels)

    svg_buf, png_buf = io.BytesIO(), io.BytesIO()
    fig.savefig(svg_buf, format="svg", transparent=True)
    fig.savefig(png_buf, format="png", dpi=300, facecolor="white")
    plt.close(fig)
    real_w = width_cm
    return svg_buf.getvalue(), png_buf.getvalue(), real_w


def _ink_top(text: str, prop) -> float:
    """Πόσο ψηλά φτάνει το «μελάνι» των γραμμάτων πάνω από τη γραμμή βάσης (σε pt) — π.χ. το δ, β, λ, ζ
    είναι ψηλότερα από το α ή το x, και τα κεφαλαία ψηλότερα από τα μικρά."""
    from matplotlib.textpath import TextPath

    try:
        ext = TextPath((0, 0), text, prop=prop).get_extents()
        return float(ext.y1) if ext.height > 0 else 0.0
    except Exception:  # noqa: BLE001 — γραμματοσειρά χωρίς το γλύφο κ.λπ.
        return 0.72 * prop.get_size_in_points()


def _layout_descent(t, renderer) -> float:
    """Απόσταση (px) της γραμμής βάσης από το κάτω άκρο του πλαισίου του κειμένου, όπως τη στοιχίζει
    το matplotlib (ιδιωτική συνάρτηση που άλλαξε μορφή στις εκδόσεις· γι' αυτό οι εναλλακτικές)."""
    try:
        lay = t._get_layout(renderer)
        info = lay[1]
        first = info[0]
        wad = first[1]
        if isinstance(wad, (tuple, list)) and len(wad) == 3:  # matplotlib ≥ 3.10: (πλάτος, ascent, descent)
            return float(wad[2])
        if len(lay) == 3 and isinstance(lay[2], (int, float)):  # παλιότερες: (bbox, info, descent)
            return float(lay[2])
    except Exception:  # noqa: BLE001
        pass
    return float(renderer.get_text_width_height_descent(t.get_text(), t.get_fontproperties(), ismath=False)[2])


def _draw_vec_arrows(fig, ax, vec_labels) -> None:
    """Βελάκι διανύσματος (→) πάνω από τα κομμάτια των ετικετών που το ζητούν (\\vec{AB}).

    Το ύψος του βέλους μετριέται από το πραγματικό σχήμα των γραμμάτων (`_ink_top`), ώστε να μην
    ακουμπά ψηλά γράμματα (δ, β, λ, ζ, ξ, θ, κεφαλαία)· όλα τα βελάκια μιας ετικέτας μπαίνουν στο ίδιο
    ύψος. Υπάρχει και ένα μικρό περιθώριο ασφαλείας, γιατί το Word μπορεί να δείξει το κείμενο με
    λίγο ψηλότερη γραμματοσειρά. Οι θέσεις γράφονται σε συντεταγμένες του σχήματος, ώστε να
    ταιριάζουν και στο SVG και στο PNG."""
    ax.apply_aspect()  # η ίση κλίμακα εφαρμόζεται κανονικά στη σχεδίαση· εδώ τη χρειαζόμαστε πριν μετρήσουμε
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    px_pt = fig.dpi / 72
    for t, spans in vec_labels:
        txt = t.get_text()
        prop = t.get_fontproperties()
        size = prop.get_size_in_points()
        em = size * px_pt  # 1 em σε pixels
        bb = t.get_window_extent(renderer)
        descent = _layout_descent(t, renderer)
        baseline = bb.y0 + descent

        def width(s):
            return renderer.get_text_width_height_descent(s, prop, ismath=False)[0] if s else 0.0

        k = bb.width / (width(txt) or 1.0)
        # ύψος των ίδιων των γραμμάτων (α, x χαμηλά· δ, β, κεφαλαία ψηλά)· όχι κάτω από το ύψος του «x»
        top = max([_ink_top(txt[a:b], prop) for a, b in spans] + [0.48 * size])  # pt
        y = baseline + (top + 0.15 * size + 0.12 * size) * px_pt  # κενό + μισό ύψος της αιχμής
        lw = max(0.6, 0.06 * size)
        for a, b in spans:
            x0, x1 = bb.x0 + width(txt[:a]) * k, bb.x0 + width(txt[:b]) * k
            x0, x1 = x0 + 0.04 * em, x1 + 0.06 * em
            if x1 - x0 < 0.62 * em:  # ένα γράμμα: το βελάκι λίγο φαρδύτερο από το γράμμα
                c = (x0 + x1) / 2
                x0, x1 = c - 0.31 * em, c + 0.31 * em
            (dx0, dy), (dx1, _) = inv.transform([(x0, y), (x1, y)])
            ax.add_patch(FancyArrowPatch((dx0, dy), (dx1, dy), arrowstyle="-|>,head_length=0.26,head_width=0.11",
                                         mutation_scale=size, linewidth=lw,
                                         color=t.get_color(), shrinkA=0, shrinkB=0, zorder=t.get_zorder(),
                                         clip_on=False))


def render_to_files(spec_json: str, out_base: Path, font: str | None = None, size: float | None = None,
                    colors: str = "original") -> dict:
    """Σχεδιάζει και γράφει out_base.svg / out_base.png. Επιστρέφει πληροφορίες και προειδοποιήσεις.
    `colors`: "original" (χρώματα όπως στο πρωτότυπο) ή "bw" (ασπρόμαυρα· τα έγχρωμα γίνονται έντονα)."""
    spec = parse_spec(spec_json)
    svg, png, width = render(spec, font, size, colors)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    svg_p, png_p = out_base.with_suffix(".svg"), out_base.with_suffix(".png")
    svg_p.write_bytes(svg)
    png_p.write_bytes(png)
    box = _box(spec)
    return {"svg": str(svg_p), "png": str(png_p), "width_cm": width, "warnings": run_checks(spec),
            "label_pt": label_size_pt(spec, box[4], box[5], _content_frac(spec, box[2], box[3])),
            "label_mode": "manual" if spec.get("label_pt") not in (None, "")
            else "original" if spec.get("label_size") not in (None, "") else "auto"}


def crop_original(image_bytes: bytes, crop: list[float], out_png: Path, width_cm: float = 6.5) -> dict:
    """Περικοπή του σχήματος από την πρωτότυπη εικόνα (όταν προτιμάται αντί για επανασχεδίαση)."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = crop if len(crop) == 4 else (0, 0, 1, 1)
    box = (int(max(0, min(x0, x1)) * w), int(max(0, min(y0, y1)) * h),
           int(min(1, max(x0, x1)) * w), int(min(1, max(y0, y1)) * h))
    if box[2] - box[0] < 10 or box[3] - box[1] < 10:
        box = (0, 0, w, h)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.crop(box).save(out_png, format="PNG")
    return {"svg": None, "png": str(out_png), "width_cm": width_cm, "warnings": []}
