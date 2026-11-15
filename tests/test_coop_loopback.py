"""Host tests for the seam's loopback codec, the replays it is checked with, and the soak simulator. No game run,
nothing deployed.

* static: the codec is compiled into the host-test DLL only (ILC_LOOPBACK_CODEC and testsupport/loopback.cpp are
  never part of the shipped build), and the hooks sit at every handover of the three message families: the input
  service's tick (frames, action frames, control view), the command queue (post_command, drain_events) and the
  five view publishes the HUD passes draw from;
* input replay: one hour of a scripted, deterministic input stream (three pads, keys, focus changes, seat commands
  through the bridge, settings regenerations) through the input_replay device world and the manual input service;
  every 20 ms the edge deltas, bridge events, snapshot frames, action frames, control view and pad mirror are
  digested (value bytes only). Codec on and off give the same digest; a deliberately lossy codec does not;
* HUD replay: one hour of view variants (snapshot, menus, party rows, Target Info, battle log) published every
  20 ms and composed by the frame driver at 60 frames per second; the digest covers each frame's result, commands
  and text. Codec on and off give the same digest; a lossy codec does not;
* soak: eight simulated hours of all of it together, plus the co-op switches (kill switch by command and by key),
  captures, battle-log traffic through its event ring, game-thread jobs, 3 s liveness stalls and settings
  regenerations: every queue and ring stays bounded, nothing that must stay flat moves, and the process does not
  grow. ILC_LOOPBACK_REPORT=<file> writes the measured values as JSON.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import unittest
from ctypes import wintypes
from pathlib import Path

from coop_native_support import COOP, native_test
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME

u32, u64, i32 = ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
HOUR_MS = 3_600_000
FAMILIES = ['frame', 'action', 'control_view', 'command', 'event', 'snapshot', 'menu_view', 'rows', 'ti_view',
            'log_view']
INPUT_FAMILIES, VIEW_FAMILIES = FAMILIES[:5], FAMILIES[5:]
INPUT_OUT = ['digest', 'ticks', 'beats', 'events', 'edge_presses', 'commands', 'command_busy', 'command_rejected',
             'settings_applied', 'focus_losses', 'disconnects', 'outbox_dropped']
HUD_OUT = ['digest', 'frames', 'composed', 'commands', 'text_bytes', 'beats', 'not_composed', 'peak_commands']
SRC = COOP / 'src'


def report(key, value):
    path = os.environ.get('ILC_LOOPBACK_REPORT')
    if not path:
        return
    p = Path(path)
    data = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}
    data[key] = value
    p.write_text(json.dumps(data, indent=2), encoding='utf-8')


def declare(d):
    d.ILCT_LoopbackSet.argtypes, d.ILCT_LoopbackSet.restype = [i32], None
    d.ILCT_LoopbackStats.argtypes, d.ILCT_LoopbackStats.restype = [P(u64), u32], u32
    d.ILCT_LoopbackResetStats.argtypes, d.ILCT_LoopbackResetStats.restype = [], None
    d.ILCT_LoopbackInputDrill.argtypes, d.ILCT_LoopbackInputDrill.restype = [u64, u32, P(u64), u32], u32
    d.ILCT_LoopbackHudDrill.argtypes = [ctypes.c_void_p, u64, u64, u32, P(u64), u32]
    d.ILCT_LoopbackHudDrill.restype = u32
    d.ILCT_SoakDrill.argtypes, d.ILCT_SoakDrill.restype = [ctypes.c_void_p, u64, u64, u32, P(u64), u32], u32
    d.ILCT_SoakNameCount.argtypes, d.ILCT_SoakNameCount.restype = [], u32
    d.ILCT_SoakName.argtypes, d.ILCT_SoakName.restype = [u32], ctypes.c_char_p
    return d


def codec_stats(d) -> dict:
    out = (u64 * (4 * len(FAMILIES)))()
    d.ILCT_LoopbackStats(out, len(out))
    return {f: dict(zip(('records', 'value_bytes', 'padding_bytes', 'wire_errors'), out[4 * k:4 * k + 4]))
            for k, f in enumerate(FAMILIES)}


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_the_codec_is_never_part_of_the_shipped_build(self):
        import build as coop_build
        self.assertNotIn('testsupport/loopback.cpp', coop_build.SOURCES)
        self.assertNotIn('testsupport/loopback_drill.cpp', coop_build.SOURCES)
        self.assertIn('testsupport/loopback.cpp', coop_build.TEST_SOURCES)
        self.assertNotIn('-DILC_LOOPBACK_CODEC=1', coop_build.CXXFLAGS + coop_build.LDFLAGS)
        self.assertEqual(coop_build.TEST_DEFINES, ['-DILC_LOOPBACK_CODEC=1'])
        text = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertEqual(text.count('TEST_DEFINES'), 2, 'defined once, used only by the host-test build')
        header = (SRC / 'runtime' / 'loopback.h').read_text(encoding='utf-8')
        release = header.split('#else  // the shipped DLL: no codec', 1)[1]
        for name in ('ILC_LOOPBACK(', 'ILC_LOOPBACK_EACH(', 'ILC_LOOPBACK_COPY(', 'ILC_LOOPBACK_COPY_STATIC(',
                     'ILC_LOOPBACK_ARRAY('):
            self.assertIn('#define ' + name, release, name)
        self.assertNotIn('::ilc::loopback', release, 'the shipped expansion calls nothing')
        for path in SRC.rglob('*.cpp'):
            self.assertNotIn('ILC_LOOPBACK_CODEC', path.read_text(encoding='utf-8'), path)

    def test_hooks_at_every_handover(self):
        sites = {
            'input/input_service.cpp': ['ILC_LOOPBACK_EACH(FAM_ACTION, g_work.actions, SEATS)',
                                        'ILC_LOOPBACK_ARRAY(ilc_seat_input_frame, FAM_FRAME, g_core.frames, SEATS, seam_frames)',
                                        'edges::record(g_core.seq, seam_frames, g_work.actions)',
                                        'pads::record(seam_frames, g_work.actions)',
                                        'memcpy(g_work.frames, seam_frames, sizeof(g_work.frames))',
                                        'ILC_LOOPBACK(FAM_CONTROL_VIEW, g_work.control);\n    publish(g_work);',
                                        'ILC_LOOPBACK_COPY(command_msg, FAM_COMMAND, m_in, m)',
                                        'ILC_LOOPBACK_EACH(FAM_EVENT, out, n)'],
            'hud/snapshot.cpp': ['ILC_LOOPBACK_COPY_STATIC(hud_snapshot, FAM_SNAPSHOT, s_in, s)'],
            'hud/menus_draw.cpp': ['ILC_LOOPBACK_COPY_STATIC(menu_view, FAM_MENU_VIEW, v_in, v)'],
            'hud/target_info.cpp': ['ILC_LOOPBACK_COPY_STATIC(view, FAM_TI_VIEW, v_in, v)'],
            'hud/battle_log.cpp': ['ILC_LOOPBACK_COPY_STATIC(view, FAM_LOG_VIEW, v_in, v)'],
            'hud/party_rows.cpp': ['ILC_LOOPBACK_COPY_STATIC(rows_record, FAM_ROWS, next, seam)',
                                   'ILC_LOOPBACK_COPY_STATIC(rows_record, FAM_ROWS, r, seam)'],
        }
        for rel, needles in sites.items():
            text = (SRC / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
            for n in needles:
                with self.subTest(file=rel, hook=n[:40]):
                    self.assertIn(n, text)
        # Nothing after the hook reads the raw frames: the three publishers take the decoded copy only.
        tick = (SRC / 'input/input_service.cpp').read_text(encoding='utf-8')
        body = tick[tick.index('ILC_LOOPBACK_ARRAY('):tick.index('publish(g_work);\n}')]
        self.assertNotIn('g_core.frames', body.split('\n', 1)[1])


# ---- input replay ---------------------------------------------------------------------------------------------------------
@native_test
class InputReplayTest(unittest.TestCase):
    def drill(self, codec: int, ms: int, seed: int = 7):
        d = declare(fresh_test_dll())  # a fresh module per run: the same starting state
        d.ILCT_LoopbackResetStats()
        d.ILCT_LoopbackSet(codec)
        out = (u64 * len(INPUT_OUT))()
        n = d.ILCT_LoopbackInputDrill(ms, seed, out, len(out))
        self.assertEqual(n, len(INPUT_OUT))
        stats = codec_stats(d)
        d.ILCT_LoopbackSet(0)
        return dict(zip(INPUT_OUT, out)), stats

    def test_one_hour_with_the_codec_on_behaves_as_with_it_off(self):
        off, s_off = self.drill(0, HOUR_MS)
        on, s_on = self.drill(1, HOUR_MS)
        report('input_replay', {'off': off, 'on': on, 'codec': s_on})
        self.assertEqual(off, on, 'identical digest and counts over one hour')
        # the stream exercised what it should
        self.assertEqual(off['ticks'], HOUR_MS // 8)
        self.assertEqual(off['beats'], HOUR_MS // 20)
        self.assertGreater(off['events'], 1000)
        self.assertGreater(off['edge_presses'], 10000)
        self.assertGreater(off['settings_applied'], 3)
        self.assertGreater(off['focus_losses'], 10)
        self.assertEqual((off['command_busy'], off['outbox_dropped']), (0, 0))
        for f in INPUT_FAMILIES:
            with self.subTest(family=f):
                self.assertEqual(s_off[f]['records'], 0, 'off: the codec never ran')
                self.assertGreater(s_on[f]['records'], 0, 'on: every record of the family went through it')
                self.assertEqual(s_on[f]['wire_errors'], 0)
        self.assertEqual(s_on['frame']['records'], 4 * HOUR_MS // 8)
        self.assertEqual(s_on['action']['records'], 4 * HOUR_MS // 8)
        self.assertEqual(s_on['control_view']['records'], HOUR_MS // 8)

    def test_a_lossy_codec_changes_the_digest(self):
        off, _ = self.drill(0, 120_000)
        lossy, stats = self.drill(2, 120_000)
        self.assertGreater(stats['frame']['records'], 997)
        self.assertNotEqual(off['digest'], lossy['digest'], 'the replay sees a codec that loses a byte')


# ---- HUD replay -----------------------------------------------------------------------------------------------------------
class HudPools(ctypes.Structure):
    _fields_ = [('snaps', ctypes.c_void_p), ('n_snaps', u32), ('epoch', u32),
                ('menus', ctypes.c_void_p), ('n_menus', u32), ('frame_hz', u32),
                ('rows', ctypes.c_void_p), ('n_rows', u32), ('reserved', u32),
                ('tis', ctypes.c_void_p), ('n_tis', u32), ('reserved2', u32),
                ('logs', ctypes.c_void_p), ('n_logs', u32), ('reserved3', u32)]


def hud_pools(frame_hz=60):
    """Variants of every view, from the HUD tests' own fixtures: 1-4 party rows with timed statuses, the boss gauge,
    seat targets; 0-4 open menu windows of each kind; stock and reduced party lists; Target Info with and without
    drops and statuses; the battle log with 0-20 lines, folded and expanded."""
    import test_coop_hud_battle_log as tbl
    import test_coop_hud_driver as thd
    import test_coop_hud_menus as thm
    import test_coop_hud_party_rows as tpr
    import test_coop_hud_target_info as tti
    snaps = []
    for rows in range(1, 5):
        for boss in (False, True):
            for targets in (False, True):
                units = [tpr.unit(k, seat=k + 1, hp=300 + 200 * k) for k in range(rows)]
                for u in units:
                    u.status_timed = (1 << tpr.PROTECT) | (1 << tpr.POISON)
                    u.duration[tpr.PROTECT], u.duration[tpr.POISON] = 3000, 1500
                s = tpr.party_snapshot(units)
                if boss:
                    s.flags |= 0x20
                if targets:
                    for k in range(rows):
                        s.seats[k].handle, s.seats[k].on_page = s.members[(k + 1) % rows].handle, 1
                snaps.append(s)
    menus = []
    kinds = [dict(kind=thm.WK_LIST, rows=8, values=True, toggle=True), dict(kind=thm.WK_TARGET, rows=6),
             dict(kind=thm.WK_LIST, rows=3)]
    for n in range(0, 5):
        for kind in kinds:
            for reduced in (True, False):
                menus.append(thm.menu_view({s: thm.seat_view(member=s, **kind) for s in range(n)}, reduced=reduced))
    rows_pool = []
    for rows in range(1, 5):
        for geometry in (tpr.STOCK, tpr.REDUCED):
            extras = [(k, tpr.EXTRA_READ | tpr.EXTRA_GAMBITS_ON | (tpr.EXTRA_ACTING if k % 2 else tpr.EXTRA_CHOOSING),
                       k % 7, 0x10 + k) for k in range(rows)]
            rows_pool.append(tpr.record(extras, geometry=geometry))
    tis = []
    for drops in (0, 3, 8):
        for statuses in (0, 4, 10):
            v = tti.a_view()
            for k in range(drops):
                dl = v.drops[k]
                dl.kind, dl.state, dl.text = tti.DROP, tti.KNOWN, b'Drop ' + bytes([0x41 + k])
            v.drop_count = drops
            for k in range(statuses):
                v.status_id[k], v.status_flags[k], v.status_secs[k] = k + 1, tti.ST_ACTIVE | tti.ST_TIMED, 30 + k
            v.status_count = statuses
            tis.append(v)
    logs = []
    for count in (0, 1, 5, 12, 20):
        for flags in (0, tbl.VIEW_EXPANDED):
            lines = [tbl.make_line(k + 1, touched=thd.NOW, actor=b'Balthier', body=b'Attack', amounts=(120 + k, 0),
                                   icons=(1, 0)) for k in range(count)]
            logs.append(tbl.make_view(lines, flags=flags, built=thd.NOW))
    arrays = []

    def array(items):
        a = (type(items[0]) * len(items))(*items)
        arrays.append(a)
        return ctypes.addressof(a), len(items)

    p = HudPools()
    p.snaps, p.n_snaps = array(snaps)
    p.menus, p.n_menus = array(menus)
    p.rows, p.n_rows = array(rows_pool)
    p.tis, p.n_tis = array(tis)
    p.logs, p.n_logs = array(logs)
    p.epoch, p.frame_hz = thd.EPOCH, frame_hz
    return p, arrays


class HudBase(unittest.TestCase):
    """The HUD world (the patch layer installed, armed and LIVE on the image twin) and the elements' reset."""

    @classmethod
    def setUpClass(cls):
        import test_coop_hud_driver as thd
        import test_coop_hud_menus as thm
        import test_coop_hud_battle_log as tbl
        import test_coop_hud_party_rows as tpr
        import test_coop_hud_target_info as tti
        cls.hw, cls.d = thm.Dll.get()
        declare(cls.d)
        for name, (args, res) in tti.SIGNATURES.items():
            fn = getattr(cls.d, name)
            fn.argtypes, fn.restype = args, res
        tbl.declare(cls.d)
        tpr.declare(cls.d)
        cls.thd = thd

    def fresh(self):
        d = self.d
        self.hw.setup()
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_HudDrvReset()
        d.ILCT_HmReset()
        d.ILCT_TiReset()
        d.ILCT_BlReset()
        d.ILCT_RowsReset()
        d.ILCT_BlConfigure(1, 1, 0, 0, 1)

    def tearDown(self):
        d = self.d
        d.ILCT_LoopbackSet(0)
        d.ILCT_HudDrvReset()
        d.ILCT_HmReset()
        d.ILCT_TiReset()
        d.ILCT_BlReset()
        d.ILCT_RowsReset()
        self.hw.twin.reset()



