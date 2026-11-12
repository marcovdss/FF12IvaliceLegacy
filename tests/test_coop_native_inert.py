"""Host contracts for the inert co-op module native/coop (no game run, nothing deployed).

* build: the pinned toolchain builds the DLL, the PE audit passes, and two builds are byte-identical;
* audit: probe DLLs that import ws2_32 or name winhttp.dll are rejected;
* ctypes: the real DLL is loaded into this process and every export is called (FF12HgetVer must return the RCX
  pointer; ILC_Bridge is also called the way memory.execute calls it: 8 slots, full 64-bit values);
* static: DllMain (found through the build's map file; the image entry is the CRT's DllMainCRTStartup) contains no
  call or jump (capstone); the lifecycle exports only forward to the boot (src/boot/boot.cpp);
* host: in a process that is not the pinned game build, FF12Hinit leaves the module inert (UNSUPPORTED_BUILD) and
  starts no thread; FF12HinitDone and FF12Hcleanup change nothing. The boot matrix is test_coop_boot.py;
* emulation: FF12HgetVer and FF12HgetName run under unicorn with a hidden-pointer destination in RCX. The same
  harness runs the installed EFL modules' FF12HgetVer as a control when IVALICE_LEGACY_GAME names the game
  folder (read-only).

Needs the MSYS2 UCRT64 g++ named by IVALICE_LEGACY_CXX (default C:/msys64/ucrt64/bin/g++.exe) and a 64-bit
Windows Python; unicorn for the emulation class. ILC_REQUIRE_NATIVE=1 turns the missing-g++ skips into failures.
The bridge ABI itself is tested in test_coop_bridge_abi.py.
"""
from pathlib import Path
import contextlib
import ctypes
import faulthandler
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
sys.path.insert(0, str(COOP))

import audit_pe  # noqa: E402
import build as coop_build  # noqa: E402

try:
    import unicorn
    from unicorn import x86_const
except ImportError:
    unicorn = None

try:
    import capstone
except ImportError:
    capstone = None

COMPILER = Path(os.environ.get('IVALICE_LEGACY_CXX', 'C:/msys64/ucrt64/bin/g++.exe'))
CAN_BUILD = os.name == 'nt' and struct.calcsize('P') == 8 and COMPILER.is_file()
GAME = os.environ.get('IVALICE_LEGACY_GAME')
REQUIRE_NATIVE = os.environ.get('ILC_REQUIRE_NATIVE') == '1'
NATIVE = CAN_BUILD or REQUIRE_NATIVE  # with ILC_REQUIRE_NATIVE=1 the native tests run, and fail without g++

ILC_ABI_MAGIC = 0x42434C49
ILC_ABI_VERSION = 1
UNKNOWN_OP, NOT_IMPLEMENTED, BAD_ARGS, BAD_HEADER, ABI_MISMATCH, OUT_TOO_SMALL, TOO_LARGE, BAD_LENGTH = range(-1, -9, -1)
HELLO_SIZE = 96  # also the success return value: bytes written
VERSION = (0, 0, 2)
EFL_EXPORTS = ['FF12HgetVer', 'FF12HgetName', 'FF12Hinit', 'FF12HinitDone', 'FF12Hcleanup']
CANARY = 0xA5

_built = {}


def built_dll() -> Path:
    """Build once per test run into a private temporary folder."""
    if not CAN_BUILD:
        raise AssertionError(f'native toolchain unavailable: {COMPILER} (ILC_REQUIRE_NATIVE=1)')
    if 'dll' not in _built:
        temp = Path(tempfile.mkdtemp(prefix='ilc-test-'))
        _built['manifest'] = coop_build.build(COMPILER.resolve(), temp)
        _built['dll'] = temp / coop_build.DLL_NAME
    return _built['dll']


def header(magic=ILC_ABI_MAGIC, abi=ILC_ABI_VERSION, size=16, generation=0, record_type=1) -> bytes:
    return struct.pack('<IHHII', magic, abi, record_type, size, generation)


