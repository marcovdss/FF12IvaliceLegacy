"""Tests for the equipment field writer (synthetic tables; the real-table check runs with IVALICE_LEGACY_GAME)."""
from pathlib import Path
import os
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ivalice_legacy import equipment as eq

SIZE = 52


def synthetic(rows, blocks):
    """rows = [(category, {offset: byte}, link), ...]; blocks = [bytes24, ...]; link = 24 * block index."""
    entry = 32
    attributes = entry + SIZE * len(rows)
    out = bytearray(b'st2e' + struct.pack('<IH', len(rows), SIZE) + bytes(2) + struct.pack('<IIII', entry, 0, 0, attributes))
    out += bytes(entry - len(out))
    for category, values, link in rows:
        row = bytearray(SIZE)
        row[eq.CATEGORY_AT] = category
        for at, v in values.items():
            row[at] = v
        struct.pack_into('<I', row, eq.LINK_AT, link)
        out += row
    for b in blocks:
        out += b
    return bytes(out)


def pack(section):
    return struct.pack('<I2I', 1, 12, 12 + len(section)) + section


def patch_for(section, **extra):
    p = pack(section)
    return p, dict(file='battle_pack.bin', section=0, sourceFileSha256=eq.sha256(p),
                   sourceSectionSha256=eq.sha256(section), **extra)


DAGGER = eq.CATEGORIES.index('Dagger')


class EquipmentTest(unittest.TestCase):
    def setUp(self):
        self.section = synthetic([(DAGGER, {26: 22, 39: 21, 18: 100}, 0), (eq.CATEGORIES.index('Body armor'), {24: 9}, 0),
                                  (eq.CATEGORIES.index('Bow'), {}, 24)], [bytes(24), bytes(24)])

    def apply(self, **extra):
        p, patch = patch_for(self.section, **extra)
        return eq.equipment(lambda name: p, patch)['battle_pack.bin.dir/section_000.bin']

    def test_weapon_fields(self):
        out = self.apply(items=[{'contentId': 0x1000, 'name': 'Dagger', 'category': 'Dagger',
                                 'set': {'chargeTime': [21, 15], 'attack': [22, 22], 'statusEffects': [[], ['Slow']],
                                         'onHitRate': [0, 20], 'hitsFlying': [False, True], 'gil': [100, 90]}}])
        row = 32
        self.assertEqual(out[row + 39], 15)
        self.assertEqual(struct.unpack_from('<I', out, row + 32)[0], 1 << eq.STATUS.index('Slow'))
        self.assertEqual((out[row + 31], out[row + 7] & eq.HITS_FLYING), (20, eq.HITS_FLYING))
        self.assertEqual(struct.unpack_from('<H', out, row + 18)[0], 90)
        self.assertEqual(out[SIZE + 32:], self.section[SIZE + 32:])

    def test_rejects_wrong_before_category_and_field(self):
        cases = [{'contentId': 0x1000, 'category': 'Dagger', 'set': {'chargeTime': [20, 15]}},
                 {'contentId': 0x1000, 'category': 'Sword', 'set': {'chargeTime': [21, 15]}},
                 {'contentId': 0x1001, 'category': 'Body armor', 'set': {'chargeTime': [0, 15]}},
                 {'contentId': 0x1001, 'category': 'Body armor', 'set': {'defense': [9, 256]}},
                 {'contentId': 0x1000, 'category': 'Dagger', 'set': {'statusEffects': [[], ['Nope']]}},
                 {'contentId': 0x1009, 'category': 'Dagger', 'set': {'attack': [0, 1]}}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.apply(items=[item])

    def test_weapon_family_change_touches_only_the_category_byte(self):
        out = self.apply(items=[{'contentId': 0x1000, 'category': 'Dagger', 'set': {'category': ['Dagger', 'Katana']}}])
        self.assertEqual(out[32 + eq.CATEGORY_AT], eq.CATEGORIES.index('Katana'))
        self.assertEqual([i for i, (a, b) in enumerate(zip(self.section, out)) if a != b], [32 + eq.CATEGORY_AT])
        for change in (['Sword', 'Katana'], ['Dagger', 'Shield'], ['Dagger', 'Nope']):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply(items=[{'contentId': 0x1000, 'category': 'Dagger', 'set': {'category': change}}])

    def test_rejects_item_twice(self):
        item = {'contentId': 0x1000, 'category': 'Dagger', 'set': {'attack': [22, 20]}}
        with self.assertRaises(ValueError):
            self.apply(items=[item, item])

    def test_moving_to_another_block_names_its_new_users(self):
        move = {'contentId': 0x1000, 'category': 'Dagger', 'set': {'attributeBlock': [0, 1]}}
        effect = {'attributeIndex': 1, 'users': [0x1000, 0x1002], 'set': {'status': [[], ['Protect']]}}
        out = self.apply(items=[move], attributes=[effect])
        self.assertEqual(struct.unpack_from('<I', out, 32 + eq.LINK_AT)[0], 24)
        with self.assertRaises(ValueError):  # the moved item must be declared as a user of its new block
            self.apply(items=[move], attributes=[dict(effect, users=[0x1002])])
        with self.assertRaises(ValueError):  # the current block must match
            self.apply(items=[dict(move, set={'attributeBlock': [1, 0]})])

    def test_shared_attribute_block_needs_every_user(self):
        change = {'immune': [[], ['Oil']]}
        with self.assertRaises(ValueError):
            self.apply(attributes=[{'attributeIndex': 0, 'users': [0x1000], 'set': change}])
        out = self.apply(attributes=[{'attributeIndex': 0, 'users': [0x1000, 0x1001], 'set': change}])
        at = 32 + 3 * SIZE
        self.assertEqual(struct.unpack_from('<I', out, at + 12)[0], 1 << eq.STATUS.index('Oil'))
        self.assertEqual(out[at + 24:], self.section[at + 24:])

    def test_appended_block_keeps_shared_blocks_and_alignment(self):
        move = {'contentId': 0x1002, 'category': 'Bow', 'set': {'attributeBlock': [1, 2]}}
        effect = {'attributeIndex': 2, 'users': [0x1002], 'set': {'status': [[], ['Protect']]}}
        out = self.apply(appendAttributeBlocks=1, items=[move], attributes=[effect])
        at = len(self.section)
        self.assertEqual((len(out) % 8, out[at + 24:]), (0, bytes(len(out) - at - 24)))
        self.assertEqual(struct.unpack_from('<I', out, 32 + 2 * SIZE + eq.LINK_AT)[0], 48)
        self.assertEqual(struct.unpack_from('<I', out, at + 8)[0], 1 << eq.STATUS.index('Protect'))
        changed = [i for i, (a, b) in enumerate(zip(self.section, out)) if a != b]
        self.assertEqual(changed, [32 + 2 * SIZE + eq.LINK_AT])  # rows and old blocks otherwise unchanged
        with self.assertRaises(ValueError):  # an appended block must be declared
            self.apply(appendAttributeBlocks=1)
        with self.assertRaises(ValueError):  # ... and used
            self.apply(appendAttributeBlocks=1, attributes=[dict(effect, users=[])])
        with self.assertRaises(ValueError):  # items can only move to blocks that exist
            self.apply(items=[dict(move, set={'attributeBlock': [1, 3]})], appendAttributeBlocks=1, attributes=[effect])

    def test_append_pads_to_eight_bytes(self):
        self.section += bytes(4)  # native tables end in zero padding
        move = {'contentId': 0x1002, 'category': 'Bow', 'set': {'attributeBlock': [1, 2]}}
        effect = {'attributeIndex': 2, 'users': [0x1002], 'set': {'weak': [[], ['Fire']]}}
        out = self.apply(appendAttributeBlocks=1, items=[move], attributes=[effect])
        self.assertEqual((len(out) % 8, len(out)), (0, len(self.section) - 4 + 24 + 4))
        self.section = self.section[:-4] + b'\x01\x00\x00\x00'
        with self.assertRaises(ValueError):  # data after the blocks is not padding
            self.apply(appendAttributeBlocks=1, items=[move], attributes=[effect])

    def test_rejects_changed_source(self):
        p, patch = patch_for(self.section, items=[])
        patch['sourceSectionSha256'] = '0' * 64
        with self.assertRaises(ValueError):
            eq.equipment(lambda name: p, patch)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'needs the game (IVALICE_LEGACY_GAME)')
