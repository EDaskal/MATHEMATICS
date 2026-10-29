"""Δεύτερος έλεγχος σχημάτων με πλέγμα, και ανάγνωση σχήματος από την εικόνα κατ' απαίτηση.

Γιατί: στην πρώτη ανάγνωση ο Claude βλέπει όλη τη σελίδα και εκτιμά τις συντεταγμένες «με το μάτι».
Σε σχήματα με διανύσματα αυτό δεν αρκεί: μια λάθος φορά βέλους αλλάζει την απάντηση της άσκησης.
Εδώ του δίνουμε:

1. την περιοχή του σχήματος, μεγεθυμένη, με ένα αριθμημένο τετραγωνισμένο πλέγμα από πάνω, ώστε να
   διαβάζει τις κορυφές σαν σε μιλιμετρέ χαρτί·
2. την πρώτη του επανασχεδίαση, για να τη συγκρίνει με το πρωτότυπο·
3. την εκφώνηση, για να καταλαβαίνει τι δείχνει το σχήμα (π.χ. ποιο διάνυσμα ζητείται).

Και του ζητάμε να γράψει πρώτα, για κάθε διάνυσμα, από πού προς πού πηγαίνει και πού είναι η αιχμή,
και μετά το νέο `spec_json`. Αν κάτι πάει στραβά, κρατιέται η πρώτη ανάγνωση — ο έλεγχος ποτέ δεν
χαλάει ένα σχήμα που ήδη υπάρχει.
"""

from __future__ import annotations

import asyncio
import io
import json

from .providers import ImageInput, Provider, ProviderError

GRID_N = 20  # τετράγωνα στη μεγάλη πλευρά
GRID_LONG = 1400  # px, η μεγάλη πλευρά της μεγεθυμένης περικοπής
PAD = 0.05  # περιθώριο γύρω από την περικοπή του Claude (συχνά είναι λίγο σφιχτή)

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "string"},
        "changes": {"type": "string"},
        "description": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "spec_json": {"type": "string"},
    },
    "required": ["notes", "changes", "description", "confidence", "spec_json"],
    "additionalProperties": False,
}

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "crop_page": {"type": "integer"},
        "crop": {"type": "array", "items": {"type": "number"}},
        "description": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["found", "crop_page", "crop", "description", "why"],
    "additionalProperties": False,
}

_GRID_INTRO = (
    "Η ΠΡΩΤΗ εικόνα είναι το σχήμα της άσκησης από το πρωτότυπο, μεγεθυμένο, με ένα μπλε τετραγωνισμένο "
    "πλέγμα από πάνω. Οι αριθμοί στο κάτω και στο αριστερό άκρο είναι συντεταγμένες: x από αριστερά προς "
    "τα δεξιά, y από κάτω προς τα πάνω· κάθε τετράγωνο = 1 μονάδα. Αγνόησε κείμενο που δεν ανήκει στο σχήμα.\n"
)

