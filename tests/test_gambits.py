"""Gambit text and link repairs keep every unselected byte and refuse changed sources."""
import copy
import struct
import unittest

from ivalice_legacy.gambits import (GLYPHS, HELP_BASE, TABLE_ROWS, TABLE_STRIDE, build, encode, replace_help_text,
                                    replace_names, sha, word_name)
from ivalice_legacy.technicks import help_strings


def word_table(names):
    entries = []
    for name in names:
        raw = b'\0\0' + encode(name) + b'\0' + bytes([GLYPHS['s']]) + b'\0' + encode('a ') + b'\0'
        entries.append(raw + bytes(-len(raw) % 4))
    out = bytearray(struct.pack('<I', len(names)) + bytes(4 * len(names)))
    offsets = []
    for raw in entries:
        offsets.append(len(out))
        out += raw
    struct.pack_into(f'<{len(names)}I', out, 4, *offsets)
    return bytes(out)


def help_file(texts):
    count = len(texts)
    strings = bytearray()
    relative = []
    base = 4 + 4 * (count + 1)
    for text in texts:
        relative.append(base + len(strings))
        strings += encode(text) + b'\0'
    size = base + len(strings)
    sub = struct.pack('<I', count) + struct.pack(f'<{count + 1}I', *relative, size) + bytes(strings)
    header = b'st2e' + struct.pack('<IIIII', count, 4, 32, 0, 32 + count * 4) + bytes(8)
    return header + bytes(4 * count) + struct.pack('<III', 1, 12, 12 + size) + sub + bytes(-size % 4)


def gambit_table(name_links, help_links):
    raw = bytearray(32 + TABLE_ROWS * TABLE_STRIDE)
    struct.pack_into('<IH', raw, 4, TABLE_ROWS, TABLE_STRIDE)
    struct.pack_into('<I', raw, 12, 32)
    for row in range(TABLE_ROWS):
        position = 32 + row * TABLE_STRIDE
        struct.pack_into('<H', raw, position + 4, HELP_BASE + help_links.get(row, row))
        struct.pack_into('<H', raw, position + 8, 0x1000 + row)
        struct.pack_into('<H', raw, position + 20, 0x800 | name_links.get(row, row))
    return bytes(raw)


class GambitTests(unittest.TestCase):
    def setUp(self):
        self.names = word_table(['Foe: any', 'Foe: w/o Safety', 'NOT USED'])
        self.help = help_file(['Target any foe.', 'Target undead.', 'Target HP Critical.'])
        self.table = gambit_table({5: 1, 6: 0}, {5: 1, 6: 0})
        self.patch = {
            'sourcePins': {'table': {'sha256': sha(self.table)}, 'names': {'sha256': sha(self.names)},
                           'help': {'sha256': sha(self.help)}},
            'fieldCorrections': [{'row': 5, 'field': 'description', 'offset': 4, 'before': HELP_BASE + 1,
                                  'after': HELP_BASE + 2},
                                 {'row': 6, 'field': 'firstTargetCondition', 'offset': 8, 'before': 0x1006,
                                  'after': 0x2039}],
            'records': [{'row': 5, 'nameIndex': 1, 'helpIndex': 2, 'name': 'Foe: HP ≥ 90%, no Safety',
                         'help': 'Target a foe whose HP is at least 90% without\nSafety protection.'}],
        }

    def test_records_follow_real_links_and_preserve_everything_else(self):
        table, names, help_text = build(self.table, self.names, self.help, self.patch)
        self.assertEqual(struct.unpack_from('<H', table, 32 + 5 * TABLE_STRIDE + 4)[0], HELP_BASE + 2)
        self.assertEqual(struct.unpack_from('<H', table, 32 + 6 * TABLE_STRIDE + 8)[0], 0x2039)
        changed = [i for i in range(len(table)) if table[i] != self.table[i]]
        self.assertLessEqual(len(changed), 4)
        self.assertEqual(word_name(names, 1), encode('Foe: HP ≥ 90%, no Safety'))
        self.assertEqual(word_name(names, 0), encode('Foe: any'))
        self.assertEqual(word_name(names, 2), encode('NOT USED'))
        strings = help_strings(help_text)
        self.assertEqual(strings[2], encode('Target a foe whose HP is at least 90% without\nSafety protection.'))
        self.assertEqual(strings[:2], help_strings(self.help)[:2])

    def test_changed_source_is_refused(self):
        for key, args in (('table', (self.table + b'\0', self.names, self.help)),
                          ('names', (self.table, self.names + b'\0', self.help)),
                          ('help', (self.table, self.names, self.help + b'\0'))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                build(*args, self.patch)

    def test_wrong_link_or_oversized_text_is_refused(self):
        for field, value in (('nameIndex', 0), ('helpIndex', 0), ('name', 'x' * 33),
                             ('help', 'x' * 49), ('help', 'a\nb\nc')):
            patch = copy.deepcopy(self.patch)
            patch['records'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build(self.table, self.names, self.help, patch)

    def test_field_correction_must_match_review(self):
        patch = copy.deepcopy(self.patch)
        patch['fieldCorrections'][1]['before'] = 0x1234
        with self.assertRaises(ValueError):
            build(self.table, self.names, self.help, patch)

    def test_renamed_placeholder_gets_regular_forms(self):
        names = replace_names(self.names, {2: "Lamont's Sword"}, regular_forms={2})
        self.assertEqual(word_name(names, 2), encode("Lamont's Sword"))
        self.assertEqual(word_name(names, 0), encode('Foe: any'))

    def test_help_refuses_unknown_glyph(self):
        with self.assertRaises(ValueError):
            replace_help_text(self.help, {0: 'Target @ foe.'})


if __name__ == '__main__':
    unittest.main()
