"""Host tests for the co-op battle menus drawn in the native window style (native/coop/src/hud/menus_draw*). No game
run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the frame driver runs on.

* static: the drawing half never blocks, allocates, calls the C runtime or reads game memory; the frame driver's menu
  pass draws the menus; the boot builds the view after the HUD snapshot and before guest interactions, configures the
  seat colours with the other settings and stops it at exit; nothing here stamps the presenter;
* geometry: Player 1's window sits on the native command window's values (field layout group 14 list origin, group 11
  help strip, group 15 LEADER and flag, group 30 footer icons) at 1920x1080, 1366x768 and 1280x720; with
  ILC_MRP_PACK the same values are read from the game's own pack; the party rows (stock and reduced) a guest's window
  slides out of;
* layout: every page kind for 1..4 seats over party sizes 1..4, stock and reduced: no window overlaps another, Player
  1's window, help strip or footer, the party list, Target Info or the battle log; everything on screen at the three
  resolutions; the frame share is never exceeded (rows squeezed, then extras dropped, then windows);
* the slide: a guest's window and its link sit on its own party row; the slide-in starts at the row side and ends in
  place after the native open table's 150 ms;
* page rendering from the menu model: a real catalog (the menu tests' fixture tables) opened in a menu test seat and
  turned into the view: names, greys, MP costs, item counts, the Gambits plate, the help line; target pages with A..Z
  letters and the LB/RB footer; the selectors;
* seat edge colours for every colour in both colour modes; the drawing is a pure function of (view, snapshot, time);
* the menu pass on the published view (epoch, staleness, withdrawal) and inside the frame driver (menus last);
* names read from the game's own text with the live reader, on the real battle pack (ILC_BATTLE_PACK).
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import unittest
from pathlib import Path

import test_coop_hud as th
import test_coop_hud_driver as thd
import test_coop_menu as tm
import test_coop_targets as tt
from coop_native_support import native_test

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'
MRP_PACK = Path(os.environ.get('ILC_MRP_PACK', '__no_mrp_pack__'))
BATTLE_PACK = os.environ.get('ILC_BATTLE_PACK')

u8, u16, u32, u64, i16, i32 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int16,
                                ctypes.c_int32)
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


ViewRow = S('ViewRow', [('text', ctypes.c_char * 24), ('value', ctypes.c_char * 8), ('flags', u8), ('reserved', u8 * 7)])
SeatView = S('SeatView', [('kind', u8), ('selector', u8), ('row_count', u8), ('cursor', u8), ('first', u16),
                          ('total', u16), ('member', u16), ('page', u8), ('group', u8), ('groups', u8), ('depth', u8),
                          ('colour_index', u8), ('reserved', u8), ('colour', u32), ('reserved2', u32),
                          ('opened_ms', u64), ('title', ctypes.c_char * 32), ('name', ctypes.c_char * 24),
                          ('help', ctypes.c_char * 128), ('rows', ViewRow * 8)])
MenuView = S('MenuView', [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64), ('open_mask', u32),
                          ('reserved', u32), ('seats', SeatView * 4)])
Box = S('Box', [('x', i16), ('y', i16), ('w', i16), ('h', i16)])
WindowLayout = S('WindowLayout', [('seat', u8), ('kind', u8), ('visible', u8), ('start', u8), ('row_index', u8),
                                  ('pushed', u8), ('connector', u8), ('help_glass', u8), ('frame', Box), ('rows', Box),
                                  ('help', Box), ('pitch', i16), ('text_scale', i16), ('commands', u32),
                                  ('text_bytes', u32), ('reserved', u32)])
FrameLayout = S('FrameLayout', [('count', u32), ('commands', u32), ('text_bytes', u32), ('dropped', u32),
                                ('w', WindowLayout * 4)])
P1Geometry = S('P1Geometry', [('list_x', i16), ('list_y', i16), ('help', Box), ('header', Box), ('name', Box),
                              ('leader', Box), ('flag', Box), ('footer_l1', Box), ('footer_r1', Box)])
COUNTERS = ('built', 'withdrawn', 'name_misses', 'drawn_frames', 'drawn_windows', 'stale', 'torn', 'squeezed',
            'dropped')
Counters = S('Counters', [(n, u64) for n in COUNTERS])
Selector = S('Selector', [('kind', u8), ('count', u8), ('index', u8), ('reserved', u8), ('member', u16 * 4)])

COMMAND_CAP, TEXT_CAP = 120, 1400
FLOOR_COMMANDS_MAX, FLOOR_TEXT_MAX = 113, 738
LAYOUTS = [ctypes.sizeof(ViewRow), ctypes.sizeof(SeatView), ctypes.sizeof(MenuView), ctypes.sizeof(WindowLayout),
           ctypes.sizeof(FrameLayout), ctypes.sizeof(Box), ctypes.sizeof(P1Geometry), ctypes.sizeof(Counters),
           COMMAND_CAP, TEXT_CAP]

WK_NONE, WK_LIST, WK_TARGET, WK_SELECTOR = range(4)
RF_GREY, RF_PAGE, RF_TOGGLE, RF_ON, RF_KNOWN, RF_ISSUER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
VIEW_REDUCED = 0x01
SEL_NONE, SEL_LEADER, SEL_CHARACTER, SEL_KO = range(4)
MEMBER_NONE = 0xFFFF
ATLAS_BATTLE_4_C, ATLAS_CURSOL_4_P, ATLAS_BT_WIN_0_C, ATLAS_BUTTONICON = 0, 5, 6, 7
EPOCH, NOW = thd.EPOCH, thd.NOW
RESOLUTIONS = [(1920, 1080), (1366, 768), (1280, 720)]
SLIDE_MS, SLIDE_DISTANCE = 150, 40

# ---- the native layout, restated from the stock field layout (mrppack_ys.bin section 1) ------------------------------
G14, G14_E0, G14_E1 = (80, 816, 704, 220), (42, 132, 256, 68), (122, 44, 256, 28)
G15_E0, G15_E1 = (86, 92, 240, 28), (18, 104, 50, 38)
G11, G11_E0 = (426, 764, 512, 256), (108, 160, 360, 36)
G30, G31_E0, G32_E0, G31, G32 = (210, 978, 640, 80), (4, 2, 50, 34), (6, 2, 50, 34), (46, 30, 64, 40), (485, 30, 60, 40)
G40_STOCK, G40_STOCK_GAP = (202, 228, 1200, 58), 52
G40_REDUCED, G40_REDUCED_GAP = (122, 242, 900, 44), 42
PANEL_STOCK, PANEL_REDUCED = (570, 741), (950, 741)
LIST_ORIGIN = (G14[0] + G14_E0[0], G14[1] + G14_E0[1] + G14_E0[3] - 14)
HELP_RECT = (G11[0] + G11_E0[0], G11[1] + G11_E0[1], G11_E0[2], G11_E0[3])
assert LIST_ORIGIN == (122, 1002) and HELP_RECT == (534, 924, 360, 36)

# Seat colours (ilc_colour order) as the canvas's test draw has them, halved to the PS2 scale.
SEAT_RGB = [(70, 130, 240), (230, 60, 70), (245, 120, 190), (60, 210, 110), (240, 190, 50), (170, 100, 235),
            (205, 127, 50)]


def seat_rgba(index: int, alpha: int = 0x80) -> int:
    r, g, b = ((c + 1) // 2 for c in SEAT_RGB[index])
    return r | g << 8 | b << 16 | alpha << 24


SIGNATURES = {
    'ILCT_HmSizes': ([P(u32), u32], u32), 'ILCT_HmReset': ([], None), 'ILCT_HmP1Native': ([P(P1Geometry)], None),
    'ILCT_HmPartyRow': ([u32, u32, i32, P(Box)], None), 'ILCT_HmPartyList': ([u32, i32, P(Box)], None),
    'ILCT_HmZones': ([P(Box), P(Box)], None),
    'ILCT_HmLayout': ([P(MenuView), P(thd.Snapshot), u32, u32, P(FrameLayout)], None),
    'ILCT_HmDraw': ([P(MenuView), P(thd.Snapshot), u64, u32, u32, P(th.Frame)], u32),
    'ILCT_HmPass': ([P(thd.Snapshot), u64, u32, P(th.Frame)], None),
    'ILCT_HmDrawDropped': ([P(MenuView), P(thd.Snapshot), u64, u32, P(th.Frame)], u32),
    'ILCT_HmFloor': ([P(MenuView), P(thd.Snapshot), P(u32), P(u32)], None), 'ILCT_HmLastDropped': ([], u32),
    'ILCT_HmForceDropped': ([u32], None),
    'ILCT_HmPublish': ([P(MenuView)], None), 'ILCT_HmWithdraw': ([], None), 'ILCT_HmRead': ([P(MenuView)], i32),
    'ILCT_HmDecode': ([ctypes.c_char_p, u32, ctypes.c_char_p, u32], u32),
    'ILCT_HmBuildSeat': ([u32, u32, u32, P(Selector), P(tt.CandidateList), P(SeatView)], None),
    'ILCT_HmSeatColour': ([u32, u32, P(u8)], u32), 'ILCT_HmConfigure': ([ctypes.c_char_p], None),
    'ILCT_HmSetViewSource': ([P(MenuView)], None), 'ILCT_HmForceReduced': ([i32], None),
    'ILCT_HmSetImageBase': ([u64], None), 'ILCT_HmBuild': ([u64, P(MenuView)], i32),
    'ILCT_HmStart': ([u64, u64], None), 'ILCT_HmStep': ([u64], None), 'ILCT_HmStop': ([], None),
    'ILCT_HmStats': ([P(Counters)], None), 'ILCT_HmLiveName': ([u32, u32, u64, ctypes.c_char_p, u32], i32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


# ---- helpers ----------------------------------------------------------------------------------------------------------
def snapshot(rows=4, seats=(1, 2, 3, 4), leader=0, epoch=EPOCH) -> thd.Snapshot:
    """Rows in HUD order; seats[k] = the seat (1..4, 0 none) holding row k's character (party ids 0.. in row order)."""
    s = thd.snapshot(epoch=epoch)
    s.member_count, s.leader = rows, leader
    for k in range(rows):
        m = s.members[k]
        m.member, m.seat, m.flags = k, seats[k] if k < len(seats) else 0, thd.UNIT_PARTY | thd.UNIT_PRESENT
    return s