@unittest.skipUnless(NATIVE, 'requires 64-bit Windows Python and the pinned MSYS2 g++')
class BuildAndAuditTest(unittest.TestCase):
    def test_build_passes_audit_and_records_toolchain(self):
        built_dll()
        manifest = _built['manifest']
        self.assertTrue(manifest['audit']['ok'], manifest['audit']['failures'])
        self.assertEqual(manifest['toolchain']['compiler'], coop_build.EXPECTED_COMPILER)
        self.assertEqual(manifest['toolchain']['linker'], coop_build.EXPECTED_LINKER)
        allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))
        self.assertEqual({dll: sorted(funcs) for dll, funcs in manifest['audit']['imports'].items()},
                         {dll: sorted(funcs) for dll, funcs in allow['imports'].items()},
                         'imports must equal the reviewed allow-list exactly')
        self.assertEqual(sorted(manifest['toolchain']['packages']), sorted(coop_build.EXPECTED_PACKAGES))
        self.assertEqual(sorted(e['name'] for e in manifest['audit']['exports']), sorted(EFL_EXPORTS + ['ILC_Bridge']))
        for value in manifest['command']:
            self.assertFalse(os.path.isabs(value), f'absolute path in recorded command: {value}')

    def test_two_builds_are_byte_identical(self):
        first = built_dll().read_bytes()
        with tempfile.TemporaryDirectory(prefix='ilc-test-') as temp:
            second = coop_build.build(COMPILER.resolve(), Path(temp))
        self.assertEqual(second['dllSha256'], _built['manifest']['dllSha256'])
        self.assertEqual(len(first), second['dllSize'])

    def _probe(self, source: str, *libs: str) -> dict:
        with tempfile.TemporaryDirectory(prefix='ilc-probe-') as temp:
            (Path(temp) / 'probe.c').write_text(source)
            env = dict(os.environ, PATH=str(COMPILER.parent) + os.pathsep + os.environ.get('PATH', ''))
            subprocess.run([str(COMPILER.parent / 'gcc.exe'), '-shared', '-O2', 'probe.c', '-o', 'probe.dll', *libs],
                           check=True, cwd=temp, env=env)
            data = (Path(temp) / 'probe.dll').read_bytes()
        allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))
        return audit_pe.audit(data, allow)

    def test_audit_rejects_a_network_import(self):
        result = self._probe('#include <winsock2.h>\n'
                             '__declspec(dllexport) int probe(void){WSADATA d;return WSAStartup(0x202,&d);}\n',
                             '-lws2_32')
        self.assertFalse(result['ok'])
        joined = '\n'.join(result['failures'])
        self.assertIn('denied DLL WS2_32.dll', joined)
        self.assertIn('denied function WS2_32.dll!WSAStartup', joined)
        self.assertIn('exports differ', joined)

    def test_audit_rejects_a_network_dll_named_for_loadlibrary(self):
        result = self._probe('#include <windows.h>\n'
                             '__declspec(dllexport) void* probe(void){return LoadLibraryA("winhttp.dll");}\n')
        self.assertFalse(result['ok'])
        self.assertTrue(any('names denied DLLs as strings' in f and 'winhttp.dll' in f
                            for f in result['failures']), result['failures'])

    def test_audit_rejects_a_network_dll_named_without_extension(self):
        result = self._probe('#include <windows.h>\n'
                             '__declspec(dllexport) void* probe(void){return LoadLibraryA("WS2_32");}\n')
        self.assertTrue(any('without extension' in f and 'ws2_32' in f for f in result['failures']),
                        result['failures'])

    def test_audit_rejects_a_denied_function_name_string(self):
        result = self._probe('#include <windows.h>\n'
                             '__declspec(dllexport) void* probe(HMODULE m){return (void*)GetProcAddress(m, "WSAStartup");}\n'
                             '__declspec(dllexport) void* probe2(HMODULE m){return (void*)GetProcAddress(m, "connect");}\n')
        found = [f for f in result['failures'] if 'denied function names as strings' in f]
        self.assertTrue(found and 'WSAStartup' in found[0] and 'connect' in found[0], result['failures'])

    def test_audit_rejects_an_ordinal_only_export(self):
        with tempfile.TemporaryDirectory(prefix='ilc-probe-') as temp:
            (Path(temp) / 'probe.c').write_text('int a(void){return 1;}\nint b(void){return 2;}\n')
            (Path(temp) / 'probe.def').write_text('EXPORTS\n    a @1\n    b @2 NONAME\n')
            env = dict(os.environ, PATH=str(COMPILER.parent) + os.pathsep + os.environ.get('PATH', ''))
            subprocess.run([str(COMPILER.parent / 'gcc.exe'), '-shared', '-O2', 'probe.c', 'probe.def', '-o',
                            'probe.dll'], check=True, cwd=temp, env=env)
            data = (Path(temp) / 'probe.dll').read_bytes()
        allow = json.loads((COOP / 'pe-allowlist.json').read_text(encoding='utf-8'))
        result = audit_pe.audit(data, allow)
        self.assertTrue(any('ordinal-only exports' in f for f in result['failures']), result['failures'])

    def test_audit_rejects_thread_local_storage(self):
        # S3: thread_local is forbidden. g++ 16.2 compiles it to native TLS (a .tls template and gs:[0x58] reads);
        # older mingw-w64 toolchains used emutls, which pulls in winpthreads. The audit catches either.
        result = self._probe('__thread int counter;\n'
                             '__declspec(dllexport) int probe(void){return ++counter;}\n')
        self.assertFalse(result['ok'])
        self.assertTrue(any(('marker strings' in f and '__emutls' in f) or 'winpthread' in f.lower()
                            or 'TLS template' in f or 'TLS array' in f for f in result['failures']), result['failures'])


