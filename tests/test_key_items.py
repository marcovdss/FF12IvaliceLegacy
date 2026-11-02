"""Proficiency Writs as key items (patches/proficiency_writs.json -> names, section 35, Key Items help)."""
import copy
import json
import os
import struct
import unittest
from pathlib import Path

from ivalice_legacy import key_items as K

ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT/'patches/proficiency_writs.json').read_text(encoding='utf-8'))
INV = {v: k for k, v in K.GLYPHS.items()}


def decode(raw):
    return ''.join(INV.get(b, f'<{b:02x}>') for b in raw)


class WritContractTest(unittest.TestCase):
    def test_writs_match_the_runtime(self):
        runtime = (ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8')
        self.assertIn('M.WRIT_IDS = {0x81F0, 0x81F1, 0x81F2}', runtime)
        self.assertEqual([i['contentId'] for i in PATCH['items']], [0x81F0, 0x81F1, 0x81F2])
        for item in PATCH['items']:
            self.assertIn(f'"{item["name"]}"', runtime)   # Montblanc names the same Writs

    def test_bad_writs_rejected(self):
        for mutate in (lambda p: p['items'].pop(),
                       lambda p: p['items'][0].update(contentId=0x81F3),
                       lambda p: p['items'][0].update(description='x' * 57),
                       lambda p: p['items'][0].update(name='Writ’s'),
                       lambda p: p['items'][1].update(article='the')):
            bad = copy.deepcopy(PATCH)
            mutate(bad)
            with self.assertRaises(ValueError):
                K.check_writs(bad)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class WritResourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ivalice_legacy.vbf import VBF
        from ivalice_legacy.formats import battle_pack_section
        archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')
        packed = archive.read(K.PACK)
        cls.s61, cls.s35 = battle_pack_section(packed, 61), battle_pack_section(packed, 35)
        cls.help = archive.read(K.LISTHELP)

    def test_names(self):
        before = K._subtable(self.s61, 12)
        after = K.key_item_names(self.s61, PATCH)
        def entries(t):
            off = [struct.unpack_from('<I', t, 4 + 4 * i)[0] for i in range(513)]
            self.assertEqual(off[-1], len(t))
            return [t[a:b] for a, b in zip(off, off[1:])]
        old, new = entries(before), entries(after)
        writs = {i['index']: i for i in PATCH['items']}
        for i in range(512):
            if i in writs:
                name, plural, article = new[i][2:].split(b'\0')[:3]
                self.assertEqual((decode(name), plural, decode(article)),
                                 (writs[i]['name'], b'', writs[i]['article'] + ' '))
            else:
                self.assertEqual(new[i], old[i])
        # The apostrophe glyph is the game's own: native names already use 0xAC.
        self.assertTrue(any(0xAC in e.split(b'\0')[2] for e in old if len(e) > 2))  # e.g. "Sage's Monograph"

    def test_rows(self):
        after = K.key_item_rows(self.s35, PATCH)
        count, stride, start = struct.unpack_from('<III', self.s35, 4)
        tomaj = struct.unpack_from('<HHBBHH', self.s35, start + stride * (0x70 - 96))
        for row in range(count):
            a = self.s35[start + stride * row:start + stride * (row + 1)]
            b = after[start + stride * row:start + stride * (row + 1)]
            if row in (400, 401, 402):
                index, sort, icon, zero, help_link, name_link = struct.unpack('<HHBBHH', b)
                self.assertEqual((index, icon, zero), (96 + row, tomaj[2], 0))
                self.assertEqual((sort, help_link, name_link), struct.unpack('<HHBBHH', a)[1:2] +
                                 struct.unpack('<HHBBHH', a)[4:])
            else:
                self.assertEqual(a, b)
        self.assertEqual(after[:start], self.s35[:start])

    def test_help(self):
        after = K.key_item_help(self.help, PATCH)
        text = struct.unpack_from('<I', self.help, 20)[0]
        sub = text + 12
        self.assertEqual(after[:sub], self.help[:text + 8] + after[text + 8:text + 12] + self.help[text + 12:sub])
        def records(raw):
            off = [struct.unpack_from('<I', raw, sub + 4 + 4 * i)[0] for i in range(513)]
            self.assertEqual(struct.unpack_from('<I', raw, text + 8)[0], 12 + off[-1])
            self.assertEqual(len(raw) % 4, 0)
            return [raw[sub + o:].split(b'\0')[0] for o in off[:-1]]
        old, new = records(self.help), records(after)
        writs = {i['index']: i['description'] for i in PATCH['items']}
        for i in range(512):
            self.assertEqual(decode(new[i]), writs[i] if i in writs else decode(old[i]))
        # Tomaj's writ (0x8070) keeps its native text; the line break glyph is the native one.
        self.assertTrue(decode(new[0x70]).startswith('A writ given you by Tomaj'))
        self.assertIn(b'\x02', old[0x70])


if __name__ == '__main__':
    unittest.main()
