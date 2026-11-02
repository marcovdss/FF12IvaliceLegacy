"""Exact checks for the Estersand hunts battle-logic overlay (dst_a section 3) in the main package's area AI."""
from pathlib import Path
import copy
import json
import os
import struct
import tempfile
import unittest
from ivalice_legacy.enemy_ai_bank import Bank, RULE, enemy_ai_bank
from ivalice_legacy.formats import ard_section
from ivalice_legacy.patches import enemy_ai, area_ai
from ivalice_legacy.vbf import VBF
ROOT = Path(__file__).resolve().parents[1]
AREA_AI = json.loads((ROOT / 'patches/area_ai.json').read_text())
PATCH = next(a for a in AREA_AI['areas'] if a['file'].endswith('/dst_a.ard'))
PILOT = json.loads((ROOT / 'patches/rampager_pilot.json').read_text())


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source checks')
class EstersandHuntsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = VBF(Path(os.environ['IVALICE_LEGACY_GAME']) / 'FFXII_TZA.vbf').read(PATCH['file'])
        cls.source = Bank.from_bytes(ard_section(cls.raw, 3))
        cls.result = Bank.from_bytes(next(iter(enemy_ai_bank(lambda _: cls.raw, PATCH).values())))

    def rule(self, script, row):
        return RULE.unpack(self.result.scripts[script].rules[row].to_bytes())

    def test_only_cactoid_and_rampager_scripts_change(self):
        changed = [i for i, (a, b) in enumerate(zip(self.source.scripts, self.result.scripts)) if a.to_bytes() != b.to_bytes()]
        self.assertEqual(changed, [6, 7, 8, 9])

    def test_rampager_matches_the_pilot(self):
        pilot = Bank.from_bytes(next(iter(enemy_ai(lambda _: self.raw, PILOT).values())))
        self.assertEqual(pilot.scripts[9].to_bytes(), self.result.scripts[9].to_bytes())

    def test_no_1000_needles_left_and_ordinary_attack_used(self):
        for s in (6, 7, 8):
            self.assertNotIn(179, [r.to_bytes()[0] | r.to_bytes()[1] << 8 for r in self.result.scripts[s].rules])
        self.assertEqual(self.rule(6, 32)[0], 33423)
        self.assertEqual(self.rule(7, 26 + 1)[0], 33423)  # one rule inserted above it in group 0
        self.assertEqual(self.rule(8, 21 + 1)[0], 33424)

    def test_cure_is_once_per_slot(self):
        for s, flag in ((7, 28), (8, 29)):
            cure, enable = self.rule(s, 3), self.rule(s, 4)
            self.assertEqual(cure[:3], (0, 0, 18432))
            self.assertEqual(cure[3:9], (4, 3842, 0, 4, 14083, flag))
            self.assertEqual(enable[:3], (16674, flag, 2048))
            self.assertEqual(self.result.scripts[s].group_sizes[0], self.source.scripts[s].group_sizes[0] + 1)

    def test_refuses_changed_source(self):
        altered = bytearray(self.raw)
        altered[-1] ^= 1
        with self.assertRaises(ValueError):
            enemy_ai_bank(lambda _: bytes(altered), PATCH)
        bad = copy.deepcopy(PATCH)
        bad['resultSectionSha256'] = '0' * 64
        with self.assertRaises(ValueError):
            enemy_ai_bank(lambda _: self.raw, bad)

    def test_area_ai_refuses_two_entries_for_one_file(self):
        self.assertEqual(len(area_ai(lambda _: self.raw, {'areas': [PATCH]})), 1)
        with self.assertRaises(ValueError):
            area_ai(lambda _: self.raw, {'areas': [PATCH, copy.deepcopy(PATCH)]})

    def test_main_package_carries_it_and_rampager(self):
        from ivalice_legacy.__main__ import build
        with tempfile.TemporaryDirectory() as out:
            _, manifest = build(os.environ['IVALICE_LEGACY_GAME'], out)
            self.assertEqual(manifest['files']['mods/deploy/ff12data/' + PATCH['file'] + '.dir/section_003.bin'],
                             PATCH['resultSectionSha256'])

if __name__ == '__main__':
    unittest.main()
