import copy
import itertools
import json
import os
import unittest
from pathlib import Path

from ivalice_legacy.hybrid_layout import validate_fused
from ivalice_legacy.job_labels import encode_label

ROOT = Path(__file__).resolve().parents[1]
GAME = os.environ.get('IVALICE_LEGACY_GAME')
DEPLOY = 'mods/deploy/ff12data/ps2data/image/ff12/test_battle/'
STAGED = os.environ.get('IVALICE_LEGACY_STAGED')


def deployed(relative):
    """A deployed file, or its staged replacement when IVALICE_LEGACY_STAGED names a folder of game-relative files
    (a release candidate checked before it is installed)."""
    staged = Path(STAGED)/relative if STAGED else None
    return staged.read_bytes() if staged and staged.is_file() else (Path(GAME)/relative).read_bytes()


class HybridNamesTest(unittest.TestCase):
    def test_one_encodable_unique_name_per_pair(self):
        names = json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))['hybrids']
        pairs = [tuple(h['pair']) for h in names]
        self.assertEqual(sorted(pairs), list(itertools.combinations(range(12), 2)))
        self.assertEqual(len({h['name'] for h in names}), 66)
        self.assertTrue(all(len(encode_label(h['name'])) <= 24 for h in names))
        self.assertEqual(dict((tuple(h['pair']), h['name']) for h in names)[(0, 4)], 'Paladin')


class HybridLicenseMovesTest(unittest.TestCase):
    """BaseJobs 0.7.0 (0.1.35): Telekinesis on Warrior (7), Protectga/Shellga and Dispel/Stona on White Mage (0) only,
    Achilles on Gunner (2). A hybrid holds a license exactly when one of its parents does."""

    def test_moved_licenses_follow_their_new_owner(self):
        boards = json.loads((ROOT/'patches/hybrid_boards.json').read_text(encoding='utf-8'))
        self.assertEqual(boards['basejobs'], '0.7.0')
        owners = {299: {7}, 210: {0}, 186: {0}, 286: {2}}
        for recipe in boards['boards']:
            ids = {c['licenseId'] for c in recipe['cells']}
            for lid, jobs in owners.items():
                with self.subTest(pair=recipe['pair'], license=lid):
                    self.assertEqual(lid in ids, bool(jobs & set(recipe['pair'])))


@unittest.skipUnless(GAME and (Path(GAME)/DEPLOY).is_dir(), 'set IVALICE_LEGACY_GAME with deployed BaseJobs boards')
class HybridBoardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {j: deployed(DEPLOY+f'in/binaryfile/board_{j+1}.bin') for j in range(12)}
        cls.licenses = deployed(DEPLOY+'us/binaryfile/battle_pack.bin.dir/section_012.bin')
        cls.boards = json.loads((ROOT/'patches/hybrid_boards.json').read_text(encoding='utf-8'))
        cls.names = {tuple(h['pair']): h['name'] for h in
                     json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))['hybrids']}

    def test_all_66_boards_are_safe_fusions(self):
        for recipe in self.boards['boards']:
            pair = tuple(recipe['pair'])
            with self.subTest(pair=pair):
                raw = validate_fused(recipe, {j: self.sources[j] for j in pair}, self.licenses)
                self.assertEqual(len(raw), 1160)
                self.assertEqual(recipe['name'], self.names[pair])

    def test_gate_pins_these_layouts(self):
        import struct
        from ivalice_legacy.hybrid_boards import layouts_digest
        gate = json.loads((ROOT/'native/hybrid_boards/hybrid-boards-gate.json').read_text())
        entries = []
        for recipe in self.boards['boards']:
            pair = tuple(recipe['pair'])
            raw = validate_fused(recipe, {j: self.sources[j] for j in pair}, self.licenses)
            entries.append({'pair': list(pair), 'layout': list(struct.unpack_from('<576H', raw, 8)),
                            'name': list(encode_label(self.names[pair]))})
        self.assertEqual(gate['layoutsSha256'], layouts_digest(entries))
        self.assertEqual((gate['specialLicensePairs'], gate['specialLicenseChecks']), (66, 6666))

    def test_paladin_is_the_approved_board(self):
        approved = json.loads((ROOT/'patches/paladin_hybrid_v2.json').read_text(encoding='utf-8'))
        paladin = next(r for r in self.boards['boards'] if r['pair'] == [0, 4])
        self.assertEqual(paladin['boardSha256'], approved['boardSha256'])

    def test_builder_refuses_incomplete_board_set(self):
        from ivalice_legacy.hybrid_boards import prepare_bundle
        names = json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))
        partial = copy.deepcopy(self.boards)
        partial['boards'].pop()
        with self.assertRaisesRegex(ValueError, '66'):
            prepare_bundle(partial, names, self.sources, self.licenses, b'', {}, [], {})


if __name__ == '__main__':
    unittest.main()
