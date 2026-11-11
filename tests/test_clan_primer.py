"""Tips in the Clan Primer's Traveler's Tips: patches/proficiency_tip.json (one gated tip, vanilla sources) and
patches/families_tip.json (four ungated tips, deployed 17-entry sources)."""
import copy
import json
import os
import struct
import unittest
from pathlib import Path

from ivalice_legacy import clan_primer as C

ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT/'patches/proficiency_tip.json').read_text(encoding='utf-8'))
FAMILIES = json.loads((ROOT/'patches/families_tip.json').read_text(encoding='utf-8'))
INV = {v: k for k, v in C.GLYPHS.items()}
RESULT_KEYS = {'menuhandbook_tutorial.bin': 'list', 'menuhandbook_tutorial.dat': 'checks',
               'menuhandbook_tutorial000.dat': 'pages', 'penelosdiary.bin': 'titles'}
# The pages file the 0.1.33 release installed (the previous families wording); a game still on 0.1.33 carries it.
PREVIOUS_PAGES = '33fec20605944c93a4407af2423a05f144d8b63d741d89637c53a107c0983090'
SOURCE_KEYS = {'listSha256': 'menuhandbook_tutorial.bin', 'checksSha256': 'menuhandbook_tutorial.dat',
               'pagesSha256': 'menuhandbook_tutorial000.dat', 'titlesSha256': 'penelosdiary.bin'}


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


def page_text(body):
    """One hdatg text: u16 header offset 4, u16 0, encoded body, a zero, padded to four bytes."""
    text = struct.pack('<HH', 4, 0) + C.encode(body) + bytes(1)
    return text + bytes(-len(text) % 4)


def synth_list(entries, categories=4):
    """An hctgf Traveler's Tips list with the given (title ID, category) entries and the native header."""
    count = len(entries)
    raw = bytearray(b'hctgf\0\0\0' + bytes((4, 0x19)))
    raw += struct.pack('<7H', 0x30, 0x2C, count, 1, categories, 1, 1)
    raw += struct.pack(f'<{categories}I', *(0x116E9 + 3 * i for i in range(categories)))
    raw += bytes(0x30 - len(raw)) + b''.join(struct.pack('<III', tid, cat, 0) for tid, cat in entries)
    return bytes(raw + bytes(4))


def synth_checks(count=16):
    return b'hchkg\0\0\0' + struct.pack('<I', count) + bytes(8 * count)


def synth_pages(bodies):
    """An hdatg pages file in the native layout: offset table (padded to four bytes), records, text block."""
    count = len(bodies)
    table_size = 2 * (count + 2)
    first = 0x10 + table_size + (-(0x10 + table_size) % 4)
    records = [first + 16 * i for i in range(count)]
    base = records[-1] + 16
    texts, offsets, at = [], [], 0
    for body in bodies:
        text = page_text(body)
        texts.append(text)
        offsets.append(at)
        at += len(text)
    raw = bytearray(b'hdatg\0\0\0' + bytes(2) + struct.pack('<HI', count, 0xFFFFFFFF))
    raw += struct.pack(f'<{count + 2}H', base, *records, 0)
    raw += bytes(first - len(raw))
    for i in range(count):
        raw += struct.pack('<HHIHHI', 1, i, offsets[i], 0x8000, 0x8000, 0x00FFFFFF)
    return bytes(raw + b''.join(texts))


def synth_titles(rows):
    """An ST2E titles file: rows = [(ID, bytes or b'')], each string in its own record."""
    n = len(rows)
    first_string = 4 + 4 * (n + 1)
    data, offsets = bytearray(), []
    for _, string in rows:
        offsets.append(first_string + len(data))
        data += string + b'\0'
    sub = struct.pack(f'<{n + 2}I', n, *offsets, first_string + len(data)) + bytes(data)
    text_at = 32 + 4 * n
    raw = bytearray(struct.pack('<4sIIIII', b'st2e', n, 4, 32, 0, text_at))
    raw += bytes(32 - len(raw)) + b''.join(struct.pack('<HH', rid, 1) for rid, _ in rows)
    raw += struct.pack('<III', 1, 12, 12 + len(sub)) + sub
    return bytes(raw + bytes(-len(sub) % 4))


