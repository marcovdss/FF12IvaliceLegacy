"""Build the Hybrid Boards package: every job pair fuses into one named board (all 66 pairs).

Reuses the reviewed pilot hook plan, thunks and package writer; this module only widens the content from one pair
(Paladin) to the 66 recipes in patches/hybrid_boards.json and names in patches/hybrid_names.json.
"""
from io import BytesIO
from pathlib import Path, PurePosixPath
import argparse
import json
import re
import struct
import zipfile

from .hybrid_layout import validate_any
from .hybrid_pilot import (GAME_EXE_SHA256, NATIVE, PILOT_SITES, PREFERRED_BASE, ROOT, SCRIPT_ROOT, STAMP, BOARD, PACK,
                           canonical, hook_templates, lua, sha)
from .job_labels import encode_label
from .vbf import VBF
from .formats import battle_pack_section

NAME = 'IvaliceLegacy-HybridBoards'
ENTRY = 'x64/scripts/IvaliceLegacyHybridBoards.lua'
CHANGES = {
    '0.3.4': ('Two more hybrids are renamed: Grenadier (Chemist + Black Mage) is now Detonator and Breaker (Lancer + Green '
              'Mage) is now Magebreaker. Only the names change: no board layout, license or price differs from 0.3.3.'),
    '0.3.3': ('Sixteen hybrids are renamed (Chrono Lancer, Crusader, Monk, Cleric, Chrono Knight, Gunbreaker, Corsair, '
              'Riskbreaker, Lanista, Swiftblade, Arithmetician, Ki Gunner, Spellfist, Dragonkin, Spellbow, Despoiler). '
              'Only the names change: no board layout, license or price differs from 0.3.2.'),
    '0.3.2': ('BaseJobs 0.7.0: Telekinesis belongs to the Warrior, Protectga and Shellga to the White Mage, Achilles to '
              'the Gunner, and the Lancer no longer has Dispel and Stona. The 49 boards whose licenses change are laid out '
              'again and the Paladin board loses its Telekinesis tile; the other 16 boards keep their layouts.'),
    '0.3.1': ('BaseJobs 0.6.0: hammers belong to Time Mage and measures to Chemist (axes stay with Warrior), so the 30 '
              'boards with Warrior, Time Mage or Chemist are laid out again. The other 36 boards are unchanged.'),
    '0.3.0': ('All 66 job pairs fuse into one board named for the hybrid (Knight + White Mage = Paladin, Lancer + '
              'Samurai = Dragoon, ...). Both parents\' licenses once each with native costs; gear up, accessories '
              'left, passives right, commands down; Espers keep their zodiac-weapon islands. Paladin keeps its 0.2.1 '
              'board. Names are provisional.'),
}


def source_inputs(game):
    game = Path(game)
    if sha((game/'x64/FFXII_TZA.exe').read_bytes()) != GAME_EXE_SHA256:
        raise ValueError('unsupported game executable')
    archive = VBF(game/'FFXII_TZA.vbf')
    deploy = game/'mods/deploy/ff12data'

    def read(relative):
        loose = deploy/relative
        return loose.read_bytes() if loose.is_file() else archive.read(relative)
    sources = {job: read(BOARD.format(job+1)) for job in range(12)}
    loose_license = deploy/(PACK+'.dir/section_012.bin')
    licenses = loose_license.read_bytes() if loose_license.is_file() else battle_pack_section(read(PACK), 12)
    return sources, licenses


def layouts_digest(entries):
    return sha(canonical([{'pair': e['pair'], 'layout': e['layout'], 'name': e['name']} for e in entries]))


