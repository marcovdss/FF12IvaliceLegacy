"""Host tests for the co-op HUD on our overlay (native/coop/src/overlay/hud_scene, hide, feed, states): the party list,
Target Info and the battle log drawn from their inputs (the Wait line was removed, Marco 2026-11-16); the vanilla pieces they replace hidden by the
window draw bit and given back; the HUD feed's rules; the Legacy script states (PUSH_HUD_STATES: validation, resolve,
tags); the seat colours of the world marks (palettes and gem tints); the Wait engine's freeze view; and a WARP render of
the whole HUD. No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME) for the image twin.

* the party list: bottom-anchored rows for 1..4 members, right of the seat slots (clear of P4's window in both party
  sizes), inside the screen; the action line names the action only, never its target (Marco, 2026-11-16); Choosing...
  while that seat's menu is open; the action gauge only with a known value; low HP glows red; the weapon word only
  while live; rolled back whole when it does not fit its budget or the batch;
* Target Info: top-left, dropped by the boss gauge, the seat edge in the seat's colour, the Libra / Anti-Libra badge,
  the danger red name, the Legacy states line only with a live states record;
* the battle log: top-right inside the log zone, the header and the chain, the seat-coloured dots, a proc tag;
* no Wait line: compose draws three elements, and nothing in the top-centre;
* hide: may_hide / may_give_back / field_ui / wanted (the rules), then on the twin: the party panel hidden and given
  back, an address reused by another window never touched, the help line only while P1's menu is ours, the stale and
  exit give-backs, nothing with the pins off or the gate shut;
* the feed: the action gauge as the native row fills it;
* the states: tag and word names, when a word is live, resolve through the record map, proc tags by time, and the bridge
  op's validation (lengths, ranges, unused entries, crowned without an element);
* the seat colours: every seat its own line palette, pink and bronze the nearest free one, never the gambit white;
  pink and bronze gems tinted;
* the Wait freeze view after each engine decision;
* WARP: the full combat HUD and the exploration HUD at 1920x1080 (ILC_OVERLAY_PREVIEW writes them as PNG).
"""
from __future__ import annotations

import ctypes
import os
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_hud_battle_log as tbl
import test_coop_hud_driver as thd
import test_coop_hud_menus as thm
import test_coop_hud_party_rows as tpr
import test_coop_hud_target_info as tti
import test_coop_overlay as tov
from coop_native_support import native_test
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME

u8, u16, u32, u64, i32, f32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32, ctypes.c_float
P = ctypes.POINTER
S = tov.S
Quad = tov.Quad
EPOCH, NOW = thd.EPOCH, thd.NOW
PREVIEW = os.environ.get('ILC_OVERLAY_PREVIEW')
QCAP = 6144

FeedRow = S('FeedRow', [('member', u16), ('action', u16), ('mp', ctypes.c_int16), ('mp_max', ctypes.c_int16),
                        ('flags', u8), ('gauge', u8), ('reserved', u8 * 2), ('ghost', u32),
                        ('name', ctypes.c_char * 24), ('action_name', ctypes.c_char * 24)])
FeedRecord = S('FeedRecord', [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64), ('row_count', u8),
                              ('reserved_w', u8 * 2), ('chain_level', u8), ('chain', u16),
                              ('chain_known', u8), ('reserved', u8), ('rows', FeedRow * 4)])
Foe = S('Foe', [('handle', u32), ('flags', u8), ('broken_s', u8), ('dent_pct', u8), ('tired', u8), ('spotter_seat', u8),
                ('crown_element', u8), ('reserved', u8 * 2)])
Word = S('Word', [('member', u16), ('kind', u8), ('value', u8)])
Proc = S('Proc', [('handle', u32), ('tag', u8), ('reserved', u8 * 3), ('at_ms', u64)])
StatesRecord = S('StatesRecord', [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64),
                                  ('pushed_ms', u64), ('foe_count', u8), ('word_count', u8), ('proc_count', u8),
                                  ('reserved', u8 * 5), ('foes', Foe * 8), ('words', Word * 4), ('procs', Proc * 8)])
Header = S('Header', [('magic', u32), ('abi_version', u16), ('record_type', u16), ('size', u32), ('generation', u32)])
HudFoe = S('HudFoe', [('record', u64), ('flags', u8), ('broken_s', u8), ('dent_pct', u8), ('tired', u8),
                      ('spotter_seat', u8), ('crown_element', u8), ('reserved', u8 * 2)])
HudWord = S('HudWord', [('member', u16), ('kind', u8), ('value', u8)])
HudProc = S('HudProc', [('record', u64), ('tag', u8), ('reserved', u8 * 3), ('age_ms', u32)])
HudStates = S('HudStates', [('header', Header), ('foe_count', u8), ('word_count', u8), ('proc_count', u8),
                            ('reserved', u8 * 5), ('foes', HudFoe * 8), ('words', HudWord * 4), ('procs', HudProc * 8)])
Output = S('Output', [('drawn', u32), ('refused', u32), ('quads', u32 * 3)])
WantIn = S('WantIn', [('now', u64), ('live_ms', u64), ('coop_on', u8), ('render_live', u8), ('pins_ok', u8),
                      ('field', u8), ('p1_ours', u8), ('leader_vanilla', u8), ('summon', u8), ('party_on', u8),
                      ('target_on', u8), ('log_on', u8), ('reserved', u8 * 6)])
HideStatus = S('HideStatus', [('held', u32), ('wanted', u32), ('pins', u8), ('reserved', u8 * 3), ('pin_row', i32)])
HideCounters = S('HideCounters', [('hides', u64 * 7), ('give_backs', u64 * 7), ('dropped', u64 * 7), ('pin_checks', u64),
                                  ('pin_off', u64), ('exit_restores', u64), ('kept_unreadable', u64),
                                  ('orphan_restores', u64), ('watch_deferred', u64),
                                  ('core_fault_restores', u64)])
FreezeView = S('FreezeView', [('frozen', u8), ('p1_stock', u8), ('reserved', u8 * 6), ('at_ms', u64)])
EngineIn = S('EngineIn', [(n, u8) for n in ('owned', 'readable', 'held', 'requested', 'live', 'active_mode', 'in_play',
                                            'frontend_on')] + [('frontend_reason', ctypes.c_int8), ('reserved', u8 * 3)])
EngineOut = S('EngineOut', [(n, u8) for n in ('action', 'owned', 'wanted', 'revalidated', 'borrowed')] +
              [('reserved', u8 * 3)])

VP = ctypes.c_void_p
SIG = {
    'ILCT_OhSizes': ([P(u32), u32], u32),
    'ILCT_HsCompose': ([u32, u32, VP, VP, VP, VP, VP, VP, VP, u64, u32, P(Quad), u32, P(Output), u32], u32),
    'ILCT_HsRowTop': ([u32, u32], f32), 'ILCT_HsConstants': ([P(f32)], None), 'ILCT_HsSeatRgba': ([VP, u32], u32),
    'ILCT_HideWanted': ([P(WantIn)], u32), 'ILCT_HideMayHide': ([u64, u64, u32], i32),
    'ILCT_HideMayGiveBack': ([u64, u64, u32], i32), 'ILCT_HideFieldUi': ([u32, u32], i32),
    'ILCT_HideReset': ([], None), 'ILCT_HideSetBase': ([u64], None), 'ILCT_HideForcePins': ([i32], None),
    'ILCT_HideForceGate': ([i32], None), 'ILCT_HideForceP1': ([i32], None), 'ILCT_HideNote': ([u32, u64], None),
    'ILCT_HideForceLive': ([i32], None), 'ILCT_HideForceElements': ([i32], None),
    'ILCT_HideTick': ([i32], None), 'ILCT_HideStale': ([], None), 'ILCT_HideExit': ([], None),
    'ILCT_HideSample': ([u64], None), 'ILCT_HideStatus': ([P(HideStatus)], None),
    'ILCT_HideWatch': ([u64], None), 'ILCT_HideSetTickMs': ([u64], None),
    'ILCT_HideSetSignals': ([i32, u64, u64], None), 'ILCT_OvLastFrameMs': ([], u64),
    'ILCT_HideStats': ([P(HideCounters)], None),
    'ILCT_FeedGauge': ([i32, u32, u32, i32, i32], u32),
    'ILCT_StTagName': ([u32], ctypes.c_char_p), 'ILCT_StWordLabel': ([u32], ctypes.c_char_p),
    'ILCT_StWordLive': ([u32, u32], i32), 'ILCT_StProcFor': ([VP, u32, u64, u64], u32),
    'ILCT_StMap': ([P(u64), P(u32), u32], None), 'ILCT_StResolve': ([P(HudStates), u64, P(StatesRecord)], i32),
    'ILCT_StReset': ([], None), 'ILCT_StPushes': ([], u64),
    'ILCT_WmLineCluts': ([P(u8), P(u8)], None), 'ILCT_WmGemTint': ([u32, P(u32)], i32),
    'ILCT_WaitFrozenAfter': ([P(EngineIn), P(EngineOut)], i32),
    'ILC_Bridge': ([u64, VP, u64, VP, u64], i32),
    'ILCT_OvWarpHud': ([VP, VP, VP, VP, VP, VP, VP, u64, u32, u32, P(tov.FrameInfo), P(Output)], i32),
    'ILCT_OvWarpQuads': ([], u32), 'ILCT_HmBuiltinHelp': ([u32], ctypes.c_char_p),
    'ILCT_MqOffset': ([u64, u64, f32, f32, f32, u32], f32),
    'ILCT_HsHpStep': ([u32, u32, u32], u32), 'ILCT_HsDropRare': ([u32, u32], i32),
    'ILCT_MqText': ([ctypes.c_char_p, f32, f32, u64, u64, i32, P(Quad), u32], u32), 'ILCT_MqClipOn': ([], i32),
}