class EflVersion(ctypes.Structure):
    _fields_ = [('major', ctypes.c_uint32), ('minor', ctypes.c_uint32), ('patch', ctypes.c_uint32)]


@unittest.skipUnless(NATIVE, 'requires 64-bit Windows Python and the pinned MSYS2 g++')
def _thread_count() -> int:
    """Native threads of this process (Toolhelp)."""
    k32 = ctypes.WinDLL('kernel32')
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Thread32First.argtypes = k32.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]

    class Entry(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('usage', ctypes.c_uint32), ('tid', ctypes.c_uint32),
                    ('pid', ctypes.c_uint32), ('base', ctypes.c_long), ('delta', ctypes.c_long),
                    ('flags', ctypes.c_uint32)]
    snap = k32.CreateToolhelp32Snapshot(4, 0)
    e = Entry()
    e.size = ctypes.sizeof(e)
    n, ok = 0, k32.Thread32First(snap, ctypes.byref(e))
    while ok:
        n += e.pid == os.getpid()
        ok = k32.Thread32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return n


class CtypesHostTest(unittest.TestCase):
    expected_reason = 0  # NONE: the shared copy never gets FF12Hinit
    @classmethod
    def setUpClass(cls):
        cls.dll = ctypes.WinDLL(str(built_dll()))
        cls.bridge = cls.dll.ILC_Bridge
        cls.bridge.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64]
        cls.bridge.restype = ctypes.c_int32

    def call(self, op, inbound=None, in_len=None, out_cap=HELLO_SIZE, out_size=None):
        out = (ctypes.c_ubyte * (out_size if out_size is not None else max(out_cap, 1) + 16))()
        ctypes.memset(out, CANARY, len(out))
        in_buf = ctypes.create_string_buffer(inbound, len(inbound)) if inbound is not None else None
        length = in_len if in_len is not None else (len(inbound) if inbound is not None else 0)
        status = self.bridge(op, in_buf, length, out, out_cap)
        return status, bytes(out)

    def assert_untouched(self, out: bytes):
        self.assertEqual(out, bytes([CANARY]) * len(out))

    def check_hello(self, out: bytes):
        fields = struct.unpack_from('<IHHII12I2Q2Q', out)
        magic, abi, record_type, size, generation = fields[:5]
        major, minor, patch, state, reason, flags, tid, settings_size, accepted, applied, ops, reserved = fields[5:17]
        self.assertEqual((magic, abi, record_type, size, generation),
                         (ILC_ABI_MAGIC, ILC_ABI_VERSION, 2, HELLO_SIZE, 0))
        self.assertEqual((major, minor, patch), VERSION)
        self.assertEqual((state, reason, flags, reserved), (1, self.expected_reason, 0, 0))
        self.assertEqual((settings_size, accepted, applied), (3492, 0, 0))
        self.assertEqual(tid, threading.get_native_id())
        source_id = struct.pack('>2Q', *fields[17:19]).hex()
        self.assertEqual(source_id, _built['manifest']['sourceId'][:32])
        self.assert_untouched(out[HELLO_SIZE:])

    def test_get_name(self):
        get_name = self.dll.FF12HgetName
        get_name.argtypes = []
        get_name.restype = ctypes.c_char_p
        self.assertEqual(get_name(), b'Ivalice Legacy Co-op')

    def test_get_ver_as_struct_return(self):
        get_ver = self.dll.FF12HgetVer
        get_ver.argtypes = []
        get_ver.restype = EflVersion
        version = get_ver()
        self.assertEqual((version.major, version.minor, version.patch), VERSION)

    def test_get_ver_returns_the_rcx_pointer(self):
        # The loader reads the 12 version bytes at RAX, so RAX must be the hidden destination passed in RCX.
        # Call it as `void* f(void* rcx)` at several destinations, aligned and not, with guard bytes around.
        address = ctypes.cast(self.dll.FF12HgetVer, ctypes.c_void_p).value
        explicit = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(address)
        raw = (ctypes.c_ubyte * 96)()
        for offset in (16, 17, 19, 24, 61):
            with self.subTest(offset=offset):
                ctypes.memset(raw, CANARY, len(raw))
                destination = ctypes.addressof(raw) + offset
                self.assertEqual(explicit(destination), destination)
                data = bytes(raw)
                self.assertEqual(data[offset:offset + 12], struct.pack('<3I', *VERSION))
                self.assert_untouched(data[:offset])
                self.assert_untouched(data[offset + 12:])

    def test_lifecycle_in_a_foreign_process_stays_inert(self):
        # This process is not FFXII_TZA.exe at 0x120000: FF12Hinit refuses at the host probe (UNSUPPORTED_BUILD,
        # detail 1 = base), creates nothing and starts no thread. A private copy keeps the shared one untouched.
        source = built_dll()
        copy = source.parent / 'ilc-inert-lifecycle.dll'
        shutil.copyfile(source, copy)
        dll = ctypes.WinDLL(str(copy))
        before = threading.active_count(), _thread_count()
        for name in ('FF12Hinit', 'FF12HinitDone', 'FF12Hcleanup'):
            function = getattr(dll, name)
            function.argtypes = []
            function.restype = None
            function()
            function()
        bridge = dll.ILC_Bridge
        bridge.argtypes = self.bridge.argtypes
        bridge.restype = ctypes.c_int32
        out = (ctypes.c_ubyte * (HELLO_SIZE + 16))()
        ctypes.memset(out, CANARY, len(out))
        self.assertEqual(bridge(1, None, 0, out, HELLO_SIZE), HELLO_SIZE)
        try:
            self.expected_reason = 1  # UNSUPPORTED_BUILD
            self.check_hello(bytes(out))
        finally:
            del self.expected_reason
        self.assertEqual((threading.active_count(), _thread_count()), before)

    def test_hello_without_inbound_record(self):
        status, out = self.call(1)
        self.assertEqual(status, HELLO_SIZE)
        self.check_hello(out)

    def test_hello_with_header(self):
        status, out = self.call(1, header())
        self.assertEqual(status, HELLO_SIZE)
        self.check_hello(out)

    def test_hello_from_another_abi_gets_our_reply(self):
        status, out = self.call(1, header(abi=2))
        self.assertEqual(status, HELLO_SIZE)
        self.check_hello(out)  # reply carries ABI 1; the caller decides

    def test_hello_rejects_bad_headers_without_writing(self):
        cases = {'short': (header()[:15], BAD_LENGTH), 'long': (header() + bytes(1), BAD_LENGTH),
                 'magic': (header(magic=0x12345678), BAD_HEADER), 'size below inLen': (header(size=15), BAD_LENGTH),
                 'size beyond inLen': (header(size=17), BAD_LENGTH)}
        for name, (record, code) in cases.items():
            with self.subTest(name):
                status, out = self.call(1, record)
                self.assertEqual(status, code)
                self.assert_untouched(out)

    def test_argument_errors_write_nothing(self):
        status, out = self.call(1, out_cap=HELLO_SIZE - 1)
        self.assertEqual(status, OUT_TOO_SMALL)
        self.assert_untouched(out)
        self.assertEqual(self.bridge(1, None, 0, None, HELLO_SIZE), BAD_ARGS)
        self.assertEqual(self.bridge(1, None, 16, None, HELLO_SIZE), BAD_ARGS)
        status, out = self.call(1, None, in_len=16)
        self.assertEqual(status, BAD_ARGS)
        self.assert_untouched(out)
        # inLen above the cap is refused before the (null) buffer would be read.
        status, out = self.call(1, None, in_len=65537)
        self.assertEqual(status, TOO_LARGE)
        self.assert_untouched(out)

    def test_unknown_ops_write_nothing(self):
        for op in (0, 9, 0x7F00, 0xFFFFFFFF):
            status, out = self.call(op)
            self.assertEqual(status, UNKNOWN_OP)
            self.assert_untouched(out)

    def test_unaligned_output_buffer(self):
        raw = (ctypes.c_ubyte * (HELLO_SIZE + 32))()
        ctypes.memset(raw, CANARY, len(raw))
        status = self.bridge(1, None, 0, ctypes.addressof(raw) + 3, HELLO_SIZE)
        self.assertEqual(status, HELLO_SIZE)
        data = bytes(raw)
        self.assert_untouched(data[:3])
        self.check_hello(data[3:])

    def test_extra_slots_match_a_clean_call_and_dirty_upper_halves_are_refused(self):
        # memory.execute passes the full 64-bit Lua value and always fills 8 slots (slots 6-8 on the stack).
        # Fail closed: a non-zero upper half in op, inLen or outCap is BAD_ARGS and writes nothing;
        # garbage in slots 6-8 is ignored.
        address = ctypes.cast(self.bridge, ctypes.c_void_p).value
        u64, ptr = ctypes.c_uint64, ctypes.c_void_p
        eight = ctypes.CFUNCTYPE(ctypes.c_int32, u64, ptr, u64, ptr, u64, u64, u64, u64)(address)
        junk = (0xDEADBEEF_CAFEF00D, 0x5A5A5A5A_5A5A5A5A, 0xFFFFFFFF_FFFFFFFF)
        cases = [(1, b'', HELLO_SIZE), (1, header(), HELLO_SIZE), (1, header(magic=0), BAD_HEADER),
                 (5, b"", 96), (9, b'', UNKNOWN_OP)]
        for op, record, expected in cases:
            in_buf = ctypes.create_string_buffer(record, len(record)) if record else None
            with self.subTest(op=op, record=record.hex(), upper='clean'):
                clean_status, clean_out = self.call(op, record or None, out_cap=HELLO_SIZE)
                out = (ctypes.c_ubyte * (HELLO_SIZE + 16))()
                ctypes.memset(out, CANARY, len(out))
                status = eight(op, in_buf, len(record), ctypes.addressof(out), HELLO_SIZE, *junk)
                self.assertEqual((status, clean_status), (expected, expected))
                if op == 1:
                    self.assertEqual(bytes(out), clean_out[:HELLO_SIZE + 16])
            for dirty in ((0xDEADBEEF_00000000, 0, 0), (0, 0xFFFFFFFF_00000000, 0), (0, 0, 0x12345678_00000000)):
                with self.subTest(op=op, record=record.hex(), upper=[hex(d) for d in dirty]):
                    out = (ctypes.c_ubyte * (HELLO_SIZE + 16))()
                    ctypes.memset(out, CANARY, len(out))
                    status = eight(dirty[0] | op, in_buf, dirty[1] | len(record), ctypes.addressof(out),
                                   dirty[2] | HELLO_SIZE, *junk)
                    self.assertEqual(status, BAD_ARGS)
                    self.assert_untouched(bytes(out))

    def test_ten_thousand_hellos_are_identical(self):
        status, first = self.call(1)
        out = (ctypes.c_ubyte * HELLO_SIZE)()
        for _ in range(10_000):
            self.assertEqual(self.bridge(1, None, 0, out, HELLO_SIZE), HELLO_SIZE)
        self.assertEqual(bytes(out), first[:HELLO_SIZE])


