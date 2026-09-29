# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets/shadowing_icon.png', 'assets')]
runtime_names = {
    'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_codecvt_ids.dll', 'vcamp140.dll', 'vccorlib140.dll',
    'vcomp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
}
binaries = [
    (f'C:/Windows/System32/{name}', '.')
    for name in sorted(runtime_names)
]
hiddenimports = []
tmp_ret = collect_all('imageio_ffmpeg')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pysubs2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('docx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PySide's wheel contains an older private VC runtime, while this Qt build needs
# entry points from the current runtime.  Also guard against unrelated native
# libraries inherited from the Codex host PATH during dependency discovery.
clean_binaries = []
for entry in a.binaries:
    destination, source, typecode = entry
    normalized_destination = destination.replace('\\', '/').lower()
    normalized_source = source.replace('\\', '/').lower()
    parts = normalized_destination.split('/')
    is_private_old_runtime = (
        len(parts) > 1
        and parts[0] in {'pyside6', 'shiboken6'}
        and parts[-1] in runtime_names
    )
    is_host_contamination = '/.cache/codex-runtimes/' in normalized_source
    if not is_private_old_runtime and not is_host_contamination:
        clean_binaries.append(entry)
a.binaries = clean_binaries
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ShadowingTrainer-v8',
    icon='assets/shadowing_icon.ico',
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