def prepare_bundle(boards, names, sources, licenses, image, image_manifest, hooks, gate, native=NATIVE):
    named = {tuple(h['pair']): h['name'] for h in names['hybrids']}
    pairs = [tuple(b['pair']) for b in boards['boards']]
    if len(pairs) != 66 or set(pairs) != set(named) or len(set(pairs)) != 66:
        raise ValueError('expected exactly one board per job pair (66)')
    entries = []
    for recipe in boards['boards']:
        pair = tuple(recipe['pair'])
        if recipe.get('name') != named[pair]:
            raise ValueError(f'board name differs from hybrid_names for {pair}')
        raw = validate_any(recipe, {j: sources[j] for j in pair}, licenses)
        encoded = encode_label(named[pair])
        if len(encoded) > 24:
            raise ValueError(f'hybrid name too long: {named[pair]}')
        entries.append({'pair': list(pair), 'layout': list(struct.unpack_from('<576H', raw, 8)), 'name': list(encoded)})
    if {entry['site'] for entry in hooks} != PILOT_SITES or len(hooks) != len(PILOT_SITES):
        raise ValueError('incomplete hook set')
    required = ('HBX_size', 'HBX_init', 'HBX_core', 'HBX_config', 'HBX_route', 'HBX_name', 'HB_put')
    exports = image_manifest.get('exports', {})
    if any(type(exports.get(name)) is not int or not 0 <= exports[name] < len(image) for name in required):
        raise ValueError('required adapter export missing or outside image')
    if image_manifest.get('imageSize') != len(image) or image_manifest.get('sha256') != sha(image):
        raise ValueError('adapter image differs from its manifest')
    if gate.get('completeHookSet') is not True or gate.get('sites') != sorted(PILOT_SITES):
        raise ValueError('native integration gate is incomplete')
    fingerprints = {'adapterImageSha256': sha(image), 'layoutsSha256': layouts_digest(entries),
                    'hookPlanSha256': sha(canonical(hooks)),
                    'loaderSha256': sha((native/'loader.lua').read_bytes()),
                    'hooksSha256': sha((native/'hooks.lua').read_bytes())}
    if any(gate.get(key) != value for key, value in fingerprints.items()):
        raise ValueError('native integration gate is stale for these package bytes')
    size = gate.get('adapterSize')
    if type(size) is not int or not 0 < size <= 0x100000:
        raise ValueError('invalid reviewed adapter state size')
    return {'schemaVersion': 2, 'preferredBase': PREFERRED_BASE, 'gameExeSha256': GAME_EXE_SHA256,
            'adapterSize': size, 'requestSize': 48, 'objectCount': 9, 'completeHookSet': True,
            'image': list(image), 'exports': exports, 'layouts': entries, 'hooks': hooks,
            'licenseTableSha256': boards['licenseTableSha256'], **fingerprints}


