"""Αρχεία build που φτιάχνει το GitHub Actions πριν από το PyInstaller.

    python scripts/make_build_files.py <έκδοση> <commit> <αριθμός-build>

Γράφει:
- app/resources/build_info.json — εμφανίζεται στο «Σχετικά» της εφαρμογής,
- build/version_info.txt — στοιχεία έκδοσης στις Ιδιότητες του Diagonismata.exe (Windows),
- app/resources/docs/ — CHANGELOG και οδηγός χρήσης μέσα στην εφαρμογή.
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v0}, {v1}, {v2}, 0), prodvers=({v0}, {v1}, {v2}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040804b0', [
      StringStruct('CompanyName', 'Diagonismata'),
      StringStruct('FileDescription', 'Γεννήτρια Διαγωνισμάτων'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Diagonismata'),
      StringStruct('OriginalFilename', 'Diagonismata.exe'),
      StringStruct('ProductName', 'Γεννήτρια Διαγωνισμάτων'),
      StringStruct('ProductVersion', '{version} (build {run}, {commit})')])]),
    VarFileInfo([VarStruct('Translation', [0x0408, 1200])])
  ]
)
"""


def main() -> None:
    version, commit, run = (sys.argv[1:4] + ["", "", ""])[:3]
    parts = [int(p) for p in version.split(".")[:3]] + [0, 0, 0]
    info = {
        "version": version,
        "commit": commit,
        "run": run,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    res = ROOT / "app" / "resources"
    (res / "build_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "build" / "version_info.txt").write_text(
        VERSION_INFO.format(v0=parts[0], v1=parts[1], v2=parts[2], version=version, run=run, commit=commit[:7]),
        encoding="utf-8",
    )
    docs = res / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "CHANGELOG.md", docs / "CHANGELOG.md")
    shutil.copyfile(ROOT / "docs" / "user-guide.md", docs / "user-guide.md")
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
