"""Boot matrix for the co-op module: FF12Hinit, FF12HinitDone and FF12Hcleanup against the image twin (the pinned
FFXII_TZA.exe mapped at 0x120000 in this test process), with a temporary game folder. No game run, nothing deployed;
the exe is only read (point IVALICE_LEGACY_GAME at a copy; its SHA-256 is pinned by sites.inc).

Each scenario loads a fresh copy of the host-test DLL (its own boot state, patch layer, MinHook and input service) and
tells its boot which process it runs in: image base, the x64 folder of the temporary game folder, the process id for
mutex names, and a private instance-guard name. The image twin itself belongs to the shared Twin; every scenario
releases its own hooks and the twin is re-mapped afterwards.

Matrix:
* Co-op module Off (and an unreadable or invalid settings file): DISABLED; no init thread, no input thread, no hook,
  no allocation near the image, no file written, the twin byte-identical;
* every Ivalice Together blocking marker (files, loaded module, both mutexes, the three region magics, the install
  marker), and uncertain checks: BLOCKED with that marker bit, nothing armed; residue alone does not block;
* wrong base / unreadable headers / not AMD64 / wrong timestamp / wrong SizeOfImage: INERT, UNSUPPORTED_BUILD, and
  not even the instance guard is created;
* a second copy (guard held), a second FF12Hinit, and a copy that never got FF12Hinit: inert;
* happy path: the init thread starts the input service (SDL_Init on the service's own thread), installs every hook
  dry-armed (all non-CORE gates closed, subsystem bits closed, UT-01 open), arms the liveness record; FF12HinitDone
  gives READY; a pushed settings record reaches the subsystem mask and the input service at the safe point;
* cleanup ordering: disarm, then join the init thread, then stop the input service; hooks stay installed; cleanup
  during start-up aborts before anything is armed; Together appearing before the install still blocks; a patch
  conflict faults with the site in the status.
The parser and the detector are also tested on their own (tables).
"""
from __future__ import annotations

import ctypes
import hashlib
import itertools
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path

import test_coop_patch_engine as pe
from coop_native_support import SCHEMA as S, Bridge, quiet_faulthandler
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, Twin, built, near_private, regions

STATE = {n: S.e('state', n) for n in ('INERT', 'DISABLED', 'BLOCKED', 'STARTING', 'ARMED', 'READY', 'FAULT')}
REASON = {n: v for n, v in S.enums['reason'].items()}
FLAG = {n: v for n, v in S.enums['boot_flag'].items()}
STEP = {n: v for n, v in S.enums['boot_step'].items()}
STEP_BEFORE_ARM = 0x80
EV = dict(INIT_THREAD=1, INPUT_STARTED=2, HOOKS_INSTALLED=3, ARMED=4, INIT_DONE=5, CLEANUP_BEGIN=6, DISARMED=7,
          THREAD_JOINED=8, INPUT_STOPPED=9, CLEANUP_END=10, THREAD_EXIT=11, JOIN_TIMEOUT=12, INPUT_START_CALL=13)
MARKER_FILE, MARKER_MODULE, MARKER_MUTEX, MARKER_REGION = 1, 2, 3, 4
BLOCKING, RESIDUE, UNCERTAIN = 0x3FF, 0x30000, 0x80000000
SETTINGS_ON, SETTINGS_OFF, SETTINGS_UNREADABLE, SETTINGS_INVALID = 0, 1, 2, 3
INVALID_SYNTAX, INVALID_SCHEMA, INVALID_NEWER, INVALID_VALUE = 1, 2, 3, 4
STATE_IDLE, STATE_INSTALLED = 0, 1
SITE_MISMATCH = 7
RUN_RUNNING = 2


class BootStatus(ctypes.Structure):
    _fields_ = [('state', ctypes.c_uint32), ('reason', ctypes.c_uint32), ('detail', ctypes.c_uint32),
                ('flags', ctypes.c_uint32)]


class Marker(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_uint32), ('bit', ctypes.c_uint32), ('offset', ctypes.c_uint32),
                ('reserved', ctypes.c_uint32), ('text', ctypes.c_char * 128)]


class Switch(ctypes.Structure):
    _fields_ = [('outcome', ctypes.c_uint32), ('detail', ctypes.c_uint32), ('defaulted', ctypes.c_bool)]


class Liveness(ctypes.Structure):
    _fields_ = [('armed', ctypes.c_uint32), ('epoch', ctypes.c_uint32), ('heartbeat_ms', ctypes.c_uint64),
                ('now_ms', ctypes.c_uint64), ('coherent', ctypes.c_bool), ('fresh', ctypes.c_bool)]


class ControlView(ctypes.Structure):
    _fields_ = [('pairing_stage', ctypes.c_uint8), ('pairing_seat', ctypes.c_uint8), ('capture_seat', ctypes.c_uint8),
                ('override_active', ctypes.c_uint8), ('parked', ctypes.c_uint8), ('focused', ctypes.c_uint8),
                ('reserved', ctypes.c_uint8 * 2), ('move_request', ctypes.c_uint32 * 4),
                ('move_character', ctypes.c_uint8 * 4), ('events_dropped', ctypes.c_uint32)]


_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
_k32.VirtualAlloc.restype = ctypes.c_void_p
_k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
_k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_k32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
_k32.LoadLibraryW.restype = ctypes.c_void_p
_k32.FreeLibrary.argtypes = [ctypes.c_void_p]
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_k32.GetModuleHandleW.restype = ctypes.c_void_p
_adv = ctypes.WinDLL('advapi32', use_last_error=True)
_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
ERROR_ALREADY_EXISTS = 183


class ThreadEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ThreadID', wintypes.DWORD),
                ('th32OwnerProcessID', wintypes.DWORD), ('tpBasePri', ctypes.c_long),
                ('tpDeltaPri', ctypes.c_long), ('dwFlags', wintypes.DWORD)]


def thread_ids() -> set[int]:
    snap = _k32.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    entry = ThreadEntry()
    entry.dwSize = ctypes.sizeof(entry)
    out, pid = set(), os.getpid()
    ok = _k32.Thread32First(snap, ctypes.byref(entry))
    while ok:
        if entry.th32OwnerProcessID == pid:
            out.add(entry.th32ThreadID)
        ok = _k32.Thread32Next(snap, ctypes.byref(entry))
    _k32.CloseHandle(snap)
    return out


_copies = itertools.count()
_guards = itertools.count()


def declare(d):
    d.ILCT_BootSetHost.argtypes = [ctypes.c_uint64, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p]
    d.ILCT_BootStatus.argtypes = [ctypes.POINTER(BootStatus)]
    d.ILCT_BootMarker.argtypes = [ctypes.c_uint32, ctypes.POINTER(Marker)]
    d.ILCT_BootDetect.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    d.ILCT_BootDetect.restype = ctypes.c_uint32
    d.ILCT_BootParseSwitch.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_int32, ctypes.POINTER(Switch)]
    d.ILCT_BootReadSwitch.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.POINTER(Switch)]
    d.ILCT_BootEvents.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    d.ILCT_BootEvents.restype = ctypes.c_uint32
    for name in ('ILCT_BootInitThreadId', 'ILCT_BootInputStartThreadId', 'ILCT_BootPausedAt', 'ILCT_BootMarkerCount',
                 'ILCT_PatchState', 'ILCT_PatchMode', 'ILCT_PatchSubsystems'):
        getattr(d, name).restype = ctypes.c_uint32
    d.ILCT_BootWaitSettled.argtypes = [ctypes.c_uint32]
    d.ILCT_BootPauseAt.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchSiteInfo.argtypes = [ctypes.c_uint32, ctypes.POINTER(pe.SiteInfo)]
    d.ILCT_LivenessSample.argtypes = [ctypes.POINTER(Liveness)]
    d.ILCT_Control.argtypes = [ctypes.POINTER(ControlView)]
    d.ILCT_TickRun.argtypes = [ctypes.c_uint32]
    for name in ('FF12Hinit', 'FF12HinitDone', 'FF12Hcleanup'):
        getattr(d, name).argtypes = []
        getattr(d, name).restype = None


def fresh_test_dll():
    source = Path(built()['test'])
    copy = source.parent / f'ilc-boot-{next(_copies)}.dll'
    shutil.copyfile(source, copy)
    d = ctypes.WinDLL(str(copy))
    declare(d)
    return d


def markers(d) -> list[Marker]:
    out = []
    for i in range(d.ILCT_BootMarkerCount()):
        m = Marker()
        assert d.ILCT_BootMarker(i, ctypes.byref(m)) == 1
        out.append(m)
    return out


def status(d) -> BootStatus:
    s = BootStatus()
    d.ILCT_BootStatus(ctypes.byref(s))
    return s


def events(d) -> list[int]:
    buf = (ctypes.c_uint32 * 32)()
    n = d.ILCT_BootEvents(buf, 32)
    return list(buf[:n])