def synth_sources():
    """A 16-entry tip set in the native layouts. Title IDs 0x16E8-0x16F7 (rows 8-23) are listed by the synthetic
    book, like the native tips are; rows 24+ are free except rows 30 and 31, which hold text."""
    tips = [(0x116E8 + i, i % 4) for i in range(16)]
    bodies = [f'Native page number {i}, kept short.' for i in range(16)]
    titles = [(0x16E0 + i, b'A real title text.' if i in (30, 31) else b'') for i in range(32)]
    return {'list': synth_list(tips), 'checks': synth_checks(16), 'pages': synth_pages(bodies),
            'titles': synth_titles(titles)}


def patch_for(sources, tip=None, tips=None):
    """A draft tip patch (null result hashes) pinned to the given synthetic sources."""
    patch = {'listSha256': C.sha(sources['list']), 'checksSha256': C.sha(sources['checks']),
             'pagesSha256': C.sha(sources['pages']), 'titlesSha256': C.sha(sources['titles']),
             'resultSha256': {'list': None, 'checks': None, 'pages': None, 'titles': None}}
    if tip is not None:
        patch['tip'] = tip
    else:
        patch['tips'] = tips
    return patch


def build(patch, sources):
    """All four tip transforms over the given sources."""
    return {'list': C.tip_list(sources['list'], patch), 'checks': C.tip_checks(sources['checks'], patch),
            'pages': C.tip_pages(sources['pages'], patch), 'titles': C.tip_title(sources['titles'], patch)}


def pinned(patch, built):
    """The patch with its result hashes filled in from an actual build."""
    out = copy.deepcopy(patch)
    out['resultSha256'] = {k: C.sha(v) for k, v in built.items()}
    return out


ONE_TIP = {'title': 'Single Tip', 'titleRow': 24, 'titleId': 0x16E0 + 24, 'category': 2, 'flag': 254,
           'body': 'A gated single tip, the legacy patch shape.'}
TWO_TIPS = [{'title': 'First Added', 'titleRow': 25, 'titleId': 0x16E0 + 25, 'category': 1, 'flag': 255,
             'body': 'An ungated tip.\nWith a second line.'},
            {'title': 'Second Added', 'titleRow': 26, 'titleId': 0x16E0 + 26, 'category': 0, 'flag': 17,
             'body': 'Another gated one.'}]


class TipContractTest(unittest.TestCase):
    def test_runtime_uses_the_same_flag(self):
        runtime = (ROOT/'prototypes/proficiency_runtime.lua').read_text(encoding='utf-8')
        self.assertIn(f"R.TIP_FLAG = {PATCH['tip']['flag']}", runtime)

    def test_bad_tips_rejected(self):
        for mutate in (lambda t: t.update(flag=256), lambda t: t.update(flag=-1), lambda t: t.update(category=4),
                       lambda t: t.update(body='x' * 57), lambda t: t.update(title='Proficiency; II'),
                       lambda t: t.update(title='x' * 31), lambda t: t.update(title='Percent 50%'),
                       lambda t: t.update(titleRow='661')):
            bad = copy.deepcopy(PATCH)
            mutate(bad['tip'])
            with self.assertRaises(ValueError):
                C.check_tip(bad['tip'])

    def test_flag_255_is_an_ungated_tip(self):
        self.assertEqual(C.check_tip(dict(ONE_TIP, flag=255))['flag'], 255)

    def test_shipped_patches_are_pinned_and_valid(self):
        for patch in (PATCH, FAMILIES):
            self.assertEqual(sorted(patch['resultSha256']), ['checks', 'list', 'pages', 'titles'])
            self.assertTrue(all(patch['resultSha256'].values()), 'shipped patch must pin every result hash')
            self.assertTrue(C.tips(patch))

    def test_duplicate_rows_rejected(self):
        clash = copy.deepcopy(FAMILIES)
        clash['tips'][1]['titleRow'] = clash['tips'][0]['titleRow']
        with self.assertRaises(ValueError):
            C.tips(clash)

    def test_families_tips_are_valid(self):
        tips = C.tips(FAMILIES)
        self.assertEqual([t['title'] for t in tips],
                         ['Weapon Families: Heavy', 'Weapon Families: Light',
                          'Weapon Families: Ranged', 'Weapon Families: Magick'])
        for tip in tips:
            self.assertEqual(tip['category'], 2)              # same book section as the Proficiency tip
            self.assertEqual(tip['flag'], 255)                # ungated
            self.assertLessEqual(len(tip['title']), 30)
            lines = tip['body'].split('\n')
            self.assertLessEqual(len(lines), 16)
            self.assertLessEqual(max(len(line) for line in lines), 56)
            self.assertEqual(decode(C.encode(tip['title'])), tip['title'])
            self.assertEqual(decode(C.encode(tip['body'])), tip['body'])

    def test_wrong_result_hash_rejected(self):
        sources = synth_sources()
        draft = patch_for(sources, tips=TWO_TIPS)
        bad = pinned(draft, build(draft, sources))
        bad['resultSha256']['list'] = '0' * 64
        with self.assertRaises(ValueError):
            C.tip_list(sources['list'], bad)


