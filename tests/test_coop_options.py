"""Host tests for the 0.1.53 co-op options on the native side.
No game run, nothing deployed.

* "Co-op On means every subsystem works": the arm mask a settings record asks for is every subsystem, less the
  developer disarm override (honoured only while developer = 1) and less CAMERA while the shared camera is off;
* the player options word (shared camera, guest interactions, towns, Wait rule, guests' pause, the battle log's line
  counts and fade time) follows the applied record, falls back to the defaults on bad values, and starts as the
  native defaults;
* the Wait rule's ANY / ALL choice over every four-seat case, against a Python oracle;
* the guests' PAUSE (firewall pulse) and PARTY (frontend) follow guest_pause; P1 is never affected;
* hook.log: lines are queued without blocking, prefixed, gated by log_level, capped by the ring, handed to the loader's
  logger by the drain only, and dropped (counted) when no logger is loaded; the logger is found by name among the
  modules already loaded (no import, no LoadLibrary);
* the record copies at mid-function detour sites stay inline with the pinned compiler at their new four-seat sizes.
"""
from __future__ import annotations

import ctypes
import itertools
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from coop_native_support import COMPILER, SCHEMA, native_test
from test_coop_input_service import fresh_test_dll

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
SRC = COOP / 'src'

S = SCHEMA
SUB = S.enums['subsystem']
EVERY = sum(SUB.values())
u8, u32, u64, i32, i64 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32, ctypes.c_int64
P = ctypes.POINTER
SETTINGS = ctypes.c_uint8 * S.size('ilc_settings')


class View(ctypes.Structure):
    _fields_ = [('shared_camera', u8), ('guest_interactions', u8), ('town_view', u8), ('wait_pause', u8),
                ('guest_pause', u8), ('log_lines_compact', u8), ('log_lines_expanded', u8), ('log_fade_s', u8)]


class LogCounters(ctypes.Structure):
    _fields_ = [('posted', u64), ('written', u64), ('dropped_full', u64), ('dropped_busy', u64), ('dropped_quiet', u64),
                ('dropped_no_sink', u64)]


class SeatView(ctypes.Structure):
    _fields_ = [('role', u8), ('open', u8)]


def declare(d):
    sig = {
        'ILCT_OptDefaults': ([ctypes.c_void_p], None),
        'ILCT_OptEffective': ([ctypes.c_char_p], u32),
        'ILCT_OptFromSettings': ([ctypes.c_char_p, P(View)], None),
        'ILCT_OptConfigure': ([ctypes.c_char_p], None),
        'ILCT_OptCurrent': ([P(View)], None),
        'ILCT_OptSet': ([P(View)], None),
        'ILCT_OptReset': ([], None),
        'ILCT_WaitPauseWantedRule': ([P(SeatView), u32], i32),
        'ILCT_WaitPauseWanted': ([P(SeatView)], i32),
        'ILCT_FwGuestPulseMay': ([u32, u32, i32, i32], u32),
        'ILCT_FwGuestPulse': ([u32, u32, i32], u32),
        'ILCT_LogPost': ([u32, ctypes.c_char_p, i32], i32),
        'ILCT_LogPostNumbers': ([u32, ctypes.c_char_p, u64, i64, u64], i32),
        'ILCT_LogDrain': ([], u32),
        'ILCT_LogSetVerbose': ([i32], None),
        'ILCT_LogStats': ([P(LogCounters)], None),
        'ILCT_LogCapture': ([i32], None),
        'ILCT_LogCaptured': ([ctypes.c_char_p, u32, P(u32)], u32),
        'ILCT_LogReset': ([], None),
        'ILCT_LogSizes': ([P(u32), u32], u32),
    }
    for name, (args, res) in sig.items():
        f = getattr(d, name)
        f.argtypes = args
        f.restype = res
    return d


def record(**fields) -> bytes:
    rec = S.decode('ilc_settings', S.defaults())
    for k, v in fields.items():
        rec[k] = v
    return bytes(S.encode('ilc_settings', rec))


