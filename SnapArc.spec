# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('src/gui/screenshot_chat/web_assets', 'src/gui/screenshot_chat/web_assets'), ('src/animate', 'src/animate'), ('src/icons', 'src/icons'), ('plugins', 'plugins'), ('plugins.yaml', '.'), ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('TRADEMARKS.md', '.'), ('ASSET_POLICY.md', '.')]
datas += collect_data_files('customtkinter')
datas += collect_data_files('pywebview')
datas += collect_data_files('pystray')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='SnapArc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SnapArc',
)
