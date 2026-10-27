"""Tests for the patch appliers.

Synthetic tests need no game data. The full build test runs only when IVALICE_LEGACY_GAME points at an
installed game:  python -m unittest discover -s tests
"""
from pathlib import Path
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ivalice_legacy.formats import shop_table
from ivalice_legacy import patches


def synthetic_shops(shops):
    """Build a shop-table section: shops = [[(condition, threshold, [items]), ...], ...]."""
    out = bytearray(b'\x64\x00\x00\x00\x08\x00' + struct.pack('<HI', len(shops), 12))
    shop_rows = len(out)
    out += bytes(8 * len(shops))
    for i, rows in enumerate(shops):
        event_table = len(out)
        struct.pack_into('<HHI', out, shop_rows + 8 * i, 600 + i, 0, event_table)
        out += b'\x64\x01\x00\x00\x08\x00' + struct.pack('<HI', len(rows), event_table + 12)
        row_base = len(out)
        out += bytes(8 * len(rows))
        for j, (condition, threshold, items) in enumerate(rows):
            header = len(out)
            out += b'\x64\x02\x00\x00\x02\x00' + struct.pack('<HI', len(items), header + 12)
            out += struct.pack(f'<{len(items)}H', *items)
            while len(out) % 4:
                out.append(0)
            struct.pack_into('<BBHI', out, row_base + 8 * j, condition, 0, threshold, header)
    return bytes(out)


class OpeningStockTest(unittest.TestCase):
    def apply(self, section, shops):
        pack = struct.pack('<I2I', 1, 12, 12 + len(section)) + section
        patch = {'file': 'battle_pack.bin', 'section': 0, 'sourceFileSha256': patches.sha256(pack),
                 'sourceSectionSha256': patches.sha256(section), 'shops': shops}
        return patches.opening_stock(lambda name: pack, patch)['battle_pack.bin.dir/section_000.bin']

    def test_moves_items_to_baseline_once(self):
        section = synthetic_shops([[(2, 200, [10, 11]), (0, 0, [1, 2])], [(0, 0, [5])]])
        result = self.apply(section, [{'shop': 0, 'availableFromStart': [{'id': 11, 'name': 'B'}, {'id': 3, 'name': 'C'}]}])
        rows = shop_table(result)[0]['rows']
        self.assertEqual([r['items'] for r in rows], [[10], [1, 2, 11, 3]])
        self.assertEqual(shop_table(result)[1]['rows'][0]['items'], [5])
        self.assertEqual([(r['condition'], r['threshold']) for r in rows], [(2, 200), (0, 0)])
        self.assertEqual(len(result) % 16, 0)

    def test_rejects_shop_without_baseline(self):
        section = synthetic_shops([[(2, 200, [10])]])
        with self.assertRaises(ValueError):
            self.apply(section, [{'shop': 0, 'availableFromStart': [{'id': 1, 'name': 'A'}]}])

    def test_rejects_other_game_version(self):
        section = synthetic_shops([[(0, 0, [1])]])
        pack = struct.pack('<I2I', 1, 12, 12 + len(section)) + section
        patch = {'file': 'x', 'section': 0, 'sourceFileSha256': '0' * 64, 'sourceSectionSha256': '0' * 64, 'shops': []}
        with self.assertRaises(ValueError):
            patches.opening_stock(lambda name: pack, patch)