# ---- schema (static) ------------------------------------------------------------------------------------------------
class SchemaTest(unittest.TestCase):
    def fields(self):
        return {f['name']: f for f in S.json['structs']['ilc_settings']['fields']}

    def test_the_new_fields_and_their_defaults(self):
        d = S.decode('ilc_settings', S.defaults())
        want = {'shared_camera': (0, 1, 1), 'guest_interactions': (0, 1, 1), 'town_view': (0, 1, 0),
                'wait_pause': (0, 1, 0), 'guest_pause': (0, 1, 1), 'log_lines_compact': (1, 8, 5),
                'log_lines_expanded': (5, 20, 20), 'log_fade_s': (3, 15, 6), 'subsystems_disarm': (0, EVERY, 0),
                'log_level': (0, 1, 0)}
        f = self.fields()
        for name, (lo, hi, default) in want.items():
            with self.subTest(name):
                self.assertEqual((f[name]['min'], f[name]['max'], d[name]), (lo, hi, default))
        self.assertEqual(S.enums['town_view'], {'PLAYERS': 0, 'PARTY': 1})
        self.assertEqual(S.enums['wait_pause'], {'ALL': 0, 'ANY': 1})
        self.assertEqual(S.size('ilc_settings'), 3572)
        self.assertEqual(bytes(d['hud_theme']), bytes(32), 'the HUD theme: all zero = the built-in default')

    def test_dead_fields_are_gone_and_positions_hold(self):
        names = [f['name'] for f in S.json['structs']['ilc_settings']['fields']]
        self.assertNotIn('all_characters_at_start', names)
        self.assertNotIn('subsystems_armed', names)
        self.assertEqual(S.json['structs']['ilc_settings']['fields'][2]['kind'], 'PAD', 'position 2 is reserved')
        self.assertEqual(names.index('subsystems_disarm'), 16, 'the override took the old mask\'s position')
        self.assertEqual(names[22:], ['shared_camera', 'guest_interactions', 'town_view', 'wait_pause', 'guest_pause',
                                      'log_lines_compact', 'log_lines_expanded', 'log_fade_s', 'hud_theme'],
                         'appended at the end')

    def test_hud_element_bits(self):
        HUD = S.enums['hud_element']
        self.assertEqual((HUD['PARTY_TIMERS'], HUD['GUEST_CURSORS']), (0x800, 0x1000))
        # The default is the Mod Menu's Standard preset (MENU-NOTES preset table): every element but PARTY_TIMERS.
        standard = 0x1FFF & ~HUD['PARTY_TIMERS']
        self.assertEqual(S.decode('ilc_settings', S.defaults())['hud_elements'], standard)
        self.assertEqual(standard, sum(v for k, v in HUD.items() if k != 'PARTY_TIMERS'))


