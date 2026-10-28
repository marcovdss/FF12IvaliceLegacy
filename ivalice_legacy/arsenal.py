"""Build the separate Arsenal package: weapon, armour and accessory field changes (battle_pack section 13)."""
import hashlib
import json

VERSION = '0.4.2'
PACK = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'
SECTION = PACK + '.dir/section_013.bin'
# Equipment tables written by earlier Arsenal releases, which this build replaces.
PREVIOUS = {'0.1.0': 'a45f3661bcd121cfbd0a334a4372d8c6361d749f2233a155bdefda55cd4a8152',
            '0.2.0': 'af3929a4ca16febdaa0e0359abfb743c226228c0ea786923d833a7baf0c0e1cc',
            '0.2.1': '2dc7970c6d1edb49e3d38f536fa79fcbf2b5dbfcc5ab93216650b3bebd5ee857',
            '0.3.0': '0449d565afd1d26a9bf8ba280f71b9b8f340e9c6d545b4432cc3588ff3fea7ca',
            '0.3.1': '831f5a180df7637b77d906e3df913513a4f6e0999e0ca4d5317305507f6fe179',
            '0.4.0': 'eb10ed5edfd46dd89797e1e18ba9ef92e009c9816ad44928b063243920c1ea8b',
            '0.4.1': '06e25902ed56a41b2edfc63e096d90aee127776bce49d26a66374436235ef5b1'}
NAMES = PACK + '.dir/section_061.bin.dir/section_001.bin'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def build(game, out):
    from pathlib import Path
    from .__main__ import ROOT, PREFIX, write_package
    from .equipment import equipment
    from .vbf import VBF
    game = Path(game)
    patch = json.loads((ROOT/'patches/arsenal.json').read_text(encoding='utf-8'))
    require(patch['file'] == PACK and patch['section'] == 13, 'Unexpected Arsenal patch target')
    archive = VBF(game/'FFXII_TZA.vbf')
    files = equipment(archive.read, patch)
    result = files[SECTION]
    # A deployed equipment table must be the native one or an Arsenal release's output.
    deploy = game/'mods/deploy/ff12data'
    loose = deploy/SECTION
    if loose.exists():
        known = {patch['sourceSectionSha256'], sha(result), *PREVIOUS.values()}
        require(loose.is_file() and sha(loose.read_bytes()) in known,
                'Another mod replaces the equipment table (section 13)')
    require(not (deploy/PACK).is_file(), 'A whole battle_pack.bin replacement is deployed')
    # Accessory names (word table sub-table 1); other sub-tables stay with their owners.
    from .formats import battle_pack_section
    from .item_names import item_names
    files[NAMES] = item_names(battle_pack_section(archive.read(PACK), 61), patch['equipmentNames'])
    require(not (deploy/(PACK+'.dir/section_061.bin')).exists(), 'A whole word-table replacement is deployed')
    if (deploy/NAMES).exists():
        require((deploy/NAMES).read_bytes() == files[NAMES], 'Another mod replaces the equipment names')
    files = {PREFIX+key: value for key, value in files.items()}
    docs = 'IvaliceLegacy-Arsenal/'
    for name in ('README.md', 'CHANGELOG.md'):
        files[docs+name] = (ROOT/'package_arsenal'/name).read_bytes()
    metadata = {'status': 'Experimental candidate', 'version': VERSION,
                'recipeSha256': sha((ROOT/'patches/arsenal.json').read_bytes()),
                'source': {'file': PACK, 'fileSha256': patch['sourceFileSha256'], 'section': 13,
                           'sectionSha256': patch['sourceSectionSha256']},
                'items': [{'contentId': i['contentId'], 'name': i['name'], 'set': i['set']} for i in patch['items']]}
    files[docs+'manifest.json'] = (json.dumps({**metadata, 'files': {k: sha(v) for k, v in sorted(files.items())},
                                             'manifestExcludesOwnHash': True}, indent=2)+'\n').encode()
    return write_package(files, out, f'IvaliceLegacy-Arsenal-{VERSION}', 'Ivalice Legacy Arsenal', VERSION, metadata)