# ABI values (bridge_abi.h)
OP_PUSH_HUD_STATES, REC_HUD_STATES = 10, 12
ABI_MAGIC = 0x42434C49
E_BAD_LENGTH, E_BAD_TYPE, E_BAD_FIELD, E_CONFLICT = -8, -9, -10, -11
FOE_BROKEN, FOE_STAGGERED, FOE_SPOTTED, FOE_KING, FOE_CROWNED = 0x01, 0x02, 0x04, 0x20, 0x40
WORD_GUARD, WORD_CADENCE, WORD_FLOW = 1, 2, 3
TAG_SUNDER, TAG_BREAK = 1, 2
EL_PARTY, EL_TARGET, EL_LOG, EL_ALL = 0, 1, 2, 4
RF_MP, RF_GAMBITS_KNOWN, RF_GAMBITS_ON, RF_ACTING, RF_CHOOSING = 0x01, 0x02, 0x04, 0x08, 0x10
ELEM_PARTY, ELEM_HELP, ELEM_TARGET, ELEM_LOG = range(4)
PIECE_PARTY, PIECE_HELP0, PIECE_HELP1, PIECE_TARGET, PIECE_LOGB, PIECE_LOGA, PIECE_CHAIN = range(7)
ALL_PIECES = 0b0111111   # every piece but the chain counter (not located: a seam, never touched)
FAULT_CORE = 0x40000000   # patch_engine.h: the fault bit of the CORE rows (UT-01 among them)
COLOUR_SAPPHIRE, COLOUR_RUBY, COLOUR_PINK, COLOUR_EMERALD, COLOUR_GOLD, COLOUR_AMETHYST, COLOUR_BRONZE = range(7)
COLOUR_NONE = 0xFF
# The default theme's colours (Bold Modern, src/overlay/themes/legacy-bold/theme.json).
LOW_HP_CORE = tov.rgba(255, 84, 64)        # colors.hp_critical
GOLD = tov.rgba(255, 218, 112)             # colors.gold (Libra, tags)
LOW = tov.rgba(255, 108, 92)               # colors.low (Anti-Libra, the danger name)
SLOT_P4_RIGHT = 24 + 3 * (275 + 16) + 275   # scene.h: P4's window ends here


def declare(d):
    for name, (args, res) in SIG.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def addr(x):
    return ctypes.addressof(x) if x is not None else None


def snapshot(rows=4, seats=(1, 2, 3, 0), hp=(1834, 1402, 300, 1120), hp_max=(2210, 1650, 2480, 1390), leader=0,
             flags=thd.SNAP_WORLD | 0x02 | 0x10):
    s = thd.snapshot(flags=flags)
    s.member_count, s.tick_scale = rows, 1
    for k in range(rows):
        u = s.members[k]
        u.member, u.seat, u.flags, u.hp, u.hp_max = k, seats[k], 0x3 | (0x04 if k == leader else 0), hp[k], hp_max[k]
        u.handle = 0x100001 + k
        u.target = 0x200001 if k < 2 else 0x100001   # rows 0/1 target a foe, rows 2/3 an ally
    return s


def rows_record(colours=(COLOUR_SAPPHIRE, COLOUR_EMERALD, COLOUR_AMETHYST, COLOUR_BRONZE)):
    r = tpr.RowsRecord()
    r.epoch, r.sampled_ms, r.enabled = EPOCH, NOW, 1
    for s, c in enumerate(colours):
        r.seat_colour[s] = c
    return r


def feed_record(rows=4, acting=(1, 0, 1, 0), action_names=(b'', b'Fira', b'Cure', b''), gauge=(70, 255, 35, 255),
                chain=0):
    f = FeedRecord()
    f.epoch, f.built_ms, f.row_count = EPOCH, NOW, rows
    f.chain, f.chain_level, f.chain_known = chain, 1, 1 if chain else 0
    names = (b'Vaan', b'Fran', b'Basch', b'Penelo')
    for k in range(4):
        r = f.rows[k]
        r.member, r.action, r.mp, r.mp_max, r.gauge = (k if k < rows else 0xFFFF), 0xFFFF, 100 + k, 200, gauge[k]
        r.flags = RF_MP | RF_GAMBITS_KNOWN | RF_GAMBITS_ON | (RF_ACTING if acting[k] else 0)
        r.name, r.action_name = names[k], action_names[k]
        if acting[k]:
            r.action = 0x96 if not action_names[k] else 0x10 + k
    return f


def ti_view(flags=0x0001 | 0x0004 | 0x0008 | 0x0020 | 0x0040 | 0x4000 | 0x8000, seat_colour=None, level=21):
    v = tti.View()
    v.epoch, v.flags, v.built_ms, v.handle, v.seat, v.level = EPOCH, flags, NOW, 0x200001, 2, level
    v.seat_colour = thm.seat_rgba(COLOUR_EMERALD) if seat_colour is None else seat_colour
    v.hp, v.hp_max, v.ghost, v.weakness = 6210, 9800, 6700, 0b100
    v.name = b'Wild Saurian'
    v.status_count = 2
    v.status_id[0], v.status_flags[0], v.status_secs[0] = 15, 0x01 | 0x04, 12
    v.status_id[1], v.status_flags[1] = 11, 0x01
    v.drop_count = 3
    for k, (kind, text) in enumerate(((0, b'Wyrm Bone'), (0, b'???'), (1, b'Tyrant Bone'))):
        v.drops[k].kind, v.drops[k].text, v.drops[k].state = kind, text, 1 if text == b'???' else 0
    return v


def log_view(lines=5, now=NOW):
    v = tbl.View()
    v.epoch, v.built_ms, v.line_count = EPOCH, now, lines
    for s in range(4):
        v.seat_colour[s] = thm.seat_rgba((0, 3, 5, 6)[s])
    texts = ((1, b'Vaan', b'', b'Wild Saurian', (-212,), 0x04, 0), (3, b'Basch', b'', b'Wild Saurian', (-188, -201), 0x04, 0),
             (6, b'Wild Saurian', b'Tail Hammer', b'Basch', (-512,), 0x02, 0), (4, b'Penelo', b'Cure', b'Basch', (348,), 0, 0),
             (2, b'Fran', b'Fira', b'Hyena B', (-640,), 0, 0x02))
    for k in range(lines):
        dot, actor, body, target, amounts, flags, tags = texts[k % len(texts)]
        ln = v.lines[k]
        ln.id, ln.cls, ln.dot, ln.flags, ln.tags = k + 1, 1, dot, flags, tags
        ln.actor_name, ln.body, ln.target_name = actor, body, target
        ln.actor, ln.target, ln.target_count, ln.count = 0x100001 + k, 0x200001, 1, len(amounts)
        ln.amount_count = len(amounts)
        for j, a in enumerate(amounts):
            ln.amounts[j] = a
        ln.born_ms = ln.touched_ms = now - 100 * (lines - k)
    return v


def states_record(foes=(), words=(), procs=(), built=NOW):
    r = StatesRecord()
    r.epoch, r.built_ms, r.pushed_ms = EPOCH, built, built
    for k, f in enumerate(foes):
        r.foes[k] = f
    for k in range(4):
        r.words[k].member = 0xFFFF
    for k, w in enumerate(words):
        r.words[k] = w
    for k, p in enumerate(procs):
        r.procs[k] = p
    r.foe_count, r.word_count, r.proc_count = len(foes), len(words), len(procs)
    return r


def colours_of(qs) -> set:
    return {q.c[0] for q in qs}


def bounds(qs):
    return (min(q.x0 for q in qs), min(q.y0 for q in qs), max(q.x1 for q in qs), max(q.y1 for q in qs))


@native_test
class HudCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(tov.declare(fresh_test_dll()))
        c = (f32 * 15)()
        cls.d.ILCT_HsConstants(c)
        (cls.party_x, cls.party_w, cls.party_bottom, cls.pitch, cls.row_h, cls.ti_x, cls.ti_top, cls.ti_w, cls.log_x,
         cls.log_right, cls.log_top, cls.boss_dy, *budgets) = list(c)
        cls.budgets = [int(b) for b in budgets]

    def element(self, which, budget=0, snap=None, rows=None, feed=None, menus=None, ti=None, log=None, states=None,
                now=NOW, boss=0, room=0):
        out = (Quad * QCAP)()
        o = Output()
        n = self.d.ILCT_HsCompose(which, budget or 6000, addr(snap), addr(rows), addr(feed), addr(menus), addr(ti),
                                  addr(log), addr(states), now, boss, out, QCAP, ctypes.byref(o), room)
        return list(out[:n]), o


class LayoutTest(HudCase):
    def test_struct_layouts(self):
        sizes = (u32 * 32)()
        n = self.d.ILCT_OhSizes(sizes, 32)
        self.assertEqual(list(sizes[:n]), [ctypes.sizeof(FeedRow), ctypes.sizeof(FeedRecord), ctypes.sizeof(Foe),
                                           ctypes.sizeof(Word), ctypes.sizeof(Proc), ctypes.sizeof(StatesRecord),
                                           ctypes.sizeof(HudStates), ctypes.sizeof(HudFoe), ctypes.sizeof(HudWord),
                                           ctypes.sizeof(HudProc), ctypes.sizeof(Output), ctypes.sizeof(WantIn),
                                           ctypes.sizeof(HideStatus), ctypes.sizeof(FreezeView), 16, 6144])
        self.assertEqual(ctypes.sizeof(HudStates), 296)

    def test_the_budgets_and_the_windows_fit_the_batch(self):
        windows, card = 4 * 200, 1600
        self.assertLessEqual(sum(self.budgets) + windows + card, 6144, 'every element at its budget, with headroom')


