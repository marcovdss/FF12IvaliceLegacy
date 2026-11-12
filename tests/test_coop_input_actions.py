"""Action layer and seat commands of the co-op module, through the real code (no game run, nothing deployed).

The host-test DLL (the shipped sources plus testsupport/) drives the input service tick by tick on a scripted
device world and a scripted keyboard, with the service clock as a fake clock (milliseconds below). Seat commands go
through the real ILC_Bridge (COMMAND), and their outcomes come back through POP_EVENTS.

The tables restate the module's rules as pass/fail rows:
* bindings: defaults, the four-binding limit, own-pad resolution, keyboard ownership and foreground, decide swap;
* edges and repeat: 0.250 s then 0.100 s, fast 0.100 s then 0.035 s, the fast cap, no bursts, independent history;
* hold absorption on every discontinuity (no edge from a held control or key until it is released; held and
  strength still count): start, reconnect, settings, keyboard transfer, focus, seat move, park, pairing, capture;
* pairing proof: held A, two pads, mirrors, Player 1's lock, another guest's pad (refuse, then a confirmed swap),
  timeout, disconnect, stale session, cancel; Player 1's guarded change (confirm, revert, commit window);
* capture, park and seat move;
* replay: a recorded run replays to the same action frames and events.

ILC_REQUIRE_NATIVE=1 makes a missing toolchain a failure instead of a skip.
"""
from __future__ import annotations

import ctypes
import re
import struct
import unittest

from coop_native_support import COOP, SCHEMA, native_test
from test_coop_input_service import (DS4, DS5, SWITCH, XBOX, SRC_NONE, SRC_SDL, SRC_XINPUT, Service, fresh_test_dll,
                                     put, sdl_reading, seat_xinput)

S = SCHEMA
OP = S.enums['op']
E = S.enums['error']
CMD = S.enums['command']
EV = S.enums['event_kind']
ACT = S.enums['action']
PADC = S.enums['pad_control']
BK = S.enums['binding_kind']
PR = S.enums['pairing_outcome']
CO = S.enums['capture_outcome']
ACTION_FRAME = struct.Struct('<IIQBBBBIIIII19H2x')
CONTROL_VIEW = struct.Struct('<BBBBBB2x4I4BI')
EVENT = struct.Struct('<IHBxIiIIII')
F_PAD, F_KBM, F_FOCUSED, F_SUPPRESSED, F_RESEEDED, F_FAST = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
ACTIVE_NONE, ACTIVE_PAD, ACTIVE_KEYBOARD = 0, 1, 2
VK_F3, VK_F6, VK_F7, VK_F9 = 0x72, 0x75, 0x76, 0x78
VK_SPACE, VK_LSHIFT, VK_SHIFT = 0x20, 0xA0, 0x10


def abit(*names) -> int:
    return sum(1 << ACT[n] for n in names)


class Rig(Service):
    """The test DLL with the action layer, the scripted keyboard and seat commands."""

    def __init__(self, dll):
        super().__init__(dll)
        d = dll
        d.ILCT_KeyboardUse.argtypes = [ctypes.c_uint32]
        d.ILCT_KeySet.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_Focus.argtypes = [ctypes.c_uint32]
        d.ILCT_SetFast.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_SetDecideSwap.argtypes = [ctypes.c_uint32]
        d.ILCT_EffectiveBinding.argtypes = [ctypes.c_uint32] * 4
        assert d.ILCT_ActionFrameSize() == ACTION_FRAME.size == 80
        assert d.ILCT_ControlViewSize() == CONTROL_VIEW.size
        d.ILCT_KeyboardUse(0)
        self.request = 0

    # ---- keyboard --------------------------------------------------------------------------------------
    def keyboard(self, on=True):
        self.dll.ILCT_KeyboardUse(1 if on else 0)

    def key(self, vk, down=True):
        self.dll.ILCT_KeySet(vk if isinstance(vk, int) else ord(vk), 1 if down else 0)

    def focus(self, on=True):
        self.dll.ILCT_Focus(1 if on else 0)

    def key_reads(self) -> tuple[int, int, set]:
        out = (ctypes.c_uint32 * 10)()
        self.dll.ILCT_KeyReads(out)
        asked = {vk for vk in range(256) if out[2 + (vk >> 5)] & (1 << (vk & 31))}
        return out[0], out[1], asked

    # ---- views -----------------------------------------------------------------------------------------
    def act(self, seat) -> dict:
        raw = ctypes.create_string_buffer(ACTION_FRAME.size)
        assert self.dll.ILCT_Actions(seat, raw) == 1
        v = ACTION_FRAME.unpack(raw.raw)
        names = ('seq', 'gen', 'time', 'seat', 'active', 'flags', 'r0', 'held', 'pressed', 'released', 'repeated',
                 'absorbed')
        f = dict(zip(names, v[:12]))
        f['strength'] = list(v[12:31])
        f['raw'] = raw.raw
        return f

    def control(self) -> dict:
        raw = ctypes.create_string_buffer(CONTROL_VIEW.size)
        self.dll.ILCT_Control(raw)
        v = CONTROL_VIEW.unpack(raw.raw)
        return {'stage': v[0], 'pairing_seat': v[1], 'capture_seat': v[2], 'override': v[3], 'parked': v[4],
                'focused': v[5], 'move_request': list(v[6:10]), 'move_character': list(v[10:14]),
                'dropped': v[14]}

    # ---- bridge ----------------------------------------------------------------------------------------
    def command(self, name, seat, a0=0, a1=0, request=None) -> tuple[int, int]:
        if request is None:
            self.request += 1
            request = self.request
        record = struct.pack('<IHHIIHBBII4x', 0x42434C49, 1, S.e('record_type', 'COMMAND'), 32, request, CMD[name],
                             seat, 0, a0, a1)
        status, _ = self.bridge.call(OP['COMMAND'], record)
        return status, request

    def events(self) -> list[dict]:
        status, out = self.bridge.call(OP['POP_EVENTS'])
        assert status >= 32, status
        count = struct.unpack_from('<I', out, 16)[0]
        result = []
        for i in range(count):
            seq, kind, seat, gen, code, field, loc, a, b = EVENT.unpack_from(out, 32 + 32 * i)
            result.append({'seq': seq, 'kind': kind, 'seat': seat, 'request': gen, 'code': code, 'a': a, 'b': b,
                           'outcome': a & 0xFF, 'other': (a >> 8) & 0xFF, 'slot': (a >> 16) & 0xFF})
        return result

    def results(self, kind) -> list[dict]:
        return [e for e in self.events() if e['kind'] == EV[kind]]

    def inventory(self) -> tuple[int, list]:
        status, reply, entries = self.devices_reply()
        assert status > 0, status
        return reply['inventory_session'], entries

    def entry(self, kind=None, slot=None, name=None) -> int:
        _, entries = self.inventory()
        for e in entries:
            if kind is not None and e['kind'] != S.e('device_kind', kind):
                continue
            if slot is not None and e['xinput_slot'] != slot:
                continue
            if name is not None and bytes(e['sdl']['name'][:e['sdl']['name_len']]) != name:
                continue
            return e['entry_id']
        raise AssertionError(f'no inventory entry {kind} {slot} {name}: {entries}')

    def pair(self, seat, entry, session=None) -> int:
        if session is None:
            session, _ = self.inventory()
        status, request = self.command('PAIRING_START', seat, session, entry)
        assert status == 0, status
        return request

    def settings_generation(self) -> int:
        status, out = self.bridge.call(OP['GET_STATUS'])
        return S.decode('ilc_status', out)['applied_generation']


def clear_device(record, seat):
    offset, field, _ = S.locate('ilc_settings', ['seats', seat - 1, 'device'])
    size = S.size('ilc_seat_device')
    record[offset:offset + size] = bytes(size)


def seat_sdl(record, seat, guid, path=None, ordinal=0, name=b'Pad'):
    """The seat on an SDL identity (the device record cleared first, so no XInput slot is left behind)."""
    clear_device(record, seat)
    put(record, ['seats', seat - 1, 'device', 'kind'], S.e('device_kind', 'SDL'))
    prefix = ['seats', seat - 1, 'device', 'sdl']
    offset, _, _ = S.locate('ilc_settings', prefix + ['guid'])
    record[offset:offset + 16] = guid
    put(record, prefix + ['ordinal'], ordinal)
    if path:
        import hashlib
        put(record, prefix + ['has_uid'], 1)
        offset, _, _ = S.locate('ilc_settings', prefix + ['uid'])
        record[offset:offset + 16] = hashlib.sha256(path.lower().encode()).digest()[:16]
    put(record, prefix + ['name_len'], len(name))
    offset, _, _ = S.locate('ilc_settings', prefix + ['name'])
    record[offset:offset + len(name)] = name


