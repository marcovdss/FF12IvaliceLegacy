"""Shared helpers for the co-op module host tests (no game run, nothing deployed).

* the pinned build, once per test run, into a private temporary folder;
* ILC_REQUIRE_NATIVE=1 turns "no g++" skips into failures;
* fresh copies of the DLL, so each test class starts from the module's initial state;
* a ctypes wrapper for ILC_Bridge that checks canary bytes after every call;
* guard-page buffers (VirtualAlloc + PAGE_NOACCESS neighbours) for out-of-bounds detection.
"""
from __future__ import annotations

import contextlib
import ctypes
import faulthandler
import itertools
import os
import shutil
import struct
import sys
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
sys.path.insert(0, str(COOP))
sys.path.insert(0, str(COOP / 'schema'))

import build as coop_build  # noqa: E402
from codec import Schema  # noqa: E402

COMPILER = Path(os.environ.get('IVALICE_LEGACY_CXX', 'C:/msys64/ucrt64/bin/g++.exe'))
CAN_BUILD = os.name == 'nt' and struct.calcsize('P') == 8 and COMPILER.is_file()
REQUIRE_NATIVE = os.environ.get('ILC_REQUIRE_NATIVE') == '1'
CANARY = 0xA5
PAGE = 4096

_built: dict = {}
_copies = itertools.count()


def native_test(cls):
    """Skip without the toolchain, unless ILC_REQUIRE_NATIVE=1, in which case the tests run and fail."""
    if CAN_BUILD or REQUIRE_NATIVE:
        return cls
    return unittest.skip('requires 64-bit Windows Python and the pinned MSYS2 g++ (set ILC_REQUIRE_NATIVE=1 to '
                         'make this a failure)')(cls)


def built_dll() -> Path:
    if not CAN_BUILD:
        raise AssertionError(f'native toolchain unavailable: {COMPILER} (ILC_REQUIRE_NATIVE=1)')
    if 'dll' not in _built:
        temp = Path(tempfile.mkdtemp(prefix='ilc-bridge-test-'))
        _built['manifest'] = coop_build.build(COMPILER.resolve(), temp)
        _built['dll'] = temp / coop_build.DLL_NAME
    return _built['dll']


def manifest() -> dict:
    built_dll()
    return _built['manifest']


def fresh_dll() -> ctypes.WinDLL:
    """Load a private copy: a new module instance with its initial (default) state."""
    source = built_dll()
    copy = source.parent / f'ilc-copy-{next(_copies)}.dll'
    shutil.copyfile(source, copy)
    return ctypes.WinDLL(str(copy))


@contextlib.contextmanager
def quiet_faulthandler():
    """faulthandler reports first-chance access violations that are handled (by unicorn, or by the module's
    own probe). Mute it while those are expected."""
    enabled = faulthandler.is_enabled()
    if enabled:
        faulthandler.disable()
    try:
        yield
    finally:
        if enabled:
            faulthandler.enable()


class Bridge:
    """ILC_Bridge through ctypes, declared exactly as the module exports it."""

    def __init__(self, dll):
        self.dll = dll
        self.fn = dll.ILC_Bridge
        self.fn.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64]
        self.fn.restype = ctypes.c_int32
        self.address = ctypes.cast(self.fn, ctypes.c_void_p).value

    def raw(self, op, in_ptr, in_len, out_ptr, out_cap) -> int:
        return self.fn(op, in_ptr, in_len, out_ptr, out_cap)

    def call(self, op: int, data: bytes | None = None, out_cap: int = 4096, in_len: int | None = None,
             tail: int = 32) -> tuple[int, bytes]:
        """Call with canaries after outCap. Returns (status, bytes written). Fails on any write it should not
        have made: past the returned length, past outCap, or anything at all on an error."""
        out = (ctypes.c_ubyte * (out_cap + tail))()
        ctypes.memset(out, CANARY, len(out))
        in_buf = ctypes.create_string_buffer(bytes(data), len(data)) if data else None
        length = in_len if in_len is not None else (len(data) if data else 0)
        status = self.fn(op, in_buf, length, out, out_cap)
        raw = bytes(out)
        written = status if status >= 0 else 0
        if status > out_cap:
            raise AssertionError(f'op {op}: returned {status} bytes with outCap {out_cap}')
        if raw[written:] != bytes([CANARY]) * (len(raw) - written):
            raise AssertionError(f'op {op}: status {status} but bytes changed past offset {written}')
        return status, raw[:written]


class GuardedBuffer:
    """size bytes placed flush against a PAGE_NOACCESS page (and, with lead=True, starting right after one)."""

    _k32 = None

    def __init__(self, size: int, lead: bool = False):
        k32 = self._kernel32()
        if size > PAGE:
            raise ValueError('guarded buffers hold at most one page')
        self.base = k32.VirtualAlloc(None, 3 * PAGE, 0x3000, 0x04)  # MEM_COMMIT|MEM_RESERVE, READWRITE
        if not self.base:
            raise OSError('VirtualAlloc failed')
        old = wintypes.DWORD()
        for page in (0, 2):
            if not k32.VirtualProtect(self.base + page * PAGE, PAGE, 0x01, ctypes.byref(old)):  # NOACCESS
                raise OSError('VirtualProtect failed')
        self.size = size
        self.address = self.base + PAGE if lead else self.base + 2 * PAGE - size

    @classmethod
    def _kernel32(cls):
        if cls._k32 is None:
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.VirtualAlloc.restype = ctypes.c_void_p
            k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
            k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
            k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
            cls._k32 = k32
        return cls._k32

    def write(self, data: bytes):
        ctypes.memmove(self.address, data, len(data))

    def read(self, count: int | None = None) -> bytes:
        return ctypes.string_at(self.address, self.size if count is None else count)

    def protect_all(self, protection: int):
        old = wintypes.DWORD()
        self._kernel32().VirtualProtect(self.base + PAGE, PAGE, protection, ctypes.byref(old))

    def close(self):
        if self.base:
            self._kernel32().VirtualFree(self.base, 0, 0x8000)  # MEM_RELEASE
            self.base = 0


SCHEMA = Schema()
