"""Input service of the co-op module, through the real code (no game run, nothing deployed).

Two builds are used:
* the shipped DLL (native/coop/build.py): static checks only (imports, strings, exports, assertion call sites);
* the host-test DLL (build.py's build_test_dll): the same sources plus testsupport/, with ILCT_* exports that
  drive the input service tick by tick on a scripted device world, record every device call, and replay a
  recording. GET_DEVICES is read through the real ILC_Bridge of that DLL.

The tables below restate the module's input rules (device reading, identities and aliases, hold absorption;
see src/input/input_core.h) as pass/fail rows. Times are in milliseconds of the service
clock; one service tick is one step.

ILC_REQUIRE_NATIVE=1 makes a missing toolchain a failure instead of a skip.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

from coop_native_support import (CANARY, COMPILER, COOP, SCHEMA, Bridge, built_dll, coop_build, manifest,
                                 native_test)

S = SCHEMA
OP = S.enums['op']
E = S.enums['error']
PAD = S.enums['pad_control']  # name -> code 1..24
BIT = {name: 1 << (code - 1) for name, code in PAD.items()}
XI_OK, XI_GONE = 0, 1167
XB = {'DPAD_UP': 0x1, 'DPAD_DOWN': 0x2, 'DPAD_LEFT': 0x4, 'DPAD_RIGHT': 0x8, 'START': 0x10, 'BACK': 0x20,
      'L3': 0x40, 'R3': 0x80, 'LB': 0x100, 'RB': 0x200, 'A': 0x1000, 'B': 0x2000, 'X': 0x4000, 'Y': 0x8000}
# SDL_GameControllerButton numbering.
SDLB = {'A': 0, 'B': 1, 'X': 2, 'Y': 3, 'BACK': 4, 'GUIDE': 5, 'START': 6, 'L3': 7, 'R3': 8, 'LB': 9, 'RB': 10,
        'DPAD_UP': 11, 'DPAD_DOWN': 12, 'DPAD_LEFT': 13, 'DPAD_RIGHT': 14}
SRC_NONE, SRC_XINPUT, SRC_SDL = 0, 1, 2
F_CONNECTED, F_ASSIGNED, F_BRIDGED, F_RESEEDED, F_VIA_ALIAS = 0x01, 0x02, 0x04, 0x08, 0x10
FRAME = struct.Struct('<IIQBBBBIIII24h2h2hHBBhhhh8s')
FRAME_FIELDS = ('seq', 'gen', 'time', 'seat', 'source', 'slot', 'flags', 'held', 'absorbed', 'pressed', 'released')
DS4 = bytes.fromhex('030000004c050000cc09000000006800')
DS5 = bytes.fromhex('030000004c050000e60c000000006800')
SWITCH = bytes.fromhex('030000007e0500000920000000006800')
XBOX = bytes.fromhex('030000005e0400008e02000000007800')


class XGamepad(ctypes.Structure):
    _pack_ = 2
    _fields_ = [('buttons', ctypes.c_uint16), ('lt', ctypes.c_uint8), ('rt', ctypes.c_uint8),
                ('lx', ctypes.c_int16), ('ly', ctypes.c_int16), ('rx', ctypes.c_int16), ('ry', ctypes.c_int16)]


class SdlReading(ctypes.Structure):
    _fields_ = [('buttons', ctypes.c_uint32), ('axes', ctypes.c_int16 * 6)]


class SdlDevice(ctypes.Structure):
    _fields_ = [('instance_id', ctypes.c_int32), ('guid', ctypes.c_uint8 * 16), ('is_controller', ctypes.c_uint8),
                ('info_fails', ctypes.c_uint8), ('open_fails', ctypes.c_uint8), ('attached', ctypes.c_uint8),
                ('read_fails', ctypes.c_uint8), ('omitted', ctypes.c_uint8), ('reserved', ctypes.c_uint8 * 2),
                ('path', ctypes.c_char * 260), ('name', ctypes.c_char * 128), ('reading', SdlReading)]


def sdl_reading(buttons=(), lx=0, ly=0, rx=0, ry=0, lt=0, rt=0) -> SdlReading:
    r = SdlReading()
    r.buttons = sum(1 << SDLB[b] for b in buttons)
    for i, v in enumerate((lx, ly, rx, ry, lt, rt)):
        r.axes[i] = v
    return r


def uid_of(path: str) -> bytes:
    return hashlib.sha256(path.lower().encode()).digest()[:16]


class Frame(dict):
    def __getattr__(self, name):
        return self[name]


def decode_frame(raw: bytes) -> Frame:
    v = FRAME.unpack(raw)
    f = Frame(zip(FRAME_FIELDS, v[:11]))
    f['strength'] = [x & 0xFFFF for x in v[11:35]]
    f['left'] = v[35:37]
    f['right'] = v[37:39]
    f['pad'] = v[39:46]
    f['raw'] = raw
    return f


_test_dll: dict = {}
_copies = iter(range(1, 1 << 30))


def _test_dll_path() -> Path:
    if 'path' not in _test_dll:
        built_dll()  # same toolchain check and SDL2 cache as the shipped build
        temp = Path(tempfile.mkdtemp(prefix='ilc-input-test-'))
        _test_dll['path'] = coop_build.build_test_dll(COMPILER.resolve(), temp)
    return _test_dll['path']


def fresh_test_dll() -> ctypes.WinDLL:
    source = _test_dll_path()
    copy = source.parent / f'ilc-input-copy-{next(_copies)}.dll'
    shutil.copyfile(source, copy)
    return ctypes.WinDLL(str(copy))


class Service:
    """The test DLL: scripted world, manual ticks, frames, settings, GET_DEVICES."""

    def __init__(self, dll):
        self.dll = dll
        self.bridge = Bridge(dll)
        d = dll
        d.ILCT_Step.argtypes = [ctypes.c_uint64]
        d.ILCT_XInputSet.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(XGamepad)]
        d.ILCT_SdlPut.argtypes = [ctypes.POINTER(SdlDevice)]
        d.ILCT_SdlRemove.argtypes = [ctypes.c_int32]
        d.ILCT_SdlMove.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        d.ILCT_SdlGlobal.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32]
        d.ILCT_Begin.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_BeginReplay.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_RecordingCopy.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.ILCT_ConfigureFromSettings.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        d.ILCT_Deadzone.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.POINTER(ctypes.c_float)]
        d.ILCT_Sanitize.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        d.ILCT_Sha256.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p]
        d.ILCT_Uid.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        d.ILCT_StopLive.argtypes = [ctypes.c_uint32]
        assert d.ILCT_FrameSize() == FRAME.size == 112
        assert d.ILCT_WorldDeviceSize() == ctypes.sizeof(SdlDevice)
        d.ILCT_WorldReset()
        self.devices: dict[int, SdlDevice] = {}
        self.gen = 0

    # ---- world -------------------------------------------------------------------------------------------
    def xinput(self, slot, rc=XI_OK, buttons=(), lt=0, rt=0, lx=0, ly=0, rx=0, ry=0, packet=1):
        pad = XGamepad(sum(XB[b] for b in buttons), lt, rt, lx, ly, rx, ry)
        self.dll.ILCT_XInputSet(slot, rc, packet, ctypes.byref(pad))

    def sdl(self, instance_id, guid=DS4, path='', name='Pad', reading=None, **flags):
        d = self.devices.get(instance_id) or SdlDevice()
        d.instance_id = instance_id
        ctypes.memmove(d.guid, guid, 16)
        d.is_controller = flags.get('is_controller', 1)
        d.info_fails = flags.get('info_fails', 0)
        d.open_fails = flags.get('open_fails', 0)
        d.attached = flags.get('attached', 1)
        d.read_fails = flags.get('read_fails', 0)
        d.omitted = flags.get('omitted', 0)
        d.path = path.encode() if isinstance(path, str) else path
        d.name = name.encode() if isinstance(name, str) else name
        d.reading = reading if reading is not None else SdlReading()
        self.devices[instance_id] = d
        assert self.dll.ILCT_SdlPut(ctypes.byref(d)) >= 0

    def sdl_set(self, instance_id, **changes):
        d = self.devices[instance_id]
        for k, v in changes.items():
            setattr(d, k, v)
        assert self.dll.ILCT_SdlPut(ctypes.byref(d)) >= 0

    def sdl_remove(self, instance_id):
        self.devices.pop(instance_id, None)
        self.dll.ILCT_SdlRemove(instance_id)

    # ---- service ------------------------------------------------------------------------------------------
    def begin(self, xinput=True, sdl=True, record=False, session=0x5EED):
        self.dll.ILCT_Begin((1 if xinput else 0) | (2 if sdl else 0) | (4 if record else 0), session)

    def end(self):
        self.dll.ILCT_End()

    def step(self, ms: float):
        self.dll.ILCT_Step(int(round(ms * 1000)))

    def run(self, start_ms, end_ms, every_ms=8):
        t = start_ms
        while t <= end_ms:
            self.step(t)
            t += every_ms

    def frame(self, seat) -> Frame | None:
        raw = ctypes.create_string_buffer(112)
        if not self.dll.ILCT_Frame(seat, raw):
            return None
        return decode_frame(raw.raw)

    def status(self) -> dict:
        out = (ctypes.c_uint32 * 14)()
        ok = self.dll.ILCT_Status(out)
        names = ('state', 'backends', 'issues', 'session', 'seq', 'inventory', 'ticks', 'xinput_polls',
                 'sdl_unreadable', 'sdl_open_failures', 'sdl_read_failures', 'sdl_pump_failures', 'sdl_retired',
                 'seat_conflicts')
        return dict(zip(names, list(out))) | {'ok': ok}

    def open_handles(self) -> int:
        return self.dll.ILCT_SdlOpenHandles()

    # ---- settings ----------------------------------------------------------------------------------------
    def settings(self) -> bytearray:
        self.gen += 1
        record = bytearray(S.defaults())
        put(record, ['header', 'generation'], self.gen)
        return record

    def apply(self, record: bytearray):
        """SET_SETTINGS (validated by the bridge), the safe point, then the service reads the applied record."""
        status, _ = self.bridge.call(OP['SET_SETTINGS'], bytes(record))
        if status < 0:
            raise AssertionError(f'settings rejected: {status}')
        self.bridge.call(OP['TEST_SAFE_POINT'])
        assert self.dll.ILCT_ConfigureFromApplied() == 1

    def apply_unvalidated(self, record: bytearray):
        buf = ctypes.create_string_buffer(bytes(record), len(record))
        assert self.dll.ILCT_ConfigureFromSettings(buf, len(record)) == 1

    def devices_reply(self, out_cap=4096) -> tuple[int, dict | None, list]:
        status, out = self.bridge.call(OP['GET_DEVICES'], out_cap=out_cap)
        if status < 0:
            return status, None, []
        full = out + bytes(S.size('ilc_devices_reply') - len(out))
        reply = S.decode('ilc_devices_reply', full)
        return status, reply, reply['entries'][:reply['count']]


def put(record: bytearray, path: list, value: int):
    offset, field, _ = S.locate('ilc_settings', path)
    fmt = {1: 'B', 2: 'H', 4: 'I'}[field['elemSize']]
    struct.pack_into('<' + fmt, record, offset, value)


def put_identity(record: bytearray, prefix: list, guid: bytes, path: str | None = None, ordinal=0, name=b'Pad'):
    offset, _, _ = S.locate('ilc_settings', prefix + ['guid'])
    record[offset:offset + 16] = guid
    put(record, prefix + ['ordinal'], ordinal)
    if path:
        put(record, prefix + ['has_uid'], 1)
        o, _, _ = S.locate('ilc_settings', prefix + ['uid'])
        record[o:o + 16] = uid_of(path)
    put(record, prefix + ['name_len'], len(name))
    o, _, _ = S.locate('ilc_settings', prefix + ['name'])
    record[o:o + len(name)] = name


def seat_xinput(record, seat, slot):
    put(record, ['seats', seat - 1, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
    put(record, ['seats', seat - 1, 'device', 'xinput_slot'], slot)


def seat_sdl(record, seat, guid, path=None, ordinal=0, name=b'Pad'):
    put(record, ['seats', seat - 1, 'device', 'kind'], S.e('device_kind', 'SDL'))
    put_identity(record, ['seats', seat - 1, 'device', 'sdl'], guid, path, ordinal, name)


def add_alias(record, slot, guid, path=None, ordinal=0, name=b'Pad'):
    n = struct.unpack_from('<B', record, S.locate('ilc_settings', ['alias_count'])[0])[0]
    put(record, ['alias_count'], n + 1)
    put(record, ['aliases', n, 'xinput_slot'], slot)
    put_identity(record, ['aliases', n, 'sdl'], guid, path, ordinal, name)


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.s = Service(fresh_test_dll())

    def tearDown(self):
        self.s.end()

    def assertRow(self, frame, row: dict, label=''):
        for key, want in row.items():
            if key == 'held_names':
                got = sorted(n for n, b in BIT.items() if frame.held & b)
                self.assertEqual(got, sorted(want), f'{label}: held')
            elif key in ('pressed', 'absorbed', 'released', 'held'):
                want_bits = sum(BIT[n] for n in want) if isinstance(want, (list, tuple, set)) else want
                self.assertEqual(frame[key], want_bits, f'{label}: {key}')
            elif key == 'flags_on':
                self.assertEqual(frame.flags & want, want, f'{label}: flags {frame.flags:#x} lack {want:#x}')
            elif key == 'flags_off':
                self.assertEqual(frame.flags & want, 0, f'{label}: flags {frame.flags:#x} have {want:#x}')
            else:
                self.assertEqual(frame[key], want, f'{label}: {key}')


# =====================================================================================================
# A. Normalisation: radial deadzone 0.24 with rescale, full diagonals, triggers
# =====================================================================================================
@native_test
class NormalisationTest(ServiceCase):
    def deadzone(self, x, y, dz=0.24):
        out = (ctypes.c_float * 2)()
        self.s.dll.ILCT_Deadzone(x, y, dz, out)
        return out[0], out[1]

    def strengths(self, **pad):
        g = XGamepad(sum(XB[b] for b in pad.pop('buttons', ())), pad.get('lt', 0), pad.get('rt', 0),
                     pad.get('lx', 0), pad.get('ly', 0), pad.get('rx', 0), pad.get('ry', 0))
        st = (ctypes.c_uint16 * 24)()
        sticks = (ctypes.c_int16 * 4)()
        self.s.dll.ILCT_StrengthsXInput(ctypes.byref(g), st, sticks)
        return {n: st[c - 1] / 65535 for n, c in PAD.items()}, list(sticks)

    def test_radial_deadzone_table(self):
        rows = [
            # (x, y)               expected magnitude, expected direction kept
            ((0.0, 0.0), 0.0),
            ((0.20, 0.10), 0.0),                       # inside, |v| = 0.224
            ((0.24 / math.sqrt(2), 0.24 / math.sqrt(2)), 0.0),  # on the circle, diagonal
            ((0.30, 0.0), (0.30 - 0.24) / 0.76),
            ((0.0, -0.62), (0.62 - 0.24) / 0.76),
            ((0.5, 0.5), (math.sqrt(0.5) - 0.24) / 0.76),
            ((1.0, 0.0), 1.0),
            ((1.0, 1.0), 1.0),                          # square-gate corner: clamped to full, not 1.41
            ((-0.7071, 0.7071), 1.0),                   # round-gate diagonal: full magnitude
        ]
        for (x, y), magnitude in rows:
            with self.subTest(x=x, y=y):
                ox, oy = self.deadzone(x, y)
                self.assertAlmostEqual(math.hypot(ox, oy), magnitude, places=4)
                if magnitude > 0:
                    self.assertAlmostEqual(math.atan2(oy, ox), math.atan2(y, x), places=4)

    def test_diagonals_keep_full_magnitude(self):
        # FF12 walks below about 0.87; a full diagonal must stay a run (native walk/run plateaus 0.40/0.55/0.92).
        for lx, ly in ((32767, 32767), (23170, 23170), (-23170, 23170), (-32768, -32768), (26000, -20000)):
            with self.subTest(lx=lx, ly=ly):
                st, sticks = self.strengths(lx=lx, ly=ly)
                magnitude = math.hypot(sticks[0], sticks[1]) / 32767
                self.assertGreaterEqual(magnitude, 0.999)
                axes = math.hypot(st['LS_RIGHT'] - st['LS_LEFT'], st['LS_UP'] - st['LS_DOWN'])
                self.assertGreaterEqual(axes, 0.999)

    def test_stick_directions_split_into_plus_and_minus(self):
        rows = [({'lx': 32767}, {'LS_RIGHT': 1.0}), ({'lx': -32768}, {'LS_LEFT': 1.0}),
                ({'ly': 32767}, {'LS_UP': 1.0}), ({'ly': -32768}, {'LS_DOWN': 1.0}),
                ({'rx': 32767}, {'RS_RIGHT': 1.0}), ({'ry': -32768}, {'RS_DOWN': 1.0}),
                ({'lx': 5000, 'ly': 5000}, {})]          # |v| 0.216 < 0.24: nothing
        for pad, want in rows:
            with self.subTest(pad=pad):
                st, _ = self.strengths(**pad)
                for name in ('LS_UP', 'LS_DOWN', 'LS_LEFT', 'LS_RIGHT', 'RS_UP', 'RS_DOWN', 'RS_LEFT', 'RS_RIGHT'):
                    self.assertAlmostEqual(st[name], want.get(name, 0.0), places=4, msg=name)

    def test_buttons_and_triggers(self):
        st, _ = self.strengths(buttons=list(XB), lt=255, rt=128)
        for name in XB:
            self.assertEqual(st[name], 1.0, name)
        self.assertEqual(st['LT'], 1.0)
        self.assertAlmostEqual(st['RT'], 128 / 255, places=4)
        st, _ = self.strengths()
        self.assertEqual(sum(st.values()), 0)

    def test_sdl_normalisation_matches_xinput_conventions(self):
        rows = [
            (sdl_reading(ly=-32768), 'LS_UP'),            # SDL y is down-positive
            (sdl_reading(ly=32767), 'LS_DOWN'),
            (sdl_reading(lx=32767, ly=-32768), None),     # full diagonal
            (sdl_reading(lt=32767), 'LT'),
            (sdl_reading(rt=-5), None),                   # negative trigger is clamped to 0
        ]
        for reading, single in rows:
            with self.subTest(single=single):
                st = (ctypes.c_uint16 * 24)()
                sticks = (ctypes.c_int16 * 4)()
                self.s.dll.ILCT_StrengthsSdl(ctypes.byref(reading), st, sticks)
                values = {n: st[c - 1] / 65535 for n, c in PAD.items()}
                if single:
                    self.assertEqual(values[single], 1.0)
                    self.assertEqual(sum(1 for v in values.values() if v > 0), 1)
                elif reading.axes[0]:
                    self.assertGreaterEqual(math.hypot(sticks[0], sticks[1]) / 32767, 0.999)
                    self.assertGreater(values['LS_UP'], 0.70)
                    self.assertGreater(values['LS_RIGHT'], 0.70)
                else:
                    self.assertEqual(sum(values.values()), 0)


# =====================================================================================================
# G. SDL -> XINPUT_GAMEPAD packing is complete
# =====================================================================================================
@native_test
class PackingTest(ServiceCase):
    def pack(self, reading):
        out = XGamepad()
        self.s.dll.ILCT_PackSdl(ctypes.byref(reading), ctypes.byref(out))
        return out

    def test_every_button_maps_to_its_xinput_bit(self):
        for name, sdl_index in SDLB.items():
            with self.subTest(button=name):
                packed = self.pack(sdl_reading(buttons=[name]))
                self.assertEqual(packed.buttons, XB.get(name, 0))  # GUIDE has no XInput bit
        everything = self.pack(sdl_reading(buttons=list(SDLB)))
        self.assertEqual(everything.buttons, sum(XB.values()))

    def test_triggers_and_axes(self):
        rows = [
            (dict(lt=32767), ('lt', 255)), (dict(lt=16384), ('lt', 128)), (dict(rt=0), ('rt', 0)),
            (dict(rt=-1), ('rt', 0)), (dict(lx=32767), ('lx', 32767)), (dict(lx=-32768), ('lx', -32768)),
            (dict(ly=-32768), ('ly', 32767)), (dict(ly=32767), ('ly', -32768)), (dict(rx=-16384), ('rx', -16384)),
            (dict(ry=100), ('ry', -100)),
        ]
        for reading, (field, want) in rows:
            with self.subTest(reading=reading):
                self.assertEqual(getattr(self.pack(sdl_reading(**reading)), field), want)

    def test_packing_is_raw_not_deadzoned(self):
        packed = self.pack(sdl_reading(lx=3000))  # inside the co-op deadzone; the game applies its own
        self.assertEqual(packed.lx, 3000)


# =====================================================================================================
# B. XInput: bounded grace from the first failure, recovery, absent re-probe
# =====================================================================================================
@native_test
class XInputGraceTest(ServiceCase):
    def setUp(self):
        super().setUp()
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        self.s.apply(r)
        self.s.begin()

    def test_transient_gap_has_no_zero_frame_and_no_recovery_edge(self):
        table = [
            # t ms, slot 1 rc, A down, expected row for P2
            (0, XI_OK, False, {'source': SRC_XINPUT, 'held': 0, 'pressed': 0}),
            (8, XI_OK, True, {'pressed': ['A'], 'held_names': ['A']}),
            (16, XI_GONE, False, {'held_names': ['A'], 'pressed': 0, 'released': 0, 'flags_on': F_BRIDGED}),
            (60, XI_GONE, False, {'held_names': ['A'], 'flags_on': F_BRIDGED | F_CONNECTED}),
            (116, XI_GONE, False, {'held_names': ['A'], 'flags_on': F_BRIDGED}),     # 100 ms after the first failure
            (120, XI_OK, True, {'held_names': ['A'], 'pressed': 0, 'released': 0, 'flags_off': F_BRIDGED | F_RESEEDED}),
        ]
        generation = None
        for t, rc, a_down, row in table:
            self.s.xinput(1, rc=rc, buttons=['A'] if a_down else [])
            self.s.step(t)
            f = self.s.frame(2)
            self.assertRow(f, row, f't={t}')
            if generation is None:
                generation = f.gen
            self.assertEqual(f.gen, generation, f't={t}: the gap must not start a new connection')

    def test_grace_boundary_and_expired_gap_absorbs_held_until_repress(self):
        table = [
            (0, XI_OK, True, {'source': SRC_XINPUT, 'absorbed': ['A'], 'pressed': 0, 'flags_on': F_RESEEDED}),
            (8, XI_OK, False, {'released': ['A'], 'absorbed': 0}),
            (16, XI_OK, True, {'pressed': ['A']}),
            (20, XI_GONE, True, {'held_names': ['A'], 'flags_on': F_BRIDGED}),       # first failure at 20
            (120, XI_GONE, True, {'held_names': ['A'], 'flags_on': F_BRIDGED}),      # 100 ms: still bridged
            (121, XI_GONE, True, {'source': SRC_NONE, 'held': 0, 'released': ['A'], 'flags_off': F_CONNECTED,
                                  'flags_on': F_ASSIGNED}),
            (200, XI_OK, True, {'source': SRC_NONE}),        # back, but not probed again before 121 + 250
            (370, XI_OK, True, {'source': SRC_NONE}),
            (371, XI_OK, True, {'source': SRC_XINPUT, 'pressed': 0, 'absorbed': ['A'], 'flags_on': F_RESEEDED}),
            (379, XI_OK, True, {'pressed': 0, 'absorbed': ['A']}),
            (387, XI_OK, False, {'released': ['A'], 'absorbed': 0}),
            (395, XI_OK, True, {'pressed': ['A']}),
        ]
        generations = []
        for t, rc, a_down, row in table:
            self.s.xinput(1, rc=rc, buttons=['A'] if a_down else [])
            self.s.step(t)
            f = self.s.frame(2)
            self.assertRow(f, row, f't={t}')
            if f.source == SRC_XINPUT:
                generations.append(f.gen)
        self.assertEqual(len(set(generations)), 2, 'recovery after an exposed gap is a new connection')
        self.assertNotIn(0, generations)

    def test_absent_slots_are_probed_every_250_ms(self):
        before = self.s.status()['xinput_polls']
        self.s.run(1, 1000, every_ms=1)  # 1000 ticks, slots 0..3 all absent
        polls = self.s.status()['xinput_polls'] - before
        self.assertLessEqual(polls, 4 * 5)
        self.assertGreaterEqual(polls, 4 * 4)


# =====================================================================================================
# C. SDL backend robustness
# =====================================================================================================
@native_test
class SdlBackendTest(ServiceCase):
    def setUp(self):
        super().setUp()
        r = self.s.settings()
        seat_sdl(r, 2, DS4, path='HID#ds4-a', name=b'Wireless Pad Four')
        self.s.apply(r)

    def inventory_names(self):
        return sorted(bytes(e['sdl']['name'][:e['sdl']['name_len']]).decode() for e in self.s.devices_reply()[2])

    def test_broken_virtual_device_is_skipped_without_dropping_healthy_ones(self):
        self.s.sdl(10, guid=XBOX, path='virt', name='Broken virtual', info_fails=1)
        self.s.sdl(11, guid=SWITCH, path='virt2', name='Unopenable', open_fails=1)
        self.s.sdl(12, guid=DS4, path='HID#ds4-a', name='Wireless Pad Four', reading=sdl_reading(buttons=['A']))
        self.s.begin()
        self.s.run(0, 3000, every_ms=8)
        self.assertEqual(self.inventory_names(), ['Wireless Pad Four'])
        f = self.s.frame(2)
        self.assertRow(f, {'source': SRC_SDL, 'flags_on': F_CONNECTED, 'held_names': ['A']})
        st = self.s.status()
        self.assertGreater(st['sdl_unreadable'], 0)
        self.assertLessEqual(st['sdl_open_failures'], 4, 'a device that fails to open is retried once a second')
        self.assertGreaterEqual(st['sdl_open_failures'], 3)
        self.assertEqual(self.s.open_handles(), 1)

    def test_keys_by_the_opened_instance_id(self):
        self.s.sdl(20, guid=DS5, path='HID#ds5', name='Wireless Pad Five')
        self.s.sdl(21, guid=DS4, path='HID#ds4-a', name='Wireless Pad Four', reading=sdl_reading(buttons=['X']))
        self.s.dll.ILCT_SdlGlobal(0, 0, 1)  # open(i) opens enumeration index i + 1: the list moved
        self.s.begin()
        self.s.step(0)    # queried 20, opened 21: tracked as 21
        self.assertEqual(self.inventory_names(), ['Wireless Pad Four'])
        self.s.step(8)    # queried 20 again, opened 21 again: that second reference is closed
        self.assertEqual(self.s.open_handles(), 1)
        self.s.dll.ILCT_SdlGlobal(0, 0, 0)
        self.s.step(16)   # the list settled: 20 is opened
        names = self.inventory_names()
        self.assertEqual(names, ['Wireless Pad Five', 'Wireless Pad Four'])
        self.assertEqual(self.s.open_handles(), 2)
        f = self.s.frame(2)
        self.assertRow(f, {'source': SRC_SDL, 'held_names': ['X']})
        entries = self.s.devices_reply()[2]
        by_name = {bytes(e['sdl']['name'][:e['sdl']['name_len']]).decode(): e for e in entries}
        self.assertEqual(bytes(by_name['Wireless Pad Four']['sdl']['uid']), uid_of('HID#ds4-a'))
        self.assertEqual(by_name['Wireless Pad Four']['owner_seat'], 2)

    def test_attached_handle_survives_one_coherent_omission(self):
        self.s.sdl(30, guid=DS4, path='HID#ds4-a', reading=sdl_reading(buttons=['A']))
        self.s.begin()
        self.s.step(0)
        self.s.step(8)
        gen = self.s.frame(2).gen
        rows = [
            (16, dict(omitted=1), {'source': SRC_SDL, 'held_names': ['A'], 'released': 0}),   # one omission
            (24, dict(omitted=0), {'source': SRC_SDL, 'held_names': ['A'], 'flags_off': F_RESEEDED}),
            (32, dict(omitted=1), {'source': SRC_SDL}),
            (40, dict(omitted=1), {'source': SRC_NONE, 'released': ['A']}),                   # second in a row
        ]
        for t, change, row in rows:
            self.s.sdl_set(30, **change)
            self.s.step(t)
            f = self.s.frame(2)
            self.assertRow(f, row, f't={t}')
            if f.source == SRC_SDL:
                self.assertEqual(f.gen, gen, f't={t}')
        self.assertEqual(self.s.open_handles(), 0)

    def test_detached_handle_is_retired_at_once(self):
        self.s.sdl(31, guid=DS4, path='HID#ds4-a')
        self.s.begin()
        self.s.step(0)
        self.s.sdl_set(31, omitted=1, attached=0)
        self.s.step(8)
        self.assertEqual(self.s.frame(2).source, SRC_NONE)
        self.assertEqual(self.s.open_handles(), 0)

    def test_a_failed_read_retires_only_that_controller(self):
        r = self.s.settings()
        seat_sdl(r, 2, DS4, path='HID#ds4-a')
        seat_sdl(r, 3, DS5, path='HID#ds5')
        self.s.apply(r)
        self.s.sdl(40, guid=DS4, path='HID#ds4-a', reading=sdl_reading(buttons=['B']))
        self.s.sdl(41, guid=DS5, path='HID#ds5', reading=sdl_reading(buttons=['Y']))
        self.s.begin()
        self.s.step(0)
        gen3 = self.s.frame(3).gen
        self.s.sdl_set(40, read_fails=1)
        self.s.step(8)
        self.assertEqual(self.s.frame(2).source, SRC_NONE)
        f3 = self.s.frame(3)
        self.assertRow(f3, {'source': SRC_SDL, 'held_names': ['Y'], 'released': 0, 'flags_off': F_RESEEDED})
        self.assertEqual(f3.gen, gen3)
        self.assertEqual(self.s.status()['sdl_read_failures'], 1)
        self.assertEqual(self.s.open_handles(), 1)

    def test_pump_failure_grace_starts_when_first_seen_then_fails_closed(self):
        self.s.sdl(50, guid=DS4, path='HID#ds4-a', reading=sdl_reading(buttons=['A']))
        self.s.begin()
        self.s.step(0)
        self.s.step(8)
        gen = self.s.frame(2).gen
        self.s.dll.ILCT_SdlGlobal(1, 0, 0)
        table = [  # last success at 8; the failure is first seen at 80
            (80, {'source': SRC_SDL, 'held_names': ['A'], 'flags_on': F_BRIDGED}),
            (170, {'source': SRC_SDL, 'held_names': ['A'], 'flags_on': F_BRIDGED}),   # 162 ms after the success
            (180, {'source': SRC_SDL, 'flags_on': F_BRIDGED}),                         # 100 ms after first seen
            (181, {'source': SRC_NONE, 'held': 0, 'released': ['A']}),                 # fail closed: neutral
            (300, {'source': SRC_NONE}),
        ]
        for t, row in table:
            self.s.step(t)
            self.assertRow(self.s.frame(2), row, f't={t}')
        self.s.dll.ILCT_SdlGlobal(0, 0, 0)
        self.s.step(308)
        f = self.s.frame(2)
        self.assertRow(f, {'source': SRC_SDL, 'pressed': 0, 'absorbed': ['A'], 'flags_on': F_RESEEDED})
        self.assertNotEqual(f.gen, gen, 'an exposed gap is a new connection')
        # A failed device count behaves like a failed update.
        self.s.dll.ILCT_SdlGlobal(0, 1, 0)
        self.s.step(316)
        self.assertTrue(self.s.frame(2).flags & F_BRIDGED)


# =====================================================================================================
# D. Identities: GUID, uid, ordinal; names
# =====================================================================================================
@native_test
class IdentityTest(ServiceCase):
    def test_sha256_against_hashlib(self):
        for data in (b'', b'abc', b'a' * 55, b'a' * 56, b'a' * 64, b'a' * 119, bytes(range(256)) * 9):
            with self.subTest(size=len(data)):
                out = ctypes.create_string_buffer(32)
                self.s.dll.ILCT_Sha256(data, len(data), out)
                self.assertEqual(out.raw, hashlib.sha256(data).digest())

    def test_uid_is_the_first_16_bytes_of_sha256_of_the_lowercased_path(self):
        path = r'\\?\HID#VID_054C&PID_09CC&MI_03#7&1A2B3C&0&0000#{4D1E55B2-F16F-11CF-88CB-001111000030}'
        out = ctypes.create_string_buffer(16)
        self.s.dll.ILCT_Uid(path.encode(), out)
        self.assertEqual(out.raw, uid_of(path))
        self.s.dll.ILCT_Uid(path.lower().encode(), out)
        self.assertEqual(out.raw, uid_of(path), 'case does not change the uid')

    def entries(self):
        return {e['entry_id']: e for e in self.s.devices_reply()[2]}

    def by_path(self):
        return {bytes(e['sdl']['uid']): e for e in self.s.devices_reply()[2]}

    def test_identical_models_get_ordinals_and_uids_keep_them(self):
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='HID#a', name='Wireless Pad Four')
        self.s.sdl(2, guid=DS4, path='HID#b', name='Wireless Pad Four')
        self.s.step(0)
        ids = self.by_path()
        self.assertEqual((ids[uid_of('HID#a')]['sdl']['ordinal'], ids[uid_of('HID#b')]['sdl']['ordinal']), (0, 1))
        rows = [
            ('unplug a', lambda: self.s.sdl_remove(1), {'HID#b': 1}),
            ('new identical pad c', lambda: self.s.sdl(3, guid=DS4, path='HID#c'), {'HID#b': 1, 'HID#c': 2}),
            ('a comes back', lambda: self.s.sdl(4, guid=DS4, path='HID#a'), {'HID#a': 0, 'HID#b': 1, 'HID#c': 2}),
            ('other model starts at 0', lambda: self.s.sdl(5, guid=DS5, path='HID#d'),
             {'HID#a': 0, 'HID#b': 1, 'HID#c': 2, 'HID#d': 0}),
        ]
        t = 0
        for label, action, want in rows:
            with self.subTest(label):
                action()
                t += 8
                self.s.step(t)
                ids = self.by_path()
                got = {p: ids[uid_of(p)]['sdl']['ordinal'] for p in want if uid_of(p) in ids}
                self.assertEqual(got, want)
                for e in ids.values():
                    self.assertEqual(e['sdl']['has_uid'], 1)

    def test_pads_without_a_path_reuse_free_ordinals(self):
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='')
        self.s.sdl(2, guid=DS4, path='')
        self.s.step(0)
        self.assertEqual(sorted(e['sdl']['ordinal'] for e in self.s.devices_reply()[2]), [0, 1])
        for e in self.s.devices_reply()[2]:
            self.assertEqual((e['sdl']['has_uid'], bytes(e['sdl']['uid'])), (0, bytes(16)))
        self.s.sdl_remove(1)
        self.s.step(8)
        self.s.sdl(3, guid=DS4, path='')
        self.s.step(16)
        self.assertEqual(sorted(e['sdl']['ordinal'] for e in self.s.devices_reply()[2]), [0, 1])

    def test_identical_models_with_distinct_uids_stay_assignable(self):
        r = self.s.settings()
        seat_sdl(r, 2, DS4, path='HID#a')
        seat_sdl(r, 3, DS4, path='HID#b', ordinal=1)
        self.s.apply(r)
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='HID#b', reading=sdl_reading(buttons=['B']))  # b first: gets ordinal 0
        self.s.sdl(2, guid=DS4, path='HID#a', reading=sdl_reading(buttons=['A']))
        self.s.step(0)
        self.s.step(8)
        self.assertEqual(self.s.frame(2).held, BIT['A'], 'P2 follows its uid, not the ordinal')
        self.assertEqual(self.s.frame(3).held, BIT['B'])

    def test_each_connection_gets_a_new_non_zero_generation(self):
        r = self.s.settings()
        seat_sdl(r, 2, DS4, path='HID#a')
        self.s.apply(r)
        self.s.begin()
        seen = []
        for t in range(0, 80, 16):
            self.s.sdl(9, guid=DS4, path='HID#a')
            self.s.step(t)
            seen.append(self.s.frame(2).gen)
            self.s.sdl_remove(9)
            self.s.step(t + 8)
        self.assertEqual(len(set(seen)), len(seen))
        self.assertNotIn(0, seen)

    def test_names_are_sanitized(self):
        rows = [
            (b'Wireless Pad Five', b'Wireless Pad Five'),
            (b'Pad\x01\x1f\x7f\tName', b'PadName'),                       # ASCII controls and DEL
            ('Pd\u0085x'.encode(), b'Pdx'),                                 # Latin-1 control (U+0085)
            (b'bad\xc3(utf8', b'bad(utf8'),
            (b'over\xc0\xafl', b'overl'),                                   # overlong '/'
            (b'sur\xed\xa0\x80r', b'surr'),                                 # surrogate
            (b'big\xf4\x90\x80\x80!', b'big!'),                             # > U+10FFFF
            ('\u00e9\u00e8 \u30d1\u30c3\u30c9 \U0001F3AE'.encode(), '\u00e9\u00e8 \u30d1\u30c3\u30c9 \U0001F3AE'.encode()),
            ('\u00e9'.encode() * 60, '\u00e9'.encode() * 48),               # 96 bytes, never a split character
            (b'x' + '\u30d1'.encode() * 40, b'x' + '\u30d1'.encode() * 31),  # 1 + 93 = 94 <= 96 < 97
        ]
        for raw, want in rows:
            with self.subTest(raw=raw[:20]):
                out = ctypes.create_string_buffer(96)
                n = self.s.dll.ILCT_Sanitize(raw, out)
                self.assertEqual(out.raw[:n], want)
                self.assertEqual(out.raw[n:], bytes(96 - n))
                want.decode('utf-8')

    def test_inventory_names_are_sanitized_and_accepted_by_settings(self):
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='HID#a', name=b'Evil\x1b[31m\xff Pad')
        self.s.step(0)
        e = self.s.devices_reply()[2][0]
        name = bytes(e['sdl']['name'][:e['sdl']['name_len']])
        self.assertEqual(name, b'Evil[31m Pad')
        # The identity as listed can be stored as a seat device: the bridge's validation accepts it.
        r = self.s.settings()
        put(r, ['seats', 1, 'device', 'kind'], S.e('device_kind', 'SDL'))
        o, _, _ = S.locate('ilc_settings', ['seats', 1, 'device', 'sdl'])
        identity = bytes(S.encode('ilc_device_identity', e['sdl']))
        r[o:o + len(identity)] = identity
        self.s.apply(r)


# =====================================================================================================
# E. Aliases and the seat's live source
# =====================================================================================================
@native_test
class AliasResolutionTest(ServiceCase):
    PHYS = ('HID#ds4-phys', DS4, 'Wireless Pad Four')      # physical pad (HIDAPI)
    VIRT = ('HID#ds4-virt', XBOX, 'Virtual Pad')         # a second SDL mirror of it (virtual output)

    def scenario(self, configure, world, seat=2):
        r = self.s.settings()
        configure(r)
        self.s.apply(r)
        self.s.begin()
        world()
        self.s.step(0)
        self.s.step(8)
        return self.s.frame(seat)

    def test_resolution_table(self):
        P, V = self.PHYS, self.VIRT
        rows = [
            ('XInput assignment, slot connected',
             lambda r: seat_xinput(r, 2, 1),
             lambda: self.s.xinput(1, buttons=['A']),
             {'source': SRC_XINPUT, 'slot': 1, 'held_names': ['A'], 'flags_off': F_VIA_ALIAS}),
            ('XInput assignment falls back to its SDL alias',
             lambda r: (seat_xinput(r, 2, 1), add_alias(r, 1, P[1], P[0])),
             lambda: self.s.sdl(1, guid=P[1], path=P[0], reading=sdl_reading(buttons=['B'])),
             {'source': SRC_SDL, 'held_names': ['B'], 'flags_on': F_VIA_ALIAS}),
            ('XInput assignment falls back to SDL\'s own XInput device',
             lambda r: seat_xinput(r, 2, 1),
             lambda: self.s.sdl(1, guid=XBOX, path='XInput#1', reading=sdl_reading(buttons=['Y'])),
             {'source': SRC_SDL, 'held_names': ['Y'], 'flags_on': F_VIA_ALIAS}),
            ('SDL assignment, identity connected',
             lambda r: seat_sdl(r, 2, P[1], P[0]),
             lambda: self.s.sdl(1, guid=P[1], path=P[0], reading=sdl_reading(buttons=['X'])),
             {'source': SRC_SDL, 'held_names': ['X'], 'flags_off': F_VIA_ALIAS}),
            ('SDL assignment falls back to its XInput alias slot',
             lambda r: (seat_sdl(r, 2, P[1], P[0]), add_alias(r, 2, P[1], P[0])),
             lambda: self.s.xinput(2, buttons=['START']),
             {'source': SRC_XINPUT, 'slot': 2, 'held_names': ['START'], 'flags_on': F_VIA_ALIAS}),
            ('SDL assignment falls back to an SDL sibling in its alias group',
             lambda r: (seat_sdl(r, 2, P[1], P[0]), add_alias(r, 2, P[1], P[0]), add_alias(r, 2, V[1], V[0])),
             lambda: self.s.sdl(7, guid=V[1], path=V[0], reading=sdl_reading(buttons=['LB'])),
             {'source': SRC_SDL, 'held_names': ['LB'], 'flags_on': F_VIA_ALIAS}),
            ('SDL assignment, nothing connected: assigned but disconnected',
             lambda r: (seat_sdl(r, 2, P[1], P[0]), add_alias(r, 2, P[1], P[0])),
             lambda: self.s.sdl(8, guid=DS5, path='HID#other'),
             {'source': SRC_NONE, 'held': 0, 'flags_on': F_ASSIGNED, 'flags_off': F_CONNECTED}),
            ('No assignment: an unowned pad drives nothing',
             lambda r: None,
             lambda: (self.s.xinput(3, buttons=['A']), self.s.sdl(9, guid=DS5, path='HID#x')),
             {'source': SRC_NONE, 'flags_off': F_ASSIGNED | F_CONNECTED}),
            ('Parked seat has no source',
             lambda r: (seat_xinput(r, 2, 1), put(r, ['seats', 1, 'parked'], 1)),
             lambda: self.s.xinput(1, buttons=['A']),
             {'source': SRC_NONE, 'held': 0}),
        ]
        for label, configure, world, row in rows:
            with self.subTest(label):
                self.s.end()
                self.s = Service(fresh_test_dll())
                self.assertRow(self.scenario(configure, world), row, label)

    def test_host_seat_ignores_free_slots(self):
        f = self.scenario(lambda r: None, lambda: (self.s.xinput(0, rc=XI_GONE), self.s.xinput(1, buttons=['A'])),
                          seat=1)
        self.assertRow(f, {'source': SRC_NONE, 'held': 0, 'flags_on': F_ASSIGNED})
        self.s.xinput(0, buttons=['B'])
        self.s.run(16, 300)
        self.assertRow(self.s.frame(1), {'source': SRC_XINPUT, 'slot': 0, 'held_names': ['B']})

    def test_configured_sdl_p1_stays_an_idle_virtual_pad_while_unavailable(self):
        r = self.s.settings()
        seat_sdl(r, 1, DS5, 'HID#p1')
        self.s.apply(r)
        self.s.begin()
        self.s.xinput(0, buttons=['A'])   # some other pad on XInput 0 must not feed P1
        self.s.step(0)
        f = self.s.frame(1)
        self.assertRow(f, {'source': SRC_NONE, 'held': 0, 'flags_on': F_ASSIGNED, 'flags_off': F_CONNECTED})
        self.assertEqual(f.pad, (0, 0, 0, 0, 0, 0, 0), 'idle virtual pad: neutral XINPUT_GAMEPAD image')
        self.s.sdl(1, guid=DS5, path='HID#p1', reading=sdl_reading(buttons=['A'], lx=32767))
        self.s.step(8)
        f = self.s.frame(1)
        self.assertEqual(f.source, SRC_SDL)
        self.assertEqual((f.pad[0], f.pad[3]), (XB['A'], 32767))

    def test_one_physical_pad_has_one_owner(self):
        # Settings validation refuses this (alias-aware); the service also fails closed if it ever gets it.
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        seat_sdl(r, 3, XBOX, 'XInput#1')
        self.s.apply_unvalidated(r)
        self.s.begin()
        self.s.xinput(1, buttons=['A'])
        self.s.sdl(1, guid=XBOX, path='XInput#1', reading=sdl_reading(buttons=['A']))
        self.s.step(0)
        self.assertEqual(self.s.frame(2).source, SRC_XINPUT)
        self.assertRow(self.s.frame(3), {'source': SRC_NONE, 'held': 0})
        self.assertGreater(self.s.status()['seat_conflicts'], 0)
        entries = self.s.devices_reply()[2]
        self.assertEqual(len(entries), 1, 'the XInput slot and SDL\'s mirror of it are one pad')
        self.assertEqual((entries[0]['kind'], entries[0]['xinput_slot'], entries[0]['owner_seat']), (1, 1, 2))

    def test_inventory_collapses_aliases_and_locks_p1(self):
        P, V = self.PHYS, self.VIRT
        r = self.s.settings()   # P1 is XInput 0 by default
        seat_xinput(r, 3, 2)
        add_alias(r, 2, P[1], P[0], name=b'Wireless Pad Four')
        self.s.apply(r)
        self.s.begin()
        self.s.xinput(0)
        self.s.sdl(1, guid=XBOX, path='XInput#0', name='Mirror Pad Zero')    # SDL's mirror of P1's pad
        self.s.xinput(2)
        self.s.sdl(2, guid=XBOX, path='XInput#2', name='Mirror Pad Two')    # virtual output of the DS4
        self.s.sdl(3, guid=P[1], path=P[0], name='Wireless Pad Four')                # the physical DS4
        self.s.sdl(4, guid=DS5, path='HID#free', name='Wireless Pad Five')
        self.s.step(0)
        status, reply, entries = self.s.devices_reply()
        self.assertEqual(status, 32 + 3 * 148)
        rows = [(e['kind'], e['xinput_slot'], bytes(e['sdl']['name'][:e['sdl']['name_len']]).decode(),
                 e['owner_seat'], e['flags'], e['connected']) for e in entries]
        self.assertEqual(rows, [
            (1, 0, 'Mirror Pad Zero', 1, 1, 1),        # "unknown P1 mirror is locked"
            (1, 2, 'Wireless Pad Four', 3, 0, 1),             # named after the configured alias, not the mirror
            (2, 255, 'Wireless Pad Five', 0, 0, 1),
        ])
        self.assertEqual(len({e['entry_id'] for e in entries}), 3)
        self.assertNotIn(0, [e['entry_id'] for e in entries])


# =====================================================================================================
# F. Hold absorption on every discontinuity
# =====================================================================================================
@native_test
class HoldAbsorptionTest(ServiceCase):
    def setUp(self):
        super().setUp()
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        add_alias(r, 1, DS4, 'HID#ds4', name=b'Wireless Pad Four')
        self.s.apply(r)

    def test_startup_absorbs_a_held_button_until_repress(self):
        self.s.xinput(1, buttons=['A', 'START'])
        self.s.begin()
        table = [
            (0, ['A', 'START'], {'pressed': 0, 'absorbed': ['A', 'START'], 'flags_on': F_RESEEDED}),
            (8, ['A', 'START', 'B'], {'pressed': ['B'], 'absorbed': ['A', 'START']}),   # others still work
            (16, ['START', 'B'], {'released': ['A'], 'absorbed': ['START']}),
            (24, ['A', 'START', 'B'], {'pressed': ['A'], 'absorbed': ['START']}),
            (32, [], {'released': ['A', 'START', 'B'], 'absorbed': 0}),
            (40, ['START'], {'pressed': ['START']}),
        ]
        for t, buttons, row in table:
            self.s.xinput(1, buttons=buttons)
            self.s.step(t)
            self.assertRow(self.s.frame(2), row, f't={t}')

    def test_every_discontinuity_absorbs(self):
        cases = {
            'first hotplug of an XInput pad': self.case_first_hotplug,
            'SDL replug (new connection generation)': self.case_sdl_replug,
            'handoff XInput -> SDL alias after an exposed gap': self.case_handoff_to_alias,
            'settings change for the seat': self.case_settings_change,
            'service restart': self.case_restart,
        }
        for label, case in cases.items():
            with self.subTest(label):
                self.s.end()
                self.s = Service(fresh_test_dll())
                r = self.s.settings()
                seat_xinput(r, 2, 1)
                add_alias(r, 1, DS4, 'HID#ds4', name=b'Wireless Pad Four')
                self.s.apply(r)
                frame = case()
                self.assertRow(frame, {'pressed': 0, 'absorbed': ['A'], 'flags_on': F_RESEEDED | F_CONNECTED},
                               label)

    def until(self, start, end, source):
        """Step from start (8 ms ticks) and return P2's first frame fed by `source`."""
        t = start
        while t <= end:
            self.s.step(t)
            f = self.s.frame(2)
            if f.source == source:
                return f
            t += 8
        raise AssertionError(f'no frame from source {source} by t={end}')

    def case_first_hotplug(self):
        self.s.begin()
        self.s.run(0, 100)               # slot 1 absent
        self.s.xinput(1, buttons=['A'])  # plugged in with A held
        return self.until(108, 600, SRC_XINPUT)

    def case_sdl_replug(self):
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='HID#ds4')
        self.s.step(0)
        self.s.step(8)
        self.s.sdl_remove(1)
        self.s.step(16)
        self.s.sdl(2, guid=DS4, path='HID#ds4', reading=sdl_reading(buttons=['A']))
        self.s.step(24)
        return self.s.frame(2)

    def case_handoff_to_alias(self):
        self.s.begin()
        self.s.sdl(1, guid=DS4, path='HID#ds4', reading=sdl_reading(buttons=['A']))
        self.s.xinput(1, buttons=['A'])
        self.s.step(0)
        self.s.step(8)
        self.s.xinput(1, rc=XI_GONE)
        return self.until(16, 300, SRC_SDL)

    def case_settings_change(self):
        self.s.begin()
        self.s.xinput(1, buttons=['A'])
        self.s.step(0)
        self.s.xinput(1, buttons=[])
        self.s.step(8)
        self.s.xinput(1, buttons=['A'])
        self.s.step(16)
        assert self.s.frame(2).pressed == BIT['A']
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        add_alias(r, 1, DS4, 'HID#ds4', name=b'Wireless Pad Four')
        add_alias(r, 3, DS5, 'HID#ds5', name=b'Pad Five')     # affects every seat's alias groups
        self.s.apply(r)
        self.s.step(24)
        return self.s.frame(2)

    def case_restart(self):
        self.s.xinput(1, buttons=['A'])
        self.s.begin()
        self.s.step(0)
        self.s.xinput(1, buttons=[])
        self.s.step(8)
        self.s.xinput(1, buttons=['A'])
        self.s.step(16)
        assert self.s.frame(2).pressed == BIT['A']
        self.s.end()
        self.s.begin()
        self.s.step(24)
        return self.s.frame(2)

    def test_transient_gap_and_other_seats_settings_do_not_absorb(self):
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        add_alias(r, 1, DS4, 'HID#ds4', name=b'Wireless Pad Four')
        self.s.apply(r)
        self.s.begin()
        self.s.xinput(1)
        self.s.step(0)
        self.s.xinput(1, buttons=['A'])
        self.s.step(8)
        r = self.s.settings()
        seat_xinput(r, 2, 1)
        add_alias(r, 1, DS4, 'HID#ds4', name=b'Wireless Pad Four')
        seat_xinput(r, 3, 2)                   # only P3 changes
        self.s.apply(r)
        self.s.step(16)
        self.assertRow(self.s.frame(2), {'held_names': ['A'], 'absorbed': 0, 'flags_off': F_RESEEDED})
        self.s.xinput(1, rc=XI_GONE)
        self.s.step(24)
        self.s.xinput(1, buttons=[])
        self.s.step(32)
        self.s.xinput(1, buttons=['A'])
        self.s.step(40)
        self.assertRow(self.s.frame(2), {'pressed': ['A'], 'absorbed': 0})


