from pathlib import Path

# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    [str(Path(SPECPATH).parent / 'desktop' / 'desktop_server.py')],
    pathex=[str(Path(SPECPATH).parent)],
    binaries=[],
    datas=[],
    hiddenimports=['limefun.player_server', 'limefun.download_manager', 'limefun.remote_sync'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LimeFunServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