class PartyListTest(HudCase):
    def test_rows_are_bottom_anchored_and_clear_of_the_slots(self):
        for rows in (1, 2, 3, 4):
            with self.subTest(rows=rows):
                qs, o = self.element(EL_PARTY, snap=snapshot(rows=rows), rows=rows_record(), feed=feed_record(rows=rows))
                self.assertTrue(o.drawn & 1)
                x0, y0, x1, y1 = tov.visible_bounds(qs)
                self.assertGreater(x0, SLOT_P4_RIGHT + 60, 'right of P4\'s window, whatever the party size')
                self.assertLessEqual(x1, 1920)
                self.assertLessEqual(y1, 1080)
                self.assertAlmostEqual(self.d.ILCT_HsRowTop(rows - 1, rows), self.party_bottom - self.row_h)
                self.assertGreaterEqual(y0, self.party_bottom - self.row_h - (rows - 1) * self.pitch - 8)

    def test_the_action_line_never_names_the_target(self):
        """Marco, 2026-11-16: the action only; the target lines show who targets what. A row whose actor targets an
        ally or a foe draws exactly what it draws with no target at all."""
        s = snapshot()
        with_targets, _ = self.element(EL_PARTY, snap=s, rows=rows_record(), feed=feed_record())
        for k in range(4):
            s.members[k].target = 0
        without, _ = self.element(EL_PARTY, snap=s, rows=rows_record(), feed=feed_record())
        self.assertEqual(len(with_targets), len(without))
        self.assertEqual([(q.x0, q.y0, q.c[0]) for q in with_targets], [(q.x0, q.y0, q.c[0]) for q in without])
        self.assertNotIn('target_name', [f[0] for f in FeedRow._fields_], 'the feed carries no target name')

    def test_choosing_while_the_seats_menu_is_open(self):
        f = feed_record(acting=(0, 0, 0, 0))
        closed, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=f)
        v = tov.view_with({1: thm.seat_view(member=1)})
        open_, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=f, menus=v)
        self.assertGreater(len(open_), len(closed), 'Choosing... on P2\'s row')
        f.rows[0].flags |= RF_CHOOSING
        stock, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=f)
        self.assertGreater(len(stock), len(closed), 'and for P1\'s stock menu (party_rows EXTRA_CHOOSING)')

    def test_the_action_gauge_only_with_a_value(self):
        with_gauge, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record())
        without, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(),
                                  feed=feed_record(gauge=(255, 255, 255, 255)))
        self.assertGreater(len(with_gauge), len(without), 'no source: the name alone, no gauge parts')

    def test_low_hp_glows_red(self):
        qs, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record())
        self.assertIn(LOW_HP_CORE, colours_of(qs), 'Basch at 300 / 2480')
        qs, _ = self.element(EL_PARTY, snap=snapshot(hp=(1834, 1402, 2000, 1120)), rows=rows_record(),
                             feed=feed_record())
        self.assertNotIn(LOW_HP_CORE, colours_of(qs))

    def test_the_weapon_word_only_while_live(self):
        base, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record())
        dead = states_record(words=[Word(0, WORD_GUARD, 1)])
        qs, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record(), states=dead)
        self.assertEqual(len(qs), len(base), 'Guard with one worn step is not shown')
        live = states_record(words=[Word(0, WORD_GUARD, 3), Word(1, WORD_CADENCE, 4), Word(2, WORD_FLOW, 30)])
        qs, _ = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record(), states=live)
        self.assertGreater(len(qs), len(base) + 20)

    def test_rolled_back_whole_when_it_does_not_fit(self):
        qs, o = self.element(EL_PARTY, budget=20, snap=snapshot(), rows=rows_record(), feed=feed_record())
        self.assertEqual((len(qs), o.drawn), (0, 0), 'over its budget: nothing, never half a list')
        qs, o = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record(), room=50)
        self.assertEqual(len(qs), 0, 'a full batch: nothing')

    def test_seat_colours_follow_the_rows_record(self):
        self.assertEqual(self.d.ILCT_HsSeatRgba(addr(rows_record()), 4) & 0xFFFFFF,
                         tov.rgba(206, 128, 50) & 0xFFFFFF, 'bronze as the party palette has it (0x80 scale, doubled)')


class TargetInfoTest(HudCase):
    def test_top_left_with_the_seat_edge_and_the_badge(self):
        qs, o = self.element(EL_TARGET, ti=ti_view())
        self.assertTrue(o.drawn & 2)
        x0, y0, x1, y1 = bounds(qs)
        self.assertLess(x1, 700)
        self.assertLess(y1, 430)
        self.assertGreaterEqual(y0, 30)
        self.assertIn(GOLD, colours_of(qs), 'Libra in gold')
        boss, _ = self.element(EL_TARGET, ti=ti_view(), boss=1)
        self.assertAlmostEqual(bounds(boss)[1] - y0, self.boss_dy, delta=1, msg='the boss gauge moves it down')

    def test_anti_libra_and_danger(self):
        qs, _ = self.element(EL_TARGET, ti=ti_view(flags=0x0001 | 0x0010 | 0x0020 | 0x0080 | 0x8000))
        self.assertIn(LOW, colours_of(qs), 'Anti-Libra and the danger name in the theme\'s red (colors.low)')
        plain, _ = self.element(EL_TARGET, ti=ti_view())
        self.assertNotIn(LOW, colours_of(plain))

    def test_the_states_line_only_with_live_states(self):
        base, _ = self.element(EL_TARGET, ti=ti_view())
        st = states_record(foes=[Foe(0x200001, FOE_BROKEN | FOE_STAGGERED, 7, 25, 2, 0, 0xFF)])
        qs, _ = self.element(EL_TARGET, ti=ti_view(), states=st)
        self.assertGreater(len(qs), len(base) + 30, 'Broken 7 . Staggered . Defense -25% . Tired')
        other = states_record(foes=[Foe(0x200099, FOE_BROKEN, 7, 0, 0, 0, 0xFF)])
        qs, _ = self.element(EL_TARGET, ti=ti_view(), states=other)
        self.assertEqual(len(qs), len(base), 'another foe\'s states are not this one\'s')

    def test_nothing_without_a_shown_view(self):
        qs, o = self.element(EL_TARGET, ti=ti_view(flags=0))
        self.assertEqual((len(qs), o.drawn), (0, 0))


class BattleLogTest(HudCase):
    def test_top_right_in_the_log_zone(self):
        qs, o = self.element(EL_LOG, log=log_view())
        self.assertTrue(o.drawn & 4)
        x0, y0, x1, y1 = bounds(qs)
        self.assertGreaterEqual(x0, 1360 - 100)
        self.assertLessEqual(x1, 1920)
        self.assertLessEqual(y1, 696)

    def test_the_chain_and_a_proc_tag(self):
        base, _ = self.element(EL_LOG, log=log_view(), feed=feed_record())
        chained, _ = self.element(EL_LOG, log=log_view(), feed=feed_record(chain=12))
        self.assertGreater(len(chained), len(base), 'Chain 12  Lv 2 in the header')
        st = states_record(procs=[Proc(0x100001 + 4, TAG_SUNDER, (0, 0, 0), NOW - 50)])
        tagged, _ = self.element(EL_LOG, log=log_view(), feed=feed_record(), states=st)
        self.assertGreater(len(tagged), len(base), 'SUNDER on its actor\'s line')

    def test_nothing_once_every_line_faded(self):
        qs, o = self.element(EL_LOG, log=log_view(), now=NOW + 60000)
        self.assertEqual((len(qs), o.drawn), (0, 0))


class ComposeTest(HudCase):
    def test_all_three_within_their_budgets(self):
        qs, o = self.element(EL_ALL, snap=snapshot(), rows=rows_record(), feed=feed_record(chain=4),
                             ti=ti_view(), log=log_view(), states=states_record(
                                 foes=[Foe(0x200001, FOE_BROKEN, 7, 25, 2, 0, 0xFF)], words=[Word(0, WORD_GUARD, 3)]))
        self.assertEqual(o.drawn, 0b111)
        self.assertEqual(o.refused, 0)
        self.assertEqual(len(self.budgets), 3, 'party, Target Info, log: no Wait budget')
        for el in range(3):
            self.assertLessEqual(o.quads[el], self.budgets[el], el)
        self.assertEqual(sum(o.quads), len(qs))


