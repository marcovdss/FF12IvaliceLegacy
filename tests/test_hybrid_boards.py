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


class HybridNamesTest(unittest.TestCase):
    def test_one_encodable_unique_name_per_pair(self):
        names = json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))['hybrids']
        pairs = [tuple(h['pair']) for h in names]
        self.assertEqual(sorted(pairs), list(itertools.combinations(range(12), 2)))
        self.assertEqual(len({h['name'] for h in names}), 66)
        self.assertTrue(all(len(encode_label(h['name'])) <= 24 for h in names))
        self.assertEqual(dict((tuple(h['pair']), h['name']) for h in names)[(0, 4)], 'Paladin')


@unittest.skipUnless(GAME and (Path(GAME)/DEPLOY).is_dir(), 'set IVALICE_LEGACY_GAME with deployed BaseJobs boards')
class HybridBoardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Path(GAME)/DEPLOY
        cls.sources = {j: (base/f'in/binaryfile/board_{j+1}.bin').read_bytes() for j in range(12)}
        cls.licenses = (base/'us/binaryfile/battle_pack.bin.dir/section_012.bin').read_bytes()
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
