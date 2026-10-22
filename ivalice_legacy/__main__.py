"""Build the Ivalice Legacy mod package from your own game files.

    python -m ivalice_legacy build [--game "<FINAL FANTASY XII THE ZODIAC AGE folder>"] [--out dist]

The game folder can also be given with the IVALICE_LEGACY_GAME environment variable. The result is
dist/IvaliceLegacy-<version>/ plus a .zip for Vortex (or manual copying into the game folder): loose files
under mods/deploy/ff12data, loaded by the FF12 External File Loader.
"""
from pathlib import Path
import argparse
import json
import os
import shutil
import sys
import zipfile

from . import __version__
from .patches import APPLIERS, sha256
from .vbf import VBF

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path('C:/Program Files (x86)/Steam/steamapps/common/FINAL FANTASY XII THE ZODIAC AGE')
PREFIX = 'mods/deploy/ff12data/'
STAMP = (2026, 1, 1, 0, 0, 0)  # fixed zip timestamps keep builds reproducible


def build(game, out):
    vbf = VBF(Path(game) / 'FFXII_TZA.vbf')
    files = {}
    for name, apply in APPLIERS.items():
        patch = json.loads((ROOT / 'patches' / f'{name}.json').read_text(encoding='utf-8'))
        for rel, data in apply(vbf.read, patch).items():
            if rel in files:
                raise ValueError(f'two patches write {rel}')
            files[rel] = data
    package = Path(out) / f'IvaliceLegacy-{__version__}'
    if package.exists():
        shutil.rmtree(package)
    manifest = {'name': 'Ivalice Legacy', 'version': __version__,
                'files': {PREFIX + rel: sha256(data) for rel, data in sorted(files.items())}}
    for rel, data in files.items():
        target = package / PREFIX / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = Path(out) / f'IvaliceLegacy-{__version__}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(files.items()):
            info = zipfile.ZipInfo(PREFIX + rel, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    (Path(out) / f'IvaliceLegacy-{__version__}.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return archive, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ivalice_legacy')
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='build the mod package from your game files')
    b.add_argument('--game', default=os.environ.get('IVALICE_LEGACY_GAME', str(DEFAULT_GAME)))
    b.add_argument('--out', default=str(ROOT / 'dist'))
    args = ap.parse_args(argv)
    try:
        archive, manifest = build(args.game, args.out)
    except (OSError, KeyError, ValueError) as error:
        print(f'Build failed: {error}', file=sys.stderr)
        return 1
    print(f"Built {archive} ({len(manifest['files'])} files)")
    for rel, digest in manifest['files'].items():
        print(f'  {digest[:16]}  {rel}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