@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HudReplayTest(HudBase):
    def drill(self, codec: int, ms: int, seed: int = 11):
        pools, keep = hud_pools()
        self.fresh()
        self.d.ILCT_LoopbackResetStats()
        self.d.ILCT_LoopbackSet(codec)
        out = (u64 * len(HUD_OUT))()
        n = self.d.ILCT_LoopbackHudDrill(ctypes.addressof(pools), self.thd.NOW, ms, seed, out, len(out))
        self.d.ILCT_LoopbackSet(0)
        self.assertEqual(n, len(HUD_OUT))
        del keep
        return dict(zip(HUD_OUT, out)), codec_stats(self.d)

    def test_one_hour_with_the_codec_on_draws_as_with_it_off(self):
        off, s_off = self.drill(0, HOUR_MS)
        on, s_on = self.drill(1, HOUR_MS)
        report('hud_replay', {'off': off, 'on': on, 'codec': s_on})
        self.assertEqual(off, on, 'identical frames over one hour')
        self.assertEqual(off['frames'], HOUR_MS * 60 // 1000 + 1)
        self.assertEqual(off['composed'], off['frames'], 'every frame composed')
        self.assertGreater(off['commands'], off['frames'] * 10, 'frames with real content')
        for f in VIEW_FAMILIES:
            with self.subTest(family=f):
                self.assertEqual(s_off[f]['records'], 0)
                self.assertEqual(s_on[f]['records'], off['beats'])
                self.assertEqual(s_on[f]['wire_errors'], 0)
        self.assertGreater(sum(s_on[f]['padding_bytes'] for f in VIEW_FAMILIES), 0,
                           'some views have padding, and it was poisoned on every decode')

    def test_a_lossy_codec_changes_the_frames(self):
        off, _ = self.drill(0, 120_000)
        lossy, _ = self.drill(2, 120_000)
        self.assertNotEqual(off['digest'], lossy['digest'])


# ---- soak ---------------------------------------------------------------------------------------------------------------
class ProcessMemory(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t), ('PrivateUsage', ctypes.c_size_t)]


