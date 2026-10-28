"""Χρήση της συνδρομής Claude: όρια (5ωρο, εβδομάδα), κατανάλωση της εφαρμογής, εκτίμηση ανά εικόνα.

Από πού έρχονται τα στοιχεία:
- **Όρια συνδρομής** (μόνο με σύνδεση «συνδρομή Claude»): σε κάθε κλήση το Claude Code CLI στέλνει ένα
  `RateLimitEvent`. Στο `rate_limit_info.raw` υπάρχουν `status` (allowed / allowed_warning / rejected),
  `overageStatus`, `isUsingOverage` και `unifiedWindows` = {"five_hour": {"utilization": 0.37,
  "resetsAt": <unix>}, "seven_day": {...}, ...}. Το πεδίο δεν είναι επίσημα τεκμηριωμένο, γι' αυτό η
  ανάγνωση είναι ανεκτική: ό,τι λείπει απλώς δεν εμφανίζεται.
- **Κατανάλωση της εφαρμογής**: από το αποτέλεσμα κάθε κλήσης (tokens, «αντίστοιχο κόστος API»).

Τα στοιχεία κρατιούνται στο `%APPDATA%\\Diagonismata\\usage.json` ώστε να φαίνονται και με το άνοιγμα της
εφαρμογής (με την ώρα της τελευταίας ενημέρωσης).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
from typing import Callable

from .paths import user_dir

WINDOW_ORDER = ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet")
KEEP_DAYS = 31


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_limits(raw: dict) -> dict:
    """Μετατροπή του `raw` του RateLimitEvent σε απλή μορφή για την οθόνη."""
    raw = raw if isinstance(raw, dict) else {}
    windows = {}
    for name, w in (raw.get("unifiedWindows") or {}).items():
        if not isinstance(w, dict):
            continue
        util = _num(w.get("utilization"))
        if util is None:
            continue
        windows[str(name)] = {"utilization": max(0.0, min(util, 1.5)), "resets_at": int(_num(w.get("resetsAt"), 0) or 0)}
    # παλιότερη μορφή: μόνο ένα παράθυρο στο ίδιο το γεγονός
    kind = raw.get("rateLimitType")
    if not windows and kind and _num(raw.get("utilization")) is not None:
        windows[str(kind)] = {"utilization": _num(raw.get("utilization")), "resets_at": int(_num(raw.get("resetsAt"), 0) or 0)}
    return {
        "status": str(raw.get("status") or ""),
        "limit_type": str(kind or ""),
        "resets_at": int(_num(raw.get("resetsAt"), 0) or 0),
        "overage_status": str(raw.get("overageStatus") or ""),
        "overage_disabled_reason": str(raw.get("overageDisabledReason") or ""),
        "using_overage": bool(raw.get("isUsingOverage")),
        "windows": windows,
    }


def _empty_totals() -> dict:
    return {"calls": 0, "images": 0, "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0,
            "cost_usd": 0.0, "seconds": 0.0, "errors": 0}


class UsageTracker:
    """Ένα στιγμιότυπο για όλη την εφαρμογή (`tracker`). Ασφαλές για πολλά νήματα."""

    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._path = path
        self.on_change: Callable[[dict], None] | None = None
        self.limits: dict | None = None
        self.updated_at = 0.0
        self.model = ""
        self.session = _empty_totals()
        self.days: dict[str, dict] = {}
        # εκτίμηση κατανάλωσης του 5ώρου ανά εικόνα (σε αυτό το παράθυρο)
        self._base = None  # (resets_at, utilization, images_at_base)
        self._images_total = 0
        self.per_image = None  # κλάσμα του 5ώρου ανά εικόνα (π.χ. 0.012)
        self.per_image_samples = 0
        self._loaded = False

    # ------------------------------------------------------------------ αποθήκευση
    def _file(self):
        return self._path or (user_dir() / "usage.json")

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                data = json.loads(self._file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            if not isinstance(data, dict):
                return
            self.limits = data.get("limits") if isinstance(data.get("limits"), dict) else None
            self.updated_at = _num(data.get("updated_at"), 0.0) or 0.0
            self.model = str(data.get("model") or "")
            self.days = {k: {**_empty_totals(), **v} for k, v in (data.get("days") or {}).items() if isinstance(v, dict)}
            self.per_image = _num(data.get("per_image"))
            self.per_image_samples = int(_num(data.get("per_image_samples"), 0) or 0)

    def _save(self) -> None:
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}
        data = {"limits": self.limits, "updated_at": self.updated_at, "model": self.model, "days": self.days,
                "per_image": self.per_image, "per_image_samples": self.per_image_samples}
        try:
            f = self._file()
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass

    def _changed(self) -> None:
        self._save()
        cb = self.on_change
        if cb:
            try:
                cb(self.snapshot())
            except Exception:  # noqa: BLE001 — η οθόνη μπορεί να έχει κλείσει
                pass

    # ------------------------------------------------------------------ καταγραφή
    def record_limits(self, raw: dict, now: float | None = None) -> None:
        lim = parse_limits(raw)
        if not lim["windows"] and not lim["status"]:
            return
        with self._lock:
            self.load()
            self.limits = lim
            self.updated_at = now or time.time()
            self._update_estimate(lim)
            self._changed()

    def _update_estimate(self, lim: dict) -> None:
        w = lim["windows"].get("five_hour")
        if not w:
            return
        if self._base is None or self._base[0] != w["resets_at"] or w["utilization"] < self._base[1]:
            self._base = (w["resets_at"], w["utilization"], self._images_total)  # νέο 5ωρο → νέα βάση
            return
        images = self._images_total - self._base[2]
        delta = w["utilization"] - self._base[1]
        if images >= 3 and delta >= 0.02:  # αρκετά δεδομένα για αξιόπιστη εκτίμηση
            self.per_image = delta / images
            self.per_image_samples = images

    def record_result(self, *, images: int = 0, usage: dict | None = None, cost_usd: float | None = None,
                      seconds: float | None = None, model: str = "", error: bool = False) -> None:
        usage = usage or {}
        with self._lock:
            self.load()
            day = self.days.setdefault(date.today().isoformat(), _empty_totals())
            for tot in (self.session, day):
                tot["calls"] += 1
                tot["images"] += int(images or 0)
                tot["input_tokens"] += int(usage.get("input_tokens") or 0)
                tot["output_tokens"] += int(usage.get("output_tokens") or 0)
                tot["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
                tot["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
                tot["cost_usd"] += float(cost_usd or 0.0)
                tot["seconds"] += float(seconds or 0.0)
                tot["errors"] += 1 if error else 0
            self._images_total += int(images or 0)
            if model:
                self.model = model
            self._changed()

    # ------------------------------------------------------------------ για την οθόνη
    def snapshot(self) -> dict:
        with self._lock:
            self.load()
            today = date.today()
            week = _empty_totals()
            for k, v in self.days.items():
                if k >= (today - timedelta(days=6)).isoformat():
                    for f in week:
                        week[f] += v.get(f, 0)
            return {
                "limits": self.limits,
                "updated_at": self.updated_at,
                "now": time.time(),
                "model": self.model,
                "session": dict(self.session),
                "today": dict(self.days.get(today.isoformat(), _empty_totals())),
                "week": week,
                "per_image": self.per_image,
                "per_image_samples": self.per_image_samples,
            }

    def limit_message(self) -> str:
        """Φιλικό μήνυμα όταν εξαντλήθηκε το όριο, με την ώρα που ξαναδουλεύει."""
        with self._lock:
            lim = self.limits or {}
        ts = lim.get("resets_at") or 0
        if not ts:
            ws = [w["resets_at"] for w in (lim.get("windows") or {}).values() if w.get("utilization", 0) >= 1]
            ts = max(ws) if ws else 0
        if ts:
            when = time.strftime("%d/%m %H:%M", time.localtime(ts))
            return f"Εξαντλήθηκε το όριο της συνδρομής Claude. Ξαναδουλεύει: {when}."
        return "Εξαντλήθηκε προσωρινά το όριο χρήσης της συνδρομής. Δοκιμάστε ξανά αργότερα."


tracker = UsageTracker()