# ---- hide: the rules --------------------------------------------------------------------------------------------------------
class HideRuleTest(HudCase):
    def test_may_hide_and_give_back(self):
        d = self.d
        self.assertEqual(d.ILCT_HideMayHide(0x29BE50, 0x29BE50, 0x82F), 1)
        self.assertEqual(d.ILCT_HideMayHide(0x29BE50, 0x29BE50, 0x82E), 0, 'already hidden')
        self.assertEqual(d.ILCT_HideMayHide(0x29BE50, 0x29BE50, 0x82F | 0x400), 0, 'being destroyed')
        self.assertEqual(d.ILCT_HideMayHide(0x123, 0x29BE50, 0x82F), 0, 'another window')
        self.assertEqual(d.ILCT_HideMayGiveBack(0x29BE50, 0x29BE50, 0x2282E), 1, 'the game set 0x2000 meanwhile: fine')
        self.assertEqual(d.ILCT_HideMayGiveBack(0x29BE50, 0x29BE50, 0x82C), 0, 'the game withdrew its show request')
        self.assertEqual(d.ILCT_HideMayGiveBack(0x29BE50, 0x29BE50, 0x82E | 0x400), 0)
        self.assertEqual(d.ILCT_HideMayGiveBack(0x2BF690, 0x29BE50, 0x82E), 0, 'the address now holds another window')

    def test_the_field_gate(self):
        d = self.d
        self.assertEqual(d.ILCT_HideFieldUi(0, 0), 1)
        for fid in (1, 3, 4, 0x8001):
            self.assertEqual(d.ILCT_HideFieldUi(1, fid), 1, fid)
        for fid in (2, 0xD, 0x23):
            self.assertEqual(d.ILCT_HideFieldUi(1, fid), 0, fid)

    def test_wanted(self):
        """0.1.60 item 7: one switch (overlay_live) with LIVE_HOLD_MS of hysteresis; every piece while live."""
        d = self.d
        w = WantIn(now=NOW, live_ms=NOW - 50, coop_on=1, render_live=1, pins_ok=1, field=1, p1_ours=1, party_on=1,
                   target_on=1, log_on=1)
        everything = ALL_PIECES | (1 << PIECE_CHAIN)
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), everything, 'the chain bit is wanted; nothing touches it')
        w.p1_ours = 0
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), everything & ~0b110,
                         'P1 held on the vanilla command menu: its help line stays; the stock TARGET INFO is hidden')
        w.p1_ours = 1
        w.field = 0
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), everything & ~0b110,
                         'a menu opened: the main menu help (the same window) is given back at once')
        w.field = 1
        w.leader_vanilla = 1
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)) & (1 << PIECE_PARTY), 0,
                         'P1\'s vanilla leader select: the party panel stays (review 10)')
        w.leader_vanilla = 0
        w.summon = 1
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)) & (1 << PIECE_PARTY), 0,
                         'an Esper out: the party panel stays (review 12: no Mist pips or Esper row of ours yet)')
        w.summon = 0
        for flag, bits in (('party_on', 1 << PIECE_PARTY), ('target_on', 1 << PIECE_TARGET),
                           ('log_on', (1 << PIECE_LOGA) | (1 << PIECE_LOGB) | (1 << PIECE_CHAIN))):
            v = WantIn.from_buffer_copy(w)
            setattr(v, flag, 0)
            self.assertEqual(d.ILCT_HideWanted(ctypes.byref(v)), everything & ~bits, f'{flag} off: its vanilla piece stays')
        w.live_ms = NOW - 250
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), everything, 'within the 250 ms hysteresis')
        w.live_ms = NOW - 251
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), 0, 'the fail-safe: not live for over 250 ms')
        w.live_ms = 0
        self.assertEqual(d.ILCT_HideWanted(ctypes.byref(w)), 0, 'never live')
        w.live_ms = NOW
        for field in ('coop_on', 'render_live', 'pins_ok'):
            v = WantIn.from_buffer_copy(w)
            setattr(v, field, 0)
            self.assertEqual(d.ILCT_HideWanted(ctypes.byref(v)), 0, field)


