"""Host tests for call-site hooks by rel32 rewrite (native/coop/src/patch/call_island.*, call_stubs.cpp).

No game run, nothing deployed. The exe is read-only input (SHA-256 pinned by sites.inc), mapped as the image twin.

* static: exactly the PATCH rows whose hook window holds a `call rel32` are call rows (capstone), with the right
  call offset and callee; the stubs' unwind data is in the DLL's .pdata (slow paths and the shared body have an
  entry, the entry jumps do not: they are leaves);
* image twin: install writes only the 18 rel32s (and MinHook's 43 jumps); every call-row failure path (island not
  made, verify, rollback that cannot restore one row) behaves like the MinHook path;
* unicorn: for every call row, RESUME skips the callee and returns the faked result, CALL_AROUND with a no-op
  after-handler equals the original call, an after-handler that edits rax is seen;
* unwind: for every call row, the real RtlVirtualUnwind of this process, fed the emulator's registers and stack,
  walks from inside the handler (handler -> dispatcher -> shared body -> slow path) to site+5 with the RSP the
  original call returns with and every non-volatile register (GPR and XMM6-15) as it was at the site; from inside
  the callee (CALL_AROUND) through our frames to site+5; from inside the callee with the gate closed or a pre-hook
  handler straight to site+5 (no frame of ours on the stack). One more step through the game function's own
  .pdata reaches a planted caller return address.

Needs what test_coop_patch_engine.py needs (pinned g++, IVALICE_LEGACY_GAME, unicorn, capstone).
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest

import test_coop_patch_engine as pe
from test_coop_patch_engine import (GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, Twin, call_offset, checked, near_private,
                                    regions)

try:
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    unicorn = None
try:
    import capstone
except ImportError:
    capstone = None

ISLAND_FAILED, VERIFY_FAILED, ROLLBACK_FAILED, ISLAND_COMMIT_FAILED = 32, 26, 27, 33
TEST_FAIL_CREATE, TEST_FAIL_VERIFY, TEST_FAIL_DISABLE = 1, 3, 4
STATE_RETIRED = 2
NONVOLATILE = ['RBX', 'RBP', 'RSI', 'RDI', 'R12', 'R13', 'R14', 'R15']
CTX_SIZE = 0x4D0
CTX_OFF = {'RAX': 0x78, 'RCX': 0x80, 'RDX': 0x88, 'RBX': 0x90, 'RSP': 0x98, 'RBP': 0xA0, 'RSI': 0xA8, 'RDI': 0xB0,
           'R8': 0xB8, 'R9': 0xC0, 'R10': 0xC8, 'R11': 0xD0, 'R12': 0xD8, 'R13': 0xE0, 'R14': 0xE8, 'R15': 0xF0,
           'RIP': 0xF8}
CTX_XMM0 = 0x1A0
UNW_FLAG_NHANDLER = 0

_ntdll = ctypes.WinDLL('ntdll') if hasattr(ctypes, 'WinDLL') else None
_k32 = ctypes.WinDLL('kernel32', use_last_error=True) if hasattr(ctypes, 'WinDLL') else None
if _ntdll is not None:
    _ntdll.RtlLookupFunctionEntry.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
    _ntdll.RtlLookupFunctionEntry.restype = ctypes.c_void_p
    _ntdll.RtlVirtualUnwind.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                        ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
    _ntdll.RtlVirtualUnwind.restype = ctypes.c_void_p
    _k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
    _k32.VirtualAlloc.restype = ctypes.c_void_p


class Context:
    """A 16-byte aligned CONTEXT for RtlVirtualUnwind, read and written by field offset."""

    def __init__(self):
        self.raw = ctypes.create_string_buffer(CTX_SIZE + 16)
        self.addr = (ctypes.addressof(self.raw) + 15) & ~15

    def get(self, name: str) -> int:
        return struct.unpack('<Q', ctypes.string_at(self.addr + CTX_OFF[name], 8))[0]

    def set(self, name: str, value: int):
        ctypes.memmove(self.addr + CTX_OFF[name], struct.pack('<Q', value), 8)

    def xmm(self, k: int) -> int:
        lo, hi = struct.unpack('<QQ', ctypes.string_at(self.addr + CTX_XMM0 + 16 * k, 16))
        return lo | hi << 64

    def set_xmm(self, k: int, value: int):
        ctypes.memmove(self.addr + CTX_XMM0 + 16 * k,
                       struct.pack('<QQ', value & (2**64 - 1), value >> 64), 16)


_stack_mapped = []


def map_stack_in_process():
    """The emulator's stack, at the same address in this process, so RtlVirtualUnwind can read it."""
    if not _stack_mapped:
        got = _k32.VirtualAlloc(pe.STACK, pe.STACK_SIZE, 0x3000, 0x04)
        if got != pe.STACK:
            raise AssertionError(f'could not reserve the emulator stack at 0x{pe.STACK:X} in this process')
        _stack_mapped.append(got)


