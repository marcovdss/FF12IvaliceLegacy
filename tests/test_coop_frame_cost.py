"""Host tests for the per-frame cost of the co-op module (0.1.54, PLAYTEST-1 F1/F2: 60 -> 20-30 fps in 0.1.53).

No game run, nothing deployed. What they prove:
* the probe's hot-path reads (runtime/probe.h read_game, load_game, store_game): a read inside a region already probed
  skips VirtualQuery; the same results as the fully checked copy (an unreadable address gives False, never a crash);
  a region freed after it was cached fails the read and is probed afresh; a guard page that appears inside a cached
  region fails the read and keeps its guard bit; a read spanning two regions is still correct; an invalidation (the
  session's new epoch) probes again; with the cache off every read queries (the 0.1.53 behaviour);
* VirtualQuery's own cost grows with the region it describes (why one query per read was a frame-rate problem);
* the HUD frame hook (h0 + h0_after) on a Full HUD (all 13 elements, four open co-op menus, Target Info, an 8-line
  battle log) on host memory with the game's draw functions stood in for: in steady state it makes no VirtualQuery
  and no texture lookup, draws exactly what the 0.1.53 path draws, and costs less per frame (printed, not asserted);
  a texture the registry re-registers is drawn with its new entry from the next frame on (0.1.55: the 0.1.54 atlas
  cache across frames could keep the old one); replays whose sprites and bars add nothing to the render ring are
  counted, and the boot turns that count into a hook.log warning and, with Developer options on, logs the frame
  hook's readings every 30 s.
"""
from __future__ import annotations

import ctypes
import time
import unittest
from ctypes import wintypes

from coop_native_support import native_test
from test_coop_patch_engine import GAME, NATIVE

u32, u64, i32 = ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

MEM_COMMIT, MEM_RESERVE, MEM_RELEASE = 0x1000, 0x2000, 0x8000
PAGE_READWRITE, PAGE_GUARD = 0x04, 0x100
PAGE = 0x1000

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
k32.VirtualFree.restype = wintypes.BOOL
k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
k32.VirtualProtect.restype = wintypes.BOOL
k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, P(wintypes.DWORD)]


class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                ('AllocationProtect', wintypes.DWORD), ('PartitionId', wintypes.WORD), ('RegionSize', ctypes.c_size_t),
                ('State', wintypes.DWORD), ('Protect', wintypes.DWORD), ('Type', wintypes.DWORD)]


k32.VirtualQuery.restype = ctypes.c_size_t
k32.VirtualQuery.argtypes = [ctypes.c_void_p, P(MBI), ctypes.c_size_t]


class ProbeStats(ctypes.Structure):
    _fields_ = [('queries', u64), ('hot_hits', u64), ('hot_misses', u64), ('hot_threads', u32), ('generation', u32),
                ('hot_failed', u64), ('sites_lost', u64)]


class ProbeSite(ctypes.Structure):
    _fields_ = [('rva', u32), ('reserved', u32), ('queries', u64), ('misses', u64)]


class BenchResult(ctypes.Structure):
    _fields_ = [(n, u64) for n in ('frames', 'qpc_ticks', 'qpc_frequency', 'queries', 'atlas_lookups', 'texts',
                                   'sprites', 'bars', 'begins', 'drawn', 'hook_ticks_total', 'hook_calls',
                                   'heap_bytes', 'sprites_on_entry', 'zero_output', 'declined_matrix',
                                   'child_space')] + [('matrix', ctypes.c_float * 16)] +                [(n, u64) for n in ('clip_lifts', 'clip_last', 'manager_context')]


BENCH_RING, BENCH_NO_ROOT_SPACE, BENCH_LAYER_OFF = 0x10000, 0x20000, 0x40000
BENCH_ROOT_SPACE = (0.5, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 1, 0, 40.0, 30.0, 0, 1)   # hud_tests.cpp


