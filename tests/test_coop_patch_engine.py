"""Host tests for the co-op patch layer (native/coop/src/patch, MinHook 1.3.4). No game run, nothing deployed.

* static: the generated site table is current (gen_site_table.py --check), the PATCH rows and their hook windows
  are sane, no address constant >= SizeOfImage in src/patch is untagged, the MinHook pin and notice are present,
  and the shipped DLL links MinHook without any new export;
* image twin: the test DLL maps FFXII_TZA.exe (read-only input, SHA-256 pinned by sites.inc) at 0x120000 in this
  process and runs the real preflight/install/rollback on it: a wrong byte gives zero writes (image, protections,
  near-image allocations and MinHook calls all unchanged); install hooks every PATCH row or none; every failure
  path restores the image byte for byte; the arm flags open exactly the expected gates;
* unicorn equivalence: for every PATCH row, the original bytes and the hooked path (MinHook's jump, relay, our stub
  and MinHook's trampoline; for the 18 call rows the retargeted call, the island, our call stub and the callee)
  run from the same random machine state and must leave the same registers, flags,
  XMM registers, memory (outside our own module and the dead stack below RSP) and continuation, with the gate
  closed, with the gate open and stale liveness, and with a fresh liveness and a pass-through handler.

Needs the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes its absence a failure), IVALICE_LEGACY_GAME for the exe,
unicorn and capstone; the site-table drift check also needs ILC_SITE_TABLE (the site table JSON) and numpy.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
sys.path.insert(0, str(COOP))

import build as coop_build  # noqa: E402

try:
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    unicorn = None
try:
    import capstone
except ImportError:
    capstone = None

COMPILER = Path(os.environ.get('IVALICE_LEGACY_CXX', 'C:/msys64/ucrt64/bin/g++.exe'))
CAN_BUILD = os.name == 'nt' and struct.calcsize('P') == 8 and COMPILER.is_file()
REQUIRE_NATIVE = os.environ.get('ILC_REQUIRE_NATIVE') == '1'
NATIVE = CAN_BUILD or REQUIRE_NATIVE
GAME = os.environ.get('IVALICE_LEGACY_GAME')
SITE_TABLE = os.environ.get('ILC_SITE_TABLE')
SITES_INC = COOP / 'src' / 'patch' / 'sites.inc'
TWIN_BASE, TWIN_SIZE, TIMESTAMP = 0x120000, 0x2F76000, 0x5DCA7FD7

# patch::result
OK, BAD_BASE, BAD_TIMESTAMP, BAD_SIZE, SITE_MISMATCH = 0, 1, 4, 5, 7
INSTALL_RETIRED, CREATE_FAILED, APPLY_FAILED, VERIFY_FAILED = 21, 23, 25, 26
STATE_IDLE, STATE_INSTALLED, STATE_RETIRED = 0, 1, 2
TEST_FAIL_CREATE, TEST_FAIL_APPLY, TEST_FAIL_VERIFY = 1, 2, 3
POLICY = {1: 'PATCH', 2: 'REQUIRE', 3: 'GATE', 4: 'INFO'}
SUBSYS = {'INPUT_ISOLATION': 0x01, 'MOVEMENT': 0x02, 'PARTY': 0x04, 'CAMERA': 0x08, 'FIREWALL_MENUS': 0x10,
          'WAIT': 0x20, 'RENDER': 0x40, 'TARGETS_CARDS': 0x80, 'CORE': 0, 'NONE': 0x80000000}

ROW = re.compile(r'X\("([^"]+)", 0x([0-9A-F]+)u, (\d+), ILC_SITE_KIND_(\w+), ILC_SITE_POLICY_(\w+), '
                 r'ILC_SITE_SUBSYS_(\w+), 0x([0-9A-F]+), (\w+), "([^"]*)", (\d+), "([^"]*)", "([^"]*)"\)')


def c_bytes(text: str) -> bytes:
    return bytes(int(h, 16) for h in re.findall(r'\\x([0-9A-F]{2})', text))


def site_rows() -> list[dict]:
    rows = []
    for m in ROW.finditer(SITES_INC.read_text(encoding='utf-8')):
        sid, rva, length, kind, policy, subsys, flags, gate, expected, hook_len, tail, purpose = m.groups()
        rows.append({'id': sid, 'rva': int(rva, 16), 'length': int(length), 'kind': kind, 'policy': policy,
                     'subsystem': subsys, 'flags': int(flags, 16), 'gate': gate, 'expected': c_bytes(expected),
                     'hook_len': int(hook_len), 'tail': c_bytes(tail), 'purpose': purpose})
    return rows


def checked(row: dict) -> bytes:
    return row['expected'] + row['tail']


def untagged_constants(line: str) -> list[str]:
    """Hex literals >= SizeOfImage on a line without a `// ... VA` or `// ... NOTADDR` tag (every address in our
    source is an RVA below 0x2F76000, or says it is a VA)."""
    if re.search(r'//.*\b(VA|NOTADDR)\b', line):
        return []
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)  # byte strings are not address constants
    code = code.split('//', 1)[0]  # nor is prose in a comment
    return [lit for lit in re.findall(r'\b0[xX][0-9A-Fa-f]+', code) if int(lit, 16) >= TWIN_SIZE]


# ---- static ----------------------------------------------------------------------------------------------------
class PatchStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = site_rows()

    def test_rows_parse(self):
        self.assertEqual(len(self.rows), 111)
        self.assertIn('#define ILC_SITE_COUNT 111', SITES_INC.read_text(encoding='utf-8'))

    def test_patch_rows_and_hook_windows(self):
        patch = [r for r in self.rows if r['policy'] == 'PATCH']
        self.assertEqual(len(patch), 61)
        for r in self.rows:
            with self.subTest(r['id']):
                if r['policy'] == 'PATCH':
                    self.assertGreaterEqual(r['hook_len'], 5)
                    self.assertEqual(len(r['tail']), max(0, r['hook_len'] - r['length']))
                    self.assertFalse(r['flags'] & 0x03, 'PATCH row flagged INTERIOR or DROPPED')
                    self.assertNotEqual(r['subsystem'], 'NONE')
                else:
                    self.assertEqual((r['hook_len'], r['tail']), (0, b''))
        interior = {r['id'] for r in self.rows if r['flags'] & 0x01}
        self.assertEqual(interior, {'CL-02', 'RT-07'})
        self.assertFalse(interior & {r['id'] for r in patch}, 'an INTERIOR row is a port site')
        # Hook windows overlap no other row (INFO rows included).
        for a in patch:
            for b in self.rows:
                if a is b:
                    continue
                b_end = b['rva'] + (b['hook_len'] if b['policy'] == 'PATCH' else len(checked(b)))
                self.assertFalse(a['rva'] < b_end and b['rva'] < a['rva'] + a['hook_len'],
                                 f'{a["id"]} hook window overlaps {b["id"]}')

    def test_no_untagged_address_constant_at_or_above_size_of_image(self):
        bad = []
        for path in sorted((COOP / 'src' / 'patch').iterdir()):
            if path.suffix not in ('.h', '.cpp', '.inc'):
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                bad += [f'{path.name}:{number}: {c}' for c in untagged_constants(line)]
        self.assertEqual(bad, [])

    def test_instrument_flags_an_untagged_va(self):
        self.assertEqual(untagged_constants('constexpr uintptr_t X = 0x7A15A0;'), [])  # an RVA: allowed
        self.assertEqual(untagged_constants('constexpr uintptr_t X = 0x8A15A0;'), [])
        self.assertEqual(untagged_constants('constexpr uintptr_t X = 0x2F76000;'), ['0x2F76000'])
        self.assertEqual(untagged_constants('X = 0x89A15A0;  // VA of the finalize call'), [])
        self.assertEqual(untagged_constants('X = 0x80000000u;  // NOTADDR: sentinel'), [])
        self.assertEqual(untagged_constants(r'X("A", 0x1u, 1, "\x2F\x76\x00\x00", "0x3000000")'), [])

    def test_minhook_pin_and_notice(self):
        lock = json.loads((COOP / 'third_party' / 'minhook' / 'minhook.lock.json').read_text(encoding='utf-8'))
        src = lock['source']
        self.assertEqual((src['version'], src['tag']), ('1.3.4', 'v1.3.4'))
        self.assertEqual(src['commit'], 'c3fcafdc10146beb5919319d0683e44e3c30d537')
        self.assertEqual(src['sha256'], '1aebeae4ca898330c507860acc2fca2eb335fe446a3a2b8444c3bf8b2660a14e')
        self.assertTrue(src['url'].startswith('https://github.com/TsudaKageyu/minhook/'))
        self.assertTrue(all(lock['build'][k] for k in ('treeSha256', 'libSha256', 'licenceSha256')))
        notice = (COOP / 'THIRD_PARTY_NOTICES.txt').read_text(encoding='utf-8')
        for needle in ('MinHook', 'Copyright (C) 2009-2017 Tsuda Kageyu.', 'Hacker Disassembler Engine 64 C',
                       'Copyright (c) 2008-2009, Vyacheslav Patkov.', 'Redistributions in binary form must reproduce'):
            self.assertIn(needle, notice)

    @unittest.skipUnless(SITE_TABLE and GAME, 'set ILC_SITE_TABLE and IVALICE_LEGACY_GAME for the drift check')
    def test_site_table_is_current(self):
        result = subprocess.run([sys.executable, '-I', str(COOP / 'tools' / 'gen_site_table.py'), '--check',
                                 '--site-table', SITE_TABLE, '--game', GAME], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


_built: dict = {}


def built() -> dict:
    if not CAN_BUILD:
        raise AssertionError(f'native toolchain unavailable: {COMPILER} (ILC_REQUIRE_NATIVE=1)')
    if not _built:
        temp = Path(tempfile.mkdtemp(prefix='ilc-patch-test-'))
        _built['manifest'] = coop_build.build(COMPILER.resolve(), temp)
        _built['dll'] = temp / coop_build.DLL_NAME
        _built['test'] = coop_build.build_test_dll(COMPILER.resolve(), temp / 'test')
    return _built


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class PatchShippedDllTest(unittest.TestCase):
    MINHOOK_IMPORTS = {'CreateToolhelp32Snapshot', 'Thread32First', 'Thread32Next', 'OpenThread', 'SuspendThread',
                       'ResumeThread', 'GetThreadContext', 'SetThreadContext', 'FlushInstructionCache', 'VirtualAlloc',
                       'VirtualFree', 'VirtualQuery', 'VirtualProtect', 'HeapCreate', 'HeapDestroy', 'HeapAlloc',
                       'HeapReAlloc', 'HeapFree', 'GetSystemInfo', 'GetCurrentProcess', 'GetCurrentProcessId',
                       'GetCurrentThreadId', 'CloseHandle', 'GetLastError', 'Sleep', 'GetModuleHandleW',
                       'GetProcAddress'}

    def test_minhook_is_linked_static_with_allowlisted_imports_and_no_new_export(self):
        manifest = built()['manifest']
        self.assertTrue(manifest['audit']['ok'], manifest['audit']['failures'])
        kernel32 = set(manifest['audit']['imports']['KERNEL32.dll'])
        self.assertLessEqual(self.MINHOOK_IMPORTS, kernel32)
        self.assertFalse([d for d in manifest['audit']['imports'] if 'minhook' in d.lower()])
        self.assertEqual({e['name'] for e in manifest['audit']['exports']},
                         {'FF12HgetVer', 'FF12HgetName', 'FF12Hinit', 'FF12HinitDone', 'FF12Hcleanup', 'ILC_Bridge'})
        mh = manifest['toolchain']['minhook']
        lock = json.loads((COOP / 'third_party' / 'minhook' / 'minhook.lock.json').read_text(encoding='utf-8'))
        self.assertEqual((mh['version'], mh['linkage'], mh['sha256']), ('1.3.4', 'static', lock['build']['libSha256']))
        map_text = (built()['dll'].parent / coop_build.MAP_NAME).read_text(encoding='utf-8', errors='replace')
        for symbol in ('MH_CreateHook', 'MH_ApplyQueued', 'ilc_patch_dispatch', 'ilc_patch_stub_base'):
            self.assertRegex(map_text, re.compile(rf'^\s+0x[0-9a-f]+\s+{symbol}\s*$', re.M), symbol)

    def test_allowlist_reasons_name_minhook(self):
        allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))
        text = ' '.join(k + ' ' + v for k, v in allow['importReasons'].items() if 'MinHook' in v)
        for name in self.MINHOOK_IMPORTS:
            self.assertIn(name, text)


# ---- the image twin --------------------------------------------------------------------------------------------
class Report(ctypes.Structure):
    _fields_ = [('result', ctypes.c_int32), ('site', ctypes.c_int32), ('detail', ctypes.c_int32),
                ('state', ctypes.c_uint32), ('patch_rows', ctypes.c_uint32), ('hooked', ctypes.c_uint32),
                ('gated_off', ctypes.c_uint32), ('info_mismatch', ctypes.c_uint32), ('mh_calls', ctypes.c_uint32),
                ('rolled_back', ctypes.c_uint32), ('retained', ctypes.c_uint32), ('quiesce_retries', ctypes.c_uint32),
                ('threads_probed', ctypes.c_uint32), ('threads_busy', ctypes.c_uint32),
                ('threads_vanished', ctypes.c_uint32), ('reserved', ctypes.c_uint32)]


class SiteInfo(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 16), ('rva', ctypes.c_uint32), ('length', ctypes.c_uint8),
                ('kind', ctypes.c_uint8), ('policy', ctypes.c_uint8), ('flags', ctypes.c_uint8),
                ('subsystem', ctypes.c_uint32), ('gate', ctypes.c_uint16), ('hook_len', ctypes.c_uint8),
                ('tail_len', ctypes.c_uint8), ('stub', ctypes.c_uint64), ('trampoline', ctypes.c_uint64),
                ('gate_open', ctypes.c_uint8), ('reserved', ctypes.c_uint8 * 7)]


class IslandInfo(ctypes.Structure):
    _fields_ = [('is_call_row', ctypes.c_uint32), ('call_offset', ctypes.c_uint32), ('island', ctypes.c_uint64),
                ('entry', ctypes.c_uint64), ('slow', ctypes.c_uint64), ('callee', ctypes.c_uint64),
                ('slot', ctypes.c_uint64), ('common', ctypes.c_uint64), ('slot_address', ctypes.c_uint64)]


def call_offset(row: dict):
    """Offset of the E8 in a call row's window (rel32 rewrite, no MinHook), or None for a MinHook row."""
    e = row['expected']
    if row['policy'] != 'PATCH':
        return None
    if row['kind'] == 'CALL' and row['length'] == 5 and e[0] == 0xE8:
        return 0
    if row['kind'] == 'DETOUR' and row['length'] == 8 and e[:2] == b'\x48\x8B' and (e[2] & 0xF8) == 0xC8 and e[3] == 0xE8:
        return 3
    return None


