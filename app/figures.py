"""Σχεδίαση σχημάτων από τη δομημένη περιγραφή (spec) σε SVG + PNG, με αριθμητικούς ελέγχους."""

from __future__ import annotations

import io
import json
import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Arc, Circle, Polygon  # noqa: E402

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
    lines = {e.get("name"): e for e in els if e.get("type") == "line" and e.get("name")}
    points = {e.get("name"): _pt(e["at"]) for e in els if e.get("type") == "point" and e.get("name") and "at" in e}
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


def _style(el: dict) -> dict:
    ls = {"solid": "-", "dashed": (0, (4, 3)), "dotted": (0, (1, 2))}.get(el.get("style", "solid"), "-")
    color = {"black": "black", "gray": "0.45", "grey": "0.45"}.get(el.get("color", "black"), "black")
    width = float(el.get("width", 1.0 if el.get("style", "solid") == "solid" else 0.8))
    return {"linestyle": ls, "color": color, "linewidth": width}


def _bounds(spec: dict) -> tuple[float, float, float, float]:
    ax = spec.get("axes")
    if ax:
        (x0, x1), (y0, y1) = ax.get("x", [-5, 5]), ax.get("y", [-5, 5])
        return float(x0), float(x1), float(y0), float(y1)
    xs, ys = [], []
    for el in spec.get("elements") or []:
        for key in ("at", "from", "to", "center", "vertex"):
            if key in el:
                x, y = _pt(el[key])
                xs.append(x)
                ys.append(y)
                if key == "center":
                    r = float(el.get("radius", 0))
                    xs += [x - r, x + r]
                    ys += [y - r, y + r]
        for key in ("points", "through"):
            for p in el.get(key) or []:
                x, y = _pt(p)
                xs.append(x)
                ys.append(y)
    if not xs:
        return -5, 5, -5, 5
    pad = 0.12 * max(max(xs) - min(xs), max(ys) - min(ys), 1)
    return min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad


def render(spec: dict, font: str | None = None) -> tuple[bytes, bytes, float]:
    """Επιστρέφει (svg, png, πλάτος σε cm). `font`: γραμματοσειρά ετικετών (ίδια με το κείμενο)."""
    serif = ([font] if font else []) + ["Times New Roman", "DejaVu Serif"]
    with plt.rc_context({"font.serif": serif}):
        return _render(spec)


