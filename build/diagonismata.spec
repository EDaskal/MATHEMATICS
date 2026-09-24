# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: pyinstaller build/diagonismata.spec --noconfirm --distpath build/dist --workpath build/work
# Φάκελος εξόδου: build/dist/Diagonismata/ (onedir — γρηγορότερη εκκίνηση, το πακετάρει το Inno Setup).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 — ορίζεται από το PyInstaller

datas = [
    (str(ROOT / "app" / "ui"), "app/ui"),
    (str(ROOT / "app" / "resources"), "app/resources"),
]
if (ROOT / "app" / "bin").exists():
    datas.append((str(ROOT / "app" / "bin"), "app/bin"))  # pandoc.exe
binaries = []
hiddenimports = []

# Agent SDK: περιλαμβάνει το Claude Code CLI (claude.exe) στο _bundled
for pkg in ("claude_agent_sdk", "webview", "keyring", "anthropic"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("app")
hiddenimports += ["keyring.backends.Windows", "win32ctypes.core", "win32ctypes.pywin32.win32cred", "clr"]
hiddenimports += ["matplotlib.backends.backend_svg", "matplotlib.backends.backend_agg"]  # φορτώνονται δυναμικά από το savefig

a = Analysis(  # noqa: F821
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "docx"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Diagonismata",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "build" / "icon.ico"),
    version=str(ROOT / "build" / "version_info.txt") if (ROOT / "build" / "version_info.txt").exists() else None,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Diagonismata",
)