# ---- native: options and the arm mask -------------------------------------------------------------------------------
@native_test
class OptionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def setUp(self):
        self.d.ILCT_OptReset()

    def view(self, rec: bytes) -> View:
        v = View()
        self.d.ILCT_OptFromSettings(rec, ctypes.byref(v))
        return v

    def test_native_defaults_are_the_schema_defaults(self):
        native = SETTINGS()
        self.d.ILCT_OptDefaults(native)
        self.assertEqual(bytes(native), S.defaults())

    def test_every_subsystem_is_armed_unless_a_developer_or_the_camera_switch_says_otherwise(self):
        for developer, disarm, camera in itertools.product((0, 1), (0, SUB['RENDER'], SUB['CAMERA'] | SUB['WAIT'], EVERY),
                                                           (0, 1)):
            with self.subTest(developer=developer, disarm=disarm, camera=camera):
                want = EVERY
                if developer:
                    want &= ~disarm
                if not camera:
                    want &= ~SUB['CAMERA']
                got = self.d.ILCT_OptEffective(record(developer=developer, subsystems_disarm=disarm, shared_camera=camera))
                self.assertEqual(got, want)
        self.assertEqual(self.d.ILCT_OptEffective(S.defaults()), EVERY, 'the defaults arm everything')

    def test_the_view_follows_the_record_and_bad_values_fall_back(self):
        v = self.view(record(shared_camera=0, guest_interactions=0, town_view=1, wait_pause=1, guest_pause=0,
                             log_lines_compact=3, log_lines_expanded=12, log_fade_s=9))
        self.assertEqual((v.shared_camera, v.guest_interactions, v.town_view, v.wait_pause, v.guest_pause,
                          v.log_lines_compact, v.log_lines_expanded, v.log_fade_s), (0, 0, 1, 1, 0, 3, 12, 9))
        v = self.view(record(town_view=7, wait_pause=9, log_lines_compact=0, log_lines_expanded=99, log_fade_s=1))
        self.assertEqual((v.town_view, v.wait_pause, v.log_lines_compact, v.log_lines_expanded, v.log_fade_s),
                         (0, 0, 5, 20, 6))
        v = self.view(record(log_lines_compact=8, log_lines_expanded=6))
        self.assertEqual((v.log_lines_compact, v.log_lines_expanded), (6, 6), 'compact never above expanded')

    def test_configure_publishes_and_reset_returns_to_the_defaults(self):
        cur = View()
        self.d.ILCT_OptCurrent(ctypes.byref(cur))
        self.assertEqual(bytes(cur), bytes(self.view(S.defaults())), 'before any record: the native defaults')
        self.d.ILCT_OptConfigure(record(guest_pause=0, town_view=1))
        self.d.ILCT_OptCurrent(ctypes.byref(cur))
        self.assertEqual((cur.guest_pause, cur.town_view), (0, 1))
        self.d.ILCT_OptReset()
        self.d.ILCT_OptCurrent(ctypes.byref(cur))
        self.assertEqual((cur.guest_pause, cur.town_view), (1, 0))

    # ---- the Wait rule ----
    def test_wait_rule_any_and_all_over_every_four_seat_case(self):
        ABSENT, PARTICIPANT, BENCHED = 0, 1, 2
        seats = (SeatView * 4)()
        cases = 0
        for roles in itertools.product((ABSENT, PARTICIPANT, BENCHED), repeat=4):
            for opens in itertools.product((0, 1), repeat=4):
                for k in range(4):
                    seats[k].role, seats[k].open = roles[k], opens[k]
                part = [o for r, o in zip(roles, opens) if r == PARTICIPANT]
                want_all = bool(part) and all(part)
                want_any = any(part)
                self.assertEqual(bool(self.d.ILCT_WaitPauseWantedRule(seats, S.e('wait_pause', 'ALL'))), want_all)
                self.assertEqual(bool(self.d.ILCT_WaitPauseWantedRule(seats, S.e('wait_pause', 'ANY'))), want_any)
                self.assertEqual(bool(self.d.ILCT_WaitPauseWanted(seats)), want_all, 'the default rule is ALL')
                cases += 1
        self.assertEqual(cases, 81 * 16)

    def test_the_frontend_passes_the_rule(self):
        src = (SRC / 'frontend/frontend.cpp').read_text(encoding='utf-8')
        self.assertIn('wait::pause_wanted(views, options::wait_pause())', src)

    # ---- the guests' PAUSE and PARTY ----
    def test_guest_pause_follows_the_option(self):
        pause = 1 << S.e('action', 'PAUSE')
        confirm = 1 << S.e('action', 'CONFIRM')
        sem_pause = self.d.ILCT_FwGuestPulse(pause, 0, 0)
        self.assertNotEqual(sem_pause, 0, 'instrument: a guest PAUSE pulses by default')
        for native in (0, 1):
            self.assertEqual(self.d.ILCT_FwGuestPulseMay(pause, 0, native, 1), sem_pause)
            self.assertEqual(self.d.ILCT_FwGuestPulseMay(pause, 0, native, 0), 0, 'guest_pause 0: nothing')
        self.assertEqual(self.d.ILCT_FwGuestPulseMay(confirm, 0, 1, 0), self.d.ILCT_FwGuestPulse(confirm, 0, 1),
                         'other pulses inside a native screen are untouched')
        fw = (SRC / 'firewall/firewall.cpp').read_text(encoding='utf-8')
        self.assertIn('guest_pulse(in.act_pressed[k], in.act_held[k], guest_screen, options::guest_pause())', fw)
        fe = (SRC / 'frontend/frontend.cpp').read_text(encoding='utf-8')
        self.assertRegex(fe, r'party_allowed = s == 0 \|\| options::guest_pause\(\);')