def _render(spec: dict) -> tuple[bytes, bytes, float]:
    xmin, xmax, ymin, ymax = _bounds(spec)
    if xmax <= xmin or ymax <= ymin:
        raise FigureError("Μη έγκυρο εύρος αξόνων στο σχήμα.")
    width_cm = float(spec.get("width_cm", 7))
    width_cm = min(max(width_cm, 3), 16)
    ratio = (ymax - ymin) / (xmax - xmin)
    equal = 0.45 <= ratio <= 1.7 and spec.get("equal", True)
    height_cm = width_cm * min(max(ratio, 0.45), 1.7)

    fig = plt.figure(figsize=(width_cm * CM, height_cm * CM))
    ax = fig.add_axes([0.04, 0.04, 0.92, 0.92])
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    if equal:
        ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    unit = max(xmax - xmin, ymax - ymin) / 30  # μικρή απόσταση για ετικέτες
    box = (xmin, xmax, ymin, ymax)

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
            ax.text(xmax, -1.3 * unit, "x", ha="right", va="top", fontsize=11)
            ax.text(xmin, 0.9 * unit, "x′", ha="left", va="bottom", fontsize=11)
            ax.text(0.9 * unit, ymax, "y", ha="left", va="top", fontsize=11)
            ax.text(0.9 * unit, ymin, "y′", ha="left", va="bottom", fontsize=11)
        origin = axes.get("origin", "O")
        if origin:
            ax.text(-0.7 * unit, -0.7 * unit, origin, ha="right", va="top", fontsize=11)
        tk = 0.45 * unit
        for t in axes.get("ticks_x") or []:
            t = float(t)
            ax.plot([t, t], [-tk, tk], color="black", linewidth=0.8)
            ax.text(t, -1.2 * unit, f"{t:g}".replace("-", "−"), ha="center", va="top", fontsize=9, zorder=4)
        for t in axes.get("ticks_y") or []:
            t = float(t)
            ax.plot([-tk, tk], [t, t], color="black", linewidth=0.8)
            side = 1 if axes.get("ticks_y_side") == "right" else -1
            ax.text(side * 1.0 * unit, t, f"{t:g}".replace("-", "−"), ha="left" if side > 0 else "right",
                    va="center", fontsize=9, zorder=4)

    for el in spec.get("elements") or []:
        kind = el.get("type")
        st = _style(el)
        try:
            if kind == "line":
                co = _line_coeffs(el)
                if co:
                    seg = _clip_line(*co, box)
                    if seg:
                        ax.plot([seg[0][0], seg[1][0]], [seg[0][1], seg[1][1]], zorder=3, **st)
                        if el.get("label"):
                            lx, ly = _pt(el["label_at"]) if el.get("label_at") else seg[1]
                            ax.text(lx, ly, _label(el["label"]), ha="center", va="center", fontsize=12)
            elif kind == "segment":
                (x1, y1), (x2, y2) = _pt(el["from"]), _pt(el["to"])
                ax.plot([x1, x2], [y1, y2], zorder=3, **st)
                if el.get("label"):
                    lx, ly = _pt(el["label_at"]) if el.get("label_at") else ((x1 + x2) / 2, (y1 + y2) / 2)
                    ax.text(lx, ly, _label(el["label"]), ha="center", va="center", fontsize=11)
            elif kind == "polyline":
                pts = np.array([_pt(p) for p in el["points"]])
                ax.plot(pts[:, 0], pts[:, 1], zorder=3, **st)
            elif kind == "polygon":
                pts = [_pt(p) for p in el["points"]]
                ax.add_patch(
                    Polygon(pts, closed=True, fill=bool(el.get("fill")), facecolor="0.9",
                            edgecolor=st["color"], linewidth=st["linewidth"], linestyle=st["linestyle"], zorder=3)
                )
            elif kind == "circle":
                ax.add_patch(
                    Circle(_pt(el["center"]), float(el["radius"]), fill=False, edgecolor=st["color"],
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
                    lx, ly = _pt(el["label_at"]) if el.get("label_at") else (xs[-1], ys[-1])
                    ax.text(lx, ly, _label(el["label"]), ha="left", va="bottom", fontsize=11)
            elif kind == "angle":
                vx, vy = _pt(el["vertex"])
                fx, fy = _pt(el["from"])
                tx, ty = _pt(el["to"])
                a1 = math.degrees(math.atan2(fy - vy, fx - vx))
                a2 = math.degrees(math.atan2(ty - vy, tx - vx))
                sweep = (a2 - a1) % 360
                t1, t2 = (a1, a2) if sweep <= 180 else (a2, a1)
                r = float(el.get("radius", 3 * unit))
                ax.add_patch(Arc((vx, vy), 2 * r, 2 * r, theta1=t1, theta2=t2, color="black", linewidth=0.9, zorder=3))
                if el.get("label"):
                    mid = math.radians(t1 + ((t2 - t1) % 360) / 2)
                    lr = r + 2.2 * unit
                    ax.text(vx + lr * math.cos(mid), vy + lr * math.sin(mid), _label(el["label"]),
                            ha="center", va="center", fontsize=9)
            elif kind == "point":
                x, y = _pt(el["at"])
                filled = el.get("filled", True)
                ax.plot([x], [y], marker="o", markersize=3.6, color="black",
                        markerfacecolor="black" if filled else "white", zorder=5)
                if el.get("label"):
                    dx, dy = _OFFS.get(el.get("label_pos", "ne"), (0.8, 0.8))
                    ax.text(x + 1.6 * unit * dx, y + 1.6 * unit * dy, _label(el["label"]),
                            ha="center", va="center", fontsize=12, zorder=6)
            elif kind == "text":
                x, y = _pt(el["at"])
                ax.text(x, y, _label(el.get("text", "")), ha="center", va="center", fontsize=float(el.get("size", 11)))
        except (KeyError, TypeError, ValueError, IndexError, SyntaxError, NameError) as exc:
            plt.close(fig)
            raise FigureError(f"Πρόβλημα στο στοιχείο «{kind}» του σχήματος: {exc}") from exc

    svg_buf, png_buf = io.BytesIO(), io.BytesIO()
    fig.savefig(svg_buf, format="svg", transparent=True)
    fig.savefig(png_buf, format="png", dpi=300, facecolor="white")
    plt.close(fig)
    real_w = width_cm
    return svg_buf.getvalue(), png_buf.getvalue(), real_w


def render_to_files(spec_json: str, out_base: Path, font: str | None = None) -> dict:
    """Σχεδιάζει και γράφει out_base.svg / out_base.png. Επιστρέφει πληροφορίες και προειδοποιήσεις."""
    spec = parse_spec(spec_json)
    svg, png, width = render(spec, font)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    svg_p, png_p = out_base.with_suffix(".svg"), out_base.with_suffix(".png")
    svg_p.write_bytes(svg)
    png_p.write_bytes(png)
    return {"svg": str(svg_p), "png": str(png_p), "width_cm": width, "warnings": run_checks(spec)}


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