# ---- hide: on the twin ------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HideTickTest(unittest.TestCase):
    UI_MANAGER, COMMAND_ACTOR, COMMAND_FLAG = 0x209BE80, 0x2092730, 0x2092734
    HUD = 0x8FA0

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(tov.declare(cls.hw.d))

    def setUp(self):
        d = self.d
        self.hw.setup()
        d.ILCT_SetFixedClock.argtypes = [u64]
        d.ILCT_SetFixedClock(NOW)
        d.ILCT_HideReset()
        d.ILCT_HideSetBase(th.TWIN_BASE)
        d.ILCT_HideForcePins(1)
        d.ILCT_HideForceGate(1)
        d.ILCT_HideForceP1(1)
        d.ILCT_HideForceLive(0)
        d.ILCT_HideForceElements(7)
        a = ctypes.addressof
        self.mgr = ctypes.create_string_buffer(0x11000)
        self.panel = ctypes.create_string_buffer(0x100)
        self.ti = ctypes.create_string_buffer(0x300)
        self.logb = ctypes.create_string_buffer(0x100)
        struct.pack_into('<Q', self.mgr, self.HUD, 0x2BE490)
        struct.pack_into('<Q', self.mgr, 0x10F78, a(self.panel))
        struct.pack_into('<QQ', self.panel, 0, 0x29BE50, 0)
        struct.pack_into('<I', self.panel, 0x40, 0x2082F)
        for slot in (0x0C0, 0x5C0):
            struct.pack_into('<Q', self.mgr, self.HUD + slot, 0x2BF690)
            struct.pack_into('<I', self.mgr, self.HUD + slot + 0x40, 0x2482F)
        struct.pack_into('<Q', self.mgr, self.HUD + 0x1030, a(self.ti))
        struct.pack_into('<Q', self.ti, 0, 0x2C0870)
        struct.pack_into('<I', self.ti, 0x40, 0x482F)
        struct.pack_into('<Q', self.mgr, self.HUD + 0x1078, a(self.logb))
        struct.pack_into('<Q', self.logb, 0, 0x2C0B40)
        struct.pack_into('<I', self.logb, 0x40, 0x82F)
        self.loga = ctypes.create_string_buffer(0x100)
        struct.pack_into('<Q', self.mgr, self.HUD + 0x1080, a(self.loga))
        struct.pack_into('<Q', self.loga, 0, 0x2C0BE0)
        struct.pack_into('<I', self.loga, 0x40, 0x82F)
        tw = self.hw.twin
        tw.poke(self.UI_MANAGER, struct.pack('<Q', a(self.mgr)))
        tw.poke(self.COMMAND_ACTOR, struct.pack('<I', 0))
        tw.poke(self.COMMAND_FLAG, struct.pack('<I', 0))

    def tearDown(self):
        self.d.ILCT_HideReset()
        self.hw.twin.reset()

    def flags(self, buf, off=0x40) -> int:
        return struct.unpack_from('<I', buf, off)[0]

    def present(self, *elements, at=NOW):
        """overlay_live (0.1.60: the pieces follow one switch, not each element's own stamp)."""
        for e in elements:
            self.d.ILCT_HideNote(e, at)
        self.d.ILCT_HideForceLive(1)

    def not_live(self, at):
        """overlay_live false from now; `at` past the 250 ms hysteresis gives everything back."""
        self.d.ILCT_HideForceLive(0)
        self.d.ILCT_SetFixedClock(at)

    def status(self) -> HideStatus:
        s = HideStatus()
        self.d.ILCT_HideStatus(ctypes.byref(s))
        return s

    def test_every_piece_hidden_while_live_and_given_back_after(self):
        d = self.d
        self.present()
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082E)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482E)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x5C0 + 0x40), 0x2482E)
        self.assertEqual(self.flags(self.ti), 0x482E)
        self.assertEqual(self.flags(self.logb), 0x82E)
        self.assertEqual(self.flags(self.loga), 0x82E, 'the first log window (item 4): its backing quad with it')
        self.assertEqual(self.status().held, ALL_PIECES)
        struct.pack_into('<I', self.panel, 0x40, 0x2282E)   # the game set 0x2000 while hidden (seen live)
        self.not_live(NOW + 250)
        d.ILCT_HideTick(1)
        self.assertEqual(self.status().held, ALL_PIECES, 'within the hysteresis: still hidden')
        d.ILCT_SetFixedClock(NOW + 251)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2282F, 'given back once not live for over 250 ms')
        self.assertEqual((self.flags(self.logb), self.flags(self.loga), self.flags(self.ti)), (0x82F, 0x82F, 0x482F))
        self.assertEqual(self.status().held, 0)

    def test_nothing_hidden_before_the_overlay_was_live(self):
        d = self.d
        d.ILCT_HideTick(1)
        self.assertEqual(self.status().held, 0)
        self.assertEqual(self.flags(self.panel), 0x2082F)

    def test_an_unreadable_read_keeps_the_piece_held(self):
        """Review 7: a failed read writes nothing and forgets nothing; the give-back happens once it reads again."""
        d = self.d
        self.present(ELEM_PARTY)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082E)
        tw = self.hw.twin
        tw.poke(self.UI_MANAGER, struct.pack('<Q', 0x10))      # the manager pointer unreadable for a moment
        self.not_live(NOW + 300)
        d.ILCT_HideTick(1)
        self.assertEqual(self.status().held & 1, 1, 'still held')
        self.assertEqual(self.flags(self.panel), 0x2082E, 'nothing written')
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertGreater(c.kept_unreadable, 0)
        self.assertEqual(c.dropped[PIECE_PARTY], 0)
        tw.poke(self.UI_MANAGER, struct.pack('<Q', ctypes.addressof(self.mgr)))
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082F, 'given back once it reads again')

    def test_a_held_window_that_is_no_longer_the_pieces_is_not_given_back(self):
        """Review 6: the give-back writes only to the window the piece has now."""
        d = self.d
        self.present(ELEM_PARTY)
        d.ILCT_HideTick(1)
        other = ctypes.create_string_buffer(0x100)
        struct.pack_into('<QQ', other, 0, 0x29BE50, 0)
        struct.pack_into('<I', other, 0x40, 0x2082F)
        struct.pack_into('<Q', self.mgr, 0x10F78, ctypes.addressof(other))   # a new panel; the old one still readable
        self.not_live(NOW + 300)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082E, "the old panel is not the piece's any more: untouched")
        self.assertEqual(self.status().held & 1, 0)

    def test_the_watch_gives_back_when_the_game_thread_stops(self):
        """Review 9: a stalled game thread runs no UT-01 and presents no frame; the co-op beat gives back."""
        d = self.d
        d.ILCT_HideSetSignals(1, 0, 0)   # UT-01 and the frame hook silent (never ran)
        self.present(ELEM_PARTY, ELEM_LOG)
        d.ILCT_HideTick(1)
        self.assertEqual(self.status().held, ALL_PIECES)
        d.ILCT_HideWatch(NOW + 500)
        self.assertEqual(self.status().held, ALL_PIECES, 'a tick 500 ms ago: still running, nothing done')
        struct.pack_into('<I', self.panel, 0x40, 0x2282E)   # the game wrote another bit meanwhile
        d.ILCT_HideWatch(NOW + 1500)
        self.assertEqual(self.status().held, 0, 'given back once')
        self.assertEqual(self.flags(self.panel), 0x2282F, "bit 0 OR-ed in, the game's bit kept")
        self.assertEqual(self.flags(self.logb), 0x82F)
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual((c.orphan_restores, c.watch_deferred), (1, 0))

    def test_the_watch_writes_nothing_while_the_game_runs_on(self):
        """Review 8: our tick silent is not enough (a closed gate stops it while the game thread runs and owns the
        windows): with UT-01 or the frame hook still recent and no CORE fault latched, nothing is written from the co-op
        thread, and the pieces stay held for UT-01's stale handler or the next tick. Both silent past 1 s: given back.
        (A latched CORE fault is the other case: test_a_core_fault_gives_the_pieces_back.)"""
        d = self.d
        self.present(ELEM_PARTY, ELEM_LOG)
        d.ILCT_HideTick(1)
        later = NOW + 1500
        for label, core, frame in (('UT-01 ran 100 ms ago', later - 100, 0), ('a frame 50 ms ago', 0, later - 50),
                                   ('both recent', later - 10, later - 10)):
            with self.subTest(label):
                d.ILCT_HideSetSignals(1, core, frame)
                d.ILCT_HideWatch(later)
                self.assertEqual(self.status().held, ALL_PIECES, 'still held')
                self.assertEqual((self.flags(self.panel), self.flags(self.logb)), (0x2082E, 0x82E), 'nothing written')
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual((c.orphan_restores, c.watch_deferred), (0, 3))
        d.ILCT_HideSetSignals(1, later - 1000, later - 1200)   # both silent for the threshold
        d.ILCT_HideWatch(later)
        self.assertEqual(self.status().held, 0)
        self.assertEqual((self.flags(self.panel), self.flags(self.logb)), (0x2082F, 0x82F))
        # The give-back the game thread does itself is unchanged: UT-01's stale handler.
        self.setUp()
        self.present(ELEM_PARTY)
        d.ILCT_HideTick(1)
        d.ILCT_HideSetSignals(1, NOW + 1400, NOW + 1400)
        d.ILCT_HideWatch(NOW + 1500)
        self.assertEqual(self.status().held & 1, 1)
        d.ILCT_HideStale()
        self.assertEqual(self.flags(self.panel), 0x2082F)

    def test_a_core_fault_gives_the_pieces_back(self):
        """A latched CORE fault: UT-01 never runs again (neither its handler nor its stale handler: the dispatcher's
        fault check comes before the stale test) while RENDER stays live, so the frame signal stays fresh. The watch
        gives the held pieces back once, with the interlocked OR (the game's other bits kept), only on a window still
        the piece's with the held handler and flags; a window the game closed meanwhile is dropped, not touched."""
        d = self.d
        d.ILCT_PatchFault.argtypes = [u32]
        self.present(ELEM_PARTY, ELEM_TARGET, ELEM_LOG)
        d.ILCT_HideTick(1)
        self.assertEqual(self.status().held, ALL_PIECES)
        later = NOW + 1500
        d.ILCT_HideSetSignals(1, later - 1000, later - 10)   # UT-01 silent, frames still presented (RENDER live)
        d.ILCT_HideWatch(later)
        self.assertEqual(self.status().held, ALL_PIECES, 'no CORE fault: the game runs on, nothing written (control)')
        d.ILCT_PatchFault(FAULT_CORE)
        struct.pack_into('<I', self.panel, 0x40, 0x2282E)    # the game wrote another bit meanwhile
        struct.pack_into('<I', self.ti, 0x40, 0x482C)        # the game closed TARGET INFO meanwhile (bit 1 off)
        d.ILCT_HideWatch(later + 10)
        self.assertEqual(self.status().held, 0, 'every held piece given back or dropped')
        self.assertEqual(self.flags(self.panel), 0x2282F, "bit 0 OR-ed in, the game's bit kept")
        self.assertEqual(self.flags(self.logb), 0x82F)
        self.assertEqual(self.flags(self.ti), 0x482C, 'closed by the game: never shown again by us')
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual((c.core_fault_restores, c.orphan_restores, c.watch_deferred), (1, 0, 1))
        self.assertEqual((c.give_backs[PIECE_PARTY], c.give_backs[PIECE_LOGB], c.dropped[PIECE_TARGET]), (1, 1, 1))
        d.ILCT_HideWatch(later + 2000)
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual(c.core_fault_restores, 1, 'once: nothing held, nothing more to do')

    def test_the_frame_hook_stamps_the_watchs_evidence(self):
        """The live wiring of the frame signal: the frame hook stamps the module clock first thing in its handler,
        after the dispatcher's gate (OV-01's gate byte, LIVE, RENDER not faulted, a fresh beat) and before the
        handler's own checks."""
        d = self.d
        d.ILCT_OvReset()
        self.assertEqual(d.ILCT_OvLastFrameMs(), 0)
        d.ILCT_SetFixedClock(NOW + 777)
        d.ILCT_OvCall(None, 1)
        self.assertEqual(d.ILCT_OvLastFrameMs(), NOW + 777)

    def test_the_exit_restore_owns_the_pieces_before_it_looks(self):
        """Review 7 (source order): restore_at_exit waits for an in-flight tick (own_wait) before it reads the held
        mask, so a piece that tick hides is not missed."""
        src = (Path(__file__).resolve().parents[1] / 'native' / 'coop' / 'src' / 'overlay' / 'hide.cpp').read_text(
            encoding='utf-8')
        body = src[src.index('void restore_at_exit()'):src.index('bool stale_for(')]
        self.assertLess(body.index('own_wait(200)'), body.index('g_held_mask'))
        self.assertIn('own_release();\n        return;', body, 'nothing held: the ownership is released')

    def test_a_reused_address_is_never_touched(self):
        d = self.d
        self.present(ELEM_PARTY)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082E)
        struct.pack_into('<Q', self.panel, 0, 0x2BF690)      # destroyed; the address now holds another window
        struct.pack_into('<I', self.panel, 0x40, 0x82E)
        d.ILCT_HideTick(1)
        d.ILCT_SetFixedClock(NOW + 200)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x82E, 'the new window\'s bit is the game\'s')
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual(c.dropped[PIECE_PARTY], 1)

    def test_a_closed_window_is_not_shown_again(self):
        d = self.d
        self.present(ELEM_PARTY)
        d.ILCT_HideTick(1)
        struct.pack_into('<I', self.panel, 0x40, 0x2082C)    # the game withdrew its show request while hidden
        d.ILCT_SetFixedClock(NOW + 200)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082C)

    def test_the_help_line_only_while_p1s_menu_is_ours(self):
        d = self.d
        d.ILCT_HideForceP1(0)
        self.present(ELEM_HELP)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482F, 'P1 vanilla: the stock help is P1\'s')
        self.assertEqual(self.flags(self.ti), 0x482E, 'the stock TARGET INFO is hidden all the same (item 7)')
        d.ILCT_HideForceP1(1)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482E)

    def test_the_main_menu_gives_everything_back(self):
        """The main menu shares the help window: given back the tick the menu opens; the rest once overlay_live (which
        the open menu turns off) has been false for over 250 ms. P1's own battle menu is still the field."""
        d = self.d
        self.present(ELEM_PARTY, ELEM_LOG)
        d.ILCT_HideTick(1)
        self.hw.twin.poke(self.COMMAND_FLAG, struct.pack('<I', 1))
        self.hw.twin.poke(self.COMMAND_ACTOR, struct.pack('<I', 0xD))
        self.not_live(NOW + 16)
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482F, 'the help: at once')
        self.assertEqual(self.flags(self.panel), 0x2082E, 'the HUD pieces: the hysteresis')
        d.ILCT_SetFixedClock(NOW + 300)
        d.ILCT_HideTick(1)
        self.assertEqual((self.flags(self.panel), self.flags(self.logb), self.flags(self.loga)), (0x2082F, 0x82F, 0x82F))
        self.hw.twin.poke(self.COMMAND_ACTOR, struct.pack('<I', 0x8001))   # P1's own battle menu: still the field
        self.present()
        d.ILCT_HideTick(1)
        self.assertEqual(self.flags(self.panel), 0x2082E)
        self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482E)

    def test_the_pause_screen_gives_the_help_back(self):
        d = self.d
        self.present()
        d.ILCT_HideTick(1)
        self.hw.twin.poke(0x209AD28, struct.pack('<Q', 0x1234))
        try:
            d.ILCT_HideTick(1)
            self.assertEqual(self.flags(self.mgr, self.HUD + 0x0C0 + 0x40), 0x2482F)
        finally:
            self.hw.twin.poke(0x209AD28, struct.pack('<Q', 0))

    def test_the_chain_counter_is_a_seam_never_touched(self):
        d = self.d
        self.present()
        d.ILCT_HideTick(1)
        s = self.status()
        self.assertTrue(s.wanted & (1 << PIECE_CHAIN), 'wanted while live')
        self.assertFalse(s.held & (1 << PIECE_CHAIN), 'no window known (CE-4): never held')
        c = HideCounters()
        d.ILCT_HideStats(ctypes.byref(c))
        self.assertEqual((c.hides[PIECE_CHAIN], c.dropped[PIECE_CHAIN]), (0, 0))

    def test_stale_exit_pins_and_the_gate(self):
        d = self.d
        for name, act in (('stale', lambda: d.ILCT_HideStale()), ('exit', lambda: d.ILCT_HideExit()),
                          ('not live', lambda: d.ILCT_HideTick(0)),
                          ('gate shut', lambda: (d.ILCT_HideForceGate(0), d.ILCT_HideTick(1))),
                          ('pins off', lambda: (d.ILCT_HideForcePins(0), d.ILCT_HideTick(1)))):
            with self.subTest(name):
                self.setUp()
                self.present(ELEM_PARTY, ELEM_TARGET)
                d.ILCT_HideTick(1)
                self.assertEqual(self.flags(self.panel), 0x2082E)
                act()
                self.assertEqual((self.flags(self.panel), self.flags(self.ti), self.flags(self.loga)),
                                 (0x2082F, 0x482F, 0x82F), name)

    def test_the_pins_are_the_exes(self):
        d = self.d
        d.ILCT_HideForcePins(-1)
        d.ILCT_HideSample(NOW)
        s = self.status()
        self.assertEqual((s.pins, s.pin_row), (1, -1), 'HH-V1..HH-V7 stock on the twin')


