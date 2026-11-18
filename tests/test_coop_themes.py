"""Host tests for the HUD themes (Phase 8: native/coop/src/overlay/theme*, font, marks; the hud_theme setting).

* the built-ins: legacy-bold (C, the default), legacy-humanist (A) and legacy-crisp (B) read from their embedded
  theme.json with the loader's own rules; their tokens are the theme files' values (shape, tab, focus bar, gauge, sizes,
  the carousel defaults), every engine icon name resolves, the tables are in order;
* player themes read from a folder: a valid package (the artist's crisp package under another id) passes, and every
  refusal rule refuses without crashing (bad JSON, format, version, id, folder name, a built-in's id, contrast, ranges,
  seats, stops, cut corners, an icon outside the set, an icons file outside the folder, a damaged or truncated atlas,
  a digest that does not match, oversized files);
* the module: the folder scan lists the built-ins then the valid player themes and counts the refused ones; a wanted
  theme is read on the co-op beat and taken at a render-frame boundary; a missing or broken one keeps the default; a
  texture failure falls back to the default for the launch;
* the font: UTF-8 decoding, accents kerning as their base letter, the space floor, measure and fit with the extra advance;
* the settings field: hud_theme validated by the bridge (id characters, zeros after), GET_THEMES' reply.
No game run, nothing deployed.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from coop_native_support import native_test
from test_coop_input_service import fresh_test_dll

ROOT = Path(__file__).resolve().parents[1]
THEMES_SRC = ROOT / 'native' / 'coop' / 'src' / 'overlay' / 'themes'
PACKAGES = Path(os.environ.get('ILC_HUD_THEMES', 'E:/Modding/games/ff12/workspace/hud-art-20261116/themes'))

u8, u16, u32, u64, i32, f32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32, ctypes.c_float
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


ThInfo = S('ThInfo', [('id', ctypes.c_char * 33), ('name', ctypes.c_char * 33)] +
           [(n, u8) for n in ('win_shape', 'tab_kind', 'gauge_kind', 'focus_bar', 'status_style', 'status_count', 'shine',
                              'page_face', 'marquee_on', 'trail_hatch', 'win_stops', 'faces')] +
           [(n, f32) for n in ('row_em', 'help_em', 'outline', 'win_cut', 'win_radius', 'gauge_h', 'gauge_slant',
                               'marquee_speed', 'marquee_fade')] +
           [(n, u32) for n in ('marquee_hold_ms', 'core', 'ink', 'hp_warn', 'hp_critical', 'drop_rare', 'seat0')] +
           [('recipe_n', u32 * 5)] + [(n, u32) for n in ('icons', 'glyphs', 'kerns', 'atlas_w', 'atlas_h')] +
           [('role_face', u8 * 3), ('reserved', u8), ('title_track', f32), ('header_track', f32)])
Header = S('Header', [('magic', u32), ('abi_version', u16), ('record_type', u16), ('size', u32), ('generation', u32)])
Entry = S('Entry', [('id', ctypes.c_char * 32), ('name', ctypes.c_char * 32), ('flags', u8), ('reserved', u8 * 3)])
Reply = S('Reply', [('header', Header), ('count', u32), ('refused', u32), ('scans', u32), ('reserved', u8 * 4),
                    ('entries', Entry * 16)])
Status = S('Status', [('active_id', ctypes.c_char * 33), ('wanted_id', ctypes.c_char * 33), ('pad', u8 * 2),
                      ('players_valid', u32), ('players_refused', u32), ('scans', u32), ('applied', u32),
                      ('players_ignored', u32)])

SHAPE_ROUND, SHAPE_CRISP, SHAPE_CHAMFER = 0, 1, 2
TAB_A, TAB_B, TAB_C = 0, 1, 2
GAUGE_A, GAUGE_B, GAUGE_C = 0, 1, 2
FACE_BODY, FACE_TITLE, FACE_NUMERALS, FACE_ICONS = 0, 1, 2, 3
(OK, NO_FILE, TOO_LARGE, READ_FAILED, BAD_JSON, BAD_FORMAT, BAD_VERSION, MISSING, BAD_VALUE, BAD_ID, BUILTIN_ID,
 BAD_ATLAS, BAD_DIGEST, BAD_ICONS, NO_MEMORY) = range(15)
THEME_BUILTIN, THEME_DEFAULT, THEME_ACTIVE, THEME_SELECTED = 0x01, 0x02, 0x04, 0x08
OP_GET_THEMES = 11
ABI_MAGIC = 0x42434C49

SIG = {
    'ILCT_ThInfoSize': ([], u32), 'ILCT_ThInfo': ([P(ThInfo)], None),
    'ILCT_ThSelectBuiltin': ([u32, ctypes.c_char_p, u32], i32), 'ILCT_ThSelectDefault': ([], None),
    'ILCT_ThLoadPlayer': ([ctypes.c_wchar_p, ctypes.c_char_p, ctypes.c_char_p, u32], u32),
    'ILCT_ThReadJson': ([ctypes.c_char_p, u32, ctypes.c_char_p, ctypes.c_char_p, u32], u32),
    'ILCT_ThRefusalText': ([u32], ctypes.c_char_p), 'ILCT_ThReset': ([], None),
    'ILCT_ThSetRoot': ([ctypes.c_wchar_p], None), 'ILCT_ThConfigure': ([ctypes.c_char_p], None),
    'ILCT_ThStep': ([u64], None), 'ILCT_ThTakePending': ([], i32), 'ILCT_ThAtlasGeneration': ([], u32),
    'ILCT_ThHasAtlas': ([], i32), 'ILCT_ThNoteAtlasFailed': ([], None), 'ILCT_ThList': ([P(Reply)], None),
    'ILCT_ThStatus': ([P(Status)], None), 'ILCT_ThStatusSize': ([], u32),
    'ILCT_FtKern': ([u32, u32, u32], i32), 'ILCT_FtNextCode': ([ctypes.c_char_p, u32, P(u32)], u32),
    'ILCT_FtBaseOf': ([u32], u32), 'ILCT_FtMeasureX': ([u32, f32, ctypes.c_char_p, u32, f32], f32),
    'ILCT_FtFitX': ([u32, f32, ctypes.c_char_p, u32, f32, f32, P(i32), P(f32)], u32),
    'ILCT_FtHasGlyph': ([u32, u32], i32), 'ILCT_FtIconId': ([u32], i32), 'ILCT_FtSetPixelScale': ([f32], None),
    'ILCT_FtTablesValid': ([], i32), 'ILC_Bridge': ([u64, ctypes.c_void_p, u64, ctypes.c_void_p, u64], i32),
    'ILCT_ThNameGlyphs': ([ctypes.c_char_p, u32, ctypes.c_char_p, P(u8), P(u16), u32], u32),
    'ILCT_ThGaugeTicks': ([f32], u32),
    'ILCT_OvLogCapture': ([i32], None), 'ILCT_OvStep': ([u64], None), 'ILCT_OvLogText': ([], ctypes.c_char_p),
}


def declare(d):
    for name, (args, res) in SIG.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def rgba(r, g, b, a=255):
    return r | g << 8 | b << 16 | a << 24


def theme_doc(name='legacy-crisp'):
    return json.loads((THEMES_SRC / name / 'theme.json').read_text(encoding='utf-8'))


@native_test
class ThemeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())
        cls.d.ILCT_ThReset()

    def tearDown(self):
        self.d.ILCT_ThSelectDefault()

    def info(self) -> ThInfo:
        i = ThInfo()
        self.d.ILCT_ThInfo(ctypes.byref(i))
        return i

    def read_json(self, doc, folder='legacy-crisp'):
        text = json.dumps(doc).encode('utf-8') if not isinstance(doc, bytes) else doc
        what = ctypes.create_string_buffer(96)
        code = self.d.ILCT_ThReadJson(text, len(text), folder.encode() if folder else None, what, 96)
        return code, what.value.decode()


class LayoutTest(ThemeCase):
    def test_struct_sizes(self):
        self.assertEqual(self.d.ILCT_ThInfoSize(), ctypes.sizeof(ThInfo))
        self.assertEqual(self.d.ILCT_ThStatusSize(), ctypes.sizeof(Status))
        self.assertEqual(ctypes.sizeof(Reply), 1120)


class BuiltinTest(ThemeCase):
    def select(self, k):
        what = ctypes.create_string_buffer(96)
        self.assertEqual(self.d.ILCT_ThSelectBuiltin(k, what, 96), 1, what.value)
        return self.info()

    def test_the_default_is_bold_modern(self):
        i = self.info()
        self.assertEqual((i.id, i.name), (b'legacy-bold', b'Bold Modern'))

    def test_each_builtin_reads_with_the_loader_rules(self):
        want = {0: ('legacy-bold', SHAPE_CHAMFER, TAB_C, GAUGE_C, 1, 21.0, 2.0),
                1: ('legacy-humanist', SHAPE_ROUND, TAB_A, GAUGE_A, 0, 22.0, 1.5),
                2: ('legacy-crisp', SHAPE_CRISP, TAB_B, GAUGE_B, 1, 22.0, 1.2)}
        for k, (tid, shape, tab, gauge, bar, row, outline) in want.items():
            with self.subTest(theme=tid):
                i = self.select(k)
                self.assertEqual((i.id.decode(), i.win_shape, i.tab_kind, i.gauge_kind, i.focus_bar),
                                 (tid, shape, tab, gauge, bar))
                self.assertAlmostEqual(i.row_em, row, places=4)
                self.assertAlmostEqual(i.outline, outline, places=4)
                self.assertEqual(i.help_em, 17.0)
                self.assertEqual((i.faces, i.icons, list(i.role_face)), (3, 22, [0, 1, 2]))
                self.assertEqual(list(i.recipe_n), [len(theme_doc(tid)['recipes'][r]) for r in
                                                    ('glove', 'chevron', 'seat_diamond', 'dot', 'status_icon')])
                self.assertEqual(i.status_count, 6)
                self.assertEqual(self.d.ILCT_FtTablesValid(), 1)
                for name in range(22):
                    self.assertNotEqual(self.d.ILCT_FtIconId(name), 0xFFFF, name)

    def test_the_tokens_are_the_files_values(self):
        doc = theme_doc('legacy-bold')
        i = self.select(0)
        self.assertEqual(i.win_cut, doc['window']['cut'])
        self.assertEqual(i.win_stops, len(doc['window']['stops']))
        self.assertAlmostEqual(i.gauge_h, doc['gauge']['height'])
        self.assertAlmostEqual(i.gauge_slant, doc['gauge']['slant'])
        self.assertEqual(i.core, rgba(*doc['colors']['core']))
        self.assertEqual(i.hp_warn, rgba(*doc['colors']['hp_warn']))
        self.assertEqual(i.seat0, rgba(*doc['seats'][0]['rgb']))
        self.assertEqual(i.trail_hatch, 1)

    def test_motion_and_tracking_are_the_files_values(self):
        """The artist's motion and text.tracking in all three built-ins (THEME-FORMAT.md 2's names)."""
        want = {0: ('legacy-bold', (36.0, 1100, 14.0, 1), FACE_TITLE, 0.05, 0.05),
                2: ('legacy-crisp', (40.0, 1000, 12.0, 1), FACE_TITLE, 0.07, 0.09),
                1: ('legacy-humanist', (40.0, 1000, 12.0, 1), FACE_BODY, 0.0, 0.06)}
        for k, (tid, motion, face, title, header) in want.items():
            with self.subTest(theme=tid):
                doc = theme_doc(tid)
                self.assertEqual(sorted(doc['text']['tracking']), ['header_em', 'page_face', 'title_em'])
                i = self.select(k)
                self.assertEqual((i.marquee_speed, i.marquee_hold_ms, i.marquee_fade, i.marquee_on), motion)
                self.assertEqual(i.page_face, face)
                self.assertAlmostEqual(i.title_track, title, places=5)
                self.assertAlmostEqual(i.header_track, header, places=5)
        i = self.select(2)
        self.assertAlmostEqual(theme_doc('legacy-crisp')['gauge']['ticks_every'], 0.1)
        self.assertEqual(self.d.ILCT_ThGaugeTicks(0.1) - self.d.ILCT_ThGaugeTicks(0.0), 9, 'crisp: nine inner ticks')

    def test_the_default_icon_order_is_the_converters(self):
        """Review 9: inherited recipes are remapped from DEFAULT_ICON_NAMES positions; make_builtin refuses a default
        whose atlas has another order (it reads here, so the order holds)."""
        what = ctypes.create_string_buffer(96)
        self.assertEqual(self.d.ILCT_ThSelectBuiltin(0, what, 96), 1, what.value)
        self.assertEqual(self.info().icons, 22)


class JsonRuleTest(ThemeCase):
    def test_the_crisp_file_passes(self):
        self.assertEqual(self.read_json(theme_doc()), (OK, ''))

    def test_motion_tokens(self):
        """Review 3: the spec's ranges (speed 10..120, hold 0..5000, fade 0..40, marquee_on a bool), no unknown key."""
        doc = theme_doc()
        doc['motion'] = {'marquee_speed': 60, 'marquee_hold_ms': 500, 'marquee_fade': 8, 'marquee_on': False}
        self.assertEqual(self.read_json(doc)[0], OK)
        for key, value, code in (('marquee_speed', 10, OK), ('marquee_speed', 120, OK), ('marquee_speed', 9.9, BAD_VALUE),
                                 ('marquee_speed', 121, BAD_VALUE), ('marquee_speed', 0, BAD_VALUE),
                                 ('marquee_speed', 500, BAD_VALUE), ('marquee_hold_ms', 0, OK),
                                 ('marquee_hold_ms', 5000, OK), ('marquee_hold_ms', 5001, BAD_VALUE),
                                 ('marquee_hold_ms', -1, BAD_VALUE), ('marquee_fade', 0, OK), ('marquee_fade', 40, OK),
                                 ('marquee_fade', 40.5, BAD_VALUE), ('marquee_on', True, OK), ('marquee_on', 1, BAD_VALUE),
                                 ('marquee_on', 'yes', BAD_VALUE)):
            with self.subTest(key=key, value=value):
                d = theme_doc()
                d['motion'] = {key: value}
                self.assertEqual(self.read_json(d)[0], code)
        d = theme_doc()
        d['motion'] = {'marquee_speed': 40, 'marquee_sped': 40}
        self.assertEqual(self.read_json(d), (BAD_VALUE, 'motion: unknown key "marquee_sped" (marquee_speed, '
                                                        'marquee_hold_ms, marquee_fade, marquee_on)'))

    def test_tracking_keys_are_the_specs(self):
        """Review 5: text.tracking = {page_face, title_em, header_em}; the old engine names are refused by name."""
        doc = theme_doc()
        doc['text']['tracking'] = {'page_face': 'title', 'title_em': 0.3, 'header_em': 0.0}
        self.assertEqual(self.read_json(doc)[0], OK)
        for bad in ({'page_em': 0.05}, {'panel_em': 0.05}, {'label_em': 0.06}, {'title_em': 0.05, 'extra': 1}):
            with self.subTest(tracking=bad):
                d = theme_doc()
                d['text']['tracking'] = bad
                code, what = self.read_json(d)
                self.assertEqual(code, BAD_VALUE)
                self.assertTrue(what.startswith('text.tracking: unknown key "'), what)
        for bad in ({'title_em': 0.31}, {'header_em': -0.01}, {'page_face': 'numerals'}):
            with self.subTest(tracking=bad):
                d = theme_doc()
                d['text']['tracking'] = bad
                self.assertEqual(self.read_json(d)[0], BAD_VALUE)
        d = theme_doc()
        d['text']['tracking'] = [0.05]
        self.assertEqual(self.read_json(d), (BAD_VALUE, 'text.tracking must be an object'))

    def test_gauge_ticks(self):
        """Review ★1: ticks_every 0 (none) or 0.02..0.5; the old probe (1e-9) and 1e-6 are refused, and the drawing is
        bounded on its own: floor(1 / ticks) ticks, at most 50, for any value the tokens might hold."""
        for value, code in ((0, OK), (0.02, OK), (0.5, OK), (0.25, OK), (1e-9, BAD_VALUE), (1e-6, BAD_VALUE),
                            (0.0199, BAD_VALUE), (0.51, BAD_VALUE)):
            with self.subTest(ticks=value):
                d = theme_doc()
                d['gauge']['ticks_every'] = value
                code_got, what = self.read_json(d)
                self.assertEqual(code_got, code, what)
                if code == BAD_VALUE and value < 0.02:
                    self.assertEqual(what, 'gauge.ticks_every must be 0 (no ticks) or 0.02 to 0.5')
        base = self.d.ILCT_ThGaugeTicks(0.0)
        self.assertNotEqual(base, 0xFFFFFFFF)
        for ticks, inner in ((0.5, 1), (0.25, 3), (0.1, 9), (0.02, 49), (1e-6, 50), (1e-9, 50), (1e-30, 50),
                             (float('inf'), 0), (float('nan'), 0), (-0.1, 0)):
            with self.subTest(ticks=ticks):
                self.assertEqual(self.d.ILCT_ThGaugeTicks(ticks) - base, inner)

    def test_contrast_uses_the_drawn_outline(self):
        """Review 4: the 7:1 and 4.5:1 rules are checked against text.ink when the theme gives one (it is the outline
        every text style draws with), else colors.ink."""
        d = theme_doc()
        d['text']['ink'] = list(d['colors']['core'])
        code, what = self.read_json(d)
        self.assertEqual((code, what), (BAD_VALUE, 'colors.core vs text.ink (the text outline) below 7:1'))
        d = theme_doc()
        d['text']['ink'] = [120, 90, 60]
        d['colors']['core'] = [255, 255, 255]
        d['colors']['hp_critical'] = [200, 60, 50]
        code, what = self.read_json(d)
        self.assertEqual(code, BAD_VALUE, what)
        self.assertIn('vs text.ink (the text outline) below', what)
        d = theme_doc()
        d['colors']['ink'] = [200, 200, 200]     # unreadable as an outline, but text.ink replaces it
        d['text']['ink'] = [0, 0, 0]
        self.assertEqual(self.read_json(d), (OK, ''))
        d = theme_doc()
        d['colors']['ink'] = list(d['colors']['core'])
        self.assertEqual(self.read_json(d), (OK, ''), 'crisp gives text.ink: colors.ink is not the drawn outline')
        d['text'].pop('ink')
        self.assertEqual(self.read_json(d), (BAD_VALUE, 'colors.core vs colors.ink (the text outline) below 7:1'))

    def test_object_member_cap(self):
        """Review 6: at most 512 members in one object, refused before the (quadratic) duplicate check."""
        d = theme_doc()
        d['sparkles'] = {f'k{n:03d}': n for n in range(512)}
        self.assertEqual(self.read_json(d)[0], OK, 'an unknown section of 512 keys is still ignored')
        d['sparkles'] = {f'k{n:03d}': n for n in range(513)}
        self.assertEqual(self.read_json(d)[0], BAD_JSON)

    def test_inherited_status_layers_resolve_by_name(self):
        """Review 9: a {name} layer of the default's status recipe, inherited by a theme with its own status order, is
        resolved by status name (glyph[k] = pic_<status k>), not by the default's slot index."""
        names = ['glove_body', 'glove_cuff', 'chevron_up', 'chevron_down', 'chevron_left', 'chevron_right', 'diamond',
                 'dot', 'badge', 'badge_ring', 'pic_poison', 'det_poison', 'pic_slow', 'det_slow', 'pic_protect',
                 'det_protect', 'pic_haste', 'det_haste', 'pic_sleep', 'det_sleep', 'pic_oil', 'det_oil']
        bit = {'poison': 8, 'slow': 15, 'protect': 18, 'haste': 20, 'sleep': 4, 'oil': 11, 'regen': 25}
        for given in (False, True):
            with self.subTest(recipes_given=given):
                d = theme_doc()
                if not given:
                    d.pop('recipes')
                d['status_icons']['names'] = ['Oil', 'Poison', 'Regen', 'Haste']
                d['status_icons']['colors'] = {}
                text = json.dumps(d).encode('utf-8')
                bits, glyphs = (u8 * 8)(), (u16 * 8)()
                n = self.d.ILCT_ThNameGlyphs(text, len(text), b'legacy-crisp', bits, glyphs, 8)
                self.assertEqual(n, 4)
                self.assertEqual(list(bits[:4]), [bit['oil'], bit['poison'], bit['regen'], bit['haste']])
                self.assertEqual(list(glyphs[:4]), [names.index('pic_oil'), names.index('pic_poison'), 0xFFFF,
                                                    names.index('pic_haste')], 'Regen has no icon: it skips the layer')

    def test_refusals(self):
        def edit(f):
            doc = theme_doc()
            f(doc)
            return doc
        cases = [
            ('not JSON', b'{"format": "ilc-theme",', BAD_JSON),
            ('trailing comma', b'{"format": "ilc-theme",}', BAD_JSON),
            ('duplicate key', b'{"id": "a", "id": "b"}', BAD_JSON),
            ('format', edit(lambda d: d.update(format='other')), BAD_FORMAT),
            ('version', edit(lambda d: d.update(version=2)), BAD_VERSION),
            ('no window', edit(lambda d: d.pop('window')), MISSING),
            ('id chars', edit(lambda d: d.update(id='Legacy Crisp')), BAD_ID),
            ('folder name', edit(lambda d: d.update(id='other-name')), BAD_ID),
            ('row', edit(lambda d: d['sizes'].update(row=31)), BAD_VALUE),
            ('help', edit(lambda d: d['sizes'].update(help=16)), BAD_VALUE),
            ('pitch', edit(lambda d: d['sizes'].update(pitch=24)), BAD_VALUE),
            ('outline', edit(lambda d: d['text'].update(outline=0.5)), BAD_VALUE),
            ('core vs ink', edit(lambda d: d['colors'].update(core=[40, 40, 50])), BAD_VALUE),
            ('hp_warn vs ink', edit(lambda d: d['colors'].update(hp_warn=[30, 20, 10])), BAD_VALUE),
            ('colour shape', edit(lambda d: d['colors'].update(gold=[255, 255])), BAD_VALUE),
            ('three seats', edit(lambda d: d['seats'].pop()), BAD_VALUE),
            ('seat numbers', edit(lambda d: d['seats'][1].update(n=3)), BAD_VALUE),
            ('one stop', edit(lambda d: d['window'].update(stops=[[0, [1, 2, 3, 4]]])), BAD_VALUE),
            ('stops order', edit(lambda d: d['window'].update(stops=[[0.6, [1, 2, 3, 4]], [0.2, [1, 2, 3, 4]]])), BAD_VALUE),
            ('shape', edit(lambda d: d['window'].update(shape='star')), BAD_VALUE),
            ('cut corners', edit(lambda d: d['window'].update(shape='chamfer', cut=10,
                                                               cut_corners=['top_right', 'bottom_left'])), BAD_VALUE),
            ('gauge height', edit(lambda d: d['gauge'].update(height=20)), BAD_VALUE),
            ('unknown recipe icon', edit(lambda d: d['recipes']['dot'][0].update(glyph='sparkle')), BAD_ICONS),
            ('unknown colour ref', edit(lambda d: d['recipes']['dot'][0].update(core='rainbow')), BAD_VALUE),
            ('status name', edit(lambda d: d['status_icons'].update(names=['Poison', 'Fizz'])), BAD_VALUE),
            ('fonts vs atlas', edit(lambda d: d['fonts'].pop('numerals')), BAD_ATLAS),
        ]
        for label, doc, code in cases:
            with self.subTest(label):
                got, what = self.read_json(doc)
                self.assertEqual(got, code, f'{label}: {what}')
        self.assertEqual(self.d.ILCT_ThRefusalText(BAD_DIGEST), b'the atlas digest does not match')

    def test_unknown_sections_are_ignored(self):
        doc = theme_doc()
        doc['sparkles'] = {'anything': 1}
        self.assertEqual(self.read_json(doc)[0], OK)


