"""Βγάζει από το CHANGELOG.md την ενότητα μιας έκδοσης (για τις σημειώσεις του GitHub Release).

    python scripts/changelog_section.py 1.1.0 > notes.md
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(version: str, text: str) -> str:
    pattern = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", re.S | re.M)
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def main() -> int:
    version = sys.argv[1].lstrip("v")
    body = section(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    if not body:
        print(f"Δεν βρέθηκε η έκδοση {version} στο CHANGELOG.md", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
