"""Host tests for the guests' interaction prompt (native/coop/src/interaction/prompt.*, IX-21). No game run, nothing
deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin.

* the site row: IX-21 is an OPTIONAL MOVEMENT call row on the prompt drawer's only call (0x2c6352 -> 0x2c7f90), its
  signature one hit; the game bytes it relies on (the drawer's arguments, the functions it calls, the key and frame
  tables) are pinned at attach and match the exe (attach on the twin);
* the rules: the native marker's level per published state, a usable level shown as the "!" only while P1's leader
  is busy for an object that animates it (never for the guest's own action word), the label string the setter
  builds (message prefix, button glyph, name), and the animation as the native widget keeps it (pop from 0 on a new
  marker, a higher level restarts its table, a lower one shows at rest, the fade in and out, the label time, the
  frame divisor, a record that went away);
* the handlers on a synthetic ctx with doubles of the game's object, sprite, key and scale functions: stock while no
  guest has a marker; one marker per guest whose scan found something, anchored to that guest's overhead record, with
  the drawer's frame, colour, scale and offsets, and for a usable level the label node (built once per object,
  appended to list D, white or grey, faded with the marker); nothing without an anchor, a texture or the overhead
  fade; never more than the pool holds; the registers the game handed over are untouched;
* the co-op thread's record on a synthetic image: a manual guest that steers its character and whose own scan found
  something; AI-controlled, parked or menu seats, P1's leader, the setting off, a modal, a request in flight or a hold
  give none; usable levels drop to the "!" only while P1's leader is busy for an object that animates it; the
  find's name and resource travel with the record for the label.
"""
from __future__ import annotations

import ctypes
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_interaction as ix
import test_coop_patch_engine as pe
from coop_native_support import native_test
from coop_session_support import GATE, SEATF, FACT, Image
from test_coop_patch_engine import GAME, TWIN_BASE

u8, u16, u32, u64, i32, i64 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32,
                               ctypes.c_int64)
P = ctypes.POINTER
EPOCH, NOW = gt.EPOCH, gt.NOW
RUN_ORIGINAL, RESUME, CALL_AROUND = 0, 1, 2

# ---- game facts (VAs at the pinned base 0x120000) -------------------------------------------------------------------
SITE, DRAWER = 0x2C6352, 0x2C7F90
UI_ATLASES, ATLAS_BATTLE = 0x209AC30, 0x350
KEYS_OUTER, KEYS_INNER, FRAME_DELTA = 0x916E10, 0x916E40, 0x916F00
LIFT_TARGET, FRAME_DIVISOR, UI_HEIGHT, BUTTON_CONFIG = 0x1E0CF38, 0x1DFE0B4, 0x1E0C1DC, 0x1E0CED4
OVERHEAD_RECORDS, OVERHEAD_FADE = 0x1D70, 0x1D78
CONTROLLED = 0x22C7FE0
FADE_STEP, POP_STEP, LIFT_STEP, FADE_FULL, TIME_END = 0x20, 0x10, 0x20, 0x80, 0x100
LABEL_STEP, NODE_SIZE, NODE_TEXT = 0x20, 0x1E0, 0x130


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


RecordHead = S('RecordHead', [('epoch', u32), ('flags', u32), ('generation', u64)])
PromptSeat = S('PromptSeat', [('actor', u32), ('object', u32), ('level', u8), ('seat', u8), ('reserved', u16),
                               ('reserved2', u32), ('text', u64), ('resource', u64)])
PromptRecord = S('PromptRecord', [('head', RecordHead), ('visible', u32), ('reserved', u32), ('seats', PromptSeat * 3)])
Anim = S('Anim', [('actor', u32), ('object', u32), ('level', u8), ('leaving', u8), ('fade', u16), ('pop', u16),
                  ('lift', u16), ('label', u16), ('reserved', u16), ('seen_ms', u64)])
COUNTER_NAMES = ['published', 'passes_wrapped', 'objects', 'pool_full', 'no_anchor', 'no_texture', 'labels',
                 'labels_built', 'no_label']
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])

