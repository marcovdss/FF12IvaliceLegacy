"""Host tests for "Keep running in background" (native/coop/src/focus and its Mod Menu setting). No game run, nothing
deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the patch layer installs on.

* the site rows: FP-01/03/04 are CORE detours with no subsystem bit, FP-V1 gates FP-01, FP-I1/FP-I2 report only, the
  pad focus gate (IN-06) is CORE, the focus-event rewrite (IN-07) is dropped, and the CORE PATCH rows are exactly the
  UI tick and the four background rows;
* arming: after an install the background rows' gates open while the module is dry-armed, with every subsystem bit
  clear (the co-op switch off) and with no guest; a rewritten app writer (FP-V1) skips FP-01; IN-06 unhooked (another
  mod rewrote its approach) makes FP-01 refuse to act (the game never runs in the background with a stale stick);
* the handlers, called directly: FP-01 over every screen state x setting x readiness x active byte x focus (dl forced
  to 1, the state's own byte held at 1 only while focused); IN-06 unfocused gives a neutral gamepad under a flipped
  packet number and the focus flag 0; FP-03/FP-04 give the neutral pad block and mouse state only while another
  window has the foreground;
* unicorn on the hooked twin: the ApplyActive detour (the application's active byte stays 1 for Window/Borderless,
  every other state equals the original bytes), the screen states' own updates with another window in the foreground
  (windowed and borderless keep running, exclusive fullscreen and its lose-focus state pause as stock; borderless
  pauses on the first loop FP-01 stops acting on: setting off or a stale heartbeat), and the game's own XInput poll
  through IN-06 (a held stick reads neutral while unfocused and live again on refocus, without a new packet);
* isolation's substitution host (P1 on SDL, a guest driving the native menus) reads neutral while the game runs
  unfocused, and presses made meanwhile are not replayed on refocus;
* the setting: off until a settings record from Lua is applied (a saved "Off" holds from boot), the schema default is
  on, the Lua store reads, writes and repairs it, the Mod Menu row and its help text exist.
"""
from __future__ import annotations

import ctypes
import json
import random
import struct
import tempfile
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_input_isolation as iso
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, checked

try:
    from lupa import LuaRuntime
except ImportError:                                  # pragma: no cover
    LuaRuntime = None

u8, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32

# game facts (VAs at the pinned base 0x120000)
APPLY_ACTIVE = 0x7A4150
VT_WINDOW, VT_FULLSCREEN, VT_BORDERLESS = 0x1CB3F50, 0x1CB3FA8, 0x1CB4000
VT_LOSE_FOCUS, VT_ON_FULL, VT_ON_WINDOW = 0x1CB4058, 0x1CB40B0, 0x1CB4108
STATES = {'Window': VT_WINDOW, 'FullScreen': VT_FULLSCREEN, 'Borderless': VT_BORDERLESS,
          'FullScreenLoseFocus': VT_LOSE_FOCUS, 'OnChangeFullScreen': VT_ON_FULL, 'OnChangeWindowMode': VT_ON_WINDOW}
KEPT = {VT_WINDOW, VT_BORDERLESS}
UPDATE = {VT_WINDOW: (0x7A40B0, 0), VT_BORDERLESS: (0x7A3E00, 2), VT_FULLSCREEN: (0x7A3EE0, 1),
          VT_LOSE_FOCUS: (0x7A3F80, 1)}
APP_POINTER, APP, APP_VTABLE, APP_ACTIVE = 0x2EA5368, 0x1F7E350, 0x8C5760, 0x1F7E377
FOCUS_FLAG, XI_INTERPRET = 0x2EA5213, 0x7A2243
DI_PAD_FINALIZE, DI_MOUSE_FINALIZE = 0x7A1C9F, 0x7A1A1E
BLOCK, BLOCK_SIZE = 0x414, 0x3B
MOUSE, MOUSE_SIZE = 0x1E8, 15
RUN_ORIGINAL, RESUME = 0, 1
FOCUS_STATS = ('forced', 'passed', 'declined', 'xinput', 'dinput_pad', 'dinput_mouse', 'probe_failed')
BACKGROUND_ROWS = ('FP-01', 'FP-03', 'FP-04', 'IN-06')


class Counters(ctypes.Structure):
    _fields_ = [(n, u64) for n in FOCUS_STATS]


class Status(ctypes.Structure):
    _fields_ = [('keep_running', u8), ('fp01_armed', u8), ('in06_armed', u8), ('fp03_armed', u8), ('fp04_armed', u8),
                ('reserved', u8 * 3)]


