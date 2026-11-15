"""Tests for the party-member template writer (battle_pack section 16), on synthetic tables."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ivalice_legacy import party_templates as pt

SIZE = 0x80


def synthetic(rows, tail=b''):
    entry = 32
    out = bytearray(b'st2e' + struct.pack('<IH', len(rows), SIZE) + bytes(2) + struct.pack('<I', entry))
    out += bytes(entry - len(out))
    for slots in rows:
        row = bytearray(range(SIZE))
        struct.pack_into('<5H', row, pt.EQUIPMENT_AT, *slots)
        out += row
    return bytes(out) + tail


def pack(section):
    return struct.pack('<I2I', 1, 12, 12 + len(section)) + section


class PartyTemplatesTest(unittest.TestCase):
    def setUp(self):
        self.section = synthetic([(0x1001, 0xFFFF, 0x1100, 0x1140, 0xFFFF), (0x11D4, 0x11D5, 0x10F4, 0x1130, 0x1156)],
                                 tail=b'\x01\x02\x03\x04')

    def apply(self, members, section=None):
        section = section or self.section
        p = pack(section)
        patch = dict(file='battle_pack.bin', section=0, sourceFileSha256=pt.sha256(p),
                     sourceSectionSha256=pt.sha256(section), members=members)
        return pt.party_templates(lambda name: p, patch)['battle_pack.bin.dir/section_000.bin']

    def test_parse_serialize_is_identity(self):
        table = pt.parse(self.section)
        self.assertEqual(pt.serialize(table), self.section)
        self.assertEqual(len(table['rows']), 2)
        self.assertEqual(pt.equipment_of(table['rows'][1])['accessory'], 0x1156)

    def test_empty_recipe_reproduces_the_source(self):
        self.assertEqual(self.apply([]), self.section)

    def test_slot_edit_touches_only_its_two_bytes(self):
        out = self.apply([{'member': 1, 'set': {'weapon': [0x11D4, 0x11BD], 'accessory': [0x1156, 0x1167]}}])
        row = 32 + SIZE
        self.assertEqual(pt.equipment_of(out[row:row + SIZE])['weapon'], 0x11BD)
        self.assertEqual(pt.equipment_of(out[row:row + SIZE])['accessory'], 0x1167)
        changed = [i for i, (a, b) in enumerate(zip(self.section, out)) if a != b]
        self.assertTrue(set(changed) <= {row + 0x0A, row + 0x0B, row + 0x12, row + 0x13})
        self.assertEqual(len(out), len(self.section))

    def test_rejections(self):
        for members in ([{'member': 1, 'set': {'weapon': [0x1000, 0x11BD]}}],
                        [{'member': 2, 'set': {'weapon': [0x11D4, 0x11BD]}}],
                        [{'member': 1, 'set': {'ring': [0x11D4, 0x11BD]}}],
                        [{'member': 1, 'set': {'weapon': [0x11D4, 0x10000]}}],
                        [{'member': 1, 'set': {}}, {'member': 1, 'set': {}}]):
            with self.subTest(members=members), self.assertRaises(ValueError):
                self.apply(members)

    def test_rejects_changed_source(self):
        p = pack(self.section)
        patch = dict(file='battle_pack.bin', section=0, sourceFileSha256=pt.sha256(p),
                     sourceSectionSha256='0' * 64, members=[])
        with self.assertRaises(ValueError):
            pt.party_templates(lambda name: p, patch)


if __name__ == '__main__':
    unittest.main()