_STEPS = """
Φτιάξε το `spec_json` του σχήματος, στη γλώσσα σχεδίασης των κανόνων:
1. Συντεταγμένες: αν το σχήμα ΔΕΝ έχει αριθμημένους άξονες, χρησιμοποίησε τις συντεταγμένες του πλέγματος
   (`"axes": null`). Αν έχει άξονες με αριθμούς, κράτα το σύστημα των αξόνων του σχήματος (`axes`, `ticks`)
   και χρησιμοποίησε το πλέγμα μόνο για να μετρήσεις πού πέφτει κάθε σημείο σε σχέση με τις υποδιαιρέσεις.
2. Βρες κάθε κορυφή και άκρο γραμμής (ακρίβεια 0.25 τετραγώνου). Δώσε σε καθένα ένα `point` με το όνομα
   του πρωτοτύπου (αλλιώς `name` P1, P2, … χωρίς `label`) και `"show": false` αν στο πρωτότυπο δεν έχει τελεία.
   Οι γραμμές και τα διανύσματα να αναφέρονται σε αυτά τα σημεία με το όνομά τους.
3. Για ΚΑΘΕ διάνυσμα κοίτα προσεκτικά ΣΕ ΠΟΙΟ ΑΚΡΟ ΕΙΝΑΙ Η ΑΙΧΜΗ (το γεμάτο τριγωνάκι): εκεί είναι το `to`.
   Στο `notes` γράψε πρώτα μία γραμμή για κάθε γραμμή/διάνυσμα: «όνομα: από Px (x,y) προς Py (x,y),
   αιχμή κοντά στο (x,y), συνεχής/διακεκομμένη, χρώμα/έμφαση». Μετά φτιάξε το spec_json από αυτές τις γραμμές.
4. Ετικέτες: `label_at` στη θέση που έχουν στο πρωτότυπο (σε συντεταγμένες του σχήματος).
5. `label_size` = (ύψος ενός κεφαλαίου γράμματος ετικέτας σε τετράγωνα) / (ύψος του σχεδίου σε τετράγωνα).
6. Χρώματα και έμφαση όπως λένε οι κανόνες: χρώμα του πρωτοτύπου (`color`), ή `"emphasis": true` για ό,τι
   ξεχωρίζει σε ασπρόμαυρο πρωτότυπο — η εκφώνηση σε βοηθά να καταλάβεις ποιο είναι το σημαντικό στοιχείο.
7. `description`: σύντομη περιγραφή του σχήματος στα ελληνικά. `confidence`: `low` αν κάποια φορά ή θέση
   δεν διαβάζεται με βεβαιότητα (γράψε ποια στο `changes`).
"""

CHECK_TASK = (
    "ΕΛΕΓΧΟΣ ΣΧΗΜΑΤΟΣ.\n" + _GRID_INTRO
    + "Η ΔΕΥΤΕΡΗ εικόνα είναι μια πρώτη επανασχεδίαση του ίδιου σχήματος· μπορεί να έχει λάθη (φορά "
    "διανυσμάτων, θέσεις κορυφών, κλίσεις πλευρών, ετικέτες).\n" + _STEPS
    + "8. Σύγκρινε με τη δεύτερη εικόνα και γράψε στο `changes` τι διόρθωσες (ή «καμία αλλαγή»). "
    "Κράτα το `width_cm` της πρώτης προσπάθειας.\n"
)

READ_TASK = "ΑΝΑΓΝΩΣΗ ΣΧΗΜΑΤΟΣ.\n" + _GRID_INTRO + _STEPS + "Στο `changes` γράψε ό,τι δεν διαβάζεται με βεβαιότητα.\n"

LOCATE_TASK = (
    "ΕΝΤΟΠΙΣΜΟΣ ΣΧΗΜΑΤΟΣ. Οι εικόνες είναι οι σελίδες μιας άσκησης (0 = πρώτη). Βρες το σχήμα που ζητείται "
    "παρακάτω — μπορεί να είναι και μέσα στη ΛΥΣΗ/ΑΠΑΝΤΗΣΗ της άσκησης, όχι μόνο στην εκφώνηση. "
    "Αν υπάρχουν πολλά σχήματα, διάλεξε αυτό που ταιριάζει καλύτερα στο αίτημα και στην εκφώνηση. "
    "Δώσε `crop_page` και `crop` = [x0, y0, x1, y1] ως κλάσματα 0–1 της σελίδας (από πάνω αριστερά), να "
    "περιέχει ολόκληρο το σχήμα με τις ετικέτες του. Αν δεν υπάρχει σχήμα, `found` = false και εξήγησε στο `why`.\n"
)


# --------------------------------------------------------------------------- εικόνες