class StoryStockTest(unittest.TestCase):
    def apply(self, section, shops):
        patch = {'file': 'battle_pack.bin', 'section': 0, 'sourceSectionSha256': patches.sha256(section), 'shops': shops,
                 'resultSectionSha256': '0' * 64}
        patch['resultSectionSha256'] = patches.sha256(self.unchecked(section, patch))
        return patches.story_stock(section, patch)

    def unchecked(self, section, patch):
        original = patches.require
        patches.require = lambda data, expected, what: None
        try:
            return patches.story_stock(section, patch)
        finally:
            patches.require = original

    def sold(self, result, shop, story):
        return patches.shop_stock(shop_table(result)[shop]['rows'], story)

    def test_item_unlocks_at_its_row_only(self):
        section = synthetic_shops([[(2, 500, [20]), (0, 0, [1])], [(0, 0, [5])]])
        result = self.apply(section, [{'shop': 0, 'rows': [{'story': 200, 'add': [{'id': 7, 'name': 'X'}]}]}])
        self.assertEqual([(r['condition'], r['threshold'], r['items']) for r in shop_table(result)[0]['rows']],
                         [(2, 500, [20]), (2, 200, [7]), (0, 0, [1])])
        self.assertNotIn(7, self.sold(result, 0, 199))
        self.assertIn(7, self.sold(result, 0, 200))
        self.assertIn(7, self.sold(result, 0, 900))
        self.assertEqual(shop_table(result)[1], shop_table(section)[1])

    def test_moves_a_later_copy(self):
        section = synthetic_shops([[(2, 900, [7, 8]), (0, 0, [9])]])
        result = self.apply(section, [{'shop': 0, 'rows': [{'story': 200, 'add': [{'id': 7, 'name': 'X'}]}]}])
        self.assertEqual([r['items'] for r in shop_table(result)[0]['rows']], [[8], [7], [9]])

    def test_rejects_a_second_offer_of_an_item_sold_earlier(self):
        section = synthetic_shops([[(2, 900, [8]), (0, 0, [9])]])
        with self.assertRaises(AssertionError):
            self.apply(section, [{'shop': 0, 'rows': [{'story': 200, 'add': [{'id': 9, 'name': 'Y'}]}]}])

    def test_last_row_is_always_sold(self):
        # No unconditional row: the game falls through to the last row, so it sells from story 0.
        section = synthetic_shops([[(2, 900, [8]), (2, 300, [9])]])
        result = self.apply(section, [{'shop': 0, 'rows': [{'story': 500, 'add': [{'id': 7, 'name': 'X'}]}]}])
        self.assertEqual([(r['condition'], r['threshold'], r['items']) for r in shop_table(result)[0]['rows']],
                         [(2, 900, [8]), (2, 500, [7]), (0, 0, [9])])
        self.assertEqual(self.sold(result, 0, 0), [9])

    def test_rejects_other_source(self):
        section = synthetic_shops([[(0, 0, [1])]])
        with self.assertRaises(ValueError):
            patches.story_stock(section, {'file': 'x', 'section': 0, 'sourceSectionSha256': '0' * 64, 'shops': [],
                                          'resultSectionSha256': '0' * 64})


def synthetic_area(stats_rows, unit_rows):
    """AR03 file with section 4 (units: stats row index at +34) and section 7 (56-byte stats rows)."""
    def st2e(rows, stride):
        return b'st2e' + struct.pack('<III', len(rows), stride, 16) + b''.join(rows)
    units = st2e([bytes(34) + struct.pack('<H', r) + bytes(14) for r in unit_rows], 50)
    stats = st2e([bytes(0x20) + struct.pack('<I', hp) + bytes(56 - 0x24) for hp in stats_rows], 56)
    offsets = [0] * 10
    offsets[4], offsets[7] = 48, 48 + len(units)
    return b'FF12AR03' + struct.pack('<10I', *offsets) + units + stats


class EnemyStatsTest(unittest.TestCase):
    def apply(self, raw, rows):
        from ivalice_legacy.formats import ard_section
        patch = {'file': 'a.ard', 'section': 7, 'sourceFileSha256': patches.sha256(raw),
                 'sourceSectionSha256': patches.sha256(ard_section(raw, 7)), 'rows': rows, 'resultSectionSha256': '0' * 64}
        original = patches.require
        patches.require = lambda data, expected, what: None
        try:
            data = patches.enemy_stats(lambda name: raw, patch)['a.ard.dir/section_007.bin']
        finally:
            patches.require = original
        patch['resultSectionSha256'] = patches.sha256(data)
        return patches.enemy_stats(lambda name: raw, patch)['a.ard.dir/section_007.bin']

    def test_sets_hp_of_one_row(self):
        raw = synthetic_area([72, 94], [0, 1])
        result = self.apply(raw, [{'row': 1, 'unit': 'Wolf', 'set': {'hp': [94, 170]}}])
        self.assertEqual(struct.unpack_from('<I', result, 16 + 56 + 0x20)[0], 170)
        self.assertEqual(struct.unpack_from('<I', result, 16 + 0x20)[0], 72)

    def test_rejects_unexpected_value_and_shared_row(self):
        raw = synthetic_area([72, 94], [0, 1, 1])
        with self.assertRaises(ValueError):
            self.apply(raw, [{'row': 0, 'unit': 'Cactite', 'set': {'hp': [70, 150]}}])
        with self.assertRaises(ValueError):
            self.apply(raw, [{'row': 1, 'unit': 'Wolf', 'set': {'hp': [94, 170]}}])
        self.apply(raw, [{'row': 1, 'unit': 'Wolf', 'users': 2, 'set': {'hp': [94, 170]}}])