# =====================================================================================================
# H. GET_DEVICES through the bridge, fed by the service
# =====================================================================================================
@native_test
class DevicesOpTest(ServiceCase):
    def test_not_ready_until_started_and_after_stop(self):
        status, reply, entries = self.s.devices_reply()
        self.assertEqual((status, reply['service_state'], reply['count'], reply['header']['size']),
                         (32, S.e('service_state', 'NOT_READY'), 0, 32))
        self.s.begin(session=0xABCD)
        self.s.xinput(0)
        self.s.step(1000)
        status, reply, entries = self.s.devices_reply()
        self.assertEqual((reply['service_state'], reply['inventory_session'], reply['count']),
                         (S.e('service_state', 'READY'), 0xABCD, 1))
        self.assertEqual(reply['header']['size'], status)
        self.assertEqual(reply['header']['record_type'], S.e('record_type', 'DEVICES'))
        self.assertEqual(reply['age_ms'], 0)
        self.s.end()
        status, reply, _ = self.s.devices_reply()
        self.assertEqual((status, reply['service_state'], reply['count']), (32, S.e('service_state', 'NOT_READY'), 0))

    def test_out_too_small_writes_nothing(self):
        self.s.begin()
        for i in range(5):
            self.s.sdl(i + 1, guid=DS4, path=f'HID#{i}', name=f'Pad {i}')
        self.s.step(0)
        full = 32 + 5 * 148
        self.assertEqual(self.s.bridge.call(OP['GET_DEVICES'], out_cap=full - 1)[0], E['OUT_TOO_SMALL'])
        self.assertEqual(self.s.bridge.call(OP['GET_DEVICES'], out_cap=full)[0], full)

    def test_sixteen_entries_at_most(self):
        self.s.begin()
        for i in range(16):
            self.s.sdl(i + 1, guid=DS4, path=f'HID#{i}')
        for slot in range(4):
            self.s.xinput(slot)
        self.s.step(0)
        status, reply, entries = self.s.devices_reply()
        self.assertEqual(reply['count'], 16)
        self.assertEqual(status, 32 + 16 * 148)


