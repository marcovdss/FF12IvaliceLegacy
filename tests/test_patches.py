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


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME to run the full build')
class FullBuildTest(unittest.TestCase):
    def test_build_matches_recorded_results(self):
        from ivalice_legacy.__main__ import build
        with tempfile.TemporaryDirectory() as out:
            archive, manifest = build(os.environ['IVALICE_LEGACY_GAME'], out)
            self.assertTrue(archive.is_file())
            root = Path(__file__).resolve().parents[1] / 'patches'
            expected = {json.loads((root / 'opening_stock.json').read_text())['resultSectionSha256']}
            expected |= {c['resultSectionSha256'] for c in json.loads((root / 'ability_chests.json').read_text())['chests']}
            self.assertEqual(set(manifest['files'].values()), expected)


if __name__ == '__main__':
    unittest.main()