class GameFolder:
    """A temporary game folder: x64/ (and scripts/config/IvaliceLegacy/ when settings are written)."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix='ilc-boot-game-'))
        self.x64 = self.root / 'x64'
        self.x64.mkdir()
        self.settings = self.x64 / 'scripts' / 'config' / 'IvaliceLegacy' / 'coop.json'

    def write_settings(self, text: str | bytes):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_bytes(text.encode() if isinstance(text, str) else text)

    def module_switch(self, on: bool):
        self.write_settings('{"schema": 1, "module": {"enabledAtStart": %s}, "keyboardSeat": 2}' %
                            ('true' if on else 'false'))

    def snapshot(self) -> dict:
        out = {}
        for p in sorted(self.root.rglob('*')):
            st = p.stat()
            out[p.relative_to(self.root).as_posix()] = (
                p.is_dir(), st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else '')
        return out

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def low_region(magic: bytes, offset: int) -> int:
    """A 64 KiB committed allocation in the low 2 GiB holding `magic` at `offset`."""
    for address in range(0x10000000, 0x70000000, 0x01000000):
        base = _k32.VirtualAlloc(address, 0x10000, 0x3000, 0x04)
        if base:
            ctypes.memmove(base + offset, magic, len(magic))
            return base
    raise AssertionError('no free 64 KiB in the low 2 GiB')


class Instrument(unittest.TestCase):
    """Shared helpers: the twin, fresh boot copies, and the "nothing happened" checks."""

    @classmethod
    def setUpClass(cls):
        cls.twin = Twin.get()

    def setUp(self):
        self.twin.reset()
        self.assertEqual(self.twin.image(), self.twin.baseline)
        self.game = GameFolder()
        self.copies = []
        self.cleanups = []

    def tearDown(self):
        for fn in reversed(self.cleanups):
            fn()
        for d in self.copies:
            d.ILCT_BootResume()
            d.FF12Hcleanup()
            self.assertEqual(d.ILCT_PatchReleaseAll(), OK)
        self.twin.reset()
        self.game.close()

    def boot_copy(self, base: int = TWIN_BASE, guard: str | None = None):
        d = fresh_test_dll()
        self.copies.append(d)
        guard = guard or f'Local\\ILCBootTest_{os.getpid()}_{next(_guards)}'
        d.ILCT_BootSetHost(base, os.getpid(), str(self.game.x64), guard)
        d.guard = guard
        return d

    def init(self, d):
        with quiet_faulthandler():
            started = time.perf_counter()
            d.FF12Hinit()
            return time.perf_counter() - started

    def before(self) -> dict:
        return {'threads': thread_ids(), 'near': near_private(), 'files': self.game.snapshot(),
                'image': self.twin.image()}

    def assert_nothing_started(self, d, before: dict):
        time.sleep(0.3)
        self.assertEqual(d.ILCT_BootInitThreadId(), 0, 'an init thread ran')
        self.assertEqual(events(d), [], 'lifecycle events were recorded')
        self.assertFalse(status(d).flags & (FLAG['THREAD_STARTED'] | FLAG['INPUT_RUNNING'] | FLAG['HOOKS_INSTALLED']))
        self.assertEqual(d.ILCT_PatchState(), STATE_IDLE)
        self.assertEqual(d.ILCT_Control(ctypes.byref(ControlView())), 0, 'the input service published')
        new_threads = thread_ids() - before['threads']
        self.assertEqual(new_threads, set(), f'threads appeared: {new_threads}')
        self.assertEqual(near_private(), before['near'], 'allocations appeared near the image')
        self.assertEqual(self.twin.image(), before['image'], 'image bytes changed')
        self.assertEqual(regions(TWIN_BASE, TWIN_BASE + TWIN_SIZE), self.twin.baseline_protections)
        self.assertEqual(self.game.snapshot(), before['files'], 'files in the game folder changed')

    def assert_status(self, d, state: str, reason: str, detail: int | None = None):
        s = status(d)
        got = (s.state, s.reason) if detail is None else (s.state, s.reason, s.detail)
        want = (STATE[state], REASON[reason]) if detail is None else (STATE[state], REASON[reason], detail)
        self.assertEqual(got, want, f'status {s.state}/{s.reason}/{s.detail:#x}')
        # The bridge reports the same.
        st = S.decode('ilc_status', Bridge(d).call(S.e('op', 'GET_STATUS'))[1])
        self.assertEqual((st['state'], st['reason'], st['reason_detail'], st['boot_flags'], st['build_flags']),
                         (s.state, s.reason, s.detail, s.flags, 0))
        hello = S.decode('ilc_hello_reply', Bridge(d).call(S.e('op', 'HELLO'))[1])
        self.assertEqual((hello['state'], hello['reason']), (s.state, s.reason))
        return s


# ---- parser and detector on their own -------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class SettingsSwitchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = fresh_test_dll()

    def parse(self, text, default_on=True):
        raw = text.encode() if isinstance(text, str) else text
        out = Switch()
        self.d.ILCT_BootParseSwitch(raw, len(raw), 1 if default_on else 0, ctypes.byref(out))
        return out.outcome, out.detail, out.defaulted

    def test_table(self):
        cases = [
            ('{"schema":1,"module":{"enabledAtStart":true}}', (SETTINGS_ON, 0, False)),
            ('{"schema":1,"module":{"enabledAtStart":false}}', (SETTINGS_OFF, 0, False)),
            ('  {"module":{"enabledAtStart":false},"schema":1}\r\n', (SETTINGS_OFF, 0, False)),
            ('\xef\xbb\xbf{"schema":1,"module":{"enabledAtStart":false}}'.encode('latin-1'), (SETTINGS_OFF, 0, False)),
            ('{"schema":1}', (SETTINGS_ON, 0, True)),
            ('{"schema":1,"module":{}}', (SETTINGS_ON, 0, True)),
            ('{"schema":1,"module":{"other":[1,2,{"enabledAtStart":false}]}}', (SETTINGS_ON, 0, True)),
            ('{"schema":1,"seats":[{"module":{"enabledAtStart":false}}]}', (SETTINGS_ON, 0, True)),
            ('{"schema":1,"module":{"enabledAtStart":true},"module":{"enabledAtStart":false}}', (SETTINGS_OFF, 0, False)),
            ('{"schema":1,"module":{"enabledAtStart":false,"enabledAtStart":true}}', (SETTINGS_ON, 0, False)),
            ('{"schema":1,"module":{"enabled\\u0041tStart":false}}', (SETTINGS_OFF, 0, False)),
            ('{"schema":1,"module":{"enabledAtStart":"false"}}', (SETTINGS_INVALID, INVALID_VALUE, False)),
            ('{"schema":1,"module":{"enabledAtStart":0}}', (SETTINGS_INVALID, INVALID_VALUE, False)),
            ('{"schema":1,"module":{"enabledAtStart":null}}', (SETTINGS_INVALID, INVALID_VALUE, False)),
            ('{"schema":1,"module":true}', (SETTINGS_INVALID, INVALID_VALUE, False)),
            ('{"schema":2,"module":{"enabledAtStart":true}}', (SETTINGS_INVALID, INVALID_NEWER, False)),
            ('{"schema":10,"module":{"enabledAtStart":true}}', (SETTINGS_INVALID, INVALID_NEWER, False)),
            ('{"schema":1.0,"module":{"enabledAtStart":true}}', (SETTINGS_INVALID, INVALID_SCHEMA, False)),
            ('{"schema":"1","module":{"enabledAtStart":true}}', (SETTINGS_INVALID, INVALID_SCHEMA, False)),
            ('{"schema":0}', (SETTINGS_INVALID, INVALID_SCHEMA, False)),
            ('{"schema":-1}', (SETTINGS_INVALID, INVALID_SCHEMA, False)),
            ('{"module":{"enabledAtStart":true}}', (SETTINGS_INVALID, INVALID_SCHEMA, False)),
            ('', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('[]', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1} x', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"module":{"enabledAtStart":true}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":01}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"a":"\x01"}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"a":"\\x"}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"module":{"enabledAtStart":tru}}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"a":' + '[' * 40 + ']' * 40 + '}', (SETTINGS_INVALID, INVALID_SYNTAX, False)),
            ('{"schema":1,"a":' + '[' * 20 + ']' * 20 + '}', (SETTINGS_ON, 0, True)),
        ]
        for text, want in cases:
            with self.subTest(text=text[:60]):
                self.assertEqual(self.parse(text), want)

    def test_default_off_follows_the_native_default(self):
        self.assertEqual(self.parse('{"schema":1}', default_on=False), (SETTINGS_OFF, 0, True))

    def test_truncations_never_read_past_the_buffer(self):
        text = b'{"schema":1,"module":{"enabledAtStart":false},"seats":[{"name":"P\\u00e9","x":-1.5e3}]}'
        self.assertEqual(self.parse(text), (SETTINGS_OFF, 0, False))
        for n in range(len(text)):
            with self.subTest(n=n):
                self.assertEqual(self.parse(text[:n])[0:2], (SETTINGS_INVALID, INVALID_SYNTAX))

    def test_file_reads(self):
        game = GameFolder()
        try:
            out = Switch()
            self.d.ILCT_BootReadSwitch(str(game.x64), 1, ctypes.byref(out))
            self.assertEqual((out.outcome, out.detail, out.defaulted), (SETTINGS_ON, 0, True), 'missing file')
            game.module_switch(False)
            before = game.snapshot()
            # A writer holding the file open (share read) does not stop the read, and the read changes nothing.
            with open(game.settings, 'r+b'):
                self.d.ILCT_BootReadSwitch(str(game.x64), 1, ctypes.byref(out))
            self.assertEqual((out.outcome, out.defaulted), (SETTINGS_OFF, False))
            self.assertEqual(game.snapshot(), before)
            game.write_settings(b' ' * (1 << 20) + b'{"schema":1}')
            self.d.ILCT_BootReadSwitch(str(game.x64), 1, ctypes.byref(out))
            self.assertEqual((out.outcome, out.detail), (SETTINGS_UNREADABLE, 0), 'larger than 1 MiB')
            game.settings.unlink()
            game.settings.mkdir()
            self.d.ILCT_BootReadSwitch(str(game.x64), 1, ctypes.byref(out))
            self.assertEqual((out.outcome, out.detail), (SETTINGS_UNREADABLE, 5), 'a folder named coop.json')
        finally:
            game.close()


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class TogetherDetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = fresh_test_dll()
        cls.table = markers(cls.d)

    def setUp(self):
        self.game = GameFolder()

    def tearDown(self):
        self.game.close()

    def detect(self) -> int:
        with quiet_faulthandler():
            return self.d.ILCT_BootDetect(str(self.game.x64), os.getpid())

    def test_table_shape(self):
        bits = [m.bit for m in self.table]
        self.assertEqual(len(bits), len(set(bits)))
        self.assertEqual(sum(b for b in bits if b & BLOCKING), BLOCKING)
        self.assertEqual(sum(b for b in bits if b & RESIDUE), RESIDUE)
        kinds = {m.kind for m in self.table if m.bit & BLOCKING}
        self.assertEqual(kinds, {MARKER_FILE, MARKER_MODULE, MARKER_MUTEX, MARKER_REGION})
        self.assertTrue(all(len(m.text) == 8 for m in self.table if m.kind == MARKER_REGION))

    def test_clean_folder_detects_nothing(self):
        self.assertEqual(self.detect(), 0)

    def test_each_marker_alone(self):
        for m in self.table:
            with self.subTest(marker=m.text.decode(), bit=hex(m.bit)):
                undo = make_marker(self.game, m)
                try:
                    self.assertEqual(self.detect(), m.bit)
                finally:
                    undo()
                self.assertEqual(self.detect(), 0)

    def test_uncertain_mutex_counts_as_present(self):
        for m in (m for m in self.table if m.kind == MARKER_MUTEX):
            name = m.text.decode() + str(os.getpid())
            with self.subTest(case='access denied', name=name):
                sd = ctypes.c_void_p()
                self.assertTrue(_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:', 1, ctypes.byref(sd),
                                                                                          None))

                class SA(ctypes.Structure):
                    _fields_ = [('n', wintypes.DWORD), ('sd', ctypes.c_void_p), ('inherit', wintypes.BOOL)]
                sa = SA(ctypes.sizeof(SA), sd, False)
                h = _k32.CreateMutexW(ctypes.byref(sa), False, name)
                self.assertTrue(h)
                try:
                    self.assertEqual(self.detect(), m.bit | UNCERTAIN)
                finally:
                    _k32.CloseHandle(h)
            with self.subTest(case='another object type', name=name):
                h = _k32.CreateEventW(None, True, False, name)
                self.assertTrue(h)
                try:
                    self.assertEqual(self.detect(), m.bit | UNCERTAIN)
                finally:
                    _k32.CloseHandle(h)

    def test_unknown_folder_is_uncertain(self):
        with quiet_faulthandler():
            found = self.d.ILCT_BootDetect(None, os.getpid())
        file_bits = sum(m.bit for m in self.table if m.kind == MARKER_FILE)
        self.assertEqual(found, file_bits | UNCERTAIN)


def make_marker(game: GameFolder, m: Marker):
    """Creates one Together marker from the module's own table; returns the undo function."""
    text = m.text.decode()
    if m.kind == MARKER_FILE:
        path = game.x64 / text.replace('\\', os.sep)
        path.parent.mkdir(parents=True, exist_ok=True)
        if '.' in path.name:
            path.write_bytes(b'')
        else:
            path.mkdir()
        return lambda: (shutil.rmtree(path) if path.is_dir() else path.unlink())
    if m.kind == MARKER_MODULE:
        folder = Path(tempfile.mkdtemp(prefix='ilc-boot-module-'))
        copy = folder / text
        shutil.copyfile(built()['dll'], copy)  # the shipped module: its DllMain does nothing
        handle = _k32.LoadLibraryW(str(copy))
        assert handle

        def undo():
            _k32.FreeLibrary(handle)
            shutil.rmtree(folder, ignore_errors=True)
        return undo
    if m.kind == MARKER_MUTEX:
        h = _k32.CreateMutexW(None, False, text + str(os.getpid()))
        assert h
        return lambda: _k32.CloseHandle(h)
    base = low_region(m.text[:8], m.offset)
    return lambda: _k32.VirtualFree(base, 0, 0x8000)