SIGNATURES = {
    'ILCT_IxPromptSizes': ([P(u32), u32], u32), 'ILCT_IxPromptLevelOf': ([u32], u32),
    'ILCT_IxPromptShownLevel': ([u32, i32], u32), 'ILCT_IxPromptSeatLevel': ([u32, i32, i32], u32),
    'ILCT_IxPromptLabelString': ([ctypes.c_char_p, u32, u32, ctypes.c_char_p, u32, ctypes.c_void_p, P(u32)], i32),
    'ILCT_IxPromptAnimStep': ([P(Anim), u32, u32, u32, i32, u32, u64], i32),
    'ILCT_IxPromptReset': ([], None), 'ILCT_IxPromptUseDoubles': ([i32], None),
    'ILCT_IxPromptSetImageBase': ([u64], None), 'ILCT_IxPromptSetPose': ([i32, i32], None),
    'ILCT_IxPromptAttach': ([u64], i32), 'ILCT_IxPromptAttached': ([], i32),
    'ILCT_IxPromptPublish': ([P(PromptRecord)], None), 'ILCT_IxPromptWithdraw': ([], None),
    'ILCT_IxPromptRead': ([P(PromptRecord)], i32), 'ILCT_IxPromptAnims': ([P(Anim)], None),
    'ILCT_IxPromptRow': ([], i32), 'ILCT_IxPromptCall': ([ctypes.c_void_p, u32], i32),
    'ILCT_IxPromptAfter': ([ctypes.c_void_p, u32], None), 'ILCT_IxPromptStats': ([P(Counters)], None),
    'ILCT_IxPromptSprites': ([P(u64), u32], u32), 'ILCT_IxPromptEvals': ([P(i64), u32], u32),
    'ILCT_IxPromptAppends': ([], u32), 'ILCT_IxPromptSetLabelDoubles': ([u64, i32], None),
    'ILCT_IxPromptNode': ([u32, ctypes.c_void_p], None), 'ILCT_IxPromptNodeAddress': ([u32], u64),
    'ILCT_IxPromptInits': ([P(u64), u32], u32), 'ILCT_IxPromptLabelCalls': ([P(u32)], u32),
}


def declare(d):
    if not hasattr(d, 'ILCT_IxPromptSizes'):
        raise unittest.SkipTest('the prompt port is not in this build (prompt-port.patch not applied)')
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def stats(d) -> Counters:
    c = Counters()
    d.ILCT_IxPromptStats(ctypes.byref(c))
    return c


def prompt_record(seats, epoch=EPOCH) -> PromptRecord:
    """seats = {k: (actor, object, level[, text, resource])}"""
    r = PromptRecord()
    r.head.epoch, r.head.generation = epoch, gt.GEN
    for k, row in seats.items():
        actor, obj, level = row[:3]
        r.seats[k].actor, r.seats[k].object, r.seats[k].level, r.seats[k].seat = actor, obj, level, k + 1
        if len(row) > 3:
            r.seats[k].text, r.seats[k].resource = row[3], row[4]
        if level:
            r.visible |= 1 << k
    return r


def label_bytes(prefix: bytes, config: int, name: bytes) -> bytes:
    return prefix + bytes([0x0F, 0x3C, ((config >> 1) & 0xC1) | 0x81, (config & 0xFF) | 0x80]) + name


_world = None


def world():
    """The twin installed, LIVE, armed, every subsystem bit (the HUD's world, nothing of the HUD attached)."""
    global _world
    if _world is None:
        _world = th.HudWorld()
        declare(_world.d)
    return _world


# ---- static -----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowTest(unittest.TestCase):
    def test_ix21_is_an_optional_movement_call_row_on_the_drawers_only_call(self):
        rows = {r['id']: r for r in pe.site_rows()}
        if 'IX-21' not in rows:
            self.skipTest('prompt-port.patch not applied')
        r = rows['IX-21']
        self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('CALL', 'PATCH', 'MOVEMENT'))
        self.assertEqual((r['rva'], r['hook_len'], r['expected']), (SITE - 0x120000, 5, bytes.fromhex('e8391c0000')))
        self.assertTrue(r['flags'] & 0x08, 'OPTIONAL: another mod rewriting the call skips the row, never the install')
        self.assertFalse(r['flags'] & 0x01, 'no interior reference')
        self.assertEqual(SITE + 5 + struct.unpack('<i', r['expected'][1:])[0], DRAWER)
        self.assertEqual(pe.call_offset(r), 0)