class RealTableTest(unittest.TestCase):
    def test_layout_and_known_rows(self):
        from ivalice_legacy.formats import battle_pack_section
        from ivalice_legacy.vbf import VBF
        vbf = VBF(Path(os.environ['IVALICE_LEGACY_GAME']) / 'FFXII_TZA.vbf')
        section = battle_pack_section(vbf.read('ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'), 13)
        count, size, entry, attributes = eq.layout(section)
        self.assertEqual(size, SIZE)
        categories = [section[entry + i * size + eq.CATEGORY_AT] for i in range(count)]
        self.assertTrue(all(c < len(eq.CATEGORIES) for c in categories))
        links = {struct.unpack_from('<I', section, entry + i * size + eq.LINK_AT)[0] for i in range(count)}
        self.assertTrue(all(link % 24 == 0 and attributes + link + 24 <= len(section) for link in links))

    def test_arsenal_patch_changes_only_its_rows(self):
        import json
        from ivalice_legacy.vbf import VBF
        from ivalice_legacy.formats import battle_pack_section
        patch = json.loads((Path(__file__).resolve().parents[1] / 'patches/arsenal.json').read_text(encoding='utf-8'))
        vbf = VBF(Path(os.environ['IVALICE_LEGACY_GAME']) / 'FFXII_TZA.vbf')
        result = eq.equipment(vbf.read, patch)[patch['file'] + '.dir/section_013.bin']
        source = battle_pack_section(vbf.read(patch['file']), 13)
        count, size, entry, attributes = eq.layout(source)
        changed = [i for i, (a, b) in enumerate(zip(source, result)) if a != b]
        rows = {(i - entry) // size for i in changed if i < attributes}
        self.assertEqual({eq.FIRST_ID + r for r in rows}, {i['contentId'] for i in patch['items']})
        blocks = {(i - attributes) // 24 for i in changed if i >= attributes}
        native = (len(source) - attributes) // 24
        blocks |= set(range(native, native + patch.get('appendAttributeBlocks', 0)))
        self.assertEqual(blocks, {b['attributeIndex'] for b in patch.get('attributes', [])})
        # Blocks 18-28 are the Espers' own weapons (party members 27-39): Arsenal never edits them.
        self.assertFalse(blocks & set(range(18, 29)))
        daggers = [i['contentId'] - eq.FIRST_ID for i in patch['items'] if i['category'] == 'Dagger']
        self.assertEqual(len(daggers), 12)  # every sold dagger; the unused 0x11CE row stays native
        self.assertTrue(all(result[entry + r * size + 39] == 15 for r in daggers))


if __name__ == '__main__':
    unittest.main()