# ---- the feed ---------------------------------------------------------------------------------------------------------------
class FeedTest(HudCase):
    def test_the_action_gauge_as_the_native_row(self):
        d = self.d
        self.assertEqual(d.ILCT_FeedGauge(0, 0x96, 4, 50, 100), 0xFF, 'no action set')
        self.assertEqual(d.ILCT_FeedGauge(1, 0x21F, 4, 50, 100), 0xFF, 'not a battle action')
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 0, 50, 100), 0xFF, 'no state')
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 2, 50, 100), 0, 'pre-charge')
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 4, 35, 100), 35)
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 4, 700, 1000), 70)
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 4, 50, 0), 50, 'need 0 counts as 100')
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 8, 200, 100), 100)
        self.assertEqual(d.ILCT_FeedGauge(1, 0x96, 9, 0, 100), 100, 'executing')


# ---- the Legacy script states -----------------------------------------------------------------------------------------------
class StatesTest(HudCase):
    def test_names_and_live_words(self):
        d = self.d
        self.assertEqual(d.ILCT_StTagName(TAG_SUNDER), b'SUNDER')
        self.assertEqual(d.ILCT_StTagName(99), b'')
        self.assertEqual([d.ILCT_StWordLabel(k) for k in (1, 2, 3)], [b'Guard', b'Cadence', b'Flow'])
        self.assertEqual([d.ILCT_StWordLive(WORD_GUARD, v) for v in (1, 2)], [0, 1])
        self.assertEqual([d.ILCT_StWordLive(WORD_CADENCE, v) for v in (0, 1)], [0, 1])
        self.assertEqual([d.ILCT_StWordLive(WORD_FLOW, v) for v in (9, 10)], [0, 1])

    def test_resolve_through_the_record_map(self):
        d = self.d
        recs = (u64 * 3)(0xAAA0, 0xBBB0, 0xCCC0)
        hands = (u32 * 3)(0x200001, 0x200002, 0x100001)
        d.ILCT_StMap(recs, hands, 3)
        p = HudStates()
        p.foe_count, p.word_count, p.proc_count = 2, 1, 2
        p.foes[0] = HudFoe(0xAAA0, FOE_BROKEN, 7, 25, 2, 0, 255)
        p.foes[1] = HudFoe(0xDEAD, FOE_BROKEN, 3, 0, 0, 0, 255)
        p.words[0] = HudWord(0, WORD_GUARD, 3)
        p.procs[0] = HudProc(0xCCC0, TAG_SUNDER, (0, 0, 0), 300)
        p.procs[1] = HudProc(0xEEEE, TAG_BREAK, (0, 0, 0), 0)
        out = StatesRecord()
        self.assertEqual(d.ILCT_StResolve(ctypes.byref(p), NOW, ctypes.byref(out)), 1)
        self.assertEqual((out.foe_count, out.foes[0].handle, out.foes[0].broken_s), (1, 0x200001, 7),
                         'an unknown record is left out')
        self.assertEqual((out.word_count, out.words[0].member, out.words[0].value), (1, 0, 3))
        self.assertEqual((out.proc_count, out.procs[0].handle, out.procs[0].at_ms), (1, 0x100001, NOW - 300))
        self.assertEqual(d.ILCT_StProcFor(addr(out), 0x100001, NOW - 400, NOW), TAG_SUNDER)
        self.assertEqual(d.ILCT_StProcFor(addr(out), 0x100001, NOW - 100, NOW), TAG_SUNDER, 'within 250 ms of the line')
        self.assertEqual(d.ILCT_StProcFor(addr(out), 0x100001, NOW, NOW), 0, 'fired 300 ms before the line: not its')
        self.assertEqual(d.ILCT_StProcFor(addr(out), 0x100001, NOW + 500, NOW + 500), 0, 'a later line: not its')
        self.assertEqual(d.ILCT_StProcFor(addr(out), 0x100001, NOW - 400, NOW + 3000), 0, 'expired')

    def push(self, p: HudStates, length=None) -> int:
        p.header = Header(ABI_MAGIC, 1, REC_HUD_STATES, ctypes.sizeof(HudStates), 1)
        n = ctypes.sizeof(HudStates) if length is None else length
        out = (u8 * 16)()
        return self.d.ILC_Bridge(OP_PUSH_HUD_STATES, ctypes.addressof(p), n, ctypes.addressof(out), 16)

    def test_the_bridge_op_validates_and_keeps_the_push(self):
        d = self.d
        d.ILCT_StReset()
        good = HudStates()
        good.foe_count, good.word_count, good.proc_count = 1, 1, 1
        good.foes[0] = HudFoe(0xAAA0, FOE_CROWNED | FOE_BROKEN, 7, 25, 2, 1, 3)
        good.words[0] = HudWord(4, WORD_CADENCE, 4)
        good.procs[0] = HudProc(0xAAA0, TAG_SUNDER, (0, 0, 0), 100)
        before = d.ILCT_StPushes()
        self.assertEqual(self.push(good), 0)
        self.assertEqual(d.ILCT_StPushes(), before + 1)

        def variant(fn):
            p = HudStates.from_buffer_copy(good)
            fn(p)
            return p
        cases = {
            'broken over 60 s': (variant(lambda p: setattr(p.foes[0], 'broken_s', 61)), E_BAD_FIELD),
            'dent over 50%': (variant(lambda p: setattr(p.foes[0], 'dent_pct', 51)), E_BAD_FIELD),
            'tired over 4': (variant(lambda p: setattr(p.foes[0], 'tired', 5)), E_BAD_FIELD),
            'a foe without a record': (variant(lambda p: setattr(p.foes[0], 'record', 0)), E_BAD_FIELD),
            'an unused foe not zero': (variant(lambda p: setattr(p.foes[3], 'flags', 1)), E_BAD_FIELD),
            'foe_count over 8': (variant(lambda p: setattr(p, 'foe_count', 9)), E_BAD_FIELD),
            'a word beyond the party ids': (variant(lambda p: setattr(p.words[0], 'member', 40)), E_BAD_FIELD),
            'a word without a kind': (variant(lambda p: setattr(p.words[0], 'kind', 0)), E_BAD_FIELD),
            'a proc without a tag': (variant(lambda p: setattr(p.procs[0], 'tag', 0)), E_BAD_FIELD),
            'a proc older than 5 s': (variant(lambda p: setattr(p.procs[0], 'age_ms', 5001)), E_BAD_FIELD),
            'a reserved byte': (variant(lambda p: p.reserved.__setitem__(0, 1)), E_BAD_FIELD),
            'a crown element 9': (variant(lambda p: setattr(p.foes[0], 'crown_element', 9)), E_BAD_FIELD),
            'crowned without an element': (variant(lambda p: setattr(p.foes[0], 'crown_element', 255)), E_CONFLICT),
        }
        for name, (p, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.push(p), want)
        self.assertEqual(self.push(good, length=ctypes.sizeof(HudStates) - 8), E_BAD_LENGTH)
        bad_type = HudStates.from_buffer_copy(good)
        out = (u8 * 16)()
        bad_type.header = Header(ABI_MAGIC, 1, 3, ctypes.sizeof(HudStates), 1)
        self.assertEqual(d.ILC_Bridge(OP_PUSH_HUD_STATES, ctypes.addressof(bad_type), ctypes.sizeof(HudStates),
                                      ctypes.addressof(out), 16), E_BAD_TYPE)
        self.assertEqual(d.ILCT_StPushes(), before + 1, 'nothing refused was kept')


# ---- the seat colours of the world marks ----------------------------------------------------------------------------------
class SeatColourTest(HudCase):
    def cluts(self, colours):
        c = (u8 * 4)(*colours)
        out = (u8 * 4)()
        self.d.ILCT_WmLineCluts(c, out)
        return list(out)

    def test_every_seat_its_own_palette_never_the_gambit_white(self):
        self.assertEqual(self.cluts([COLOUR_SAPPHIRE, COLOUR_EMERALD, COLOUR_AMETHYST, COLOUR_BRONZE]), [0, 3, 5, 4])
        # The default characters (Vaan sapphire, Ashe pink, Fran amethyst, Balthier bronze): pink cannot take purple.
        self.assertEqual(self.cluts([COLOUR_SAPPHIRE, COLOUR_PINK, COLOUR_AMETHYST, COLOUR_BRONZE]), [0, 1, 5, 4])
        self.assertEqual(self.cluts([COLOUR_PINK, COLOUR_GOLD, COLOUR_BRONZE, COLOUR_NONE]), [5, 4, 1, 0xFF])
        for combo in ([COLOUR_BRONZE] * 4, [COLOUR_PINK] * 4, list(range(4)), [3, 4, 5, 6]):
            self.assertNotIn(6, self.cluts(combo), combo)

    def test_pink_and_bronze_gems_are_tinted(self):
        rgb = (u32 * 3)()
        self.assertEqual(self.d.ILCT_WmGemTint(COLOUR_SAPPHIRE, rgb), 0)
        self.assertEqual(list(rgb), [0x80, 0x80, 0x80])
        self.assertEqual(self.d.ILCT_WmGemTint(COLOUR_PINK, rgb), 1)
        self.assertEqual(rgb[0], 0xFF, 'its strongest channel at full')
        self.assertTrue(rgb[0] > rgb[2] > rgb[1], 'pink: red over blue over green')
        self.assertEqual(self.d.ILCT_WmGemTint(COLOUR_BRONZE, rgb), 1)
        self.assertTrue(rgb[0] > rgb[1] > rgb[2], 'bronze: red over green over blue')