class Walker:
    """Unwinds with this process's RtlVirtualUnwind: our DLL through the loader's function tables, the image twin
    through its own .pdata (mapped at 0x120000; the loader does not know it)."""

    def __init__(self, twin: Twin):
        self.twin = twin
        f = twin.file
        lfanew = struct.unpack_from('<I', f, 0x3C)[0]
        self.pdata_rva, self.pdata_size = struct.unpack_from('<II', f, lfanew + 24 + 112 + 3 * 8)
        self.count = self.pdata_size // 12
        handle = twin.dll._handle
        size = struct.unpack('<I', ctypes.string_at(handle + lfanew_of(handle) + 24 + 56, 4))[0]
        self.dll = (handle, handle + size)

    def frame_register(self, rip: int) -> int:
        """FrameRegister of the unwind data covering rip (chained entries included), 0 = none."""
        entry = self.twin_entry(rip)
        while entry:
            unwind = struct.unpack('<III', ctypes.string_at(entry, 12))[2]
            ver, _, count, frame = ctypes.string_at(TWIN_BASE + unwind, 4)
            if frame & 0xF:
                return frame & 0xF
            if not ver & 0x20:  # UNW_FLAG_CHAININFO
                return 0
            entry = TWIN_BASE + unwind + 4 + 2 * ((count + 1) & ~1)
        return 0

    def twin_entry(self, rip: int):
        rva = rip - TWIN_BASE
        lo, hi = 0, self.count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            at = TWIN_BASE + self.pdata_rva + 12 * mid
            begin, end, _ = struct.unpack('<III', ctypes.string_at(at, 12))
            if rva < begin:
                hi = mid - 1
            elif rva >= end:
                lo = mid + 1
            else:
                return at
        return None

    def step(self, ctx: Context) -> str:
        rip = ctx.get('RIP')
        base = ctypes.c_uint64(0)
        if self.dll[0] <= rip < self.dll[1]:
            entry = _ntdll.RtlLookupFunctionEntry(rip, ctypes.byref(base), None)
            image = base.value
        elif TWIN_BASE <= rip < TWIN_BASE + TWIN_SIZE:
            entry, image = self.twin_entry(rip), TWIN_BASE
        else:
            raise AssertionError(f'walk left the known images at 0x{rip:X}')
        if not entry:
            rsp = ctx.get('RSP')
            ctx.set('RIP', struct.unpack('<Q', ctypes.string_at(rsp, 8))[0])
            ctx.set('RSP', rsp + 8)
            return 'leaf'
        data, frame = ctypes.c_void_p(), ctypes.c_uint64()
        _ntdll.RtlVirtualUnwind(UNW_FLAG_NHANDLER, image, rip, entry, ctx.addr, ctypes.byref(data),
                                ctypes.byref(frame), None)
        return 'pdata'


def lfanew_of(handle: int) -> int:
    return struct.unpack('<I', ctypes.string_at(handle + 0x3C, 4))[0]


