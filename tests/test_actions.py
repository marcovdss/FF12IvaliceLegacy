"""Action-table field edits on a synthetic section 14; the real recipe is checked against the game separately."""
import hashlib
import json
import struct
import unittest
from pathlib import Path

from ivalice_legacy import actions

ROOT = Path(__file__).resolve().parents[1]
FILE = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'


def table():
    size, entry = 60, 32
    data = bytearray(entry + actions.ACTION_COUNT * size + 8)
    data[:4] = b'st2e'
    struct.pack_into('<IH', data, 4, actions.ACTION_COUNT, size)
    struct.pack_into('<I', data, 12, entry)
    data[entry + 150 * size + 9] = 25  # Attack's charge time
    return bytes(data)


def pack(section):
    # battle_pack.bin with sections 0..14, section 14 = the table, the others empty.
    count = 15
    offsets, body = [], b''
    start = 4 + 4 * (count + 1)
    for i in range(count):
        offsets.append(start + len(body))
        if i == 14:
            body += section
    offsets.append(start + len(body))
    return struct.pack(f'<I{count + 1}I', count, *offsets) + body


class ActionFieldsTests(unittest.TestCase):
    def setUp(self):
        self.section = table()
        self.pack = pack(self.section)
        self.patch = {'file': FILE, 'section': 14, 'sourceFileSha256': hashlib.sha256(self.pack).hexdigest(),
                      'sourceSectionSha256': hashlib.sha256(self.section).hexdigest(),
                      'actions': [{'id': 150, 'name': 'Attack', 'set': {'aoeRange': [0, 4],
                                                                        'aoePositioningRange': [0, 180]}}]}
        expected = bytearray(self.section)
        expected[32 + 150 * 60 + 6], expected[32 + 150 * 60 + 7] = 4, 180
        self.expected = bytes(expected)
        self.patch['resultSectionSha256'] = hashlib.sha256(self.expected).hexdigest()

    def apply(self, patch=None):
        return actions.action_fields(lambda name: self.pack, patch or self.patch)

    def test_changes_only_the_listed_bytes(self):
        files = self.apply()
        self.assertEqual(list(files), [FILE + '.dir/section_014.bin'])
        result = files[FILE + '.dir/section_014.bin']
        self.assertEqual(result, self.expected)
        self.assertEqual(sum(a != b for a, b in zip(self.section, result)), 2)

    def test_rejects_wrong_before_value_and_unknown_fields(self):
        self.patch['actions'][0]['set']['aoeRange'] = [1, 4]
        with self.assertRaisesRegex(ValueError, 'unexpected value'):
            self.apply()
        self.patch['actions'][0]['set'] = {'firstFlags': [0, 1]}
        with self.assertRaisesRegex(ValueError, 'unsupported field'):
            self.apply()

    def test_rejects_other_sources_results_and_duplicates(self):
        with self.assertRaisesRegex(ValueError, 'supported game version'):
            self.apply(dict(self.patch, sourceSectionSha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            self.apply(dict(self.patch, resultSectionSha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'listed twice'):
            self.apply(dict(self.patch, actions=self.patch['actions'] * 2))
        with self.assertRaisesRegex(ValueError, 'not an action ID'):
            self.apply(dict(self.patch, actions=[{'id': 543, 'set': {}}]))
        with self.assertRaisesRegex(ValueError, 'does not fit'):
            self.apply(dict(self.patch, actions=[{'id': 150, 'set': {'aoeRange': [0, 256]}}]))

    def test_experiment_recipe_targets_only_attack_area(self):
        recipe = json.loads((ROOT / 'patches/attack_area_experiment.json').read_text(encoding='utf-8'))
        self.assertEqual(recipe['section'], 14)
        self.assertEqual(recipe['actions'], [{'id': 150, 'name': 'Attack',
                                              'set': {'aoeRange': [0, 4], 'aoePositioningRange': [0, 180]}}])

    def test_experiment_ships_the_cleave_filter_on_forge_228(self):
        from ivalice_legacy.__main__ import ATTACK_AREA_SCRIPTS
        self.assertEqual(ATTACK_AREA_SCRIPTS, {
            'forge_filter_228.lua': 'x64/scripts/config/TheInsurgentsForgeConfig/functions/228.lua',
            'attack_area_filter.lua': 'x64/scripts/IvaliceLegacy/attack_area_filter.lua'})
        adapter = (ROOT / 'prototypes/forge_filter_228.lua').read_text(encoding='utf-8')
        self.assertIn("loadfile('scripts/TheInsurgentsForge/functions/228.lua'", adapter)
        self.assertIn("loadfile('scripts/IvaliceLegacy/attack_area_filter.lua'", adapter)


if __name__ == '__main__':
    unittest.main()