# ---- the Wait engine's freeze view (wait::freeze, unchanged) -----------------------------------------------------------------------------------------
class WaitFreezeTest(HudCase):
    def test_frozen_after_each_decision(self):
        d = self.d
        base = dict(readable=1, active_mode=0, in_play=1)
        for kw, action, want in ((dict(held=1), 0, 1), (dict(held=0), 1, 1), (dict(held=1), 2, 0), (dict(held=0), 0, 0),
                                 (dict(held=1, active_mode=1), 0, 0), (dict(held=1, in_play=0), 0, 0),
                                 (dict(held=1, readable=0), 0, 0)):
            i = EngineIn(**{**base, **kw})
            o = EngineOut(action=action)
            self.assertEqual(d.ILCT_WaitFrozenAfter(ctypes.byref(i), ctypes.byref(o)), want, (kw, action))


# ---- HP warning steps and rare drops (mockup v4, Marco 2026-11-16) ---------------------------------------------------------
HP_NORMAL, HP_WARNING, HP_CRIT, HP_DOWN = range(4)
HP_WARN_C = tov.rgba(255, 222, 64)      # colors.hp_warn (Bold Modern)
DROP_RARE_C = tov.rgba(255, 140, 190)    # colors.drop_rare
ST_KO, ST_HP_CRITICAL, ST_BUBBLE = 1 << 0, 1 << 29, 1 << 28


class ReviewFixTest(HudCase):
    """The phase 1-7 review's fixes on the HUD elements."""

    def test_the_party_list_switched_off_draws_nothing(self):
        """Review 2: off, nothing is drawn (so nothing is stamped and the vanilla panel is never hidden)."""
        qs, o = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record(), boss=2)
        self.assertEqual((len(qs), o.drawn & 1), (0, 0))
        qs, o = self.element(EL_PARTY, snap=snapshot(), rows=rows_record(), feed=feed_record())
        self.assertEqual(o.drawn & 1, 1)

    def test_a_long_expanded_log_keeps_its_newest_lines(self):
        """Review 3: twenty long lines past the budget drop the oldest lines, never the whole element."""
        v = log_view(lines=20)
        v.flags |= tbl.VIEW_EXPANDED
        for k in range(20):
            v.lines[k].touched_ms = v.lines[k].born_ms = NOW - 50
        budget = self.budgets[2]
        qs, o = self.element(EL_LOG, budget=budget, log=v, feed=feed_record(chain=12))
        self.assertEqual(o.drawn & 4, 4, 'the log is drawn (and stamped)')
        self.assertLessEqual(len(qs), budget)
        full, _ = self.element(EL_LOG, budget=6000, log=v, feed=feed_record(chain=12))
        self.assertGreater(len(full), budget, 'the view does not fit the budget whole')
        ys = sorted({round(q.y0) for q in qs if q.mode == tov.QM_SDF and q.y0 > 70 + self.boss_dy * 0})
        self.assertLess(min(ys), 100, 'the kept lines start at the first row (the gap closed)')

    def test_the_log_header_reads_battle_log_left_and_the_chain_right(self):
        """Review 5: as the mockup, "Battle Log" on the left, "Chain 12 . Lv 2" right-aligned at the log's right edge."""
        qs, _ = self.element(EL_LOG, log=log_view(lines=1), feed=feed_record(chain=12))
        head = [q for q in qs if q.mode == tov.QM_SDF and q.y1 < 82]
        self.assertTrue(head)
        self.assertLess(min(q.x0 for q in head), self.log_x + 20, "the title starts at the log's left edge")
        self.assertGreater(max(q.x1 for q in head), self.log_right - 20, 'the chain ends at its right edge')
        no_chain, _ = self.element(EL_LOG, log=log_view(lines=1), feed=feed_record(chain=0))
        head = [q for q in no_chain if q.mode == tov.QM_SDF and q.y1 < 82]
        self.assertLess(max(q.x1 for q in head), self.log_x + 260, 'without a chain only the title, on the left')


class HpStepTest(HudCase):
    def test_the_thresholds(self):
        d = self.d
        self.assertEqual(d.ILCT_HsHpStep(1001, 2000, 0), HP_NORMAL)
        self.assertEqual(d.ILCT_HsHpStep(1000, 2000, 0), HP_WARNING, 'exactly half: warning')
        self.assertEqual(d.ILCT_HsHpStep(401, 2000, 0), HP_WARNING)
        self.assertEqual(d.ILCT_HsHpStep(399, 2000, 0), HP_CRIT, 'below a fifth: the critical test of the game')
        self.assertEqual(d.ILCT_HsHpStep(1500, 2000, ST_HP_CRITICAL), HP_CRIT, 'the HP Critical status itself')
        self.assertEqual(d.ILCT_HsHpStep(700, 2000, 0), HP_WARNING)
        self.assertEqual(d.ILCT_HsHpStep(700, 2000, ST_BUBBLE), HP_CRIT, 'Bubble doubles max for the critical test')
        self.assertEqual(d.ILCT_HsHpStep(0, 2000, 0), HP_DOWN)
        # One effective max (review 13): under Bubble the warning step is half of the doubled max too.
        self.assertEqual(d.ILCT_HsHpStep(1500, 2000, ST_BUBBLE), HP_WARNING, '1500 of 4000')
        self.assertEqual(d.ILCT_HsHpStep(2100, 2000, ST_BUBBLE), HP_NORMAL)
        self.assertEqual(d.ILCT_HsHpStep(500, 2000, ST_KO), HP_DOWN)

    def test_number_and_gauge_take_the_step_colour(self):
        warn, _ = self.element(EL_PARTY, snap=snapshot(hp=(1000, 1402, 2000, 1120)), rows=rows_record(),
                               feed=feed_record())
        self.assertIn(HP_WARN_C, colours_of(warn), 'Vaan at 1000 / 2210: yellow number and fill')
        self.assertNotIn(LOW_HP_CORE, colours_of(warn))
        crit, _ = self.element(EL_PARTY, snap=snapshot(hp=(1834, 1402, 300, 1120)), rows=rows_record(),
                               feed=feed_record())
        self.assertIn(LOW_HP_CORE, colours_of(crit), 'Basch critical: red with the glow')
        calm, _ = self.element(EL_PARTY, snap=snapshot(hp=(2200, 1600, 2400, 1300)), rows=rows_record(),
                               feed=feed_record())
        self.assertNotIn(HP_WARN_C, colours_of(calm))
        self.assertNotIn(LOW_HP_CORE, colours_of(calm))


class RareDropTest(HudCase):
    def test_what_rare_means(self):
        d = self.d
        self.assertEqual(d.ILCT_HsDropRare(0, 0x02), 1, 'a drop flagged rare (the rare and very rare slots 2-3)')
        self.assertEqual(d.ILCT_HsDropRare(0, 0x00), 0, 'a common drop')
        self.assertEqual(d.ILCT_HsDropRare(2, 0x00), 1, 'a special drop (Monograph, Canopic Jar)')
        self.assertEqual(d.ILCT_HsDropRare(1, 0x00), 0, 'a steal')

    def test_the_mark_and_colour_only_on_rare_drops(self):
        v = ti_view()
        plain, _ = self.element(EL_TARGET, ti=v)
        self.assertNotIn(DROP_RARE_C, colours_of(plain))
        v.drops[0].flags = 0x02
        rare, _ = self.element(EL_TARGET, ti=v)
        self.assertIn(DROP_RARE_C, colours_of(rare))
        self.assertGreater(len(rare), len(plain), 'the diamond mark')


