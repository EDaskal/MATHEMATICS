"""Κατανομή μονάδων: σύνολο 100, προτάσεις ανά θέμα και ανά ερώτημα."""

from __future__ import annotations

TOTAL = 100

_PRESETS = {
    1: [100],
    2: [50, 50],
    3: [40, 30, 30],
    4: [25, 25, 25, 25],
    5: [20, 20, 20, 20, 20],
}


def split_even(total: int, n: int) -> list[int]:
    """Ισόποση κατανομή· το υπόλοιπο πηγαίνει στα πρώτα (25 σε 3 → 9, 8, 8)."""
    if n <= 0:
        return []
    base, rem = divmod(int(total), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def theme_points(n_themes: int) -> list[int]:
    return list(_PRESETS.get(n_themes) or split_even(TOTAL, n_themes))


def item_points(theme_total: int, n_items: int) -> list[int]:
    return split_even(theme_total, max(1, n_items)) if n_items else [theme_total]


def check_total(values: list[int]) -> tuple[bool, int]:
    s = sum(int(v) for v in values)
    return s == TOTAL, s


def points_line(values: list[int]) -> str:
    return "Μονάδες " + " + ".join(str(int(v)) for v in values)