def private_bytes() -> int:
    k32 = ctypes.WinDLL('kernel32')
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    m = ProcessMemory()
    m.cb = ctypes.sizeof(m)
    assert k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
    return m.PrivateUsage


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class SoakTest(HudBase):
    SOAK_HOURS = float(os.environ.get('ILC_SOAK_HOURS', '8'))

    def soak(self, ms: int, seed: int = 5) -> dict:
        pools, keep = hud_pools()
        self.fresh()
        self.d.ILCT_LoopbackSet(0)
        names = [self.d.ILCT_SoakName(k).decode() for k in range(self.d.ILCT_SoakNameCount())]
        out = (u64 * len(names))()
        n = self.d.ILCT_SoakDrill(ctypes.addressof(pools), self.thd.NOW, ms, seed, out, len(out))
        self.assertEqual(n, len(names))
        del keep
        return dict(zip(names, out))

    def test_eight_simulated_hours_stay_bounded(self):
        ms = int(self.SOAK_HOURS * HOUR_MS)
        self.soak(60_000, seed=3)  # warm-up: every lazy table and cache is built before the measurement
        before = private_bytes()
        r = self.soak(ms)
        after = private_bytes()
        r['private_bytes_before'], r['private_bytes_after'] = before, after
        report('soak', r)
        # the simulation exercised everything
        self.assertEqual(r['sim_ms'], ms)
        for key in ('stall_count', 'kills', 'revives', 'kill_key_presses', 'captures_started', 'gtq_ran',
                    'log_ring_written', 'log_published', 'frames_composed', 'settings_applied', 'toggles'):
            with self.subTest(exercised=key):
                self.assertGreater(r[key], 0)
        # bounded queues and rings, flat counters
        self.assertEqual(r['command_busy'], 0, 'the input service inbox (16) never filled')
        self.assertEqual(r['input_outbox_dropped'], 0, 'the input service outbox (32) never dropped')
        self.assertEqual(r['bridge_events_dropped_delta'], 0, 'the bridge event queue (64) never dropped')
        self.assertLessEqual(r['peak_bridge_events_pending'], 64)
        self.assertEqual(r['gtq_push_full'], 0)
        self.assertLessEqual(r['gtq_peak_in_flight'], 64)
        self.assertLessEqual(r['gtq_pushed'] - r['gtq_drained'] - r['gtq_reaped'], 64, 'nothing left behind')
        self.assertLessEqual(r['log_peak_unread'], 256, 'the battle log ring (256)')
        self.assertEqual(r['log_ring_lost'], 0)
        self.assertLessEqual(r['hud_peak_commands'], 192)
        self.assertEqual(r['mask_busy'], 0)
        self.assertLess(after - before, 4 << 20, 'the process did not grow over the soak')


if __name__ == '__main__':
    unittest.main()