def written_bytes(row: dict) -> range:
    """RVAs a hooked row changes: MinHook's five-byte jump, or a call row's four rel32 bytes."""
    k = call_offset(row)
    if k is None:
        return range(row['rva'], row['rva'] + 5)
    return range(row['rva'] + k + 1, row['rva'] + k + 5)


class Addresses(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('gate_array', 'tramp_array', 'clock_slot', 'liveness', 'stats0',
                                               'stats_stride', 'stub0', 'passthrough')]


class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_uint64), ('AllocationBase', ctypes.c_uint64),
                ('AllocationProtect', wintypes.DWORD), ('PartitionId', wintypes.WORD), ('_pad', wintypes.WORD),
                ('RegionSize', ctypes.c_uint64), ('State', wintypes.DWORD), ('Protect', wintypes.DWORD),
                ('Type', wintypes.DWORD), ('_pad2', wintypes.DWORD)]


_k32 = ctypes.WinDLL('kernel32', use_last_error=True) if os.name == 'nt' else None
if _k32 is not None:
    _k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    _k32.VirtualQuery.restype = ctypes.c_size_t


def regions(start: int, end: int) -> list[tuple]:
    out, at = [], start
    while at < end:
        info = MBI()
        if not _k32.VirtualQuery(at, ctypes.byref(info), ctypes.sizeof(info)):
            break
        out.append((info.BaseAddress, info.RegionSize, info.State, info.Protect, info.Type))
        at = info.BaseAddress + info.RegionSize
    return out


