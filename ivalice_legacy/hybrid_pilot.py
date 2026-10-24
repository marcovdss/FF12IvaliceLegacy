"""Build a separate, source-pinned Paladin pilot after native review gates pass.

This command never installs or deploys files. Run with --help for inputs.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import zipfile
from io import BytesIO

from .formats import battle_pack_section
from .hybrid_layout import validate_recipe
from .vbf import VBF

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'native/hybrid_boards'
GAME_EXE_SHA256 = '73461d0896204b1c259c52f61aef4efbc95a2ffc388cadb36493d5d1d89721f8'
PREFERRED_BASE = 0x120000
PACK = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'
BOARD = 'ps2data/image/ff12/test_battle/in/binaryfile/board_{}.bin'
PILOT_SITES = frozenset(range(9)) | frozenset(range(10, 18))
STAMP = (2026, 1, 1, 0, 0, 0)
NAME = 'IvaliceLegacy-Paladin-Pilot'
SCRIPT_ROOT = 'x64/scripts/IvaliceLegacyHybrids/'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def lua(value):
    """Serialize trusted data only; generated bundle contains no executable input."""
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if value is None:
        return 'nil'
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        # UTF-8 octal/hex shortcuts differ across Lua versions; JSON escapes are
        # not Lua escapes. Decimal escapes are unambiguous for every byte.
        return '"'+''.join('\\'+f'{byte:03d}' for byte in value.encode('utf-8'))+'"'
    if isinstance(value, (bytes, bytearray, list, tuple)):
        return '{'+','.join(lua(item) for item in value)+'}'
    if isinstance(value, dict):
        return '{'+','.join('['+lua(key)+']='+lua(item) for key, item in sorted(value.items()))+'}'
    raise ValueError('unsupported bundle data type')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_templates(native=NATIVE):
    sites = _load('hybrid_pilot_sites', native/'sites.py')
    thunks = _load('hybrid_pilot_thunks', native/'thunks.py')
    result = []
    for definition in sites.SITES:
        if not definition['pilot']:
            continue
        fixes = []
        template = thunks.emit_site(0, 0, definition, 0, fixups=fixes)
        entry = {'name': definition['name'], 'site': definition['site'],
                 'source': definition['source'], 'continuation': definition['continuation'],
                 'reject': definition['reject'], 'expected': list(definition['expected']),
                 'template': list(template), 'fixups': fixes,
                 'fallbackCall': definition.get('fallbackCall', 0)}
        result.append(entry)
    if {entry['site'] for entry in result} != PILOT_SITES or len(result) != len(PILOT_SITES):
        raise ValueError('pilot hook set is incomplete or contains duplicate sites')
    return result


def source_inputs(game):
    game = Path(game)
    if sha((game/'x64/FFXII_TZA.exe').read_bytes()) != GAME_EXE_SHA256:
        raise ValueError('unsupported game executable')
    archive = VBF(game/'FFXII_TZA.vbf')
    deploy = game/'mods/deploy/ff12data'
    def read(relative):
        loose = deploy/relative
        return loose.read_bytes() if loose.is_file() else archive.read(relative)
    sources = {job: read(BOARD.format(job+1)) for job in (0, 4)}
    loose_license = deploy/(PACK+'.dir/section_012.bin')
    licenses = (loose_license.read_bytes() if loose_license.is_file()
                else battle_pack_section(read(PACK), 12))
    return sources, licenses


def prepare_bundle(recipe, sources, licenses, image, image_manifest, hooks, gate, native=NATIVE):
    board = validate_recipe(recipe, sources, licenses)
    if recipe['pair'] != [0, 4]:
        raise ValueError('this pilot supports only native White Mage0 + Knight4')
    if {entry['site'] for entry in hooks} != PILOT_SITES or len(hooks) != len(PILOT_SITES):
        raise ValueError('incomplete pilot hook set')
    required = ('HBX_size', 'HBX_init', 'HBX_core', 'HBX_config', 'HBX_route', 'HB_put')
    exports = image_manifest.get('exports', {})
    if any(type(exports.get(name)) is not int or not 0 <= exports[name] < len(image) for name in required):
        raise ValueError('required adapter export missing or outside image')
    if image_manifest.get('imageSize') != len(image) or image_manifest.get('sha256') != sha(image):
        raise ValueError('adapter image differs from its manifest')
    if gate.get('completeHookSet') is not True or gate.get('sites') != sorted(PILOT_SITES):
        raise ValueError('native integration gate is incomplete')
    fingerprints = {'adapterImageSha256': sha(image), 'layoutSha256': sha(board),
                    'hookPlanSha256': sha(canonical(hooks)),
                    'loaderSha256': sha((native/'loader.lua').read_bytes()),
                    'hooksSha256': sha((native/'hooks.lua').read_bytes())}
    if any(gate.get(key) != value for key, value in fingerprints.items()):
        raise ValueError('native integration gate is stale for these package bytes')
    size = gate.get('adapterSize')
    if type(size) is not int or not 0 < size <= 0x100000:
        raise ValueError('invalid reviewed adapter state size')
    return {'schemaVersion': 1, 'pair': [0, 4], 'preferredBase': PREFERRED_BASE,
            'gameExeSha256': GAME_EXE_SHA256, 'adapterSize': size,
            'requestSize': 48, 'objectCount': 9, 'completeHookSet': True,
            'image': list(image), 'exports': exports,
            'layout': list(struct.unpack_from('<576H', board, 8)), 'hooks': hooks,
            'sourceBoards': recipe['sourceBoards'], 'licenseTableSha256': recipe['licenseTableSha256'],
            **fingerprints}


def package_files(bundle, version, native=NATIVE):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('invalid package version')
    entry = '''-- Ivalice Legacy: Paladin pilot. No save-file redirection or hot unload.
local loader = require("IvaliceLegacyHybrids.loader")
local hooks = require("IvaliceLegacyHybrids.hooks")
local bundle = require("IvaliceLegacyHybrids.bundle")
local handle, retained
local function attempt()
  if handle then
    local ok, ready = pcall(handle.reconfigure)
    if not ok or not ready then
      print("Ivalice Legacy Paladin pilot: save-load configuration unavailable; hybrid contexts refused.")
    end
    return true
  end
  if retained and not retained.patchesRestored then return true end
  local error
  handle, error, retained = loader.install(memory, arg or memory.arg, bundle, hooks, event)
  if handle then
    print("Ivalice Legacy Paladin pilot: native adapter installed for White Mage + Knight.")
  elseif retained and not retained.patchesRestored then
    print("Ivalice Legacy Paladin pilot: FATAL partial hook transaction; exit the game and disable this pilot before continuing. Native allocations retained.")
  else
    print("Ivalice Legacy Paladin pilot: not installed: " .. tostring(error))
  end
  return true
end
if type(event) ~= "table" or type(event.registerEventSync) ~= "function" then
  print("Ivalice Legacy Paladin pilot: synchronous lifecycle API unavailable; not installed.")
  return
end
if event.registerEventSync("onSaveLoad", attempt) ~= true then
  print("Ivalice Legacy Paladin pilot: save-load lifecycle unavailable; not installed.")
  return
end
-- Startup may precede keep/menu initialization. The synchronous save-load
-- callback retries without exposing incomplete hooks or hot-unloading state.
event.registerEventSync("onInitDone", attempt)
attempt()
'''
    readme = f'''# Ivalice Legacy Paladin Pilot {version}

Separate experimental addon for the native White Mage + Knight pair. Both job
orders derive the same authored Paladin board. Other pairs retain normal behavior.
Existing job IDs, license meanings, individual prices, acquired licenses and LP
remain native. New paths through the layout change progression costs and require
design review; this package is not a claim of tested in-game balance.

Install only this pilot archive through Vortex on the intended Legacy profile,
with the game closed. It is separate from the main mod and adds no save redirect.
Do not merge it into the Horizon profile. Preserve existing saves and sidecars.
Runtime preflight refuses changed hook bytes. Never reload/unload it while the
game is running: code and state allocations live until process exit.

Uninstall/rollback: close the game, disable/remove this pilot in Vortex, redeploy,
and verify the four pilot Lua paths listed in the package manifest are absent or restored from their
recorded prior-owner backup. No save migration or license refund is performed
by installation/removal. Do not delete shared x64/scripts folders.

Only files from this package may be removed; any preexisting owner at a pilot
path must be identified and backed up before installation. Together files and
settings, native global board files, executable files on disk and saves are not
part of this package.

Offline validation and source hashes are recorded in manifest.json. Actual
installed-loader behavior, UI rendering and current-campaign play remain
unverified until a coherent in-game observation is recorded.
'''
    files = {'x64/scripts/IvaliceLegacyPaladin.lua': entry.encode(),
             SCRIPT_ROOT+'loader.lua': (native/'loader.lua').read_bytes(),
             SCRIPT_ROOT+'hooks.lua': (native/'hooks.lua').read_bytes(),
             SCRIPT_ROOT+'bundle.lua': ('return '+lua(bundle)+'\n').encode(),
             SCRIPT_ROOT+'README.md': readme.encode(),
             SCRIPT_ROOT+'CHANGELOG.md': f'# {version}\n\nInitial authored Paladin pilot and native per-character board adapter.\n'.encode()}
    manifest = {'name': NAME, 'version': version, 'pair': [0, 4],
                'status': 'uninstalled review candidate; in-game validation pending',
                'gameExeSha256': GAME_EXE_SHA256,
                'adapterImageSha256': bundle['adapterImageSha256'],
                'layoutSha256': bundle['layoutSha256'], 'hookPlanSha256': bundle['hookPlanSha256'],
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
    archive_path, folder = out/(stem+'.zip'), out/stem
    if archive_path.exists() and archive_path.read_bytes() != data:
        raise ValueError('version already exists with different bytes; increment version')
    if folder.exists():
        actual = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        if actual != files:
            raise ValueError('version folder exists with different bytes; increment version')
    out.mkdir(parents=True, exist_ok=True)
    folder.mkdir(exist_ok=True)
    for name, content in files.items():
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive_path.write_bytes(data)
    return archive_path, {**manifest, 'archiveSha256': sha(data),
                          'allFiles': {name: sha(content) for name, content in sorted(files.items())}}


def build(game, recipe_path, gate_path, out, version='0.1.0', native=NATIVE):
    if Path(out).resolve().is_relative_to(Path(game).resolve()):
        raise ValueError('build output must be outside the game installation')
    recipe = json.loads(Path(recipe_path).read_text())
    gate = json.loads(Path(gate_path).read_text())
    sources, licenses = source_inputs(game)
    image = (native/'build/hybrid_adapter.image').read_bytes()
    image_manifest = json.loads((native/'build/adapter-image-manifest.json').read_text())
    bundle = prepare_bundle(recipe, sources, licenses, image, image_manifest,
                            hook_templates(native), gate, native)
    return write_package(*package_files(bundle, version, native), out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', required=True)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--gate', required=True)
    parser.add_argument('--out', default=str(ROOT/'dist'))
    parser.add_argument('--version', default='0.1.0')
    args = parser.parse_args()
    archive, receipt = build(args.game, args.recipe, args.gate, args.out, args.version)
    print(json.dumps({'archive': str(archive), **receipt}, indent=2))


if __name__ == '__main__':
    main()