# ---- hook.log ---------------------------------------------------------------------------------------------------------
@native_test
class LogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def setUp(self):
        self.d.ILCT_LogReset()

    def captured(self) -> list[tuple[int, str]]:
        buf = ctypes.create_string_buffer(16384)
        lines = u32()
        n = self.d.ILCT_LogCaptured(buf, len(buf), ctypes.byref(lines))
        out = []
        for part in buf.raw[:n].split(b'\0'):
            if part:
                out.append((part[0] - ord('0'), part[1:].decode('ascii')))
        self.assertEqual(len(out), lines.value)
        return out

    def stats(self) -> LogCounters:
        c = LogCounters()
        self.d.ILCT_LogStats(ctypes.byref(c))
        return c

    def test_lines_reach_the_logger_with_the_prefix_and_their_level(self):
        self.d.ILCT_LogCapture(1)
        self.assertEqual(self.d.ILCT_LogPost(0, b'hooks installed', 0), 1)
        self.assertEqual(self.d.ILCT_LogPost(2, b'start-up stopped', 0), 1)
        self.assertEqual(self.d.ILCT_LogPost(9, b'odd level', 0), 1)
        self.assertEqual(self.captured(), [], 'nothing is written before the drain')
        self.assertEqual(self.d.ILCT_LogDrain(), 3)
        self.assertEqual(self.captured(), [(0, 'Ivalice Legacy Co-op: hooks installed\n'),
                                           (2, 'Ivalice Legacy Co-op: start-up stopped\n'),
                                           (0, 'Ivalice Legacy Co-op: odd level\n')])
        self.assertEqual(self.d.ILCT_LogDrain(), 0, 'drained once')

    def test_numbers_line_breaks_and_length(self):
        self.d.ILCT_LogCapture(1)
        self.d.ILCT_LogPostNumbers(1, b'n', 1234567890123, -42, 0xBEEF)
        self.d.ILCT_LogPost(0, b'two\nlines\r', 0)
        self.d.ILCT_LogPost(0, b'x' * 500, 0)
        self.d.ILCT_LogDrain()
        lines = self.captured()
        self.assertEqual(lines[0], (1, 'Ivalice Legacy Co-op: n u=1234567890123 i=-42 x=0xbeef\n'))
        self.assertEqual(lines[1][1], 'Ivalice Legacy Co-op: two lines \n', 'one entry, one log line, ended by one newline')
        sizes = (u32 * 4)()
        self.d.ILCT_LogSizes(sizes, 4)
        self.assertEqual(len(lines[2][1]), sizes[2] - 1, 'cut at LINE_MAX')
        self.assertTrue(lines[2][1].endswith('x\n'), 'a cut line still ends with its newline')

    def test_verbose_lines_need_log_level_1(self):
        self.d.ILCT_LogCapture(1)
        self.assertEqual(self.d.ILCT_LogPost(0, b'settings 3 applied', 1), 0)
        self.assertEqual(self.stats().dropped_quiet, 1)
        self.d.ILCT_LogSetVerbose(1)
        self.assertEqual(self.d.ILCT_LogPost(0, b'settings 4 applied', 1), 1)
        self.d.ILCT_LogDrain()
        self.assertEqual([t for _, t in self.captured()], ['Ivalice Legacy Co-op: settings 4 applied\n'])

    def test_the_ring_drops_when_full_and_without_a_logger(self):
        sizes = (u32 * 4)()
        self.d.ILCT_LogSizes(sizes, 4)
        ring = sizes[3]
        for k in range(ring + 5):
            self.d.ILCT_LogPost(0, f'line {k}'.encode(), 0)
        c = self.stats()
        self.assertEqual((c.posted, c.dropped_full), (ring, 5))
        self.assertEqual(self.d.ILCT_LogDrain(), 0, 'no logger in a test process: nothing written')
        self.assertEqual(self.stats().dropped_no_sink, ring)
        self.d.ILCT_LogCapture(1)
        self.d.ILCT_LogPost(0, b'after', 0)
        self.assertEqual(self.d.ILCT_LogDrain(), 1, 'the ring is free again')

    def test_the_logger_is_found_by_name_never_loaded_or_imported(self):
        src = (SRC / 'runtime/log.cpp').read_text(encoding='utf-8')
        self.assertIn('GetModuleHandleW(LOGGER_MODULE)', src)
        self.assertIn('L"simpleLog.dll"', src)
        self.assertIn('"_log"', src)
        for word in ('LoadLibrary', 'CreateFile', 'WriteFile', 'malloc', 'new ', 'Sleep(', 'WaitFor'):
            self.assertNotIn(word, src)
        self.assertIn('TryAcquireSRWLockExclusive(&g_lock)', src)
        self.assertNotIn('AcquireSRWLockExclusive(&g_lock);\n    g_head', src.split('void test_reset')[0],
                         'producers and the drain only try the lock')

    def test_only_the_boot_threads_drain(self):
        drains = {}
        for p in SRC.rglob('*.cpp'):
            n = p.read_text(encoding='utf-8').count('log::drain(')
            if n:
                drains[p.relative_to(SRC).as_posix()] = n
        self.assertEqual(set(drains), {'boot/boot.cpp'})
        boot = (SRC / 'boot/boot.cpp').read_text(encoding='utf-8')
        self.assertIn('log_beat(watch);', boot, 'the co-op thread drains once per beat')

    def test_no_detour_or_game_thread_file_posts(self):
        """Hooks, handlers and the UI tick never touch the log (post is non-blocking, but it is not theirs to call)."""
        for p in list(SRC.rglob('hooks.cpp')) + [SRC / 'runtime/tick.cpp', SRC / 'runtime/guard.cpp', SRC / 'hud/replay.cpp',
                                                  SRC / 'wait/wait.cpp']:
            with self.subTest(p.name):
                self.assertIsNone(re.search(r'(?<![\w:])log::', p.read_text(encoding='utf-8')))

    def test_the_imports_stay_as_audited(self):
        import json
        allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))
        dlls = {name.lower() for name in allow['imports']}
        self.assertNotIn('simplelog.dll', dlls, 'the logger is looked up at run time, never imported')