SIGNATURES = {
    'ILCT_ProbeReadGame': ([ctypes.c_void_p, u64, u64], i32),
    'ILCT_ProbeLoadGame': ([ctypes.c_void_p, u64, u64], i32),
    'ILCT_ProbeStoreGame': ([u64, ctypes.c_void_p, u64], i32),
    'ILCT_ProbeInvalidate': ([], None),
    'ILCT_ProbeSetHot': ([i32], None),
    'ILCT_ProbeStats': ([P(ProbeStats)], None),
    'ILCT_ProbeSites': ([P(ProbeSite), u32], u32),
    'ILCT_HudBench': ([u32, u32, u32, u32, i32, u64, u32, P(BenchResult)], i32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def alloc(size: int) -> int:
    a = k32.VirtualAlloc(None, size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)
    assert a, ctypes.get_last_error()
    ctypes.memset(a, 0, size)
    return a


def free(a: int):
    assert k32.VirtualFree(a, 0, MEM_RELEASE)


# ---- the probe's hot-path reads ------------------------------------------------------------------------------------
@native_test
class HotReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_coop_patch_engine as pe
        cls.d = declare(ctypes.WinDLL(str(pe.built()['test'])))

    def setUp(self):
        self.d.ILCT_ProbeSetHot(1)
        self.d.ILCT_ProbeInvalidate()
        self.regions = []

    def tearDown(self):
        self.d.ILCT_ProbeSetHot(1)
        for a in self.regions:
            free(a)

    def region(self, size: int) -> int:
        a = alloc(size)
        self.regions.append(a)
        return a

    def stats(self) -> ProbeStats:
        s = ProbeStats()
        self.d.ILCT_ProbeStats(ctypes.byref(s))
        return s

    def read(self, address: int, n: int):
        buf = ctypes.create_string_buffer(n)
        ok = self.d.ILCT_ProbeReadGame(buf, address, n)
        return ok, buf.raw

    def test_a_cached_region_skips_virtualquery(self):
        a = self.region(4 << 20)
        ctypes.memmove(a + 0x1234, b'hello hot path', 14)
        q0 = self.stats().queries
        self.assertEqual(self.read(a + 0x1234, 14), (1, b'hello hot path'))
        q1 = self.stats().queries
        self.assertEqual(q1 - q0, 1, 'the first read probes the region once')
        before = self.stats()
        for k in range(200):
            ok, data = self.read(a + 0x1000 + 8 * k, 8)
            self.assertEqual(ok, 1)
        after = self.stats()
        self.assertEqual(after.queries - before.queries, 0, 'reads inside the cached region make no VirtualQuery')
        self.assertEqual(after.hot_hits - before.hot_hits, 200)
        value = u64()
        self.assertEqual(self.d.ILCT_ProbeLoadGame(ctypes.byref(value), a + 0x2000, 8), 1)
        self.assertEqual(self.d.ILCT_ProbeStoreGame(a + 0x3000, b'\x2a\0\0\0', 4), 1)
        self.assertEqual(ctypes.c_uint32.from_address(a + 0x3000).value, 42)
        self.assertEqual(self.stats().queries - after.queries, 0, 'loads and stores share the cache')

    def test_invalidation_probes_again(self):
        a = self.region(1 << 20)
        self.read(a, 8)
        q = self.stats().queries
        self.read(a + 64, 8)
        self.assertEqual(self.stats().queries, q)
        self.d.ILCT_ProbeInvalidate()
        self.read(a + 64, 8)
        self.assertEqual(self.stats().queries, q + 1, 'a new epoch: the region is probed afresh')

    def test_unreadable_and_freed_addresses_fail_without_a_crash(self):
        self.assertEqual(self.read(0x8, 8)[0], 0, 'an unmapped address')
        a = alloc(1 << 20)
        self.assertEqual(self.read(a + 0x100, 8)[0], 1)
        free(a)   # freed after it was cached
        self.assertEqual(self.read(a + 0x100, 8)[0], 0, 'the cached region is gone: the read fails, caught')
        self.assertEqual(self.d.ILCT_ProbeLoadGame(ctypes.byref(u64()), a + 0x200, 8), 0)
        self.assertEqual(self.d.ILCT_ProbeStoreGame(a + 0x300, b'\1\2\3\4', 4), 0)

    def test_a_guard_page_after_the_probe_fails_the_read_and_keeps_its_guard(self):
        a = self.region(16 * PAGE)
        self.assertEqual(self.read(a, 8)[0], 1)            # the whole region is cached now
        old = wintypes.DWORD()
        self.assertTrue(k32.VirtualProtect(a + 8 * PAGE, PAGE, PAGE_READWRITE | PAGE_GUARD, ctypes.byref(old)))
        self.assertEqual(self.read(a + 8 * PAGE + 16, 8)[0], 0, 'the guard page is not read')
        m = MBI()
        k32.VirtualQuery(a + 8 * PAGE, ctypes.byref(m), ctypes.sizeof(m))
        self.assertEqual(m.Protect & PAGE_GUARD, PAGE_GUARD, 'the guard bit was set again for its owner')
        self.assertTrue(k32.VirtualProtect(a + 8 * PAGE, PAGE, PAGE_READWRITE, ctypes.byref(old)))

    def test_a_read_across_two_regions_is_still_correct(self):
        a = self.region(4 * PAGE)
        old = wintypes.DWORD()
        self.assertTrue(k32.VirtualProtect(a + 2 * PAGE, 2 * PAGE, 0x02, ctypes.byref(old)))  # PAGE_READONLY: a new region
        ctypes.memmove(a + 2 * PAGE - 4, b'ab', 2)
        self.assertEqual(self.read(a + 2 * PAGE - 4, 8), (1, b'ab' + b'\0' * 6))
        self.assertTrue(k32.VirtualProtect(a + 2 * PAGE, 2 * PAGE, PAGE_READWRITE, ctypes.byref(old)))

    def test_an_unreadable_region_is_refused_after_one_query_and_never_kept(self):
        a = k32.VirtualAlloc(None, 1 << 20, MEM_RESERVE, PAGE_READWRITE)   # reserved, never committed
        self.assertTrue(a)
        try:
            self.assertEqual(self.read(a + 0x100, 8)[0], 0)
            s0 = self.stats()
            for k in range(20):
                self.assertEqual(self.read(a + 0x100 + 64 * k, 8)[0], 0)
            s1 = self.stats()
            self.assertEqual(s1.queries - s0.queries, 20, 'one query each: the uncached path would make the same one')
            self.assertEqual(s1.hot_failed - s0.hot_failed, 20)
            k32.VirtualAlloc(a, PAGE, MEM_COMMIT, PAGE_READWRITE)   # the game commits memory there later
            self.assertEqual(self.read(a + 0x100, 8)[0], 1, 'readable at once: nothing unreadable was kept')
        finally:
            free(a)

    def test_the_busiest_sites_are_named(self):
        a = self.region(1 << 20)
        self.d.ILCT_ProbeSetHot(0)
        for k in range(30):
            self.read(a + 8 * k, 8)
        self.d.ILCT_ProbeSetHot(1)
        out = (ProbeSite * 5)()
        n = self.d.ILCT_ProbeSites(out, 5)
        self.assertGreater(n, 0)
        self.assertGreaterEqual(out[0].queries + out[0].misses, 30, 'the test export is among the busiest sites')
        self.assertGreater(out[0].rva, 1, 'an offset inside the module')

    def test_with_the_cache_off_every_read_queries(self):
        a = self.region(1 << 20)
        self.d.ILCT_ProbeSetHot(0)
        q = self.stats().queries
        for k in range(10):
            self.read(a + 8 * k, 8)
        self.assertEqual(self.stats().queries - q, 10, 'the 0.1.53 path: one VirtualQuery per read')

    def test_virtualquery_cost_grows_with_the_region(self):
        """Why: VirtualQuery sizes the region by walking it. Printed for the notes (not asserted: machine-dependent)."""
        m = MBI()
        rows = []
        for mib in (1, 16, 64):
            a = self.region(mib << 20)
            n = 200
            t0 = time.perf_counter()
            for _ in range(n):
                k32.VirtualQuery(a + 64, ctypes.byref(m), ctypes.sizeof(m))
            rows.append((mib, (time.perf_counter() - t0) / n * 1e6))
        print('\nVirtualQuery on a committed, touched region: ' +
              ', '.join(f'{mib} MiB {us:.1f} us' for mib, us in rows))
        self.assertGreater(rows[-1][1], rows[0][1], 'a larger region costs more to describe')


# ---- the frame hook on a Full HUD ---------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin: the patch layer installs on it)')
class FrameHookBenchTest(unittest.TestCase):
    WARM, FRAMES = 16, 400

    @classmethod
    def setUpClass(cls):
        import test_coop_battle_hud as tbh
        import test_coop_game_thread as gt
        import test_coop_hud as th
        import test_coop_hud_battle_log as tbl
        import test_coop_hud_driver as thd
        import test_coop_hud_menus as thm
        import test_coop_hud_party_rows as tpr
        import test_coop_hud_target_info as tti
        cls.gt, cls.th, cls.thd = gt, th, thd
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        tpr.declare(cls.d)
        tbl.declare(cls.d)
        thm.declare(cls.d)
        cls.d.ILCT_BhConfigure.argtypes, cls.d.ILCT_BhConfigure.restype = [ctypes.c_char_p], None
        cls.tbh, cls.tpr, cls.tti, cls.tbl, cls.thm = tbh, tpr, tti, tbl, thm

    def setUp(self):
        d, gt = self.d, self.gt
        self.hw.setup()
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_CtlReset()
        rec = self.tbh.defaults_record()
        rec['header']['generation'] = 1
        rec['hud_elements'] = sum(self.tbh.HUD.values())   # Full: all 13 elements, the party timers included
        d.ILCT_BhConfigure(bytes(self.tbh.S.encode('ilc_settings', rec)))
        self.publish_full_hud(gt.NOW)

    def tearDown(self):
        d = self.d
        d.ILCT_HudDrvReset()
        d.ILCT_RowsReset()
        d.ILCT_BhReset()
        self.thd.withdraw_menu_view(d)
        self.hw.twin.reset()

    def publish_full_hud(self, now):
        d, tpr, tti, tbl, thm = self.d, self.tpr, self.tti, self.tbl, self.thm
        members = [tpr.unit(k, seat=k + 1, hp=300 + 100 * k, hp_max=1000) for k in range(4)]
        snap = tpr.party_snapshot(members, now=now)
        foe = tti.unit()
        snap.targets[0] = foe
        snap.target_count = 1
        for k in range(4):
            snap.seats[k].handle, snap.seats[k].since_ms, snap.seats[k].on_page = foe.handle, now - 100, 1
        d.ILCT_HudSnapPublish(ctypes.byref(snap))
        colours = (tpr.GOLD, tpr.SAPPHIRE, tpr.EMERALD, tpr.AMETHYST)
        d.ILCT_RowsPublish(ctypes.byref(tpr.record([(k, 0, c, 0xFFFF) for k, c in enumerate(colours)], now=now)))
        d.ILCT_TiPublish(ctypes.byref(tti.a_view()))
        lines = [tbl.make_line(k + 1, touched=now - 10 * k, body=b'Cura', amounts=(1234,)) for k in range(8)]
        d.ILCT_BlPublish(ctypes.byref(tbl.make_view(lines, built=now)))
        views = {s: thm.seat_view(rows=8, member=s, values=True) for s in range(4)}
        d.ILCT_HmPublish(ctypes.byref(thm.menu_view(views, built=now)))

    def bench(self, legacy: bool, heap_mib: int, frames=None, flags=BENCH_RING) -> BenchResult:
        r = BenchResult()
        frames = frames or self.FRAMES
        self.assertEqual(self.d.ILCT_HudBench(self.gt.EPOCH, self.WARM, frames, heap_mib, 1 if legacy else 0,
                                              self.th.TWIN_BASE, flags, ctypes.byref(r)), 0)
        return r

    @staticmethod
    def us(r: BenchResult) -> float:
        return r.qpc_ticks / r.qpc_frequency * 1e6 / r.frames

    def test_a_full_hud_frame_makes_no_virtualquery_and_draws_the_same(self):
        rows = []
        for mib in (1, 64, 256):
            old = self.bench(True, mib, frames=60 if mib == 256 else None)
            new = self.bench(False, mib, frames=60 if mib == 256 else None)
            with self.subTest(heap_mib=mib):
                self.assertEqual(new.drawn, new.frames, 'every frame replayed to its end')
                self.assertEqual(old.drawn, old.frames)
                per = lambda r: (r.texts / r.frames, r.sprites / r.frames, r.bars / r.frames, r.begins / r.frames)
                self.assertEqual(per(new), per(old), 'the same draw calls per frame as the 0.1.53 path')
                self.assertGreater(new.texts / new.frames, 50, 'a Full HUD with four open menus')
                self.assertEqual(new.queries, 0, 'steady state: no VirtualQuery in the frame hook')
                self.assertEqual(new.atlas_lookups, old.atlas_lookups,
                                 'the atlases are looked up on every replay, as in 0.1.53 (0.1.55)')
                self.assertGreater(old.queries / old.frames, 10, '0.1.53 queried on every read')
                self.assertEqual((new.zero_output, old.zero_output), (0, 0), 'the draws added to the ring')
            rows.append((mib, old, new))
        print('\nFull HUD frame hook (h0 + h0_after; host, game draw calls stood in for), per frame:')
        for mib, old, new in rows:
            print(f'  world in a {mib:>3} MiB region: 0.1.53 {self.us(old):9.1f} us '
                  f'({old.queries / old.frames:.0f} VirtualQuery, {old.atlas_lookups / old.frames:.0f} lookups) -> '
                  f'now {self.us(new):7.1f} us ({new.queries / new.frames:.0f} VirtualQuery, '
                  f'{new.atlas_lookups / new.frames:.0f} lookups); {new.texts // new.frames} texts, '
                  f'{new.sprites // new.frames} sprites, {new.bars // new.frames} bars')

    def test_a_re_registered_texture_is_drawn_with_its_new_entry_at_once(self):
        """0.1.55 regression: the registry names another entry while the old one stays valid (picture and magic intact).
        0.1.54 kept the old entry for up to 2 s (its check only compared the old entry's own picture); every sprite of
        the frames after the change must use the new one."""
        frames, at = 40, 20
        r = self.bench(False, 1, frames=frames, flags=BENCH_RING | at)
        per_frame = r.sprites // frames
        self.assertGreater(per_frame, 0)
        self.assertEqual(r.drawn, frames)
        self.assertEqual(r.sprites_on_entry, per_frame * (frames - at + 1),
                         'every sprite from the change on is drawn with the entry the registry names now')

    def test_the_hud_root_space_is_used_when_the_root_context_has_no_matrix(self):
        """0.1.56 regression (in game, 0.1.55: "depth 2, matrix [0 0 / 0 0 / t 0 0]", nothing visible). The root's own
        context has an all-zero matrix; the replay must draw in the matrix the root hands its children
        (context+0xE0). 0.1.55 drew with the zero matrix."""
        r = self.bench(False, 1, frames=20)
        self.assertEqual(r.drawn, 20)
        self.assertEqual((r.child_space, r.declined_matrix), (20, 0))
        self.assertEqual(tuple(r.matrix), BENCH_ROOT_SPACE, 'the text draw got the root space')
        b = self.th.Budget()
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual((b.depth, b.source), (2, 1), 'depth 2, the root child space')

    def test_the_log_windows_clip_is_lifted_and_set_back(self):
        """0.1.57: while the log manager draws, the render scissor is the log window's rect (0x2d8530, set by the window
        draw), so 0.1.56 showed only what fell inside the log window. Each replay lifts it and sets the log manager's
        back."""
        r = self.bench(False, 1, frames=20)
        self.assertEqual(r.drawn, 20)
        self.assertEqual(r.clip_lifts, 20, 'one lift per replayed frame')
        self.assertEqual(r.clip_last, r.manager_context, "the clip set back from the log manager's own context")

    def test_with_the_layer_off_nothing_is_drawn_and_the_native_log_runs(self):
        """0.1.57 interim default: no co-op layer (vanilla command menu and log)."""
        r = self.bench(False, 1, frames=20, flags=BENCH_RING | BENCH_LAYER_OFF)
        self.assertEqual((r.drawn, r.texts, r.clip_lifts), (0, 0, 0))
        drv = self.thd.stats(self.d)
        self.assertGreaterEqual(drv.layer_off, 20)

    def test_no_usable_matrix_draws_nothing(self):
        r = self.bench(False, 1, frames=20, flags=BENCH_RING | BENCH_NO_ROOT_SPACE)
        self.assertEqual((r.drawn, r.declined_matrix, r.texts), (0, 20, 0), 'never drawn at scale 0')
        b = self.th.Budget()
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual(b.source, 2)

    def test_draws_that_add_nothing_to_the_ring_are_counted(self):
        quiet = self.bench(False, 1, frames=20, flags=0)
        self.assertEqual(quiet.zero_output, 20, 'sprites and bars drawn, the ring cursor never moved')
        normal = self.bench(False, 1, frames=20)
        self.assertEqual(normal.zero_output, 0)
        c = self.th.Counters()
        self.d.ILCT_HudStats(ctypes.byref(c))
        self.assertGreater(c.ring_ours_last, 0)

    def test_the_hook_times_itself(self):
        r = self.bench(False, 1)
        self.assertGreaterEqual(r.hook_calls, r.frames, 'every hook call is summed')
        self.assertGreater(r.hook_ticks_total, 0)
        c = self.th.Counters()
        self.d.ILCT_HudStats(ctypes.byref(c))
        self.assertGreater(c.hook_ticks_max, 0)
        self.assertGreaterEqual(c.hook_ticks_max, c.hook_ticks_last)
        self.assertGreater(c.text_last, 50)
        self.assertGreater(c.chars_last, c.text_last)


class InterimLayerStaticTest(unittest.TestCase):
    """0.1.57 interim: the shipped module draws no co-op layer unless Developer options and the HUD test draw are on."""

    def test_the_shipped_default_is_off_and_the_switch_is_the_experiment(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / 'native/coop/src/hud'
        drv = (src / 'driver.cpp').read_text(encoding='utf-8')
        self.assertIn('volatile uint32_t g_layer = 0;', drv, 'off in the shipped module')
        self.assertNotIn('ILC_LOOPBACK_CODEC', drv, 'no host-test switch in shipped code')
        self.assertIn('if (!layer_enabled()) {', drv)
        bh = (src / 'battle_hud.cpp').read_text(encoding='utf-8')
        self.assertIn('driver::set_layer_enabled(false);', bh)   # the overlay replaced the old layer
        native = (src / 'native.cpp').read_text(encoding='utf-8')
        self.assertIn('driver::layer_enabled()', native, 'the reduced party list and circles off follow the layer')


class BootLogStaticTest(unittest.TestCase):
    def test_the_boot_logs_the_hud_readings(self):
        from pathlib import Path
        boot = (Path(__file__).resolve().parents[1] / 'native/coop/src/boot/boot.cpp').read_text(encoding='utf-8')
        self.assertIn('HUD_LOG_MS = 30000', boot)
        self.assertIn('HUD warning: ', boot)
        self.assertIn('if (!g_developer ||', boot, 'the periodic readings only with Developer options on')
        self.assertEqual(boot.count('g_developer = applied'), 2, 'kept in step with the applied settings')
        self.assertIn('hud_lines(w, patch::now_ms());', boot)
        self.assertIn('frames not drawn: no usable draw matrix', boot)
        self.assertIn('probe sites (queries/misses):', boot)


if __name__ == '__main__':
    unittest.main()