@unittest.skipUnless((PACKAGES / 'legacy-crisp' / 'atlas' / 'theme.atlas').is_file(), 'the theme packages')
class PlayerThemeTest(ThemeCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='ilc-themes-'))
        self.root = self.tmp / 'themes'
        self.root.mkdir()

    def tearDown(self):
        super().tearDown()
        self.d.ILCT_ThReset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def package(self, folder='my-crisp', edit=None, atlas_edit=None, with_icons=True):
        src = PACKAGES / 'legacy-crisp'
        dst = self.root / folder
        (dst / 'atlas').mkdir(parents=True)
        doc = json.loads((src / 'theme.json').read_text(encoding='utf-8'))
        doc['id'] = folder
        doc['name'] = 'My Crisp \u00e9t\u00e9'
        if with_icons:
            (dst / 'icons').mkdir()
            shutil.copyfile(src / 'icons' / 'icons.json', dst / 'icons' / 'icons.json')
        else:
            doc.pop('icons', None)
        if edit:
            edit(doc, dst)
        (dst / 'theme.json').write_text(json.dumps(doc), encoding='utf-8')
        data = bytearray((src / 'atlas' / 'theme.atlas').read_bytes())
        if atlas_edit:
            data = atlas_edit(data)
        (dst / 'atlas' / 'theme.atlas').write_bytes(bytes(data))
        return dst

    def load(self, dst, folder=None):
        what = ctypes.create_string_buffer(96)
        code = self.d.ILCT_ThLoadPlayer(str(dst), (folder or dst.name).encode(), what, 96)
        return code, what.value.decode()

    def test_a_valid_player_theme(self):
        self.assertEqual(self.load(self.package()), (OK, ''))
        i = self.info()
        self.assertEqual(i.id, b'my-crisp')
        self.assertEqual(i.name, b'My Crisp ?t?', 'non-ASCII shown as ?')
        self.assertEqual((i.win_shape, i.tab_kind, i.icons), (SHAPE_CRISP, TAB_B, 22))
        self.assertEqual(self.d.ILCT_ThHasAtlas(), 1)
        self.assertEqual(self.d.ILCT_FtTablesValid(), 1)

    def test_without_an_icons_file_the_default_order_is_used(self):
        self.assertEqual(self.load(self.package(with_icons=False))[0], OK)

    def test_device_like_names_that_are_not_devices_pass(self):
        """The DOS device rule matches the stem exactly: 'console', 'com10' and 'nulls' are ordinary names."""
        def edit(doc, dst):
            (dst / 'console').mkdir()
            shutil.copyfile(dst / 'icons' / 'icons.json', dst / 'console' / 'com10.json')
            doc['icons'] = {'file': 'console/com10.json'}
        self.assertEqual(self.load(self.package('device-like', edit=edit)), (OK, ''))

    def test_missing_motion_and_tracking_take_the_spec_defaults(self):
        """THEME-FORMAT.md 2: without text.tracking and motion a theme gets body / 0 / 0.06 and 40 / 1000 / 12 / true,
        whatever theme it starts from (the default's own are title / 0.05 / 0.05 and 36 / 1100 / 14)."""
        def edit(doc, dst):
            doc['text'].pop('tracking', None)
            doc.pop('motion', None)
        self.assertEqual(self.load(self.package('no-extras', edit=edit)), (OK, ''))
        i = self.info()
        self.assertEqual((i.page_face, i.marquee_speed, i.marquee_hold_ms, i.marquee_fade, i.marquee_on),
                         (FACE_BODY, 40.0, 1000, 12.0, 1))
        self.assertAlmostEqual(i.title_track, 0.0)
        self.assertAlmostEqual(i.header_track, 0.06, places=5)
        def partial(doc, dst):
            doc['text']['tracking'] = {'header_em': 0.2}
            doc['motion'] = {'marquee_on': False}
        self.assertEqual(self.load(self.package('partial-extras', edit=partial)), (OK, ''))
        i = self.info()
        self.assertEqual((i.page_face, i.marquee_speed, i.marquee_on), (FACE_BODY, 40.0, 0))
        self.assertAlmostEqual(i.header_track, 0.2, places=5)

    def test_valid_themes_past_the_list_are_logged_as_ignored(self):
        """Nit: a valid player theme past the 13 player slots gets its own hook.log line, not a silent refusal."""
        for n in range(14):
            self.package(f'theme-{n:02d}')
        self.d.ILCT_ThReset()
        self.d.ILCT_OvLogCapture(1)
        try:
            self.d.ILCT_ThSetRoot(str(self.root))
            self.d.ILCT_OvStep(1000)
            log = (self.d.ILCT_OvLogText() or b'').decode('utf-8', 'replace')
        finally:
            self.d.ILCT_OvLogCapture(0)
        r = Reply()
        self.d.ILCT_ThList(ctypes.byref(r))
        self.assertEqual(r.count, 16)
        self.assertEqual([r.entries[k].id.decode() for k in range(3, 16)], [f'theme-{n:02d}' for n in range(13)])
        self.assertIn('HUD theme "theme-13" ignored: too many themes', log)
        self.assertNotIn('"theme-13" refused', log)
        self.assertEqual(r.refused, 0, 'a valid theme left out for lack of slots is not a refusal')
        st = Status()
        self.d.ILCT_ThStatus(ctypes.byref(st))
        self.assertEqual((st.players_valid, st.players_refused, st.players_ignored), (13, 0, 1))

    def test_ignored_and_refused_are_counted_apart(self):
        """Nit (theme.cpp scan): with a broken package and a folder that is not an id beside 14 valid themes, the
        refused count is exactly those two; the valid theme past the slots is counted as ignored only."""
        for n in range(14):
            self.package(f'theme-{n:02d}')
        self.package('zzz-broken', atlas_edit=lambda d: d[:-50])
        (self.root / 'Not An Id').mkdir()
        self.d.ILCT_ThReset()
        self.d.ILCT_ThSetRoot(str(self.root))
        r = Reply()
        self.d.ILCT_ThList(ctypes.byref(r))
        self.assertEqual(r.count, 16)
        self.assertEqual(r.refused, 2, 'the broken package and the folder that is not an id; not the ignored theme')
        st = Status()
        self.d.ILCT_ThStatus(ctypes.byref(st))
        self.assertEqual((st.players_valid, st.players_refused, st.players_ignored), (13, 2, 1))

    def test_atlas_refusals(self):
        def flip_rle(d):
            # A literal sample of the run-length data (not a zero run marker or its length): the size still decodes.
            i = len(d) - 500
            while d[i] in (0, 1) or d[i - 1] == 0:
                i -= 1
            d[i] ^= 0x01
            return d
        def flip_digest(d):
            d[40] ^= 0xFF
            return d
        cases = [
            ('magic', lambda d: b'XXXX' + bytes(d[4:]), BAD_ATLAS),
            ('version', lambda d: d[:4] + struct.pack('<H', 3) + d[6:], BAD_ATLAS),
            ('truncated', lambda d: d[:-100], BAD_ATLAS),
            ('trailing bytes', lambda d: d + b'\0' * 8, BAD_ATLAS),
            ('glyph count', lambda d: d[:18] + struct.pack('<I', 5000) + d[22:], BAD_ATLAS),
            ('sample byte', flip_rle, BAD_DIGEST),
            ('digest', flip_digest, BAD_DIGEST),
            ('reserved byte', lambda d: d[:62 + 8 * 4 + 5] + b'\x07' + d[62 + 8 * 4 + 6:], BAD_ATLAS),
        ]
        for label, f, code in cases:
            with self.subTest(label):
                folder = 'case-' + label.replace(' ', '-')
                got, what = self.load(self.package(folder, atlas_edit=lambda d, f=f: bytearray(f(bytearray(d)))))
                self.assertEqual(got, code, f'{label}: {what}')

    def test_folder_refusals(self):
        def escape(doc, dst):
            doc['icons'] = {'file': '../secret.json'}
        def huge(doc, dst):
            doc['description'] = 'x' * (70 * 1024)
        def bad_icons(doc, dst):
            data = json.loads((dst / 'icons' / 'icons.json').read_text(encoding='utf-8'))
            data['icons'][3]['name'] = data['icons'][2]['name']
            (dst / 'icons' / 'icons.json').write_text(json.dumps(data), encoding='utf-8')
        def icons_at(path):
            def edit(doc, dst):
                doc['icons'] = {'file': path}
            return edit
        cases = [('icons outside the folder', escape, BAD_VALUE), ('theme.json too large', huge, TOO_LARGE),
                 ('icon name twice', bad_icons, BAD_ICONS),
                 # nit: icons.file is a .json file and never a DOS device name, in any component
                 ('icons not json', icons_at('icons/icons.txt'), BAD_VALUE),
                 ('icons no suffix', icons_at('icons/icons'), BAD_VALUE),
                 ('icons only suffix', icons_at('icons/.json'), BAD_VALUE),
                 ('icons nul', icons_at('nul.json'), BAD_VALUE), ('icons NUL upper', icons_at('NUL.json'), BAD_VALUE),
                 ('icons con folder', icons_at('con/icons.json'), BAD_VALUE),
                 ('icons com1', icons_at('icons/com1.json'), BAD_VALUE),
                 ('icons lpt9', icons_at('icons/LPT9.x.json'), BAD_VALUE),
                 ('icons aux', icons_at('aux.json'), BAD_VALUE),
                 ('icons trailing dot folder', icons_at('icons./icons.json'), BAD_VALUE)]
        for label, edit, code in cases:
            with self.subTest(label):
                folder = 'case-' + label.replace(' ', '-').replace('.', '')
                got, what = self.load(self.package(folder, edit=edit))
                self.assertEqual(got, code, f'{label}: {what}')
        self.assertEqual(self.load(self.package('legacy-bold'))[0], BUILTIN_ID)
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertEqual(self.load(empty)[0], NO_FILE)

    def test_the_scan_and_the_list(self):
        self.package('aaa-valid')
        self.package('bbb-broken', atlas_edit=lambda d: d[:-50])
        (self.root / 'Not An Id').mkdir()
        self.d.ILCT_ThReset()
        self.d.ILCT_ThSetRoot(str(self.root))
        r = Reply()
        self.d.ILCT_ThList(ctypes.byref(r))
        ids = [r.entries[k].id.decode() for k in range(r.count)]
        self.assertEqual(ids, ['legacy-bold', 'legacy-humanist', 'legacy-crisp', 'aaa-valid'])
        self.assertEqual(r.refused, 2, 'the broken package and the folder that is not an id')
        self.assertEqual(r.entries[0].flags & (THEME_BUILTIN | THEME_DEFAULT | THEME_SELECTED),
                         THEME_BUILTIN | THEME_DEFAULT | THEME_SELECTED)
        self.assertEqual(r.entries[3].flags & THEME_BUILTIN, 0)
        self.assertEqual(r.entries[3].name, b'My Crisp ?t?')

    def test_selection_hand_over_and_fallbacks(self):
        self.package('aaa-valid')
        self.d.ILCT_ThReset()
        self.d.ILCT_ThSetRoot(str(self.root))
        gen = self.d.ILCT_ThAtlasGeneration()
        self.d.ILCT_ThConfigure(b'aaa-valid')
        self.d.ILCT_ThStep(1000)
        self.assertEqual(self.info().id, b'legacy-bold', 'nothing changes before a frame boundary')
        self.assertEqual(self.d.ILCT_ThTakePending(), 1)
        self.assertEqual(self.info().id, b'aaa-valid')
        self.assertNotEqual(self.d.ILCT_ThAtlasGeneration(), gen, 'gpu re-creates the atlas texture')
        self.assertEqual(self.d.ILCT_ThTakePending(), 0, 'one hand-over')
        self.d.ILCT_ThStep(1020)   # frees the retired default slot
        # A theme that is not there: the default comes back, the refusal named once.
        self.d.ILCT_ThConfigure(b'no-such-theme')
        self.d.ILCT_ThStep(1040)
        self.assertEqual(self.d.ILCT_ThTakePending(), 1)
        self.assertEqual(self.info().id, b'legacy-bold')
        self.d.ILCT_ThStep(1060)
        self.assertEqual(self.d.ILCT_ThTakePending(), 0, 'a refused theme is not read again every beat')
        # A built-in by id.
        self.d.ILCT_ThConfigure(b'legacy-humanist')
        self.d.ILCT_ThStep(1080)
        self.assertEqual(self.d.ILCT_ThTakePending(), 1)
        self.assertEqual(self.info().id, b'legacy-humanist')
        # A texture failure: the default for the rest of the launch.
        self.d.ILCT_ThStep(1100)
        self.d.ILCT_ThNoteAtlasFailed()
        self.d.ILCT_ThStep(1120)
        self.assertEqual(self.d.ILCT_ThTakePending(), 1)
        self.assertEqual(self.info().id, b'legacy-bold')
        st = Status()
        self.d.ILCT_ThStatus(ctypes.byref(st))
        self.assertEqual(st.active_id, b'legacy-bold')


