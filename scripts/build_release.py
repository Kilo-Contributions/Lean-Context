#!/usr/bin/env python3
"""Build a self-contained zipapp and full source release using the standard library."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile
import zipapp
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.2.0'


def included(path):
    return not any(part in {'__pycache__','.venv','.git','.superpowers','build'} or
                   part.endswith('.egg-info') for part in path.parts) and path.suffix not in {'.pyc','.pyo'}


def main():
    output=ROOT/'dist'
    output.mkdir(exist_ok=True)
    prefix=f'leancontext-{VERSION}'
    with tempfile.TemporaryDirectory(prefix='leancontext-release-') as tmp:
        staging=Path(tmp)/prefix
        staging.mkdir()
        with tempfile.TemporaryDirectory(prefix='leancontext-pyz-') as ztmp:
            zroot=Path(ztmp)
            shutil.copytree(ROOT/'leancontext',zroot/'leancontext',
                            ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            (zroot/'__main__.py').write_text('from leancontext.cli import main\nraise SystemExit(main())\n')
            zipapp.create_archive(zroot,target=staging/'leancontext.pyz',
                                  interpreter='/usr/bin/env python3',compressed=True)
        for directory in ('leancontext','tests','docs','benchmarks','integrations','scripts'):
            for source in (ROOT/directory).rglob('*'):
                if source.is_file() and included(source.relative_to(ROOT)):
                    dest=staging/source.relative_to(ROOT)
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,dest)
        for name in ('pyproject.toml','README.md','LICENSE','CHANGELOG.md','PROGRESS.md','leancontext_launcher.py'):
            shutil.copy2(ROOT/name,staging/name)
        for wheel in output.glob(f'leancontext-{VERSION}-*.whl'):
            dest=staging/'wheels'/wheel.name
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(wheel,dest)
        sums=[]
        for f in sorted(staging.rglob('*')):
            if f.is_file():
                sums.append(f'{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(staging).as_posix()}')
        (staging/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
        archive=output/f'LeanContext-{VERSION}.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for f in sorted(staging.rglob('*')):
                if f.is_file():
                    z.write(f,f'{prefix}/{f.relative_to(staging).as_posix()}')
        shutil.copy2(staging/'leancontext.pyz',output/'leancontext.pyz')
    print(f'{archive}\nSHA256 {hashlib.sha256(archive.read_bytes()).hexdigest()}\nBytes {archive.stat().st_size}')


if __name__=='__main__':
    main()