class SynthTipTest(unittest.TestCase):
    """Transform mechanics on synthetic sources: legacy single-tip shape, multi-append chaining, check bytes,
    title-row safety."""

    def test_single_tip_legacy_shape(self):
        sources = synth_sources()
        patch = patch_for(sources, tip=ONE_TIP)
        built = build(patch, sources)
        self.assertEqual(struct.unpack_from('<H', built['list'], 0x0E)[0], 17)
        self.assertEqual(struct.unpack_from('<III', built['list'], 0x30 + 12 * 16),
                         (0x10000 | ONE_TIP['titleId'], 2, 0))
        self.assertEqual(struct.unpack_from('<I', built['checks'], 8)[0], 17)
        self.assertEqual(built['checks'][-8:], bytes((0xFF, 0xFF, 254, 0)) + b'\xff' * 4)
        self.assertEqual(len(pages(built['pages'])), 17)
        self.assertEqual(decode(pages(built['pages'])[16][1]), ONE_TIP['body'])
        self.assertEqual(build(pinned(patch, built), sources), built)     # pinned patch rebuilds byte-identically

    def test_multi_append_round_trip(self):
        sources = synth_sources()
        draft = patch_for(sources, tip=ONE_TIP)
        first = build(pinned(draft, build(draft, sources)), sources)      # 16 -> 17, byte-identical second build
        second = patch_for(first, tips=TWO_TIPS)                          # 17 -> 19 from the built state
        built = build(second, first)
        self.assertEqual(struct.unpack_from('<H', built['list'], 0x0E)[0], 19)
        self.assertEqual(built['list'][:0x0E], first['list'][:0x0E])
        self.assertEqual(built['list'][0x10:0x30 + 12 * 17], first['list'][0x10:0x30 + 12 * 17])
        self.assertEqual(built['list'][0x30 + 12 * 19:], first['list'][0x30 + 12 * 17:])  # footer
        self.assertEqual(struct.unpack_from('<III', built['list'], 0x30 + 12 * 17),
                         (0x10000 | TWO_TIPS[0]['titleId'], 1, 0))
        self.assertEqual(struct.unpack_from('<III', built['list'], 0x30 + 12 * 18),
                         (0x10000 | TWO_TIPS[1]['titleId'], 0, 0))
        self.assertEqual(built['list'][-4:], bytes(4))
        self.assertEqual(struct.unpack_from('<I', built['checks'], 8)[0], 19)
        self.assertEqual(built['checks'][12 + 8 * 17:20 + 8 * 17], bytes((0xFF, 0xFF, 255, 0)) + b'\xff' * 4)
        self.assertEqual(built['checks'][12 + 8 * 18:20 + 8 * 18], bytes((0xFF, 0xFF, 17, 0)) + b'\xff' * 4)
        old, new = pages(first['pages']), pages(built['pages'])
        self.assertEqual(len(new), 19)
        self.assertEqual([t for _, t in new[:17]], [t for _, t in old])   # existing pages untouched
        self.assertEqual(decode(new[17][1]), TWO_TIPS[0]['body'])
        self.assertEqual(decode(new[18][1]), TWO_TIPS[1]['body'])
        self.assertEqual(len(built['pages']) % 4, 0)
        self.assertEqual(build(pinned(second, built), first), built)

    def test_title_rows_written_and_used_rows_rejected(self):
        sources = synth_sources()
        draft = patch_for(sources, tips=TWO_TIPS)
        patch = pinned(draft, build(draft, sources))
        built = build(patch, sources)
        raw = built['titles']
        sub = struct.unpack_from('<I', raw, 20)[0] + 12
        for tip in TWO_TIPS:
            off = struct.unpack_from('<I', raw, sub + 4 + 4 * tip['titleRow'])[0]
            self.assertEqual(decode(raw[sub + off:raw.index(b'\0', sub + off)]), tip['title'])
        used = copy.deepcopy(patch)
        used['tips'] = [dict(TWO_TIPS[0], titleRow=31, titleId=0x16E0 + 31)]  # row 31 holds a real string
        with self.assertRaises(ValueError):
            C.tip_title(sources['titles'], used)
        wrong_id = copy.deepcopy(patch)
        wrong_id['tips'] = [dict(TWO_TIPS[0], titleRow=25, titleId=0x16E0 + 24)]  # row ID mismatch
        with self.assertRaises(ValueError):
            C.tip_title(sources['titles'], wrong_id)

    def test_free_title_rows_skips_text_and_referenced_rows(self):
        titles = synth_titles([(0x100, b'Alpha'), (0x101, b''), (0x102, b''), (0x103, b'Beta'), (0x104, b'')])
        book = synth_list([(0x10102, 0)], categories=1)                   # a book lists row 2's ID
        self.assertEqual(C.free_title_rows(titles, [book]), [1, 4])
        self.assertEqual(C.free_title_rows(titles, []), [1, 2, 4])

    def test_build_files_uses_the_reader(self):
        sources = synth_sources()
        files = C.build_files(lambda rel: sources[RESULT_KEYS[rel.rsplit('/', 1)[1]]],
                              patch_for(sources, tip=ONE_TIP))
        self.assertEqual(sorted(files), sorted(C.DEPLOY + rel for rel in C.SOURCES))


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class TipResourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ivalice_legacy.vbf import VBF
        cls.game = Path(os.environ['IVALICE_LEGACY_GAME'])
        cls.archive = VBF(cls.game/'FFXII_TZA.vbf')
        cls.deploy = cls.game/'mods/deploy/ff12data'

    def read(self, path):
        return self.archive.read(path)

    def deployed(self, rel):
        return (self.deploy / rel).read_bytes()

    def families_source(self, rel):
        """The families patch's pre-patch bytes for one source file.

        Before 0.1.33 installs, that is the deployed file. Once the tips are deployed, the deployed file must equal
        the patch's result pin, and the pre-patch copy is read from a previous release's Vortex staging folder
        (found by its pinned hash).
        """
        name = rel.rsplit('/', 1)[1]
        key = next(k for k, n in SOURCE_KEYS.items() if n == name)
        data = self.deployed(rel)
        if C.sha(data) == FAMILIES[key]:
            return data
        self.assertIn(C.sha(data), (FAMILIES['resultSha256'][RESULT_KEYS[name]], PREVIOUS_PAGES),
                      'deployed file is neither the families source nor a families result: ' + rel)
        manifest = json.loads((self.game / 'vortex.deployment.json').read_text(encoding='utf-8'))
        for staged in sorted(Path(manifest['stagingPath']).glob('IvaliceLegacy-*/mods/deploy/ff12data/' + rel)):
            raw = staged.read_bytes()
            if C.sha(raw) == FAMILIES[key]:
                return raw
        self.skipTest('families tips are deployed and no staged pre-patch copy was found')

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

    def test_proficiency_build_files(self):
        """The existing single-tip patch still builds byte-identically from the vanilla archive."""
        files = C.build_files(self.archive.read, PATCH)
        self.assertEqual(sorted(files), sorted(C.DEPLOY + rel for rel in C.SOURCES))
        for rel, data in files.items():
            self.assertEqual(C.sha(data), PATCH['resultSha256'][RESULT_KEYS[rel.rsplit('/', 1)[1]]])

    def test_families_sources_are_the_deployed_files(self):
        """The families patch is pinned to the deployed 17-entry state (or, once installed, its result)."""
        for key, name in SOURCE_KEYS.items():
            rel = next(r for r in C.SOURCES if r.rsplit('/', 1)[1] == name)
            self.assertIn(C.sha(self.deployed(rel)), (FAMILIES[key], FAMILIES['resultSha256'][RESULT_KEYS[name]],
                                                      PREVIOUS_PAGES))
            self.assertEqual(C.sha(self.families_source(rel)), FAMILIES[key])
        self.assertNotEqual(C.sha(self.deployed(C.BOOK + '.bin')), C.sha(self.read(C.BOOK + '.bin')))

    def test_families_build_from_deployed(self):
        """17 -> 21 entries against the deployed files, everything the patch declares verified."""
        files = C.build_files(self.families_source, FAMILIES)
        listing = files[C.DEPLOY + C.BOOK + '.bin']
        self.assertEqual(struct.unpack_from('<H', listing, 0x0E)[0], 21)
        for i, tip in enumerate(C.tips(FAMILIES)):
            self.assertEqual(struct.unpack_from('<III', listing, 0x30 + 12 * (17 + i)),
                             (0x10000 | tip['titleId'], tip['category'], 0))
        self.assertEqual(listing[-4:], bytes(4))
        checks = files[C.DEPLOY + C.BOOK + '.dat']
        self.assertEqual(struct.unpack_from('<I', checks, 8)[0], 21)
        for i in range(4):
            self.assertEqual(checks[12 + 8 * (17 + i):20 + 8 * (17 + i)], bytes((0xFF, 0xFF, 0xFF, 0)) + b'\xff' * 4)
        new = pages(files[C.DEPLOY + C.BOOK + '000.dat'])
        self.assertEqual(len(new), 21)
        for i, tip in enumerate(C.tips(FAMILIES)):
            self.assertEqual(struct.unpack_from('<H', new[17 + i][0], 2)[0], 17 + i)
            self.assertEqual(decode(new[17 + i][1]), tip['body'])
        self.assertEqual(len(files[C.DEPLOY + C.BOOK + '000.dat']) % 4, 0)

    def test_families_title_rows(self):
        source = self.families_source(C.TITLES)
        built = C.tip_title(source, FAMILIES)
        def title(raw, row):
            text = struct.unpack_from('<I', raw, 20)[0]
            sub = text + 12
            off = struct.unpack_from('<I', raw, sub + 4 + 4 * row)[0]
            return decode(raw[sub + off:raw.index(b'\0', sub + off)])
        self.assertEqual([title(built, r) for r in (654, 655, 656, 657)],
                         [t['title'] for t in C.tips(FAMILIES)])
        for row in (658, 659, 660, 661, 662, 681):
            self.assertEqual(title(built, row), title(source, row))

    def test_free_title_rows_real(self):
        """How the four title rows were found: empty rows whose ID no handbook book lists."""
        books = [self.read(n) for n in self.archive.names
                 if n.startswith('ps2data/image/ff12/myoshiok/us/handbook/') and n.endswith('.bin')
                 and self.read(n)[:4] == b'hctgf']
        free = C.free_title_rows(self.read(C.TITLES), books)
        self.assertIn(661, free)                                  # the Proficiency tip's row, free in vanilla
        for row in (654, 655, 656, 657, 660):
            self.assertIn(row, free)
        self.assertNotIn(658, free)                               # bestiary's two tutorial-page categories
        self.assertNotIn(659, free)
        deployed_free = C.free_title_rows(self.families_source(C.TITLES), books)
        for row in (654, 655, 656, 657, 660):
            self.assertIn(row, deployed_free)
        self.assertNotIn(661, deployed_free)                      # taken by the deployed Proficiency tip


if __name__ == '__main__':
    unittest.main()
