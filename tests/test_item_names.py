"""Accessory renames in the word table's equipment-name sub-table."""
import json
import os
import struct
import unittest
from pathlib import Path

from ivalice_legacy.item_names import encode_label, rename

ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT/'patches/arsenal.json').read_text(encoding='utf-8'))['equipmentNames']


def raw(name, plural, article):
    out = b'\0\0' + encode_label(name) + plural + b'\0' + article + b'\0'
    return out + bytes(-len(out) % 4)


class RenameTest(unittest.TestCase):
    def test_article_follows_the_new_name(self):
        old = raw('Opal Ring', b'\x09' + encode_label('s')[:-1], encode_label('an')[:-1] + b'\x04')
        new = rename(old, 'Cagnazzo Ring')
        self.assertTrue(new.startswith(b'\0\0' + encode_label('Cagnazzo Ring')))
        self.assertIn(b'\0' + encode_label('a')[:-1] + b'\x04\0', new)
        self.assertEqual(len(new) % 4, 0)

    def test_pair_items_keep_their_pair_form(self):
        pair = encode_label('pairs of')[:-1] + b'\x04\x09'
        old = raw('Winged Boots', pair, encode_label('a pair of')[:-1] + b'\x04')
        new = rename(old, 'Pixie Shoes')
        self.assertIn(pair, new)
        self.assertIn(encode_label('a pair of')[:-1], new)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class NamesTableTest(unittest.TestCase):
    def test_only_renamed_entries_change(self):
        from ivalice_legacy.vbf import VBF
        from ivalice_legacy.formats import battle_pack_section
        from ivalice_legacy.item_names import item_names, subtable
        archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')
        section = battle_pack_section(archive.read('ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'), 61)
        before, after = subtable(section, 1), item_names(section, PATCH)
        count = struct.unpack_from('<I', before, 0)[0]
        def entries(table):
            slots = [struct.unpack_from('<I', table, 4+4*i)[0] for i in range(count)] + [len(table)]
            return [table[slots[i]:slots[i+1]] for i in range(count)]
        old, new = entries(before), entries(after)
        changed = {i for i in range(count) if old[i] != new[i]}
        self.assertEqual(changed, {c['index'] for c in PATCH['changes']})


if __name__ == '__main__':
    unittest.main()
