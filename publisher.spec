# Build two entry points sharing one runtime directory. No user-data paths are collected.
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "entry.py")], pathex=[str(root)],
    binaries=[], datas=[(str(root / "assets"), "assets")],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PIL", "resvg_py", "unittest"], noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ICViewsPublisher",
          debug=False, strip=False, upx=False, console=False,
          icon=str(root / "assets" / "app.ico"))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name="icviews-worker",
             debug=False, strip=False, upx=False, console=True,
             icon=str(root / "assets" / "app.ico"))
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name="ICViewsPublisher")