# ---- the carousel (Marco, 2026-11-16: long names move from one side to the other) ----------------------------------------
class CarouselTest(HudCase):
    SPEED, HOLD = 40.0, 1000

    def off(self, t, since=0, text_w=300.0, col_w=200.0):
        return self.d.ILCT_MqOffset(t, since, text_w, col_w, self.SPEED, self.HOLD)

    def test_short_text_never_moves(self):
        for t in (0, 500, 1500, 5000, 99999):
            self.assertEqual(self.off(t, text_w=150.0), 0.0)
            self.assertEqual(self.off(t, text_w=200.0), 0.0, 'exactly the column')

    def test_long_text_hits_both_ends_with_holds(self):
        travel, glide = 100.0, 2500   # 100 units at 40 units/s
        self.assertEqual(self.off(0), 0.0)
        self.assertEqual(self.off(999), 0.0, 'holds 1 s at the start')
        self.assertAlmostEqual(self.off(1000 + glide // 2), travel / 2, delta=0.5, msg='glides at 40 units/s')
        self.assertAlmostEqual(self.off(1000 + glide), travel, delta=0.01, msg='reaches the end')
        self.assertAlmostEqual(self.off(1000 + glide + 999), travel, delta=0.01, msg='holds 1 s at the end')
        self.assertAlmostEqual(self.off(2000 + glide + glide // 2), travel / 2, delta=0.5, msg='glides back')
        self.assertEqual(self.off(2 * 1000 + 2 * glide), 0.0, 'and starts over')
        for t in range(0, 20000, 37):
            self.assertTrue(0.0 <= self.off(t) <= travel, t)

    def test_restart_on_focus_change(self):
        self.assertGreater(self.off(3000), 0.0)
        self.assertEqual(self.off(3000, since=2500), 0.0, 'the cursor arrived 0.5 s ago: still holding the start')
        self.assertEqual(self.off(3000, since=4000), 0.0, 'a focus in the future: in place')

    def test_clipping_never_exceeds_the_column(self):
        name = b'Magicks & Technicks of the Old Kingdom'
        x, col = 400.0, 150.0
        for t, focused in ((0, 1), (1800, 1), (4000, 1), (9000, 1), (4000, 0)):
            out = (Quad * 400)()
            n = self.d.ILCT_MqText(name, x, col, t, 0, focused, out, 400)
            qs = list(out[:n])
            self.assertGreater(n, 4)
            self.assertGreaterEqual(min(q.x0 for q in qs), x - 0.01, t)
            self.assertLessEqual(max(q.x1 for q in qs), x + col + 0.01, t)
            self.assertEqual(self.d.ILCT_MqClipOn(), 0, 'the batch clip is restored')
        out = (Quad * 400)()
        n = self.d.ILCT_MqText(name, x, col, 0, 0, 0, out, 400)
        right = [q for q in out[:n] if q.x1 > x + col - 6]
        self.assertTrue(any((q.c[1] >> 24) < (q.c[0] >> 24) or (q.c[1] >> 24) < 0xE0 for q in right),
                        'unfocused: the start shows, softly faded at the clipped edge (no dots)')

    def test_a_short_name_is_plain_text(self):
        out = (Quad * 100)()
        n = self.d.ILCT_MqText(b'Cure', 400.0, 150.0, 1234, 0, 1, out, 100)
        self.assertGreater(n, 0)
        self.assertLess(max(q.x1 for q in out[:n]), 400.0 + 80.0)


# ---- the windows' built-in help lines fit the footer in every theme ------------------------------------------------------
THEMES = Path(os.environ.get('ILC_HUD_THEMES', 'E:/Modding/games/ff12/workspace/hud-art-20261116/themes'))
FOOTER_W = 235.0        # the 275-unit window's footer text width (scene.h slot_layout), with margin


def theme_measure(theme_dir: Path):
    """A width function from a theme package's atlas/theme.atlas (THEME-FORMAT.md section 5), read here on its own."""
    d = (theme_dir / 'atlas' / 'theme.atlas').read_bytes()
    magic, ver, faces, icons, em, spread, w, h = struct.unpack_from('<4sHHHHHHH', d, 0)
    assert magic == b'ILCA', magic
    ng, nk, nr = struct.unpack_from('<III', d, 18)
    off = 18 + 12 + 32 + 8 * (faces + 1)
    adv = {}
    for i in range(ng):
        code, face, x, y, gw, gh, bx, by, a64 = struct.unpack_from('<IBxxxHHHHhhi', d, off + 24 * i)
        adv[(face, code)] = a64
    off += 24 * ng
    kern = {}
    for i in range(nk):
        f, left, right, k64 = struct.unpack_from('<BxxxIIi', d, off + 16 * i)
        kern[(f, left, right)] = k64

    def measure(text: str, size: float, face=0) -> float:
        total, prev = 0, None
        for ch in text:
            c = ord(ch)
            total += adv.get((face, c), adv.get((face, ord('?')), 0))
            if prev is not None:
                total += kern.get((face, prev, c), 0)
            prev = c
        return total / 64.0 * size / em
    return measure


class HelpFitTest(HudCase):
    def builtin(self):
        out, k = [], 0
        while True:
            s = self.d.ILCT_HmBuiltinHelp(k)
            if s is None:
                return out
            out.append(s.decode())
            k += 1

    def lines(self):
        texts = self.builtin()
        self.assertGreater(len(texts), 15)
        return texts   # no cost suffix: the MP column carries the charged cost

    def test_every_help_line_fits_the_footer_in_the_stand_in_font(self):
        for t in self.lines():
            w = self.d.ILCT_OvMeasure(tov.FACE_BODY, 17.0, t.encode(), len(t))
            self.assertLessEqual(w, FOOTER_W, f'{t!r}: {w:.1f}')

    @unittest.skipUnless(THEMES.is_dir(), 'the theme packages (workspace hud-art-20261116/themes)')
    def test_every_help_line_fits_the_footer_in_all_three_themes(self):
        import json
        for name in ('legacy-bold', 'legacy-humanist', 'legacy-crisp'):
            theme = THEMES / name
            size = json.loads((theme / 'theme.json').read_text(encoding='utf-8'))['sizes']['help']
            measure = theme_measure(theme)
            for t in self.lines():
                with self.subTest(theme=name, text=t):
                    self.assertLessEqual(measure(t, size), FOOTER_W, f'{name} {t!r} at {size}')


# ---- WARP: the whole HUD ---------------------------------------------------------------------------------------------------
@native_test
class WarpHudTest(unittest.TestCase):
    W, H = 1920, 1080

    @classmethod
    def setUpClass(cls):
        cls.d = declare(tov.declare(fresh_test_dll()))
        cls.renderer = cls.d.ILCT_OvWarpInit(cls.W, cls.H, tov.R8G8B8A8_UNORM)
        if cls.renderer == 0:
            raise unittest.SkipTest('no D3D11 WARP device on this machine')

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_ThSelectDefault()
        cls.d.ILCT_OvWarpFree()

    # The built-in themes (Phase 8): C the default, A, B. Each render is written once per theme.
    BUILTINS = (('legacy-bold', 0), ('legacy-humanist', 1), ('legacy-crisp', 2))

    def select(self, k):
        what = ctypes.create_string_buffer(96)
        self.d.ILCT_ThSelectBuiltin.argtypes = [u32, ctypes.c_char_p, u32]
        self.d.ILCT_ThSelectBuiltin.restype = i32
        self.assertEqual(self.d.ILCT_ThSelectBuiltin(k, what, 96), 1, what.value)

    def pixels(self) -> bytes:
        buf = (u8 * (self.W * self.H * 4))()
        self.assertEqual(self.d.ILCT_OvWarpRead(buf, len(buf)), len(buf))
        return bytes(buf)

    def alpha_at(self, px, x0, y0, x1, y1) -> int:
        n = 0
        for y in range(y0, y1, 2):
            row = y * self.W * 4
            n += sum(1 for x in range(x0, x1, 2) if px[row + x * 4 + 3] != 0)
        return n

    def render(self, name, view, snap, feed, ti, log, states, p1=1):
        self.d.ILCT_OvWarpClear(0)
        info, o = tov.FrameInfo(), Output()
        r = self.d.ILCT_OvWarpHud(addr(view), addr(snap), addr(rows_record()), addr(feed), addr(ti), addr(log),
                                  addr(states), NOW, 0, p1, ctypes.byref(info), ctypes.byref(o))
        self.assertEqual(r, tov.DRAWN)
        px = self.pixels()
        if PREVIEW:
            tov.write_png(Path(PREVIEW) / name, self.W, self.H, px)
        return px, o, info

    def test_the_full_combat_hud(self):
        for theme, k in self.BUILTINS:
            with self.subTest(theme=theme):
                self.select(k)
                self.combat(f'warp-hud-combat-{theme}-1920x1080.png')
        self.select(0)
        self.combat('warp-hud-combat-1920x1080.png')

    def combat(self, name):
        v = tov.view_with({0: thm.seat_view(member=0, toggle=True, title='Vaan', help_text='Attack with weapon in hand.'),
                           1: thm.seat_view(kind=thm.WK_TARGET, member=1, rows=3, title='P2 - Fira',
                                            help_text='Target: foes or party'),
                           3: thm.seat_view(member=3, values=True, rows=4, title='P4 - White Magicks',
                                            help_text='Restores HP to one ally.')})
        st = states_record(foes=[Foe(0x200001, FOE_BROKEN | FOE_KING, 7, 25, 2, 0, 0xFF)],
                           words=[Word(0, WORD_GUARD, 2), Word(3, WORD_CADENCE, 4)],
                           procs=[Proc(0x100001 + 1, TAG_SUNDER, (0, 0, 0), NOW - 40)])
        px, o, info = self.render(name, v, snapshot(), feed_record(chain=12),
                                  ti_view(), log_view(), st)
        self.assertEqual(o.drawn, 0b111)
        self.assertLess(info.quads, 6144)
        self.assertGreater(self.alpha_at(px, 40, 40, 650, 400), 2000, 'Target Info')
        self.assertGreater(self.alpha_at(px, 1274, 40, 1900, 260), 2000, 'the battle log')
        self.assertGreater(self.alpha_at(px, 1290, 820, 1900, 1070), 3000, 'the party list')
        self.assertEqual(self.alpha_at(px, 700, 30, 1200, 110), 0, 'no Wait line in the top-centre (removed)')
        self.assertEqual(self.alpha_at(px, 700, 400, 1200, 700), 0, 'the middle stays the game\'s')

    def test_the_exploration_hud(self):
        for theme, k in self.BUILTINS:
            with self.subTest(theme=theme):
                self.select(k)
                self.exploration(f'warp-hud-exploration-{theme}-1920x1080.png')
        self.select(0)
        self.exploration('warp-hud-exploration-1920x1080.png')

    def exploration(self, name):
        f = feed_record(acting=(0, 0, 0, 0))
        px, o, _ = self.render(name, tov.view_with({}), snapshot(), f, None, log_view(lines=2), None)
        self.assertEqual(o.drawn & 0b0011, 0b0001, 'the party list; no Target Info without a target')
        self.assertEqual(self.alpha_at(px, 40, 40, 650, 400), 0)


if __name__ == '__main__':
    unittest.main()