def capture(uc) -> Context:
    """The emulator's registers in a CONTEXT, and its stack copied into this process at the same address."""
    ctypes.memmove(pe.STACK, bytes(uc.mem_read(pe.STACK, pe.STACK_SIZE)), pe.STACK_SIZE)
    ctx = Context()
    for name in CTX_OFF:
        ctx.set(name, uc.reg_read(getattr(X, f'UC_X86_REG_{name}')))
    for k in range(16):
        ctx.set_xmm(k, uc.reg_read(getattr(X, f'UC_X86_REG_XMM{k}')))
    return ctx


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class CallRowStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()

    @unittest.skipUnless(capstone is not None, 'requires capstone')
    def test_call_rows_are_exactly_the_windows_holding_a_call(self):
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        found = {}
        for row in self.twin.patch:
            r = self.twin.rows[row]
            site = TWIN_BASE + r['rva']
            for insn in cs.disasm(checked(r)[:r['hook_len']], site):
                if insn.mnemonic == 'call':
                    found[r['id']] = (insn.address - site, int(insn.op_str, 16))
        island = {self.twin.rows[row]['id']: self.twin.island(row) for row in self.twin.patch}
        self.assertEqual(len(found), 18)
        self.assertEqual({sid for sid, i in island.items() if i.is_call_row}, set(found))
        self.assertEqual({self.twin.rows[r]['id'] for r in self.twin.calls}, set(found))
        for sid, (offset, target) in found.items():
            row = self.twin.index[sid]
            self.assertEqual(island[sid].call_offset, offset, sid)
            self.assertEqual(call_offset(self.twin.rows[row]), offset, sid)
            self.assertEqual(TWIN_BASE + self.twin.dll.ILCT_IslandCalleeRva(row), target, sid)
        self.assertEqual(set(found), {'UT-01', 'WT-01', 'WT-02', 'WT-03', 'IN-01', 'IN-07', 'MV-04', 'MV-07', 'IX-01',
                                      'PT-01', 'CM-02', 'CM-03', 'CM-04', 'CM-05', 'IN-02', 'IN-03', 'IN-05', 'FW-05'})

    def test_stub_unwind_data_is_static_pdata(self):
        self.twin.dll.ILCT_IslandCalleeRva.restype = ctypes.c_uint64
        base = ctypes.c_uint64()
        for row in self.twin.calls:
            info = self.twin.island(row)
            sid = self.twin.rows[row]['id']
            slow = _ntdll.RtlLookupFunctionEntry(info.slow + 8, ctypes.byref(base), None)
            self.assertTrue(slow, f'{sid}: the slow path has no .pdata entry')
            begin, end, unwind = struct.unpack('<III', ctypes.string_at(slow, 12))
            self.assertEqual(base.value + begin, info.slow, sid)
            ver, prolog, count, frame = ctypes.string_at(base.value + unwind, 4)
            codes = ctypes.string_at(base.value + unwind + 4, 2 * count)
            # pushfq (alloc 8, offset 1) then lea rsp,[rsp-0x120] (alloc 0x120: UWOP_ALLOC_LARGE, scaled by 8)
            self.assertEqual((ver & 7, frame), (1, 0), sid)
            total, k = 0, 0
            while k < len(codes):
                op, info_ = codes[k + 1] & 0xF, codes[k + 1] >> 4
                if op == 2:  # ALLOC_SMALL
                    total += info_ * 8 + 8
                    k += 2
                elif op == 1 and info_ == 0:  # ALLOC_LARGE, 16-bit count of qwords
                    total += struct.unpack_from('<H', codes, k + 2)[0] * 8
                    k += 4
                else:
                    self.fail(f'{sid}: unexpected unwind code {op}/{info_}')
            self.assertEqual(total, 0x128, sid)
            self.assertFalse(_ntdll.RtlLookupFunctionEntry(info.entry, ctypes.byref(base), None),
                             f'{sid}: the entry jump must be a leaf (no unwind data)')
        common = _ntdll.RtlLookupFunctionEntry(self.twin.island(self.twin.calls[0]).common + 8, ctypes.byref(base),
                                               None)
        self.assertTrue(common, 'the shared body has no .pdata entry')

    def test_shipped_dll_carries_the_call_stubs_in_pdata(self):
        manifest = pe.built()['manifest']
        self.assertTrue(manifest['audit']['ok'], manifest['audit']['failures'])
        map_text = (pe.built()['dll'].parent / pe.coop_build.MAP_NAME).read_text(encoding='utf-8', errors='replace')
        for symbol in ('ilc_call_common', 'ilc_call_slow_0', 'ilc_call_slow_17', 'ilc_call_entry_17',
                       'ilc_call_dispatch', 'ilc_call_after'):
            self.assertIn(symbol, map_text)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class CallRowTransactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()

    def setUp(self):
        self.twin.reset()

    def tearDown(self):
        self.twin.dll.ILCT_PatchInject(0, 0)
        self.twin.dll.ILCT_IslandFailWrite(9999, 9999)
        self.twin.reset()

    def test_island_not_made_is_the_not_enabled_rollback(self):
        private = near_private()
        row = self.twin.index['IN-05']
        self.twin.dll.ILCT_PatchInject(TEST_FAIL_CREATE, row)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.site, report.state, report.rolled_back, report.retained),
                         (ISLAND_FAILED, row, STATE_RETIRED, len(self.twin.mh) + self.twin.calls.index(row), 0))
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assertEqual(near_private(), private, 'MinHook buffers or the island page were left mapped')
        self.assertEqual(self.twin.dll.ILCT_IslandRegion(), 0)

    def test_verify_failure_on_a_call_row_restores_every_rel32_and_keeps_the_islands(self):
        row = self.twin.index['UT-01']
        self.twin.dll.ILCT_PatchInject(TEST_FAIL_VERIFY, row)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.site, report.state, report.rolled_back, report.retained),
                         (VERIFY_FAILED, row, STATE_RETIRED, 61, 61))
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.assertNotEqual(self.twin.dll.ILCT_IslandRegion(), 0, 'a rel32 was written: the islands must stay')
        for call_row in self.twin.calls:
            info = self.twin.island(call_row)
            self.assertEqual(info.slot, info.callee, self.twin.rows[call_row]['id'])
            self.assertEqual(self.twin.info(call_row).gate_open, 0)

    def test_partial_commit_is_undone_inside_the_same_freeze(self):
        """The rel32 write of a call row j fails after earlier rows were written: those rows get their pinned rel32
        back inside the same freeze, nothing points at an island, and only then may the page go."""
        row = self.twin.index['MV-07']
        self.assertGreater(self.twin.calls.index(row), 3, 'earlier call rows must have been written first')
        self.twin.dll.ILCT_IslandFailWrite(row, 9999)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.state), (ISLAND_COMMIT_FAILED, STATE_RETIRED))
        self.assertEqual(self.twin.image(), self.twin.baseline, 'a written rel32 was left behind')
        self.assertEqual(self.twin.dll.ILCT_IslandRegion(), 0, 'nothing points at the islands: the page is freed')

    def test_partial_commit_whose_undo_fails_keeps_the_page_and_reverts_later(self):
        """Same, but the undo of an earlier row i fails too: row i stays committed, the enabled rollback's revert
        restores it, and the island page is kept (a thread may have run through row i's island)."""
        row, earlier = self.twin.index['MV-07'], self.twin.index['IN-01']
        self.assertLess(self.twin.calls.index(earlier), self.twin.calls.index(row))
        self.twin.dll.ILCT_IslandFailWrite(row, earlier)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.state), (ISLAND_COMMIT_FAILED, STATE_RETIRED))
        self.assertEqual(self.twin.image(), self.twin.baseline, 'the revert did not restore row i')
        self.assertNotEqual(self.twin.dll.ILCT_IslandRegion(), 0, 'row i pointed at its island: the page must stay')
        info = self.twin.island(earlier)
        self.assertEqual(info.slot, info.callee)

    def test_rollback_that_cannot_restore_a_call_row_leaves_a_working_island(self):
        row = self.twin.index['IN-07']
        r = self.twin.rows[row]
        self.twin.dll.ILCT_PatchInject(TEST_FAIL_DISABLE, row)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.state), (ROLLBACK_FAILED, STATE_RETIRED))
        info = self.twin.island(row)
        call = TWIN_BASE + r['rva']
        self.assertEqual(call + 5 + struct.unpack('<i', ctypes.string_at(call + 1, 4))[0], info.island)
        self.assertEqual(info.slot, info.callee, 'the left-over island must lead straight to the callee')
        after = self.twin.image()
        for other in self.twin.patch:
            if other == row:
                continue
            o = self.twin.rows[other]
            self.assertEqual(after[o['rva']:o['rva'] + o['hook_len']], checked(o)[:o['hook_len']], o['id'])


