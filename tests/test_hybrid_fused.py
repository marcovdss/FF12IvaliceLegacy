import copy
import json
import os
import unittest
from pathlib import Path

from ivalice_legacy.hybrid_layout import validate_fused

ROOT = Path(__file__).resolve().parents[1]
GAME = os.environ.get('IVALICE_LEGACY_GAME')
DEPLOY = 'mods/deploy/ff12data/ps2data/image/ff12/test_battle/'


@unittest.skipUnless(GAME and (Path(GAME)/DEPLOY).is_dir(), 'set IVALICE_LEGACY_GAME with deployed BaseJobs boards')
class FusedPaladinTest(unittest.TestCase):
    def setUp(self):
        base = Path(GAME)/DEPLOY
        self.sources = {job: (base/f'in/binaryfile/board_{job+1}.bin').read_bytes() for job in (0, 4)}
        self.licenses = (base/'us/binaryfile/battle_pack.bin.dir/section_012.bin').read_bytes()
        self.recipe = json.loads((ROOT/'patches/paladin_hybrid_v2.json').read_text(encoding='utf-8'))

    def bad(self, mutate, message):
        recipe = copy.deepcopy(self.recipe)
        recipe.pop('boardSha256', None)
        mutate(recipe)
        with self.assertRaisesRegex(ValueError, message):
            validate_fused(recipe, self.sources, self.licenses)

    def cell(self, recipe, lid):
        return next(c for c in recipe['cells'] if c['licenseId'] == lid)

    def test_recipe_is_whole_union_and_safe(self):
        raw = validate_fused(self.recipe, self.sources, self.licenses)
        self.assertEqual(len(raw), 1160)
        self.assertEqual(len(self.recipe['cells']), 163)

    def test_rejects_missing_or_duplicate_license(self):
        self.bad(lambda r: r['cells'].pop(), 'missing')
        def dup(r):
            r['cells'][1]['licenseId'] = r['cells'][0]['licenseId']
        self.bad(dup, 'duplicate')

    def test_rejects_stale_source_or_table_pin(self):
        def pin(r):
            r['sourceBoards'][0]['sha256'] = '0'*64
        self.bad(pin, 'pin mismatch')
        def table(r):
            r['licenseTableSha256'] = '0'*64
        self.bad(table, 'pin mismatch')

    def test_rejects_island_reachable_without_its_esper(self):
        # Put Belias's island (Yagyu Darkblade, 52) next to Essentials' core instead of behind Belias.
        def move(r):
            island = self.cell(r, 52)
            island['x'], island['y'] = 21, 12  # empty; its only neighbour is HP Lore (20,12) in the core
        self.bad(move, 'Esper islands differ')

    def test_rejects_special_joining_two_core_tiles(self):
        def move(r):
            q = self.cell(r, 0)              # Quickening 1 into the middle of the gear lanes' top edge
            q['x'], q['y'] = 8, 5            # touches Heavy armour (7,5) and Shields (8,6)
        self.bad(move, 'edge tile')

    def test_rejects_wrong_board_hash(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['boardSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'board hash'):
            validate_fused(recipe, self.sources, self.licenses)


if __name__ == '__main__':
    unittest.main()
