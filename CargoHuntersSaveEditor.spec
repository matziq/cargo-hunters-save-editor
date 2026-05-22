# Cargo Hunters Save Editor — PyInstaller spec.
# Produces a single-file Windows executable that bundles the CSV catalog
# and Sprite icons.  No external Python install required for users.

from PyInstaller.utils.hooks import collect_data_files  # noqa: F401

block_cipher = None


a = Analysis(
    ['editor_gui.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('all_items_detailed.csv', '.'),
        ('exported_icons/Sprite', 'exported_icons/Sprite'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Strip large modules that aren't used by the GUI.
        'numpy', 'pandas', 'scipy', 'matplotlib', 'PIL', 'PyQt5', 'PyQt6',
        'PySide2', 'PySide6', 'cv2', 'tornado', 'IPython', 'jupyter',
        'pytest', 'sphinx',
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
    name='CargoHuntersSaveEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
