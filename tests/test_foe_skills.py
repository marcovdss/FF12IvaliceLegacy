"""Foe Skills + consumables (0.1.8): field writer, price writer, and the house rules the recipe must keep."""
import hashlib
import json
import struct
import unittest
from pathlib import Path

from ivalice_legacy import actions, items
from ivalice_legacy.technicks import help_strings, replace_help

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
RECIPE = json.loads((ROOT / 'patches/foe_skills.json').read_text(encoding='utf-8'))
ACTIONS = RECIPE['actionSection']['actions']
BUFFS = {'Protect', 'Shell', 'Regen', 'Haste', 'Bravery', 'Faith', 'Bubble', 'Reflect'}
CURES = {89: 'Gold Needle', 90: 'Echo Herbs', 91: 'Antidote', 95: 'Chronos Tear'}


def action_table():
    size, entry = 60, 32
    data = bytearray(entry + actions.ACTION_COUNT * size + 8)
    data[:4] = b'st2e'
    struct.pack_into('<IH', data, 4, actions.ACTION_COUNT, size)
    struct.pack_into('<I', data, 12, entry)
    at = entry + 91 * size
    struct.pack_into('<I', data, at + 24, 1 << actions.STATUS.index('Poison'))
    return bytes(data), entry, size


def item_table():
    size, entry = 12, 32
    data = bytearray(entry + items.ITEM_COUNT * size)
    data[:4] = b'st2e'
    struct.pack_into('<IH', data, 4, items.ITEM_COUNT, size)
    struct.pack_into('<I', data, 12, entry)
    struct.pack_into('<HH', data, entry + 15 * size + 8, 97, 700)
    return bytes(data), entry, size


class FieldWriterTests(unittest.TestCase):
    def test_status_names_become_bits_and_only_listed_bytes_change(self):
        table, entry, size = action_table()
        out = actions.edit_rows(table, [{'id': 91, 'name': 'Antidote', 'set': {
            'statusEffects': [['Poison'], ['Poison', 'Sap']], 'onHitRate': [0, 10], 'accuracy': [0, 65]}}])
        at = entry + 91 * size
        self.assertEqual(struct.unpack_from('<I', out, at + 24)[0], (1 << 8) | (1 << 10))
        self.assertEqual((out[at + 18], out[at + 20]), (65, 10))
        self.assertEqual(sum(a != b for a, b in zip(table, out)), 3)

    def test_rejects_wrong_status_before_and_unknown_names(self):
        table, _, _ = action_table()
        with self.assertRaisesRegex(ValueError, 'unexpected value'):
            actions.edit_rows(table, [{'id': 91, 'set': {'statusEffects': [['Blind'], ['Sap']]}}])
        with self.assertRaisesRegex(ValueError, 'distinct status names'):
            actions.edit_rows(table, [{'id': 91, 'set': {'statusEffects': [['Poison'], ['Toxic']]}}])
        with self.assertRaisesRegex(ValueError, 'distinct status names'):
            actions.edit_rows(table, [{'id': 91, 'set': {'statusEffects': [['Poison'], ['Sap', 'Sap']]}}])

    def test_price_writer(self):
        table, entry, size = item_table()
        out = items.edit_prices(table, [{'id': 15, 'name': 'Serum', 'gil': [700, 150]}])
        self.assertEqual(struct.unpack_from('<HH', out, entry + 15 * size + 8), (97, 150))
        self.assertEqual(sum(a != b for a, b in zip(table, out)), 2)
        with self.assertRaisesRegex(ValueError, 'unexpected price'):
            items.edit_prices(table, [{'id': 15, 'gil': [600, 150]}])
        with self.assertRaisesRegex(ValueError, 'only prices'):
            items.edit_prices(table, [{'id': 15, 'gil': [700, 150], 'action': [97, 98]}])


class RecipeRuleTests(unittest.TestCase):
    def test_counts_and_fields(self):
        self.assertEqual(len(ACTIONS), 51)
        self.assertEqual(len({a['id'] for a in ACTIONS}), 51)
        for a in ACTIONS:
            # MP cost, radius, targeting, formula and power stay native.
            self.assertLessEqual(set(a['set']), {'accuracy', 'onHitRate', 'statusEffects', 'chargeTime'}, a['name'])

    def test_house_rules(self):
        for a in ACTIONS:
            s = a['set']
            if 'chargeTime' in s:
                self.assertGreater(s['chargeTime'][1], s['chargeTime'][0], 'never shorten a cast')
            if 'statusEffects' in s and a['id'] not in CURES:
                self.assertNotIn('KO', s['statusEffects'][1], a['name'])
            if 'onHitRate' in s and s['onHitRate'][1] > 50:
                # Only self/ally buffs (a Dispel window) may be certain.
                self.assertLessEqual(set(s['statusEffects'][1]), BUFFS, a['name'])
            if 'accuracy' in s:
                self.assertLess(s['accuracy'][1], 99, a['name'])

    def test_cures_only_widen(self):
        cures = {a['id']: a['set']['statusEffects'] for a in ACTIONS if a['id'] in CURES}
        self.assertEqual(set(cures), set(CURES))
        for before, after in cures.values():
            self.assertLess(set(before), set(after))
        every = set().union(*(after for _, after in cures.values()))
        self.assertLessEqual({'Doom', 'Disable', 'Immobilize', 'Sap', 'Berserk'}, every)

    def test_no_new_items_and_serum_price(self):
        self.assertEqual(RECIPE['itemSection']['items'], [{'id': 15, 'name': 'Serum', 'gil': [700, 150]}])
        # Reserve item actions 111-123 are not touched (new items would need Together's catalogue).
        self.assertFalse({a['id'] for a in ACTIONS} & set(range(111, 124)))

    def test_help_lines_fit_and_match_notes(self):
        notes = RECIPE['itemNotes']
        for name, spec in RECIPE['help'].items():
            for text in spec['strings'].values():
                self.assertLessEqual(len(text), 48)
        self.assertEqual(notes['9'], 'Remove Poison and Sap from one ally.')

    @unittest.skipIf(LuaRuntime is None, 'requires lupa')
    def test_description_data_carries_the_item_notes(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        data = lua.execute((ROOT / 'package/x64/scripts/IvaliceLegacy/description_data.lua').read_text())
        for key, text in RECIPE['itemNotes'].items():
            self.assertEqual(data['items'][int(key)]['notes'], text)


class HelpWriterTests(unittest.TestCase):
    def test_replace_help_on_a_synthetic_file(self):
        strings = [b'One', b'', b'Three']
        n = len(strings)
        body = b''
        offsets = []
        for s in strings:
            offsets.append(4 + 4 * (n + 1) + len(body))
            body += s + b'\0'
        sub = struct.pack(f'<I{n + 1}I', n, *offsets, 4 + 4 * (n + 1) + len(body)) + body
        sub += bytes(-len(sub) % 4)
        text = 32 + 4 * n
        size = 4 + 4 * (n + 1) + len(strings[0]) + len(strings[1]) + len(strings[2]) + 3
        raw = (b'st2e' + struct.pack('<IIIII', n, 4, 32, 0, text) + bytes(8) + bytes(4 * n)
               + struct.pack('<III', 1, 12, 12 + size) + sub)
        out = replace_help(raw, {1: 'Remove Poison and Sap from one ally.'}, hashlib.sha256(raw).hexdigest())
        got = help_strings(out)
        self.assertEqual((got[0], got[2]), (b'One', b'Three'))
        self.assertTrue(got[1])


if __name__ == '__main__':
    unittest.main()