def seat_view(kind=WK_LIST, rows=5, cursor=0, member=0, title='Title', help_text='Help line.', total=None, first=0,
              values=False, toggle=False, opened=1, colour=None, group=0, depth=1, selector=SEL_NONE) -> SeatView:
    v = SeatView()
    v.kind, v.row_count, v.cursor, v.member, v.first = kind, rows, cursor, member, first
    v.total = rows if total is None else total
    v.group, v.depth, v.selector, v.opened_ms = group, depth, selector, opened
    v.title, v.name, v.help = title.encode(), b'Basch', help_text.encode()
    v.colour = seat_rgba(member % 7) if colour is None else colour
    for k in range(rows):
        r = v.rows[k]
        r.text = f'Row {k} name'.encode()
        if values:
            r.value = str(10 + k).encode()
    if toggle and rows:
        v.rows[min(3, rows - 1)].flags = RF_TOGGLE | RF_KNOWN | RF_ON
    return v


def menu_view(seats: dict, reduced=True, epoch=EPOCH, built=NOW) -> MenuView:
    v = MenuView()
    v.epoch, v.flags, v.generation, v.built_ms = epoch, VIEW_REDUCED if reduced else 0, 1, built
    for s, sv in seats.items():
        v.seats[s] = sv
        if sv.kind != WK_NONE:
            v.open_mask |= 1 << s
    return v


def boxes_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def tup(b: Box) -> tuple:
    return (b.x, b.y, b.w, b.h)


def to_screen(rect, res) -> tuple:
    """Authoring units -> pixels: the HUD's 1920x1080 space scaled uniformly and centred, as the game maps its windows."""
    w, h = res
    s = min(w / 1920, h / 1080)
    ox, oy = (w - 1920 * s) / 2, (h - 1080 * s) / 2
    x, y, rw, rh = rect
    x0, y0 = round(ox + x * s), round(oy + y * s)
    return (x0, y0, round(ox + (x + rw) * s) - x0, round(oy + (y + rh) * s) - y0)


def commands(frame: th.Frame) -> list:
    return [frame.cmds[k] for k in range(frame.count)]


def texts(frame: th.Frame) -> list[bytes]:
    return thd.texts(frame)


def decoded(raw: bytes) -> str:
    inv = {v: k for k, v in th.CHARS.items()}
    return ''.join(inv.get(c, '?') for c in raw)


def frame_bytes(frame: th.Frame) -> bytes:
    n = th.Frame.cmds.offset + frame.count * ctypes.sizeof(th.Cmd)
    head = ctypes.string_at(ctypes.addressof(frame) + th.Frame.count.offset, n - th.Frame.count.offset)
    pool = ctypes.string_at(ctypes.addressof(frame) + th.Frame.text.offset, frame.text_used)
    return head + pool


