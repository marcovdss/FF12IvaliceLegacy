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
    for value in SPECIAL:
        require(len(graph[value]) == 1 and graph[value][0] not in SPECIAL,
                'Special license must be an ordinary pendant leaf')
    ordinary = ids - SPECIAL
    visited = {31}
    pending = [31]
    for value in pending:
        for neighbor in graph[value]:
            if neighbor in ordinary and neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    require(visited == ordinary, 'Disconnected ordinary curriculum')
    require(sum(len([n for n in graph[v] if n in ordinary]) for v in ordinary) == 2*(len(ordinary)-1),
            'Ordinary curriculum has a shortcut/cycle')
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
    require(sha((game/'x64/FFXII_TZA.exe').read_bytes()) == recipe['gameExeSha256'],
            'Unsupported game executable')
    archive = VBF(game/'FFXII_TZA.vbf')
    deploy = game/'mods/deploy/ff12data'
    def read(relative):
        relative = safe_path(relative)
        baseline = archive.read(relative)
        loose = deploy/relative
        if loose.exists():
            require(loose.is_file() and loose.read_bytes() == baseline,
                    f'Conflicting deployed replacement: {relative}')
        require(not (deploy/(relative+'.dir')).exists(), f'Conflicting deployed section replacements: {relative}')
        return baseline
    pack = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'
    # Other Legacy modules replace unrelated battle-pack sections intentionally.
    packed = archive.read(pack)
    licenses = battle_pack_section(packed, 12)
    full_loose = deploy/pack
    if full_loose.is_file():
        require(battle_pack_section(full_loose.read_bytes(), 12) == licenses,
                'Conflicting deployed license table')
    loose_license = deploy/(pack+'.dir/section_012.bin')
    if loose_license.exists():
        require(loose_license.is_file() and loose_license.read_bytes() == licenses,
                'Conflicting deployed license section')
    files = apply_boards(read, recipe, licenses)
    # Additional native UI resources are source-pinned by the label recipe.
    from .job_labels import apply_labels
    labels = apply_labels(read, recipe['labels'])
    require(not set(files).intersection(labels), 'Duplicate board/label output resource')
    files.update(labels)
    files = {PREFIX+key: value for key, value in files.items()}
    docs = 'IvaliceLegacy-BaseJobs/'
    files[docs+'README.md'] = (ROOT/'package_base_jobs/README.md').read_bytes()
    files[docs+'CHANGELOG.md'] = (ROOT/'package_base_jobs/CHANGELOG.md').read_bytes()
    metadata = {'status': 'Offline experimental candidate; not installed or validated in-game',
                'recipeSha256': sha((ROOT/'patches/base_jobs.json').read_bytes()),
                'nativeSlots': [{k: j[k] for k in ('nativeSlot','sourceName','name')} for j in recipe['jobs']],
                'limitations': ['Existing slot assignments and acquired licenses persist; no automatic respec.',
                                'Do not combine with the separate Paladin pilot or other board replacements.',
                                'UI, saved-campaign behavior and combat balance require integration validation.']}
    files[docs+'manifest.json'] = (json.dumps({**metadata, 'files': {key: sha(value) for key, value in sorted(files.items())},
                                             'manifestExcludesOwnHash': True}, indent=2)+'\n').encode()
    return write_package(files, out,
                         'IvaliceLegacy-BaseJobs-0.1.0', 'Ivalice Legacy Base Jobs Candidate', '0.1.0',
                         metadata)