def _open(data: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        pass
    return img.convert("RGB")


def grid_crop(page: bytes, crop, pad: float = PAD, n: int = GRID_N) -> bytes:
    """Η περιοχή του σχήματος μεγεθυμένη, με αριθμημένο τετραγωνισμένο πλέγμα (PNG)."""
    from PIL import Image, ImageDraw, ImageFont

    img = _open(page)
    w, h = img.size
    try:
        x0, y0, x1, y1 = (float(v) for v in crop)
    except (TypeError, ValueError):
        x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
    box = (int(max(0.0, x0 - pad) * w), int(max(0.0, y0 - pad) * h),
           int(min(1.0, x1 + pad) * w), int(min(1.0, y1 + pad) * h))
    c = img.crop(box)
    s = GRID_LONG / max(c.size)
    c = c.resize((max(1, int(c.width * s)), max(1, int(c.height * s))), Image.LANCZOS)
    m = 52
    g = Image.new("RGB", (c.width + 2 * m, c.height + 2 * m), "white")
    g.paste(c, (m, m))
    d = ImageDraw.Draw(g)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        try:
            font = ImageFont.truetype("arialbd.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
    step = max(c.size) / n
    nx, ny = int(c.width / step), int(c.height / step)
    for i in range(nx + 1):
        x = m + i * step
        d.line([(x, m), (x, m + c.height)], fill=(0, 140, 255), width=1)
        if i % 2 == 0:
            d.text((x - 8, m + c.height + 10), str(i), fill=(0, 90, 200), font=font)
    for j in range(ny + 1):
        y = m + c.height - j * step
        d.line([(m, y), (m + c.width, y)], fill=(0, 140, 255), width=1)
        if j % 2 == 0:
            d.text((10, y - 12), str(j), fill=(0, 90, 200), font=font)
    out = io.BytesIO()
    g.save(out, format="PNG")
    return out.getvalue()


# --------------------------------------------------------------------------- πότε χρειάζεται

def needs_check(fig: dict | None) -> bool:
    """Σχήματα με διανύσματα, γεωμετρικά (χωρίς άξονες) ή με αβέβαιη ανάγνωση.
    Τα γραφήματα συναρτήσεων σε άξονες με βέβαιη ανάγνωση δεν χρειάζονται δεύτερο κοίταγμα."""
    if not fig or not fig.get("spec_json"):
        return False
    if fig.get("confidence") == "low":
        return True
    try:
        spec = json.loads(fig["spec_json"])
    except (TypeError, ValueError):
        return True
    if not isinstance(spec, dict):
        return True
    els = spec.get("elements") or []
    if any(isinstance(e, dict) and e.get("type") == "vector" for e in els):
        return True
    return not spec.get("axes")


def exercise_text(data: dict) -> str:
    """Η εκφώνηση και τα ερωτήματα σε απλό κείμενο (για να καταλαβαίνει ο Claude τι δείχνει το σχήμα)."""
    parts = [data.get("stem", "")]

    def walk(items):
        for it in items or []:
            parts.append(f"{it.get('label', '')}) {it.get('text', '')}")
            walk(it.get("items"))

    walk(data.get("items"))
    parts.append(data.get("closing", ""))
    return "\n".join(p for p in parts if p and p.strip())[:3000]


# --------------------------------------------------------------------------- κλήσεις

async def _ask(provider: Provider, rules: str, text: str, images: list[ImageInput], schema: dict) -> dict:
    try:  # ο έλεγχος δεν μετρά ως «εικόνα που διαβάστηκε» στην ένδειξη χρήσης
        return await provider.extract_json(rules, text, images, schema, count_images=0)
    except TypeError:  # provider χωρίς την παράμετρο (π.χ. δοκιμαστικός)
        return await provider.extract_json(rules, text, images, schema)


def _valid_spec(spec_json: str) -> bool:
    from .figures import parse_spec, render

    try:
        render(parse_spec(spec_json))
        return True
    except Exception:  # noqa: BLE001
        return False


async def check_figure(provider: Provider, pages: list[bytes], fig: dict, statement: str, rules: str) -> dict:
    """Δεύτερος έλεγχος· επιστρέφει νέο `figure` ή το ίδιο αν κάτι δεν πάει καλά."""
    from .figures import parse_spec, render

    page = pages[min(max(0, int(fig.get("crop_page") or 0)), len(pages) - 1)]
    images = [ImageInput(await asyncio.to_thread(grid_crop, page, fig.get("crop")), "image/png")]
    try:
        _svg, png, _w = await asyncio.to_thread(render, parse_spec(fig["spec_json"]))
        images.append(ImageInput(png, "image/png"))
    except Exception:  # noqa: BLE001 — η πρώτη προσπάθεια δεν σχεδιάζεται: ο έλεγχος τη φτιάχνει από την αρχή
        pass
    task = (CHECK_TASK if len(images) > 1 else READ_TASK)
    text = (task + "\n\nΕκφώνηση της άσκησης:\n«" + statement + "»\n\nΠρώτη προσπάθεια (spec_json):\n" + fig["spec_json"])
    try:
        out = await _ask(provider, rules, text, images, CHECK_SCHEMA)
    except ProviderError:
        raise
    except Exception:  # noqa: BLE001
        return fig
    spec_json = str(out.get("spec_json") or "")
    if not spec_json or not await asyncio.to_thread(_valid_spec, spec_json):
        return fig
    new = dict(fig)
    new.update({
        "spec_json": spec_json,
        "description": str(out.get("description") or fig.get("description", "")),
        "confidence": out.get("confidence") if out.get("confidence") in ("high", "low") else fig.get("confidence", "high"),
        "checked": True,
        "check_notes": str(out.get("changes") or "")[:1500],
    })
    return new


async def read_figure(provider: Provider, pages: list[bytes], request: str, statement: str, rules: str) -> dict:
    """Σχήμα από την εικόνα κατ' απαίτηση (π.χ. από τη λύση): εντοπισμός → ανάγνωση σε πλέγμα.
    Επιστρέφει `figure` ή {"error": …}."""
    from .transcribe import prepare_image

    imgs = [prepare_image(p) for p in pages]
    text = (LOCATE_TASK + "\nΑίτημα της καθηγήτριας: «" + (request.strip() or "το σχήμα της άσκησης") + "»"
            + "\n\nΕκφώνηση της άσκησης:\n«" + statement + "»")
    loc = await _ask(provider, rules, text, imgs, LOCATE_SCHEMA)
    if not loc.get("found"):
        return {"error": "Δεν βρέθηκε σχήμα στην εικόνα." + (f" ({loc.get('why')})" if loc.get("why") else "")}
    page_i = min(max(0, int(loc.get("crop_page") or 0)), len(pages) - 1)
    crop = loc.get("crop") if isinstance(loc.get("crop"), list) and len(loc["crop"]) == 4 else [0, 0, 1, 1]
    crop = [min(1.0, max(0.0, float(c))) for c in crop]
    grid = await asyncio.to_thread(grid_crop, pages[page_i], crop)
    text = READ_TASK + "\n\nΕκφώνηση της άσκησης:\n«" + statement + "»\n\nΤι δείχνει (από τον εντοπισμό): " + str(loc.get("description", ""))
    out = await _ask(provider, rules, text, [ImageInput(grid, "image/png")], CHECK_SCHEMA)
    spec_json = str(out.get("spec_json") or "")
    if not spec_json or not await asyncio.to_thread(_valid_spec, spec_json):
        return {"error": "Ο Claude δεν κατάφερε να περιγράψει το σχήμα. Δοκιμάστε ξανά ή χρησιμοποιήστε την πρωτότυπη εικόνα."}
    return {
        "description": str(out.get("description") or loc.get("description", "")),
        "spec_json": spec_json,
        "crop": crop,
        "crop_page": page_i,
        "confidence": out.get("confidence") if out.get("confidence") in ("high", "low") else "low",
        "use_original": False,
        "checked": True,
        "check_notes": str(out.get("changes") or "")[:1500],
    }
