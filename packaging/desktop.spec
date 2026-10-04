from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPEC).resolve().parent.parent
src_root = project_root / "src"
esi_root = project_root / "ESI" / "active"
webui_dist = project_root / "webui" / "dist"
app_name = "SYNTEC-ECAT-Test"

if not (webui_dist / "index.html").is_file():
    raise SystemExit(
        "webui/dist/index.html is missing. Run 'npm run build' in webui/ first."
    )

analysis = Analysis(
    [str(project_root / "packaging" / "desktop_entry.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=[
        (str(esi_root), "ESI"),
        (str(webui_dist), str(Path("webui") / "dist")),
    ],
    hiddenimports=(
        collect_submodules("dm3c_ecat")
        # pywebview 平台后端是运行时动态导入，PyInstaller 抓不到
        + collect_submodules("webview.platforms")
        + collect_submodules("uvicorn")
    ),
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
    name=app_name,
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
    name=app_name,
)