def seat_on_xinput(record, seat, slot):
    clear_device(record, seat)
    seat_xinput(record, seat, slot)


def binding(record, seat, action, slot, kind, code):
    put(record, ['seats', seat - 1, 'bindings', ACT[action] * 4 + slot, 'kind'], BK[kind])
    put(record, ['seats', seat - 1, 'bindings', ACT[action] * 4 + slot, 'code'], code)


def defaults_bindings(seat) -> list[tuple[str, int, int]]:
    record = S.decode('ilc_settings', S.defaults())
    out = []
    for i, b in enumerate(record['seats'][seat - 1]['bindings']):
        if b['kind']:
            out.append((i // 4, b['kind'], b['code']))
    return out


class RigCase(unittest.TestCase):
    def setUp(self):
        self.s = Rig(fresh_test_dll())
        self.t = 0

    def tearDown(self):
        self.s.end()

    def at(self, ms):
        self.t = ms
        self.s.step(ms)

    def tick(self, n=1, every=8):
        for _ in range(n):
            self.t += every
            self.s.step(self.t)

    def until(self, ms, every=8):
        while self.t + every <= ms:
            self.tick(every=every)
        if self.t < ms:
            self.at(ms)

    def two_guests(self, kbm=2, record_hook=None):
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        seat_xinput(r, 3, 2)
        put(r, ['kbm_seat'], kbm)
        if record_hook:
            record_hook(r)
        self.s.apply(r)
        for slot in (0, 1, 2):
            self.s.xinput(slot)
        return r


# =====================================================================================================
# A. Bindings
# =====================================================================================================
@native_test
class BindingTableTest(RigCase):
    def test_defaults_cover_every_action_and_p1_has_exactly_its_eight(self):
        p1_actions = {a for a, _, _ in defaults_bindings(1)}
        self.assertEqual(p1_actions, {ACT[n] for n in ('MENU_UP', 'MENU_DOWN', 'MENU_LEFT', 'MENU_RIGHT', 'CONFIRM',
                                                       'CANCEL', 'PARTY', 'PAUSE')})
        self.assertEqual(defaults_bindings(4), [], 'P4 cannot play in this release: no default bindings')
        for seat in (2, 3):
            with self.subTest(seat=seat):
                self.assertEqual({a for a, _, _ in defaults_bindings(seat)}, set(range(19)))
                kinds = {(a, k) for a, k, _ in defaults_bindings(seat)}
                for action in range(19):
                    if action == ACT['TOGGLE_CONTROL']:
                        continue
                    self.assertIn((action, BK['PAD']), kinds, f'P{seat} action {action} has a pad binding')

    def test_toggle_keys_leave_f3_free(self):
        toggles = {2: VK_F6, 3: VK_F7}  # P4 has no join key in this release
        for seat in (1, 2, 3, 4):
            with self.subTest(seat=seat):
                rows = defaults_bindings(seat)
                self.assertNotIn(VK_F3, [c for _, k, c in rows if k == BK['KEY']], 'F3 is the game speed key')
                if seat in toggles:
                    self.assertIn((ACT['TOGGLE_CONTROL'], BK['KEY'], toggles[seat]), rows)

    def test_default_party_pause_and_quickening(self):
        rows = {seat: defaults_bindings(seat) for seat in (1, 2, 3, 4)}
        self.assertIn((ACT['PAUSE'], BK['PAD'], PADC['START']), rows[1])
        self.assertIn((ACT['PARTY'], BK['PAD'], PADC['Y']), rows[1])
        for seat in (2, 3):
            self.assertIn((ACT['PARTY'], BK['KEY'], ord('R')), rows[seat])
            self.assertIn((ACT['PARTY'], BK['PAD'], PADC['Y']), rows[seat])
        self.assertIn((ACT['QUICKENING'], BK['PAD'], PADC['X']), rows[2])
        self.assertIn((ACT['QUICKENING'], BK['PAD'], PADC['A']), rows[3])

    def test_all_four_bindings_of_an_action_count(self):
        # Guest menu-left keeps the full PC layout: J, numpad 4, Q and the D-pad (four bindings, no fifth).
        rows = [(k, c) for a, k, c in defaults_bindings(2) if a == ACT['MENU_LEFT']]
        self.assertEqual(len(rows), 4)
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        for kind, code in rows:
            with self.subTest(kind=kind, code=code):
                if kind == BK['KEY']:
                    self.s.key(code)
                else:
                    self.s.xinput(1, buttons=['DPAD_LEFT'])
                self.tick()
                f = self.s.act(2)
                self.assertTrue(f['pressed'] & abit('MENU_LEFT'), f)
                self.s.key(code, False)
                self.s.xinput(1)
                self.tick()
                self.assertFalse(self.s.act(2)['held'] & abit('MENU_LEFT'))

    def test_each_guest_reads_its_own_pad(self):
        self.two_guests()
        self.s.begin()
        self.at(0)
        rows = [
            # slot buttons, expected P2 held, expected P3 held
            ({2: ['A']}, 0, abit('CONFIRM', 'QUICKENING')),           # P3's Quickening is A
            ({1: ['A']}, abit('CONFIRM'), 0),
            ({1: ['DPAD_UP'], 2: ['DPAD_DOWN']}, abit('MENU_UP'), abit('MENU_DOWN')),
            ({0: ['A', 'DPAD_UP']}, 0, 0),                            # P1's pad never feeds a guest
        ]
        for buttons, p2, p3 in rows:
            with self.subTest(buttons=buttons):
                for slot in (0, 1, 2):
                    self.s.xinput(slot, buttons=buttons.get(slot, []))
                self.tick()
                self.assertEqual(self.s.act(2)['held'], p2)
                self.assertEqual(self.s.act(3)['held'], p3)
                for slot in (0, 1, 2):
                    self.s.xinput(slot)
                self.tick()

    def test_a_shared_key_counts_only_for_the_keyboard_seat(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.s.key('W')
        self.tick()
        self.assertEqual(self.s.act(2)['held'], abit('MOVE_UP'))
        self.assertEqual(self.s.act(3)['held'], 0, 'P3 has W bound too, but does not own the keyboard')
        self.assertTrue(self.s.act(2)['flags'] & F_KBM)
        self.assertFalse(self.s.act(3)['flags'] & F_KBM)

    def test_keyboard_is_read_only_in_the_foreground_and_only_for_bound_keys(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.s.key_reads()
        self.tick()
        reads, focus_queries, asked = self.s.key_reads()
        wanted = {c for _, k, c in defaults_bindings(2) if k in (BK['KEY'], BK['MOUSE'])}
        self.assertEqual(asked, wanted)
        self.assertEqual(focus_queries, 1)
        self.s.focus(False)
        self.s.key('W')
        self.s.xinput(1, buttons=['A'])
        self.tick()
        reads, _, asked = self.s.key_reads()
        self.assertEqual((reads, asked), (0, set()), 'no key is read while the game is in the background')
        f = self.s.act(2)
        self.assertEqual(f['held'], abit('CONFIRM'), 'pads keep working in the background')
        self.assertFalse(f['flags'] & F_FOCUSED)

    def test_no_keys_are_read_without_a_keyboard_seat(self):
        self.two_guests(kbm=0)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.s.key_reads()
        self.s.key('W')
        self.tick(3)
        reads, _, _ = self.s.key_reads()
        self.assertEqual(reads, 0)
        self.assertEqual(self.s.act(2)['held'], 0)

    def test_decide_swap_swaps_pad_a_and_b_only(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.dll.ILCT_SetDecideSwap(1)
        self.s.begin()
        self.at(0)
        rows = [
            ({'buttons': ['B']}, None, abit('CONFIRM')),
            ({'buttons': ['A']}, None, abit('CANCEL')),
            ({}, VK_SPACE, abit('CONFIRM')),            # keyboard bindings never swap
            ({}, ord('C'), abit('CANCEL')),
        ]
        for pad, vk, want in rows:
            with self.subTest(pad=pad, vk=vk):
                self.s.xinput(1, **pad)
                if vk:
                    self.s.key(vk)
                self.tick()
                self.assertEqual(self.s.act(2)['held'] & abit('CONFIRM', 'CANCEL'), want)
                self.s.xinput(1)
                if vk:
                    self.s.key(vk, False)
                self.tick()
        self.assertEqual(self.s.dll.ILCT_EffectiveBinding(BK['PAD'], PADC['A'], ACT['CONFIRM'], 1),
                         BK['PAD'] | PADC['B'] << 8)
        self.assertEqual(self.s.dll.ILCT_EffectiveBinding(BK['PAD'], PADC['A'], ACT['QUICKENING'], 1),
                         BK['PAD'] | PADC['A'] << 8, 'only confirm and cancel swap')

    def test_a_confirm_held_across_a_decide_swap_is_absorbed_until_released(self):
        self.two_guests()
        self.s.begin()
        self.at(0)
        self.s.xinput(1, buttons=['B'])
        self.tick()
        self.assertEqual(self.s.act(2)['held'], abit('CANCEL'))
        self.s.dll.ILCT_SetDecideSwap(1)                 # B now confirms
        self.tick()
        f = self.s.act(2)
        self.assertEqual((f['held'], f['pressed'], f['absorbed']), (abit('CONFIRM'), 0, abit('CONFIRM')))
        self.s.xinput(1)
        self.tick()
        self.s.xinput(1, buttons=['B'])
        self.tick()
        self.assertEqual(self.s.act(2)['pressed'], abit('CONFIRM'))

    def test_strength_and_active_device(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.s.xinput(1, lx=16384)        # half right: (0.5 - 0.24) / 0.76 after the deadzone
        self.tick()
        f = self.s.act(2)
        self.assertEqual(f['held'], 0, 'below half strength is not held')
        self.assertEqual(f['strength'][ACT['MOVE_RIGHT']], 0)
        self.s.xinput(1, lx=32767)
        self.tick()
        f = self.s.act(2)
        self.assertEqual(f['held'], abit('MOVE_RIGHT'))
        self.assertEqual(f['strength'][ACT['MOVE_RIGHT']], 65535)
        self.assertEqual(f['active'], ACTIVE_PAD)
        self.s.xinput(1)
        self.s.key('D')
        self.tick()
        f = self.s.act(2)
        self.assertEqual((f['held'], f['strength'][ACT['MOVE_RIGHT']], f['active']),
                         (abit('MOVE_RIGHT'), 65535, ACTIVE_KEYBOARD))

    def test_p1_stick_needs_no_move_binding(self):
        self.two_guests()
        self.s.begin()
        self.at(0)
        self.s.xinput(0, lx=32767, ly=32767, buttons=['A'])
        self.tick(2)
        f = self.s.act(1)
        self.assertEqual(f['held'] & abit('MOVE_UP', 'MOVE_DOWN', 'MOVE_LEFT', 'MOVE_RIGHT'), 0)
        self.assertEqual(f['held'], abit('CONFIRM'), 'only the bound A counts')


# =====================================================================================================
# B. Edges and repeat (fake clock)
# =====================================================================================================
@native_test
class RepeatTimingTest(RigCase):
    def setUp(self):
        super().setUp()
        self.two_guests()
        self.s.begin()
        self.at(0)
        self.at(8)

    def hold(self, buttons):
        self.s.xinput(1, buttons=buttons)

    def pulses(self, times, action='MENU_DOWN'):
        """Step at each time; return (pressed times, repeat times)."""
        pressed, repeated = [], []
        for t in times:
            self.at(t)
            f = self.s.act(2)
            if f['pressed'] & abit(action):
                pressed.append(t)
            if f['repeated'] & abit(action):
                repeated.append(t)
            self.assertFalse(f['pressed'] & f['repeated'], 'a press is never also a repeat')
        return pressed, repeated

    def test_normal_repeat_after_250_then_every_100_ms(self):
        self.hold(['DPAD_DOWN'])
        pressed, repeated = self.pulses([100, 349, 350, 351, 449, 450, 549, 550, 650])
        self.assertEqual(pressed, [100])
        self.assertEqual(repeated, [350, 450, 550, 650])

    def test_fast_repeat_after_100_then_every_35_ms(self):
        self.s.dll.ILCT_SetFast(2, 1)
        self.at(16)
        self.hold(['DPAD_DOWN'])
        pressed, repeated = self.pulses([100, 199, 200, 234, 235, 269, 270, 305])
        self.assertEqual(pressed, [100])
        self.assertEqual(repeated, [200, 235, 270, 305])
        self.assertTrue(self.s.act(2)['flags'] & F_FAST)

    def test_turning_fast_on_caps_a_pending_slow_timer(self):
        self.hold(['DPAD_DOWN'])
        self.at(100)                                    # press
        self.at(150)
        self.s.dll.ILCT_SetFast(2, 1)                    # read at the next tick (199)
        _, repeated = self.pulses([199, 233, 234, 268, 269])
        self.assertEqual(repeated, [234, 269], 'capped to 0.035 s after fast came on, not press + 0.250 s')

    def test_cap_after_a_slow_repeat_uses_the_fast_interval(self):
        self.hold(['DPAD_DOWN'])
        self.at(100)
        _, repeated = self.pulses([350])
        self.assertEqual(repeated, [350])
        self.s.dll.ILCT_SetFast(2, 1)
        _, repeated = self.pulses([360, 394, 395, 430])
        self.assertEqual(repeated, [395, 430])

    def test_turning_fast_off_keeps_the_pending_time(self):
        self.s.dll.ILCT_SetFast(2, 1)
        self.at(16)
        self.hold(['DPAD_DOWN'])
        self.at(100)
        self.s.dll.ILCT_SetFast(2, 0)
        _, repeated = self.pulses([150, 199, 200, 299, 300])
        self.assertEqual(repeated, [200, 300], 'the pending fast time stands, then the slow interval')

    def test_no_burst_after_a_stall(self):
        self.hold(['DPAD_DOWN'])
        self.at(100)
        _, repeated = self.pulses([2000, 2008, 2016, 2099, 2100])
        self.assertEqual(repeated, [2000, 2100], 'one pulse after the stall, then the normal cadence')

    def test_release_stops_and_a_new_press_restarts_the_delay(self):
        self.hold(['DPAD_DOWN'])
        self.at(100)
        self.at(350)
        self.hold([])
        self.at(360)
        self.hold(['DPAD_DOWN'])
        pressed, repeated = self.pulses([370, 450, 619, 620])
        self.assertEqual(pressed, [370])
        self.assertEqual(repeated, [620])

    def test_only_menu_directions_repeat(self):
        self.hold(['A', 'RB'])
        pressed, repeated = self.pulses([100, 400, 800, 1600], action='CONFIRM')
        self.assertEqual((pressed, repeated), ([100], []))
        _, repeated = self.pulses([1700, 2000], action='TARGET_NEXT')
        self.assertEqual(repeated, [])

    def test_directions_have_independent_histories(self):
        self.hold(['DPAD_UP'])
        self.at(100)
        self.hold(['DPAD_UP', 'DPAD_DOWN'])
        self.at(200)
        f = self.s.act(2)
        self.assertEqual(f['pressed'], abit('MENU_DOWN'))
        up, down = [], []
        for t in (349, 350, 449, 450, 549, 550):
            self.at(t)
            f = self.s.act(2)
            if f['repeated'] & abit('MENU_UP'):
                up.append(t)
            if f['repeated'] & abit('MENU_DOWN'):
                down.append(t)
        self.assertEqual(up, [350, 450, 550], 'the second direction did not restart the first')
        self.assertEqual(down, [450, 550])


# =====================================================================================================
# C. Hold absorption on every discontinuity
# =====================================================================================================
@native_test
class AbsorptionTest(RigCase):
    def test_start_absorbs_a_held_toggle_and_a_held_button(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.key(VK_F6)
        self.s.xinput(1, buttons=['A'])
        self.s.begin()
        self.at(0)
        self.tick(3)
        f = self.s.act(2)
        self.assertEqual(f['pressed'], 0)
        self.assertEqual(f['held'], abit('TOGGLE_CONTROL', 'CONFIRM'), 'still held: only edges are absorbed')
        self.assertEqual(f['absorbed'], abit('TOGGLE_CONTROL', 'CONFIRM'))
        self.s.key(VK_F6, False)
        self.s.xinput(1)
        self.tick()
        self.s.key(VK_F6)
        self.tick()
        self.assertEqual(self.s.act(2)['pressed'], abit('TOGGLE_CONTROL'))

    def test_reconnect_with_a_held_button_does_not_press(self):
        r = self.s.settings()
        seat_sdl(r, 2, DS4, path='HID#guest', name=b'Guest pad')
        self.s.apply(r)
        self.s.sdl(20, guid=DS4, path='HID#guest', name='Guest pad')
        self.s.begin()
        self.at(0)
        self.tick()
        self.s.sdl_set(20, reading=sdl_reading(buttons=['A']))
        self.tick()
        self.assertEqual(self.s.act(2)['pressed'], abit('CONFIRM'))
        self.s.sdl_remove(20)
        self.tick(3)
        self.assertEqual(self.s.act(2)['held'], 0)
        self.s.sdl(20, guid=DS4, path='HID#guest', name='Guest pad', reading=sdl_reading(buttons=['A']))
        self.tick(3)
        f = self.s.act(2)
        self.assertEqual((f['held'], f['pressed'], f['absorbed']), (abit('CONFIRM'), 0, abit('CONFIRM')))
        self.s.sdl_set(20, reading=sdl_reading())
        self.tick()
        self.s.sdl_set(20, reading=sdl_reading(buttons=['A']))
        self.tick()
        self.assertEqual(self.s.act(2)['pressed'], abit('CONFIRM'))

    def test_a_binding_change_absorbs_that_seat_only(self):
        r = self.two_guests()
        self.s.begin()
        self.at(0)
        self.tick()
        self.s.xinput(1, buttons=['X'])
        self.s.xinput(2, buttons=['X'])
        self.tick()
        self.assertEqual(self.s.act(2)['held'], abit('BATTLE_MENU', 'QUICKENING'))
        r2 = bytearray(r)
        put(r2, ['header', 'generation'], self.s.gen + 1)
        self.s.gen += 1
        binding(r2, 2, 'FLEE', 1, 'PAD', PADC['X'])          # P2's table changes; P3's does not
        self.s.apply(r2)
        self.tick()
        f2, f3 = self.s.act(2), self.s.act(3)
        self.assertEqual(f2['pressed'], 0, 'the new FLEE binding on a held X does not press')
        self.assertEqual(f2['absorbed'], abit('BATTLE_MENU', 'QUICKENING', 'FLEE'))
        self.assertEqual((f3['held'], f3['absorbed']), (abit('BATTLE_MENU'), 0), "P3's table did not change")
        self.s.xinput(1)
        self.s.xinput(2)
        self.tick()
        self.s.xinput(1, buttons=['X'])
        self.tick()
        self.assertEqual(self.s.act(2)['pressed'], abit('BATTLE_MENU', 'QUICKENING', 'FLEE'))

    def test_keyboard_transfer_absorbs_held_keys(self):
        r = self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.s.key('W')
        self.tick()
        self.assertEqual(self.s.act(2)['held'], abit('MOVE_UP'))
        r2 = bytearray(r)
        self.s.gen += 1
        put(r2, ['header', 'generation'], self.s.gen)
        put(r2, ['kbm_seat'], 3)
        self.s.apply(r2)
        self.tick()
        self.assertEqual(self.s.act(2)['held'], 0)
        self.assertEqual(self.s.act(2)['released'], abit('MOVE_UP'))
        f3 = self.s.act(3)
        self.assertEqual((f3['held'], f3['pressed'], f3['absorbed']), (abit('MOVE_UP'), 0, abit('MOVE_UP')))
        self.s.key('W', False)
        self.tick()
        self.s.key('W')
        self.tick()
        self.assertEqual(self.s.act(3)['pressed'], abit('MOVE_UP'))

    def test_focus_return_absorbs_keys_but_keeps_pad_edges_and_repeat(self):
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.at(8)
        self.s.xinput(1, buttons=['DPAD_DOWN'])
        self.s.key('K')                                  # menu down on both
        self.at(100)
        self.assertEqual(self.s.act(2)['pressed'], abit('MENU_DOWN'))
        self.s.focus(False)
        self.at(200)
        self.s.focus(True)
        self.s.key(VK_SPACE)                             # a key held while the window comes back
        self.s.xinput(1, buttons=['DPAD_DOWN', 'A'])     # a pad press on the focus frame
        self.at(300)
        f = self.s.act(2)
        self.assertEqual(f['pressed'], abit('CONFIRM'), 'pad edges on the focus frame count')
        self.assertTrue(f['held'] & abit('MENU_DOWN'), 'still held on the pad')
        self.assertFalse(f['pressed'] & abit('MENU_DOWN'))
        _, rep = self.pulses_down([349, 350, 450])
        self.assertEqual(rep, [350, 450], 'the pad repeat cadence did not restart')
        self.s.xinput(1, buttons=['A'])                  # the pad lets go; K is still held, absorbed
        self.at(460)
        f = self.s.act(2)
        self.assertEqual((f['held'] & abit('MENU_DOWN'), f['absorbed'] & abit('MENU_DOWN')),
                         (abit('MENU_DOWN'), abit('MENU_DOWN')))
        _, rep = self.pulses_down([700, 900])
        self.assertEqual(rep, [], 'an absorbed key never repeats')
        self.s.key(0x62)                                 # numpad 2: another key of the same action presses
        self.at(908)
        self.assertEqual(self.s.act(2)['pressed'] & abit('MENU_DOWN'), abit('MENU_DOWN'))

    def pulses_down(self, times):
        out = [], []
        for t in times:
            self.at(t)
            f = self.s.act(2)
            if f['pressed'] & abit('MENU_DOWN'):
                out[0].append(t)
            if f['repeated'] & abit('MENU_DOWN'):
                out[1].append(t)
        return out

    def test_seat_move_absorbs_and_queues(self):
        self.two_guests()
        self.s.begin()
        self.at(0)
        self.tick()
        self.s.xinput(2, buttons=['B'])
        self.tick()
        self.assertEqual(self.s.act(3)['held'], abit('CANCEL'))
        status, request = self.s.command('MOVE_SEAT_NOW', 3, S.e('character', 'FRAN'))
        self.assertEqual(status, 0)
        self.tick()
        f = self.s.act(3)
        self.assertEqual((f['held'], f['pressed'], f['absorbed']), (abit('CANCEL'), 0, abit('CANCEL')))
        self.assertTrue(f['flags'] & F_RESEEDED)
        events = self.s.results('COMMAND_RESULT')
        self.assertEqual([(e['seat'], e['request'], e['a'], e['b']) for e in events],
                         [(3, request, CMD['MOVE_SEAT_NOW'], S.e('character', 'FRAN') | 0x100)])
        c = self.s.control()
        self.assertEqual((c['move_request'][2], c['move_character'][2]), (request, S.e('character', 'FRAN')))

    def test_an_absorbed_stick_keeps_steering_but_never_presses(self):
        self.two_guests()
        self.s.xinput(1, lx=32767)
        self.s.begin()
        self.at(0)
        self.tick(5)
        f = self.s.act(2)
        self.assertEqual((f['held'], f['pressed'], f['absorbed'], f['strength'][ACT['MOVE_RIGHT']]),
                         (abit('MOVE_RIGHT'), 0, abit('MOVE_RIGHT'), 65535))
        self.s.xinput(1)
        self.tick()
        self.s.xinput(1, lx=32767)
        self.tick()
        f = self.s.act(2)
        self.assertEqual((f['pressed'], f['absorbed']), (abit('MOVE_RIGHT'), 0))


# =====================================================================================================
# D. Pairing proof
# =====================================================================================================
@native_test
class PairingTest(RigCase):
    def setUp(self):
        super().setUp()
        r = self.s.settings()
        seat_xinput(r, 3, 2)                       # P3 holds XInput 2; P1 holds XInput 0 (default)
        self.record = r
        self.s.apply(r)
        self.s.xinput(0)
        self.s.xinput(2)
        self.s.sdl(30, guid=DS5, path='HID#free-pad', name='Free pad')
        self.s.sdl(31, guid=SWITCH, path='HID#other-pad', name='Other pad')
        self.s.begin()
        self.at(0)
        self.tick()

    def press_free(self, down=True):
        self.s.sdl_set(30, reading=sdl_reading(buttons=['A'] if down else []))

    def outcomes(self):
        return [(e['seat'], e['request'], e['outcome'], e['other'], e['slot'], e['b'])
                for e in self.s.results('PAIRING_RESULT')]

    def test_a_guest_pairs_a_free_pad_without_changing_settings(self):
        generation = self.s.settings_generation()
        entry = self.s.entry(name=b'Free pad')
        request = self.s.pair(2, entry)
        self.tick(3)
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(self.s.control()['stage'], 2, 'armed: no A is down')
        self.press_free()
        self.tick()
        self.press_free(False)
        self.until(self.t + 200)
        self.assertEqual(self.outcomes(), [(2, request, PR['READY'], 0, 0xFF, entry)])
        self.assertEqual(self.s.settings_generation(), generation, 'pairing never edits settings')
        self.assertEqual(self.s.control()['stage'], 0)

    def test_a_held_a_never_proves(self):
        self.press_free()                         # held before the start (the menu's own A, say)
        self.tick()
        entry = self.s.entry(name=b'Free pad')
        request = self.s.pair(2, entry)
        self.until(self.t + 1000)
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(self.s.control()['stage'], 1, 'waiting for every A to be released')
        self.press_free(False)
        self.tick()
        self.press_free()
        self.until(self.t + 216)
        self.assertEqual(self.outcomes()[0][2], PR['READY'])
        # Held the whole time: HELD_A at the timeout.
        request = self.s.pair(2, entry)
        start = self.t
        self.until(start + 19990)
        self.assertEqual(self.outcomes(), [])
        self.until(start + 20008)
        self.assertEqual(self.outcomes(), [(2, request, PR['HELD_A'], 0, 0xFF, entry)])

    def test_two_pads_answering_are_refused(self):
        entry = self.s.entry(name=b'Free pad')
        rows = [(0, PR['TWO_PADS']), (104, PR['TWO_PADS']), (192, PR['TWO_PADS']), (216, PR['READY'])]
        for delay, want in rows:
            with self.subTest(delay=delay):
                request = self.s.pair(2, entry)
                self.tick()
                self.press_free()
                start = self.t
                self.tick()
                self.until(start + delay)
                self.s.sdl_set(31, reading=sdl_reading(buttons=['A']))
                self.until(start + 300)
                self.assertEqual([o[2] for o in self.outcomes()], [want])
                self.press_free(False)
                self.s.sdl_set(31, reading=sdl_reading())
                self.tick()
                del request

    def test_player_one_answering_in_the_window_is_refused(self):
        # Player 1's XInput slot answering with the target reads as "the target is Player 1's mirror": locked.
        entry = self.s.entry(name=b'Free pad')
        self.s.pair(2, entry)
        self.tick()
        self.press_free()
        self.s.xinput(0, buttons=['A'])
        self.until(self.t + 300)
        self.assertEqual([(o[2], o[3]) for o in self.outcomes()], [(PR['P1_LOCKED'], 1)])

    def test_two_xinput_slots_answering_are_refused(self):
        self.s.xinput(1)
        self.s.xinput(3)
        self.until(self.t + 300)
        entry = self.s.entry(name=b'Free pad')
        self.s.pair(2, entry)
        self.tick()
        self.press_free()
        self.s.xinput(1, buttons=['A'])
        self.s.xinput(3, buttons=['A'])
        self.until(self.t + 300)
        self.assertEqual([o[2] for o in self.outcomes()], [PR['TWO_PADS']])

    def test_the_xinput_mirror_teaches_the_alias(self):
        # A pad seen twice: its HID side (SDL) and a virtual XInput slot 1 that SDL lists as "XInput#1".
        self.s.xinput(1)
        self.s.sdl(40, guid=DS4, path='HID#mirrored', name='Mirrored pad')
        self.s.sdl(41, guid=XBOX, path='XInput#1', name='Virtual pad')
        self.until(self.t + 300)                  # slot 1 is probed every 0.250 s while absent
        entry = self.s.entry(name=b'Mirrored pad')
        request = self.s.pair(2, entry)
        self.tick()
        self.s.sdl_set(40, reading=sdl_reading(buttons=['A']))
        self.s.xinput(1, buttons=['A'])
        self.s.sdl_set(41, reading=sdl_reading(buttons=['A']))
        self.until(self.t + 216)
        self.assertEqual(self.outcomes(), [(2, request, PR['READY'], 0, 1, entry)])

    def test_an_unknown_mirror_of_player_ones_pad_is_locked(self):
        self.s.sdl(50, guid=DS4, path='HID#p1-side', name='P1 HID side')
        self.s.sdl(51, guid=XBOX, path='XInput#0', name='P1 virtual')
        self.tick(2)
        entry = self.s.entry(name=b'P1 HID side')
        _, entries = self.s.inventory()
        self.assertEqual([e['owner_seat'] for e in entries if e['entry_id'] == entry], [0], 'not yet known as P1')
        request = self.s.pair(2, entry)
        self.tick()
        self.s.sdl_set(50, reading=sdl_reading(buttons=['A']))
        self.s.xinput(0, buttons=['A'])
        self.s.sdl_set(51, reading=sdl_reading(buttons=['A']))
        self.until(self.t + 216)
        self.assertEqual(self.outcomes(), [(2, request, PR['P1_LOCKED'], 1, 0, entry)])

    def test_player_ones_pad_and_another_guests_pad_are_refused_at_once(self):
        p1 = self.s.entry(kind='XINPUT', slot=0)
        p3 = self.s.entry(kind='XINPUT', slot=2)
        r1 = self.s.pair(2, p1)
        self.tick()
        r3 = self.s.pair(2, p3)
        self.tick()
        self.assertEqual(self.outcomes(), [(2, r1, PR['P1_LOCKED'], 1, 0xFF, p1),
                                           (2, r3, PR['OWNED_BY_SEAT'], 3, 0xFF, p3)])

    def test_the_offered_swap_is_one_settings_record(self):
        # P2 on the free pad, P3 on XInput 2. P2 picks P3's pad: refused with P3 named; the Mod Menu asks
        # "Swap with P3?" and, on yes, sends one record that swaps the two devices. Both seats reseed.
        r = bytearray(self.record)
        self.s.gen += 1
        put(r, ['header', 'generation'], self.s.gen)
        seat_sdl(r, 2, DS5, path='HID#free-pad', name=b'Free pad')
        self.s.apply(r)
        self.tick()
        p3 = self.s.entry(kind='XINPUT', slot=2)
        self.s.pair(2, p3)
        self.tick()
        self.assertEqual([(o[2], o[3]) for o in self.outcomes()], [(PR['OWNED_BY_SEAT'], 3)])
        self.s.xinput(2, buttons=['A'])
        self.press_free()
        self.tick(2)
        swapped = bytearray(r)
        self.s.gen += 1
        put(swapped, ['header', 'generation'], self.s.gen)
        seat_on_xinput(swapped, 2, 2)
        seat_sdl(swapped, 3, DS5, path='HID#free-pad', name=b'Free pad')
        self.s.apply(swapped)
        self.tick()
        f2, f3 = self.s.act(2), self.s.act(3)
        self.assertEqual((f2['pressed'], f2['absorbed'] & abit('CONFIRM')), (0, abit('CONFIRM')))
        self.assertEqual((f3['pressed'], f3['absorbed'] & abit('CONFIRM')), (0, abit('CONFIRM')))
        self.assertEqual(self.s.frame(2).slot, 2)

    def test_timeout_after_20_s(self):
        entry = self.s.entry(name=b'Free pad')
        start = self.t
        request = self.s.pair(2, entry)
        self.until(start + 19992)
        self.assertEqual(self.outcomes(), [])
        self.until(start + 20008)
        self.assertEqual(self.outcomes(), [(2, request, PR['TIMEOUT'], 0, 0xFF, entry)])

    def test_disconnect_while_waiting(self):
        entry = self.s.entry(name=b'Free pad')
        request = self.s.pair(2, entry)
        self.tick()
        self.s.sdl_remove(30)
        self.tick(3)
        self.assertEqual(self.outcomes(), [(2, request, PR['DISCONNECTED'], 0, 0xFF, entry)])

    def test_stale_session_and_unknown_entry(self):
        session, _ = self.s.inventory()
        entry = self.s.entry(name=b'Free pad')
        r1 = self.s.pair(2, entry, session=session ^ 0x55)
        r2 = self.s.pair(2, 0x7FFF0000)
        self.tick()
        self.assertEqual(self.outcomes(), [(2, r1, PR['STALE_SESSION'], 0, 0xFF, entry),
                                           (2, r2, PR['NOT_FOUND'], 0, 0xFF, 0x7FFF0000)])

    def test_cancel_and_a_newer_start(self):
        entry = self.s.entry(name=b'Free pad')
        other = self.s.entry(name=b'Other pad')
        r1 = self.s.pair(2, entry)
        self.tick()
        r2 = self.s.pair(3, other)                 # a newer start ends the older session
        self.tick()
        status, r3 = self.s.command('PAIRING_CANCEL', 3)
        self.assertEqual(status, 0)
        self.tick()
        status, r4 = self.s.command('PAIRING_CANCEL', 2)  # nothing to cancel for P2: still answered
        self.tick()
        self.assertEqual(self.outcomes(), [(2, r1, PR['CANCELLED'], 0, 0xFF, entry),
                                           (3, r2, PR['CANCELLED'], 0, 0xFF, other),
                                           (2, r4, PR['CANCELLED'], 0, 0xFF, 0)])
        del r3

    def test_identical_pads_stay_distinct(self):
        self.s.sdl(60, guid=DS4, path='HID#twin-a', name='Twin pad')
        self.s.sdl(61, guid=DS4, path='HID#twin-b', name='Twin pad')
        self.tick(2)
        _, entries = self.s.inventory()
        twins = [e['entry_id'] for e in entries if bytes(e['sdl']['name'][:8]) == b'Twin pad']
        self.assertEqual(len(set(twins)), 2)
        request = self.s.pair(3, twins[1])
        self.tick()
        self.s.sdl_set(60, reading=sdl_reading(buttons=['A']))   # the other twin: not the target
        self.until(self.t + 400)
        self.assertEqual(self.outcomes(), [])
        self.s.sdl_set(60, reading=sdl_reading())
        self.tick()
        self.s.sdl_set(61, reading=sdl_reading(buttons=['A']))
        self.until(self.t + 216)
        self.assertEqual(self.outcomes(), [(3, request, PR['READY'], 0, 0xFF, twins[1])])

    def test_pairing_works_with_every_guest_parked(self):
        status, _ = self.s.command('PARK', 0, 1)
        self.assertEqual(status, 0)
        self.tick()
        self.assertEqual(self.s.control()['parked'], 0b1110)
        _, entries = self.s.inventory()
        self.assertIn(b'Free pad', [bytes(e['sdl']['name'][:8]) for e in entries], 'unassigned pads are listed')
        entry = self.s.entry(name=b'Free pad')
        request = self.s.pair(2, entry)
        self.tick()
        self.press_free()
        self.until(self.t + 216)
        self.assertIn((2, request, PR['READY'], 0, 0xFF, entry), self.outcomes())

    def test_the_seat_being_paired_reads_neutral(self):
        entry = self.s.entry(name=b'Free pad')
        self.s.pair(3, entry)
        self.tick()
        self.s.xinput(2, buttons=['B'])            # P3's current pad
        self.tick()
        f = self.s.act(3)
        self.assertTrue(f['flags'] & F_SUPPRESSED)
        self.assertEqual((f['held'], f['pressed']), (0, 0))
        self.s.command('PAIRING_CANCEL', 3)
        self.tick()
        f = self.s.act(3)
        self.assertFalse(f['flags'] & F_SUPPRESSED)
        self.assertEqual((f['held'], f['pressed'], f['absorbed']), (abit('CANCEL'), 0, abit('CANCEL')))

    def test_service_stop_cancels(self):
        entry = self.s.entry(name=b'Free pad')
        request = self.s.pair(2, entry)
        self.tick()
        self.s.end()
        self.assertEqual(self.outcomes(), [(2, request, PR['CANCELLED'], 0, 0xFF, entry)])
        status, _ = self.s.command('PAIRING_START', 2, 1, entry)
        self.assertEqual(status, E['NOT_READY'])

    def test_a_full_queue_answers_busy(self):
        entry = self.s.entry(name=b'Free pad')
        for i in range(16):
            self.assertEqual(self.s.command('PARK', 2, i & 1)[0], 0)
        status, _ = self.s.command('PAIRING_START', 2, 1, entry)
        self.assertEqual(status, E['BUSY'])
        st = S.decode('ilc_status', self.s.bridge.call(OP['GET_STATUS'])[1])
        self.assertEqual(st['last_reject_code'], E['BUSY'])
        self.tick()
        self.assertEqual(len(self.s.results('COMMAND_RESULT')), 16)


@native_test
class PlayerOneIdentifyTest(RigCase):
    def setUp(self):
        super().setUp()
        self.s.apply(self.s.settings())           # P1 on XInput 0
        self.s.xinput(0)
        self.s.sdl(70, guid=DS4, path='HID#elsewhere', name='Elsewhere pad')
        self.s.begin()
        self.at(0)
        self.tick()

    def test_player_one_identifies_its_own_pad_without_reassigning(self):
        generation = self.s.settings_generation()
        entry = self.s.entry(kind='XINPUT', slot=0)
        request = self.s.pair(1, entry)
        self.tick()
        self.s.xinput(0, buttons=['A'])
        self.until(self.t + 216)
        events = self.s.results('PAIRING_RESULT')
        self.assertEqual([(e['outcome'], e['request'], e['b']) for e in events], [(PR['IDENTIFIED'], request, entry)])
        self.assertEqual(self.s.settings_generation(), generation)
        self.assertEqual(self.s.control()['override'], 0)

    def test_a_wrong_pad_cannot_answer_for_player_one(self):
        entry = self.s.entry(kind='XINPUT', slot=0)
        start = self.t
        request = self.s.pair(1, entry)
        self.tick()
        self.s.sdl_set(70, reading=sdl_reading(buttons=['A']))   # another pad: never the proof
        self.until(start + 20008)
        events = self.s.results('PAIRING_RESULT')
        self.assertEqual([(e['outcome'], e['request']) for e in events], [(PR['TIMEOUT'], request)])


# =====================================================================================================
# E. Player 1's guarded pad change
# =====================================================================================================
@native_test
class PlayerOneChangeTest(RigCase):
    def setUp(self):
        super().setUp()
        self.base = self.s.settings()             # P1 on XInput 0
        self.s.apply(self.base)
        self.s.xinput(0)
        self.s.sdl(80, guid=DS5, path='HID#new-pad', name='New pad')
        self.s.begin()
        self.at(0)
        self.tick()
        self.entry = self.s.entry(name=b'New pad')

    def press_new(self, down=True):
        self.s.sdl_set(80, reading=sdl_reading(buttons=['A'] if down else []))

    def outcomes(self):
        return [e['outcome'] for e in self.s.results('PAIRING_RESULT')]

    def provisional(self):
        request = self.s.pair(1, self.entry)
        self.tick()
        self.press_new()
        self.until(self.t + 216)
        self.assertEqual(self.outcomes(), [PR['PROVISIONAL']])
        self.tick()
        f = self.s.frame(1)
        self.assertEqual(f.source, SRC_SDL, 'the new pad drives Player 1 now')
        self.assertEqual(self.s.control()['override'], 1)
        return request

    def test_confirm_then_commit(self):
        self.provisional()
        self.s.xinput(0, buttons=['DPAD_UP'])     # the old pad no longer drives Player 1
        self.tick()
        self.assertEqual(self.s.frame(1).held & (1 << (PADC['DPAD_UP'] - 1)), 0)
        self.press_new(False)
        self.tick()
        self.press_new()
        self.tick()
        self.assertEqual(self.outcomes(), [PR['CONFIRMED']])
        committed = bytearray(self.base)
        self.s.gen += 1
        put(committed, ['header', 'generation'], self.s.gen)
        seat_sdl(committed, 1, DS5, path='HID#new-pad', name=b'New pad')
        self.s.apply(committed)
        self.tick()
        self.assertEqual(self.s.control()['override'], 0, 'the settings took over')
        self.until(self.t + 6000)
        self.assertEqual(self.outcomes(), [], 'no revert after the commit')
        self.assertEqual(self.s.frame(1).source, SRC_SDL)

    def test_no_confirm_reverts_after_10_s(self):
        self.provisional()
        start = self.t
        self.press_new(False)
        self.until(start + 9900)
        self.assertEqual(self.outcomes(), [])
        self.until(start + 10100)
        self.assertEqual(self.outcomes(), [PR['REVERTED']])
        self.tick()
        self.assertEqual((self.s.frame(1).source, self.s.frame(1).slot), (SRC_XINPUT, 0))
        self.assertEqual(self.s.control()['override'], 0)

    def test_a_held_a_is_not_a_confirm(self):
        self.provisional()
        self.until(self.t + 10100)                 # A never released on the new pad
        self.assertEqual(self.outcomes(), [PR['REVERTED']])

    def test_confirm_without_a_commit_reverts_after_5_s(self):
        self.provisional()
        self.press_new(False)
        self.tick()
        self.press_new()
        self.tick()
        self.assertEqual(self.outcomes(), [PR['CONFIRMED']])
        start = self.t
        self.until(start + 4900)
        self.assertEqual(self.outcomes(), [])
        self.until(start + 5100)
        self.assertEqual(self.outcomes(), [PR['REVERTED']])
        self.tick()
        self.assertEqual(self.s.frame(1).source, SRC_XINPUT)

    def test_cancel_or_unplug_during_the_change_reverts(self):
        for how in ('cancel', 'unplug'):
            with self.subTest(how=how):
                self.provisional()
                if how == 'cancel':
                    self.s.command('PAIRING_CANCEL', 1)
                    want = PR['CANCELLED']
                else:
                    self.s.sdl_remove(80)
                    want = PR['DISCONNECTED']
                self.tick(2)
                self.assertEqual(self.outcomes(), [want])
                self.tick()
                self.assertEqual(self.s.frame(1).source, SRC_XINPUT)
                self.assertEqual(self.s.control()['override'], 0)
                if how == 'unplug':
                    break
                self.press_new(False)
                self.tick()

    def test_a_pad_held_by_a_guest_is_refused_for_player_one(self):
        r = bytearray(self.base)
        self.s.gen += 1
        put(r, ['header', 'generation'], self.s.gen)
        seat_sdl(r, 3, DS5, path='HID#new-pad', name=b'New pad')
        self.s.apply(r)
        self.tick()
        self.s.pair(1, self.entry)
        self.tick()
        events = self.s.results('PAIRING_RESULT')
        self.assertEqual([(e['outcome'], e['other']) for e in events], [(PR['OWNED_BY_SEAT'], 3)])


# =====================================================================================================
# F. Capture
# =====================================================================================================
@native_test
class CaptureTest(RigCase):
    def setUp(self):
        super().setUp()
        self.two_guests(kbm=2)
        self.s.keyboard()
        self.s.begin()
        self.at(0)
        self.tick()

    def captures(self):
        return [(e['seat'], e['outcome'], (e['a'] >> 8) & 0xFF, (e['a'] >> 16) & 0xFF, e['b'] & 0xFF, e['b'] >> 8)
                for e in self.s.results('CAPTURE_RESULT')]

    def test_the_opening_press_must_be_released_first(self):
        self.s.xinput(1, buttons=['A'])            # the press that opened the dialog
        self.tick()
        self.s.command('CAPTURE_START', 2, ACT['CONFIRM'], 3)
        self.tick(5)
        self.assertEqual(self.captures(), [])
        self.assertTrue(self.s.act(2)['flags'] & F_SUPPRESSED)
        self.s.xinput(1, buttons=['A', 'X'])       # a fresh X while A is still held
        self.tick()
        self.assertEqual(self.captures(), [(2, CO['CAPTURED'], ACT['CONFIRM'], 3, BK['PAD'], PADC['X'])])
        f = self.s.act(2)
        self.assertEqual(f['pressed'], 0, 'the captured press does not leak')
        self.assertFalse(f['flags'] & F_SUPPRESSED)

    def test_a_capture_needs_a_firm_press_after_a_rest(self):
        self.s.xinput(1, rt=160)                   # 0.63: resting on the trigger at the start
        self.tick()
        self.s.command('CAPTURE_START', 2, ACT['FLEE'], 2)
        self.tick(2)
        self.s.xinput(1, rt=230)                   # 0.90, but never released below 0.55
        self.tick(3)
        self.assertEqual(self.captures(), [])
        self.s.xinput(1, rt=120)                   # 0.47: released
        self.tick()
        self.s.xinput(1, rt=170)                   # 0.67: held, not firm
        self.tick(3)
        self.assertEqual(self.captures(), [])
        self.s.xinput(1, rt=200)                   # 0.78: firm
        self.tick()
        self.assertEqual(self.captures(), [(2, CO['CAPTURED'], ACT['FLEE'], 2, BK['PAD'], PADC['RT'])])

    def test_keys_only_for_the_keyboard_seat(self):
        self.s.command('CAPTURE_START', 3, ACT['FLEE'], 0)
        self.tick()
        self.s.key('G')
        self.tick(3)
        self.assertEqual(self.captures(), [], 'P3 does not own the keyboard')
        self.s.key('G', False)
        self.s.command('CAPTURE_CANCEL', 3)
        self.tick()
        self.assertEqual([c[1] for c in self.captures()], [CO['CANCELLED']])
        rows = [([VK_SHIFT, VK_LSHIFT], BK['KEY'], VK_LSHIFT), ([1], BK['MOUSE'], 1), ([ord('G')], BK['KEY'], ord('G'))]
        for keys, kind, code in rows:
            with self.subTest(keys=keys):
                self.s.command('CAPTURE_START', 2, ACT['FLEE'], 1)
                self.tick(2)
                for vk in keys:
                    self.s.key(vk)
                self.tick()
                self.assertEqual(self.captures(), [(2, CO['CAPTURED'], ACT['FLEE'], 1, kind, code)])
                for vk in keys:
                    self.s.key(vk, False)
                self.tick()

    def test_a_key_held_while_unfocused_must_be_released(self):
        self.s.command('CAPTURE_START', 2, ACT['PAUSE'], 0)
        self.tick()
        self.s.focus(False)
        self.tick()
        self.s.key('P')
        self.tick()
        self.s.focus(True)
        self.tick(3)
        self.assertEqual(self.captures(), [])
        self.s.key('P', False)
        self.tick()
        self.s.key('P')
        self.tick()
        self.assertEqual(self.captures(), [(2, CO['CAPTURED'], ACT['PAUSE'], 0, BK['KEY'], ord('P'))])

    def test_no_input_timeout_and_supersede(self):
        status, _ = self.s.command('CAPTURE_START', 4, ACT['FLEE'], 0)   # P4 cannot play in this release
        self.assertEqual(status, E['BAD_FIELD'])
        r = self.s.settings()                      # P3 without a pad and without the keyboard
        seat_xinput(r, 2, 1)
        put(r, ['kbm_seat'], 2)
        self.s.apply(r)
        self.tick()
        status, _ = self.s.command('CAPTURE_START', 3, ACT['FLEE'], 0)
        self.assertEqual(status, 0)
        self.tick()
        self.assertEqual([c[1] for c in self.captures()], [CO['NO_INPUT']])
        self.two_guests(kbm=2)
        self.tick()
        self.s.command('CAPTURE_START', 2, ACT['FLEE'], 0)
        self.tick()
        self.s.command('CAPTURE_START', 3, ACT['FLEE'], 2)                  # supersedes P2's
        self.tick()
        self.assertEqual([(c[0], c[1]) for c in self.captures()], [(2, CO['CANCELLED'])])
        start = self.t
        self.until(start + 19990)
        self.assertEqual(self.captures(), [])
        self.until(start + 20010)
        self.assertEqual([(c[0], c[1]) for c in self.captures()], [(3, CO['TIMEOUT'])])

    def test_p1_capture_uses_p1_actions_only(self):
        status, _ = self.s.command('CAPTURE_START', 1, ACT['FLEE'], 0)
        self.assertEqual(status, E['BAD_FIELD'], 'P1 has no flee action')
        self.s.command('CAPTURE_START', 1, ACT['PARTY'], 1)
        self.tick()
        self.s.xinput(0, buttons=['BACK'])
        self.tick()
        self.assertEqual(self.captures(), [(1, CO['CAPTURED'], ACT['PARTY'], 1, BK['PAD'], PADC['BACK'])])


# =====================================================================================================
# G. Park
# =====================================================================================================
@native_test
class ParkTest(RigCase):
    def test_park_is_live_and_settings_take_over_again(self):
        r = self.two_guests()
        self.s.begin()
        self.at(0)
        self.tick()
        status, request = self.s.command('PARK', 2, 1)
        self.assertEqual(status, 0)
        self.tick()
        self.assertEqual(self.s.frame(2).source, SRC_NONE)
        self.assertEqual(self.s.frame(3).source, SRC_XINPUT)
        self.assertEqual([(e['seat'], e['request'], e['b']) for e in self.s.results('COMMAND_RESULT')],
                         [(2, request, 0b0010)])
        # An unrelated settings change keeps the live park; one that changes P2's parked field takes over.
        r2 = bytearray(r)
        self.s.gen += 1
        put(r2, ['header', 'generation'], self.s.gen)
        put(r2, ['colour_mode'], 1)
        self.s.apply(r2)
        self.tick()
        self.assertEqual(self.s.control()['parked'], 0b0010)
        r3 = bytearray(r2)
        self.s.gen += 1
        put(r3, ['header', 'generation'], self.s.gen)
        put(r3, ['seats', 1, 'parked'], 1)
        self.s.apply(r3)
        self.tick()
        r4 = bytearray(r3)
        self.s.gen += 1
        put(r4, ['header', 'generation'], self.s.gen)
        put(r4, ['seats', 1, 'parked'], 0)
        self.s.apply(r4)
        self.tick()
        self.assertEqual(self.s.control()['parked'], 0, 'the settings decide again')

    def test_unpark_with_a_held_button_does_not_press(self):
        self.two_guests()
        self.s.begin()
        self.at(0)
        self.s.command('PARK', 0, 1)
        self.tick()
        self.assertEqual(self.s.control()['parked'], 0b1110, 'every guest, never Player 1')
        self.s.xinput(1, buttons=['A'])
        self.tick()
        self.s.command('PARK', 0, 0)
        self.tick(2)
        f = self.s.act(2)
        self.assertEqual((f['held'], f['pressed'], f['absorbed']), (abit('CONFIRM'), 0, abit('CONFIRM')))
        self.assertEqual(self.s.command('PARK', 1, 1)[0], E['BAD_FIELD'], 'Player 1 cannot be parked')


# =====================================================================================================
# H. Replay
# =====================================================================================================
@native_test
class ReplayTest(unittest.TestCase):
    def scenario(self, rig: Rig, record: bool):
        r = rig.settings()
        seat_xinput(r, 2, 1)
        rig.apply(r)
        rig.xinput(0)
        rig.xinput(1)
        rig.sdl(30, guid=DS5, path='HID#free-pad', name='Free pad')
        rig.begin(record=record)
        frames, events = [], []
        t = 0
        script = {16: ('DPAD_DOWN',), 400: (), 600: ('A',), 616: ()}  # P2's A after the proof window
        for step in range(120):
            t = step * 8
            if t in script:
                rig.xinput(1, buttons=list(script[t]))
            if t == 200:
                session, entries = rig.inventory()
                entry = [e['entry_id'] for e in entries if e['kind'] == S.e('device_kind', 'SDL')][0]
                rig.pair(3, entry, session=session)
            if t == 240:
                rig.sdl_set(30, reading=sdl_reading(buttons=['A']))
            rig.step(t)
            frames.append(b''.join(rig.act(seat)['raw'] for seat in (1, 2, 3, 4)))
            events += [(e['kind'], e['seat'], e['a'], e['b']) for e in rig.events()]
        return frames, events

    def test_a_recorded_run_replays_to_the_same_actions_and_events(self):
        live = Rig(fresh_test_dll())
        frames, events = self.scenario(live, record=True)
        size = live.dll.ILCT_RecordingSize()
        transcript = ctypes.create_string_buffer(size)
        live.dll.ILCT_RecordingCopy(transcript, size)
        live.end()
        self.assertEqual([e[:3] for e in events if e[0] == EV['PAIRING_RESULT']],
                         [(EV['PAIRING_RESULT'], 3, PR['READY'] | 0xFF0000)])

        replay = Rig(fresh_test_dll())
        replay.dll.ILCT_WorldReset()
        r = replay.settings()
        seat_xinput(r, 2, 1)
        replay.apply(r)
        self.assertEqual(replay.dll.ILCT_BeginReplay(transcript, size, 0x5EED), 1)
        replay_frames, replay_events = [], []
        for step in range(120):
            t = step * 8
            if t == 200:
                session, entries = replay.inventory()
                entry = [e['entry_id'] for e in entries if e['kind'] == S.e('device_kind', 'SDL')][0]
                replay.pair(3, entry, session=session)
            replay.step(t)
            replay_frames.append(b''.join(replay.act(seat)['raw'] for seat in (1, 2, 3, 4)))
            replay_events += [(e['kind'], e['seat'], e['a'], e['b']) for e in replay.events()]
        self.assertEqual(replay.dll.ILCT_ReplayMismatches(), 0)
        self.assertEqual(replay.dll.ILCT_ReplayRemaining(), 0)
        replay.end()
        self.assertEqual(replay_events, events)
        mismatches = sum(1 for a, b in zip(frames, replay_frames) if a != b)
        self.assertEqual(mismatches, 0)

    def test_pairing_adds_no_device_call(self):
        # The proof reads what the core already read: the same run with and without a pairing session makes
        # exactly the same device calls.
        sizes = []
        for pair in (False, True):
            rig = Rig(fresh_test_dll())
            r = rig.settings()
            rig.apply(r)
            rig.xinput(0)
            rig.sdl(30, guid=DS5, path='HID#free-pad', name='Free pad')
            rig.begin(record=True)
            for step in range(60):
                if pair and step == 5:
                    session, entries = rig.inventory()
                    rig.pair(2, entries[-1]['entry_id'], session=session)
                if step == 20:
                    rig.sdl_set(30, reading=sdl_reading(buttons=['A']))
                rig.step(step * 8)
            size = rig.dll.ILCT_RecordingSize()
            buf = ctypes.create_string_buffer(size)
            rig.dll.ILCT_RecordingCopy(buf, size)
            sizes.append(buf.raw)
            rig.end()
        self.assertEqual(sizes[0], sizes[1])


# =====================================================================================================
# I. Static checks
# =====================================================================================================
class StaticTest(unittest.TestCase):
    def test_seat_commands_and_actions_never_touch_a_device(self):
        for name in ('actions.cpp', 'seat_control.cpp', 'actions.h', 'seat_control.h'):
            text = (COOP / 'src' / 'input' / name).read_text(encoding='utf-8')
            with self.subTest(file=name):
                for pattern in (r'get_state\s*\(', r'->\s*(open|read|pump|count|close|attached|device_info|handle_info)\s*\(',
                                r'GetAsyncKeyState', r'windows\.h', r'XInputGetState', r'SDL_\w+\s*\('):
                    self.assertIsNone(re.search(pattern, text), pattern)

    def test_outcome_enums_are_in_the_schema_and_the_lua_data(self):
        lua = (COOP.parents[1] / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop' /
               'schema_data.lua').read_text(encoding='utf-8')
        for enum in ('pairing_outcome', 'capture_outcome'):
            self.assertIn(enum, S.enums)
            self.assertIn(f'{enum} = {{', lua)
        self.assertEqual(PR['READY'], 1)
        self.assertEqual(max(PR.values()), len(PR), 'pairing outcomes are dense from 1')
        self.assertEqual(max(CO.values()), len(CO))

    def test_timings(self):
        text = (COOP / 'src' / 'input' / 'actions.h').read_text(encoding='utf-8')
        for name, value in (('REPEAT_FIRST_US', 250000), ('REPEAT_NEXT_US', 100000), ('FAST_FIRST_US', 100000),
                            ('FAST_NEXT_US', 35000)):
            self.assertRegex(text, rf'{name}\s*=\s*{value};')
        text = (COOP / 'src' / 'input' / 'seat_control.h').read_text(encoding='utf-8')
        for name, value in (('PAIRING_TIMEOUT_US', 20000000), ('PROOF_WINDOW_US', 200000),
                            ('CONFIRM_WINDOW_US', 10000000), ('COMMIT_WINDOW_US', 5000000),
                            ('CAPTURE_TIMEOUT_US', 20000000)):
            self.assertRegex(text, rf'{name}\s*=\s*{value};')


if __name__ == '__main__':
    unittest.main()
