"""The Proficiency tip in the Clan Primer's Traveler's Tips (patches/proficiency_tip.json)."""
import copy
import json
import os
import struct
import unittest
from pathlib import Path

from ivalice_legacy import clan_primer as C

ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT/'patches/proficiency_tip.json').read_text(encoding='utf-8'))
INV = {v: k for k, v in C.GLYPHS.items()}


def decode(raw):
    return ''.join(INV.get(b, f'<{b:02x}>') for b in raw)


def pages(raw):
    """[(record bytes, text bytes)] read the way the native code does: base + u32 offset + u16 header."""
    count = struct.unpack_from('<H', raw, 0x0A)[0]
    table = [struct.unpack_from('<H', raw, 0x10 + 2 * i)[0] for i in range(count + 2)]
    out = []
    for r in table[1:-1]:
        rec = raw[r:r + 16]
        start = table[0] + struct.unpack_from('<I', rec, 4)[0]
        text = start + struct.unpack_from('<H', raw, start)[0]
        out.append((rec, raw[text:raw.index(b'\0', text)]))
    return out


class TipContractTest(unittest.TestCase):
    def test_runtime_uses_the_same_flag(self):
        runtime = (ROOT/'prototypes/proficiency_runtime.lua').read_text(encoding='utf-8')
        self.assertIn(f"R.TIP_FLAG = {PATCH['tip']['flag']}", runtime)

    def test_bad_tips_rejected(self):
        for mutate in (lambda t: t.update(flag=255), lambda t: t.update(category=4),
                       lambda t: t.update(body='x' * 57), lambda t: t.update(title='Proficiency; II')):
            bad = copy.deepcopy(PATCH)
            mutate(bad['tip'])
            with self.assertRaises(ValueError):
                C.check_tip(bad)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class TipResourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ivalice_legacy.vbf import VBF
        cls.archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')

    def read(self, path):
        return self.archive.read(path)

    def test_list(self):
        before, after = self.read(C.BOOK + '.bin'), C.tip_list(self.read(C.BOOK + '.bin'), PATCH)
        self.assertEqual(struct.unpack_from('<H', after, 0x0E)[0], 17)
        self.assertEqual(after[:0x0E], before[:0x0E])
        self.assertEqual(after[0x10:0xF0], before[0x10:0xF0])            # categories and the 16 native tips
        self.assertEqual(struct.unpack_from('<III', after, 0xF0), (0x116E7, 2, 0))
        self.assertEqual(after[0xFC:], before[0xF0:])                     # the unused footer follows the entries

    def test_checks(self):
        before, after = self.read(C.BOOK + '.dat'), C.tip_checks(self.read(C.BOOK + '.dat'), PATCH)
        self.assertEqual(struct.unpack_from('<I', after, 8)[0], 17)
        self.assertEqual(after[12:12 + 8 * 16], before[12:])
        self.assertEqual(after[-8:], bytes((0xFF, 0xFF, PATCH['tip']['flag'], 0)) + b'\xff' * 4)
        self.assertNotEqual(after[-6], 0xFF)                              # 0xFF would mean "no flag test"
        # No native tip check uses the flag bytes, so the bit cannot already unlock a native tip.
        self.assertTrue(all(before[12 + 8 * i:15 + 8 * i] == b'\xff\xff\xff' for i in range(16)))

    def test_pages(self):
        before, after = self.read(C.BOOK + '000.dat'), C.tip_pages(self.read(C.BOOK + '000.dat'), PATCH)
        old, new = pages(before), pages(after)
        self.assertEqual(len(new), 17)
        for (a, ta), (b, tb) in zip(old, new):
            self.assertEqual((a[:4], a[8:], ta), (b[:4], b[8:], tb))      # same records (apart from offsets), same text
        rec, text = new[16]
        self.assertEqual((rec[0], struct.unpack_from('<H', rec, 2)[0], rec[8:]), (1, 16, old[15][0][8:]))
        self.assertEqual(decode(text), PATCH['tip']['body'])
        self.assertTrue(all(line and len(line) <= 56 for line in PATCH['tip']['body'].split('\n') if line))
        self.assertIn(b'\x02', old[1][1])                                 # native pages break lines with 0x02
        self.assertEqual(len(after) % 4, 0)

    def test_title(self):
        before = self.read(C.TITLES)
        after = C.tip_title(before, PATCH)
        def title(raw, row):
            text = struct.unpack_from('<I', raw, 20)[0]
            sub = text + 12
            off = struct.unpack_from('<I', raw, sub + 4 + 4 * row)[0]
            return raw[sub + off:raw.index(b'\0', sub + off)]
        self.assertEqual(decode(title(after, 661)), 'Clan Proficiency')
        for row in (0, 4, 658, 662, 681):
            self.assertEqual(title(after, row), title(before, row))


if __name__ == '__main__':
    unittest.main()