STACK, BUFFER, SENTINEL = 0x10000000, 0x20000000, 0x30000000


@contextlib.contextmanager
def quiet_faulthandler():
    """Unicorn's mem_map raises and handles a first-chance access violation on Windows (reproducible with a bare
    Uc().mem_map call). Python's faulthandler reports it although it is handled, so mute it while emulating."""
    enabled = faulthandler.is_enabled()
    if enabled:
        faulthandler.disable()
    try:
        yield
    finally:
        if enabled:
            faulthandler.enable()


def emulate_export(image: bytes, name: str):
    """Run one export of a PE32+ image in unicorn with RCX = BUFFER. Returns (rax, buffer bytes, writes, uc)."""
    with quiet_faulthandler():
        return _emulate_export(image, name)


def _emulate_export(image: bytes, name: str):
    pe = audit_pe.PE(image)
    export = {e['name']: e for e in pe.exports()}[name]
    uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
    uc.mem_map(pe.image_base, (pe.size_of_image + 0xFFF) & ~0xFFF)
    uc.mem_write(pe.image_base, image[:pe.size_of_headers])
    for s in pe.sections:
        size = min(s['raw_size'], s['vsize'] or s['raw_size'])
        uc.mem_write(pe.image_base + s['va'], image[s['raw_ptr']:s['raw_ptr'] + size])
    for base in (STACK, BUFFER, SENTINEL):
        uc.mem_map(base, 0x10000)
    uc.mem_write(BUFFER, bytes([CANARY]) * 64)
    rsp = STACK + 0x8000 - 8  # as after a call: RSP = 8 (mod 16)
    uc.mem_write(rsp, struct.pack('<Q', SENTINEL))
    garbage = 0x5A5A5A5A5A5A5A5A
    for reg in (x86_const.UC_X86_REG_RAX, x86_const.UC_X86_REG_RDX, x86_const.UC_X86_REG_R8,
                x86_const.UC_X86_REG_R9, x86_const.UC_X86_REG_R10, x86_const.UC_X86_REG_R11):
        uc.reg_write(reg, garbage)
    uc.reg_write(x86_const.UC_X86_REG_RSP, rsp)
    uc.reg_write(x86_const.UC_X86_REG_RCX, BUFFER)
    writes = []
    uc.hook_add(unicorn.UC_HOOK_MEM_WRITE, lambda _uc, _a, addr, size, _v, _d: writes.append((addr, size)))
    uc.emu_start(pe.image_base + export['rva'], SENTINEL, count=10_000)
    rip = uc.reg_read(x86_const.UC_X86_REG_RIP)
    if rip != SENTINEL:
        raise AssertionError(f'{name} did not return (RIP 0x{rip:X})')
    return uc.reg_read(x86_const.UC_X86_REG_RAX), bytes(uc.mem_read(BUFFER, 64)), writes, uc