class AbilityChestTest(unittest.TestCase):
    def test_changes_one_byte(self):
        script = bytearray(64)
        script[10] = 20
        raw = bytearray(b'EBP2' + bytes(12) + struct.pack('<20I', 96, *([0] * 19))) + script
        raw = bytes(raw)
        chest = {'ability': 'Test', 'file': 'a.ebp', 'section': 0, 'sourceFileSha256': patches.sha256(raw),
                 'sourceSectionSha256': patches.sha256(bytes(script)), 'sectionByteOffset': 10,
                 'spawnChanceBefore': 20, 'spawnChanceAfter': 100}
        out = patches.ability_chests(lambda name: raw, {'chests': [chest]})['a.ebp.dir/section_000.bin']
        self.assertEqual([i for i, (a, b) in enumerate(zip(script, out)) if a != b], [10])
        self.assertEqual(out[10], 100)


class KingsTest(unittest.TestCase):
    def script_file(self, script):
        return bytes(b'EBP2' + bytes(12) + struct.pack('<20I', 96, *([0] * 19))) + bytes(script)

    def patch(self, raw, script, before):
        return {'kings': [{'king': 'Test', 'file': 'a.ebp', 'section': 0, 'sourceFileSha256': patches.sha256(raw),
                           'sourceSectionSha256': patches.sha256(bytes(script)),
                           'immediates': [{'sectionByteOffset': 4, 'before': before, 'after': 100}]}]}

    def test_changes_only_the_operand(self):
        script = bytearray(16)
        script[4:7] = bytes([0x4F, 33, 0])  # push 33
        raw = self.script_file(script)
        out = patches.kings(lambda name: raw, self.patch(raw, script, 33))['a.ebp.dir/section_000.bin']
        self.assertEqual(out[4:7], bytes([0x4F, 100, 0]))  # push 100
        self.assertEqual([i for i, (a, b) in enumerate(zip(script, out)) if a != b], [5])

    def test_replaces_whole_instruction(self):
        script = bytearray(16)
        script[4:7] = bytes([0x58, 0xA9, 0x05])  # call command 0x5A9
        raw = self.script_file(script)
        patch = self.patch(raw, script, 0)
        patch['kings'][0]['immediates'] = []
        patch['kings'][0]['replacements'] = [{'sectionByteOffset': 4, 'before': '58a905', 'after': '4f0100'}]
        out = patches.kings(lambda name: raw, patch)['a.ebp.dir/section_000.bin']
        self.assertEqual(out[4:7], bytes([0x4F, 1, 0]))  # push 1
        self.assertEqual(len(out), len(script))

    def test_rejects_unexpected_value(self):
        script = bytearray(16)
        script[4:7] = bytes([0x4F, 34, 0])  # push 34
        raw = self.script_file(script)
        with self.assertRaises(ValueError):
            patches.kings(lambda name: raw, self.patch(raw, script, 33))


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME to run the full build')
class FullBuildTest(unittest.TestCase):
    def test_build_matches_recorded_results(self):
        from ivalice_legacy.__main__ import build
        with tempfile.TemporaryDirectory() as out:
            archive, manifest = build(os.environ['IVALICE_LEGACY_GAME'], out)
            self.assertTrue(archive.is_file())
            root = Path(__file__).resolve().parents[1] / 'patches'
            expected = {json.loads((root / 'story_stock.json').read_text())['resultSectionSha256']}
            expected |= {c['resultSectionSha256'] for c in json.loads((root / 'ability_chests.json').read_text())['chests']}
            self.assertTrue(expected <= set(manifest['files'].values()))
            package_files = [p for p in (root.parent / 'package').rglob('*') if p.is_file()]
            self.assertEqual(len(manifest['files']), len(expected) + len(json.loads((root / 'kings.json').read_text())['kings'])
                             + len(package_files))
            self.assertIn('x64/scripts/init/IvaliceLegacySaves.lua', manifest['files'])
            self.assertNotIn('x64/scripts/IvaliceLegacySaves.lua', manifest['files'])


if __name__ == '__main__':
    unittest.main()