# =====================================================================================================
# I. Record and replay: a recorded run replays to the same frames, call for call
# =====================================================================================================
@native_test
class RecordReplayTest(ServiceCase):
    def scripted_run(self, record: bool) -> list[bytes]:
        s = self.s
        r = s.settings()
        seat_xinput(r, 2, 1)
        seat_sdl(r, 3, DS5, 'HID#ds5')
        add_alias(r, 1, DS4, 'HID#ds4')
        s.apply(r)
        s.begin(record=record, session=7)
        frames = []
        script = {
            0: lambda: (s.xinput(1, buttons=['A'])),
            40: lambda: s.sdl(1, guid=DS5, path='HID#ds5', reading=sdl_reading(buttons=['X'], lx=20000)),
            64: lambda: s.xinput(1, rc=XI_GONE),
            72: lambda: s.sdl(2, guid=DS4, path='HID#ds4', reading=sdl_reading(buttons=['A'])),
            200: lambda: s.dll.ILCT_SdlGlobal(1, 0, 0),
            400: lambda: s.dll.ILCT_SdlGlobal(0, 0, 0),
            480: lambda: s.sdl_set(1, omitted=1),
            488: lambda: s.sdl_set(1, omitted=0),
            520: lambda: s.xinput(1, buttons=['B']),
        }
        for t in range(0, 800, 8):
            if t in script:
                script[t]()
            s.step(t)
            frames.append(b''.join(s.frame(seat).raw for seat in (1, 2, 3, 4)))
        return frames

    def test_replay_of_a_recording_gives_identical_frames(self):
        recorded = self.scripted_run(record=True)
        size = self.s.dll.ILCT_RecordingSize()
        self.assertGreater(size, 0)
        transcript = ctypes.create_string_buffer(size)
        self.assertEqual(self.s.dll.ILCT_RecordingCopy(transcript, size), size)
        self.s.end()

        replay = Service(fresh_test_dll())
        replay.apply_unvalidated(self.last_settings())
        self.assertEqual(replay.dll.ILCT_BeginReplay(transcript, size, 7), 1)
        frames = []
        for t in range(0, 800, 8):
            replay.step(t)
            frames.append(b''.join(replay.frame(seat).raw for seat in (1, 2, 3, 4)))
        self.assertEqual(replay.dll.ILCT_ReplayMismatches(), 0)
        self.assertEqual(replay.dll.ILCT_ReplayRemaining(), 0)
        for i, (a, b) in enumerate(zip(recorded, frames)):
            self.assertEqual(a, b, f'tick {i} differs')
        self.assertEqual(len(frames), len(recorded))
        # The recording exercised what it claims to: P2 was fed by XInput, by its SDL alias, and by nothing.
        sources = {decode_frame(f[112:224]).source for f in recorded}
        self.assertEqual(sources, {SRC_XINPUT, SRC_SDL, SRC_NONE})
        replay.end()

        # A truncated transcript: the replay reports mismatches and fails closed (neutral), never invents input.
        record = self.s.dll.ILCT_RecordBytes()
        cut = bytes(transcript.raw[:(size // record // 2) * record])
        truncated = Service(fresh_test_dll())
        truncated.apply_unvalidated(self.last_settings())
        self.assertEqual(truncated.dll.ILCT_BeginReplay(cut, len(cut), 7), 1)
        out = []
        for t in range(0, 800, 8):
            truncated.step(t)
            out.append(b''.join(truncated.frame(seat).raw for seat in (1, 2, 3, 4)))
        self.assertGreater(truncated.dll.ILCT_ReplayMismatches(), 0)
        self.assertNotEqual(out, recorded)
        last = decode_frame(out[-1][112:224])
        self.assertEqual((last.source, last.held), (SRC_NONE, 0), 'past the end of a transcript: neutral')
        truncated.end()

    def last_settings(self) -> bytearray:
        r = bytearray(S.defaults())
        seat_xinput(r, 2, 1)
        seat_sdl(r, 3, DS5, 'HID#ds5')
        add_alias(r, 1, DS4, 'HID#ds4')
        return r


# =====================================================================================================
# J. The live service on this machine (no game): System32 XInput, SDL, thread start/stop
# =====================================================================================================
@native_test
class LiveServiceTest(ServiceCase):
    def test_start_publish_stop(self):
        ucrt = ctypes.CDLL('ucrtbase')
        ucrt.signal.restype = ctypes.c_void_p
        ucrt.signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
        SIGINT, SIGTERM = 2, 15

        def handler(sig):
            temp = ucrt.signal(sig, 0)  # SIG_DFL
            ucrt.signal(sig, temp)
            return temp

        before = (handler(SIGINT), handler(SIGTERM))
        self.assertEqual(self.s.devices_reply()[1]['service_state'], S.e('service_state', 'NOT_READY'))
        self.assertEqual(self.s.dll.ILCT_StartLive(), 1)
        self.assertEqual(self.s.dll.ILCT_StartLive(), 0, 'one service at a time')
        deadline = time.monotonic() + 5
        st = self.s.status()
        while time.monotonic() < deadline and not (st['state'] == 2 and st['seq'] > 5):
            time.sleep(0.02)
            st = self.s.status()
        self.assertEqual(st['state'], 2, st)
        self.assertEqual(st['backends'] & 3, 3, f'XInput and SDL both up: {st}')
        self.assertEqual(st['issues'] & 0x3, 0, st)
        status, reply, entries = self.s.devices_reply()
        self.assertEqual(reply['service_state'], S.e('service_state', 'READY'))
        self.assertNotEqual(reply['inventory_session'], 0)
        self.assertLess(reply['age_ms'], 1000)
        f = self.s.frame(1)
        self.assertIsNotNone(f)
        self.assertEqual(f.seat, 1)
        # Tick rate: about 125 Hz (8 ms timer), well above a 100 Hz floor.
        seq0, t0 = self.s.status()['seq'], time.monotonic()
        time.sleep(0.5)
        rate = (self.s.status()['seq'] - seq0) / (time.monotonic() - t0)
        self.assertGreater(rate, 90)
        self.assertLess(rate, 200)
        # XInput came from System32, by full path.
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.GetModuleHandleW.restype = wintypes.HMODULE
        k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        module = k32.GetModuleHandleW('xinput1_4.dll')
        self.assertTrue(module)
        path = ctypes.create_unicode_buffer(520)
        k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetModuleFileNameW(module, path, 520)
        system = ctypes.create_unicode_buffer(260)
        k32.GetSystemDirectoryW(system, 260)
        self.assertEqual(Path(path.value).parent.resolve(), Path(system.value).resolve())
        self.assertEqual((handler(SIGINT), handler(SIGTERM)), before, 'SDL must not install signal handlers')
        self.assertEqual(self.s.dll.ILCT_StopLive(3000), 1)
        st = self.s.status()
        self.assertEqual(st['state'], 0)
        self.assertEqual(self.s.devices_reply()[1]['service_state'], S.e('service_state', 'NOT_READY'))
        self.assertIsNone(self.s.frame(1))
        # Restartable.
        self.assertEqual(self.s.dll.ILCT_StartLive(), 1)
        self.assertEqual(self.s.dll.ILCT_StopLive(3000), 1)


# =====================================================================================================
# K. Static checks on the shipped DLL and the SDL2 pin
# =====================================================================================================
@native_test
class StaticShippedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dll_path = built_dll()
        cls.data = cls.dll_path.read_bytes()
        cls.manifest = manifest()
        cls.lock = json.loads((COOP / 'third_party' / 'sdl2' / 'sdl2.lock.json').read_text(encoding='utf-8'))
        cls.allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))

    def test_sdl2_is_static_with_the_dynamic_api_off(self):
        imports = self.manifest['audit']['imports']
        self.assertFalse([d for d in imports if 'sdl' in d.lower()], imports)
        for marker in (b'SDL_DYNAMIC_API', b'SDL_DYNAPI_entry', b'SDL2.dll', b'S\0D\0L\0' + b'2\0.\0d\0l\0l\0'):
            self.assertNotIn(marker, self.data)
        self.assertIn(b'SDL_GAMECONTROLLERCONFIG', self.data, 'control: SDL is really linked in')
        sdl = self.manifest['toolchain']['sdl2']
        self.assertEqual((sdl['version'], sdl['linkage'], sdl['dynamicApi']), ('2.32.10', 'static', 'off'))
        self.assertEqual(sdl['sha256'], self.lock['build']['libSha256'])
        self.assertEqual(sdl['tarballSha256'], self.lock['source']['sha256'])
        lib = Path(os.environ.get('ILC_SDL2_DIR', COOP / 'build' / 'sdl2')) / 'libSDL2-ilc.a'
        lib_data = lib.read_bytes()
        self.assertEqual(hashlib.sha256(lib_data).hexdigest(), self.lock['build']['libSha256'])
        self.assertNotIn(b'SDL_DYNAMIC_API', lib_data)

    def test_lock_pins_the_official_release(self):
        src = self.lock['source']
        self.assertEqual(src['sha256'], '5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165')
        self.assertEqual(src['signature']['keyFingerprint'], '1528635D8053A57F77D1E08630A59377A7763BE6')
        self.assertTrue(src['url'].startswith('https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/'))

    def test_no_test_exports_and_no_network_imports(self):
        exports = {e['name'] for e in self.manifest['audit']['exports']}
        self.assertFalse([e for e in exports if e.startswith('ILCT_')])
        self.assertEqual(exports, set(self.allow['exports']))
        lowered = {d.lower() for d in self.manifest['audit']['imports']}
        self.assertFalse(lowered & {d.lower() for d in self.allow['deniedDlls']})

    def test_every_allowed_import_has_a_reason(self):
        reasons = list(self.allow['importReasons'])

        def covered(dll: str, func: str) -> bool:
            for key in reasons:
                dlls, _, funcs = key.partition('!')
                dll_keys = [d.strip().lower() for d in dlls.split(',')]
                if not any(dll.lower() == d or dll.lower().startswith(d + '-') or dll.lower().startswith(d + '.')
                           for d in dll_keys):
                    continue
                if not funcs:
                    return True
                for pattern in (p.strip() for p in funcs.split(',')):
                    if re.fullmatch(re.escape(pattern).replace(r'\*', '.*'), func):
                        return True
            return False

        missing = [f'{dll}!{func}' for dll, funcs in self.allow['imports'].items() for func in funcs
                   if not covered(dll, func)]
        self.assertEqual(missing, [])

    def test_imports_match_the_allowlist_exactly(self):
        imports = self.manifest['audit']['imports']
        self.assertEqual({d: sorted(f) for d, f in imports.items()},
                         {d: sorted(f) for d, f in self.allow['imports'].items()})

    def test_no_code_calls_sdl_report_assertion(self):
        """SDL_ASSERT_LEVEL 0: nothing reaches SDL's assertion handler (and so its process-exit path)."""
        map_text = (self.dll_path.parent / 'ivalice-legacy-coop.map').read_text(encoding='utf-8', errors='replace')
        m = re.search(r'^\s+0x([0-9a-f]+)\s+SDL_ReportAssertion\s*$', map_text, flags=re.M)
        if m is None:
            return  # not even linked
        target = int(m.group(1), 16)
        image = coop_build.audit_pe.PE(self.data)
        image_base = image.image_base
        hits = []
        for section in image.sections:
            if section['name'] != '.text':
                continue
            va = section['va']
            code = self.data[section['raw_ptr']:section['raw_ptr'] + min(section['raw_size'], section['vsize'])]
            for i in range(len(code) - 5):
                if code[i] in (0xE8, 0xE9):
                    rel = struct.unpack_from('<i', code, i + 1)[0]
                    if image_base + va + i + 5 + rel == target:
                        hits.append(hex(image_base + va + i))
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