def written_bytes(writes, start, end):
    return {a for addr, size in writes for a in range(addr, addr + size) if start <= a < end}


@unittest.skipUnless(NATIVE and unicorn is not None, 'requires unicorn and the pinned MSYS2 g++')
class EmulatedExportTest(unittest.TestCase):
    def check_get_ver(self, image: bytes):
        rax, buffer, writes, _ = emulate_export(image, 'FF12HgetVer')
        self.assertEqual(rax, BUFFER, 'FF12HgetVer must return the hidden pointer in RAX')
        outside = [(a, s) for a, s in writes if not (BUFFER <= a < BUFFER + 12 or STACK <= a < STACK + 0x10000)]
        self.assertEqual(outside, [], 'writes outside the 12-byte destination and the stack')
        self.assertEqual(written_bytes(writes, BUFFER, BUFFER + 64), set(range(BUFFER, BUFFER + 12)))
        self.assertEqual(buffer[12:], bytes([CANARY]) * 52)
        return struct.unpack_from('<3I', buffer)

    def test_our_get_ver(self):
        self.assertEqual(self.check_get_ver(built_dll().read_bytes()), VERSION)

    def test_instrument_rejects_a_non_hidden_pointer_export(self):
        # Control: point FF12HgetVer's export slot at FF12HgetName's code, which returns a string pointer
        # instead of RCX and writes nothing; the hidden-pointer check must fail on it.
        image = bytearray(built_dll().read_bytes())
        pe = audit_pe.PE(bytes(image))
        exports = {e['name']: e for e in pe.exports()}
        directory = pe.offset(pe.dirs[0][0])
        base, functions = struct.unpack_from('<I', image, directory + 16)[0], struct.unpack_from('<I', image, directory + 28)[0]
        slot = pe.offset(functions + 4 * (exports['FF12HgetVer']['ordinal'] - base))
        struct.pack_into('<I', image, slot, exports['FF12HgetName']['rva'])
        with self.assertRaises(AssertionError):
            self.check_get_ver(bytes(image))

    def test_our_get_name(self):
        rax, _, writes, uc = emulate_export(built_dll().read_bytes(), 'FF12HgetName')
        self.assertEqual([w for w in writes if not STACK <= w[0] < STACK + 0x10000], [])
        self.assertEqual(bytes(uc.mem_read(rax, 21)), b'Ivalice Legacy Co-op\0')
        pe = audit_pe.PE(built_dll().read_bytes())
        section = pe.section_of(rax - pe.image_base)
        self.assertIsNotNone(section)
        self.assertFalse(section['flags'] & 0x80000000, f'name lives in writable section {section["name"]}')

    @unittest.skipUnless(GAME, 'set IVALICE_LEGACY_GAME to run the installed-module control (read-only)')
    def test_installed_modules_follow_the_same_abi(self):
        modules = Path(GAME) / 'x64' / 'modules'
        found = 0
        for name in ('00-ff12-tkmalloc.dll', 'ff12-lua-loader.dll', 'ff12-file-loader.dll'):
            path = modules / name
            if not path.exists():
                continue
            found += 1
            with self.subTest(name):
                version = self.check_get_ver(path.read_bytes())
                self.assertTrue(any(version), f'{name} returned version {version}')
        self.assertGreater(found, 0, f'no EFL modules found under {modules}')


