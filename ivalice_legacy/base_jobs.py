"""Build the separate twelve-base-job candidate from source-pinned game files.

No installation, save migration or Together modification is performed.
"""
from pathlib import PurePosixPath
import hashlib
import json
import struct

BOARD = 'ps2data/image/ff12/test_battle/in/binaryfile/board_{}.bin'
SPECIAL = frozenset(range(18, 31)) | {0, 1, 2, 3, 360}
HEADER = b'licd\x18\x00\x18\x00'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(value):
    require(isinstance(value, str) and value not in ('', '.') and '\\' not in value and ':' not in value,
            'Invalid resource path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and str(path) == value,
            'Invalid resource path')
    return value


def encode_board(job):
    cells = {}
    ids = set()
    for cell in job['cells']:
        x, y, value = cell['x'], cell['y'], cell['licenseId']
        require(all(type(v) is int for v in (x, y, value)), 'Noninteger board cell')
        require(0 <= x < 24 and 0 <= y < 24 and 0 <= value <= 360,
                'Out-of-range board cell')
        require((x, y) not in cells and value not in ids, 'Duplicate board cell/license')
        cells[x, y] = value
        ids.add(value)
    require(SPECIAL <= ids and not (set(range(4, 18)) & ids) and 31 in ids,
            'Incomplete native special choices or Essentials')
    graph = {v: [] for v in ids}
    for (x, y), value in cells.items():
        graph[value] = [cells[p] for p in ((x-1,y), (x+1,y), (x,y-1), (x,y+1)) if p in cells]
    ordinary = ids - SPECIAL
    core = {31}
    pending = [31]
    for value in pending:
        for neighbor in graph[value]:
            if neighbor in ordinary and neighbor not in core:
                core.add(neighbor)
                pending.append(neighbor)
    # Compact regions have loops, as native boards do. Ordinary licenses outside the core are islands: each one
    # touches only its own Esper or Quickening gate, so it opens only for the character who owns that gate.
    gated = set()
    for value in ordinary - core:
        gates = graph[value]
        require(len(gates) == 1 and gates[0] in SPECIAL and gates[0] != 360 and gates[0] not in gated,
                'Island license must touch exactly one gate of its own')
        gated.add(gates[0])
    islands = {(graph[value][0], value) for value in ordinary - core}
    require(islands == {(i['gateId'], i['licenseId']) for i in job.get('islands', [])},
            'Islands differ from the recipe')
    for value in SPECIAL:
        # Each Esper/Quickening touches exactly one core license (plus its island, if it gates one); it never joins
        # two parts of the core. Plain ones may touch each other. The Second Board stays a plain leaf.
        require(len([n for n in graph[value] if n in core]) == 1
                and len([n for n in graph[value] if n in ordinary - core]) == (value in gated)
                and (value != 360 or len(graph[value]) == 1),
                'Special license must be an edge tile with one core neighbour')
    grid = [65535]*576
    for (x, y), value in cells.items():
        grid[y*24+x] = value
    result = HEADER + struct.pack('<576H', *grid)
    require(sha(result) == job['candidateSha256'], 'Authored board hash mismatch')
    return result


def apply_boards(read, recipe, licenses):
    """Source-check every slot before returning any replacement files."""
    require(recipe['schemaVersion'] == 1, 'Unsupported base-job recipe')
    require(sha(licenses) == recipe['licenseTableSha256'], 'Unsupported license table')
    jobs = recipe['jobs']
    require(len(jobs) == 12 and {j['nativeSlot'] for j in jobs} == set(range(12)),
            'Expected each of the twelve native slots exactly once')
    require(all(type(j['nativeSlot']) is int for j in jobs), 'Noninteger native slot')
    require(len({j['name'] for j in jobs}) == 12 and len({j['key'] for j in jobs}) == 12,
            'Duplicate base-job identity')
    result = {}
    for job in jobs:
        relative = BOARD.format(job['nativeSlot']+1)
        require(sha(read(relative)) == job['sourceSha256'], f'Unsupported source board: {relative}')
        result[relative] = encode_board(job)
    return result