def near_private(start: int = 0x10000, end: int = TWIN_BASE + TWIN_SIZE + 0x40000000) -> list[tuple]:
    """Committed private regions within MinHook's 1 GiB search range around the twin (its buffers land here)."""
    return [r for r in regions(start, end) if r[2] == 0x1000 and r[4] == 0x20000]


class Twin:
    """The test DLL and the image twin, shared by the test classes (one MinHook instance per process)."""
    _instance = None

    @classmethod
    def get(cls) -> 'Twin':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        exe = Path(GAME) / 'x64' / 'FFXII_TZA.exe'
        self.file = exe.read_bytes()
        pinned = re.search(r'exe-sha256: ([0-9a-f]{64})', SITES_INC.read_text(encoding='utf-8')).group(1)
        if hashlib.sha256(self.file).hexdigest() != pinned:
            raise AssertionError(f'{exe} is not the pinned build (sites.inc exe-sha256 {pinned})')
        self.dll = ctypes.WinDLL(str(built()['test']))
        d = self.dll
        d.ILCT_PatchTwinMap.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
        d.ILCT_PatchTwinMap.restype = ctypes.c_uint64
        d.ILCT_PatchTwinPoke.argtypes = [ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32]
        for name in ('ILCT_PatchPreflight', 'ILCT_PatchInstall'):
            getattr(d, name).argtypes = [ctypes.c_uint64, ctypes.POINTER(Report)]
            getattr(d, name).restype = ctypes.c_int32
        d.ILCT_PatchSiteInfo.argtypes = [ctypes.c_uint32, ctypes.POINTER(SiteInfo)]
        d.ILCT_PatchAddresses.argtypes = [ctypes.POINTER(Addresses)]
        d.ILCT_PatchSetMode.argtypes = [ctypes.c_uint32]
        d.ILCT_PatchInject.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_PatchState.restype = ctypes.c_uint32
        d.ILCT_IslandInfo.argtypes = [ctypes.c_uint32, ctypes.POINTER(IslandInfo)]
        d.ILCT_IslandSetHandler.argtypes = [ctypes.c_uint32, ctypes.c_int32]
        d.ILCT_IslandFakeResult.restype = ctypes.c_uint64
        d.ILCT_IslandFailWrite.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_IslandRegion.restype = ctypes.c_uint64
        self.rows = site_rows()
        self.index = {r['id']: i for i, r in enumerate(self.rows)}
        self.patch = [i for i, r in enumerate(self.rows) if r['policy'] == 'PATCH']
        self.calls = [i for i in self.patch if call_offset(self.rows[i]) is not None]
        self.mh = [i for i in self.patch if call_offset(self.rows[i]) is None]
        self.addresses = Addresses()
        d.ILCT_PatchAddresses(ctypes.byref(self.addresses))
        self.reset()
        self.baseline = self.image()
        self.baseline_protections = regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE)

    def reset(self):
        released = self.dll.ILCT_PatchReleaseAll()
        if released != OK:
            raise AssertionError(f'release_all returned {released}')
        if self.dll.ILCT_PatchTwinMap(self.file, len(self.file)) != TWIN_BASE:
            raise AssertionError('could not map the image twin at 0x120000')

    def image(self) -> bytes:
        return ctypes.string_at(TWIN_BASE, TWIN_SIZE)

    def poke(self, address: int, data: bytes):
        if not self.dll.ILCT_PatchTwinPoke(address, data, len(data)):
            raise AssertionError('poke failed')

    def call(self, name: str, base: int = TWIN_BASE) -> Report:
        report = Report()
        result = getattr(self.dll, name)(base, ctypes.byref(report))
        if result != report.result:
            raise AssertionError(f'{name}: returned {result}, report says {report.result}')
        return report

    def info(self, row: int) -> SiteInfo:
        info = SiteInfo()
        self.dll.ILCT_PatchSiteInfo(row, ctypes.byref(info))
        return info

    def island(self, row: int) -> IslandInfo:
        info = IslandInfo()
        self.dll.ILCT_IslandInfo(row, ctypes.byref(info))
        return info

    def call_returns(self) -> dict:
        """Return address -> callee for every call row (what the original `call` pushes and where it goes)."""
        out = {}
        for row in self.calls:
            r = self.rows[row]
            at = TWIN_BASE + r['rva'] + call_offset(r)
            rel = struct.unpack_from('<i', r['expected'], call_offset(r) + 1)[0]
            out[at + 5] = at + 5 + rel
        return out


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class PatchTwinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()

    def setUp(self):
        self.twin.reset()
        self.assertEqual(self.twin.image(), self.twin.baseline)

    def tearDown(self):
        self.twin.dll.ILCT_PatchInject(0, 0)

    def assert_untouched(self, reference: bytes, private_before: list):
        self.assertEqual(self.twin.image(), reference, 'image bytes changed')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections,
                         'page protections changed')
        self.assertEqual(near_private(), private_before, 'allocations appeared near the image')

    def test_preflight_passes_on_the_pinned_image(self):
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.site, report.patch_rows, report.info_mismatch, report.gated_off,
                          report.mh_calls), (OK, -1, 61, 0, 0, 0))
        self.assertEqual(self.twin.image(), self.twin.baseline)

    def test_wrong_expected_byte_gives_zero_writes(self):
        for sid, offset in (('IN-01', 0), ('MV-05', 4), ('TG-06', 8), ('TG-01', 3), ('TG-01', 8), ('RT-03', 7),
                            ('PT-03', 26)):
            with self.subTest(sid=sid, offset=offset):
                self.twin.reset()
                row = self.twin.index[sid]
                r = self.twin.rows[row]
                address = TWIN_BASE + r['rva'] + offset
                original = ctypes.string_at(address, 1)
                self.twin.poke(address, bytes([original[0] ^ 0x5A]))
                reference, private = self.twin.image(), near_private()
                report = self.twin.call('ILCT_PatchInstall')
                self.assertEqual((report.result, report.site, report.mh_calls, report.hooked, report.state),
                                 (SITE_MISMATCH, row, 0, 0, STATE_IDLE))
                self.assert_untouched(reference, private)

    def test_header_identity_refusals_write_nothing(self):
        private = near_private()
        report = self.twin.call('ILCT_PatchInstall', base=TWIN_BASE + 0x10000)
        self.assertEqual((report.result, report.mh_calls), (BAD_BASE, 0))
        lfanew = struct.unpack_from('<I', self.twin.file, 0x3C)[0]
        for offset, value, expected in ((lfanew + 8, TIMESTAMP + 1, BAD_TIMESTAMP),
                                        (lfanew + 24 + 56, TWIN_SIZE + 0x1000, BAD_SIZE)):
            with self.subTest(expected=expected):
                self.twin.reset()
                self.twin.poke(TWIN_BASE + offset, struct.pack('<I', value))
                reference = self.twin.image()
                report = self.twin.call('ILCT_PatchInstall')
                self.assertEqual((report.result, report.mh_calls, report.state), (expected, 0, STATE_IDLE))
                self.assert_untouched(reference, private)

    def hooked_sites_ok(self, rows):
        for row in rows:
            r, info = self.twin.rows[row], self.twin.info(row)
            site = TWIN_BASE + r['rva']
            k = call_offset(r)
            if k is not None:
                call = site + k
                self.assertEqual(ctypes.string_at(site, k + 1), checked(r)[:k + 1], r['id'])
                island = call + 5 + struct.unpack('<i', ctypes.string_at(call + 1, 4))[0]
                self.assertEqual(island, self.twin.island(row).island, r['id'])
                self.assertEqual(ctypes.string_at(island, 6), b'\xFF\x25\0\0\0\0', r['id'])
                self.assertEqual(struct.unpack('<Q', ctypes.string_at(island + 6, 8))[0], info.stub, r['id'])
                self.assertEqual(info.stub, self.twin.island(row).entry, r['id'])
                self.assertEqual(info.trampoline, self.twin.island(row).callee, r['id'])
                continue
            jump = ctypes.string_at(site, 5)
            self.assertEqual(jump[0], 0xE9, r['id'])
            relay = site + 5 + struct.unpack_from('<i', jump, 1)[0]
            self.assertEqual(ctypes.string_at(relay, 6), b'\xFF\x25\0\0\0\0', r['id'])
            self.assertEqual(struct.unpack('<Q', ctypes.string_at(relay + 6, 8))[0], info.stub, r['id'])
            self.assertLess(abs(relay - site), 0x40000000)
            self.assertNotEqual(info.trampoline, 0)

    def test_install_hooks_every_patch_row_and_release_restores_the_image(self):
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.hooked, report.state, report.gated_off), (OK, 61, STATE_INSTALLED, 0))
        self.assertEqual(len(self.twin.calls), 18)
        self.hooked_sites_ok(self.twin.patch)
        after = self.twin.image()
        written = set()
        for row in self.twin.patch:
            written.update(written_bytes(self.twin.rows[row]))
        diff = [i for i in written if after[i] != self.twin.baseline[i]]
        self.assertTrue(diff)
        import numpy as np  # noqa: PLC0415 (only here: a 50 MB comparison)
        a = np.frombuffer(after, dtype=np.uint8)
        b = np.frombuffer(self.twin.baseline, dtype=np.uint8)
        changed = set(np.nonzero(a != b)[0].tolist())
        self.assertLessEqual(changed, written, 'bytes outside the 43 five-byte jumps and 18 rel32s changed')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, 20)  # ALREADY_INSTALLED
        self.assertEqual(self.twin.dll.ILCT_PatchReleaseAll(), OK)
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)

    def test_failed_create_removes_everything(self):
        private = near_private()
        row = self.twin.patch[30]
        self.assertNotIn(row, self.twin.calls)
        self.twin.dll.ILCT_PatchInject(TEST_FAIL_CREATE, row)
        report = self.twin.call('ILCT_PatchInstall')
        before = len([r for r in self.twin.mh if r < row])  # MinHook creates run first; islands come after them
        self.assertEqual((report.result, report.site, report.state, report.rolled_back, report.retained),
                         (CREATE_FAILED, row, STATE_RETIRED, before, 0))
        self.assert_untouched(self.twin.baseline, private)  # MinHook's buffers were freed again
        again = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((again.result, again.mh_calls), (INSTALL_RETIRED, 0))

    def check_enabled_rollback(self, stage: int, row: int, expected: int):
        self.twin.dll.ILCT_PatchInject(stage, row)
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.state, report.rolled_back, report.retained),
                         (expected, STATE_RETIRED, 61, 61))
        self.assertEqual(self.twin.image(), self.twin.baseline, 'rollback did not restore the image')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        callees = {row: self.twin.call_returns()[TWIN_BASE + self.twin.rows[row]['rva'] +
                                                 call_offset(self.twin.rows[row]) + 5] for row in self.twin.calls}
        for patch_row in self.twin.patch:
            info = self.twin.info(patch_row)
            if patch_row in self.twin.calls:
                island = self.twin.island(patch_row)
                callee = callees[patch_row]
                self.assertEqual(info.trampoline, callee, 'a retired call row continues at its callee')
                if island.island:  # islands kept (a rel32 was written): stragglers go straight to the callee
                    self.assertEqual(island.slot, callee)
            else:
                self.assertEqual(info.trampoline, TWIN_BASE + self.twin.rows[patch_row]['rva'],
                                 'a retired stub must continue at its restored site')
            self.assertEqual(info.gate_open, 0)
        return report

    def test_failed_apply_rolls_back_byte_identical(self):
        report = self.check_enabled_rollback(TEST_FAIL_APPLY, 0, APPLY_FAILED)
        self.assertEqual(report.site, -1)

    def test_failed_verify_rolls_back_byte_identical(self):
        last = self.twin.patch[-1]
        report = self.check_enabled_rollback(TEST_FAIL_VERIFY, last, VERIFY_FAILED)
        self.assertEqual(report.site, last)

    def test_gate_mismatch_skips_only_the_gated_row(self):
        gate, gated = self.twin.index['IN-06a'], self.twin.index['IN-06']
        address = TWIN_BASE + self.twin.rows[gate]['rva']
        self.twin.poke(address, b'\x90')
        reference = self.twin.image()
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.hooked, report.gated_off), (OK, 60, 1))
        gated_site = TWIN_BASE + self.twin.rows[gated]['rva']
        self.assertEqual(ctypes.string_at(gated_site, 6), self.twin.rows[gated]['expected'])
        self.assertEqual(self.twin.info(gated).trampoline, 0)
        self.hooked_sites_ok([row for row in self.twin.patch if row != gated])
        self.assertEqual(self.twin.dll.ILCT_PatchReleaseAll(), OK)
        self.assertEqual(self.twin.image(), reference)

    def test_info_mismatch_is_counted_not_refused(self):
        self.twin.poke(TWIN_BASE + self.twin.rows[self.twin.index['CL-01']]['rva'], b'\xCC')
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.info_mismatch, report.hooked), (OK, 1, 61))

    def test_arm_flags_open_exactly_the_expected_gates(self):
        d, rows = self.twin.dll, self.twin.rows
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, OK)

        def gates():
            return {self.twin.rows[r]['id'] for r in self.twin.patch if self.twin.info(r).gate_open}

        all_ids = {rows[r]['id'] for r in self.twin.patch}
        self.assertEqual(gates(), set())
        d.ILCT_PatchSetMode(1)  # LIVE (the layer starts DRY: see the dry-arm tests)
        for r in self.twin.patch:
            d.ILCT_PatchSetPassthrough(r, 1)
            d.ILCT_PatchSetSiteArmed(r, 1)
        self.assertEqual(gates(), set(), 'gates open before the global arm')
        d.ILCT_PatchSetArmed(1)
        self.assertEqual(gates(), all_ids)
        mask = 0xFF & ~SUBSYS['MOVEMENT']
        d.ILCT_PatchApplySubsystems(mask)
        self.assertEqual(gates(), {rows[r]['id'] for r in self.twin.patch if rows[r]['subsystem'] != 'MOVEMENT'})
        d.ILCT_PatchApplySubsystems(0)
        self.assertEqual(gates(), {rows[r]['id'] for r in self.twin.patch if rows[r]['subsystem'] == 'CORE'})
        self.assertEqual(gates(), {'UT-01'})
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetSiteArmed(self.twin.index['FW-01'], 0)
        d.ILCT_PatchSetPassthrough(self.twin.index['CM-01'], 0)
        self.assertEqual(gates(), all_ids - {'FW-01', 'CM-01'})
        self.assertEqual(d.ILCT_PatchSetSiteArmed(self.twin.index['RT-01'], 1), 0, 'non-PATCH row accepted')
        d.ILCT_PatchSetArmed(0)
        self.assertEqual(gates(), set())


