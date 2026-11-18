"""patches/arsenal_0160.json: the gear recipe that produces the shipped 0.1.60 battle_pack section 13.

patches/arsenal.json is the older 0.7.2 experimental Arsenal package recipe (its own tests pin it); this recipe is the 0.1.41
equipment recipe plus the seven approved 2026-11-16 gear changes. The static checks need no game; the build check runs with
IVALICE_LEGACY_GAME."""
import json
import hashlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ivalice_legacy import equipment as eq

RECIPE = json.loads((ROOT / 'patches/arsenal_0160.json').read_text(encoding='utf-8'))
SECTION_013_0160 = '7adb8cb5528441144adbf2e16080a84de3f59cedd6b2969542a88c0c89f97155'
ITEMS = {i['contentId']: i for i in RECIPE['items']}
BLOCKS = {b['attributeIndex']: b for b in RECIPE['attributes']}
CLAYMORE, ULTIMA_BLADE, GENJI_GLOVES, HERMES, PIXIE, ELMDORE_FANG, MIRACLE_SHOES = 4114, 4118, 4452, 4473, 4476, 4464, 4477
ENSANGUINED = 4315


class Arsenal0160Static(unittest.TestCase):
    def test_result_pin(self):
        self.assertEqual(RECIPE['resultSectionSha256'], SECTION_013_0160)

    def test_greatsword_trim(self):
        self.assertEqual(ITEMS[CLAYMORE]['set']['attack'], [83, 63])
        self.assertEqual(ITEMS[ULTIMA_BLADE]['set']['attack'], [109, 57])

    def test_genji_gloves_keep_vanilla_combo_boost(self):
        self.assertNotIn(GENJI_GLOVES, ITEMS)

    def test_accessory_blocks(self):
        by_user = {u: b for b in BLOCKS.values() for u in b['users']}
        hermes, pixie, fang, miracle = (by_user[x] for x in (HERMES, PIXIE, ELMDORE_FANG, MIRACLE_SHOES))
        for block, user in ((hermes, HERMES), (pixie, PIXIE), (fang, ELMDORE_FANG), (miracle, MIRACLE_SHOES)):
            self.assertEqual(block['users'], [user], 'a changed block must belong to one item only')
        self.assertEqual(hermes['set']['status'], [['Haste'], []])      # no Equip Haste: the opening Haste is a runtime stage
        self.assertEqual(pixie['set']['weak'], [[], ['Wind']])
        self.assertNotIn('status', fang['set'])                          # no Equip Sap
        self.assertEqual(miracle['set']['status'], [[], ['Protect', 'Shell']])   # Equip Slow gone

    def test_ensanguined_shield_never_harms_its_wearer(self):
        # Marco 2026-11-16: a downside never damages or debuffs the wielder (2026-11-17 audit); evade 60 / magick evade 0 stays
        by_user = {u: b for b in BLOCKS.values() for u in b['users']}
        self.assertEqual(by_user[ENSANGUINED]['set']['status'], [['Poison', 'Sap', 'Slow'], []])
        self.assertEqual(ITEMS[ENSANGUINED]['set']['evade'], [90, 60])

    def test_every_change_is_a_before_after_pin(self):
        for item in RECIPE['items']:
            for name, change in item['set'].items():
                with self.subTest(item=item['name'], field=name):
                    self.assertTrue(isinstance(change, list) and len(change) == 2)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'real-table build runs with IVALICE_LEGACY_GAME')
class Arsenal0160Build(unittest.TestCase):
    def test_recipe_builds_the_shipped_section(self):
        from ivalice_legacy.vbf import VBF
        vbf = VBF(Path(os.environ['IVALICE_LEGACY_GAME']) / 'FFXII_TZA.vbf')
        result = eq.equipment(vbf.read, RECIPE)[RECIPE['file'] + '.dir/section_013.bin']
        self.assertEqual(hashlib.sha256(result).hexdigest(), SECTION_013_0160)


if __name__ == '__main__':
    unittest.main()
