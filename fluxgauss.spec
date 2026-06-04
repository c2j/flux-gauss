# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None

# Locate ogsql binary for bundling
ogsql_bin = os.environ.get('OGSQL_BIN_PATH', '')
if not ogsql_bin or not os.path.isfile(ogsql_bin):
    for candidate in ['ogsql', 'ogsql.exe', '../ogsql', '../ogsql.exe']:
        if os.path.isfile(candidate):
            ogsql_bin = os.path.abspath(candidate)
            break

datas = []
if ogsql_bin and os.path.isfile(ogsql_bin):
    datas = [(ogsql_bin, '.')]
else:
    print("WARNING: ogsql binary not found. Set OGSQL_BIN_PATH env var.")
    print("  The packaged binary will NOT be able to parse SQL without ogsql.")

a = Analysis(
    ['converter/flux_gauss.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'test', 'tests',
        'distutils', 'setuptools', 'pip',
        'xmlrpc', 'pydoc', 'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='fluxgauss-py',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