def declare(d):
    d.ILCT_FocusSizes.argtypes = [ctypes.POINTER(u32), u32]
    d.ILCT_FocusSizes.restype = u32
    d.ILCT_FocusSetKeep.argtypes = [i32]
    d.ILCT_FocusSetForeground.argtypes = [i32]
    d.ILCT_FocusSetReady.argtypes = [i32]
    d.ILCT_FocusKeep.restype = i32
    d.ILCT_FocusReady.restype = i32
    d.ILCT_FocusConfigure.argtypes = [ctypes.c_char_p]
    d.ILCT_FocusAttach.restype = i32
    d.ILCT_FocusCall.argtypes = [u32, ctypes.POINTER(iso.Ctx), u32]
    d.ILCT_FocusCall.restype = i32
    d.ILCT_FocusStats.argtypes = [ctypes.POINTER(Counters)]
    d.ILCT_FocusStatus.argtypes = [ctypes.POINTER(Status)]
    return d


def gate(twin, sid) -> int:
    return ctypes.string_at(twin.addresses.gate_array + twin.index[sid], 1)[0]


# ---- the site rows (static) ------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowsTest(unittest.TestCase):
    def test_the_background_rows(self):
        rows = {r['id']: r for r in pe.site_rows()}
        for sid in ('FP-01', 'FP-03', 'FP-04'):
            with self.subTest(sid):
                r = rows[sid]
                self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('DETOUR', 'PATCH', 'CORE'))
                self.assertFalse(r['flags'] & 0x03, 'INTERIOR or DROPPED')
                self.assertGreaterEqual(r['hook_len'], 5)
        self.assertEqual(checked(rows['FP-01'])[:6], bytes.fromhex('40 53 48 83 EC 20'))
        self.assertEqual(checked(rows['FP-03'])[:5], bytes.fromhex('3D 1E 00 07 80'))
        self.assertEqual(checked(rows['FP-04'])[:7], bytes.fromhex('48 8B 4F 08 48 8B 01'))
        self.assertEqual((rows['FP-V1']['policy'], rows['FP-V1']['gate']), ('GATE', str(pe.site_rows().index(rows['FP-01']))))
        self.assertEqual(rows['FP-V1']['expected'], bytes.fromhex('88 51 27 33 c0 c3'))
        for sid in ('FP-I1', 'FP-I2'):
            self.assertEqual(rows[sid]['policy'], 'INFO', sid)
        self.assertEqual((rows['IN-06']['policy'], rows['IN-06']['subsystem']), ('PATCH', 'CORE'))
        self.assertEqual(rows['IN-07']['policy'], 'INFO')
        self.assertTrue(rows['IN-07']['flags'] & 0x02, 'IN-07 dropped by decision')
        core = {r['id'] for r in pe.site_rows() if r['policy'] == 'PATCH' and r['subsystem'] == 'CORE'}
        self.assertEqual(core, {'UT-01', *BACKGROUND_ROWS}, 'dry-armed, only these rows may run')

    def test_no_untagged_address_constant(self):
        src = pe.COOP / 'src' / 'focus' / 'focus.cpp'
        bad = [(n, line) for n, line in enumerate(src.read_text(encoding='utf-8').splitlines(), 1)
               if pe.untagged_constants(line)]
        self.assertEqual(bad, [])
        self.assertTrue(pe.untagged_constants('constexpr uint64_t X = 0x80000000u;'), 'instrument')


# ---- the handlers, the arming and the setting on the installed twin -----------------------------------------------------
class Focus:
    """The test DLL's background exports on the installed image twin (isolation's world, which installs the hooks)."""
    _instance = None

    @classmethod
    def get(cls) -> 'Focus':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.iso = iso.Iso.get()
        self.twin = self.iso.twin
        self.d = declare(self.twin.dll)

    def setup(self):
        self.iso.setup()  # install, LIVE, armed, isolation attached
        self.d.ILCT_FocusReset()
        assert self.d.ILCT_FocusAttach() == 1

    def call(self, which, c, epoch=gt.EPOCH) -> int:
        return self.d.ILCT_FocusCall(which, ctypes.byref(c), epoch)

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_FocusStats(ctypes.byref(c))
        return c

    def status(self) -> Status:
        s = Status()
        self.d.ILCT_FocusStatus(ctypes.byref(s))
        return s