# ---- unicorn equivalence ---------------------------------------------------------------------------------------
GPRS = ['RAX', 'RCX', 'RDX', 'RBX', 'RSP', 'RBP', 'RSI', 'RDI', 'R8', 'R9', 'R10', 'R11', 'R12', 'R13', 'R14', 'R15']
STACK, STACK_SIZE = 0x10_0000_0000, 0x100000
SCRATCH, SCRATCH_SIZE = 0x20_0000_0000, 0x1000000
CLOCK_PAGE = 0x30_0000_0000
FLAG_MASK = 0xCD5  # CF PF AF ZF SF DF OF
PAGE = 0x1000


class Emulator:
    """One unicorn instance holding the hooked twin, the test DLL and MinHook's buffers, copied from this process."""

    budget = 20000  # instructions per emu_start before a run counts as "never left"

    def __init__(self, twin: Twin):
        self.twin = twin
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        self.uc = uc
        uc.mem_map(TWIN_BASE, TWIN_SIZE)
        uc.mem_write(TWIN_BASE, twin.image())
        handle = twin.dll._handle
        lfanew = struct.unpack('<I', ctypes.string_at(handle + 0x3C, 4))[0]
        size = struct.unpack('<I', ctypes.string_at(handle + lfanew + 24 + 56, 4))[0]
        self.dll = (handle, handle + size)
        uc.mem_map(handle, (size + PAGE - 1) & ~(PAGE - 1))
        for base, length, state, protect, _ in regions(handle, handle + size):
            if state == 0x1000 and not protect & 0x101:  # committed, not NOACCESS/GUARD
                uc.mem_write(base, ctypes.string_at(base, min(length, handle + size - base)))
        pages = set()
        self.call_returns = twin.call_returns()
        for row in twin.patch:
            info = twin.info(row)
            site = TWIN_BASE + twin.rows[row]['rva']
            if row in twin.calls:
                island = twin.island(row).island
                if island:
                    pages.add(island & ~(PAGE - 1))
                continue
            relay = site + 5 + struct.unpack('<i', ctypes.string_at(site + 1, 4))[0]
            for address in (info.trampoline, relay, relay + 13, info.trampoline + 63):
                pages.add(address & ~(PAGE - 1))
        self.buffers = sorted(pages)
        for page in self.buffers:
            uc.mem_map(page, PAGE)
            uc.mem_write(page, ctypes.string_at(page, PAGE))
        uc.mem_map(STACK, STACK_SIZE)
        uc.mem_map(SCRATCH, SCRATCH_SIZE)
        uc.mem_write(SCRATCH, random.Random(1208).randbytes(SCRATCH_SIZE))
        uc.mem_map(CLOCK_PAGE, PAGE)
        uc.mem_write(twin.addresses.clock_slot, struct.pack('<Q', CLOCK_PAGE))
        self.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.lazy = set()
        uc.hook_add(unicorn.UC_HOOK_CODE, self._code)
        uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, self._write)
        uc.hook_add(unicorn.UC_HOOK_MEM_READ_UNMAPPED | unicorn.UC_HOOK_MEM_WRITE_UNMAPPED, self._unmapped)

    # hooks
    def _code(self, uc, address, size, _):
        if not self.allowed(address):
            self.exit = address
            uc.emu_stop()
            return
        self.steps += 1
        self.last = (address, size)

    def _write(self, uc, _access, address, size, _value, _):
        self.undo.append((address, bytes(uc.mem_read(address, size))))
        self.written.update(range(address, address + size))

    def _unmapped(self, uc, _access, address, _size, _value, _):
        page = address & ~(PAGE - 1)
        if page >= 1 << 47 or page in self.lazy:
            return False
        uc.mem_map(page, PAGE)
        self.lazy.add(page)
        return True

    def write_code(self, address: int, data: bytes):
        """Write code and drop unicorn's translated blocks, which would otherwise keep running the old bytes."""
        self.uc.mem_write(address, data)
        self.uc.ctl_flush_tb()

    def force_gate(self, row: int, open_: bool):
        """Open or close a row's gate in the emulator only: the gate byte, and for a call row also the slot its
        entry jumps through (slow path when open, the callee when closed), as recompute_gates would."""
        self.uc.mem_write(self.twin.addresses.gate_array + row, b'\1' if open_ else b'\0')
        if row in self.twin.calls:
            island = self.twin.island(row)
            self.uc.mem_write(island.slot_address, struct.pack('<Q', island.slow if open_ else island.callee))

    def set_clock(self, value: int):
        self.write_code(CLOCK_PAGE, b'\x48\xB8' + struct.pack('<Q', value) + b'\xC3')  # mov rax, value; ret

    def run(self, start: int, allowed, state: dict, probe=None, on_callee=None) -> dict:
        """Run from `state` until execution leaves `allowed`; callees are returned from at once. Memory is put back
        afterwards; `probe(uc)` reads anything else before that."""
        uc = self.uc
        for name, value in state['gpr'].items():
            uc.reg_write(getattr(X, f'UC_X86_REG_{name}'), value)
        for k, value in enumerate(state['xmm']):
            uc.reg_write(getattr(X, f'UC_X86_REG_XMM{k}'), value)
        uc.reg_write(X.UC_X86_REG_RFLAGS, state['rflags'])
        self.allowed, self.undo, self.written, self.calls, self.steps = allowed, [], set(), [], 0
        rip = start
        while True:
            self.exit, self.last = None, None
            uc.emu_start(rip, 0, count=self.budget)
            if self.exit is None:
                raise AssertionError(f'did not leave the hooked code (RIP 0x{uc.reg_read(X.UC_X86_REG_RIP):X})')
            if uc.reg_read(X.UC_X86_REG_RIP) != self.exit:
                raise AssertionError('emu_stop executed past the exit')
            insn = next(self.cs.disasm(bytes(uc.mem_read(self.last[0], self.last[1])), self.last[0])) \
                if self.last else None
            rsp = uc.reg_read(X.UC_X86_REG_RSP)
            top = struct.unpack('<Q', uc.mem_read(rsp, 8))[0]
            if (insn is None or insn.mnemonic != 'call') and self.call_returns.get(top) == self.exit:
                # A call row's stub tail-jumped into the callee with the game's own return address on the stack:
                # the same callee, entered exactly as the original `call` enters it.
                self.calls.append(self.exit)
                if on_callee:
                    on_callee(uc, self.exit)
                uc.reg_write(X.UC_X86_REG_RSP, rsp + 8)
                rip = top
                continue
            if insn is not None and insn.mnemonic == 'call':
                back = struct.unpack('<Q', uc.mem_read(rsp, 8))[0]
                if back != insn.address + insn.size:
                    raise AssertionError(f'call at 0x{insn.address:X} pushed 0x{back:X}')
                self.calls.append(self.exit)  # the callee is game code: return from it at once
                if on_callee:
                    on_callee(uc, self.exit)
                uc.reg_write(X.UC_X86_REG_RSP, rsp + 8)
                rip = back
                continue
            break
        result = {'exit': self.exit, 'calls': list(self.calls), 'steps': self.steps,
                  'gpr': {n: uc.reg_read(getattr(X, f'UC_X86_REG_{n}')) for n in GPRS},
                  'xmm': [uc.reg_read(getattr(X, f'UC_X86_REG_XMM{k}')) for k in range(16)],
                  'rflags': uc.reg_read(X.UC_X86_REG_RFLAGS) & FLAG_MASK,
                  'final': {a: uc.mem_read(a, 1)[0] for a in self.written},
                  'probe': probe(uc) if probe else None}
        for address, old in reversed(self.undo):
            uc.mem_write(address, old)
        return result

    def stats(self, row: int):
        a = self.twin.addresses
        return lambda uc: struct.unpack('<3Q', uc.mem_read(a.stats0 + row * a.stats_stride, 24))


