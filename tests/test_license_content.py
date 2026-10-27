"""Moves of equipment between license nodes and in-place license renames."""
import copy
import json
import os
import struct
import unittest
from pathlib import Path

from ivalice_legacy.license_content import apply_license_contents, license_names, sha

ROOT = Path(__file__).resolve().parents[1]
RECIPE = json.loads((ROOT/'patches/base_jobs.json').read_text(encoding='utf-8'))


class LicenseContentTest(unittest.TestCase):
    def setUp(self):
        raw = bytearray(32+361*24)
        raw[:4] = b'st2e'
        struct.pack_into('<III', raw, 4, 361, 24, 32)
        self.patch = copy.deepcopy(RECIPE['licenseContentPatch'])
        for i in range(361):
            raw[32+24*i+5] = 1
            struct.pack_into('<8H', raw, 32+24*i+8, *[0xFFFF]*8)
        for c in self.patch['changes']:
            struct.pack_into('<8H', raw, 32+24*c['licenseId']+8, *(c['before']+[0xFFFF]*(8-len(c['before']))))
        self.raw = bytes(raw)
        result = bytearray(raw)
        for c in self.patch['changes']:
            struct.pack_into('<8H', result, 32+24*c['licenseId']+8, *(c['after']+[0xFFFF]*(8-len(c['after']))))
        self.patch['resultSha256'] = sha(bytes(result))

    def test_zodiac_weapons_stand_alone_and_partners_move_down_a_tier(self):
        result = apply_license_contents(self.raw, self.patch)
        contents = lambda lid: [v for v in struct.unpack_from('<8H', result, 32+24*lid+8) if v != 0xFFFF]
        self.assertEqual([contents(i) for i in (52, 95, 76, 330)], [[4267], [4268], [4272], [4290]])
        self.assertIn(4137, contents(336))
        self.assertIn(4113, contents(329))

    def test_items_cannot_vanish_or_appear(self):
        self.patch['changes'][1]['after'] = self.patch['changes'][1]['before']
        with self.assertRaisesRegex(ValueError, 'not appear or vanish'):
            apply_license_contents(self.raw, self.patch)

    def test_unexpected_contents_and_command_licenses_rejected(self):
        patch = copy.deepcopy(self.patch)
        patch['changes'][0]['before'] = [1]
        with self.assertRaisesRegex(ValueError, 'Unexpected contents'):
            apply_license_contents(self.raw, patch)
        raw = bytearray(self.raw)
        raw[32+24*self.patch['changes'][0]['licenseId']+5] = 9
        with self.assertRaisesRegex(ValueError, 'Only equipment'):
            apply_license_contents(bytes(raw), self.patch)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class LicenseNameTest(unittest.TestCase):
    def test_four_names_change_in_their_slots_only(self):
        from ivalice_legacy.vbf import VBF
        from ivalice_legacy.formats import battle_pack_section
        archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')
        section = battle_pack_section(archive.read('ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'), 61)
        result = license_names(section, RECIPE['licenseNamePatch'])
        bounds = [struct.unpack_from('<I', section, 4+4*i)[0] for i in range(16)]
        original = section[bounds[3]:bounds[4]]
        self.assertEqual(len(result), len(original))
        slots = [struct.unpack_from('<I', original, 4+4*i)[0] for i in range(362)] + [len(original)]
        touched = {i for i, (a, b) in enumerate(zip(original, result)) if a != b}
        allowed = set().union(*(range(slots[c['licenseId']], slots[c['licenseId']+1])
                                for c in RECIPE['licenseNamePatch']['changes']))
        self.assertTrue(touched and touched <= allowed)
        patch = copy.deepcopy(RECIPE['licenseNamePatch'])
        patch['changes'][0]['replacement'] = 'A much longer name than fits'
        with self.assertRaises(ValueError):
            license_names(section, patch)


if __name__ == '__main__':
    unittest.main()
