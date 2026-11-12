"""Image-twin drills for the co-op patch layer: the real MinHook transaction against FFXII_TZA.exe mapped at its
preferred base (0x120000) in this test process, with real threads parked inside the displaced bytes. No game run,
nothing deployed; the exe is only read (point IVALICE_LEGACY_GAME at a copy; its SHA-256 is pinned by sites.inc).

* Parked threads are created suspended and never run while parked; each one's RIP is set to an instruction
  boundary inside a hook window (every boundary of every PATCH row, window starts included). They are what the
  quiescence probe and MinHook's freeze see.
* The test DLL's own import slots for the heap, VirtualAlloc/Free, the CRT allocator and Suspend/ResumeThread are
  pointed at counting wrappers, so every drill counts allocations, leaks and allocations made while any thread is
  suspended (must be zero: a suspended thread may hold a heap lock).

Drills: a wrong expected byte in any window gives zero writes (image, protections, near-image allocations, MinHook
calls, suspensions); a thread inside a window makes the install retry and then commit once it has left, or refuse
with zero writes if it never leaves; threads that enter a window after the clean probe are moved into the
trampolines by MinHook (and run equivalently there, by unicorn) and moved back by every rollback path (a thread
parked at an instruction start of one of the 18 call rows stays where it is: only the rel32 changes, and from there
the hooked code runs like the original); the
rolled-back image is byte-identical; a row whose restore fails keeps its trampoline as the stub's continuation; a
site changed between create and enable aborts with nothing enabled; a full cycle leaks nothing.
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest

import test_coop_patch_engine as pe
from test_coop_patch_engine import (GAME, NATIVE, OK, STATE_IDLE, STATE_INSTALLED, STATE_RETIRED, TWIN_BASE, TWIN_SIZE,
                                    VERIFY_FAILED, Report, Twin, checked, near_private, regions)

try:
    import capstone
except ImportError:
    capstone = None

QUIESCE_BUSY, SITE_CHANGED, ROLLBACK_FAILED, SITE_MISMATCH = 29, 31, 27, 7
TEST_FAIL_VERIFY, TEST_FAIL_DISABLE, TEST_CHANGE_BEFORE_APPLY = 3, 4, 5
PHASE_BUSY, PHASE_CLEAN = 0, 1
ANY_ATTEMPT = 0xFFFFFFFF
QUIESCE_ATTEMPTS = 8


class Counters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int64) for n in (
        'heap_alloc', 'heap_free', 'heap_realloc', 'heap_create', 'heap_destroy', 'heap_live_foreign',
        'virtual_alloc', 'virtual_free', 'crt_alloc', 'crt_free', 'allocs_while_suspended', 'suspend_now',
        'suspend_max', 'suspends', 'resumes', 'thread_opens', 'handle_closes', 'installed')]


def declare(d):
    d.ILCT_ParkCreate.argtypes = [ctypes.c_uint32]
    d.ILCT_ParkSetRip.argtypes = [ctypes.c_uint32, ctypes.c_uint64]
    d.ILCT_ParkGetRip.argtypes = [ctypes.c_uint32]
    d.ILCT_ParkGetRip.restype = ctypes.c_uint64
    d.ILCT_CountersRead.argtypes = [ctypes.POINTER(Counters)]
    d.ILCT_QuiescePlanAdd.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64]
    d.ILCT_QuiescePokeBeforeApply.argtypes = [ctypes.c_uint64, ctypes.c_uint32]
    d.ILCT_QuiesceHookCalls.argtypes = [ctypes.c_uint32]
    d.ILCT_QuiesceHookCalls.restype = ctypes.c_uint32
    d.ILCT_PatchSetMode.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchOriginal.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchOriginal.restype = ctypes.c_uint64


def boundaries(twin) -> list[tuple[int, int]]:
    """(row, offset) for every instruction start inside every PATCH row's hook window."""
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    out = []
    for row in twin.patch:
        r = twin.rows[row]
        site = TWIN_BASE + r['rva']
        for insn in cs.disasm(checked(r)[:r['hook_len']], site):
            out.append((row, insn.address - site))
    return out


_k32 = ctypes.WinDLL('kernel32')
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_k32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]


def handle_count() -> int:
    n = ctypes.c_uint32()
    _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