# ---- the boot matrix --------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class BootRefusalTest(Instrument):
    def test_module_off_starts_nothing(self):
        self.game.module_switch(False)
        before = self.before()
        d = self.boot_copy()
        self.assertLess(self.init(d), 1.0)
        s = self.assert_status(d, 'DISABLED', 'MODULE_OFF', 0)
        self.assertEqual(s.flags, FLAG['INIT_CALLED'] | FLAG['GUARD_HELD'])
        self.assert_nothing_started(d, before)
        d.FF12HinitDone()
        d.FF12Hcleanup()
        self.assert_status(d, 'DISABLED', 'MODULE_OFF', 0)
        self.assertEqual(events(d), [EV['INIT_DONE'], EV['CLEANUP_BEGIN'], EV['CLEANUP_END']])
        self.assertEqual(self.game.snapshot(), before['files'])

    def test_unreadable_or_invalid_settings_disable(self):
        cases = [
            ('garbage', b'\x00not json', 'SETTINGS_INVALID', INVALID_SYNTAX),
            ('newer schema', b'{"schema":2,"module":{"enabledAtStart":true}}', 'SETTINGS_INVALID', INVALID_NEWER),
            ('wrong type', b'{"schema":1,"module":{"enabledAtStart":1}}', 'SETTINGS_INVALID', INVALID_VALUE),
            ('too large', b' ' * ((1 << 20) + 1), 'SETTINGS_UNREADABLE', 0),
        ]
        for name, data, reason, detail in cases:
            with self.subTest(name):
                self.game.write_settings(data)
                before = self.before()
                d = self.boot_copy()
                self.init(d)
                self.assert_status(d, 'DISABLED', reason, detail)
                self.assert_nothing_started(d, before)

    def test_together_markers_block(self):
        self.game.module_switch(True)  # a missed marker would boot fully, and the checks below would catch it
        table = markers(fresh_test_dll())
        for m in (m for m in table if m.bit & BLOCKING):
            with self.subTest(marker=m.text.decode(), bit=hex(m.bit)):
                undo = make_marker(self.game, m)
                try:
                    before = self.before()
                    d = self.boot_copy()
                    self.init(d)
                    s = self.assert_status(d, 'BLOCKED', 'TOGETHER_PRESENT', m.bit)
                    self.assertEqual(s.flags, FLAG['INIT_CALLED'] | FLAG['GUARD_HELD'])
                    self.assert_nothing_started(d, before)
                finally:
                    undo()

    def test_uncertain_check_blocks(self):
        self.game.module_switch(True)
        name = next(m for m in markers(fresh_test_dll()) if m.kind == MARKER_MUTEX).text.decode() + str(os.getpid())
        h = _k32.CreateEventW(None, True, False, name)
        try:
            before = self.before()
            d = self.boot_copy()
            self.init(d)
            s = self.assert_status(d, 'BLOCKED', 'TOGETHER_PRESENT')
            self.assertTrue(s.detail & UNCERTAIN)
            self.assert_nothing_started(d, before)
        finally:
            _k32.CloseHandle(h)

    def test_residue_alone_does_not_block(self):
        self.game.module_switch(False)  # Off, so the boot stops after the Together check without starting threads
        for m in (m for m in markers(fresh_test_dll()) if m.bit & RESIDUE):
            with self.subTest(marker=m.text.decode()):
                undo = make_marker(self.game, m)
                try:
                    before = self.before()
                    d = self.boot_copy()
                    self.init(d)
                    s = self.assert_status(d, 'DISABLED', 'MODULE_OFF')
                    self.assertTrue(s.flags & FLAG['TOGETHER_RESIDUE'])
                    self.assert_nothing_started(d, before)
                finally:
                    undo()

    def test_unsupported_builds_stay_fully_inert(self):
        self.game.module_switch(True)
        lfanew = struct.unpack_from('<I', self.twin.file, 0x3C)[0]
        timestamp_at, size_at, machine_at = lfanew + 8, lfanew + 24 + 56, lfanew + 4
        cases = [
            ('wrong base', TWIN_BASE + 0x10000, None, 1),
            ('process image (python)', _k32.GetModuleHandleW(None), None, 1),
            ('headers unreadable', TWIN_BASE, 'noaccess', 2),
            ('not AMD64', TWIN_BASE, (machine_at, b'\x4c\x01'), 3),
            ('wrong timestamp', TWIN_BASE, (timestamp_at, struct.pack('<I', 0x5DCA7FD8)), 4),
            ('wrong SizeOfImage', TWIN_BASE, (size_at, struct.pack('<I', 0x2F77000)), 5),
        ]
        for name, base, change, detail in cases:
            with self.subTest(name):
                self.twin.reset()
                old = wintypes.DWORD()
                if change is not None and change != 'noaccess':
                    self.twin.poke(TWIN_BASE + change[0], change[1])
                before = self.before()
                if change == 'noaccess':
                    self.assertTrue(_k32.VirtualProtect(TWIN_BASE, 0x1000, 0x01, ctypes.byref(old)))
                d = self.boot_copy(base=base)
                self.init(d)
                if change == 'noaccess':
                    self.assertTrue(_k32.VirtualProtect(TWIN_BASE, 0x1000, old.value, ctypes.byref(old)))
                s = self.assert_status(d, 'INERT', 'UNSUPPORTED_BUILD', detail)
                self.assertEqual(s.flags, FLAG['INIT_CALLED'], 'the instance guard was taken')
                # The guard name is still free: nothing was created.
                ctypes.set_last_error(0)
                h = _k32.CreateMutexW(None, False, d.guard)
                self.assertNotEqual(ctypes.get_last_error(), ERROR_ALREADY_EXISTS)
                _k32.CloseHandle(h)
                if change != 'noaccess':
                    self.assert_nothing_started(d, before)

    def test_duplicates_stay_inert(self):
        self.game.module_switch(False)
        first = self.boot_copy()
        self.init(first)
        self.assert_status(first, 'DISABLED', 'MODULE_OFF')
        # A second FF12Hinit in the same copy changes nothing.
        self.init(first)
        self.assert_status(first, 'DISABLED', 'MODULE_OFF')
        # A second copy (a stale duplicate deployment) finds the guard.
        self.game.module_switch(True)
        before = self.before()
        second = self.boot_copy(guard=first.guard)
        self.init(second)
        s = self.assert_status(second, 'INERT', 'DUPLICATE_INSTANCE', 0)
        self.assertEqual(s.flags, FLAG['INIT_CALLED'])
        self.assert_nothing_started(second, before)
        # A copy the loader never initialised (a stray LoadLibrary) is inert and says so.
        stray = self.boot_copy()
        self.assert_status(stray, 'INERT', 'NONE', 0)
        self.assertEqual(status(stray).flags, 0)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class BootLiveTest(Instrument):
    """Scenarios where the init thread runs: the input service starts for real on this PC (no game)."""

    def settle(self, d, timeout=20.0):
        self.assertEqual(d.ILCT_BootWaitSettled(int(timeout * 1000)), 1, f'init thread did not settle: {status(d).reason}')

    def push(self, d, generation: int, mask: int, park_p2: bool):
        record = bytearray(S.defaults())
        struct.pack_into('<I', record, S.locate('ilc_settings', ['header', 'generation'])[0], generation)
        struct.pack_into('<I', record, S.locate('ilc_settings', ['subsystems_armed'])[0], mask)
        record[S.locate('ilc_settings', ['seats', 1, 'parked'])[0]] = 1 if park_p2 else 0
        status_code, _ = Bridge(d).call(S.e('op', 'SET_SETTINGS'), bytes(record))
        self.assertGreater(status_code, 0)

    def gates(self, d) -> dict:
        out = {}
        for row in self.twin.patch:
            info = pe.SiteInfo()
            d.ILCT_PatchSiteInfo(row, ctypes.byref(info))
            out[info.id.decode()] = (info.gate_open, info.subsystem)
        return out

    def test_happy_path_dry_armed_then_ready_then_orderly_cleanup(self):
        self.game.module_switch(True)
        threads_before = thread_ids()
        d = self.boot_copy()
        elapsed = self.init(d)
        self.assertLess(elapsed, 1.0, 'FF12Hinit must not wait for the init thread')
        self.settle(d)
        s = self.assert_status(d, 'ARMED', 'DRY_ARMED', 0)
        self.assertEqual(s.flags, FLAG['INIT_CALLED'] | FLAG['GUARD_HELD'] | FLAG['THREAD_STARTED'] |
                         FLAG['INPUT_RUNNING'] | FLAG['HOOKS_INSTALLED'] | FLAG['ARMED'])
        init_tid = d.ILCT_BootInitThreadId()
        self.assertNotIn(init_tid, (0, threading.get_native_id()))
        self.assertEqual(d.ILCT_BootInputStartThreadId(), init_tid, 'start_live() must run on the init thread')
        self.assertEqual(events(d), [EV['INIT_THREAD'], EV['INPUT_START_CALL'], EV['INPUT_STARTED'],
                                     EV['HOOKS_INSTALLED'], EV['ARMED']])
        # Hooks: every PATCH row hooked; only MinHook's 43 x 5 jump bytes and the 18 call rows' rel32s differ.
        self.assertEqual(d.ILCT_PatchState(), STATE_INSTALLED)
        image = self.twin.image()
        expected = bytearray(self.twin.baseline)
        for row in self.twin.patch:
            r = self.twin.rows[row]
            k = pe.call_offset(r)
            first = r['rva'] if k is None else r['rva'] + k
            self.assertEqual(image[first], 0xE9 if k is None else 0xE8, r['id'])
            span = pe.written_bytes(r)
            expected[span.start:span.stop] = image[span.start:span.stop]
        self.assertEqual(image, bytes(expected), 'bytes outside the jumps and rel32s changed')
        # Dry-armed: DRY mode, subsystem bits closed, every non-CORE gate closed, UT-01 open.
        self.assertEqual((d.ILCT_PatchMode(), d.ILCT_PatchSubsystems()), (0, 0))
        gates = self.gates(d)
        self.assertEqual({k for k, (g, _) in gates.items() if g}, {'UT-01'})
        live = Liveness()
        self.assertEqual(d.ILCT_LivenessSample(ctypes.byref(live)), 1)
        self.assertEqual((live.armed, live.epoch, live.fresh), (1, 1, True))
        beat = live.heartbeat_ms
        time.sleep(0.1)
        d.ILCT_LivenessSample(ctypes.byref(live))
        self.assertGreater(live.heartbeat_ms, beat, 'the co-op thread heartbeats')
        # The session core runs on the co-op thread: the twin has no game world, so it waits in its first epoch.
        seats = S.decode('ilc_seats_reply', Bridge(d).call(S.e('op', 'GET_SEATS'))[1])
        self.assertEqual((seats['session_state'], seats['epoch']), (S.e('session_state', 'NO_WORLD'), 1))
        self.assertEqual([v['live_member'] for v in seats['seats']][3], 0xFF)
        # The game finished starting.
        d.FF12HinitDone()
        self.assert_status(d, 'READY', 'DRY_ARMED', 0)
        # Settings pushed by Lua reach the arm bits and the input service at the safe point, and nothing leaves DRY.
        self.push(d, 1, 0x05, park_p2=True)
        self.assertEqual(d.ILCT_PatchSubsystems(), 0)
        self.assertEqual(d.ILCT_TickRun(1), 0)
        self.assertEqual(d.ILCT_PatchSubsystems(), 0x05)
        self.assertEqual(d.ILCT_PatchMode(), 0)
        self.assertEqual({k for k, (g, _) in self.gates(d).items() if g}, {'UT-01'})
        view = ControlView()
        deadline = time.time() + 2
        while time.time() < deadline:
            if d.ILCT_Control(ctypes.byref(view)) and view.parked & 0x02:
                break
            time.sleep(0.01)
        self.assertTrue(view.parked & 0x02, 'the input service did not receive the applied record')
        # Cleanup: disarm, join, stop input; hooks stay installed.
        d.FF12Hcleanup()
        self.assertEqual(events(d)[5:], [EV['INIT_DONE'], EV['CLEANUP_BEGIN'], EV['DISARMED'], EV['THREAD_EXIT'],
                                         EV['THREAD_JOINED'], EV['INPUT_STOPPED'], EV['CLEANUP_END']])
        s = self.assert_status(d, 'INERT', 'SHUT_DOWN', 0)
        self.assertFalse(s.flags & (FLAG['ARMED'] | FLAG['INPUT_RUNNING']))
        self.assertTrue(s.flags & FLAG['HOOKS_INSTALLED'] and s.flags & FLAG['CLEANUP_DONE'])
        self.assertEqual(d.ILCT_LivenessSample(ctypes.byref(live)), 0)
        self.assertEqual(live.armed, 0)
        self.assertEqual(d.ILCT_PatchState(), STATE_INSTALLED, 'hooks are never removed at exit')
        self.assertEqual({k for k, (g, _) in self.gates(d).items() if g}, set(), 'disarmed: every gate closed')
        seats = S.decode('ilc_seats_reply', Bridge(d).call(S.e('op', 'GET_SEATS'))[1])
        self.assertEqual(seats['session_state'], S.e('session_state', 'NOT_RUNNING'), 'the session record was withdrawn')
        view = ControlView()
        self.assertTrue(d.ILCT_Control(ctypes.byref(view)))
        time.sleep(0.3)
        left = (thread_ids() - threads_before) & {init_tid}
        self.assertEqual(left, set(), 'the init thread is still alive')
        # A second cleanup is a no-op.
        d.FF12Hcleanup()
        self.assertEqual(events(d)[-1], EV['CLEANUP_END'])
        self.assertEqual(events(d).count(EV['CLEANUP_BEGIN']), 1)

    def test_init_done_before_arming_goes_straight_to_ready(self):
        self.game.module_switch(True)
        d = self.boot_copy()
        d.ILCT_BootPauseAt(STEP_BEFORE_ARM)
        self.init(d)
        self.wait_paused(d, STEP_BEFORE_ARM)
        d.FF12HinitDone()
        self.assert_status(d, 'STARTING', 'BOOTING', STEP['ARM'])
        d.ILCT_BootResume()
        self.settle(d)
        self.assert_status(d, 'READY', 'DRY_ARMED', 0)

    def wait_paused(self, d, step, timeout=20.0):
        deadline = time.time() + timeout
        while d.ILCT_BootPausedAt() != step:
            self.assertLess(time.time(), deadline, f'init thread never reached step {step:#x}')
            time.sleep(0.005)

    def test_cleanup_during_start_up_aborts_before_anything_is_armed(self):
        self.game.module_switch(True)
        d = self.boot_copy()
        d.ILCT_BootPauseAt(STEP['PATCH'])
        self.init(d)
        self.wait_paused(d, STEP['PATCH'])
        cleaner = threading.Thread(target=d.FF12Hcleanup)
        cleaner.start()
        deadline = time.time() + 5
        while EV['DISARMED'] not in events(d):
            self.assertLess(time.time(), deadline)
            time.sleep(0.005)
        d.ILCT_BootResume()
        cleaner.join(10)
        self.assertFalse(cleaner.is_alive())
        ev = events(d)
        self.assertEqual(ev, [EV['INIT_THREAD'], EV['INPUT_START_CALL'], EV['INPUT_STARTED'], EV['CLEANUP_BEGIN'],
                              EV['DISARMED'], EV['THREAD_EXIT'], EV['THREAD_JOINED'], EV['INPUT_STOPPED'],
                              EV['CLEANUP_END']])
        s = self.assert_status(d, 'INERT', 'BOOT_ABORTED', 0)
        self.assertFalse(s.flags & (FLAG['HOOKS_INSTALLED'] | FLAG['ARMED'] | FLAG['INPUT_RUNNING']))
        self.assertEqual(d.ILCT_PatchState(), STATE_IDLE)
        self.assertEqual(self.twin.image(), self.twin.baseline)

    def test_no_script_safe_point_once_start_up_began(self):
        self.game.module_switch(True)
        d = self.boot_copy()
        before = Bridge(d).call(S.e('op', 'TEST_SAFE_POINT'))[0]
        self.assertGreater(before, 0, 'control: the debug op works before FF12Hinit')
        d.ILCT_BootPauseAt(STEP['INPUT'])
        self.init(d)
        self.wait_paused(d, STEP['INPUT'])
        self.assert_status(d, 'STARTING', 'BOOTING', STEP['INPUT'])
        self.assertEqual(Bridge(d).call(S.e('op', 'TEST_SAFE_POINT'))[0], S.e('error', 'NOT_AVAILABLE'))
        d.ILCT_BootResume()
        self.settle(d)
        self.assertEqual(Bridge(d).call(S.e('op', 'TEST_SAFE_POINT'))[0], S.e('error', 'NOT_AVAILABLE'))

    def test_cleanup_join_timeout_never_lets_the_late_thread_change_anything(self):
        self.game.module_switch(True)
        d = self.boot_copy()
        d.ILCT_BootPauseAt(STEP['INPUT'])
        self.init(d)
        self.wait_paused(d, STEP['INPUT'])
        started = time.perf_counter()
        d.FF12Hcleanup()  # the init thread stays paused: the join times out (3 s) and exit goes on
        self.assertLess(time.perf_counter() - started, 6.0)
        self.assertIn(EV['JOIN_TIMEOUT'], events(d))
        self.assert_status(d, 'INERT', 'BOOT_ABORTED', 0)
        d.ILCT_BootResume()
        self.settle(d)
        time.sleep(0.2)
        s = self.assert_status(d, 'INERT', 'BOOT_ABORTED', 0)
        self.assertFalse(s.flags & (FLAG['HOOKS_INSTALLED'] | FLAG['ARMED'] | FLAG['INPUT_RUNNING']))
        self.assertNotIn(EV['INPUT_START_CALL'], events(d))
        self.assertEqual(d.ILCT_PatchState(), STATE_IDLE)
        self.assertEqual(self.twin.image(), self.twin.baseline)

    def test_together_appearing_before_the_install_blocks(self):
        self.game.module_switch(True)
        d = self.boot_copy()
        d.ILCT_BootPauseAt(STEP['RECHECK'])
        self.init(d)
        self.wait_paused(d, STEP['RECHECK'])
        m = next(m for m in markers(d) if m.kind == MARKER_FILE and m.bit & BLOCKING)
        undo = make_marker(self.game, m)
        self.cleanups.append(undo)
        d.ILCT_BootResume()
        self.settle(d)
        s = self.assert_status(d, 'BLOCKED', 'TOGETHER_PRESENT', m.bit)
        self.assertFalse(s.flags & (FLAG['HOOKS_INSTALLED'] | FLAG['ARMED'] | FLAG['INPUT_RUNNING']))
        self.assertEqual(events(d), [EV['INIT_THREAD'], EV['INPUT_START_CALL'], EV['INPUT_STARTED'],
                                     EV['INPUT_STOPPED'], EV['THREAD_EXIT']])
        self.assertEqual(d.ILCT_PatchState(), STATE_IDLE)
        self.assertEqual(self.twin.image(), self.twin.baseline)

    def test_patch_conflict_faults_with_the_site(self):
        self.game.module_switch(True)
        row = self.twin.index['MV-05']
        rva = self.twin.rows[row]['rva']
        self.twin.poke(TWIN_BASE + rva, bytes([self.twin.baseline[rva] ^ 0xFF]))
        reference = self.twin.image()
        d = self.boot_copy()
        self.init(d)
        self.settle(d)
        s = self.assert_status(d, 'FAULT', 'PATCH_CONFLICT', SITE_MISMATCH | ((row + 1) << 16))
        self.assertFalse(s.flags & (FLAG['HOOKS_INSTALLED'] | FLAG['ARMED'] | FLAG['INPUT_RUNNING']))
        self.assertIn(EV['INPUT_STOPPED'], events(d))
        self.assertEqual(d.ILCT_PatchState(), STATE_IDLE)
        self.assertEqual(self.twin.image(), reference, 'a refused install wrote to the image')


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++')
class BootStaticTest(unittest.TestCase):
    def test_shipped_dll_has_no_test_hooks_reachable(self):
        manifest = built()['manifest']
        names = {e['name'] for e in manifest['audit']['exports']}
        self.assertEqual(names, {'FF12HgetVer', 'FF12HgetName', 'FF12Hinit', 'FF12HinitDone', 'FF12Hcleanup',
                                 'ILC_Bridge'})
        self.assertFalse(any(n.startswith('ILCT_') for n in names))

    def test_detection_names_live_only_in_the_detection_file(self):
        # The other mod's markers are spelled once, in src/boot/together_detect.cpp (the provenance tool's
        # allowed detection path); tests take them from the module's own table.
        source = (pe.COOP / 'src' / 'boot' / 'together_detect.cpp').read_text(encoding='utf-8')
        table = markers(fresh_test_dll()) if GAME else []
        for m in table:
            self.assertIn(m.text.decode().replace('\\', '\\\\'), source)
        for path in (pe.COOP / 'src').rglob('*'):
            if path.is_file() and path.name not in ('together_detect.cpp', 'together_detect.h'):
                text = path.read_text(encoding='utf-8', errors='replace')
                for m in table:
                    if m.bit & BLOCKING:  # residue names include the generic word "coop"
                        name = m.text.decode().rsplit(chr(92), 1)[-1]
                        self.assertFalse(name in text, f'{name} spelled in {path.name}')


if __name__ == '__main__':
    unittest.main()