class Buffer:
    def __init__(self, size):
        self.raw = ctypes.create_string_buffer(size + 16)
        self.addr = (ctypes.addressof(self.raw) + 15) & ~15

    def put(self, off, data):
        ctypes.memmove(self.addr + off, data, len(data))

    def get(self, off, n):
        return ctypes.string_at(self.addr + off, n)


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Focus.get()
        cls.f.setup()
        cls.d = cls.f.d

    def setUp(self):
        self.d.ILCT_FocusReset()
        self.d.ILCT_FocusSetReady(1)
        self.d.ILCT_FocusSetForeground(1)

    def tearDown(self):
        self.d.ILCT_FocusReset()
        self.d.ILCT_FocusSetReady(self.f.status().in06_armed)

    def test_struct_mirrors(self):
        out = (u32 * 4)()
        self.assertEqual(self.d.ILCT_FocusSizes(out, 4), 2)
        self.assertEqual((out[0], out[1]), (ctypes.sizeof(Counters), ctypes.sizeof(Status)))

    def test_apply_active_over_every_state_setting_byte_and_focus(self):
        runs = 0
        for name, vt in STATES.items():
            for keep in (0, 1):
                for ready in (0, 1):
                    for dl in (0, 1):
                        for focused in (0, 1):
                            self.d.ILCT_FocusSetKeep(keep)
                            self.d.ILCT_FocusSetReady(ready)
                            self.d.ILCT_FocusSetForeground(focused)
                            state = Buffer(0x40)
                            state.put(0, struct.pack('<Q', vt))
                            state.put(0x18, b'\0')
                            c = iso.Ctx()
                            c.rcx, c.rdx = state.addr, 0x5A5A5A5A5A5A5A00 | dl
                            with self.subTest(state=name, keep=keep, ready=ready, dl=dl, focused=focused):
                                self.assertEqual(self.f.call(1, c), RUN_ORIGINAL, 'the original always runs')
                                forced = keep and ready and vt in KEPT
                                self.assertEqual(c.rdx, 0x5A5A5A5A5A5A5A01 if forced else 0x5A5A5A5A5A5A5A00 | dl,
                                                 'dl forced to 1 whatever the focus')
                                self.assertEqual(state.get(0x18, 1), b'\1' if forced and focused else b'\0',
                                                 "the state's own byte is held only while focused")
                                self.assertEqual(c.resume, 0)
                            runs += 1
        self.assertEqual(runs, 96)
        s = self.f.stats()
        self.assertEqual((s.forced, s.passed), (8, 16), 'keep and ready: 2 states x 2 bytes x 2 focus held, 4 passed')
        self.assertEqual(s.declined, 72, 'setting off or IN-06 not hooked: stock')

    def test_apply_active_unreadable_state_is_stock(self):
        c = iso.Ctx()
        c.rcx, c.rdx = 0, 0
        self.assertEqual(self.f.call(1, c), RUN_ORIGINAL)
        self.assertEqual((c.rdx, self.f.stats().probe_failed), (0, 1))

    def xinput_frame(self):
        frame = Buffer(0x100)
        frame.put(0x28, struct.pack('<I', 0x1234))
        frame.put(0x2C, bytes(range(1, 13)))  # a held gamepad
        frame.put(0x38, b'\xEE' * 8)
        return frame

    def test_in06_neutral_xinput_while_unfocused(self):
        self.f.twin.poke(FOCUS_FLAG, b'\1')
        for zf, keep, acts in ((0x40, 1, False), (0, 0, False), (0, 1, True)):
            with self.subTest(focused=bool(zf), keep=keep):
                self.d.ILCT_FocusSetKeep(keep)
                frame = self.xinput_frame()
                c = iso.Ctx()
                c.rsp, c.rflags = frame.addr, 0x202 | zf
                result = self.f.call(6, c)
                if not acts:
                    self.assertEqual((result, c.resume), (RUN_ORIGINAL, 0))
                    self.assertEqual(frame.get(0x28, 16), struct.pack('<I', 0x1234) + bytes(range(1, 13)))
                    continue
                self.assertEqual((result, c.resume), (RESUME, XI_INTERPRET))
                self.assertEqual(frame.get(0x2C, 12), bytes(12), 'a zero gamepad')
                self.assertEqual(struct.unpack('<I', frame.get(0x28, 4))[0], 0x1234 ^ 0x80000000,
                                 'a packet number the stored one never equals')
                self.assertEqual(frame.get(0x38, 8), b'\xEE' * 8, 'nothing past the gamepad')
                self.assertEqual(ctypes.string_at(FOCUS_FLAG, 1), b'\0', 'the focus flag says unfocused')
        self.assertEqual(self.f.stats().xinput, 1)

    def device_case(self, which, area, size, finalize):
        dev = Buffer(0x470)
        dev.put(0, b'\xCC' * 0x470)
        for foreground, keep, acts in ((1, 1, False), (0, 0, False), (0, 1, True)):
            with self.subTest(which=which, foreground=foreground, keep=keep):
                dev.put(0, b'\xCC' * 0x470)
                self.d.ILCT_FocusSetKeep(keep)
                self.d.ILCT_FocusSetForeground(foreground)
                c = iso.Ctx()
                c.rbx = dev.addr
                result = self.f.call(which, c)
                if not acts:
                    self.assertEqual((result, dev.get(0, 0x470)), (RUN_ORIGINAL, b'\xCC' * 0x470))
                    continue
                self.assertEqual((result, c.resume), (RESUME, finalize))
                self.assertEqual(dev.get(area, size), bytes(size))
                self.assertEqual(dev.get(area - 1, 1) + dev.get(area + size, 1), b'\xCC\xCC', 'only the state')

    def test_directinput_pad_and_mouse_neutral_while_unfocused(self):
        self.device_case(3, BLOCK, BLOCK_SIZE, DI_PAD_FINALIZE)
        self.device_case(4, MOUSE, MOUSE_SIZE, DI_MOUSE_FINALIZE)
        s = self.f.stats()
        self.assertEqual((s.dinput_pad, s.dinput_mouse), (1, 1))

    def test_the_setting_follows_the_applied_settings(self):
        defaults = bytearray(gt_defaults())
        self.assertEqual(defaults[keep_running_offset()], 1, 'the schema default: on')
        self.assertEqual(struct.unpack_from('<I', defaults, GENERATION)[0], 0, 'the native defaults record')
        self.d.ILCT_FocusSetKeep(1)
        self.d.ILCT_FocusConfigure(bytes(defaults))
        self.assertEqual(self.d.ILCT_FocusKeep(), 0, 'nothing read from the player yet: off (a saved "Off" holds)')
        struct.pack_into('<I', defaults, GENERATION, 1)
        self.d.ILCT_FocusConfigure(bytes(defaults))
        self.assertEqual(self.d.ILCT_FocusKeep(), 1, "Lua's first record with the default: on")
        struct.pack_into('<I', defaults, GENERATION, 2)
        defaults[keep_running_offset()] = 0
        self.d.ILCT_FocusConfigure(bytes(defaults))
        self.assertEqual(self.d.ILCT_FocusKeep(), 0, 'a saved "Off"')
        self.assertEqual(self.f.call(1, self.window_state()), RUN_ORIGINAL)
        self.assertEqual(self.f.stats().declined, 1, 'off: FP-01 is stock')

    def window_state(self):
        state = Buffer(0x40)
        state.put(0, struct.pack('<Q', VT_WINDOW))
        c = iso.Ctx()
        c.rcx = state.addr
        c._state = state  # kept alive with the context
        return c

    def test_the_native_flag_starts_off(self):
        src = (pe.COOP / 'src' / 'focus' / 'focus.cpp').read_text(encoding='utf-8')
        self.assertIn('volatile uint32_t g_keep_running = 0;', src, 'off from boot until the settings are read')
        self.assertIn('s.header.generation != 0 && s.keep_running != 0', src)