class Dll:
    """The world's test DLL (the patch layer on the image twin), with the menu exports declared."""
    hw = None
    d = None

    @classmethod
    def get(cls):
        if cls.d is None:
            cls.hw = th.world()
            cls.d = declare(thd.declare(cls.hw.d))
        return cls.hw, cls.d


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_the_drawing_half_never_blocks_allocates_or_reads_the_game(self):
        text = (HUD_SRC / 'menus_draw.cpp').read_text(encoding='utf-8')
        for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                          'copy_to_caller', 'memcpy(', 'memset(', 'session::', 'game_reader', 'frontend::',
                          'load_from_caller', 'snprintf', 'strlen('):
            self.assertNotIn(forbidden, text, forbidden)
        view = (HUD_SRC / 'menus_draw_view.cpp').read_text(encoding='utf-8')
        for forbidden in ('VirtualProtect', 'memcpy(', 'Sleep', 'WaitFor', 'malloc'):
            self.assertNotIn(forbidden, view, forbidden)

    def test_the_menu_pass_draws_the_menus_and_nothing_here_stamps(self):
        driver = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        body = re.search(r'void pass_menus\(canvas& cv, const pass_input& in\)\n\{(.*?)\n\}', driver, re.S).group(1)
        self.assertIn('menus_draw::pass(cv, in);', body)
        for name in ('menus_draw.cpp', 'menus_draw_view.cpp', 'menus_draw.h'):
            code = [ln for ln in (HUD_SRC / name).read_text(encoding='utf-8').splitlines()
                    if not ln.lstrip().startswith('//')]
            self.assertFalse([ln for ln in code if 'note_drawn' in ln], name)

    def test_the_boot_builds_the_view_after_the_snapshot_and_stops_it(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        order = [boot.index(s) for s in ('frontend::step(now);', 'hud::snapshot::step(now);',
                                         'hud::menus_draw::step(now);', 'interaction::step(now);')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(boot.count('hud::menus_draw::configure_from_settings('), 2, 'at start and on each change')
        self.assertIn('hud::menus_draw::start(g_host.image_base', boot)
        self.assertLess(boot.index('hud::snapshot::stop();'), boot.index('hud::menus_draw::stop();'))
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("'src/hud/menus_draw.cpp', 'src/hud/menus_draw_view.cpp'", build)

    def test_the_view_reads_names_only_through_the_fault_safe_reader(self):
        view = (HUD_SRC / 'menus_draw_view.cpp').read_text(encoding='utf-8')
        self.assertIn('game_reader r(image_base());', view)
        self.assertNotRegex(view, r'reinterpret_cast<const (uint8_t|char)\*>', 'no raw game pointer dereference')
        self.assertNotIn('FN_NAME_TEXT', view, 'the game name lookup is walked, never called')


# ---- geometry -----------------------------------------------------------------------------------------------------------
@native_test
class GeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def test_layouts(self):
        out = (u32 * 16)()
        n = self.d.ILCT_HmSizes(out, 16)
        self.assertEqual(list(out[:n]), LAYOUTS)

    def p1(self) -> P1Geometry:
        g = P1Geometry()
        self.d.ILCT_HmP1Native(ctypes.byref(g))
        return g

    def test_player_ones_window_is_the_native_command_window(self):
        g = self.p1()
        self.assertEqual((g.list_x, g.list_y), (122, 1002), 'G14.x + E0.x, G14.y + E0.y + E0.h - 14')
        self.assertEqual(tup(g.help), (534, 924, 360, 36), 'G11 + G11 E0')
        self.assertEqual(tup(g.header), G14)
        self.assertEqual(tup(g.name), (G14[0] + G14_E1[0], G14[1] + G14_E1[1], 256, 28))
        self.assertEqual(tup(g.leader), (G14[0] + G15_E0[0], G14[1] + G15_E0[1], 240, 28), 'G15 sits at G14 E2 (0,0)')
        self.assertEqual(tup(g.flag), (G14[0] + G15_E1[0], G14[1] + G15_E1[1], 50, 38))
        self.assertEqual(tup(g.footer_l1), (G30[0] + G31[0] + G31_E0[0], G30[1] + G31[1] + G31_E0[1], 50, 34))
        self.assertEqual(tup(g.footer_r1), (G30[0] + G32[0] + G32_E0[0], G30[1] + G32[1] + G32_E0[1], 50, 34))

    def test_native_positions_at_three_resolutions(self):
        g = self.p1()
        for res in RESOLUTIONS:
            with self.subTest(res=res):
                # The game maps its own windows from the same authoring values; ours land on the same pixels.
                self.assertEqual(to_screen((g.list_x, g.list_y, 0, 0), res)[:2], to_screen((122, 1002, 0, 0), res)[:2])
                self.assertEqual(to_screen(tup(g.help), res), to_screen(HELP_RECT, res))
                self.assertEqual(to_screen(tup(g.name), res), to_screen((202, 860, 256, 28), res))
        self.assertEqual(to_screen(HELP_RECT, (1366, 768)), (380, 657, 256, 26))
        self.assertEqual(to_screen(HELP_RECT, (1280, 720)), (356, 616, 240, 24))

    @unittest.skipUnless(MRP_PACK.is_file(), 'set ILC_MRP_PACK to an extracted, unmodified mrppack_ys.bin')
    def test_the_values_are_the_stock_packs(self):
        pack = MRP_PACK.read_bytes()
        start, end = struct.unpack_from('<II', pack, 4)
        sec = pack[start:end]
        count, glist = struct.unpack_from('<II', sec, 0x1C)
        self.assertEqual(count, 52)

        def group(k):
            rec = glist + k * 0x14
            n = sec[rec]
            rect = struct.unpack_from('<hhhh', sec, rec + 4)
            gap = struct.unpack_from('<i', sec, rec + 12)[0]
            e = rec + struct.unpack_from('<I', sec, rec + 0x10)[0]
            entries = []
            for _ in range(n):
                entries.append(struct.unpack_from('<hhhh', sec, e + 4))
                e += sec[e]
            return rect, gap, entries

        g14, _, e14 = group(14)
        self.assertEqual((g14, e14[0], e14[1]), (G14, G14_E0, G14_E1))
        _, _, e15 = group(15)
        self.assertEqual((e15[0], e15[1]), (G15_E0, G15_E1))
        g11, _, e11 = group(11)
        self.assertEqual((g11, e11[0]), (G11, G11_E0))
        g30, _, e30 = group(30)
        self.assertEqual((g30, e30[0][:2], e30[1][:2]), (G30, G31[:2], G32[:2]))
        self.assertEqual((group(31)[2][0], group(32)[2][0]), (G31_E0, G32_E0))
        g40, gap40, _ = group(40)
        self.assertEqual((g40, gap40), (G40_STOCK, G40_STOCK_GAP))
        self.assertEqual(group(21)[0][:2], PANEL_STOCK)

    def test_party_rows_stock_and_reduced(self):
        # The party rows' own anchors (party_rows.h): layout y = G40.y - gap * (rows - 1 - index), mapped into the
        # canvas with one scale (15/13) so that the stock bottom row's top is at 970; the box starts at the row's
        # party-target hand (stock 1184, reduced 1381) and runs to the right edge.
        def scale(v):
            p = v * 15
            return (p + 6) // 13 if p >= 0 else -((-p + 6) // 13)

        for reduced, (g, gap, left) in ((0, (G40_STOCK, G40_STOCK_GAP, 1184)), (1, (G40_REDUCED, G40_REDUCED_GAP, 1381))):
            for rows in range(1, 5):
                for k in range(rows):
                    with self.subTest(reduced=reduced, rows=rows, index=k):
                        b = Box()
                        self.d.ILCT_HmPartyRow(k, rows, reduced, ctypes.byref(b))
                        top = 970 + scale(g[1] - gap * (rows - 1 - k) - 228)
                        self.assertEqual(tup(b), (left, top, 1920 - left, scale(gap)), 'bottom-anchored rows')
        tops = []
        for k in range(4):
            b = Box()
            self.d.ILCT_HmPartyRow(k, 4, 1, ctypes.byref(b))
            tops.append(b.y)
        self.assertEqual(tops, sorted(tops), 'HUD order, top to bottom')


# ---- layout -------------------------------------------------------------------------------------------------------------
@native_test
class LayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def setUp(self):
        self.d.ILCT_HmReset()

    def layout(self, v: MenuView, snap: thd.Snapshot, commands=COMMAND_CAP, text=TEXT_CAP) -> FrameLayout:
        fl = FrameLayout()
        self.d.ILCT_HmLayout(ctypes.byref(v), ctypes.byref(snap), commands, text, ctypes.byref(fl))
        return fl

    def zones(self, rows: int, reduced: bool) -> dict:
        ti, log, party = Box(), Box(), Box()
        self.d.ILCT_HmZones(ctypes.byref(ti), ctypes.byref(log))
        self.d.ILCT_HmPartyList(rows, 1 if reduced else 0, ctypes.byref(party))
        return {'Target Info': tup(ti), 'battle log': tup(log), 'party list': tup(party), 'P1 help strip': HELP_RECT,
                'P1 footer': G30}

    def kinds(self):
        return {'list': dict(kind=WK_LIST, rows=8, values=True, toggle=True),
                'target': dict(kind=WK_TARGET, rows=6),
                'selector': dict(kind=WK_SELECTOR, rows=4, selector=SEL_CHARACTER),
                'short list': dict(kind=WK_LIST, rows=2)}

    def test_no_window_overlaps_anything_for_every_page_kind_seat_count_and_party(self):
        cases = 0
        for reduced in (True, False):
            for rows in range(1, 5):
                for nseats in range(1, 5):
                    for name, kw in self.kinds().items():
                        seats_present = list(range(nseats))
                        snap = snapshot(rows=rows, seats=[s + 1 for s in range(rows)])
                        v = menu_view({s: seat_view(member=s, **kw) for s in seats_present}, reduced=reduced)
                        fl = self.layout(v, snap)
                        cases += 1
                        with self.subTest(reduced=reduced, rows=rows, seats=nseats, kind=name):
                            self.assertEqual((fl.count, fl.dropped), (nseats, 0))
                            self.assertLessEqual(fl.commands, COMMAND_CAP)
                            self.assertLessEqual(fl.text_bytes, TEXT_CAP)
                            frames = [(fl.w[k].seat, tup(fl.w[k].frame)) for k in range(fl.count)]
                            zones = self.zones(rows, reduced)
                            for res in RESOLUTIONS:
                                px = [(s, to_screen(f, res)) for s, f in frames]
                                for i, (sa, a) in enumerate(px):
                                    x, y, w, h = a
                                    self.assertTrue(0 <= x and 0 <= y and x + w <= res[0] and y + h <= res[1],
                                                    f'seat {sa + 1} on screen at {res}: {a}')
                                    for sb, b in px[i + 1:]:
                                        self.assertFalse(boxes_overlap(a, b), f'seats {sa + 1} and {sb + 1} at {res}')
                                    for zname, z in zones.items():
                                        if sa == 0 and zname.startswith('P1'):
                                            continue  # Player 1's own help strip and footer
                                        self.assertFalse(boxes_overlap(a, to_screen(z, res)),
                                                         f'seat {sa + 1} vs {zname} at {res}')
        self.assertEqual(cases, 2 * 4 * 4 * 4)

    def test_player_ones_window_grows_up_from_the_list_origin(self):
        for n in range(1, 9):
            with self.subTest(rows=n):
                v = menu_view({0: seat_view(rows=n, cursor=n - 1)})
                fl = self.layout(v, snapshot())
                w = fl.w[0]
                self.assertEqual((w.visible, w.pitch), (n if n <= 8 else 8, 44))
                self.assertEqual((w.rows.x, w.rows.y + w.rows.h), LIST_ORIGIN, 'rows end on the list origin')
                self.assertEqual(w.frame.y + w.frame.h, G14[1] + G14[3], "the glass ends on group 14's bottom")
                self.assertEqual(tup(w.help), HELP_RECT)
                if n == 1:
                    self.assertEqual(w.frame.y, G14[1], "one row: the glass is group 14's rect")

    def test_a_guests_window_slides_out_of_its_own_row(self):
        # One guest at each row: the window's right edge 12 units left of the row's leftmost piece (kept clear of the
        # expanded log's column), its bottom on its row's bottom, its link on its row's middle.
        for reduced in (1, 0):
            for rows in range(1, 5):
                for index in range(rows):
                    seats = [0] * rows
                    seats[index] = 2
                    snap = snapshot(rows=rows, seats=seats)
                    v = menu_view({1: seat_view(member=index, rows=4)}, reduced=bool(reduced))
                    fl = self.layout(v, snap)
                    w = fl.w[0]
                    row = Box()
                    self.d.ILCT_HmPartyRow(index, rows, reduced, ctypes.byref(row))
                    ti, log = Box(), Box()
                    self.d.ILCT_HmZones(ctypes.byref(ti), ctypes.byref(log))
                    with self.subTest(reduced=reduced, rows=rows, row=index):
                        self.assertEqual((w.seat, w.row_index), (1, index), 'the window knows the row it came out of')
                        self.assertEqual(w.frame.x + w.frame.w, min(row.x - 12, log.x - 8))
                        bottom = w.frame.y + w.frame.h
                        if reduced:
                            self.assertEqual((bottom, w.pushed), (row.y + row.h, 0), 'on its own row')
                            self.assertEqual(w.connector, 1, 'one line across to the row')
                        elif row.y + row.h > HELP_RECT[1] - 12:
                            # The stock list starts further left: a low row's window rises above P1's help strip
                            # and the link goes down to its row.
                            self.assertEqual((bottom, w.pushed, w.connector), (HELP_RECT[1] - 12 - 8, 1, 2))
                        else:
                            self.assertEqual((bottom, w.pushed), (row.y + row.h, 0))

    def test_guests_stack_in_row_order_and_never_overlap(self):
        snap = snapshot(rows=4, seats=[4, 3, 2, 1])
        v = menu_view({s: seat_view(member=3 - s, rows=6, kind=WK_TARGET) for s in (1, 2, 3)})
        fl = self.layout(v, snap)
        by_row = sorted((fl.w[k].row_index, fl.w[k].frame.y) for k in range(fl.count))
        ys = [y for _, y in by_row]
        self.assertEqual(ys, sorted(ys), 'a higher row has a higher window')
        self.assertTrue(all(fl.w[k].frame.y >= 40 for k in range(fl.count)))

    def floor(self, v: MenuView, snap: thd.Snapshot) -> tuple[int, int]:
        c, t = u32(), u32()
        self.d.ILCT_HmFloor(ctypes.byref(v), ctypes.byref(snap), ctypes.byref(c), ctypes.byref(t))
        return c.value, t.value

    def test_the_floor_is_the_least_room_that_keeps_every_window(self):
        # The frame driver keeps exactly this back for the menu pass: with it every open window is drawn; one command
        # (or one text byte) less and a window has to go.
        cases = 0
        for reduced in (True, False):
            for rows in (1, 4):
                snap = snapshot(rows=rows, seats=[1, 2, 3, 4][:rows])
                for nseats in range(1, 5):
                    for kname, kind in self.kinds().items():
                        cases += 1
                        v = menu_view({s: seat_view(member=s, **kind) for s in range(nseats)}, reduced=reduced)
                        c, t = self.floor(v, snap)
                        with self.subTest(reduced=reduced, rows=rows, seats=nseats, kind=kname, floor=(c, t)):
                            fl = self.layout(v, snap, commands=c, text=t)
                            kept = [fl.w[k].seat for k in range(fl.count) if fl.w[k].kind != WK_NONE]
                            self.assertEqual((len(kept), fl.dropped), (nseats, 0))
                            self.assertLessEqual((fl.commands, fl.text_bytes), (c, t))
                            self.assertGreaterEqual(self.layout(v, snap, commands=c - 1).dropped, 1)
                            self.assertGreaterEqual(self.layout(v, snap, text=t - 1).dropped, 1)
                            self.assertLessEqual(c, FLOOR_COMMANDS_MAX)
                            self.assertLessEqual(t, FLOOR_TEXT_MAX)
        self.assertEqual(cases, 2 * 2 * 4 * 4)
        self.assertEqual(self.floor(menu_view({}), snapshot()), (0, 0), 'nothing open: nothing kept back')

    def test_the_worst_floor_meets_the_static_bound(self):
        # Every seat open on a target page, Player 1 leading, every string at its field's full length, every row with
        # a value and the ON/OFF plate, both scroll arrows: exactly FLOOR_COMMANDS_MAX commands.
        def worst(seat):
            sv = seat_view(kind=WK_TARGET, rows=8, cursor=1, member=seat, first=1, total=40, values=True, group=0)
            sv.title, sv.name, sv.help = b'T' * 32, b'N' * 24, b'H' * 64
            for k in range(8):
                r = sv.rows[k]
                r.text, r.value, r.flags = b'R' * 24, b'9' * 8, RF_TOGGLE | RF_KNOWN | RF_ON
            return sv
        v = menu_view({s: worst(s) for s in range(4)})
        c, t = self.floor(v, snapshot(leader=0))
        self.assertEqual(c, FLOOR_COMMANDS_MAX)
        self.assertLessEqual(t, FLOOR_TEXT_MAX)
        self.assertLessEqual(FLOOR_COMMANDS_MAX, COMMAND_CAP)
        header = (HUD_SRC / 'menus_draw.h').read_text(encoding='utf-8')
        self.assertIn(f'constexpr uint32_t FLOOR_COMMANDS_MAX = {FLOOR_COMMANDS_MAX};', header)
        self.assertIn(f'constexpr uint32_t FLOOR_TEXT_MAX = {FLOOR_TEXT_MAX};', header)
        self.assertIn('static_assert(FLOOR_COMMANDS_MAX <= COMMAND_CAP && COMMAND_CAP <= MAX_COMMANDS', header)
        self.assertIn('FLOOR_*_MAX bound four floor windows',
                      (HUD_SRC / 'menus_draw.cpp').read_text(encoding='utf-8'))

    def test_the_frame_share_squeezes_then_drops_never_exceeds(self):
        snap = snapshot(rows=4, seats=[1, 2, 3, 4])
        v = menu_view({s: seat_view(member=s, rows=8, values=True, kind=WK_LIST, toggle=True) for s in range(4)})
        full = self.layout(v, snap)
        self.assertLessEqual(full.commands, COMMAND_CAP)
        squeezed = [full.w[k].visible for k in range(4)]
        self.assertTrue(any(n < 8 for n in squeezed), squeezed)
        for room in (80, 60, 40, 20, 9, 0):
            with self.subTest(room=room):
                fl = self.layout(v, snap, commands=room)
                self.assertLessEqual(fl.commands, room)
                kept = [fl.w[k].seat for k in range(fl.count) if fl.w[k].kind != WK_NONE]
                self.assertEqual(len(kept) + fl.dropped, 4)
                self.assertEqual(kept, sorted(kept)[:len(kept)], 'the last seats go first')
                if kept:
                    self.assertIn(0, kept, "Player 1's window is the last to go")
        fl = self.layout(v, snap, text=300)
        self.assertLessEqual(fl.text_bytes, 300)


# ---- drawing --------------------------------------------------------------------------------------------------------------
@native_test
class DrawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def setUp(self):
        self.d.ILCT_HmReset()

    def draw(self, v: MenuView, snap=None, now=NOW + 1000, prefill=0) -> th.Frame:
        f = th.Frame()
        snap = snapshot() if snap is None else snap
        self.d.ILCT_HmDraw(ctypes.byref(v), ctypes.byref(snap), now, 0, prefill, ctypes.byref(f))
        return f

    def test_player_one_root_page(self):
        sv = seat_view(rows=5, cursor=1, member=0, toggle=True, help_text='Cast magicks and use technicks.')
        f = self.draw(menu_view({0: sv}), snapshot(leader=0))
        cmds = commands(f)
        glass = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BT_WIN_0_C and c.sheet == 0]
        self.assertEqual([c.cell for c in glass], list(range(9)), 'the battle glass 9-slice')
        self.assertEqual((glass[0].x, glass[0].w, glass[0].h), (60, 83, 43))
        t = [decoded(x) for x in texts(f)]
        self.assertEqual(t[:2], ['Basch', 'LEADER'], 'name and LEADER (Player 1 leads)')
        self.assertEqual(t[2:7], [f'Row {k} name' for k in range(5)])
        self.assertEqual(t[7], 'Cast magicks and use technicks.')
        name = next(c for c in cmds if c.kind == th.CMD_TEXT)
        self.assertEqual((name.x, name.y), (202, 1002 - 5 * 44 - 98))
        hand = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_CURSOL_4_P and c.sheet == 0]
        self.assertEqual(len(hand), 1)
        self.assertEqual(hand[0].x, 76, 'the hand in G14 E3')
        bars = [c for c in cmds if c.kind == th.CMD_BAR]
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].colour, (sv.colour & 0xFFFFFF) | 0x40000000, 'selection bar in the seat colour')
        self.assertEqual(bars[0].y, 1002 - 5 * 44 + 44 + 3, 'on the cursor row')
        plate = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BATTLE_4_C and c.page == 14 and c.sheet == 2]
        self.assertEqual([c.cell for c in plate], [0], 'Gambits ON')
        flag = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BATTLE_4_C and c.page == 14 and c.sheet == 0]
        self.assertEqual([(c.x, c.w, c.h) for c in flag], [(98, 50, 38)], 'the leader flag of group 15')
        help_fill = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BT_WIN_0_C and c.sheet == 1]
        self.assertEqual([(c.x, c.y, c.w, c.h) for c in help_fill], [HELP_RECT], 'the help strip on group 11')
        self.assertNotIn('LEADER', [decoded(x) for x in texts(self.draw(menu_view({0: sv}), snapshot(leader=3)))])

    def test_greys_values_and_scroll_arrows(self):
        sv = seat_view(rows=8, cursor=6, values=True, total=12, first=2)
        sv.rows[2].flags = RF_GREY
        f = self.draw(menu_view({0: sv}))
        cmds = commands(f)
        texts_ = [(decoded(ctypes.string_at(ctypes.addressof(f) + th.Frame.text.offset + c.text)), c)
                  for c in cmds if c.kind == th.CMD_TEXT]
        row2 = [c for t, c in texts_ if t == 'Row 2 name']
        self.assertEqual(row2[0].colour, 0x804C4848, 'a greyed row')
        self.assertEqual([c.colour for t, c in texts_ if t == '12'], [0x804C4848], 'its value greyed too')
        self.assertEqual([c.colour for t, c in texts_ if t == '13'], [0x80807670])
        value = next(c for t, c in texts_ if t == '13')
        self.assertEqual(value.x, 122 + 256 - 2 * 15, 'right-aligned on the row quad (G14 E0) edge')
        arrows = sorted(c.cell for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_CURSOL_4_P and c.sheet == 6)
        self.assertEqual(arrows, [0, 1], 'more above (first > 0) and more below')
        top = seat_view(rows=8, cursor=0, total=8)
        arrows = [c for c in commands(self.draw(menu_view({0: top})))
                  if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_CURSOL_4_P and c.sheet == 6]
        self.assertEqual(arrows, [], 'everything fits: no arrow')

    def test_a_guest_window_has_its_seat_edge_and_header(self):
        for index in range(7):
            colour = seat_rgba(index)
            sv = seat_view(member=1, rows=3, colour=colour, title='P2 - Green Magicks')
            f = self.draw(menu_view({1: sv}), snapshot(rows=4, seats=[1, 2, 0, 0]))
            cmds = commands(f)
            with self.subTest(colour=index):
                edge = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BT_WIN_0_C and c.sheet == 1]
                self.assertGreaterEqual(len(edge), 1)
                self.assertEqual((edge[0].w, edge[0].colour), (4, colour), 'a thin seat-colour edge')
                header = next(c for c in cmds if c.kind == th.CMD_TEXT)
                self.assertEqual(header.colour, colour)
                self.assertEqual(decoded(texts(f)[0]), 'P2 - Green Magicks')
                glass = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BT_WIN_0_C and c.sheet == 0]
                self.assertEqual(len(glass), 9, 'the same native glass as Player 1')

    def test_the_slide_starts_at_the_row_and_ends_in_place(self):
        snap = snapshot(rows=4, seats=[0, 2, 0, 0])
        opened = NOW
        sv = seat_view(member=1, rows=3, opened=opened)
        v = menu_view({1: sv})
        fl = FrameLayout()
        self.d.ILCT_HmLayout(ctypes.byref(v), ctypes.byref(snap), COMMAND_CAP, TEXT_CAP, ctypes.byref(fl))
        x_final = fl.w[0].frame.x

        def first_glass(now):
            f = self.draw(v, snap, now=now)
            return next(c for c in commands(f) if c.kind == th.CMD_SPRITE and c.sheet == 0)

        start, mid, end = first_glass(opened), first_glass(opened + SLIDE_MS // 2), first_glass(opened + SLIDE_MS)
        self.assertEqual(start.x, x_final + SLIDE_DISTANCE, 'starts at the row side')
        self.assertEqual(start.colour >> 24, 0, 'and transparent')
        self.assertTrue(x_final < mid.x < x_final + SLIDE_DISTANCE)
        self.assertEqual((end.x, end.colour >> 24), (x_final, 0x80), 'in place after the open table (150 ms)')
        # The link to the party row sits on that row's middle, whichever row it is.
        for index in range(4):
            seats = [0, 0, 0, 0]
            seats[index] = 2
            snap = snapshot(rows=4, seats=seats)
            f = self.draw(v, snap)
            row = Box()
            self.d.ILCT_HmPartyRow(index, 4, 1, ctypes.byref(row))
            links = [c for c in commands(f) if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BT_WIN_0_C and c.sheet == 1
                     and c.h == 3]
            with self.subTest(row=index):
                self.assertEqual([c.y for c in links], [row.y + row.h // 2 - 1])
                self.assertEqual(links[0].x + links[0].w, row.x - 6)

    def test_a_target_page_has_the_lb_rb_footer(self):
        sv = seat_view(kind=WK_TARGET, rows=3, cursor=1, group=0, title='Attack')
        f = self.draw(menu_view({0: sv}))
        cmds = commands(f)
        icons = [c for c in cmds if c.kind == th.CMD_SPRITE and c.atlas == ATLAS_BUTTONICON]
        self.assertEqual([(c.page, c.sheet, c.cell, c.x, c.y) for c in icons],
                         [(0, 4, 0, 260, 1010), (0, 4, 1, 701, 1010)], 'L1/R1 of the native footer (groups 31/32)')
        self.assertIn('Foes', [decoded(t) for t in texts(f)])
        self.assertIn('Attack', [decoded(t) for t in texts(f)], 'the pending action as the page title')

    def test_drawing_is_a_pure_function_of_view_snapshot_and_time(self):
        v = menu_view({0: seat_view(rows=6, cursor=2, values=True, toggle=True),
                       1: seat_view(member=1, rows=4, kind=WK_TARGET),
                       2: seat_view(member=2, rows=3, kind=WK_SELECTOR)})
        snap = snapshot(rows=4, seats=[1, 2, 3, 0])
        a, b = frame_bytes(self.draw(v, snap)), frame_bytes(self.draw(v, snap))
        self.assertEqual(a, b)
        other = snapshot(rows=4, seats=[1, 2, 3, 0])
        for k in range(4):
            other.members[k].hp, other.members[k].status_timed = 1234 + k, 0xFFFF
        other.targets[0].handle = 77
        self.assertEqual(frame_bytes(self.draw(v, other)), a, 'only rows, seats and the leader matter')
        self.assertNotEqual(frame_bytes(self.draw(v, snapshot(rows=4, seats=[1, 3, 2, 0]))), a,
                            'a guest that moves rows moves its window')

    def test_the_menus_fit_what_the_earlier_passes_left(self):
        v = menu_view({s: seat_view(member=s, rows=8, values=True) for s in range(4)})
        snap = snapshot()
        for prefill in (0, 100, 150, 185, 192):
            with self.subTest(prefill=prefill):
                f = self.draw(v, snap, prefill=prefill)
                self.assertLessEqual(f.count, th.MAX_COMMANDS)
                self.assertLessEqual(f.count - prefill, COMMAND_CAP)
        c = Counters()
        self.d.ILCT_HmStats(ctypes.byref(c))
        self.assertGreater(c.dropped, 0, 'windows that did not fit are counted')

    def test_a_window_left_out_is_reported_by_seat(self):
        v = menu_view({s: seat_view(member=s, rows=8, values=True) for s in range(4)})
        snap = snapshot()
        c, t = u32(), u32()
        self.d.ILCT_HmFloor(ctypes.byref(v), ctypes.byref(snap), ctypes.byref(c), ctypes.byref(t))
        f = th.Frame()

        def dropped(prefill):
            return self.d.ILCT_HmDrawDropped(ctypes.byref(v), ctypes.byref(snap), NOW + 1000, prefill, ctypes.byref(f))
        self.assertEqual(dropped(th.MAX_COMMANDS - c.value), 0, 'the floor left: every window drawn')
        self.assertEqual(dropped(th.MAX_COMMANDS - c.value + 1), 0b1000, 'one short: the last seat goes')
        self.assertEqual(dropped(th.MAX_COMMANDS), 0b1111, 'no room: every seat')
        self.assertEqual(dropped(0), 0)

    def test_nothing_open_draws_nothing(self):
        f = self.draw(menu_view({}))
        self.assertEqual(f.count, 0)


# ---- page rendering from the menu model ------------------------------------------------------------------------------
@native_test
class PageRenderingTest(unittest.TestCase):
    """A real catalog (the menu tests' fixture tables) in a menu test seat, turned into the view with name doubles."""

    SEAT = 2

    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()
        tm.declare(cls.d)
        cls.fixture = tm.Fixture()
        assert tm.load_sections(cls.d, cls.fixture.sections()) == tm.TABLES_OK

    def setUp(self):
        self.d.ILCT_HmReset()
        self.d.ILCT_MenuSeatReset(self.SEAT)

    def open(self, sh, st):
        self.assertEqual(self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(sh), ctypes.byref(st), None), 1)
        e = tm.Event()
        self.d.ILCT_MenuSeatOp(self.SEAT, tm.OP_OPEN, 0, 0, ctypes.byref(e))
        self.assertEqual(e.kind, tm.EV_OPENED)

    def op(self, op, a=0, b=0):
        e = tm.Event()
        self.d.ILCT_MenuSeatOp(self.SEAT, op, a, b, ctypes.byref(e))
        return e

    def view(self, seat=1, member=0, sel=None, cands=None) -> SeatView:
        out = SeatView()
        self.d.ILCT_HmBuildSeat(self.SEAT, seat, member, ctypes.byref(sel) if sel else None,
                                ctypes.byref(cands) if cands else None, ctypes.byref(out))
        return out

    def goto(self, label: str):
        for _ in range(16):
            v = self.view()
            if v.cursor != 0xFF and v.rows[v.cursor].text.decode() == label:
                return
            self.op(tm.OP_NAV, 1, 1)
        self.fail(f'{label} not reached')

    def rows(self, v: SeatView) -> list[tuple]:
        return [(v.rows[k].text.decode(), v.rows[k].value.decode(), v.rows[k].flags) for k in range(v.row_count)]

    def test_the_root_page(self):
        self.open(tm.shared(magicks=(0, 1), items={0: 3}), tm.seat(summary=(6,)))
        v = self.view()
        self.assertEqual((v.kind, v.page, v.depth, v.cursor, v.total), (WK_LIST, tm.PG_ROOT, 1, 0, v.row_count))
        names = [r[0] for r in self.rows(v)]
        self.assertEqual(names[0], f'Act{tm.ATTACK}', "Attack is the game's own name")
        self.assertIn('Magicks & Technicks', names)
        self.assertIn('Gambits', names)
        self.assertIn('Items', names)
        gambits = self.rows(v)[names.index('Gambits')]
        self.assertTrue(gambits[2] & RF_TOGGLE)
        self.assertEqual(v.title.decode(), 'P2 - Vaan', "a guest's root page is headed with its seat and character")
        self.assertEqual(v.name.decode(), 'Vaan')
        self.assertTrue(v.help.decode(), 'the help line of the highlighted row')

    def test_a_magick_page_with_mp_costs_and_greys(self):
        self.open(tm.shared(magicks=(0, 1, 2)), tm.seat(summary=(6, 7), mp=10))
        self.goto('Magicks & Technicks')
        self.op(tm.OP_CONFIRM)          # Magicks & Technicks
        self.op(tm.OP_CONFIRM)          # White Magicks
        v = self.view()
        self.assertEqual((v.kind, v.page, v.depth), (WK_LIST, tm.PG_WHITE, 3))
        self.assertEqual(v.title.decode(), 'P2 - White Magicks')
        rows = {r[0]: r for r in self.rows(v)}
        # Fixture costs: action 0x01 costs 6, 0x00 costs 8, 0x05 costs 20 (MP 10: greyed).
        self.assertEqual(rows['Act1'][1:], ('6', 0))
        self.assertEqual(rows['Act0'][1:], ('8', 0))
        self.assertEqual(rows['Act5'][1], '20')
        self.assertTrue(rows['Act5'][2] & RF_GREY, 'not enough MP')
        self.assertTrue(v.help.decode().startswith('Target'))

    def test_items_show_counts(self):
        self.open(tm.shared(items={0: 3, 3: 12}), tm.seat())
        self.goto('Items')
        self.op(tm.OP_CONFIRM)
        v = self.view()
        self.assertEqual(v.page, tm.PG_ITEMS)
        self.assertEqual([(r[0], r[1]) for r in self.rows(v)], [(f'Act{0x52}', '3'), (f'Act{0x55}', '12')])

    def test_the_gambits_plate_follows_the_game_state(self):
        self.open(tm.shared(), tm.seat(gambits=False))
        v = self.view()
        g = next(r for r in self.rows(v) if r[0] == 'Gambits')
        self.assertEqual(g[2] & (RF_TOGGLE | RF_KNOWN | RF_ON), RF_TOGGLE | RF_KNOWN, 'read: off')
        self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(tm.shared()), ctypes.byref(tm.seat(gambits=True)), None)
        g = next(r for r in self.rows(self.view()) if r[0] == 'Gambits')
        self.assertEqual(g[2] & (RF_TOGGLE | RF_KNOWN | RF_ON), RF_TOGGLE | RF_KNOWN | RF_ON)

    def test_a_target_page_lists_foes_with_letters(self):
        self.open(tm.shared(), tm.seat())
        e = self.op(tm.OP_CONFIRM)      # Attack -> target mode
        self.assertEqual(e.kind, tm.EV_TARGET_OPENED)
        self.op(tm.OP_TARGETS, 3)
        name = (ctypes.c_char * 16)(*th.encoded('Hyena'))
        l = tt.CandidateList()
        l.first[0], l.count[0], l.selectable[0] = 0, 3, 3
        for k, (letter, number) in enumerate(((ord('A'), 0), (ord('B'), 0), (0, 27))):
            c = l.c[k]
            c.handle, c.member, c.group, c.flags = 0x200000 + k, MEMBER_NONE, 0, 0x01
            c.letter, c.number, c.name = letter, number, ctypes.addressof(name)
        l.used = 3
        v = self.view(cands=l)
        self.assertEqual((v.kind, v.group, v.total), (WK_TARGET, 0, 3))
        self.assertEqual([r[0] for r in self.rows(v)], ['Hyena A', 'Hyena B', 'Hyena 27'])
        self.assertEqual(v.title.decode(), f'P2 - Act{tm.ATTACK}')
        self.assertEqual(v.cursor, 0)
        self.assertEqual(v.help.decode(), 'Choose a target.')

    def test_the_selectors(self):
        sel = Selector()
        sel.kind, sel.count, sel.index = SEL_LEADER, 3, 1
        sel.member[0], sel.member[1], sel.member[2] = 0, 3, 5
        v = self.view(seat=0, member=0, sel=sel)
        self.assertEqual((v.kind, v.selector, v.cursor, v.title.decode()), (WK_SELECTOR, SEL_LEADER, 1, 'Change Leader'))
        self.assertEqual([r[0] for r in self.rows(v)], ['Vaan', 'Balthier', 'Penelo'])
        self.assertEqual(self.rows(v)[0][2], RF_ISSUER)
        sel.kind = SEL_KO
        self.assertEqual(self.view(seat=0, sel=sel).title.decode(), 'Choose a Leader')
        sel.kind = SEL_CHARACTER
        self.assertEqual(self.view(seat=3, member=4, sel=sel).title.decode(), 'P4 - Change Character')

    def test_a_long_page_carries_a_window_around_the_cursor(self):
        self.open(tm.shared(items={k: 1 for k in range(14)}), tm.seat())
        self.goto('Items')
        self.op(tm.OP_CONFIRM)
        for _ in range(11):
            self.op(tm.OP_NAV, 1, 0)
        v = self.view()
        self.assertEqual(v.total, 14)
        self.assertEqual(v.row_count, 8)
        self.assertLessEqual(v.first, 11)
        self.assertEqual(v.first + v.cursor, 11)
        self.assertEqual(v.rows[v.cursor].text.decode(), f'Act{0x52 + 11}')


# ---- seat colours -----------------------------------------------------------------------------------------------------------
@native_test
class SeatColourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def setUp(self):
        self.d.ILCT_HmReset()

    @staticmethod
    def settings(mode: int, seats=(0, 3, 6, 5), characters=(0, 2, 5, 6, 3, 4)) -> bytes:
        schema = json.loads((COOP / 'schema' / 'bridge-abi-v1.json').read_text(encoding='utf-8'))
        fields = {f['name']: f for f in schema['structs']['ilc_settings']['fields']}
        colour = next(f for f in schema['structs']['ilc_seat_settings']['fields'] if f['name'] == 'colour')
        rec = bytearray.fromhex(schema['defaults']['hex'])
        rec[fields['colour_mode']['offset']] = mode
        for k, c in enumerate(characters):
            rec[fields['character_colour']['offset'] + k] = c
        for k, c in enumerate(seats):
            rec[fields['seats']['offset'] + k * fields['seats']['elemSize'] + colour['offset']] = c
        struct.pack_into('<I', rec, 12, 2)
        return bytes(rec)

    def test_every_colour_in_both_modes(self):
        idx = u8()
        for c in range(7):
            # Colours follow seats (PLAYER = 1): the seat's own colour whoever it holds.
            self.d.ILCT_HmConfigure(self.settings(1, seats=(c, c, c, c)))
            for seat in range(4):
                for member in (0, 3, 0x20):
                    with self.subTest(mode='player', colour=c, seat=seat, member=member):
                        self.assertEqual(self.d.ILCT_HmSeatColour(seat, member, ctypes.byref(idx)), seat_rgba(c))
                        self.assertEqual(idx.value, c)
            # Colours follow characters (CHARACTER = 0): the character's colour; no character -> the seat's.
            self.d.ILCT_HmConfigure(self.settings(0, seats=(1, 1, 1, 1), characters=(c,) * 6))
            for seat in range(4):
                with self.subTest(mode='character', colour=c, seat=seat):
                    self.assertEqual(self.d.ILCT_HmSeatColour(seat, 2, ctypes.byref(idx)), seat_rgba(c))
                    self.assertEqual(self.d.ILCT_HmSeatColour(seat, 0x20, ctypes.byref(idx)), seat_rgba(1))

    def test_the_seat_edge_takes_the_configured_colour(self):
        # Through the view: the sampler resolves the colour; the drawn edge and selection bar carry it.
        self.d.ILCT_HmConfigure(self.settings(1, seats=(4, 0, 3, 5)))
        for seat, want in ((1, 0), (2, 3), (3, 5)):
            colour = self.d.ILCT_HmSeatColour(seat, 1, None)
            sv = seat_view(member=1, rows=3, colour=colour, cursor=0)
            v = menu_view({seat: sv})
            f = th.Frame()
            snap = snapshot(rows=4, seats=[0, seat + 1, 0, 0])
            self.d.ILCT_HmDraw(ctypes.byref(v), ctypes.byref(snap), NOW + 1000, 0, 0, ctypes.byref(f))
            cmds = commands(f)
            edge = next(c for c in cmds if c.kind == th.CMD_SPRITE and c.sheet == 1 and c.w == 4)
            bar = next(c for c in cmds if c.kind == th.CMD_BAR)
            with self.subTest(seat=seat):
                self.assertEqual(edge.colour, seat_rgba(want))
                self.assertEqual(bar.colour, seat_rgba(want, 0x40))

    def test_defaults_follow_characters(self):
        idx = u8()
        # Native defaults: colours follow characters (Vaan sapphire, Ashe pink, Fran amethyst, Balthier bronze,
        # Basch emerald, Penelo gold); a character outside 0..5 takes the seat's colour (P2 emerald).
        for member, want in ((0, 0), (1, 2), (2, 5), (3, 6), (4, 3), (5, 4)):
            with self.subTest(member=member):
                self.assertEqual(self.d.ILCT_HmSeatColour(1, member, ctypes.byref(idx)), seat_rgba(want))
                self.assertEqual(idx.value, want)
        self.assertEqual(self.d.ILCT_HmSeatColour(1, 0x20, ctypes.byref(idx)), seat_rgba(3))


# ---- the menu pass -------------------------------------------------------------------------------------------------------------
@native_test
class PassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def setUp(self):
        self.d.ILCT_HmReset()

    def tearDown(self):
        self.d.ILCT_HmReset()

    def run_pass(self, snap=None, now=NOW) -> th.Frame:
        f = th.Frame()
        self.d.ILCT_HmPass(ctypes.byref(snapshot() if snap is None else snap), now, 0, ctypes.byref(f))
        return f

    def test_the_published_view_is_drawn_and_only_then(self):
        v = menu_view({0: seat_view()})
        self.assertEqual(self.run_pass().count, 0, 'nothing published')
        self.d.ILCT_HmPublish(ctypes.byref(v))
        self.assertGreater(self.run_pass().count, 0)
        self.assertEqual(self.run_pass(snapshot(epoch=EPOCH + 1)).count, 0, 'a view of another epoch')
        self.assertGreater(self.run_pass(now=NOW + 250).count, 0, '250 ms old: still drawn')
        self.assertEqual(self.run_pass(now=NOW + 251).count, 0, 'stale')
        self.d.ILCT_HmWithdraw()
        self.assertEqual(self.run_pass().count, 0, 'withdrawn: not even the last good copy')
        c = Counters()
        self.d.ILCT_HmStats(ctypes.byref(c))
        self.assertGreaterEqual(c.stale, 3)

    def test_the_pass_reports_no_seat_left_out_when_it_drew_them_all(self):
        self.d.ILCT_HmPublish(ctypes.byref(menu_view({s: seat_view(member=s) for s in range(4)})))
        self.d.ILCT_HmForceDropped(0)
        self.assertGreater(self.run_pass().count, 0)
        self.assertEqual(self.d.ILCT_HmLastDropped(), 0)
        self.d.ILCT_HmWithdraw()
        self.run_pass()
        self.assertEqual(self.d.ILCT_HmLastDropped(), 0, 'no view: nothing known left out')

    def test_inside_the_frame_driver_the_menus_are_the_last_commands(self):
        hw, d = self.hw, self.d
        hw.setup()
        d.ILCT_HudSetTestDraw(0)
        try:
            d.ILCT_HudSnapPublish(ctypes.byref(snapshot(leader=5)))
            d.ILCT_HmPublish(ctypes.byref(menu_view({0: seat_view(rows=3, title='Items')})))
            d.ILCT_HudDrvSetProbe(0xF)
            out = th.Frame()
            self.assertEqual(d.ILCT_HudDrvCompose(EPOCH, NOW, None, ctypes.byref(out)), thd.COMPOSE_OK)
            t = texts(out)
            self.assertEqual(t[3], th.encoded('menus'), 'the menu pass marker, then the menus')
            self.assertEqual([decoded(x) for x in t[4:6]], ['Basch', 'Row 0 name'], 'the root page: no title')
            self.assertEqual(t[-1], th.encoded('Help line.'), 'the last command of the frame is a menu command')
            d.ILCT_PatchApplySubsystems(0xFF & ~thd.FIREWALL_MENUS)
            out = th.Frame()
            d.ILCT_HudDrvCompose(EPOCH, NOW, None, ctypes.byref(out))
            self.assertEqual(texts(out), [th.encoded(n) for n in thd.PASS_NAMES[:3]], 'no menu without FIREWALL_MENUS')
        finally:
            d.ILCT_HudDrvReset()
            d.ILCT_HmReset()
            hw.twin.reset()


# ---- names from the game's text --------------------------------------------------------------------------------------------------
@native_test
class NamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = Dll.get()

    def decode(self, raw: bytes) -> str:
        out = ctypes.create_string_buffer(64)
        self.d.ILCT_HmDecode(raw, len(raw), out, 64)
        return out.value.decode()

    def test_the_decoder(self):
        self.assertEqual(self.decode(th.encoded('Hi-Potion')), 'Hi-Potion')
        self.assertEqual(self.decode(bytes([0x22, 0x4E, 0x4B, 0x3E])), 'Cure', 'a section 61 entry after its prefix')
        self.assertEqual(self.decode(th.encoded('Fire') + b'\x0f\x29\x83\x80\x80\x80' + th.encoded('ga')), 'Firega',
                         'colour tokens are skipped')
        self.assertEqual(self.decode(th.encoded('A') + bytes([4, 4]) + th.encoded('B') + bytes([4])), 'A B')
        self.assertEqual(self.decode(bytes([0x01, 0x20])), 'A', 'control codes are left out')
        self.assertEqual(self.decode(th.encoded('x' * 80)), 'x' * 63, 'bounded by the buffer')

    @unittest.skipUnless(BATTLE_PACK, 'set ILC_BATTLE_PACK to the Steam battle_pack.bin')
    def test_names_from_the_real_battle_pack(self):
        from coop_session_support import Image
        pack = Path(BATTLE_PACK).read_bytes()
        count = struct.unpack_from('<I', pack)[0]
        offs = struct.unpack_from(f'<{count + 1}I', pack, 4)
        sections = tm.pack_sections(BATTLE_PACK, None)
        img, heap = Image(), tm.LowHeap(4 << 20)
        try:
            tm.place_tables(img, heap, {tm.T_ACTIONS: sections[tm.T_ACTIONS], tm.T_MEMBERS: sections[tm.T_MEMBERS]})
            # Section 61 as the pack loader leaves it: sub-table and entry offsets turned into 32-bit addresses.
            raw = bytearray(pack[offs[61]:offs[62]])
            base = heap.alloc(len(raw), page=True)
            n = struct.unpack_from('<H', raw)[0]
            for k in range(n):
                sub = struct.unpack_from('<I', raw, 4 + 4 * k)[0]
                entries = struct.unpack_from('<H', raw, sub)[0]
                for i in range(entries):
                    rel = struct.unpack_from('<I', raw, sub + 4 + 4 * i)[0]
                    struct.pack_into('<I', raw, sub + 4 + 4 * i, base + sub + rel)
                struct.pack_into('<I', raw, 4 + 4 * k, base + sub)
            Image.put(base, bytes(raw))
            img.put_global(0x2EBF170, struct.pack('<Q', base))
            self.d.ILCT_HmSetImageBase(img.base)
            out = ctypes.create_string_buffer(24)

            def name(kind, ident, pointer=0):
                ok = self.d.ILCT_HmLiveName(kind, ident, pointer, out, 24)
                return out.value.decode() if ok else None

            self.assertEqual(name(1, 0x96), 'Attack')
            self.assertEqual(name(2, 0), 'Vaan')
            self.assertEqual(name(2, 3), 'Balthier')
            self.assertIsNone(name(1, 5000), 'past the table')
            foe = (ctypes.c_char * 8)(*th.encoded('Wolf'))
            self.assertEqual(name(3, 0, ctypes.addressof(foe)), 'Wolf', 'a bare name text')
            self.assertIsNone(name(3, 0, 0))
        finally:
            self.d.ILCT_HmSetImageBase(0)
            img.close()
            heap.close()


if __name__ == '__main__':
    unittest.main()