@unittest.skipUnless(NATIVE and capstone is not None, 'requires capstone and the pinned MSYS2 g++')
class StaticCodeTest(unittest.TestCase):
    def straight_line(self, image: bytes, rva: int) -> list:
        """Disassemble from rva to the first ret; fail on any call or jump before it."""
        pe = audit_pe.PE(image)
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        listing = []
        for insn in md.disasm(image[pe.offset(rva):pe.offset(rva) + 64], pe.image_base + rva):
            listing.append(f'{insn.mnemonic} {insn.op_str}'.strip())
            self.assertFalse(insn.mnemonic.startswith(('call', 'j', 'syscall', 'int')),
                             f'control transfer at 0x{insn.address:X}: {listing}')
            if insn.mnemonic == 'ret':
                return listing
        self.fail(f'no ret within 64 bytes: {listing}')

    def map_symbol(self, name: str) -> int:
        pe = audit_pe.PE(built_dll().read_bytes())
        text = (built_dll().parent / coop_build.MAP_NAME).read_text(errors='replace')
        match = re.search(r'^\s+0x([0-9a-f]{16})\s+' + re.escape(name) + r'\s*$', text, re.M)
        self.assertIsNotNone(match, f'{name} not in the map file')
        return int(match.group(1), 16) - pe.image_base

    def test_dllmain_does_nothing(self):
        # The image entry is the mingw-w64 CRT's DllMainCRTStartup, which runs the CRT
        # start-up and static constructors and then calls our DllMain; DllMain itself stays a bare return TRUE.
        image = built_dll().read_bytes()
        pe = audit_pe.PE(image)
        self.assertEqual(pe.entry_rva, self.map_symbol('DllMainCRTStartup'))
        self.assertEqual(self.straight_line(image, self.map_symbol('DllMain')), ['mov eax, 1', 'ret'])

    def test_lifecycle_exports_only_forward_to_the_boot(self):
        # Each export is a single tail jump to its boot function (src/boot/boot.cpp), found through the map.
        image = built_dll().read_bytes()
        pe = audit_pe.PE(image)
        exports = {e['name']: e['rva'] for e in pe.exports()}
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        targets = {'FF12Hinit': 'on_init', 'FF12HinitDone': 'on_init_done', 'FF12Hcleanup': 'on_cleanup'}
        text = (built_dll().parent / coop_build.MAP_NAME).read_text(errors='replace')
        for name, target in targets.items():
            with self.subTest(name):
                insn = next(md.disasm(image[pe.offset(exports[name]):pe.offset(exports[name]) + 16],
                                      pe.image_base + exports[name]))
                self.assertEqual(insn.mnemonic, 'jmp')
                match = re.search(r'^\s+0x([0-9a-f]{16})\s+(\S*' + target + r'\S*)\s*$', text, re.M)
                destination = int(insn.op_str, 16)
                symbols = [m for m in re.finditer(r'^\s+0x([0-9a-f]{16})\s+(\S+)\s*$', text, re.M)
                           if int(m.group(1), 16) == destination]
                self.assertTrue(any(target in m.group(2) for m in symbols),
                                f'{name} jumps to 0x{destination:X}, not {target} ({match})')


def tearDownModule():
    # The loaded DLL keeps its folder locked until the process exits; leave cleanup to the OS temp sweep.
    if 'dll' in _built:
        shutil.rmtree(_built['dll'].parent, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
