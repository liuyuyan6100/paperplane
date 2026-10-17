# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('web', 'web'),
        ('skills', 'skills'),
        ('agents', 'agents'),
        ('library/web', 'library/web'),
        ('docs/visual-styles/aiclaw-warm-engineering-v1', 'visual-styles/aiclaw-warm-engineering-v1'),
    ],
    hiddenimports=[
        'studio.skills',
        'studio.skill_executors',
        'yaml',  # PyYAML for skills metadata
    ],
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
    name='PaperPlaneStudio',
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
    icon=['web/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PaperPlaneStudio',
)
