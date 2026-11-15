"""Host tests for the couch layout (native/coop/src/hud/layout.*): the corner table, the boss-gauge drop and the
integration of every HUD pass. No game run, nothing deployed.

* the table: Target Info top-left and the battle log top-right on one header line, Player 1's menu bottom-left, the
  party list bottom-right, guest menus out of their rows; the online mode returns the couch table (seam);
* the boss gauge: read from the chain in the HUD facts (0x2B62D88 -> +0xC0 -> +0xC8 / +0xD0 -> +0x130 bit 0x10) on
  synthetic memory, carried in the snapshot, and both corner headers move down together; the expanded log keeps its
  zone's bottom and drops its oldest lines, nothing shrinks;
* integration: the Target Info, battle log, party rows and every menu window drawn by their own passes from one
  snapshot, for 1-4 seats, party sizes 1-4, the stock and the reduced party list, with and without the boss gauge, at
  1920x1080, 1366x768 and 1280x720: no two elements' boxes overlap and every box is on screen (the menus' own
  overlap check extended to every element);
* the text floor: the corner text is at least TEXT_FLOOR_PX high at the smallest screen.
"""
from __future__ import annotations

import ctypes
import re
import unittest
from pathlib import Path

import test_coop_hud as th
import test_coop_hud_battle_log as tbl
import test_coop_hud_driver as thd
import test_coop_hud_menus as thm
import test_coop_hud_party_rows as tpr
import test_coop_hud_target_info as tti
from coop_native_support import native_test
from test_coop_patch_engine import GAME

ROOT = Path(__file__).resolve().parents[1]
HUD_SRC = ROOT / 'native' / 'coop' / 'src' / 'hud'

u8, u16, u32, u64, i16, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int16, ctypes.c_int32
P = ctypes.POINTER
EPOCH, NOW = thd.EPOCH, thd.NOW
RESOLUTIONS = thm.RESOLUTIONS
SNAP_BOSS_GAUGE = 0x20
LAYOUT_COUCH, LAYOUT_ONLINE = 0, 1
CORNER_TI, CORNER_LOG, CORNER_P1, CORNER_PARTY = range(4)
# facts restated (snapshot_facts.h)
BOSS_MANAGER, BOSS_OWNER, BOSS_OBJECTS, BOSS_FLAGS, BOSS_SHOWN = 0x2B62D88, 0xC0, (0xC8, 0xD0), 0x130, 0x10
IMAGE_VA = 0x120000
BOSS_SHIFT, TEXT_FLOOR_PX, FONT_HEIGHT, HEADER_Y = 54, 13, 26, 50
ADVANCE = 14   # authoring units per character at scale 100 (target_info::corner::ADVANCE_X100 / 100)
TI_CAP, ROWS_CAP = 72, 40   # target_info.h MAX_COMMANDS_TI, party_rows.h PASS_MAX_COMMANDS
LOG_FLOOR, LOG_TEXT_FLOOR = 12, 160   # battle_log.h: what the log pass may always use while it replaces the native log
STATUS_W, CHIP = 22, 8      # target_info.cpp: a status icon's width, a drop line's chip

Zone = thm.Box

