"""Build the Ivalice Legacy mod package from your own game files.

    python -m ivalice_legacy build [--game "<FINAL FANTASY XII THE ZODIAC AGE folder>"] [--out dist]

The game folder can also be given with the IVALICE_LEGACY_GAME environment variable. The result is
dist/IvaliceLegacy-<version>/ plus a .zip for Vortex (or manual copying into the game folder): patched game
data as loose files under mods/deploy/ff12data (loaded by the FF12 External File Loader), plus the mod's own
files from package/ (Lua scripts, loaded by the FF12 LUA Loader).
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
    from .save_isolation import validate_runtime
    runtime = validate_runtime(game)
    vbf = VBF(Path(game) / 'FFXII_TZA.vbf')
    files = {}
    for name, apply in APPLIERS.items():
        patch = json.loads((ROOT / 'patches' / f'{name}.json').read_text(encoding='utf-8'))
        for rel, data in apply(vbf.read, patch).items():
            if rel in files:
                raise ValueError(f'two patches write {rel}')
            files[rel] = data
    files = {PREFIX + rel: data for rel, data in files.items()}
    for source in sorted((ROOT / 'package').rglob('*')):
        if source.is_file():
            files[source.relative_to(ROOT / 'package').as_posix()] = source.read_bytes()
    return write_package(files, out, f'IvaliceLegacy-{__version__}', 'Ivalice Legacy', __version__,
                         {'saveIsolation': runtime})


def build_rampager_pilot(game, out):
    from .patches import enemy_ai
    vbf = VBF(Path(game) / 'FFXII_TZA.vbf')
    patch = json.loads((ROOT / 'patches/rampager_pilot.json').read_text(encoding='utf-8'))
    files = {PREFIX + rel: data for rel, data in enemy_ai(vbf.read, patch).items()}
    return write_package(files, out, 'IvaliceLegacy-RampagerPilot-0.0.1',
                         'Ivalice Legacy Rampager Pilot', '0.0.1',
                         {'source': {'file': patch['file'], 'fileSha256': patch['sourceFileSha256'],
                                     'section': patch['section'], 'sectionSha256': patch['sourceSectionSha256']},
                          'conditions': patch['conditions']})


def write_package(files, out, stem, name, version, metadata=None):
    package = Path(out) / stem
    if package.exists():
        shutil.rmtree(package)
    manifest = {'name': name, 'version': version,
                'files': {rel: sha256(data) for rel, data in sorted(files.items())}}
    if metadata:
        manifest.update(metadata)
    for rel, data in files.items():
        target = package / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = Path(out) / f'{stem}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(files.items()):
            info = zipfile.ZipInfo(rel, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    (Path(out) / f'{stem}.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return archive, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ivalice_legacy')
    sub = ap.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='build the mod package from your game files')
    pilot = sub.add_parser('build-rampager-pilot', help='build only the optional Rampager AI pilot')
    pilot.add_argument('--game', default=os.environ.get('IVALICE_LEGACY_GAME', str(DEFAULT_GAME)))
    pilot.add_argument('--out', default=str(ROOT / 'dist'))
    bases = sub.add_parser('build-base-jobs', help='build the separate experimental twelve-base-job candidate')
    bases.add_argument('--game', default=os.environ.get('IVALICE_LEGACY_GAME', str(DEFAULT_GAME)))
    bases.add_argument('--out', default=str(ROOT / 'dist'))
    b.add_argument('--game', default=os.environ.get('IVALICE_LEGACY_GAME', str(DEFAULT_GAME)))
    b.add_argument('--out', default=str(ROOT / 'dist'))
    args = ap.parse_args(argv)
    try:
        if args.command == 'build-base-jobs':
            from .base_jobs import build as builder
        else:
            builder = build_rampager_pilot if args.command == 'build-rampager-pilot' else build
        archive, manifest = builder(args.game, args.out)
    except (OSError, KeyError, ValueError) as error:
        print(f'Build failed: {error}', file=sys.stderr)
        return 1
    print(f"Built {archive} ({len(manifest['files'])} files)")
    for rel, digest in manifest['files'].items():
        print(f'  {digest[:16]}  {rel}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