class FontTest(ThemeCase):
    def code(self, text: bytes):
        at = u32(0)
        out = []
        while at.value < len(text):
            out.append(self.d.ILCT_FtNextCode(text, len(text), ctypes.byref(at)))
        return out

    def test_utf8(self):
        self.assertEqual(self.code('A\u00e9\u25c6'.encode()), [0x41, 0xE9, 0x25C6])
        self.assertEqual(self.code('\U0001F600'.encode()), [0x1F600])
        self.assertEqual(self.code(b'\xc3'), [ord('?')], 'a cut sequence')
        self.assertEqual(self.code(b'\xff\x41'), [ord('?'), 0x41], 'a bad lead byte is one ?')
        self.assertEqual(self.code(b'\xc0\x80'), [ord('?'), ord('?')], 'an overlong form')
        self.assertEqual(self.code(b'\xed\xa0\x80'), [ord('?')] * 3, 'a surrogate')

    def test_accents_kern_like_their_base(self):
        self.assertEqual(self.d.ILCT_FtBaseOf(0xC1), ord('A'))
        self.assertEqual(self.d.ILCT_FtBaseOf(0xE7), ord('c'))
        self.assertEqual(self.d.ILCT_FtBaseOf(0xD7), 0, 'the multiplication sign has no base')
        pairs = [(a, b) for a in range(65, 91) for b in range(65, 91) if self.d.ILCT_FtKern(FACE_BODY, a, b) != 0]
        self.assertTrue(pairs, 'the default body face kerns')
        a, b = next((a, b) for a, b in pairs if a == ord('A')) if any(a == 65 for a, _ in pairs) else pairs[0]
        accented = {ord('A'): 0xC1, ord('E'): 0xC9, ord('O'): 0xD3, ord('U'): 0xDA}.get(a)
        if accented:
            self.assertEqual(self.d.ILCT_FtKern(FACE_BODY, accented, b), self.d.ILCT_FtKern(FACE_BODY, a, b))

    def test_measure_and_fit_with_the_extra_advance(self):
        text = 'Wyrm Bone'.encode()
        base = self.d.ILCT_FtMeasureX(FACE_BODY, 20.0, text, 99, 0.0)
        self.assertAlmostEqual(self.d.ILCT_FtMeasureX(FACE_BODY, 20.0, text, 99, 0.7), base + 0.7 * len(text), places=3)
        cut, width = i32(), f32()
        keep = self.d.ILCT_FtFitX(FACE_BODY, 20.0, text, 99, base * 0.6, 0.7, ctypes.byref(cut), ctypes.byref(width))
        self.assertEqual(cut.value, 1)
        # The cut width is what glyph_run draws: the kept letters, the kerning from the last one to the first '.', then
        # the dots: exactly measure(prefix + "...") (nit: the kerning before the dots was left out).
        cut_text = text[:keep] + b'...'
        self.assertAlmostEqual(width.value, self.d.ILCT_FtMeasureX(FACE_BODY, 20.0, cut_text, len(cut_text), 0.7),
                               places=3)
        for s in (b'AVAVAVAVAVAVAVAV', b'Wyrm Fang Wyrm Fang', b'T.T.T.T.T.T.T.'):
            full = self.d.ILCT_FtMeasureX(FACE_BODY, 22.0, s, len(s), 0.0)
            for frac in (0.35, 0.55, 0.8):
                k = self.d.ILCT_FtFitX(FACE_BODY, 22.0, s, len(s), full * frac, 0.0, ctypes.byref(cut), ctypes.byref(width))
                drawn = self.d.ILCT_FtMeasureX(FACE_BODY, 22.0, s[:k] + b'...', k + 3, 0.0)
                self.assertAlmostEqual(width.value, drawn, places=3, msg=(s, frac))
                self.assertLessEqual(drawn, full * frac + 1e-3, 'the cut text with its dots fits')
        # A cut never splits a code point.
        s = 'Caf\u00e9 Caf\u00e9 Caf\u00e9'.encode()
        full = self.d.ILCT_FtMeasureX(FACE_BODY, 20.0, s, 99, 0.0)
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7):
            k = self.d.ILCT_FtFitX(FACE_BODY, 20.0, s, 99, full * frac, 0.0, ctypes.byref(cut), ctypes.byref(width))
            s[:k].decode('utf-8')

    def test_small_size_tracking_and_the_space_floor(self):
        self.d.ILCT_FtSetPixelScale(1.0)
        big = self.d.ILCT_FtMeasureX(FACE_BODY, 30.0, b'AAAA', 4, 0.0)
        self.d.ILCT_FtSetPixelScale(0.5)   # 30 units = 15 px: below the theme's 19 px, +0.02 em a glyph
        self.assertAlmostEqual(self.d.ILCT_FtMeasureX(FACE_BODY, 30.0, b'AAAA', 4, 0.0), big + 4 * 0.02 * 30.0, places=3)
        self.d.ILCT_FtSetPixelScale(1.0)
        self.assertGreaterEqual(self.d.ILCT_FtMeasureX(FACE_BODY, 20.0, b' ', 1, 0.0), 0.22 * 20.0 - 1e-3)

    def test_hud_marks_exist(self):
        self.assertEqual(self.d.ILCT_FtHasGlyph(FACE_BODY, 0x25C6), 1, 'the rare drop diamond')
        self.assertEqual(self.d.ILCT_FtHasGlyph(FACE_BODY, 0xB7), 1, 'the middle dot separator')
        self.assertEqual(self.d.ILCT_FtHasGlyph(FACE_BODY, 0x2212), 1, 'the minus sign')