def random_state(rng: random.Random) -> dict:
    gpr = {}
    for name in GPRS:
        pick = rng.random()
        if name == 'RSP':
            gpr[name] = STACK + 0x80000 + rng.randrange(0, 0x800) * 8
        elif pick < 0.6:
            gpr[name] = SCRATCH + 0x100000 + rng.randrange(0, SCRATCH_SIZE - 0x200000)
        elif pick < 0.8:
            gpr[name] = rng.randrange(0, 0x20)
        else:
            gpr[name] = 0
    flags = 0x202 | sum(bit for bit in (0x1, 0x4, 0x10, 0x40, 0x80, 0x800) if rng.random() < 0.5)
    return {'gpr': gpr, 'xmm': [rng.getrandbits(128) for _ in range(16)], 'rflags': flags}


@unittest.skipUnless(NATIVE and GAME and unicorn is not None and capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class TrampolineEquivalenceTest(unittest.TestCase):
    TRIALS = 24

    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()
        cls.twin.reset()
        report = cls.twin.call('ILCT_PatchInstall')
        if report.result != OK:
            raise AssertionError(f'install failed: {report.result} at row {report.site}')
        for row in cls.twin.patch:
            cls.twin.dll.ILCT_PatchSetPassthrough(row, 1)  # gates stay closed: nothing is armed in-process
        cls.twin.dll.ILCT_PatchSetMode(1)  # LIVE, so a gate forced open in the emulator reaches the handler
        cls.emu = Emulator(cls.twin)

    @classmethod
    def tearDownClass(cls):
        cls.twin.reset()

    def compare(self, row: int, a: dict, b: dict, baseline: dict, entry_rsp: int):
        sid = self.twin.rows[row]['id']
        self.assertEqual(a['exit'], b['exit'], f'{sid}: continuation')
        self.assertEqual(a['calls'], b['calls'], f'{sid}: callees')
        self.assertEqual(a['gpr'], b['gpr'], f'{sid}: general registers')
        self.assertEqual(a['xmm'], b['xmm'], f'{sid}: XMM registers')
        self.assertEqual(a['rflags'], b['rflags'], f'{sid}: flags')
        rsp = a['gpr']['RSP']
        lo, hi = self.emu.dll
        diffs = []
        for address in set(a['final']) | set(b['final']):
            if lo <= address < hi:
                continue  # our module's own bookkeeping (counters)
            if STACK <= address < rsp:
                continue  # below the final RSP: dead (Windows x64 has no red zone)
            if STACK <= address < entry_rsp and address not in a['final']:
                continue  # dead at entry and never stored by the original code: its value is undefined
            va, vb = a['final'].get(address, baseline[address]), b['final'].get(address, baseline[address])
            if va != vb:
                diffs.append(hex(address))
        self.assertEqual(diffs, [], f'{sid}: memory')

    def test_every_patch_row_is_equivalent_in_all_three_modes(self):
        twin, emu, uc = self.twin, self.emu, self.emu.uc
        a_live = twin.addresses.liveness
        results, branchy = {}, []
        for row in twin.patch:
            r = twin.rows[row]
            site = TWIN_BASE + r['rva']
            window = (site, site + r['hook_len'])
            patched = bytes(uc.mem_read(site, r['hook_len']))
            original = checked(r)[:r['hook_len']]
            rng = random.Random(0xB08 * 1000 + row)
            modes = {'closed': 0, 'stale': 0, 'handler': 0}
            conditional = any(i.mnemonic.startswith('j') and i.mnemonic != 'jmp'
                              for i in emu.cs.disasm(original, site))
            exits = set()
            for trial in range(self.TRIALS):
                state = random_state(rng)
                emu.write_code(site, original)
                a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                            window[0] <= x < window[1], state)
                exits.add(a['exit'])
                emu.write_code(site, patched)
                for mode in modes:
                    if mode == 'closed':
                        emu.force_gate(row, False)
                    else:
                        emu.force_gate(row, True)
                        uc.mem_write(a_live, struct.pack('<IIQ', 1, 7, 1_000_000))
                        emu.set_clock(1_000_000 + (2001 if mode == 'stale' else 5))
                    before = emu.stats(row)(uc)
                    b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                                site <= x < site + 5, state, probe=emu.stats(row))
                    after = b['probe']
                    emu.force_gate(row, False)
                    uc.mem_write(a_live, struct.pack('<IIQ', 0, 0, 0))
                    touched = set(a['final']) | set(b['final'])
                    baseline = {address: uc.mem_read(address, 1)[0] for address in touched}
                    with self.subTest(site=r['id'], trial=trial, mode=mode):
                        self.compare(row, a, b, baseline, state['gpr']['RSP'])
                        expected = {'closed': (0, 0, 0), 'stale': (1, 1, 0), 'handler': (1, 0, 1)}[mode]
                        self.assertEqual(tuple(x - y for x, y in zip(after, before)), expected,
                                         f'{r["id"]} {mode}: slow-path counters')
                        modes[mode] += 1
            if conditional:
                self.assertEqual(len(exits), 2, f'{r["id"]}: the random states did not take both branch directions')
                branchy.append(r['id'])
            results[r['id']] = modes
        passed = [sid for sid, modes in results.items() if all(v == self.TRIALS for v in modes.values())]
        print(f'\ntrampoline equivalence: {len(passed)}/{len(twin.patch)} PATCH rows PASS '
              f'({self.TRIALS} random states x 3 modes each; both directions taken at {", ".join(branchy)})')
        self.assertEqual(len(passed), len(twin.patch))

    def test_instrument_detects_a_wrong_trampoline(self):
        """Control: corrupt one byte of a trampoline copy in the emulator only; equivalence must fail."""
        twin, emu, uc = self.twin, self.emu, self.emu.uc
        row = twin.index['FW-01']  # lea eax,[rdx-0xB]; cmp eax,0x10
        info = twin.info(row)
        r = twin.rows[row]
        site = TWIN_BASE + r['rva']
        saved = bytes(uc.mem_read(info.trampoline, 6))
        emu.write_code(info.trampoline + 2, bytes([saved[2] ^ 0x01]))  # lea eax,[rdx-0xB] -> different displacement
        try:
            state = random_state(random.Random(7))
            patched = bytes(uc.mem_read(site, 5))
            emu.write_code(site, checked(r)[:r['hook_len']])
            a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 6, state)
            emu.write_code(site, patched)
            b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5, state)
            self.assertNotEqual(a['gpr']['RAX'], b['gpr']['RAX'])
        finally:
            emu.write_code(info.trampoline, saved)


if __name__ == '__main__':
    unittest.main()