def build(game, out):
    from pathlib import Path
    from .__main__ import ROOT, PREFIX, write_package
    from .formats import battle_pack_section
    from .vbf import VBF
    game = Path(game)
    recipe = json.loads((ROOT/'patches/base_jobs.json').read_text(encoding='utf-8'))
    require(recipe['version'] == '0.4.0', 'Unsupported base-job version')
    require(sha((game/'x64/FFXII_TZA.exe').read_bytes()) == recipe['gameExeSha256'],
            'Unsupported game executable')
    archive = VBF(game/'FFXII_TZA.vbf')
    deploy = game/'mods/deploy/ff12data'
    # Rebuilding from our own exact deployed candidate is safe: all inputs
    # still come from the pinned archive, never from the loose replacements.
    own_outputs = {BOARD.format(j['nativeSlot']+1): j['candidateSha256'] for j in recipe['jobs']}
    previous_outputs = {BOARD.format(j['nativeSlot']+1): j['previousCandidateSha256'] for j in recipe['jobs']}
    from .job_labels import apply_text_patch
    text_patches = [*recipe['labels'], recipe['descriptions']]
    for patch in text_patches:
        relative = safe_path(patch['archivePath'])
        own_outputs[relative] = sha(apply_text_patch(archive.read(relative), patch))
    def read(relative):
        relative = safe_path(relative)
        baseline = archive.read(relative)
        loose = deploy/relative
        if loose.exists():
            require(loose.is_file() and (loose.read_bytes() == baseline or sha(loose.read_bytes()) in
                    {own_outputs.get(relative), previous_outputs.get(relative)}),
                    f'Conflicting deployed replacement: {relative}')
        require(not (deploy/(relative+'.dir')).exists(), f'Conflicting deployed section replacements: {relative}')
        return baseline
    pack = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'
    # Other Legacy modules replace unrelated battle-pack sections intentionally.
    packed = archive.read(pack)
    licenses = battle_pack_section(packed, 12)
    from .license_lp import apply_license_lp
    from .license_content import apply_license_contents, license_names, NAMES_SUBTABLE
    # Thirteen legendary weapons sit alone on Esper islands: four bundled licenses give their partner weapon to the
    # job's previous tier, and those four licenses are renamed after the zodiac weapon they now hold.
    updated_licenses = apply_license_contents(apply_license_lp(licenses, recipe['licenseLpPatch'], recipe['jobs']),
                                              recipe['licenseContentPatch'])
    names = license_names(battle_pack_section(packed, 61), recipe['licenseNamePatch'])
    names_path = f'{pack}.dir/section_061.bin.dir/section_{NAMES_SUBTABLE:03}.bin'
    require(not (deploy/(pack+'.dir/section_061.bin')).exists(), 'Conflicting deployed word table')
    loose_names = deploy/names_path
    if loose_names.exists():
        require(loose_names.is_file() and loose_names.read_bytes() == names, 'Conflicting deployed license names')
    full_loose = deploy/pack
    if full_loose.is_file():
        require(battle_pack_section(full_loose.read_bytes(), 12) in (licenses, updated_licenses),
                'Conflicting deployed license table')
    loose_license = deploy/(pack+'.dir/section_012.bin')
    if loose_license.exists():
        # The previous BaseJobs version's own table is a known, replaceable winner.
        require(loose_license.is_file() and (loose_license.read_bytes() in (licenses, updated_licenses) or
                sha(loose_license.read_bytes()) == recipe['licenseLpPatch'].get('previousResultSha256')),
                'Conflicting deployed license section')
    files = apply_boards(read, recipe, licenses)
    # Additional native UI resources are source-pinned by the label recipe.
    from .job_labels import apply_labels
    labels = apply_labels(read, text_patches)
    require(not set(files).intersection(labels), 'Duplicate board/label output resource')
    files.update(labels)
    files[pack+'.dir/section_012.bin'] = updated_licenses
    files[names_path] = names
    files = {PREFIX+key: value for key, value in files.items()}
    docs = 'IvaliceLegacy-BaseJobs/'
    files[docs+'README.md'] = (ROOT/'package_base_jobs/README.md').read_bytes()
    files[docs+'CHANGELOG.md'] = (ROOT/'package_base_jobs/CHANGELOG.md').read_bytes()
    metadata = {'status': 'Offline experimental candidate; not installed or validated in-game',
                'version': '0.4.0',
                'recipeSha256': sha((ROOT/'patches/base_jobs.json').read_bytes()),
                'licenseLpChanges': recipe['licenseLpPatch']['changes'],
                'nativeSlots': [{k: j[k] for k in ('nativeSlot','sourceName','name')} for j in recipe['jobs']],
                'limitations': ['Existing slot assignments and acquired licenses persist; no automatic respec.',
                                'Do not combine with the separate Paladin pilot or other board replacements.',
                                'UI, saved-campaign behavior and combat balance require integration validation.']}
    files[docs+'manifest.json'] = (json.dumps({**metadata, 'files': {key: sha(value) for key, value in sorted(files.items())},
                                             'manifestExcludesOwnHash': True}, indent=2)+'\n').encode()
    return write_package(files, out,
                         'IvaliceLegacy-BaseJobs-0.4.0', 'Ivalice Legacy Base Jobs Candidate', '0.4.0',
                         metadata)