def package_files(bundle, version, native=NATIVE):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('invalid package version')
    entry = '''-- Ivalice Legacy: hybrid boards (66 job pairs). No save-file redirection or hot unload.
local loader = require("IvaliceLegacyHybrids.loader")
local hooks = require("IvaliceLegacyHybrids.hooks")
local bundle = require("IvaliceLegacyHybrids.bundle")
local handle, retained
local function attempt()
  if handle then
    local ok, ready = pcall(handle.reconfigure)
    if not ok or not ready then
      print("Ivalice Legacy hybrid boards: save-load configuration unavailable; hybrid contexts refused.")
    end
    return true
  end
  if retained and not retained.patchesRestored then return true end
  local error
  handle, error, retained = loader.install(memory, arg or memory.arg, bundle, hooks, event)
  if handle then
    print("Ivalice Legacy hybrid boards: native adapter installed for " .. #bundle.layouts .. " hybrids.")
  elseif retained and not retained.patchesRestored then
    print("Ivalice Legacy hybrid boards: FATAL partial hook transaction; exit the game and disable this mod before continuing. Native allocations retained.")
  else
    print("Ivalice Legacy hybrid boards: not installed: " .. tostring(error))
  end
  return true
end
if type(event) ~= "table" or type(event.registerEventSync) ~= "function" then
  print("Ivalice Legacy hybrid boards: synchronous lifecycle API unavailable; not installed.")
  return
end
if event.registerEventSync("onSaveLoad", attempt) ~= true then
  print("Ivalice Legacy hybrid boards: save-load lifecycle unavailable; not installed.")
  return
end
-- Startup may precede keep/menu initialization. The synchronous save-load
-- callback retries without exposing incomplete hooks or hot-unloading state.
event.registerEventSync("onInitDone", attempt)
attempt()
'''
    readme = f'''# Ivalice Legacy Hybrid Boards {version}

When a character takes a second job, the two license boards become one board named for the hybrid
(for example Knight + White Mage = Paladin). All 66 job pairs, either job order. Existing job IDs, license
meanings, prices, acquired licenses and LP stay native; only the board layout and its name change.

Install through Vortex on the intended Legacy profile with the game closed. It adds no save redirect.
Runtime preflight refuses changed hook bytes. Never reload/unload it while the game is running.

Uninstall/rollback: close the game, disable this mod in Vortex and deploy. Bought licenses stay bought; the two
native tabs return. Together files and settings, native board files, executables and saves are not part of this
package. Hybrid names are provisional.
'''
    files = {ENTRY: entry.encode(),
             SCRIPT_ROOT+'loader.lua': (native/'loader.lua').read_bytes(),
             SCRIPT_ROOT+'hooks.lua': (native/'hooks.lua').read_bytes(),
             SCRIPT_ROOT+'bundle.lua': ('return '+lua(bundle)+'\n').encode(),
             SCRIPT_ROOT+'README.md': readme.encode(),
             SCRIPT_ROOT+'CHANGELOG.md': ''.join(f'# {v}\n\n{CHANGES[v]}\n\n' for v in sorted(CHANGES, reverse=True)
                                                 if v <= version).encode()}
    manifest = {'name': NAME, 'version': version, 'hybrids': len(bundle['layouts']),
                'status': 'review candidate; in-game validation pending', 'gameExeSha256': GAME_EXE_SHA256,
                'adapterImageSha256': bundle['adapterImageSha256'], 'layoutsSha256': bundle['layoutsSha256'],
                'hookPlanSha256': bundle['hookPlanSha256'],
                'files': {name: sha(data) for name, data in sorted(files.items())}}
    files[SCRIPT_ROOT+'manifest.json'] = (json.dumps(manifest, indent=2)+'\n').encode()
    return files, manifest


def write_package(files, manifest, out):
    out = Path(out)
    stem = NAME+'-'+manifest['version']
    payload = BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            if not name or '\\' in name or ':' in name or name.startswith('/') or '..' in PurePosixPath(name).parts:
                raise ValueError('unsafe package path')
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    data = payload.getvalue()
    archive_path = out/(stem+'.zip')
    if archive_path.exists() and archive_path.read_bytes() != data:
        raise ValueError('version already exists with different bytes; increment version')
    out.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(data)
    return archive_path, {**manifest, 'archiveSha256': sha(data),
                          'allFiles': {name: sha(content) for name, content in sorted(files.items())}}


def build(game, boards_path, gate_path, out, version='0.3.0', native=NATIVE):
    if Path(out).resolve().is_relative_to(Path(game).resolve()):
        raise ValueError('build output must be outside the game installation')
    boards = json.loads(Path(boards_path).read_text(encoding='utf-8'))
    names = json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))
    gate = json.loads(Path(gate_path).read_text())
    sources, licenses = source_inputs(game)
    image = (native/'build/hybrid_adapter.image').read_bytes()
    image_manifest = json.loads((native/'build/adapter-image-manifest.json').read_text())
    bundle = prepare_bundle(boards, names, sources, licenses, image, image_manifest, hook_templates(native), gate, native)
    return write_package(*package_files(bundle, version, native), out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', required=True)
    parser.add_argument('--boards', default=str(ROOT/'patches/hybrid_boards.json'))
    parser.add_argument('--gate', default=str(NATIVE/'hybrid-boards-gate.json'))
    parser.add_argument('--out', default=str(ROOT/'dist'))
    parser.add_argument('--version', default='0.3.0')
    args = parser.parse_args()
    archive, receipt = build(args.game, args.boards, args.gate, args.out, args.version)
    print(json.dumps({'archive': str(archive), **{k: v for k, v in receipt.items() if k != 'files'}}, indent=2))


if __name__ == '__main__':
    main()