@unittest.skipUnless(NATIVE and GAME and capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME (a copy of the exe) and capstone')
class ImageTwinDrillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()
        cls.d = cls.twin.dll
        declare(cls.d)
        cls.twin.reset()  # MinHook uninitialised: its private heap is created after the counters are in place
        installed = cls.d.ILCT_CountersInstall()
        if installed < 11:
            raise AssertionError(f'only {installed} import slots redirected to the counters')
        cls.points = boundaries(cls.twin)
        cls.sites = [TWIN_BASE + cls.twin.rows[row]['rva'] + off for row, off in cls.points]

    def setUp(self):
        self.d.ILCT_PatchInject(0, 0)
        self.d.ILCT_QuiescePlanClear()
        self.twin.reset()
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.d.ILCT_CountersReset()

    def tearDown(self):
        self.d.ILCT_QuiescePlanClear()
        self.d.ILCT_PatchInject(0, 0)
        released = self.d.ILCT_ParkReleaseAll()
        self.twin.reset()
        self.assertGreaterEqual(released, 0)

    # helpers
    def counters(self) -> Counters:
        c = Counters()
        self.d.ILCT_CountersRead(ctypes.byref(c))
        return c

    def park(self, count: int) -> list[int]:
        """Creates `count` parked threads; returns each one's start RIP (outside every window)."""
        self.assertEqual(self.d.ILCT_ParkCreate(count), count)
        return [self.d.ILCT_ParkGetRip(k) for k in range(count)]

    def park_in_windows(self) -> list[int]:
        starts = self.park(len(self.sites))
        for k, address in enumerate(self.sites):
            self.assertEqual(self.d.ILCT_ParkSetRip(k, address), 1)
            self.assertEqual(self.d.ILCT_ParkGetRip(k), address)
        return starts

    def install(self) -> Report:
        return self.twin.call('ILCT_PatchInstall')

    def assert_no_allocation_while_suspended(self):
        c = self.counters()
        self.assertEqual(c.allocs_while_suspended, 0, 'an allocation happened while a thread was suspended')
        self.assertEqual(c.suspend_now, 0, 'a suspension was left outstanding')
        self.assertEqual(c.suspends, c.resumes)

    # ---- static: no return address can point inside a window ----
    def test_no_window_holds_a_call_before_its_last_instruction(self):
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        for row in self.twin.patch:
            r = self.twin.rows[row]
            site = TWIN_BASE + r['rva']
            for insn in cs.disasm(checked(r)[:r['hook_len']], site):
                if insn.mnemonic == 'call':
                    self.assertEqual(insn.address + insn.size, site + r['hook_len'], r['id'])
        self.assertEqual(len(self.points), len(self.twin.patch) + 24, 'window boundaries changed: review the drills')

    # ---- wrong expected byte -> zero writes, threads parked ----
    def test_wrong_expected_byte_in_any_window_gives_zero_writes_with_threads_parked(self):
        self.park_in_windows()
        private = near_private()
        handles = handle_count()
        cases = 0
        for row in self.twin.patch:
            r = self.twin.rows[row]
            for offset in sorted({0, r['hook_len'] - 1}):
                with self.subTest(site=r['id'], offset=offset):
                    self.twin.reset()
                    self.d.ILCT_CountersReset()
                    address = TWIN_BASE + r['rva'] + offset
                    original = ctypes.string_at(address, 1)
                    self.twin.poke(address, bytes([original[0] ^ 0xA5]))
                    reference = self.twin.image()
                    report = self.install()
                    self.assertEqual((report.result, report.site, report.mh_calls, report.hooked, report.state,
                                      report.threads_probed), (SITE_MISMATCH, row, 0, 0, STATE_IDLE, 0))
                    self.assertEqual(self.twin.image(), reference, 'image bytes changed')
                    self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
                    c = self.counters()
                    self.assertEqual((c.heap_alloc, c.heap_create, c.virtual_alloc, c.crt_alloc, c.suspends),
                                     (0, 0, 0, 0, 0), 'a refused preflight allocated or suspended')
                    cases += 1
        self.assertEqual(near_private(), private)
        self.assertEqual(handle_count(), handles)
        for k, address in enumerate(self.sites):
            self.assertEqual(self.d.ILCT_ParkGetRip(k), address, 'a parked thread moved')
        self.assertEqual(cases, sum(2 if self.twin.rows[r]['hook_len'] > 1 else 1 for r in self.twin.patch))

    # ---- RIP in a window -> retry, then commit ----
    def test_rip_in_range_retries_then_commits(self):
        starts = self.park_in_windows()
        for k, start in enumerate(starts):  # after the first busy probe every parked thread leaves its window
            self.d.ILCT_QuiescePlanAdd(PHASE_BUSY, 0, k, start)
        report = self.install()
        self.assertEqual((report.result, report.hooked, report.state, report.quiesce_retries, report.threads_busy),
                         (OK, 61, STATE_INSTALLED, 1, 0))
        self.assertGreaterEqual(report.threads_probed, len(self.sites))
        self.assertEqual((self.d.ILCT_QuiesceHookCalls(PHASE_BUSY), self.d.ILCT_QuiesceHookCalls(PHASE_CLEAN)),
                         (1, 1))
        self.assert_no_allocation_while_suspended()
        for k, start in enumerate(starts):
            self.assertEqual(self.d.ILCT_ParkGetRip(k), start)
        self.assertEqual(self.d.ILCT_PatchReleaseAll(), OK)
        self.assertEqual(self.twin.image(), self.twin.baseline, 'released image differs')
        self.assert_no_allocation_while_suspended()

    def test_rip_in_range_then_failed_verify_rolls_back_byte_identical(self):
        starts = self.park_in_windows()
        for k, start in enumerate(starts):
            self.d.ILCT_QuiescePlanAdd(PHASE_BUSY, 2, k, start)  # they leave after the third busy probe
        last = self.twin.patch[-1]
        self.d.ILCT_PatchInject(TEST_FAIL_VERIFY, last)
        report = self.install()
        self.assertEqual((report.result, report.site, report.state, report.quiesce_retries, report.rolled_back),
                         (VERIFY_FAILED, last, STATE_RETIRED, 3, 61))
        self.assertEqual(self.twin.image(), self.twin.baseline, 'rollback did not restore the image')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assert_no_allocation_while_suspended()

    def test_rip_that_never_leaves_refuses_with_zero_writes(self):
        row = self.twin.index['TG-01']
        inside = TWIN_BASE + self.twin.rows[row]['rva'] + 3  # the jge after cmp eax,2
        start = self.park(1)[0]
        self.d.ILCT_ParkSetRip(0, inside)
        private, handles = near_private(), handle_count()
        report = self.install()
        self.assertEqual((report.result, report.site, report.mh_calls, report.state, report.quiesce_retries,
                          report.threads_busy, report.hooked),
                         (QUIESCE_BUSY, row, 0, STATE_IDLE, QUIESCE_ATTEMPTS - 1, 1, 0))
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assertEqual(near_private(), private)
        self.assertEqual(handle_count(), handles)
        c = self.counters()
        self.assertEqual((c.heap_alloc, c.heap_create, c.virtual_alloc, c.crt_alloc), (0, 0, 0, 0))
        self.assertEqual(c.suspend_max, report.threads_probed, 'the probe suspends every other thread at once')
        self.assert_no_allocation_while_suspended()
        self.assertEqual(self.d.ILCT_ParkGetRip(0), inside)
        # Nothing was written, so a later install may try again: once the thread has left, it commits.
        self.d.ILCT_ParkSetRip(0, start)
        again = self.install()
        self.assertEqual((again.result, again.hooked, again.quiesce_retries), (OK, 61, 0))

    def test_window_start_counts_as_inside(self):
        row = self.twin.index['UT-01']
        self.park(1)
        self.d.ILCT_ParkSetRip(0, TWIN_BASE + self.twin.rows[row]['rva'])
        report = self.install()
        self.assertEqual((report.result, report.site), (QUIESCE_BUSY, row))

    # ---- threads that enter after the clean probe: MinHook moves them, rollback moves them back ----
    def enter_after_clean_probe(self) -> list[int]:
        starts = self.park(len(self.sites))
        for k, address in enumerate(self.sites):
            self.d.ILCT_QuiescePlanAdd(PHASE_CLEAN, ANY_ATTEMPT, k, address)
        return starts

    def test_threads_entering_after_the_probe_are_relocated_and_moved_back_on_release(self):
        self.enter_after_clean_probe()
        report = self.install()
        self.assertEqual((report.result, report.hooked, report.quiesce_retries), (OK, 61, 0))
        self.assert_no_allocation_while_suspended()
        moved = {}
        for k, (row, offset) in enumerate(self.points):
            rip = self.d.ILCT_ParkGetRip(k)
            tramp = self.twin.info(row).trampoline
            if row in self.twin.calls:
                with self.subTest(site=self.twin.rows[row]['id'], offset=offset):
                    self.assertEqual(rip, self.sites[k], 'a thread at a call row instruction start must stay put')
                moved[k] = rip
                continue
            with self.subTest(site=self.twin.rows[row]['id'], offset=offset):
                self.assertFalse(TWIN_BASE <= rip < TWIN_BASE + TWIN_SIZE, 'thread left in overwritten bytes')
                self.assertTrue(tramp <= rip < tramp + 64, f'RIP 0x{rip:X} not in the trampoline 0x{tramp:X}')
                if offset == 0:
                    self.assertEqual(rip, tramp)
            moved[k] = rip
        self.check_relocated_threads_run_like_the_original(moved)
        self.assertEqual(self.d.ILCT_PatchReleaseAll(), OK)  # MH_RemoveHook per enabled row: freeze + move back
        for k, address in enumerate(self.sites):
            self.assertEqual(self.d.ILCT_ParkGetRip(k), address, 'disable did not move the thread back')
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.assert_no_allocation_while_suspended()

    def test_threads_relocated_by_the_enable_are_moved_back_by_the_enabled_rollback(self):
        self.enter_after_clean_probe()
        last = self.twin.patch[-1]
        self.d.ILCT_PatchInject(TEST_FAIL_VERIFY, last)
        report = self.install()
        self.assertEqual((report.result, report.state, report.rolled_back, report.retained),
                         (VERIFY_FAILED, STATE_RETIRED, 61, 61))
        for k, address in enumerate(self.sites):
            self.assertEqual(self.d.ILCT_ParkGetRip(k), address, 'the rollback did not move the thread back')
        self.assertEqual(self.twin.image(), self.twin.baseline, 'rolled-back image differs')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assert_no_allocation_while_suspended()

    def check_relocated_threads_run_like_the_original(self, moved: dict):
        """Unicorn: from each relocated RIP, the trampoline does what the original bytes do from the parked RIP."""
        if pe.unicorn is None:
            self.skipTest('unicorn not installed')
        emu = pe.Emulator(self.twin)
        uc = emu.uc
        for k, (row, offset) in enumerate(self.points):
            r = self.twin.rows[row]
            site = TWIN_BASE + r['rva']
            patched = bytes(uc.mem_read(site, r['hook_len']))
            rng = random.Random(0x7717 + k)
            for trial in range(6):
                state = pe.random_state(rng)
                emu.write_code(site, checked(r)[:r['hook_len']])
                a = emu.run(site + offset, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                            site <= x < site + r['hook_len'], state)
                emu.write_code(site, patched)
                if row in self.twin.calls:  # still in the image: run the hooked call (island, stub, callee)
                    b = emu.run(moved[k], lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                                site <= x < site + r['hook_len'], state)
                else:
                    b = emu.run(moved[k], lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE), state)
                with self.subTest(site=r['id'], offset=offset, trial=trial):
                    self.assertEqual(a['exit'], b['exit'])
                    self.assertEqual(a['calls'], b['calls'])
                    self.assertEqual(a['gpr'], b['gpr'])
                    self.assertEqual(a['xmm'], b['xmm'])
                    self.assertEqual(a['rflags'], b['rflags'])
                    self.assertEqual({x: v for x, v in a['final'].items() if x >= a['gpr']['RSP'] or
                                      not pe.STACK <= x},
                                     {x: v for x, v in b['final'].items() if x >= b['gpr']['RSP'] or
                                      not pe.STACK <= x})

    # ---- rollback continuation and the last look before the enable ----
    def test_row_left_hooked_by_rollback_continues_through_its_trampoline(self):
        """One row's disable is really skipped, so its E9 stays. Its stub must continue at MinHook's trampoline for
        that row (the pointer MH_CreateHook returned), and execution from the site must reach the stock
        continuation. Control: with the continuation pointed at the site, the same run never leaves (a loop)."""
        row = self.twin.index['FW-01']  # lea eax,[rdx-0xB]; cmp eax,0x10
        r = self.twin.rows[row]
        site = TWIN_BASE + r['rva']
        self.d.ILCT_PatchInject(TEST_FAIL_DISABLE, row)
        report = self.install()
        self.assertEqual((report.result, report.state, report.site), (ROLLBACK_FAILED, STATE_RETIRED, row))
        self.assertEqual(ctypes.string_at(site, 1), b'\xE9', 'the injected row was disabled after all')
        changed = [i for i in range(r['rva'], r['rva'] + 5)]
        image = bytearray(self.twin.image())
        for k in changed:
            image[k] = self.twin.baseline[k]
        self.assertEqual(bytes(image), self.twin.baseline, 'bytes outside the kept jump differ from the original')
        originals = {}
        for patch_row in self.twin.patch:
            info = self.twin.info(patch_row)
            original = self.d.ILCT_PatchOriginal(patch_row)
            originals[patch_row] = original
            self.assertNotEqual(original, 0)
            if patch_row == row:
                self.assertEqual(info.trampoline, original, "continuation is not the row's MinHook trampoline")
            elif patch_row in self.twin.calls:
                self.assertEqual(info.trampoline, self.twin.island(patch_row).callee, 'a call row continues at its callee')
            else:
                self.assertEqual(info.trampoline, TWIN_BASE + self.twin.rows[patch_row]['rva'])
            self.assertEqual(info.gate_open, 0)
        mh = {k: v for k, v in originals.items() if k not in self.twin.calls}  # call rows may share a callee
        self.assertEqual(len(set(mh.values())), len(mh))
        if pe.unicorn is None:
            self.skipTest('unicorn not installed')

        class One:  # the emulator maps MinHook buffers for the rows it is given: only the one still hooked
            pass
        one = One()
        for name in ('image', 'dll', 'rows', 'info', 'addresses'):
            setattr(one, name, getattr(self.twin, name))
        one.patch = [row]
        one.calls = []
        one.call_returns = dict  # no call row is hooked here
        one.island = self.twin.island
        emu = pe.Emulator(one)
        hooked = bytes(emu.uc.mem_read(site, 5))
        inside = lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5  # noqa: E731
        for trial in range(8):
            state = pe.random_state(random.Random(0xB1 + trial))
            emu.write_code(site, checked(r)[:r['hook_len']])
            a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                        site <= x < site + r['hook_len'], state)
            emu.write_code(site, hooked)
            b = emu.run(site, inside, state)
            with self.subTest(trial=trial):
                self.assertEqual((b['exit'], b['calls'], b['gpr'], b['rflags']),
                                 (a['exit'], a['calls'], a['gpr'], a['rflags']))
        slot = self.twin.addresses.tramp_array + 8 * row
        emu.uc.mem_write(slot, struct.pack('<Q', site))  # control: the bug this guards against
        with self.assertRaisesRegex(AssertionError, 'did not leave'):
            emu.run(site, inside, pe.random_state(random.Random(1)))

    def test_site_changed_between_create_and_enable_aborts_with_nothing_enabled(self):
        row = self.twin.patch[25]
        address = TWIN_BASE + self.twin.rows[row]['rva'] + 1
        flipped = ctypes.string_at(address, 1)[0] ^ 0x3C
        private = near_private()
        self.d.ILCT_QuiescePokeBeforeApply(address, flipped)
        self.d.ILCT_PatchInject(TEST_CHANGE_BEFORE_APPLY, row)
        report = self.install()
        self.assertEqual((report.result, report.site, report.state, report.hooked), (SITE_CHANGED, row, STATE_RETIRED, 0))
        image = bytearray(self.twin.baseline)
        image[address - TWIN_BASE] = flipped
        self.assertEqual(self.twin.image(), bytes(image), 'something besides the foreign write changed')
        self.assertEqual(near_private(), private, "MinHook's buffers were not freed")
        c = self.counters()
        self.assertEqual((c.heap_create, c.virtual_alloc), (c.heap_destroy, c.virtual_free))
        self.assert_no_allocation_while_suspended()

    def last_look(self, row: int, address: int, value: int) -> Report:
        self.d.ILCT_QuiescePokeBeforeApply(address, value)
        self.d.ILCT_PatchInject(TEST_CHANGE_BEFORE_APPLY, row)
        return self.install()

    def test_last_look_covers_require_rows_and_gate_decisions(self):
        cases = []
        require = self.twin.index['RT-03']
        gate, gated = self.twin.index['IN-06a'], self.twin.index['IN-06']
        self.assertEqual((self.twin.rows[require]['policy'], self.twin.rows[gate]['policy']), ('REQUIRE', 'GATE'))
        for label, row in (('REQUIRE changed', require), ('gate now mismatches', gate)):
            with self.subTest(label):
                self.twin.reset()
                address = TWIN_BASE + self.twin.rows[row]['rva']
                flipped = ctypes.string_at(address, 1)[0] ^ 0x5A
                private = near_private()
                report = self.last_look(row, address, flipped)
                self.assertEqual((report.result, report.site, report.hooked), (SITE_CHANGED, row, 0))
                image = bytearray(self.twin.baseline)
                image[address - TWIN_BASE] = flipped
                self.assertEqual(self.twin.image(), bytes(image))
                self.assertEqual(near_private(), private)
                cases.append(label)
        with self.subTest('gate now matches'):
            # The gate differs at preflight (IN-06 is skipped), then a writer restores it before the enable: the
            # decision no longer holds, so nothing is enabled.
            self.twin.reset()
            address = TWIN_BASE + self.twin.rows[gate]['rva']
            original = ctypes.string_at(address, 1)[0]
            self.twin.poke(address, bytes([original ^ 0x5A]))
            report = self.last_look(gate, address, original)
            self.assertEqual((report.result, report.site, report.hooked, report.gated_off), (SITE_CHANGED, gate, 0, 1))
            self.assertEqual(self.twin.image(), self.twin.baseline)
            cases.append('gate now matches')
        self.assertEqual(len(cases), 3)
        self.assertNotEqual(gated, gate)

    # ---- leaks ----
    def test_full_cycle_leaks_nothing(self):
        self.park_in_windows()
        starts = [self.d.ILCT_ParkGetRip(k) for k in range(len(self.sites))]
        self.assertEqual(starts, self.sites)
        private = near_private()
        handles = None
        for cycle in range(5):
            # The first two cycles in a process create one lazily-made, process-lifetime system handle (measured:
            # +1 once in cycle 2, then flat for six more cycles); measure the three cycles after them.
            if cycle == 2:
                handles = handle_count()
                self.d.ILCT_CountersReset()
            self.d.ILCT_QuiescePlanClear()
            for k in range(len(self.sites)):
                self.d.ILCT_QuiescePlanAdd(PHASE_BUSY, 0, k, 0x7FFE0000)  # out of every window
                self.d.ILCT_QuiescePlanAdd(PHASE_CLEAN, ANY_ATTEMPT, k, self.sites[k])
            self.assertEqual(self.install().result, OK)
            self.assertEqual(self.d.ILCT_PatchReleaseAll(), OK)
            self.assertEqual(self.twin.image(), self.twin.baseline)
        c = self.counters()
        self.assertGreater(c.heap_create, 0, 'instrument: MinHook created no heap through the counters')
        self.assertGreater(c.virtual_alloc, 0, 'instrument: MinHook reserved no buffer through the counters')
        self.assertEqual(c.heap_create, c.heap_destroy, 'a MinHook heap was not destroyed')
        self.assertEqual(c.virtual_alloc, c.virtual_free, 'a MinHook buffer was not released')
        self.assertEqual(c.crt_alloc, c.crt_free)
        self.assertEqual(c.heap_live_foreign, 0, 'a block on a foreign heap leaked')
        self.assert_no_allocation_while_suspended()
        self.assertGreater(c.suspend_max, len(self.sites), 'instrument: suspensions were not seen')
        self.assertEqual(near_private(), private)
        self.assertEqual(handle_count(), handles)


if __name__ == '__main__':
    unittest.main()
