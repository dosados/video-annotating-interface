from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / 'run.py')], pathex=[str(root / 'src')],
             datas=[(str(root / 'src/video_annotating_interface/static'), 'video_annotating_interface/static')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='video-annotating-interface',
          debug=False, strip=False, upx=False, console=True)