GENERATION = 12  # ilc_settings.header.generation (u32; 0 = the native defaults)


def gt_defaults() -> bytes:
    from coop_native_support import SCHEMA
    return bytes.fromhex(SCHEMA.json['defaults']['hex'])


def keep_running_offset() -> int:
    from coop_native_support import SCHEMA
    for f in SCHEMA.json['structs']['ilc_settings']['fields']:
        if f['name'] == 'keep_running':
            return f['offset']
    raise AssertionError('keep_running is not in the settings record')


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ArmingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Focus.get()
        cls.twin = cls.f.twin
        cls.d = cls.f.d

    def tearDown(self):
        self.f.setup()

    def test_the_background_rows_act_dry_armed_with_every_subsystem_off(self):
        self.f.setup()
        d = self.d
        d.ILCT_PatchSetMode(gt.DRY)
        d.ILCT_PatchApplySubsystems(0)  # the co-op switch off: every subsystem bit clear
        for sid in ('UT-01', *BACKGROUND_ROWS):
            self.assertEqual(gate(self.twin, sid), 1, sid)
        for sid in ('IN-01', 'FW-01', 'MV-01', 'FG-01'):
            self.assertEqual(gate(self.twin, sid), 0, f'{sid}: a co-op row stays stock while dry-armed')
        s = self.f.status()
        self.assertEqual((s.fp01_armed, s.in06_armed, s.fp03_armed, s.fp04_armed), (1, 1, 1, 1))
        self.assertEqual(d.ILCT_FocusReady(), 1)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_PatchApplySubsystems(0xFF)

    def test_a_rewritten_app_writer_skips_fp01(self):
        self.twin.reset()
        r = self.twin.rows[self.twin.index['FP-V1']]
        self.twin.poke(TWIN_BASE + r['rva'], b'\xC3')  # as another mod that rewrites the writer would
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.gated_off, report.hooked), (OK, 1, pe.PATCH_ROWS - 1))
        self.assertEqual(self.d.ILCT_FocusAttach(), 1)
        s = self.f.status()
        self.assertEqual((s.fp01_armed, s.in06_armed), (0, 1))
        site = TWIN_BASE + self.twin.rows[self.twin.index['FP-01']]['rva']
        self.assertEqual(ctypes.string_at(site, 9), bytes.fromhex('40 53 48 83 EC 20 0F B6 DA'), 'stock bytes')

    def test_without_in06_fp01_never_acts(self):
        self.twin.reset()
        r = self.twin.rows[self.twin.index['IN-06a']]
        self.twin.poke(TWIN_BASE + r['rva'], b'\xE9')  # another mod's own auto-pause switch
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.gated_off), (OK, 1))
        self.assertEqual(self.d.ILCT_FocusAttach(), 1)
        self.assertEqual((self.f.status().in06_armed, self.d.ILCT_FocusReady()), (0, 0))
        state = Buffer(0x40)
        state.put(0, struct.pack('<Q', VT_WINDOW))
        c = iso.Ctx()
        c.rcx, c.rdx = state.addr, 0
        self.d.ILCT_FocusSetKeep(1)
        self.assertEqual(self.f.call(1, c), RUN_ORIGINAL)
        self.assertEqual((c.rdx, state.get(0x18, 1)), (0, b'\0'), 'fail closed: the stock pause, no stale stick')