# ---- rules ------------------------------------------------------------------------------------------------------------
@native_test
class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = world().d

    def test_layouts(self):
        out = (u32 * 8)()
        n = self.d.ILCT_IxPromptSizes(out, 8)
        self.assertEqual(list(out[:n]), [ctypes.sizeof(PromptSeat), ctypes.sizeof(PromptRecord), ctypes.sizeof(Anim),
                                         ctypes.sizeof(Counters)])

    def test_levels(self):
        for state, level in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 0), (10, 0), (0xFFFFFFFF, 0)):
            self.assertEqual(self.d.ILCT_IxPromptLevelOf(state), level, state)
        for level, can, shown in ((0, 1, 0), (0, 0, 0), (1, 1, 1), (1, 0, 1), (2, 1, 2), (2, 0, 1), (3, 1, 3),
                                  (3, 0, 1)):
            self.assertEqual(self.d.ILCT_IxPromptShownLevel(level, can), shown, (level, can))

    def test_seat_level_asks_only_p1s_leader_and_only_for_objects_that_animate_it(self):
        for state, chest, leader, want in ((2, 1, 0, 2), (2, 1, 1, 2), (3, 0, 1, 3), (3, 0, 0, 1), (2, 0, 0, 1),
                                           (1, 0, 0, 1), (1, 1, 1, 1), (0, 1, 1, 0), (10, 1, 1, 0)):
            self.assertEqual(self.d.ILCT_IxPromptSeatLevel(state, chest, leader), want, (state, chest, leader))

    def test_the_label_string_is_the_setters(self):
        out = (u8 * 0x80)()
        n = u32()
        for config in (0, 1, 2, 0x13, 0xFF, 0x1234):
            ok = self.d.ILCT_IxPromptLabelString(b'', 0, config, b'Open', 4, out, ctypes.byref(n))
            self.assertEqual(ok, 1)
            self.assertEqual(bytes(out[:n.value + 1]), label_bytes(b'', config, b'Open') + b'\0', hex(config))
        self.d.ILCT_IxPromptLabelString(b'\x01\x02', 2, 0, b'Talk', 4, out, ctypes.byref(n))
        self.assertEqual(bytes(out[:n.value]), label_bytes(b'\x01\x02', 0, b'Talk'), 'the message prefix first')
        self.assertEqual(self.d.ILCT_IxPromptLabelString(b'', 0, 0, b'', 0, out, ctypes.byref(n)), 0, 'no name')
        name = b'x' * (0x7F - 4)
        self.assertEqual(self.d.ILCT_IxPromptLabelString(b'', 0, 0, name, len(name), out, ctypes.byref(n)), 1)
        self.assertEqual(self.d.ILCT_IxPromptLabelString(b'', 0, 0, name + b'x', len(name) + 1, out,
                                                         ctypes.byref(n)), 0, 'too long: no label, never cut')

    def step(self, a, level, actor=0x100001, obj=0x10004, divisor=1, now=NOW, labelled=0):
        return self.d.ILCT_IxPromptAnimStep(ctypes.byref(a), actor, obj, level, labelled, divisor, now)

    def test_the_label_time_rises_with_a_named_usable_level_and_falls_otherwise(self):
        a = Anim()
        self.step(a, 1, labelled=1)
        self.assertEqual(a.label, 0, 'the "!" has no label')
        for _ in range(10):
            self.step(a, 2, labelled=1)
        self.assertEqual(a.label, TIME_END)
        self.step(a, 1)
        self.assertEqual(a.label, TIME_END - LABEL_STEP, 'down to the "!": the label fades')
        self.step(a, 0)
        self.assertEqual(a.label, TIME_END - 2 * LABEL_STEP, '... and while leaving')

    def test_a_new_marker_pops_and_fades_in_like_the_native_widget(self):
        a = Anim()
        self.assertEqual(self.step(a, 1), 1)
        self.assertEqual((a.level, a.fade, a.pop, a.lift), (1, FADE_STEP, POP_STEP, LIFT_STEP))
        for _ in range(20):
            self.step(a, 1)
        self.assertEqual((a.fade, a.pop, a.lift), (FADE_FULL, TIME_END, TIME_END), 'clamped at the native limits')

    def test_level_changes(self):
        a = Anim()
        for _ in range(20):
            self.step(a, 1)
        self.step(a, 2)
        self.assertEqual((a.level, a.pop), (2, POP_STEP), 'a higher level restarts its table')
        for _ in range(20):
            self.step(a, 2)
        self.step(a, 1)
        self.assertEqual((a.level, a.pop), (1, TIME_END), 'a lower level shows at its rest')
        self.step(a, 1, obj=0x10005)
        self.assertEqual(a.pop, POP_STEP, 'a new object at the "!" pops again')
        self.assertEqual(a.fade, FADE_FULL, '... without fading out first')

    def test_fading_out_then_gone(self):
        a = Anim()
        for _ in range(10):
            self.step(a, 2)
        drawn = [self.step(a, 0) for _ in range(5)]
        self.assertEqual(drawn, [1, 1, 1, 0, 0], 'four frames of fade at 0x20 from 0x80')
        self.assertEqual(bytes(a)[:16], bytes(16), 'cleared')

    def test_the_divisor_and_a_record_that_went_away(self):
        a = Anim()
        self.step(a, 1, divisor=2)
        self.assertEqual((a.fade, a.pop, a.lift), (FADE_STEP // 2, POP_STEP // 2, LIFT_STEP // 2))
        b = Anim()
        self.step(b, 1, divisor=0)
        self.assertEqual(b.fade, FADE_STEP, 'divisor 0 is taken as 1')
        c = Anim()
        self.step(c, 1, divisor=64)
        self.assertEqual((c.fade, c.pop, c.lift), (1, 1, 1), 'every step at least 1')
        self.assertEqual(self.step(c, 0, divisor=64), 0, 'so a marker can always fade out')
        for _ in range(10):
            self.step(b, 1)
        self.step(b, 1, now=NOW + 1000)
        self.assertEqual((b.fade, b.pop), (FADE_STEP, POP_STEP), 'no update for 250 ms: whatever shows is new')
        self.step(b, 1, actor=0x200002, now=NOW + 1020)
        self.assertEqual((b.actor, b.pop), (0x200002, POP_STEP), 'another character: a new marker')


# ---- handlers ---------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HandlerTest(unittest.TestCase):
    """IX-21's handler and after-handler on host memory with doubles of the game's functions."""

    CAP = 128
    P2, P3, P4, FOE = 0x00100001, 0x00400002, 0x00500003, 0x00600009

    @classmethod
    def setUpClass(cls):
        cls.hw = world()
        cls.d = cls.hw.d

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_IxPromptReset()
        d.ILCT_IxPromptUseDoubles(1)
        self.img = Image()
        d.ILCT_IxPromptSetImageBase(self.img.base)
        # battle_4_p: texture entry -> resource; header = resource + 0x30 (frames at +0x10, sheet starts at +0x14);
        # the prompt drawer's sheet index at resource +0x44.
        self.atlases = self.img.alloc(0x400)
        self.texture = self.img.alloc(0x100)
        self.resource = self.img.alloc(0x1000)
        self.img.put_global(UI_ATLASES, struct.pack('<Q', self.atlases))
        self.img.p64(self.atlases + ATLAS_BATTLE, self.texture)
        self.img.p64(self.texture + 0x20, self.resource)
        header = self.resource + 0x30
        self.img.p16(header + 0x10, 0x200)          # frame table at header + 0x200
        self.img.p16(self.resource + 0x44, 3)       # sheet index 3
        self.img.p16(header + 0x14 + 2 * 3, 40)     # its first frame: 40
        self.frames = header + 0x200
        self.img.put_global(FRAME_DELTA, bytes([1, 0, 2]))
        self.img.put_global(KEYS_OUTER, bytes(40))
        self.img.put_global(KEYS_INNER, bytes(80))
        self.img.put_global(LIFT_TARGET, struct.pack('<b', -17))
        self.img.put_global(FRAME_DIVISOR, struct.pack('<I', 1))
        self.img.put_global(UI_HEIGHT, struct.pack('<i', 448))
        self.img.put_global(BUTTON_CONFIG, struct.pack('<I', 0x13))
        self.message = self.img.alloc(16)
        self.img.put(self.message, b'\x05\0')
        self.name = self.img.alloc(32)
        self.img.put(self.name, b'Open\0')
        self.d.ILCT_IxPromptSetLabelDoubles(self.message, 1)
        self.list_d = self.img.alloc(0x80)
        self.stack = self.img.alloc(0x40)
        self.img.p64(self.stack + 0x28, self.list_d)
        self.manager = self.img.alloc(0x2000)
        self.img.p16(self.manager + OVERHEAD_FADE, 0x80)
        self.objects = self.img.alloc(self.CAP * 0x100)
        self.pool = self.img.alloc(16)
        self.list_b = self.img.alloc(32)
        self.widget = self.img.alloc(0x200)
        self.records = {}
        prev = self.manager + OVERHEAD_RECORDS - 0x18
        for k, handle in enumerate((self.FOE, self.P2, self.P3)):
            rec = self.img.alloc(0x40)
            self.img.put(rec, struct.pack('<4f', 1.0 + k, 2.0, 3.0 + k, 1.0))
            self.img.p32(rec + 0x10, handle)
            self.img.p64(prev + 0x18, rec)
            prev = rec
            self.records[handle] = rec
        self.reset_pool(0)
        self.now = NOW

    def tearDown(self):
        self.d.ILCT_IxPromptUseDoubles(0)
        self.d.ILCT_IxPromptReset()
        self.hw.twin.reset()
        self.img.close()

    def reset_pool(self, used, cap=None):
        self.img.p64(self.pool, self.objects)
        self.img.put(self.pool + 8, struct.pack('<ii', self.CAP if cap is None else cap, used))
        self.img.put(self.list_b, bytes(32))

    def publish(self, seats, epoch=EPOCH):
        self.d.ILCT_IxPromptPublish(ctypes.byref(prompt_record(seats, epoch)))

    def ctx(self):
        c = th.HookCtx()
        c.index, c.rcx, c.rdx, c.r8, c.r9 = self.d.ILCT_IxPromptRow(), self.manager, self.widget, self.list_b, self.pool
        c.rsp = self.stack
        return c

    def frame(self, epoch=EPOCH, dt=33):
        d = self.d
        self.now += dt
        d.ILCT_SetFixedClock(self.now)
        c = self.ctx()
        res = d.ILCT_IxPromptCall(ctypes.byref(c), epoch)
        if res == CALL_AROUND:
            d.ILCT_IxPromptAfter(ctypes.byref(c), epoch)
        return res

    def used(self):
        return struct.unpack_from('<i', ctypes.string_at(self.pool + 12, 4))[0]

    def chain(self):
        out, obj = [], struct.unpack('<Q', ctypes.string_at(self.list_b, 8))[0]
        while obj:
            out.append(obj)
            obj = struct.unpack('<Q', ctypes.string_at(obj + 0xD0, 8))[0]
        return out

    def sprites(self):
        buf = (u64 * (6 * 32))()
        n = self.d.ILCT_IxPromptSprites(buf, 32)
        return [tuple(buf[6 * k:6 * k + 6]) for k in range(n)]

    def evals(self):
        buf = (i64 * (4 * 32))()
        n = self.d.ILCT_IxPromptEvals(buf, 32)
        return [tuple(buf[4 * k:4 * k + 4]) for k in range(n)]

    def quad(self, obj):
        return struct.unpack('<4f', ctypes.string_at(obj + 0x50, 16))

    def test_nothing_found_runs_the_drawer_as_stock(self):
        self.publish({0: (self.P2, 0, 0), 1: (self.P3, 0, 0)})
        self.assertEqual(self.frame(), RUN_ORIGINAL)
        self.assertEqual((self.used(), self.chain(), self.sprites()), (0, [], []))

    def test_one_marker_per_guest_anchored_to_its_own_overhead_record(self):
        self.publish({0: (self.P2, 0x10004, 1), 1: (self.P3, 0x10005, 2)})
        self.assertEqual(self.frame(), CALL_AROUND)
        objs = self.chain()
        self.assertEqual((self.used(), len(objs)), (2, 2))
        sp = self.sprites()
        self.assertEqual([s[0] for s in sp], [self.records[self.P2], self.records[self.P3]])
        self.assertEqual([s[1] for s in sp], objs)
        self.assertEqual([s[2] for s in sp], [self.frames + 8 * 41, self.frames + 8 * 40],
                         'level 1: sheet first + 1 (the "!"); level 2: + 0 (the usable marker)')
        self.assertEqual({s[4] for s in sp}, {0x80808080}, 'the drawer colour')
        self.assertEqual({s[5] for s in sp}, {self.texture}, 'battle_4_p')
        self.assertEqual([s[3] for s in sp], [FADE_STEP, FADE_STEP], 'pose alpha 0x80 x fade 0x20 x overhead 0x80')
        ev = self.evals()
        self.assertEqual([(e[1], e[2]) for e in ev], [(5, POP_STEP), (2, LIFT_STEP), (2, 0), (10, POP_STEP),
                                                      (2, LIFT_STEP), (2, 0)],
                         "each marker: its level's table at the pop time, the lift curve, the label curve (no name: "
                         "its time stays 0)")
        self.assertEqual(ev[0][0], self.img.base + KEYS_OUTER - 0x120000)
        self.assertEqual(ev[3][0], self.img.base + KEYS_INNER - 0x120000)
        self.assertEqual({e[3] for e in ev if e[1] == 2}, {-17}, 'the lift curve ends at [0x1e0cf38]')
        x0, y0, x1, y1 = self.quad(objs[0])
        self.assertEqual((x0, x1), (-16.0, 16.0))
        self.assertEqual((y0, y1), (-57.0 - 10.0, -10.0), 'lift 0 until it has run, then the table y -10')

    def test_rest_pose_and_scale(self):
        self.publish({0: (self.P2, 0x10004, 1)})
        for _ in range(20):
            self.reset_pool(0)
            self.frame()
        self.d.ILCT_IxPromptSetPose(0x80, 0x200)
        self.reset_pool(0)
        self.frame()
        x0, y0, x1, y1 = self.quad(self.chain()[0])
        self.assertEqual((x0, x1), (-32.0, 32.0), 'scale 0x200 = 2.0 on x0, y0, x1 (0x2708b0)')
        self.assertEqual((y0, y1), (-114.0 - 17.0 - 10.0, -17.0 - 10.0), 'lift -17 and table -10')
        self.assertEqual(self.sprites()[-1][3], 0x80, 'faded in')

    def test_a_usable_find_gets_the_setters_label_in_list_d(self):
        self.publish({0: (self.P2, 0x10004, 2, self.name, 0x7777)})
        self.assertEqual(self.frame(), CALL_AROUND)
        node = self.d.ILCT_IxPromptNodeAddress(0)
        inits = (u64 * 9)()
        self.assertEqual(self.d.ILCT_IxPromptInits(inits, 3), 1)
        self.assertEqual(tuple(inits[:3]), (node, node + NODE_TEXT, 0x7777), '0x2b3400(node, node + 0x130, resource)')
        text = ctypes.string_at(node + NODE_TEXT)
        self.assertEqual(text, label_bytes(b'\x05', 0x13, b'Open'), 'prefix message, glyph from [0x1e0ced4], name')
        w, h = len(text) * 8, 20
        self.assertEqual(struct.unpack('<hhHH', ctypes.string_at(node + 0xF0, 8)), (-(w >> 1), 0, w, h),
                         'centred like the setter; the y offset is the label and lift curves (0 this frame)')
        self.assertEqual(struct.unpack('<Q', ctypes.string_at(self.list_d + 0x40, 8))[0], node,
                         'appended to list D from the first frame its curve is visible')
        for _ in range(10):
            self.reset_pool(0)
            self.frame()
        self.assertEqual(struct.unpack('<Q', ctypes.string_at(self.list_d + 0x40, 8))[0], node, 'appended to list D')
        rgba = ctypes.string_at(node + 0x30, 4)
        self.assertEqual(rgba, bytes([0x80, 0x80, 0x80, 0x80]), 'white, full alpha (pose 0x80, fade 0x80)')
        obj = self.chain()[0]
        self.assertEqual(ctypes.string_at(node + 0xD0, 12), ctypes.string_at(obj, 12), "the marker's anchor")
        self.assertEqual(struct.unpack('<f', ctypes.string_at(node + 0xDC, 4))[0], 1.0)
        self.assertEqual(struct.unpack('<h', ctypes.string_at(node + 0xF2, 2))[0], -17 - 17,
                         'ui_scale 1.0 x label -17 + lift -17')
        calls = (u32 * 2)()
        self.assertEqual(self.d.ILCT_IxPromptLabelCalls(calls), 1, 'built once for the object')
        self.assertEqual(stats(self.d).labels_built, 1)
        self.assertGreater(stats(self.d).labels, 0)

    def test_grey_label_new_object_rebuilds_and_no_label_cases(self):
        self.d.ILCT_IxPromptSetLabelDoubles(self.message, 0)
        self.publish({0: (self.P2, 0x10004, 3, self.name, 1)})
        for _ in range(10):
            self.reset_pool(0)
            self.frame()
        node = self.d.ILCT_IxPromptNodeAddress(0)
        self.assertEqual(ctypes.string_at(node + 0x30, 3), bytes([0x40] * 3), '0x3df6f0 said 0: grey')
        other = self.img.alloc(16)
        self.img.put(other, b'Talk\0')
        self.publish({0: (self.P2, 0x10005, 2, other, 1)})
        self.reset_pool(0)
        self.frame()
        self.assertEqual(stats(self.d).labels_built, 2, 'another object: its own label')
        self.assertTrue(ctypes.string_at(node + NODE_TEXT).endswith(b'Talk'))
        before = stats(self.d).no_label
        self.publish({0: (self.P2, 0x10006, 2, 0, 0)})
        self.reset_pool(0)
        self.frame()
        self.assertEqual(stats(self.d).no_label, before, 'no name: a usable marker without a label, not a failure')
        self.img.p64(self.stack + 0x28, 0)
        self.publish({0: (self.P2, 0x10004, 2, self.name, 1)})
        self.reset_pool(0)
        self.frame()
        self.assertEqual(stats(self.d).no_label, before + 1, 'list D unreadable: no label')
        self.assertEqual(len(self.chain()), 1, '... the marker still shows')

    def test_no_anchor_no_texture_no_overhead_fade(self):
        self.publish({0: (self.P2, 0x10004, 1), 2: (self.P4, 0x10006, 1)})
        self.frame()
        self.assertEqual(len(self.chain()), 1, 'P4 has no overhead record: nothing over nobody')
        self.assertEqual(stats(self.d).no_anchor, 1)
        self.reset_pool(0)
        self.img.p16(self.manager + OVERHEAD_FADE, 0)
        self.frame()
        self.assertEqual(self.chain(), [], 'the game hides every overhead mark: ours too')
        self.img.p16(self.manager + OVERHEAD_FADE, 0x80)
        self.img.p64(self.atlases + ATLAS_BATTLE, 0)
        self.frame()
        self.assertEqual(self.chain(), [])
        self.assertEqual(stats(self.d).no_texture, 1)

    def test_never_more_than_the_pool_holds(self):
        self.publish({0: (self.P2, 0x10004, 1), 1: (self.P3, 0x10005, 3)})
        self.reset_pool(self.CAP - 1)
        self.frame()
        self.assertEqual((self.used(), len(self.chain())), (self.CAP, 1))
        self.assertEqual(stats(self.d).pool_full, 1)

    def test_fade_out_after_the_record_says_none_then_stock(self):
        self.publish({0: (self.P2, 0x10004, 2)})
        for _ in range(6):
            self.reset_pool(0)
            self.frame()
        self.publish({0: (self.P2, 0, 0)})
        results = []
        for _ in range(5):
            self.reset_pool(0)
            results.append((self.frame(), len(self.chain())))
        self.assertEqual(results, [(CALL_AROUND, 1), (CALL_AROUND, 1), (CALL_AROUND, 1), (CALL_AROUND, 0),
                                   (RUN_ORIGINAL, 0)])

    def test_stock_for_another_epoch_no_record_or_the_subsystem_off(self):
        self.publish({0: (self.P2, 0x10004, 1)})
        self.assertEqual(self.frame(epoch=EPOCH + 1), RUN_ORIGINAL)
        self.d.ILCT_IxPromptWithdraw()
        self.assertEqual(self.frame(), RUN_ORIGINAL)
        self.publish({0: (self.P2, 0x10004, 1)})
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x02)
        self.assertEqual(self.frame(), RUN_ORIGINAL, 'MOVEMENT off')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.assertEqual(self.frame(), CALL_AROUND)

    def test_the_game_registers_are_left_alone(self):
        self.publish({0: (self.P2, 0x10004, 1)})
        before = self.ctx()
        for name, value in (('rax', 0x1111), ('rbx', 0x2222), ('rsi', 0x3333), ('r14', 0x4444), ('r15', 0x5555)):
            setattr(before, name, value)
        after = th.HookCtx.from_buffer_copy(before)
        self.d.ILCT_SetFixedClock(NOW + 33)
        self.assertEqual(self.d.ILCT_IxPromptCall(ctypes.byref(after), EPOCH), CALL_AROUND)
        self.d.ILCT_IxPromptAfter(ctypes.byref(after), EPOCH)
        self.assertEqual(bytes(after), bytes(before), 'IX-21 changes no register in either half')
        self.assertEqual(ctypes.string_at(self.widget, 0x200), bytes(0x200), "P1's widget is never written")

    def test_attach_pins_match_the_exe(self):
        self.assertEqual(self.d.ILCT_IxPromptAttach(TWIN_BASE), 1, 'every pinned byte matches the twin')
        self.assertEqual(self.d.ILCT_IxPromptAttached(), 1)
        self.d.ILCT_IxPromptReset()
        self.assertEqual(self.d.ILCT_IxPromptAttach(self.img.base), 0, 'other bytes: IX-21 stays stock')
        self.assertEqual(self.d.ILCT_IxPromptAttached(), 0)


# ---- co-op thread -----------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ComposeTest(unittest.TestCase):
    """interaction::step() composes the prompt record from the session, the guests' private finds and the actors.
    The synthetic image and its helpers are test_coop_interaction's CoopThreadTest fixture (borrowed, not
    inherited: its own tests stay in their module)."""

    entry, actor, publish, step = (ix.CoopThreadTest.entry, ix.CoopThreadTest.actor, ix.CoopThreadTest.publish,
                                   ix.CoopThreadTest.step)
    record, request, finish, find = (ix.CoopThreadTest.record, ix.CoopThreadTest.request, ix.CoopThreadTest.finish,
                                     ix.CoopThreadTest.find)
    tearDown = ix.CoopThreadTest.tearDown

    def setUp(self):
        ix.CoopThreadTest.setUp(self)
        try:
            declare(self.d)
        except unittest.SkipTest:
            self.tearDown()
            raise
        self.d.ILCT_IxPromptReset()
        self.door = ix.handle_of(1, 4, 2)
        self.entry(1, 4, 2)
        chest_e = self.entry(1, 5, 2)
        self.img.p16(chest_e + 0x102, 434)
        self.chest = ix.handle_of(1, 5, 2)
        self.p1 = self.actor(0, 0, gen=1)
        self.img.put_global(CONTROLLED, struct.pack('<I', self.p1['handle']))

    def prompt(self) -> PromptRecord:
        r = PromptRecord()
        self.assertEqual(self.d.ILCT_IxPromptRead(ctypes.byref(r)), 1)
        return r

    def shown(self):
        r = self.prompt()
        return {k: (r.seats[k].actor, r.seats[k].object, r.seats[k].level) for k in range(3) if r.visible & (1 << k)}

    def find_p3(self, slot, handle, state, tick=10, mark=10):
        p = ix.IxPrivate()
        p.state[slot], p.handle[slot], p.tick, p.mark = state, handle, tick, mark
        p.source_handle = self.p3['handle']
        self.d.ILCT_IxPublishPrivate(1, ctypes.byref(p))

    def test_a_guest_near_something_gets_the_native_level(self):
        self.publish()
        self.find(5, self.door, 3)
        self.find_p3(2, self.chest, 1)
        self.step()
        self.assertEqual(self.shown(), {0: (self.p2['handle'], self.door, 3), 1: (self.p3['handle'], self.chest, 1)})
        self.assertEqual(self.prompt().head.epoch, EPOCH)

    def test_the_finds_name_and_resource_travel_for_the_label(self):
        self.publish()
        p = ix.IxPrivate()
        p.state[2], p.handle[2], p.text[2], p.resource[2], p.tick, p.mark = 2, self.chest, 0xABC0, 0xDEF0, 10, 10
        p.state[1], p.handle[1], p.text[1], p.resource[1] = 1, self.door, 0x1110, 0x2220
        p.source_handle = self.p2['handle']
        self.d.ILCT_IxPublishPrivate(0, ctypes.byref(p))
        self.step()
        r = self.prompt()
        self.assertEqual((r.seats[0].object, r.seats[0].level, r.seats[0].text, r.seats[0].resource),
                         (self.chest, 2, 0xABC0, 0xDEF0), "the current slot's name and resource")

    def test_nothing_found_or_a_stale_find_gives_none(self):
        self.publish()
        self.step()
        self.assertEqual(self.shown(), {})
        self.find(2, self.door, 3, tick=20, mark=15)
        self.step()
        self.assertEqual(self.shown(), {}, 'a find older than four passes')
        self.find(2, ix.handle_of(1, 9, 2), 3)
        self.step()
        self.assertEqual(self.shown(), {}, 'an object that no longer resolves')
        self.find(2, self.door, 10)
        self.step()
        self.assertEqual(self.shown(), {}, 'a state the native widget does not draw')

    def test_usable_levels_drop_to_the_exclamation_only_while_p1s_leader_is_busy(self):
        self.publish()
        self.find(5, self.door, 3)
        self.img.p64(self.p2['actor'], ix.EXECUTION)
        self.img.p32(self.p2['actor'] + 0x714, 0x4124)
        self.step()
        self.assertEqual(self.shown()[0][2], 3, "the guest's own action word is not asked (it follows the leader)")
        self.img.p64(self.p2['actor'], 0)
        self.img.p32(self.p2['actor'] + 0x714, 0x4021)
        self.img.p64(self.p1['actor'], ix.WEAPON_HANDLING)
        self.step()
        self.assertEqual(self.shown()[0][2], 1, "a door animates P1's leader, who is busy")
        self.find(5, self.chest, 2, tick=11, mark=11)
        self.step()
        self.assertEqual(self.shown()[0][2], 2, "a chest needs only the guest's own character")
        self.img.p64(self.p1['actor'], 0)
        self.find(5, self.door, 3, tick=12, mark=12)
        self.step()
        self.assertEqual(self.shown()[0][2], 3)

    def test_who_never_gets_one(self):
        auto = SEATF['IN_SESSION'] | SEATF['ACTOR'] | SEATF['STEERS'] | SEATF['ALIVE'] | SEATF['ALIVE_KNOWN']
        cases = (
            ('an AI-controlled member', dict(seats={1: dict(self.p2, flags=auto)})),
            ('guests may not interact now', dict(gates=GATE['WORLD'] | GATE['LOGICAL'])),
            ('a native modal', dict(facts=FACT['MODAL'])),
        )
        for label, kw in cases:
            with self.subTest(label):
                seats = kw.pop('seats', None)
                if seats is not None:
                    self.d.ILCT_IxSessionPublish(ctypes.byref(ix.session_record(
                        {s: dict(actor=v['actor'], handle=v['handle'], flags=v['flags']) for s, v in seats.items()},
                        **kw)))
                else:
                    self.publish(**kw)
                self.find(5, self.door, 3)
                self.step()
                self.assertEqual(self.shown(), {})
        self.publish({1: self.p1})
        self.find(5, self.door, 3, source=self.p1['handle'])
        self.step()
        self.assertEqual(self.shown(), {}, "a guest seat holding P1's leader (the controlled actor)")
        self.publish()
        self.find(5, self.door, 3)
        self.d.ILCT_IxSetMenuSeats(EPOCH, 1 << 1)
        try:
            self.step()
            self.assertEqual(self.shown(), {}, 'a guest whose battle menu is open')
        finally:
            self.d.ILCT_IxSetMenuSeats(EPOCH, 0)

    def test_the_setting_off_a_request_or_a_hold_hide_every_guests_marker(self):
        opt = self.d.ILCT_OptSet
        opt.argtypes = [ctypes.c_char_p]
        self.publish()
        self.find(5, self.door, 3)
        try:
            opt(bytes([1, 0, 0, 0, 1, 5, 20, 6]))
            self.step()
            self.assertEqual(self.shown(), {}, 'Guests use doors, chests, NPCs and shops: Off')
        finally:
            self.d.ILCT_OptReset()
        self.step()
        self.assertEqual(set(self.shown()), {0})
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, ix.REQ_ARMED)
        self.assertEqual(self.shown(), {}, 'a guest interaction in flight')
        self.finish(ix.WR['CONSUMED'], self.door)
        self.step()
        self.assertEqual(self.shown(), {}, 'its hold')

    def test_stop_withdraws_the_record(self):
        self.publish()
        self.find(5, self.door, 3)
        self.step()
        self.d.ILCT_IxStop()
        r = PromptRecord()
        self.assertEqual(self.d.ILCT_IxPromptRead(ctypes.byref(r)), 0)


if __name__ == '__main__':
    unittest.main()