SIGNATURES = {
    'ILCT_LyCorners': ([u32, i32, u32, i32, P(Zone)], None), 'ILCT_LyHeaderDy': ([u32, i32], i32),
    'ILCT_LyLogLines': ([i32, u32, i32, u32], u32), 'ILCT_LyTextPx': ([u32, u32, u32], i32),
    'ILCT_LyBossShown': ([u64], i32), 'ILCT_LyConstants': ([P(i32)], None),
    'ILCT_LyTiDraw': ([P(thd.Snapshot), u64, P(th.Frame)], u32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def overlap(a, b) -> bool:
    return thm.boxes_overlap(a, b)


def union(boxes):
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def frame_box(frame: th.Frame):
    """The bounding box of everything a frame draws (text: estimated advance and the font's line height)."""
    boxes = []
    for k in range(frame.count):
        c = frame.cmds[k]
        if c.kind == th.CMD_TEXT:
            raw = frame.text[c.text:].split(b'\0', 1)[0]
            scale = c.scale or 100
            boxes.append((c.x, c.y, max(1, len(raw) * ADVANCE * scale // 100), max(1, FONT_HEIGHT * scale // 100)))
        else:
            boxes.append((c.x, c.y, max(1, c.w), max(1, c.h)))
    return union(boxes)


def tup(b) -> tuple:
    return (b.x, b.y, b.w, b.h)


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_one_table_for_the_corners(self):
        menus = (HUD_SRC / 'menus_draw.cpp').read_text(encoding='utf-8')
        self.assertNotIn('{16, 16, 480, 360}', menus, 'the menus read the corners from the layout table')
        self.assertNotIn('{1360, 16, 544, 680}', menus)
        table = (HUD_SRC / 'layout.h').read_text(encoding='utf-8')
        self.assertIn('constexpr zone TARGET_INFO_ZONE = {16, 16, 480, 360};', table)
        self.assertIn('constexpr zone LOG_ZONE = {1360, 16, 544, 680};', table)
        log_h = (HUD_SRC / 'battle_log.h').read_text(encoding='utf-8')
        self.assertIn('RIGHT_LIMIT = 1904;', log_h, 'the log zone\'s right edge (1360 + 544)')

    def test_boss_gauge_facts_and_wiring(self):
        facts = (HUD_SRC / 'snapshot_facts.h').read_text(encoding='utf-8')
        for name, v in (('BOSS_GAUGE_MANAGER', BOSS_MANAGER), ('BOSS_GAUGE_OWNER', BOSS_OWNER),
                        ('BOSS_GAUGE_OBJECT_A', BOSS_OBJECTS[0]), ('BOSS_GAUGE_OBJECT_B', BOSS_OBJECTS[1]),
                        ('BOSS_GAUGE_FLAGS', BOSS_FLAGS), ('BOSS_GAUGE_SHOWN', BOSS_SHOWN)):
            self.assertRegex(facts, rf'{name} = 0x{v:X};', name)
        snap = (HUD_SRC / 'snapshot.cpp').read_text(encoding='utf-8')
        self.assertIn('s->head.flags |= SNAP_BOSS_GAUGE;', snap)
        for f in ('target_info.cpp', 'battle_log.cpp'):
            src = (HUD_SRC / f).read_text(encoding='utf-8')
            self.assertIn('layout::header_dy(in.layout, layout::boss_gauge(*in.snap))', src, f)
        menus = (HUD_SRC / 'menus_draw.cpp').read_text(encoding='utf-8')
        self.assertIn('layout::boss_gauge(snap)', menus, 'the menus keep clear of the moved Target Info')

    def test_choosing_is_shown_only_where_no_guest_window_goes(self):
        src = (HUD_SRC / 'party_rows.cpp').read_text(encoding='utf-8')
        self.assertIn('guest_open = guest_open || (s != 0 && open);', src)
        self.assertIn('for (uint32_t s = 0; s < frontend::SEATS && !guest_open; ++s) {', src)
        self.assertIn('if (s != 0 || (v.mode != menu::MODE_LIST', src)

    def test_online_is_a_seam(self):
        snap = (HUD_SRC / 'snapshot.cpp').read_text(encoding='utf-8')
        self.assertRegex(snap, r'uint8_t layout_for_session\(\)\s*\{\s*return LAYOUT_COUCH;')


# ---- the table and the boss gauge (native) ------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the world test DLL)')
class TableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = thm.Dll.get()
        declare(cls.d)

    def corners(self, mode=LAYOUT_COUCH, boss=False, rows=4, reduced=True) -> list:
        out = (Zone * 4)()
        self.d.ILCT_LyCorners(mode, 1 if boss else 0, rows, 1 if reduced else 0, out)
        return [tup(z) for z in out]

    def test_constants(self):
        c = (i32 * 6)()
        self.d.ILCT_LyConstants(c)
        self.assertEqual(list(c), [BOSS_SHIFT, TEXT_FLOOR_PX, FONT_HEIGHT, HEADER_Y, tbl.LINE_PITCH, tbl.LINES_DY])

    def test_the_corners(self):
        for reduced in (True, False):
            for rows in range(1, 5):
                with self.subTest(reduced=reduced, rows=rows):
                    ti, log, p1, party = self.corners(rows=rows, reduced=reduced)
                    self.assertLess(ti[0] + ti[2], 960, 'Target Info: the left half')
                    self.assertLess(ti[1] + ti[3], 540, '... the top half')
                    self.assertGreater(log[0], 960, 'battle log: the right half')
                    self.assertLess(log[1], 540, '... from the top')
                    self.assertLess(p1[0] + p1[2], 960, 'Player 1\'s menu: the left half')
                    self.assertGreater(p1[1] + p1[3], 540, '... at the bottom')
                    self.assertGreater(party[0] + party[2], 1600, 'party list: the right edge')
                    self.assertGreater(party[1], 540, '... at the bottom')
                    self.assertEqual(ti[1], log[1], 'the two top corners start on one line')
                    for a, b in ((ti, log), (ti, p1), (ti, party), (log, p1), (log, party), (p1, party)):
                        self.assertFalse(overlap(a, b), (a, b))
        self.assertEqual(self.corners(LAYOUT_ONLINE), self.corners(LAYOUT_COUCH), 'online: the same table (seam)')

    def test_the_boss_gauge_moves_both_headers_together(self):
        self.assertEqual(self.d.ILCT_LyHeaderDy(LAYOUT_COUCH, 0), 0)
        self.assertEqual(self.d.ILCT_LyHeaderDy(LAYOUT_COUCH, 1), BOSS_SHIFT)
        self.assertEqual(self.d.ILCT_LyHeaderDy(LAYOUT_ONLINE, 1), BOSS_SHIFT)
        ti0, log0, _, _ = self.corners(boss=False)
        ti1, log1, _, _ = self.corners(boss=True)
        self.assertEqual(ti1[3] - ti0[3], BOSS_SHIFT, 'Target Info\'s zone grows down with its header')
        self.assertEqual(log1, log0, 'the log keeps its zone ...')
        lines = self.d.ILCT_LyLogLines
        self.assertEqual(lines(0, tbl.LINE_PITCH, tbl.LINES_DY, 20), 20)
        self.assertEqual(lines(BOSS_SHIFT, tbl.LINE_PITCH, tbl.LINES_DY, 20), 18, '... and drops two lines')
        self.assertEqual(lines(0, tbl.LINE_PITCH, tbl.LINES_DY, 5), 5)
        self.assertEqual(lines(2000, tbl.LINE_PITCH, tbl.LINES_DY, 20), 0)
        self.assertEqual(lines(0, 0, tbl.LINES_DY, 20), 0)

    def test_boss_gauge_read_on_synthetic_memory(self):
        mem = (ctypes.c_uint8 * 0x1000)()
        at = ctypes.addressof(mem)
        base = (at - (BOSS_MANAGER - IMAGE_VA)) & 0xFFFFFFFFFFFFFFFF
        manager, owner, obj_a, obj_b = at + 0x100, at + 0x300, at + 0x500, at + 0x800

        def put64(addr, v):
            ctypes.c_uint64.from_address(addr).value = v

        def put32(addr, v):
            ctypes.c_uint32.from_address(addr).value = v

        shown = self.d.ILCT_LyBossShown
        self.assertEqual(shown(base), 0, 'no manager')
        put64(at, manager)
        self.assertEqual(shown(base), 0, 'no owner')
        put64(manager + BOSS_OWNER, owner)
        self.assertEqual(shown(base), 0, 'no objects')
        put64(owner + BOSS_OBJECTS[0], obj_a)
        put64(owner + BOSS_OBJECTS[1], obj_b)
        put32(obj_a + BOSS_FLAGS, 0xFFFFFFEF)
        self.assertEqual(shown(base), 0, 'every flag but the shown bit')
        put32(obj_b + BOSS_FLAGS, BOSS_SHOWN)
        self.assertEqual(shown(base), 1, 'the second object shows')
        put32(obj_b + BOSS_FLAGS, 0)
        put32(obj_a + BOSS_FLAGS, BOSS_SHOWN)
        self.assertEqual(shown(base), 1, 'the first object shows')
        put64(owner + BOSS_OBJECTS[0], 0x10)
        self.assertEqual(shown(base), 0, 'an unreadable object is not shown (fail closed)')

    def test_text_floor(self):
        """The corner text (the headers at scale 75, the log lines at 80) and the menus' rows (80, P1 100) stay at or
        above the floor at every screen. FONT_HEIGHT is [U] (unmeasured), so smaller detail text (the Target Info's
        55-70, the guests' help line at 70) is an open item, not asserted here."""
        px = self.d.ILCT_LyTextPx
        self.assertEqual(px(100, 1920, 1080), FONT_HEIGHT)
        self.assertEqual(px(100, 3840, 1080), FONT_HEIGHT, 'letterboxed: the height decides')
        self.assertEqual(px(75, 1280, 720), 13, 'exact: 26 x 0.75 x 2/3')
        self.assertEqual(px(100, 0, 720), 0)
        for scale in (75, 80, 100):
            for w, h in RESOLUTIONS:
                with self.subTest(scale=scale, res=(w, h)):
                    self.assertGreaterEqual(px(scale, w, h), TEXT_FLOOR_PX)


# ---- integration ---------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the world test DLL)')
class IntegrationTest(unittest.TestCase):
    """Every pass draws from one snapshot; their boxes never overlap, at three resolutions."""

    @classmethod
    def setUpClass(cls):
        cls.hw, cls.d = thm.Dll.get()
        declare(cls.d)
        for name, (args, res) in tti.SIGNATURES.items():
            fn = getattr(cls.d, name)
            fn.argtypes, fn.restype = args, res
        tbl.declare(cls.d)
        tpr.declare(cls.d)

    def setUp(self):
        self.d.ILCT_HmReset()
        self.d.ILCT_TiReset()
        self.d.ILCT_BlReset()
        self.d.ILCT_RowsReset()
        self.d.ILCT_BlConfigure(1, 1, 0, 0, 1)

    def tearDown(self):
        self.d.ILCT_TiReset()
        self.d.ILCT_BlReset()
        self.d.ILCT_RowsReset()
        self.d.ILCT_HmReset()

    # one snapshot for every pass
    def snapshot(self, rows: int, boss: bool):
        units = [tpr.unit(k, seat=k + 1 if k < 4 else 0, hp=300 if k == 0 else 1000) for k in range(rows)]
        for u in units:
            u.status_timed = (1 << tpr.PROTECT) | (1 << tpr.POISON)
            u.duration[tpr.PROTECT], u.duration[tpr.POISON] = 3000, 1500
        s = tpr.party_snapshot(units)
        if boss:
            s.flags |= SNAP_BOSS_GAUGE
        return s

    def publish_everything(self, rows: int, reduced: bool):
        v = tti.a_view()
        for k in range(8):
            d = v.drops[k]
            d.kind, d.state, d.text = tti.DROP, tti.KNOWN, b'Long Drop Name ' + bytes([0x41 + k])
        v.drop_count = 8
        self.d.ILCT_TiPublish(ctypes.byref(v))
        lines = [tbl.make_line(k + 1, touched=NOW, actor=b'Balthier', body=b'Hell Blaster', amounts=(9999, 9999),
                               icons=(1, 2)) for k in range(20)]
        self.d.ILCT_BlPublish(ctypes.byref(tbl.make_view(lines, flags=tbl.VIEW_EXPANDED, built=NOW)))
        self.publish_rows(rows, reduced, p1_choosing=True)

    def publish_rows(self, rows: int, reduced: bool, p1_choosing: bool):
        # The sampler's rule: "Choosing..." only for Player 1's row and only while no guest window is open.
        extras = [(k, tpr.EXTRA_READ | tpr.EXTRA_GAMBITS_ON | (tpr.EXTRA_CHOOSING if k == 0 and p1_choosing else 0),
                   k % 7, 0xFFFF) for k in range(rows)]
        self.d.ILCT_RowsPublish(ctypes.byref(tpr.record(extras, geometry=tpr.REDUCED if reduced else tpr.STOCK)))

    def element_boxes(self, rows: int, nseats: int, reduced: bool, boss: bool, kind: dict) -> dict:
        snap = self.snapshot(rows, boss)
        self.publish_rows(rows, reduced, p1_choosing=nseats == 1)
        out = {}
        f = th.Frame()
        self.d.ILCT_LyTiDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
        out['Target Info'] = frame_box(f)
        self.d.ILCT_BlDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
        out['battle log'] = frame_box(f)
        self.d.ILCT_RowsDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
        party = thm.Box()
        self.d.ILCT_HmPartyList(rows, 1 if reduced else 0, ctypes.byref(party))
        out['party list'] = union([frame_box(f), tup(party)])
        v = thm.menu_view({s: thm.seat_view(member=s, **kind) for s in range(nseats)}, reduced=reduced)
        fl = thm.FrameLayout()
        self.d.ILCT_HmLayout(ctypes.byref(v), ctypes.byref(snap), thm.COMMAND_CAP, thm.TEXT_CAP, ctypes.byref(fl))
        self.assertEqual((fl.count, fl.dropped), (nseats, 0))
        for k in range(fl.count):
            w = fl.w[k]
            out[f'P{w.seat + 1} menu'] = tup(w.frame)
        return out

    def test_no_element_overlaps_another_at_three_resolutions(self):
        kinds = {'list': dict(kind=thm.WK_LIST, rows=8, values=True, toggle=True),
                 'target': dict(kind=thm.WK_TARGET, rows=6)}
        cases = 0
        for boss in (False, True):
            for reduced in (True, False):
                for rows in range(1, 5):
                    self.publish_everything(rows, reduced)
                    for nseats in range(1, 5):
                        for kname, kind in kinds.items():
                            cases += 1
                            boxes = self.element_boxes(rows, nseats, reduced, boss, kind)
                            with self.subTest(boss=boss, reduced=reduced, rows=rows, seats=nseats, kind=kname):
                                for name in ('Target Info', 'battle log', 'party list'):
                                    self.assertIsNotNone(boxes[name], f'{name} drew something')
                                names = list(boxes)
                                for res in RESOLUTIONS:
                                    px = {n: thm.to_screen(boxes[n], res) for n in names}
                                    for i, a in enumerate(names):
                                        x, y, w, h = px[a]
                                        self.assertTrue(0 <= x and 0 <= y and x + w <= res[0] and y + h <= res[1],
                                                        f'{a} on screen at {res}: {px[a]}')
                                        for b in names[i + 1:]:
                                            self.assertFalse(overlap(px[a], px[b]), f'{a} {px[a]} vs {b} {px[b]} at {res}')
        self.assertEqual(cases, 2 * 2 * 4 * 4 * 2)

    def test_both_headers_share_one_line_with_and_without_the_boss_gauge(self):
        self.publish_everything(4, True)
        for boss in (False, True):
            snap = self.snapshot(4, boss)
            ti, log = th.Frame(), th.Frame()
            self.d.ILCT_LyTiDraw(ctypes.byref(snap), NOW, ctypes.byref(ti))
            self.d.ILCT_BlDraw(ctypes.byref(snap), NOW, ctypes.byref(log))
            ti_head = [c for c in tti.frame_texts(ti) if c[2] == 'TARGET INFO']
            log_head = [c for c in tti.frame_texts(log) if c[2].startswith('BATTLE LOG')]
            with self.subTest(boss=boss):
                self.assertEqual(len(ti_head), 1)
                self.assertEqual(len(log_head), 1)
                want = HEADER_Y + (BOSS_SHIFT if boss else 0)
                self.assertEqual((ti_head[0][1], log_head[0][1]), (want, want))
                for res in RESOLUTIONS:
                    a = thm.to_screen((0, ti_head[0][1], 1, 1), res)
                    b = thm.to_screen((0, log_head[0][1], 1, 1), res)
                    self.assertEqual(a[1], b[1], f'one header line at {res}')

    def test_the_expanded_log_drops_lines_under_the_boss_gauge_and_never_shrinks(self):
        self.publish_everything(4, True)
        counts = {}
        for boss in (False, True):
            snap = self.snapshot(4, boss)
            f = th.Frame()
            self.d.ILCT_BlDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
            texts = [c for c in tti.frame_texts(f) if not c[2].startswith('BATTLE LOG')]
            counts[boss] = len({c[1] for c in texts})
            box = frame_box(f)
            self.assertLessEqual(box[1] + box[3], 16 + 680 + 4, f'inside the log zone (boss={boss}): {box}')
        self.assertEqual(counts[False] - counts[True], 2, 'two fewer rows: the oldest lines go')


    # ---- the menus' floor under capped passes (every pass yields to the open windows' floor) ----------------------
    def publish_capped(self):
        """Target Info and the party rows at their own caps: a Libra'd boss with every element row full, every status slot
        timed and eight long drop lines; four party rows with timed statuses, the action line and party-target hands;
        the expanded log."""
        self.publish_everything(4, True)
        v = tti.a_view()
        for k in range(10):
            v.status_id[k], v.status_flags[k], v.status_secs[k] = k + 1, tti.ST_ACTIVE | tti.ST_TIMED, 59
        v.status_count = 10
        v.weakness, v.half, v.null_ = 0xFF, 0xFF, 0xFF   # every element in all three rows
        v.flags |= tti.VIEW_ELEMENTS_EXT | tti.VIEW_BOSS | tti.VIEW_DANGER
        self.d.ILCT_TiPublish(ctypes.byref(v))
        rows = [(k, tpr.EXTRA_READ | tpr.EXTRA_GAMBITS_ON | tpr.EXTRA_ACTING, k % 7, 0x10 + k) for k in range(4)]
        self.d.ILCT_RowsPublish(ctypes.byref(tpr.record(rows, geometry=tpr.REDUCED)))

    def capped_snapshot(self):
        snap = self.snapshot(4, True)
        for k in range(4):
            u = snap.members[k]
            for sid in (tpr.PROTECT, tpr.SHELL, tpr.HASTE, tpr.POISON, tpr.SLOW, tpr.REGEN):
                u.status_timed |= 1 << sid
                u.duration[sid] = 3000
            snap.seats[k].handle, snap.seats[k].on_page = snap.members[(k + 1) % 4].handle, 1
        return snap

    def compose_with_menus(self, nseats: int, kind: dict, test_frame=None):
        snap = self.capped_snapshot()
        self.d.ILCT_HudSnapPublish(ctypes.byref(snap))
        v = thm.menu_view({s: thm.seat_view(member=s, **kind) for s in range(nseats)})
        self.d.ILCT_HmPublish(ctypes.byref(v))
        floor_c, floor_t = u32(), u32()
        self.d.ILCT_HmFloor(ctypes.byref(v), ctypes.byref(snap), ctypes.byref(floor_c), ctypes.byref(floor_t))
        out = th.Frame()
        before, after = thm.Counters(), thm.Counters()
        self.d.ILCT_HmStats(ctypes.byref(before))
        r = self.d.ILCT_HudDrvCompose(EPOCH, NOW, ctypes.byref(test_frame) if test_frame else None, ctypes.byref(out))
        self.d.ILCT_HmStats(ctypes.byref(after))
        self.assertEqual(r, thd.COMPOSE_OK)
        return out, after.drawn_windows - before.drawn_windows, after.dropped - before.dropped, floor_c.value

    def test_target_info_and_the_party_rows_are_capped_here(self):
        # Instrument for the next test: alone, Target Info fills its own share and the party rows most of theirs.
        self.publish_capped()
        snap = self.capped_snapshot()
        f = th.Frame()
        self.d.ILCT_LyTiDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
        # At its cap: whole sections and whole drop lines only, so up to one drop line (2 commands) short of it.
        self.assertGreaterEqual(f.count, TI_CAP - 2, 'Target Info at its cap')
        self.assertLessEqual(f.count, TI_CAP)
        self.assert_whole_sections(f)
        self.d.ILCT_RowsDraw(ctypes.byref(snap), NOW, ctypes.byref(f))
        # Four rows with timers, action lines and party-target hands (the damage ghost needs an HP drop over time).
        self.assertGreaterEqual(f.count, 24, 'the party rows well filled')
        self.assertLessEqual(f.count, ROWS_CAP)

    def test_capped_passes_leave_every_open_window_its_floor(self):
        thd.declare(self.d)
        self.hw.setup()
        self.d.ILCT_HudSetTestDraw(0)
        kinds = {'list': dict(kind=thm.WK_LIST, rows=8, values=True, toggle=True),
                 'target': dict(kind=thm.WK_TARGET, rows=6, values=True)}
        try:
            self.publish_capped()
            for nseats in (3, 4):
                for kname, kind in kinds.items():
                    with self.subTest(seats=nseats, kind=kname):
                        out, drawn, dropped, floor = self.compose_with_menus(nseats, kind)
                        self.assertEqual((drawn, dropped), (nseats, 0), 'every window drawn, none dropped')
                        self.assertEqual(self.d.ILCT_HmLastDropped(), 0, 'no seat reported left out')
                        c = thd.stats(self.d)
                        self.assertEqual(c.kept_commands, floor, 'the floor was kept back for the menu pass')
                        self.assertGreater(floor, 52 if nseats == 3 else 77, 'more than the old 48 reserve')
                        self.assertLessEqual(out.count, th.MAX_COMMANDS)
                        self.assertLessEqual(out.text_used, th.TEXT_POOL)
        finally:
            self.d.ILCT_HudDrvReset()
            self.d.ILCT_HmWithdraw()
            self.hw.twin.reset()

    # ---- the battle log's floor under capped passes ---------------------------------------------------------
    @staticmethod
    def pass_slice(out, c, pass_id: int):
        """One pass's commands of a composed frame (the passes run in order; the driver counts what each added)."""
        f = th.Frame()
        start = sum(c.pass_commands[k] for k in range(pass_id))
        n = c.pass_commands[pass_id]
        ctypes.memmove(ctypes.addressof(f) + th.Frame.text.offset, ctypes.addressof(out) + th.Frame.text.offset,
                       th.TEXT_POOL)
        for k in range(n):
            f.cmds[k] = out.cmds[start + k]
        f.count = n
        return f

    def assert_whole_sections(self, f):
        """Target Info short of room ends after its last whole section: no row is cut in the middle."""
        texts = [x[2] for x in tti.frame_texts(f)]
        sprites = tti.frame_kind(f, th.CMD_SPRITE)
        bars = tti.frame_kind(f, th.CMD_BAR)
        self.assertIn('TARGET INFO', texts)
        if 'Weak' in texts:
            self.assertEqual(len([s for s in sprites if s.atlas == tti.ATLAS_BATTLE_4_C and s.page == 10]), 24,
                             'the element row whole (Weak, Half and Null: 8 each)')
        if 'Status' in texts:
            icons = [s for s in sprites if s.atlas == tti.ATLAS_PARTYTOP_4_C and s.page == 3 and s.w == STATUS_W]
            self.assertEqual((len(icons), texts.count('59')), (10, 10), 'the status row whole: icons and timers')
        if 'DROPS' in texts:
            chips = [b for b in bars if b.w == CHIP and b.h == CHIP]
            lines = [x for x in texts[texts.index('DROPS') + 1:] if x not in ('steal', 'special', 'poach')]
            self.assertEqual(len(chips), len(lines), 'whole drop lines (chip and name)')
            self.assertGreater(len(lines), 0, 'the title never alone')

    def test_capped_passes_leave_the_battle_log_its_floor(self):
        thd.declare(self.d)
        self.hw.setup()
        self.d.ILCT_HudSetTestDraw(0)
        header = (HUD_SRC / 'battle_log.h').read_text(encoding='utf-8')
        self.assertIn(f'constexpr uint32_t LOG_FLOOR = {LOG_FLOOR};', header)
        self.assertIn(f'constexpr uint32_t LOG_TEXT_FLOOR = {LOG_TEXT_FLOOR};', header)
        kinds = {'list': dict(kind=thm.WK_LIST, rows=8, values=True, toggle=True),
                 'target': dict(kind=thm.WK_TARGET, rows=6, values=True)}
        try:
            self.publish_capped()
            ti_alone = th.Frame()
            self.d.ILCT_LyTiDraw(ctypes.byref(self.capped_snapshot()), NOW, ctypes.byref(ti_alone))
            for nseats in (3, 4):
                for kname, kind in kinds.items():
                    with self.subTest(seats=nseats, kind=kname):
                        out, drawn, dropped, floor = self.compose_with_menus(nseats, kind)
                        c = thd.stats(self.d)
                        self.assertEqual((drawn, dropped), (nseats, 0), 'every menu window drawn')
                        self.assertEqual(self.d.ILCT_HmLastDropped(), 0)
                        self.assertEqual((c.kept_commands, c.kept_log), (floor, LOG_FLOOR), 'both floors kept')
                        rows, ti, log, menus = (c.pass_commands[k] for k in range(4))
                        self.assertGreaterEqual(log, LOG_FLOOR, 'the battle log gets at least its floor')
                        self.assertLessEqual(rows + ti, th.MAX_COMMANDS - floor - LOG_FLOOR, 'party and TI yielded')
                        self.assertEqual(rows + ti + log + menus, out.count)
                        log_texts = [x[2] for x in tti.frame_texts(self.pass_slice(out, c, thd.PASS_LOG))]
                        self.assertTrue(log_texts and log_texts[0].startswith('BATTLE LOG'), 'its header')
                        self.assertGreaterEqual(len(log_texts), 3, 'the header and lines')
                        ti_frame = self.pass_slice(out, c, thd.PASS_TI)
                        if nseats == 4:   # three seats leave Target Info its whole share; four do not
                            self.assertLess(ti_frame.count, ti_alone.count, 'instrument: Target Info had to yield')
                        self.assert_whole_sections(ti_frame)
                        self.assertLessEqual(out.count, th.MAX_COMMANDS)
                        self.assertLessEqual(out.text_used, th.TEXT_POOL)
            # Control: the log element off (the native log shows): no floor kept for it, Target Info gets the room.
            with self.subTest('log off'):
                self.compose_with_menus(4, kinds['list'])
                c0 = thd.stats(self.d)
                self.d.ILCT_BlConfigure(0, 1, 0, 0, 1)
                self.compose_with_menus(4, kinds['list'])
                c = thd.stats(self.d)
                self.assertEqual((c.kept_log, c.pass_commands[thd.PASS_LOG]), (0, 0))
                self.assertGreater(c.pass_commands[thd.PASS_TI], c0.pass_commands[thd.PASS_TI], 'the room went to TI')
        finally:
            self.d.ILCT_BlConfigure(1, 1, 0, 0, 1)
            self.d.ILCT_HudDrvReset()
            self.d.ILCT_HmWithdraw()
            self.hw.twin.reset()

    def test_a_full_diagnostics_frame_never_pushes_a_menu_out(self):
        thd.declare(self.d)
        self.hw.setup()
        self.d.ILCT_HudSetTestDraw(0)
        try:
            self.publish_capped()
            d = self.d
            d.ILCT_HudBegin(EPOCH, NOW)
            for k in range(th.MAX_COMMANDS):
                d.ILCT_HudText(700, 100 + k, b'test pattern', th.WHITE, 100, 0)
            test = self.hw.frame()
            self.assertEqual(test.count, th.MAX_COMMANDS)
            alone, drawn0, _, _ = self.compose_with_menus(4, dict(kind=thm.WK_LIST, rows=8, values=True))
            layer = (alone.count, alone.text_used)
            out, drawn, dropped, _ = self.compose_with_menus(4, dict(kind=thm.WK_LIST, rows=8, values=True), test)
            self.assertEqual((drawn, dropped), (4, 0))
            self.assertEqual(out.count, th.MAX_COMMANDS)
            self.assertEqual(thd.stats(d).appended, th.MAX_COMMANDS - layer[0], 'the pattern got only what was left')
            tail = [out.cmds[out.count - layer[0] + k].kind for k in range(layer[0])]
            self.assertEqual(tail, [alone.cmds[k].kind for k in range(layer[0])], 'the whole layer, menus last')
        finally:
            self.d.ILCT_HudDrvReset()
            self.d.ILCT_HmWithdraw()
            self.hw.twin.reset()

    def test_budget_of_the_worst_frame(self):
        """Every pass at once (4 open windows, the expanded log, the full Target Info, 4 party rows, the boss gauge):
        within the frame's commands and text, the ring estimate under half of the canvas share, and the host cost of
        composing it (the driver's passes, not the game's replay) far under the 0.35 ms frame budget."""
        import statistics
        import time
        thd.declare(self.d)
        self.hw.setup()
        try:
            self.publish_everything(4, True)
            snap = self.snapshot(4, True)
            self.publish_rows(4, True, p1_choosing=False)
            self.d.ILCT_HudSnapPublish(ctypes.byref(snap))
            kind = dict(kind=thm.WK_LIST, rows=8, values=True, toggle=True)
            self.d.ILCT_HmPublish(ctypes.byref(thm.menu_view({s: thm.seat_view(member=s, **kind) for s in range(4)})))
            out = th.Frame()
            before, after = thm.Counters(), thm.Counters()
            self.d.ILCT_HmStats(ctypes.byref(before))
            self.assertEqual(self.d.ILCT_HudDrvCompose(EPOCH, NOW, None, ctypes.byref(out)), thd.COMPOSE_OK)
            self.d.ILCT_HmStats(ctypes.byref(after))
            self.assertEqual(after.drawn_windows - before.drawn_windows, 4, 'every window drawn')
            self.assertEqual(after.dropped - before.dropped, 0, 'no window dropped for room')
            squeezed = after.squeezed - before.squeezed
            self.assertLessEqual(out.count, th.MAX_COMMANDS)
            self.assertLessEqual(out.text_used, th.TEXT_POOL)
            ring = self.d.ILCT_HudRingEstimate(ctypes.byref(out))
            self.assertLess(ring, th.RING_OURS_MAX // 2, 'under half of the canvas share of the ring')
            times = []
            for _ in range(300):
                t0 = time.perf_counter()
                self.d.ILCT_HudDrvCompose(EPOCH, NOW, None, ctypes.byref(out))
                times.append(time.perf_counter() - t0)
            median_ms = statistics.median(times) * 1000
            print('\n' f'worst frame: {out.count}/{th.MAX_COMMANDS} commands, {out.text_used}/{th.TEXT_POOL} text bytes, '
                  f'ring estimate {ring} bytes ({100 * ring / th.RING_OURS_MAX:.1f}% of the share), '
                  f'host compose median {median_ms:.3f} ms, {squeezed} window(s) squeezed to fewer rows')
            self.assertLess(median_ms, 0.35)
        finally:
            self.d.ILCT_HudDrvReset()
            self.d.ILCT_HmWithdraw()
            self.hw.twin.reset()


if __name__ == '__main__':
    unittest.main()