# ---- unicorn on the hooked twin ------------------------------------------------------------------------------------------
class FocusEmulator(iso.IsoEmulator):
    """The isolation emulator, answering the window message the ApplyActive function posts."""

    def __init__(self, twin):
        super().__init__(twin)
        self.posted = []

    def _exe_import(self, uc, address, size, _):
        if self.exe_stub.get(address) == 'PostMessageA':
            X = iso.X
            self.posted.append(tuple(uc.reg_read(r) for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8)))
            uc.reg_write(X.UC_X86_REG_RAX, 1)
            return
        super()._exe_import(uc, address, size, _)


@native_test
@unittest.skipUnless(NATIVE and GAME and iso.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class FocusEmulationTest(unittest.TestCase):
    TRIALS = 4

    @classmethod
    def setUpClass(cls):
        cls.f = Focus.get()
        cls.f.setup()
        cls.twin = cls.f.twin
        cls.d = cls.f.d
        with quiet_faulthandler():
            cls.emu = FocusEmulator(cls.twin)
        uc = cls.emu.uc
        uc.mem_write(APP_POINTER, struct.pack('<Q', APP))  # the application singleton, as its static constructor sets it
        uc.mem_write(APP, struct.pack('<Q', APP_VTABLE))
        cls.di_inst = cls.emu.alloc(0x244)
        uc.mem_write(cls.di_inst, struct.pack('<I', 0x244))

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_FocusReset()
        cls.twin.reset()

    def arm(self, keep, rows_open=BACKGROUND_ROWS, foreground=0):
        d, emu = self.d, self.emu
        d.ILCT_FocusSetKeep(keep)
        d.ILCT_FocusSetReady(1)
        d.ILCT_FocusSetForeground(foreground)  # never asks Windows from inside the emulator
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        for sid in BACKGROUND_ROWS:
            emu.force_gate(self.twin.index[sid], sid in rows_open)
        for sid in ('IN-01', 'IN-02', 'IN-03', 'IN-05'):
            emu.force_gate(self.twin.index[sid], False)
        emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQI', 1, gt.EPOCH, gt.NOW, 0))

    def state(self, vt, active=0):
        s = self.emu.alloc(0x40)
        self.emu.uc.mem_write(s, struct.pack('<Q', vt) + struct.pack('<i', -1) + b'\0' * 4)  # vtable, next = stay
        self.emu.uc.mem_write(s + 0x18, bytes([active]))
        return s

    def app_active(self) -> int:
        return self.emu.bytes_at(APP_ACTIVE, 1)[0]

    def test_apply_active_holds_the_application_active_in_window_and_borderless(self):
        for name, vt in STATES.items():
            for keep in (0, 1):
                for dl in (0, 1):
                    for focused in (0, 1):
                        with self.subTest(state=name, keep=keep, dl=dl, focused=focused):
                            self.arm(keep, foreground=focused)
                            s = self.state(vt, active=dl)
                            self.emu.uc.mem_write(APP_ACTIVE, b'\x77')
                            self.emu.posted.clear()
                            self.emu.call(APPLY_ACTIVE, s, dl)
                            kept = keep and vt in KEPT
                            self.assertEqual(self.app_active(), 1 if kept else dl, 'the application active byte')
                            self.assertEqual(self.emu.bytes_at(s + 0x18, 1)[0], 1 if kept and focused else dl,
                                             "the state's own byte: held at 1 only while focused")
                            self.assertEqual(self.emu.posted, [(iso.FAKE_HWND, 0x8001, 0 if kept else int(dl == 0))],
                                             'the cursor request follows the active byte')
        self.assertEqual(self.emu.exe_unknown, set())

    def test_apply_active_equals_the_original_bytes_whenever_it_must_not_act(self):
        r = self.twin.rows[self.twin.index['FP-01']]
        site = TWIN_BASE + r['rva']
        out = lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE)  # noqa: E731
        runs = 0
        for label, keep, vts in (('setting off', 0, list(STATES.values())),
                                 ('fullscreen states', 1, [VT_FULLSCREEN, VT_LOSE_FOCUS, VT_ON_FULL, VT_ON_WINDOW])):
            self.arm(keep)
            for vt in vts:
                for trial in range(self.TRIALS):
                    state = pe.random_state(random.Random(0xF0C5 + vt + trial))
                    state['gpr']['RSP'] = (state['gpr']['RSP'] & ~0xF) - 8
                    obj = pe.SCRATCH + 0x40000 + trial * 0x100
                    self.emu.uc.mem_write(obj, struct.pack('<Q', vt))
                    state['gpr']['RCX'] = obj
                    with self.subTest(case=label, vt=hex(vt), trial=trial):
                        patched = bytes(self.emu.uc.mem_read(site, r['hook_len']))
                        self.emu.write_code(site, checked(r)[:r['hook_len']])
                        try:
                            a = self.emu.run(site, lambda x: out(x) or site <= x < site + r['hook_len'], state)
                        finally:
                            self.emu.write_code(site, patched)
                        b = self.emu.run(site, lambda x: out(x) or site <= x < site + 5, state)
                        baseline = {x: self.emu.uc.mem_read(x, 1)[0] for x in set(a['final']) | set(b['final'])}
                        gt.compare(self, self.emu, f'FP-01 {label}', a, b, baseline, state['gpr']['RSP'])
                        runs += 1
        self.assertEqual(runs, (6 + 4) * self.TRIALS)

    def run_update(self, vt, keep, active):
        fn, mode = UPDATE[vt]
        self.arm(keep)
        s = self.state(vt, active=active)
        self.emu.uc.mem_write(APP_ACTIVE, b'\1')
        self.emu.foreground = 0x9999  # another application's window has the foreground
        try:
            self.emu.call(fn, s, mode)
        finally:
            self.emu.foreground = iso.FAKE_HWND
        return self.app_active()

    def test_screen_states_with_another_window_in_the_foreground(self):
        # Windowed: the Window update polls the foreground itself and clears its active byte every frame.
        self.assertEqual(self.run_update(VT_WINDOW, keep=0, active=1), 0, 'stock windowed: the pause starts')
        self.assertEqual(self.run_update(VT_WINDOW, keep=1, active=1), 1, 'windowed keeps running')
        # Borderless: focus loss arrives as an event that cleared the active byte (no poll of its own).
        self.assertEqual(self.run_update(VT_BORDERLESS, keep=0, active=0), 0, 'stock borderless: the pause starts')
        self.assertEqual(self.run_update(VT_BORDERLESS, keep=1, active=0), 1, 'borderless keeps running')
        # Exclusive fullscreen: the lose-focus state passes 0 and the game leaves exclusive mode its own way.
        self.assertEqual(self.run_update(VT_LOSE_FOCUS, keep=1, active=0), 0, 'exclusive fullscreen pauses as stock')
        self.assertEqual(self.run_update(VT_FULLSCREEN, keep=1, active=0), 1, 'fullscreen while it has the screen')
        self.assertEqual(self.emu.exe_unknown - {'PostMessageA'}, set())

    def test_borderless_unfocused_pauses_on_the_first_loop_fp01_does_not_act_on(self):
        """Borderless, unfocused: one loop with the setting on keeps the application active without holding the
        state's own byte, so the next loop pauses as stock when FP-01 stops acting (the setting off, or a stale
        heartbeat that sends the detour to the trampoline): never a game running unfocused with a stale stick."""
        fn, mode = UPDATE[VT_BORDERLESS]
        for label in ('setting off', 'stale heartbeat'):
            with self.subTest(label):
                self.arm(1)
                s = self.state(VT_BORDERLESS, active=0)  # the focus-loss event cleared it
                self.emu.uc.mem_write(APP_ACTIVE, b'\1')
                self.emu.foreground = 0x9999  # another application's window has the foreground
                try:
                    self.emu.call(fn, s, mode)
                    self.assertEqual(self.app_active(), 1, 'instrument: one loop kept running')
                    self.assertEqual(self.emu.bytes_at(s + 0x18, 1), b'\0', 'the state byte is not held unfocused')
                    if label == 'setting off':
                        self.arm(0)
                    else:
                        self.emu.tick_clock(gt.NOW + 2500)  # the heartbeat (gt.NOW) is more than 2 s old
                    self.emu.call(fn, s, mode)
                finally:
                    self.emu.foreground = iso.FAKE_HWND
                self.assertEqual(self.app_active(), 0, f'{label}: the stock pause starts on the next loop')
        self.assertEqual(self.emu.exe_unknown - {'PostMessageA'}, set())

    # ---- IN-06 through the game's own XInput poll ----
    def make_pad(self):
        """An XInput object built by the game's constructor, bound like any pad (stick nodes, button nodes)."""
        self.emu.uc.mem_write(iso.DEVLIST, struct.pack('<QQ', iso.DEVLIST, iso.DEVLIST))
        self.di_inst = self.__class__.di_inst
        dev = iso.IsolationEmulationTest.make(self, 'XI', 0)
        iso.IsolationEmulationTest.bind(self, [dev])
        return dev

    def poll(self, dev, packet, pad, foreground):
        self.emu.xinput[0] = (0, iso.xstate(packet, pad))
        self.emu.foreground = iso.FAKE_HWND if foreground else 0x9999
        try:
            self.emu.call(iso.XI_POLL, dev['dev'])
        finally:
            self.emu.foreground = iso.FAKE_HWND
        return iso.IsolationEmulationTest.snapshot(self, dev)

    @staticmethod
    def level(snap):
        """The device as the game reads it, without the one-poll press edges (byte 1 of each button node)."""
        return dict(snap, buttons=[b[0] for b in snap['buttons']])

    def test_a_held_stick_reads_neutral_unfocused_and_live_again_on_refocus(self):
        held = (0x1000, 0, 255, 20000, -20000, 0, 0)  # A, RT, the left stick pushed
        for keep, gate_open in ((0, True), (1, False), (1, True)):
            with self.subTest(keep=keep, gate_open=gate_open):
                self.maxDiff = None
                self.arm(keep, rows_open=('IN-06',) if gate_open else ())
                dev = self.make_pad()
                neutral = self.level(self.poll(self.make_pad(), 1, (0,) * 7, True))
                live = self.poll(dev, 50, held, True)
                self.assertNotEqual(self.level(live)['fields'], neutral['fields'], 'instrument: the stick is held')
                away = self.level(self.poll(dev, 50, held, False))
                acts = keep and gate_open
                self.assertEqual(away, neutral if acts else self.level(live),
                                 'unfocused: neutral' if acts else 'stock: the last state stays (the game is paused)')
                if acts:
                    self.assertEqual(self.emu.bytes_at(FOCUS_FLAG, 1), bytes(1), 'the focus flag says unfocused')
                back = self.poll(dev, 50, held, True)
                self.assertEqual(self.level(back), self.level(live), 'refocus with the same packet: live again')
                if acts:
                    pressed = [b[1] for b in back['buttons'] if b[0]]
                    self.assertTrue(pressed and all(pressed), 'released while away: the press is a new edge')
        self.assertEqual(self.emu.exe_unknown, set())


