# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['O:\\Users\\KirrieD\\Desktop\\Dev_DoS\\Cast_Prog\\P3D_Viewer_repo\\p3d_viewer.py'],
    pathex=[],
    binaries=[],
    datas=[('embedded_configs.py', '.'), ('proxies', 'proxies'), ('cpp_parser.py', '.'), ('p3d_proxy_parser.py', '.'), ('proxy_index.py', '.')],
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
    name='P3D_Viewer_repo',
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
    icon=['O:\\Users\\KirrieD\\Desktop\\Dev_DoS\\Cast_Prog\\P3D_Viewer_repo\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='P3D_Viewer_repo',
)