def fresh_state(rng: random.Random) -> dict:
    state = pe.random_state(rng)
    state['rflags'] &= ~0x400  # DF clear at a call boundary (ABI)
    return state


@unittest.skipUnless(NATIVE and GAME and unicorn is not None and capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class CallRowEmulationTest(unittest.TestCase):
    TRIALS = 6

    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()
        cls.twin.reset()
        report = cls.twin.call('ILCT_PatchInstall')
        if report.result != OK:
            raise AssertionError(f'install failed: {report.result} at row {report.site}')
        cls.twin.dll.ILCT_PatchSetMode(1)
        cls.emu = pe.Emulator(cls.twin)
        cls.marker = cls.twin.dll.ILCT_IslandFakeResult()
        map_stack_in_process()
        cls.walker = Walker(cls.twin)

    @classmethod
    def tearDownClass(cls):
        cls.twin.reset()

    def open_live(self, row: int):
        emu, uc = self.emu, self.emu.uc
        emu.force_gate(row, True)
        uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQ', 1, 7, 1_000_000))
        emu.set_clock(1_000_005)

    def close(self, row: int):
        self.emu.force_gate(row, False)
        self.emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQ', 0, 0, 0))

    def runs(self, row: int, state: dict, on_callee=None):
        r = self.twin.rows[row]
        site = TWIN_BASE + r['rva']
        allowed = (lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + r['hook_len'])
        return self.emu.run(site, allowed, state, on_callee=on_callee)

    def original(self, row: int, state: dict):
        r = self.twin.rows[row]
        site = TWIN_BASE + r['rva']
        patched = bytes(self.emu.uc.mem_read(site, r['hook_len']))
        self.emu.write_code(site, checked(r)[:r['hook_len']])
        try:
            return self.runs(row, state)
        finally:
            self.emu.write_code(site, patched)

    def set_mode(self, row: int, mode: int):
        """Register a test handler in-process and copy the handler/after tables into the emulator."""
        self.twin.dll.ILCT_IslandSetHandler(row, mode)
        lo, hi = self.emu.dll
        for base, length, state, protect, _ in regions(lo, hi):
            if state == 0x1000 and protect in (0x04, 0x08, 0x40):  # writable data: handler and after tables
                self.emu.uc.mem_write(base, ctypes.string_at(base, length))
        self.emu.uc.mem_write(self.twin.addresses.clock_slot, struct.pack('<Q', pe.CLOCK_PAGE))

    def test_resume_around_and_after_on_every_call_row(self):
        for row in self.twin.calls:
            r = self.twin.rows[row]
            sid = r['id']
            ret = TWIN_BASE + r['rva'] + call_offset(r) + 5
            rng = random.Random(0xB20 + row)
            for trial in range(self.TRIALS):
                state = fresh_state(rng)
                a = self.original(row, state)
                with self.subTest(site=sid, trial=trial, mode='resume'):
                    self.set_mode(row, 1)
                    self.open_live(row)
                    b = self.runs(row, state)
                    self.close(row)
                    self.assertEqual(b['exit'], ret)
                    self.assertEqual(b['calls'], [], f'{sid}: RESUME must skip the callee')
                    self.assertEqual(b['gpr']['RAX'], self.marker)
                    self.assertEqual({k: v for k, v in b['gpr'].items() if k != 'RAX'},
                                     {k: v for k, v in a['gpr'].items() if k != 'RAX'})
                for mode, label in ((2, 'around'), (4, 'around-no-after')):
                    with self.subTest(site=sid, trial=trial, mode=label):
                        self.set_mode(row, mode)
                        self.open_live(row)
                        b = self.runs(row, state)
                        self.close(row)
                        self.assertEqual((b['exit'], b['calls']), (a['exit'], a['calls']), sid)
                        self.assertEqual(b['gpr'], a['gpr'], sid)
                        self.assertEqual(b['xmm'], a['xmm'], sid)
                        self.assertEqual(b['rflags'], a['rflags'], sid)
                with self.subTest(site=sid, trial=trial, mode='after-edits-rax'):
                    self.set_mode(row, 3)
                    self.open_live(row)
                    b = self.runs(row, state)
                    self.close(row)
                    self.assertEqual(b['calls'], a['calls'])
                    self.assertEqual(b['gpr']['RAX'], a['gpr']['RAX'] ^ self.marker)
            self.set_mode(row, 0)

    # ---- unwinding ---------------------------------------------------------------------------------------------
    def walk_to_site(self, ctx: Context, row: int, state: dict, expect_ours: list) -> list:
        """Walk until the first frame outside our DLL; check our frames, then the game frame (site+5)."""
        r = self.twin.rows[row]
        ret = TWIN_BASE + r['rva'] + call_offset(r) + 5
        lo, hi = self.walker.dll
        frames = []
        for _ in range(16):
            rip = ctx.get('RIP')
            if not lo <= rip < hi:
                break
            frames.append(rip)
            self.walker.step(ctx)
        info = self.twin.island(row)
        named = []
        for rip in frames:
            if info.slow <= rip < info.slow + 0x100:
                named.append('slow')
            elif info.common <= rip < info.common + 0x400:
                named.append('common')
            else:
                named.append('dll')
        self.assertEqual(named, expect_ours, f'{r["id"]}: frames of ours')
        self.assertEqual(ctx.get('RIP'), ret, f'{r["id"]}: the walk must reach site+5')
        self.assertEqual(ctx.get('RSP'), state['gpr']['RSP'], f'{r["id"]}: RSP after the game call returns')
        for name in NONVOLATILE:
            self.assertEqual(ctx.get(name), state['gpr'][name], f'{r["id"]}: {name} not restored by the walk')
        for k in range(6, 16):
            self.assertEqual(ctx.xmm(k), state['xmm'][k], f'{r["id"]}: XMM{k} not restored by the walk')
        return named

    def site_state(self, row: int, rng: random.Random) -> dict:
        """A random state with a 16-byte aligned RSP (the game's RSP at a call), and, when the game function keeps a
        frame pointer, that register pointing into the stack above RSP (as its prolog would have left it)."""
        state = fresh_state(rng)
        state['gpr']['RSP'] &= ~0xF
        r = self.twin.rows[row]
        reg = self.walker.frame_register(TWIN_BASE + r['rva'] + call_offset(r) + 5)
        if reg:
            name = ['RAX', 'RCX', 'RDX', 'RBX', 'RSP', 'RBP', 'RSI', 'RDI', 'R8', 'R9', 'R10', 'R11', 'R12', 'R13',
                    'R14', 'R15'][reg]
            state['gpr'][name] = state['gpr']['RSP'] + 0x200
        return state

    def game_step(self, ctx: Context, row: int):
        """One step through the game function's own .pdata: plant a caller return address where its unwind data
        says the return address lives, and the walk must land on it."""
        sentinel = 0x1234_5678_9ABC
        probe = Context()
        ctypes.memmove(probe.addr, ctx.addr, CTX_SIZE)
        self.walker.step(probe)
        slot = probe.get('RSP') - 8
        self.assertTrue(pe.STACK <= slot < pe.STACK + pe.STACK_SIZE - 8, 'the game frame lies outside the stack')
        ctypes.memmove(slot, struct.pack('<Q', sentinel), 8)
        self.walker.step(ctx)
        self.assertEqual(ctx.get('RIP'), sentinel, f'{self.twin.rows[row]["id"]}: the game frame does not unwind')

    def test_unwind_from_inside_the_handler_reaches_the_game_caller(self):
        handler = self.twin.addresses.passthrough
        results = {}
        for row in self.twin.calls:
            r = self.twin.rows[row]
            state = self.site_state(row, random.Random(0x5E + row))
            captured = []

            def at_handler(uc, address, size, _):
                if address == handler and not captured:
                    captured.append(capture(uc))

            hook = self.emu.uc.hook_add(unicorn.UC_HOOK_CODE, at_handler, begin=handler, end=handler)
            try:
                self.twin.dll.ILCT_PatchSetPassthrough(row, 1)
                self.set_mode_tables()
                self.open_live(row)
                self.runs(row, state)
            finally:
                self.close(row)
                self.emu.uc.hook_del(hook)
                self.twin.dll.ILCT_PatchSetPassthrough(row, 0)
            with self.subTest(site=r['id']):
                self.assertEqual(len(captured), 1, 'the handler did not run')
                ctx = captured[0]
                named = self.walk_to_site(ctx, row, state, ['dll', 'dll', 'common', 'slow'])
                self.game_step(ctx, row)
                results[r['id']] = named
        print(f'\nunwind from the handler: {len(results)}/18 call rows reach site+5 and the game caller')
        self.assertEqual(len(results), 18)

    def set_mode_tables(self):
        lo, hi = self.emu.dll
        for base, length, state, protect, _ in regions(lo, hi):
            if state == 0x1000 and protect in (0x04, 0x08, 0x40):
                self.emu.uc.mem_write(base, ctypes.string_at(base, length))
        self.emu.uc.mem_write(self.twin.addresses.clock_slot, struct.pack('<Q', pe.CLOCK_PAGE))

    def test_unwind_from_inside_the_callee(self):
        cases = {'around': (2, True, ['common', 'slow']), 'pre-hook': (-1, True, []), 'closed': (0, False, [])}
        done = 0
        for row in self.twin.calls:
            r = self.twin.rows[row]
            for label, (mode, open_, ours) in cases.items():
                state = self.site_state(row, random.Random(0xCA11 + row))
                captured = []

                def at_callee(uc, address):
                    captured.append(capture(uc))

                if mode == -1:
                    self.twin.dll.ILCT_PatchSetPassthrough(row, 1)
                    self.set_mode_tables()
                else:
                    self.set_mode(row, mode)
                if open_:
                    self.open_live(row)
                try:
                    self.runs(row, state, on_callee=at_callee)
                finally:
                    self.close(row)
                    self.set_mode(row, 0)
                with self.subTest(site=r['id'], mode=label):
                    self.assertEqual(len(captured), 1, 'the callee was not entered exactly once')
                    ctx = captured[0]
                    callee = ctx.get('RIP')
                    rsp = ctx.get('RSP')
                    back = struct.unpack('<Q', ctypes.string_at(rsp, 8))[0]
                    if not ours:
                        ret = TWIN_BASE + r['rva'] + call_offset(r) + 5
                        self.assertEqual(back, ret, f'{r["id"]} {label}: a foreign return address while game code runs')
                    # The callee at its first instruction is unwound as at offset 0 of its own function.
                    self.walker.step(ctx)
                    self.walk_to_site(ctx, row, state, ours)
                    self.assertTrue(TWIN_BASE <= callee < TWIN_BASE + TWIN_SIZE)
                    done += 1
        print(f'\nunwind from inside the callee: {done} walks (18 rows x around / pre-hook / closed)')
        self.assertEqual(done, 54)


if __name__ == '__main__':
    unittest.main()