# ---- isolation's substitution host while the game runs unfocused ------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class BackgroundHostTest(unittest.TestCase):
    """The substitution host (P1 on SDL, or a guest driving the native menus) is written neutral while the setting is on
    and another window has the foreground; presses made meanwhile are consumed, never replayed on refocus."""
    A = 1  # ilc_pad_bit(ILC_PAD_A): edge bit 0
    ISO_DRIVER = 0x80

    @classmethod
    def setUpClass(cls):
        cls.f = Focus.get()
        cls.iso = cls.f.iso
        d = cls.iso.d
        for name, args in (('ILCT_EdgeReset', [u32]), ('ILCT_EdgeRecord', [u32, ctypes.POINTER(u32)])):
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, None

    def setUp(self):
        self.f.setup()
        self.d = self.f.d
        self.iso.d.ILCT_IsoFakeFlee(gt.EPOCH, 0)
        self.xi0, self.xi1 = iso.Device(iso.VT_XI, 0), iso.Device(iso.VT_XI, 1)
        self.pads = [(1, self.xi0), (1, self.xi1)]
        self.seq = 0
        self.iso.d.ILCT_EdgeReset(0x61)
        self.tick()

    def tearDown(self):
        self.iso.d.ILCT_IsoFakeFlee(0, 0)
        self.d.ILCT_FocusReset()
        self.iso.twin.reset()

    def tick(self, seat=None, pressed=0):
        masks = (u32 * 28)()
        if seat is not None:
            masks[(seat - 1) * 7] = pressed      # pressed
            masks[(seat - 1) * 7 + 1] = pressed  # ... and released within the tick: a tap
        self.seq += 1
        self.iso.d.ILCT_EdgeRecord(self.seq, masks)

    def visit(self, flags, driver=0, keep=1, focused=1):
        self.d.ILCT_FocusSetKeep(keep)
        self.d.ILCT_FocusSetForeground(focused)
        record = iso.IsoRecord(gt.EPOCH, flags, 1, 0xFF, 0, 0b10, driver, 0, 0, 0)
        self.iso.d.ILCT_IsoPublish(ctypes.byref(record))
        for _, dev in self.pads:
            dev.reset()
        self.assertEqual([self.iso.call(w, dev.ctx()) for w, dev in self.pads], [RUN_ORIGINAL] * 2,
                         'the finalizer always runs')
        return self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE)

    def test_p1_on_sdl_reads_neutral_unfocused_and_live_on_refocus(self):
        held = (0x1000, 0, 200, 20000, -20000, 0, 0)
        self.iso.mirror(1, iso.SRC_SDL, pad=held)
        sub = iso.ISO_GUARD | iso.ISO_SUBST | iso.ISO_BG
        live = iso.reference_block(held)
        self.assertEqual(self.visit(sub), live, 'instrument: focused, the host carries P1')
        before = self.iso.stats()['background']
        away = self.visit(sub, focused=0)
        self.assertEqual(away, bytes(iso.BLOCK_SIZE), 'unfocused: neutral')
        self.assertEqual(self.xi0.peek(0x45C, 4), bytes(4), 'the next real packet is interpreted again')
        self.assertTrue(self.iso.stats()['background'] > before)
        self.assertEqual(self.visit(sub, keep=0, focused=0), live, 'setting off: stock substitution (the game pauses)')
        self.assertEqual(self.visit(sub), live, 'refocus: live again')
        self.assertEqual(self.xi1.peek(iso.BLOCK, iso.BLOCK_SIZE), bytes(iso.BLOCK_SIZE), 'the other pad is masked')

    def test_a_tap_made_unfocused_is_not_replayed_on_refocus(self):
        idle = (0, 0, 0, 0, 0, 0, 0)
        self.iso.mirror(1, iso.SRC_SDL, pad=idle)
        sub = iso.ISO_GUARD | iso.ISO_SUBST | iso.ISO_BG
        neutral = iso.reference_block(idle)
        self.assertEqual(self.visit(sub), neutral)
        self.tick(1, self.A)
        self.assertNotEqual(self.visit(sub), neutral, 'instrument: a focused tap is injected')
        self.assertEqual(self.visit(sub), neutral)
        self.tick(1, self.A)  # P1 taps A while another window has the foreground
        self.tick()
        self.assertEqual(self.visit(sub, focused=0), bytes(iso.BLOCK_SIZE))
        self.assertEqual(self.visit(sub), neutral, 'the tap made unfocused was replayed on refocus')

    def test_a_guest_driving_the_native_menus_reads_neutral_unfocused(self):
        guest = (0x1000 | 0x0001, 0, 0, 0, 20000, 0, 0)
        self.iso.mirror(1, iso.SRC_XINPUT, slot=0)
        self.iso.mirror(2, iso.SRC_SDL, pad=guest)
        drive = self.ISO_DRIVER | iso.ISO_GUARD | iso.ISO_NATIVE | iso.ISO_BG
        self.assertEqual(self.visit(drive, driver=1), iso.reference_block(guest), "instrument: the guest's pad")
        self.assertEqual(self.visit(drive, driver=1, focused=0), bytes(iso.BLOCK_SIZE), 'unfocused: neutral')
        self.assertEqual(self.visit(drive, driver=1), iso.reference_block(guest), 'refocus: the guest drives again')