class SettingTest(ThemeCase):
    def settings(self, theme: bytes):
        from test_coop_options import S as SCHEMA
        d = SCHEMA.decode('ilc_settings', SCHEMA.defaults())
        d['header']['generation'] = 1
        d['hud_theme'] = theme.ljust(32, b'\0')
        return SCHEMA, SCHEMA.encode('ilc_settings', d)

    def test_the_bridge_checks_the_id(self):
        for theme, ok in ((b'', True), (b'legacy-crisp', True), (b'my_theme-2', True), (b'Bad Theme', False),
                          (b'a\0b', False), (b'x' * 32, True)):
            with self.subTest(theme=theme):
                schema, rec = self.settings(theme)
                out = (ctypes.c_uint8 * 4096)()
                buf = (ctypes.c_uint8 * len(rec)).from_buffer_copy(rec)
                d = declare(fresh_test_dll())
                r = d.ILC_Bridge(2, ctypes.addressof(buf), len(rec), ctypes.addressof(out), 4096)
                if ok:
                    self.assertGreaterEqual(r, 0, theme)
                else:
                    self.assertEqual(r, -10, f'{theme}: BAD_FIELD')

    def test_get_themes(self):
        out = Reply()
        r = self.d.ILC_Bridge(OP_GET_THEMES, None, 0, ctypes.addressof(out), ctypes.sizeof(out))
        self.assertEqual(r, ctypes.sizeof(Reply))
        self.assertEqual((out.header.magic, out.header.record_type, out.header.size), (ABI_MAGIC, 13, 1120))
        self.assertGreaterEqual(out.count, 3)
        self.assertEqual([out.entries[k].id for k in range(3)], [b'legacy-bold', b'legacy-humanist', b'legacy-crisp'])
        small = (ctypes.c_uint8 * 64)()
        self.assertEqual(self.d.ILC_Bridge(OP_GET_THEMES, None, 0, ctypes.addressof(small), 64), -6, 'OUT_TOO_SMALL')


if __name__ == '__main__':
    unittest.main()
