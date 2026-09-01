from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPEC).resolve().parent.parent
src_root = project_root / "src"
esi_root = project_root / "ESI"
backend_name = "SYNTEC-ECAT-Test-Backend"

analysis = Analysis(
    [str(project_root / "packaging" / "backend_entry.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=[(str(esi_root), "ESI")],
    hiddenimports=collect_submodules("dm3c_ecat"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name=backend_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(project_root / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=backend_name,
)