# ---- the setting in the Lua store and the Mod Menu page ---------------------------------------------------------------
@unittest.skipUnless(LuaRuntime is not None and (NATIVE or iso.pe.REQUIRE_NATIVE), 'requires lupa')
class LuaSettingTest(unittest.TestCase):
    def store(self, root):
        import test_coop_settings_handshake as sh
        return sh.SettingsHandshake.store(None, root)

    def test_new_store_on_written_and_read_back(self):
        import test_coop_settings_handshake as sh
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            self.assertIs(s.state.keepRunning, True, 'the native default: on')
            doc = json.loads((root / sh.PATH).read_text())
            self.assertIs(doc['keepRunning'], True)
            lua.globals().S = s
            lua.execute('S.change(function(st) st.keepRunning = false end, nil)')
            self.assertIs(json.loads((root / sh.PATH).read_text())['keepRunning'], False)
            byte = lua.eval('function(S, g, k) return string.byte(S.encode(S.state, g), k + 1) end')
            self.assertEqual(byte(s, 1, keep_running_offset()), 0, 'the record carries it at its schema offset')
            lua.execute('S.change(function(st) st.keepRunning = true end, nil)')
            self.assertEqual(byte(s, 2, keep_running_offset()), 1)

    def test_a_bad_value_is_repaired_to_the_default(self):
        import test_coop_settings_handshake as sh
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            doc = json.loads((root / sh.PATH).read_text())
            doc['keepRunning'] = 'yes'
            (root / sh.PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.state.keepRunning), ('repaired', True))

    def test_the_mod_menu_row(self):
        page = (iso.pe.ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop' / 'page.lua').read_text(
            encoding='utf-8')
        self.assertIn("toggle(main, 'keepRunning', 'Keep running in background', {'Off', 'On'},", page)
        help_text = page.split("'keepRunning', 'Keep running in background'", 1)[1].split('cancelRow', 1)[0]
        for phrase in ('controller and mouse are ignored', 'with co-op off too', 'Windowed and borderless only'):
            self.assertIn(phrase, help_text)
        self.assertIn("'keepRunning'", page.split('M.SETTINGS_ROWS = ', 1)[1].split('\n', 1)[0])


if __name__ == '__main__':
    unittest.main()