# ---- record copies at mid-function sites (pinned compiler) ------------------------------------------------------------
@unittest.skipUnless(os.name == 'nt' and COMPILER.is_file(), 'requires the pinned MSYS2 g++')
class InlineCopyTest(unittest.TestCase):
    """movement/planner.h, party/remap.h, interaction/interaction.h: four seats grew the steering and remap records past
    128 bytes. The pinned g++ expands a fixed __builtin_memcpy of up to 256 bytes inline (SSE, XMM only); a UCRT memcpy
    call (which may use AVX) would be unsafe at a mid-function site. 512 bytes is the control: it becomes a call."""

    def test_256_inline_512_called(self):
        src = '''#include <stdint.h>
struct R168 { uint8_t b[168]; }; struct R176 { uint8_t b[176]; }; struct R256 { uint8_t b[256]; };
struct R512 { uint8_t b[512]; };
template <typename T> T rd(const volatile T* v) { T o; __builtin_memcpy(&o, const_cast<const T*>(v), sizeof(T)); return o; }
extern void use(void*);
void f168(const volatile R168* v) { R168 x = rd(v); use(&x); }
void f176(const volatile R176* v) { R176 x = rd(v); use(&x); }
void f256(const volatile R256* v) { R256 x = rd(v); use(&x); }
void f512(const volatile R512* v) { R512 x = rd(v); use(&x); }
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'probe.cpp'
            c.write_text(src)
            env = dict(os.environ)
            env['PATH'] = str(COMPILER.parent) + os.pathsep + env.get('PATH', '')
            subprocess.run([str(COMPILER), '-std=c++20', '-O2', '-fno-exceptions', '-fno-rtti', '-fno-stack-protector',
                            '-S', str(c), '-o', str(Path(tmp) / 'probe.s')], check=True, env=env)
            asm = (Path(tmp) / 'probe.s').read_text()
        bodies = dict(re.findall(r'^(_Z\d+f\d+\w*):\n(.*?)(?=^_Z|\Z)', asm, flags=re.S | re.M))
        by_size = {int(re.search(r'f(\d+)', k).group(1)): v for k, v in bodies.items()}
        for size in (168, 176, 256):
            self.assertNotIn('memcpy', by_size[size], f'{size} bytes must stay inline')
        self.assertIn('memcpy', by_size[512], 'control: a large copy becomes a call')

    def test_the_records_fit(self):
        for path, needle in (('movement/planner.h', 'sizeof(steer_record) <= 256'),
                             ('party/remap.h', 'sizeof(remap_record) <= 256'),
                             ('interaction/interaction.h', 'sizeof(ix_record) <= 256')):
            self.assertIn(needle, (SRC / path).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
