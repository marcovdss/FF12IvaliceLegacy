"""Host tests for the expanded Target Info (native/coop/src/hud/target_info*). No game run, nothing deployed; the exe
is only read (IVALICE_LEGACY_GAME, a copy) to check the pinned bytes the rows and facts name.

* static: the TI-01 row (the native window's only open call), its signature and OPTIONAL flag; the facts restated
  (Anti-Libra test, Hunter's Insight hook, the section globals the battle-pack loader registers); every address
  constant tagged; the game-thread code never blocks, allocates or calls the C runtime; the driver runs the pass, the
  boot composes the view after the snapshot and withdraws it at exit; the Lua scaler's window is never touched;
* Libra gating (the native rule restated): party targets reveal, Libra reveals a foe, stock Anti-Libra hides it whatever Libra
  says, a patched-out Anti-Libra test reads as absent, Libra unknown reads as absent (all 32 inputs);
* the last targeter: the seat that moved onto a target most recently, whose target is in the snapshot; ties to
  the lower seat;
* drops (Hunter's Insight behaviour): named once obtained, "???" otherwise, steals already taken dimmed, empty
  slots left out, gil, a known item without a readable name stays "???", at most 8 lines; no knowledge source = no
  drops list; names from the game's relocated name text (synthetic, and the real battle pack when given);
* statuses with seconds, immune ones only when revealed; the damage ghost; the KO and drop-out fades;
* the drawing: the header on the corner line, the gated rows present or absent, element and status cells, the badge,
  the seat edge, drops; the header line on the log's line at 1920x1080, 1366x768 and 1280x720;
* the native TARGET INFO: TI-01 turns the open request into the close request only while ours is live (element on,
  co-op on, pass and menu layer presented within 250 ms); with the toggle off it is stock;
* the native window given back (target_info_native.*): the TI-V rows (verify-only, INFO), each pattern once in the
  pinned exe; against a synthetic game: ours stopped for 500 ms while the field target mode holds focus on a target
  that resolves writes the owner's id and pending-create bit, each precondition's falsifier writes nothing, the
  pending create is withdrawn when ours comes back, focus is lost or the aim moves, and given up after 10 ticks;
  ours live for 250 ms closes a native window under it (bit 2) and clears a pending create; a TI-V row off keeps
  everything off; the aim the snapshot reads.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import struct
import unittest
from pathlib import Path

import test_coop_hud as th
import test_coop_hud_driver as tdrv
import test_coop_patch_engine as pe
from coop_native_support import CAN_BUILD, REQUIRE_NATIVE
from coop_session_support import GameWorld, Image
from test_coop_hud_native import alloc_low, free
from test_coop_patch_engine import GAME

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'
NATIVE = CAN_BUILD or REQUIRE_NATIVE
BATTLE_PACK = Path(os.environ.get('ILC_BATTLE_PACK', '__no_battle_pack__'))

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

# ---- facts (restated) ---------------------------------------------------------------------------------------------------
TI_OPEN_CALL, TI_SET, TI_CALLER = 0x2BE20E, 0x2BE300, 0x2BE1A0
ANTI_LIBRA_TEST, ANTI_LIBRA_STOCK = 0x2BFFAA, bytes.fromhex('f6871101000002')
HI_HOOK, HI_HOOK_STOCK = 0x2C0ACF, bytes.fromhex('e88c58fdff')
RESOURCE_BASE_PTR, NAME_TEXT = 0x1F83530, 0x2EBF170
ITEM_TABLES = [(0x0000, 12, 8, 0x2EBF100, 18), (0x1000, 52, 2, 0x2EBF0F8, 13), (0x2000, 10, 6, 0x2EBF098, 32)]
ACTOR_LOOT, ACTOR_CLASS, ACTOR_EXT, ACTOR_UNIT, UNIT_NAME = 0xE60, 0xE68, 0x6A0, 0x10, 0xF8
REC_BLOCK = 0x38
FOE_CLASS = 0x00300000

VIEW_SHOWN, VIEW_PARTY, VIEW_REVEAL, VIEW_LIBRA, VIEW_ANTI_LIBRA = 0x1, 0x2, 0x4, 0x8, 0x10
VIEW_NAME, VIEW_DROPS, VIEW_DANGER, VIEW_BOSS, VIEW_FAMILY = 0x20, 0x40, 0x80, 0x100, 0x200
VIEW_ELEMENTS_EXT, VIEW_IMMUNE, VIEW_KO, VIEW_GONE, VIEW_TIMERS, VIEW_HP = 0x400, 0x800, 0x1000, 0x2000, 0x4000, 0x8000
DROP, STEAL, SPECIAL, POACH = 0, 1, 2, 3
KNOWN, UNKNOWN = 0, 1
TAKEN, RARE, GIL = 1, 2, 4
ST_ACTIVE, ST_IMMUNE, ST_TIMED = 1, 2, 4
HI_UNKNOWN, HI_ABSENT, HI_FOREIGN, HI_FOUND = 0, 1, 2, 3
LIVE_MS, KO_FADE_MS, GONE_HOLD_MS = 250, 300, 750
MARGIN_X, HEADER_Y, RULE_W = 47, 50, 420
ATLAS_BATTLE_4_C, ATLAS_PARTYTOP_4_C = 0, 1
EPOCH, NOW = tdrv.EPOCH, tdrv.NOW
SNAP_WORLD, SNAP_PARTY, SNAP_LIBRA_KNOWN, SNAP_LIBRA, SNAP_TICKS = 0x1, 0x2, 0x4, 0x8, 0x10
UNIT_PRESENT, UNIT_ACTOR, UNIT_ANTI_LIBRA, UNIT_PARTY = 0x1, 0x2, 0x8, 0x10


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


DropLine = S('DropLine', [('kind', u8), ('state', u8), ('slot', u8), ('flags', u8), ('text', ctypes.c_char * 24)])
View = S('View', [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64), ('fade_from_ms', u64),
                  ('handle', u32), ('seat', u8), ('level', u8), ('weakness', u8), ('half', u8), ('null_', u8),
                  ('status_count', u8), ('drop_count', u8), ('reserved', u8), ('seat_colour', u32), ('hp', u32),
                  ('hp_max', u32), ('ghost', u32), ('status_id', u8 * 10), ('status_flags', u8 * 10),
                  ('status_secs', u16 * 10), ('name', ctypes.c_char * 32), ('drops', DropLine * 8)])
Ghost = S('Ghost', [('handle', u32), ('ghost', u32), ('frm', u32), ('last_hp', u32), ('hit_ms', u64)])
Counters = S('Counters', [(n, u64) for n in ('built', 'empty', 'withdrawn', 'drawn', 'skipped_epoch',
                                             'native_closed', 'native_stock', 'name_misses', 'hi_scans')])
Status = S('Status', [('attached', u8), ('enabled', u8), ('hi', u8), ('text_ready', u8), ('anti_libra_stock', u8),
                      ('reserved', u8 * 3)])
Unit, Snapshot = tdrv.Unit, tdrv.Snapshot

SIGNATURES = {
    'ILCT_TiSizes': ([P(u32), u32], u32), 'ILCT_TiReset': ([], None), 'ILCT_TiSetImageBase': ([u64], None),
    'ILCT_TiSetEnabled': ([i32], None), 'ILCT_TiSetAttached': ([i32], None), 'ILCT_TiSetLast': ([u64, u64], None),
    'ILCT_TiNow': ([], u64), 'ILCT_TiSelect': ([P(Snapshot), P(i32)], i32),
    'ILCT_TiEngaged': ([P(Snapshot), u32], i32), 'ILCT_TiForceEngaged': ([i32], None),
    'ILCT_TiGate': ([u32, u32, u32, u32, u32], u32), 'ILCT_TiGhost': ([P(Ghost), u32, u32, u64], u32),
    'ILCT_TiBuildDrops': ([ctypes.c_char_p, ctypes.c_char_p, u16, i32, P(DropLine), u32], u32),
    'ILCT_TiDecode': ([ctypes.c_char_p, u32, ctypes.c_char_p, u32], u32),
    'ILCT_TiStatuses': ([P(Unit), u32, i32, u32, P(u8), P(u8), P(u16)], u32),
    'ILCT_TiFade': ([P(View), u64], u32), 'ILCT_TiCompose': ([P(View), u64, P(th.Frame)], u32),
    'ILCT_TiHideNative': ([u32, u32, u32, u64, u64, u64, u32], i32), 'ILCT_TiSetP1Owned': ([i32], None), 'ILCT_TiHeaderBox': ([u32, u32, u32, P(i32)], None),
    'ILCT_TiBuild': ([P(Snapshot), u64, P(View)], i32), 'ILCT_TiPublish': ([P(View)], None),
    'ILCT_TiLatest': ([P(View)], i32), 'ILCT_TiDraw': ([P(Snapshot), u64, P(th.Frame)], u32),
    'ILCT_TiHandler': ([P(th.HookCtx), u32], i32), 'ILCT_TiItemName': ([u32, ctypes.c_char_p, u32], i32),
    'ILCT_TiFindHi': ([P(u64), P(u64)], u32), 'ILCT_TiStats': ([P(Counters)], None),
    'ILCT_TiHiCode': ([ctypes.c_void_p, u32, u64, i32], u32), 'ILCT_TiHiOffsets': ([], u32),
    'ILCT_TiStatus': ([P(Status)], None), 'ILCT_CtlReset': ([], None), 'ILCT_CtlState': ([], u32),
}
GbCounters = S('GbCounters', [(n, u64) for n in ('opens', 'opened', 'cancels', 'giveups', 'superseded', 'rehides',
                                                 'pending_cleared', 'rejected', 'pin_checks', 'pin_off', 'aims', 'gated')])
GbStatus = S('GbStatus', [('pins', u32), ('pin_row', i32), ('phase', u8), ('live', u8), ('reserved', u8 * 2),
                          ('target', u32)])
SIGNATURES.update({
    'ILCT_TiGbSizes': ([P(u32), u32], u32), 'ILCT_TiGbReset': ([], None), 'ILCT_TiGbSetImageBase': ([u64], None),
    'ILCT_TiGbForcePins': ([i32], None), 'ILCT_TiGbForceLive': ([i32], None), 'ILCT_TiGbForceGate': ([i32], None), 'ILCT_TiGbGateOpen': ([], i32), 'ILCT_TiGbSample': ([u64], None),
    'ILCT_TiGbTick': ([u64, u32], None), 'ILCT_TiGbPinRow': ([u32], i32), 'ILCT_TiGbLiveNow': ([u64], i32),
    'ILCT_TiGbAim': ([u64], u32), 'ILCT_TiGbStats': ([P(GbCounters)], None), 'ILCT_TiGbStatus': ([P(GbStatus)], None),
})
_dll: dict = {}


def shared_dll():
    if 'd' not in _dll:
        d = ctypes.WinDLL(str(pe.built()['test']))
        for name, (args, res) in SIGNATURES.items():
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, res
        _dll['d'] = d
    return _dll['d']


# ---- game text (single-byte encoding) ------------------------------------------------------------------------------
INV = {v: k for k, v in th.CHARS.items()}
INV.update({2: ' ', 115: 'e', 129: 'u'})


def decode_game(raw: bytes) -> str:
    """The reference decoder: control tokens skipped, unknown glyphs '?', spaces collapsed, trimmed."""
    out, i = [], 0
    while i < len(raw) and raw[i] != 0:
        b = raw[i]
        if b == 0x0F:
            count = raw[i + 2] & 7 if i + 2 < len(raw) and raw[i + 1] == 0x29 else -1
            i += 4 if count <= 0 else 3 + count
            continue
        i += 1
        if b == 3:
            continue
        c = INV.get(b, '?')
        if c == ' ' and (not out or out[-1] == ' '):
            continue
        out.append(c)
    return ''.join(out).rstrip(' ')


def frame_texts(frame) -> list[tuple[int, int, str, int]]:
    """(x, y, text, colour) of every text command, decoded."""
    pool = ctypes.string_at(ctypes.addressof(frame) + th.Frame.text.offset, th.TEXT_POOL)
    out = []
    for k in range(frame.count):
        c = frame.cmds[k]
        if c.kind == th.CMD_TEXT:
            out.append((c.x, c.y, ''.join(INV.get(b, '?') for b in pool[c.text:].split(b'\0', 1)[0]), c.colour))
    return out


def frame_kind(frame, kind) -> list:
    return [frame.cmds[k] for k in range(frame.count) if frame.cmds[k].kind == kind]


def unit(handle=0x00300005, hp=1644, hp_max=2782, level=18, weakness=0x21, flags=UNIT_PRESENT | UNIT_ACTOR,
         timed=0, lasting=0, durations=None) -> Unit:
    u = Unit()
    u.handle, u.member, u.flags, u.hp, u.hp_max, u.level, u.weakness = handle, 0xFFFF, flags, hp, hp_max, level, weakness
    u.status_timed, u.status_lasting = timed, lasting
    for k, v in (durations or {}).items():
        u.duration[k] = v
    return u


def snap_with(targets, seats, flags=SNAP_WORLD | SNAP_PARTY | SNAP_LIBRA_KNOWN | SNAP_TICKS, members=(),
              tick_scale=1) -> Snapshot:
    s = tdrv.snapshot(flags=flags)
    s.tick_scale = tick_scale
    for k, m in enumerate(members):
        s.members[k] = m
    s.member_count = len(members)
    for k, t in enumerate(targets):
        s.targets[k] = t
    s.target_count = len(targets)
    for k, (handle, since) in enumerate(seats):
        s.seats[k].handle, s.seats[k].since_ms, s.seats[k].on_page = handle, since, 1 if handle else 0
    return s


def loot_record(drops=(0xFFFF,) * 5, steals=(0xFFFF,) * 3, poaches=(0xFFFF,) * 2, special=(0xFFFF, 0xFFFF)) -> bytes:
    b = bytearray(b'\xff' * 0x50)
    for k, v in enumerate(drops):
        struct.pack_into('<H', b, 0x28 + 2 * k, v)
    for k, v in enumerate(steals):
        struct.pack_into('<H', b, 0x32 + 2 * k, v)
    for k, v in enumerate(poaches):
        struct.pack_into('<H', b, 0x44 + 2 * k, v)
    struct.pack_into('<H', b, 0x4A, special[0])
    struct.pack_into('<H', b, 0x4E, special[1])
    return bytes(b)


# ---- the game's text and item tables in memory (relocated as the battle-pack loader does) ---------------------------
def text_section(groups: list[list[bytes]]) -> bytes:
    """File form of section 61: u32 groups, u32 header size, u32 group ends; each group: u32 count, u32 offsets
    [count + 1], strings with a 2-byte prefix."""
    blobs = []
    for strings in groups:
        n = len(strings)
        head = 4 + 4 * (n + 1)
        body, offsets = b'', []
        for s in strings:
            offsets.append(head + len(body))
            body += b'\x00\x00' + s + b'\x00'
        offsets.append(head + len(body))
        blob = struct.pack('<I', n) + struct.pack(f'<{n + 1}I', *offsets) + body
        blobs.append(blob + b'\x00' * (-len(blob) % 4))
    count = len(groups)
    header = 8 + 4 * count
    ends, at = [], header
    for blob in blobs:
        at += len(blob)
        ends.append(at)
    return struct.pack('<II', count, header) + struct.pack(f'<{count}I', *ends) + b''.join(blobs)


def relocate_text(sec: bytes, address: int) -> bytes:
    """What 0x3846F0(global, section, depth 1) leaves in memory, the section placed at `address`."""
    b = bytearray(sec)
    count = struct.unpack_from('<H', b, 0)[0]
    for k in range(count + 1):
        rel = struct.unpack_from('<I', b, 4 + 4 * k)[0]
        struct.pack_into('<I', b, 4 + 4 * k, address + rel)
        if k == count:
            break
        g = rel
        n = struct.unpack_from('<H', b, g)[0]
        for e in range(n + 1):
            r = struct.unpack_from('<I', b, g + 4 + 4 * e)[0]
            struct.pack_into('<I', b, g + 4 + 4 * e, address + g + r)
    return bytes(b)


def st2e_table(rows: list[bytes], stride: int) -> bytes:
    body = b''.join(r.ljust(stride, b'\0')[:stride] for r in rows)
    return b'st2e' + struct.pack('<IHHI', len(rows), stride, 0, 32) + b'\0' * 16 + body


def relocate_st2e(sec: bytes, address: int) -> bytes:
    b = bytearray(sec)
    struct.pack_into('<I', b, 12, address + struct.unpack_from('<I', b, 12)[0])
    return bytes(b)


class TextWorld:
    """Section 61 and the item tables below 4 GiB, their globals in a synthetic image."""

    def __init__(self, img: Image, text: bytes, tables: dict[int, bytes]):
        self.blocks = []
        self.img = img
        at = self.place(text)
        img.put_global(NAME_TEXT, struct.pack('<I', at))
        ctypes.memmove(at, relocate_text(text, at), len(text))
        img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0))
        for global_va, sec in tables.items():
            t = self.place(sec)
            ctypes.memmove(t, relocate_st2e(sec, t), len(sec))
            img.put_global(global_va, struct.pack('<Q', t))

    def place(self, data: bytes) -> int:
        at = alloc_low((len(data) + 0xFFF) & ~0xFFF)
        self.blocks.append(at)
        ctypes.memmove(at, data, len(data))
        return at

    def close(self):
        for at in self.blocks:
            free(at)


def encode(text: str) -> bytes:
    return th.encoded(text)


def link(group: int, entry: int) -> int:
    return group << 11 | entry


# ---- static ------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_ti01_row(self):
        rows = {r['id']: r for r in pe.site_rows()}
        r = rows['TI-01']
        self.assertEqual((r['rva'], r['length'], r['kind'], r['policy'], r['subsystem']),
                         (TI_OPEN_CALL - 0x120000, 5, 'CALL', 'PATCH', 'RENDER'))
        self.assertEqual(r['expected'], bytes.fromhex('e8ed000000'))
        self.assertTrue(r['flags'] & 0x08, 'OPTIONAL: another mod owning the call skips the row, refuses nothing')
        legacy = (COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8')
        self.assertIn('"id": "TI-01"', legacy)
        self.assertIn('"site_offset": 15, "hits_in_text": 1', legacy)

    def test_address_constants_are_tagged(self):
        for name in ('target_info_facts.h', 'target_info.h', 'target_info.cpp', 'target_info_sample.cpp'):
            for n, line in enumerate((HUD_SRC / name).read_text(encoding='utf-8').splitlines(), 1):
                self.assertEqual(pe.untagged_constants(line), [], f'{name}:{n}: {line.strip()}')

    def test_facts_restated(self):
        facts = (HUD_SRC / 'target_info_facts.h').read_text(encoding='utf-8')
        for needle in ('TI_OPEN_CALL = 0x2BE20E', 'ANTI_LIBRA_TEST = 0x2BFFAA', 'HI_HOOK = 0x2C0ACF',
                       'RESOURCE_BASE_PTR = 0x1F83530', 'NAME_TEXT = 0x2EBF170', 'ACTOR_LOOT = 0xE60',
                       'ACTOR_CLASS = 0xE68', 'CLASS_BESTIARY = 0x52', 'ACTOR_EXT = 0x6A0', 'EXT_STEAL_LEFT = 0x14',
                       'REC_STATUS_BLOCK = 0x38', 'UNIT_NAME = 0xF8', 'HI_SAVE_COUNT = 0x200'):
            self.assertIn(needle, facts)
        for first, stride, link_off, global_va, _section in ITEM_TABLES:
            self.assertIn(f'{{0x{first:04X}, {stride}, {link_off}, 0, 0x{global_va:X}}}', facts)
        # The same globals the menu catalog reads (section 18 and section 61).
        menu = (COOP / 'src' / 'menu' / 'menu_facts.h').read_text(encoding='utf-8')
        self.assertIn('ITEM_TABLE_PTR = 0x2EBF100', menu)
        self.assertIn('NAME_TEXT_PTR = 0x2EBF170', menu)
        self.assertIn('RESOURCE_BASE_PTR = 0x1F83530', menu)

    def test_game_thread_code_never_blocks_allocates_or_calls_the_c_runtime(self):
        text = (HUD_SRC / 'target_info.cpp').read_text(encoding='utf-8')
        for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                          'copy_to_caller', 'memcpy(', 'memset(', 'session::read', 'game_reader', 'snprintf'):
            self.assertNotIn(forbidden, text, forbidden)
        sample = (HUD_SRC / 'target_info_sample.cpp').read_text(encoding='utf-8')
        for forbidden in ('memory_write', 'VirtualProtect', 'WriteProcessMemory', 'Sleep', 'WaitFor'):
            self.assertNotIn(forbidden, sample, forbidden)

    def test_wiring(self):
        driver = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        body = driver[driver.index('void pass_target_info'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('target_info::draw(cv, in);', body)
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        self.assertLess(boot.index('hud::snapshot::step(now);'), boot.index('hud::target_info::step(now);'))
        self.assertLess(boot.index('hud::target_info::attach('), boot.index('gtq::seal();'))
        self.assertIn('hud::target_info::stop();', boot)
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("'src/hud/target_info.cpp', 'src/hud/target_info_sample.cpp'", build)

    def test_the_lua_scaler_and_hunters_insight_are_never_written(self):
        for name in ('target_info.cpp', 'target_info_sample.cpp', 'target_info_facts.h'):
            code = (HUD_SRC / name).read_text(encoding='utf-8')
            self.assertNotIn('0x9FD0', code, 'the native window is never read or written')
            self.assertNotIn('write_game', code)
            self.assertNotIn('patch_bytes', code)


@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the pinned exe, read only)')
class PinnedBytesTest(unittest.TestCase):
    """The facts, checked against the pinned exe (static: capstone over .text)."""

    @classmethod
    def setUpClass(cls):
        from capstone import CS_ARCH_X86, CS_MODE_64, Cs
        exe = Path(GAME) / 'x64' / 'FFXII_TZA.exe'
        cls.data = exe.read_bytes()
        cls.md = Cs(CS_ARCH_X86, CS_MODE_64)
        e = struct.unpack_from('<I', cls.data, 0x3C)[0]
        nsec = struct.unpack_from('<H', cls.data, e + 6)[0]
        opt = struct.unpack_from('<H', cls.data, e + 20)[0]
        cls.sections = []
        for k in range(nsec):
            s = e + 24 + opt + 40 * k
            vsize, va, rsize, raw = struct.unpack_from('<IIII', cls.data, s + 8)
            cls.sections.append((cls.data[s:s + 8].rstrip(b'\0'), va, vsize, raw, rsize))

    def at(self, va: int, n: int) -> bytes:
        rva = va - 0x120000
        for _name, sva, vsize, raw, rsize in self.sections:
            if sva <= rva < sva + max(vsize, rsize):
                return self.data[raw + rva - sva: raw + rva - sva + n]
        raise AssertionError(hex(va))

    def dis(self, va: int, n: int):
        return list(self.md.disasm(self.at(va, n * 15), va))[:n]

    def test_ti01_is_the_only_call_to_the_set_routine(self):
        self.assertEqual(self.at(TI_OPEN_CALL, 5), bytes.fromhex('e8ed000000'))
        text = next(s for s in self.sections if s[0] == b'.text')
        _n, va, vsize, raw, _r = text
        body = self.data[raw:raw + vsize]
        callers = []
        for m in re.finditer(rb'[\xe8\xe9]', body):
            k = m.start()
            if k + 5 <= len(body):
                site = 0x120000 + va + k
                if site + 5 + struct.unpack_from('<i', body, k + 1)[0] == TI_SET:
                    callers.append(site)
        self.assertEqual(callers, [TI_OPEN_CALL])

    def test_every_call_here_is_an_open_and_the_close_request_creates_nothing(self):
        caller = [(i.address, i.mnemonic, i.op_str) for i in self.dis(TI_CALLER, 16)]
        # 0x2BE1A0: r8d != 0 -> continue; r9d < 0 -> return before TI-01 (so TI-01 never carries a close).
        self.assertIn((0x2BE1BF, 'test', 'r8d, r8d'), caller)
        self.assertIn((0x2BE1C4, 'test', 'r9d, r9d'), caller)
        self.assertIn((0x2BE1C7, 'js', '0x2be213'), caller)
        setr = [(i.address, i.mnemonic, i.op_str) for i in self.dis(TI_SET, 30)]
        self.assertIn((0x2BE30F, 'mov', 'esi, r8d'), setr)
        self.assertIn((0x2BE31B, 'test', 'r9d, r9d'), setr)
        self.assertIn((0x2BE31E, 'jns', '0x2be36a'), setr)
        self.assertIn((0x2BE320, 'test', 'esi, esi'), setr)
        self.assertIn((0x2BE322, 'jne', '0x2be36a'), setr)
        # The close path (0x2BE324..0x2BE365) ends in the close routine and never reaches the creator 0x2C0F30.
        close = [i for i in self.dis(0x2BE324, 15)]
        self.assertEqual((close[-1].mnemonic, close[-1].op_str), ('jmp', '0x2bea40'))
        self.assertFalse(any('0x2c0f30' in i.op_str for i in close))

    def test_discovery_sites_are_stock_in_the_pinned_exe(self):
        self.assertEqual(self.at(ANTI_LIBRA_TEST, 7), ANTI_LIBRA_STOCK)
        self.assertEqual(self.at(HI_HOOK, 5), HI_HOOK_STOCK)

    def test_the_loader_registers_the_section_globals(self):
        # 0x236C30: lea rcx,[rip+d] before each section's registration; section n is at header +4 * (n + 1).
        loader = {(i.address, i.op_str) for i in self.dis(0x236C30, 180) if i.mnemonic == 'lea'}
        for va, section, global_va in ((0x236DCE, 18, 0x2EBF100), (0x236E72, 32, 0x2EBF098),
                                       (0x236FD6, 61, NAME_TEXT)):
            ins = self.dis(va, 1)[0]
            self.assertEqual(ins.mnemonic, 'lea')
            self.assertEqual(ins.op_str, f'rcx, [rip + 0x{global_va - (va + 7):x}]', section)
            self.assertIn((va, ins.op_str), loader)
        # Section 13 goes through 0x238160, whose first registration is the equipment table global.
        ins = self.dis(0x238169, 1)[0]
        self.assertEqual(ins.op_str, f'rcx, [rip + 0x{0x2EBF0F8 - 0x238170:x}]')


# ---- pure rules -------------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_TiReset()

    def test_engaged(self):
        """0.1.60 item 6 (Marco: "At least just while you are engaged with an enemy")."""
        d = self.d
        foe = unit(handle=FOE_CLASS | 5)
        foe.unit_type = 1
        npc = unit(handle=FOE_CLASS | 6)
        npc.unit_type = 3
        vaan = unit(handle=0x00100001, flags=UNIT_PRESENT | UNIT_ACTOR | UNIT_PARTY)
        vaan.member = 0

        def engaged(seats=((0, 0),) * 4, members=(vaan,), targets=(foe, npc), stance=0, aim=0):
            s = snap_with(list(targets), list(seats), members=list(members))
            s.seats[0].native_aim = aim
            return d.ILCT_TiEngaged(ctypes.byref(s), stance)

        self.assertEqual(engaged(), 0, 'nobody aims, nobody in stance, nobody targets: the sticky target is gone')
        self.assertEqual(engaged(seats=((foe.handle, NOW),) + ((0, 0),) * 3), 1, 'P1 on a target page, on a foe')
        self.assertEqual(engaged(seats=((0, 0), (foe.handle, NOW), (0, 0), (0, 0))), 1, 'a guest seat aims')
        self.assertEqual(engaged(seats=((npc.handle, NOW),) + ((0, 0),) * 3), 0, 'aiming at a neutral is no fight')
        s = snap_with([foe], [(foe.handle, NOW)] + [(0, 0)] * 3, members=[vaan])
        s.seats[0].on_page = 0
        self.assertEqual(d.ILCT_TiEngaged(ctypes.byref(s), 0), 0, 'the remembered last target alone does not count')
        s.seats[0].native_aim = 1
        self.assertEqual(d.ILCT_TiEngaged(ctypes.byref(s), 0), 1, "P1's native target mode aim counts")
        self.assertEqual(engaged(stance=0b1), 1, 'a party member in fighting stance (actor +0x02 bit 7)')
        fighter = unit(handle=0x00100001, flags=UNIT_PRESENT | UNIT_ACTOR | UNIT_PARTY)
        fighter.target = foe.handle
        self.assertEqual(engaged(members=(fighter,)), 1, 'a party member targets a foe')
        fighter.target = npc.handle
        self.assertEqual(engaged(members=(fighter,)), 0)
        hunter = unit(handle=FOE_CLASS | 7)
        hunter.unit_type, hunter.target = 7, vaan.handle
        self.assertEqual(engaged(targets=(hunter,)), 1, 'a foe targets the party')
        gone = unit(handle=FOE_CLASS | 5, flags=UNIT_ACTOR)
        gone.unit_type = 1
        self.assertEqual(engaged(seats=((gone.handle, NOW),) + ((0, 0),) * 3, targets=(gone,)), 0, 'not present')

    def test_struct_mirrors(self):
        out = (u32 * 16)()
        n = self.d.ILCT_TiSizes(out, 16)
        self.assertEqual(list(out[:n]), [ctypes.sizeof(View), ctypes.sizeof(DropLine), ctypes.sizeof(Counters),
                                         ctypes.sizeof(Status), ctypes.sizeof(Snapshot), ctypes.sizeof(th.Frame),
                                         ctypes.sizeof(Ghost), View.handle.offset, View.seat_colour.offset,
                                         View.status_id.offset, View.name.offset, View.drops.offset])
        self.assertLessEqual(ctypes.sizeof(View), 512)

    def test_libra_gating_table(self):
        """The native rule restated: every input combination."""
        for party in (0, 1):
            for known in (0, 1):
                for libra in (0, 1):
                    for anti in (0, 1):
                        for stock in (0, 1):
                            with self.subTest(party=party, known=known, libra=libra, anti=anti, stock=stock):
                                if party:
                                    want = VIEW_REVEAL
                                elif anti and stock:
                                    want = VIEW_ANTI_LIBRA
                                elif known and libra:
                                    want = VIEW_REVEAL | VIEW_LIBRA
                                else:
                                    want = 0
                                self.assertEqual(self.d.ILCT_TiGate(party, known, libra, anti, stock), want)

    def test_libra_gating_named_cases(self):
        g = self.d.ILCT_TiGate
        self.assertEqual(g(0, 1, 0, 0, 1), 0, 'a foe without Libra: gated rows hidden')
        self.assertEqual(g(0, 1, 1, 0, 1), VIEW_REVEAL | VIEW_LIBRA, 'Libra reveals a foe (badge)')
        self.assertEqual(g(0, 1, 1, 1, 1), VIEW_ANTI_LIBRA, 'stock Anti-Libra beats Libra (red X)')
        self.assertEqual(g(0, 1, 1, 1, 0), VIEW_REVEAL | VIEW_LIBRA, "TIBB's noAntiLibra: Anti-Libra reads absent")
        self.assertEqual(g(0, 0, 1, 0, 1), 0, 'Libra not known yet: absent')
        self.assertEqual(g(1, 0, 0, 1, 1), VIEW_REVEAL, 'a party target always reveals, no badge')

    def test_last_targeter(self):
        a, b, c = unit(0x00300005), unit(0x00300006), unit(0x00300007)
        sel = lambda s: (self.d.ILCT_TiSelect(ctypes.byref(s), ctypes.byref(idx)), idx.value)  # noqa: E731
        idx = i32()
        self.assertEqual(sel(snap_with([a], [])), (-1, -1), 'nobody targeted')
        self.assertEqual(sel(snap_with([a], [(a.handle, NOW)])), (0, 0))
        self.assertEqual(sel(snap_with([a, b], [(a.handle, NOW - 50), (b.handle, NOW)])), (1, 1), 'P2 most recent')
        self.assertEqual(sel(snap_with([a, b], [(a.handle, NOW), (b.handle, NOW - 50)])), (0, 0), 'P1 most recent')
        self.assertEqual(sel(snap_with([a, b], [(a.handle, NOW), (b.handle, NOW)])), (0, 0), 'tie: lower seat')
        self.assertEqual(sel(snap_with([a, c], [(0, 0), (0, 0), (c.handle, NOW - 900), (b.handle, NOW)])), (2, 1),
                         'the most recent seat whose target is not in the snapshot is skipped')
        gone = unit(0x00300006, flags=0)
        self.assertEqual(sel(snap_with([a, gone], [(a.handle, NOW - 10), (gone.handle, NOW)])), (0, 0),
                         'a target that was not read coherently is skipped')
        self.assertEqual(sel(snap_with([a, b], [(0, 0), (0, 0), (0, 0), (b.handle, NOW)])), (3, 1), 'P4')

    def drops(self, loot, known, steal_left=0x7, names=1, cap=8):
        out = (DropLine * 8)()
        n = self.d.ILCT_TiBuildDrops(loot, known, steal_left, names, out, cap)
        return [(d.kind, d.state, d.slot, d.flags, d.text.decode()) for d in out[:n]]

    def test_drops_discovered_and_undiscovered(self):
        loot = loot_record(drops=(0x2001, 0xFFFF, 0x2003, 0x1020, 0xE064), steals=(0x0000, 0x2002, 0xFFFF),
                           poaches=(0x2010, 0xFFFF), special=(0xFFFF, 0x2011))
        none = bytes([0, 0, 0, 0])
        self.assertEqual(self.drops(loot, none), [
            (DROP, UNKNOWN, 0, 0, '???'), (DROP, UNKNOWN, 2, RARE, '???'), (DROP, UNKNOWN, 3, RARE, '???'),
            (DROP, UNKNOWN, 4, GIL, '???'), (STEAL, UNKNOWN, 0, 0, '???'), (STEAL, UNKNOWN, 1, 0, '???'),
            (SPECIAL, UNKNOWN, 1, 0, '???'), (POACH, UNKNOWN, 0, 0, '???')], 'nothing obtained yet: every slot ???')
        # Drops 0 and 4 obtained, steal 1, the Canopic drop, no poach.
        known = bytes([0b010, 0b10001, 0b10, 0b00])
        self.assertEqual(self.drops(loot, known), [
            (DROP, KNOWN, 0, 0, 'Item 2001'), (DROP, UNKNOWN, 2, RARE, '???'), (DROP, UNKNOWN, 3, RARE, '???'),
            (DROP, KNOWN, 4, GIL, '100 Gil'), (STEAL, UNKNOWN, 0, 0, '???'), (STEAL, KNOWN, 1, 0, 'Item 2002'),
            (SPECIAL, KNOWN, 1, 0, 'Item 2011'), (POACH, UNKNOWN, 0, 0, '???')])
        # Steal 1 already taken from this foe: dimmed; slot 0 still there.
        taken = self.drops(loot, known, steal_left=0b101)
        self.assertEqual([d[3] & TAKEN for d in taken if d[0] == STEAL], [0, TAKEN])
        # HI's "always show": every slot named.
        self.assertTrue(all(d[1] == KNOWN for d in self.drops(loot, b'\xff' * 4)))

    def test_drops_edge_cases(self):
        self.assertEqual(self.drops(loot_record(), b'\xff' * 4), [], 'an empty record: no lines')
        self.assertEqual(self.drops(loot_record(drops=(0x2FFF,) + (0xFFFF,) * 4), b'\xff' * 4),
                         [(DROP, UNKNOWN, 0, 0, '???')], 'known but its name unreadable: ??? (never invented)')
        self.assertEqual(self.drops(loot_record(drops=(0x2001,) + (0xFFFF,) * 4), b'\xff' * 4, names=0),
                         [(DROP, UNKNOWN, 0, 0, '???')], 'no name source')
        full = loot_record(drops=(0x2001,) * 5, steals=(0x2002,) * 3, poaches=(0x2003,) * 2, special=(0x2004,) * 2)
        lines = self.drops(full, b'\xff' * 4)
        self.assertEqual(len(lines), 8, 'at most 8 lines')
        self.assertEqual([d[0] for d in lines], [DROP] * 5 + [STEAL] * 3, 'drops first, then steals')
        self.assertEqual(self.drops(full, b'\xff' * 4, cap=3), lines[:3])
        out = (DropLine * 8)()
        self.assertEqual(self.d.ILCT_TiBuildDrops(full, None, 7, 1, out, 8), 0, 'no knowledge source: no drops')

    def decode(self, raw: bytes, cap=64) -> str:
        out = ctypes.create_string_buffer(cap)
        n = self.d.ILCT_TiDecode(raw, len(raw), out, cap)
        self.assertEqual(n, len(out.value))
        return out.value.decode()

    def test_decode_text(self):
        for text in ('Hyena C', 'Hi-Potion', "Dragon's Scale (Rare): 3/4 & 50% !?", 'Wyrm Bone', 'Ether 2'):
            with self.subTest(text):
                self.assertEqual(self.decode(encode(text)), text)
        self.assertEqual(self.decode(b'\x0f\x29\x83\x80\x80\x80' + encode('Red') + b'\x0f\x29\x80\x80'), 'Red',
                         'colour tokens skipped')
        self.assertEqual(self.decode(b'\x0f\x01\x02\x03' + encode('Ok')), 'Ok', 'other control tokens: 4 bytes')
        self.assertEqual(self.decode(encode('A') + bytes([0xF0]) + encode('B')), 'A?B', 'unknown glyph')
        self.assertEqual(self.decode(b'\x04\x04' + encode('Name') + b'\x02\x04\x04'), 'Name', 'spaces trimmed')
        self.assertEqual(self.decode(encode('Abc') + b'\x00' + encode('Def')), 'Abc', 'NUL ends')
        self.assertEqual(self.decode(encode('Longname'), cap=5), 'Long', 'capped with the NUL')
        for raw in (encode('Pok') + bytes([115]) + encode('mon'), bytes(range(1, 255))):
            self.assertEqual(self.decode(raw, cap=300), decode_game(raw))

    def statuses(self, u, block=0, show=True, scale=1):
        ids, flags, secs = (u8 * 10)(), (u8 * 10)(), (u16 * 10)()
        n = self.d.ILCT_TiStatuses(ctypes.byref(u), block, 1 if show else 0, scale, ids, flags, secs)
        return [(ids[k], flags[k], secs[k]) for k in range(n)]

    def test_statuses(self):
        poison, slow, protect, stop, sleep, ko, crit, libra = 8, 15, 18, 3, 4, 0, 29, 30
        u = unit(timed=1 << poison | 1 << slow | 1 << libra | 1 << crit, lasting=1 << protect | 1 << ko,
                 durations={poison: 14 * 300, slow: 5 * 300 + 1, protect: 999})
        self.assertEqual(self.statuses(u), [(poison, ST_ACTIVE | ST_TIMED, 14), (slow, ST_ACTIVE | ST_TIMED, 6),
                                            (protect, ST_ACTIVE, 0)], 'KO, HP Critical and Libra left out')
        self.assertEqual(self.statuses(u, scale=2), [(poison, ST_ACTIVE | ST_TIMED, 7),
                                                     (slow, ST_ACTIVE | ST_TIMED, 3), (protect, ST_ACTIVE, 0)],
                         '60 fps timing: twice the ticks per second')
        self.assertEqual(self.statuses(u, scale=0)[0], (poison, ST_ACTIVE, 0), 'no tick scale: no seconds')
        block = 1 << stop | 1 << sleep | 1 << poison
        self.assertEqual(self.statuses(u, block=block)[3:], [(stop, ST_IMMUNE, 0), (sleep, ST_IMMUNE, 0)],
                         'immune and not active, greyed after the active ones')
        self.assertEqual(len(self.statuses(u, block=block, show=False)), 3, 'immune statuses need Libra')
        many = unit(timed=0x0FFFFFFE)
        self.assertEqual(len(self.statuses(many, block=0xFFFFFFFF)), 10, 'at most 10')

    def test_ghost(self):
        g = Ghost()
        step = lambda hp, t, h=7: self.d.ILCT_TiGhost(ctypes.byref(g), h, hp, t)  # noqa: E731
        self.assertEqual(step(1000, 0), 1000)
        self.assertEqual(step(600, 100), 1000, 'a hit: the ghost holds')
        self.assertEqual(step(600, 350), 1000, 'still holding at 250 ms')
        self.assertEqual(step(600, 550), 800, 'eases over 400 ms')
        self.assertEqual(step(500, 560), 800, 'another hit restarts the hold from where the ghost is')
        self.assertEqual(step(500, 1300), 500, 'settled')
        self.assertEqual(step(900, 1310), 900, 'a heal: the ghost follows at once')
        self.assertEqual(step(100, 1320, h=8), 100, 'a new target starts without a ghost')

    def test_fade(self):
        v = View()
        v.flags = VIEW_SHOWN
        self.assertEqual(self.d.ILCT_TiFade(ctypes.byref(v), NOW), 0x80)
        v.flags, v.fade_from_ms = VIEW_SHOWN | VIEW_KO, NOW
        self.assertEqual([self.d.ILCT_TiFade(ctypes.byref(v), NOW + t) for t in (0, 150, 300)], [0x80, 0x40, 0])
        v.flags = VIEW_SHOWN | VIEW_GONE
        self.assertEqual([self.d.ILCT_TiFade(ctypes.byref(v), NOW + t) for t in (0, 750, 900, 1050)],
                         [0x80, 0x80, 0x40, 0])
        v.flags = 0
        self.assertEqual(self.d.ILCT_TiFade(ctypes.byref(v), NOW), 0)

    def test_hide_native_table(self):
        def h(*a, owned=1):
            return self.d.ILCT_TiHideNative(*a, owned)
        self.assertEqual(h(1, 1, 1, NOW, NOW, NOW), 1)
        self.assertEqual(h(1, 1, 1, NOW, NOW - LIVE_MS, NOW - LIVE_MS), 1, '250 ms still live')
        self.assertEqual(h(0, 1, 1, NOW, NOW, NOW), 0, 'element off')
        self.assertEqual(h(1, 0, 1, NOW, NOW, NOW), 0, 'co-op off (kill switch)')
        self.assertEqual(h(1, 1, 0, NOW, NOW, NOW), 0, 'not attached')
        self.assertEqual(h(1, 1, 1, NOW, NOW - LIVE_MS - 1, NOW), 0, 'the pass stopped drawing')
        self.assertEqual(h(1, 1, 1, NOW, NOW, NOW - LIVE_MS - 1), 0, 'the menu layer is not presented')
        self.assertEqual(h(1, 1, 1, NOW, 0, NOW), 0, 'never drawn')
        self.assertEqual(h(1, 1, 1, NOW, NOW + LIVE_MS + 1, NOW), 0, 'a stamp from the future is not fresh')
        self.assertEqual(h(1, 1, 1, NOW, NOW, NOW, owned=0), 0,
                         'P1 on the stock battle menu: its target list keeps the native window')


# ---- drawing --------------------------------------------------------------------------------------------------------
def a_view(reveal=True, libra=True, anti=False, seat=2, drops=True, ko=False) -> View:
    v = View()
    v.epoch, v.generation, v.built_ms = EPOCH, 1, NOW
    v.flags = VIEW_SHOWN | VIEW_NAME | VIEW_HP | VIEW_TIMERS | (VIEW_DROPS if drops else 0)
    if reveal:
        v.flags |= VIEW_REVEAL | (VIEW_LIBRA if libra else 0)
    if anti:
        v.flags |= VIEW_ANTI_LIBRA
    if ko:
        v.flags |= VIEW_KO
        v.fade_from_ms = NOW
    v.handle, v.seat, v.seat_colour = 0x00300005, seat, th.rgba(0x23, 0x41, 0x78)
    v.level, v.weakness, v.hp, v.hp_max, v.ghost = 18, 0b100001, 1644, 2782, 1780
    v.name = b'Hyena C'
    statuses = [(8, ST_ACTIVE | ST_TIMED, 14), (15, ST_ACTIVE, 0), (4, ST_IMMUNE, 0)]
    for k, (i, f, s) in enumerate(statuses):
        v.status_id[k], v.status_flags[k], v.status_secs[k] = i, f, s
    v.status_count = len(statuses)
    lines = [(DROP, KNOWN, 0, 0, b'Fire Stone'), (DROP, UNKNOWN, 1, 0, b'???'), (DROP, KNOWN, 3, RARE, b'Green Beret'),
             (STEAL, KNOWN, 0, TAKEN, b'Hi-Potion')]
    for k, (kind, state, slot, flags, text) in enumerate(lines):
        d = v.drops[k]
        d.kind, d.state, d.slot, d.flags, d.text = kind, state, slot, flags, text
    v.drop_count = len(lines)
    return v


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class DrawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_TiReset()

    def compose(self, v, now=NOW):
        f = th.Frame()
        n = self.d.ILCT_TiCompose(ctypes.byref(v), now, ctypes.byref(f))
        self.assertEqual(n, f.count)
        return f

    def test_revealed_layout(self):
        f = self.compose(a_view())
        texts = frame_texts(f)
        by_text = {t[2]: t for t in texts}
        self.assertEqual(by_text['TARGET INFO'][:2], (MARGIN_X, HEADER_Y), 'the header on the corner line')
        self.assertIn('Lv18', by_text)
        self.assertEqual(by_text['Hyena C'][:2], (MARGIN_X + 64, HEADER_Y + 28))
        self.assertIn('1644 / 2782', by_text)
        self.assertIn('60%', by_text, 'percentage rounded up (a living foe never reads 0%)')
        self.assertIn('Weak', by_text)
        self.assertIn('Status', by_text)
        self.assertIn('14', by_text, 'the timed status shows its seconds')
        for name in ('DROPS', 'Fire Stone', '???', 'Green Beret', 'Hi-Potion', 'steal'):
            self.assertIn(name, by_text)
        bars = frame_kind(f, th.CMD_BAR)
        rule = bars[0]
        self.assertEqual((rule.x, rule.y, rule.w, rule.h), (MARGIN_X, HEADER_Y + 20, RULE_W, 2))
        hp = [b for b in bars if b.h == 6]
        self.assertEqual([b.w for b in hp], [260, 260 * 1780 // 2782, 260 * 1644 // 2782], 'back, ghost, HP')
        edge = [b for b in bars if b.w == 3]
        self.assertEqual(len(edge), 1)
        self.assertEqual((edge[0].x, edge[0].y, edge[0].colour), (MARGIN_X - 12, HEADER_Y, th.rgba(0x23, 0x41, 0x78)))
        sprites = frame_kind(f, th.CMD_SPRITE)
        elements = [(s.cell, s.w) for s in sprites if s.atlas == ATLAS_BATTLE_4_C and s.page == 10]
        self.assertEqual(elements, [(0, 26), (5, 26)], 'native element icons, one per weakness bit')
        status = [(s.cell, s.colour) for s in sprites if s.atlas == ATLAS_PARTYTOP_4_C and s.page == 3]
        self.assertEqual([c for c, _ in status], [30, 8, 15, 4], 'the Libra badge, then the status cells')
        self.assertEqual(status[3][1], th.rgba(0x40, 0x40, 0x44, 0x70), 'immune: greyed')
        self.assertLessEqual(f.count, 72)

    def test_without_libra(self):
        v = a_view(reveal=False)
        for k in range(v.status_count):
            if v.status_flags[k] & ST_IMMUNE:
                v.status_count = k   # the sampler leaves immune statuses out when not revealed
        texts = {t[2] for t in frame_texts(self.compose(v))}
        self.assertIn('Hyena C', texts)
        self.assertIn('Lv18', texts, 'level shows without Libra')
        self.assertIn('HP', texts)
        self.assertNotIn('1644 / 2782', texts, 'exact HP needs Libra')
        self.assertFalse(any(t.endswith('%') for t in texts))
        self.assertNotIn('Weak', texts, 'weaknesses need Libra')
        self.assertIn('Status', texts, 'statuses always')
        self.assertIn('Fire Stone', texts, 'drops are not Libra-gated')
        f = self.compose(v)
        self.assertEqual(len([b for b in frame_kind(f, th.CMD_BAR) if b.h == 6]), 3, 'the HP bar always shows')
        self.assertFalse([s for s in frame_kind(f, th.CMD_SPRITE) if s.page == 10], 'no element icons')
        self.assertFalse([s for s in frame_kind(f, th.CMD_SPRITE) if s.cell == 30 and s.page == 3], 'no badge')

    def test_anti_libra_mark(self):
        f = self.compose(a_view(reveal=False, anti=True))
        x = [s for s in frame_kind(f, th.CMD_SPRITE) if s.atlas == ATLAS_BATTLE_4_C and s.page == 13]
        self.assertEqual([(s.cell, s.x) for s in x], [(1, MARGIN_X + RULE_W - 30)], 'the native red X')

    def test_drops_hidden_without_a_knowledge_source(self):
        texts = {t[2] for t in frame_texts(self.compose(a_view(drops=False)))}
        self.assertNotIn('DROPS', texts)
        self.assertNotIn('Fire Stone', texts)

    def test_no_seat_no_edge(self):
        f = self.compose(a_view(seat=0))
        self.assertFalse([b for b in frame_kind(f, th.CMD_BAR) if b.w == 3])

    def test_fade_applies_and_ends(self):
        v = a_view(ko=True)
        half = self.compose(v, NOW + 150)
        full = self.compose(a_view())
        self.assertEqual(half.count, full.count)
        self.assertEqual(half.cmds[0].colour >> 24, (0x66 * 0x40) // 0x80, 'alpha scaled')
        self.assertEqual(self.compose(v, NOW + 300).count, 0, 'faded out: nothing')

    def test_header_line_matches_the_log_at_three_resolutions(self):
        box = (i32 * 4)()
        for w, h in ((1920, 1080), (1366, 768), (1280, 720)):
            with self.subTest(f'{w}x{h}'):
                self.d.ILCT_TiHeaderBox(0, w, h, box)
                left = tuple(box)
                self.d.ILCT_TiHeaderBox(1, w, h, box)
                right = tuple(box)
                self.assertEqual(left[1], right[1], 'one header line')
                self.assertEqual(left[3], right[3])
                self.assertLess(left[0] + left[2], right[0], 'the TI header ends before the log header starts')
                self.assertAlmostEqual(left[0], w - (right[0] + right[2]), delta=2, msg='mirrored margins')
                k = min(w / 1920, h / 1080)
                self.assertAlmostEqual(left[1], round(HEADER_Y * k), delta=1)
                self.assertAlmostEqual(left[0], round(MARGIN_X * k), delta=1)
        self.d.ILCT_TiHeaderBox(0, 0, 0, box)
        self.assertEqual(tuple(box), (MARGIN_X, HEADER_Y, RULE_W, 22), 'authoring units')
        self.d.ILCT_TiHeaderBox(1, 0, 0, box)
        self.assertEqual(tuple(box), (1920 - MARGIN_X - RULE_W, HEADER_Y, RULE_W, 22))


# ---- the pass and TI-01 ------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class PassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_TiReset()
        self.d.ILCT_CtlReset()
        self.d.ILCT_TiSetP1Owned(1)   # the co-op menus own P1 (the stock-menu tests clear it)

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_TiStats(ctypes.byref(c))
        return c

    def draw(self, snap, now=NOW):
        f = th.Frame()
        self.d.ILCT_TiDraw(ctypes.byref(snap) if snap is not None else None, now, ctypes.byref(f))
        return f

    def test_the_pass_draws_the_published_view_of_this_epoch(self):
        snap = tdrv.snapshot()
        self.assertEqual(self.draw(snap).count, 0, 'nothing published')
        self.d.ILCT_TiPublish(ctypes.byref(a_view()))
        self.assertGreater(self.draw(snap).count, 20)
        other = tdrv.snapshot(epoch=EPOCH + 1)
        self.assertEqual(self.draw(other).count, 0, 'a view of another epoch is never drawn')
        self.d.ILCT_TiSetEnabled(0)
        self.assertEqual(self.draw(snap).count, 0, 'element off')
        self.d.ILCT_TiSetEnabled(1)
        empty = View()
        empty.epoch = EPOCH
        self.d.ILCT_TiPublish(ctypes.byref(empty))
        self.assertEqual(self.draw(snap).count, 0, 'live but nothing to show')
        c = self.stats()
        self.assertEqual((c.drawn, c.skipped_epoch), (1, 2))

    def ctx(self) -> th.HookCtx:
        c = th.HookCtx()
        c.rcx, c.rdx, c.r8, c.r9 = 0x1234560, 0, 0x55, 0x00300005
        return c

    def test_native_ti_kept_closed_while_ours_is_live(self):
        self.assertEqual(self.d.ILCT_CtlState(), 0, 'co-op on')
        now = self.d.ILCT_TiNow()
        self.d.ILCT_TiSetAttached(1)
        self.d.ILCT_TiSetLast(now, now)
        c = self.ctx()
        self.assertEqual(self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH), 0, 'RUN_ORIGINAL with new arguments')
        self.assertEqual((c.rcx, c.rdx, c.r8, c.r9), (0x1234560, 0, 0, 0xFFFFFFFF),
                         'the close request: r8d = 0, r9d = -1 (owner and mode untouched)')
        self.assertEqual(self.stats().native_closed, 1)

    def test_toggle_off_the_native_ti_returns(self):
        now = self.d.ILCT_TiNow()
        self.d.ILCT_TiSetAttached(1)
        self.d.ILCT_TiSetLast(now, now)
        self.d.ILCT_TiSetEnabled(0)
        c = self.ctx()
        self.assertEqual(self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH), 0)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'stock: the native window opens')
        self.d.ILCT_TiSetEnabled(1)
        self.d.ILCT_TiSetLast(now - 1000, now)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'ours stopped drawing: stock')
        self.d.ILCT_TiSetLast(now, now - 1000)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'menu layer not presented: stock')
        self.d.ILCT_TiSetLast(now, now)
        self.d.ILCT_TiSetAttached(0)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'not attached: stock')
        self.assertEqual(self.stats().native_stock, 4)

    def test_p1_on_the_stock_menu_keeps_the_native_ti(self):
        # The native window comes through while the co-op menus do not own P1.
        now = self.d.ILCT_TiNow()
        self.d.ILCT_TiSetAttached(1)
        self.d.ILCT_TiSetLast(now, now)
        self.d.ILCT_TiSetP1Owned(0)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'stock: the native window opens for the stock target list')
        self.d.ILCT_TiSetP1Owned(1)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0, 0xFFFFFFFF), 'owned again: closed')

    def test_ownership_follows_the_firewall_gate(self):
        now = self.d.ILCT_TiNow()
        self.d.ILCT_TiSetAttached(1)
        self.d.ILCT_TiSetLast(now, now)
        self.d.ILCT_TiSetP1Owned(-1)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0x55, 0x00300005), 'no firewall record / FIREWALL_MENUS not live: not owned')

    def test_the_pass_keeps_ours_live(self):
        """Drawing stamps 'last drawn', so TI-01 hides the native window only while the pass runs."""
        now = self.d.ILCT_TiNow()
        self.d.ILCT_TiSetAttached(1)
        self.d.ILCT_TiSetLast(0, now)
        v = a_view()
        self.d.ILCT_TiPublish(ctypes.byref(v))
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual(c.r8, 0x55, 'before the first pass frame: stock')
        snap = tdrv.snapshot()
        self.draw(snap, now)
        c = self.ctx()
        self.d.ILCT_TiHandler(ctypes.byref(c), EPOCH)
        self.assertEqual((c.r8, c.r9), (0, 0xFFFFFFFF), 'after it: closed')


# ---- the view from a snapshot and a synthetic game ----------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class SampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_TiReset()
        self.img = Image()
        self.world = GameWorld(self.img)
        self.d.ILCT_TiSetImageBase(self.img.base)
        self.text = None

    def tearDown(self):
        self.d.ILCT_TiSetImageBase(0)
        self.d.ILCT_TiReset()
        if self.text is not None:
            self.text.close()
        self.img.close()

    # The game side.
    def foe(self, index=5, name='Hyena', loot=None, bestiary=12, steal_left=7, block=0):
        a = self.world.add_actor(index, 0, handle_class=FOE_CLASS, hp=1644)
        unit_block = self.img.alloc(0x200)
        name_at = self.img.alloc(64)
        self.img.put(name_at, encode(name) + b'\0')
        self.img.p64(unit_block + UNIT_NAME, name_at)
        self.img.p64(a.actor + ACTOR_UNIT, unit_block)
        self.img.p32(a.battle + REC_BLOCK, block)
        if loot is not None:
            rec = self.img.alloc(0x60)
            self.img.put(rec, loot)
            self.img.p64(a.actor + ACTOR_LOOT, rec)
            cls = self.img.alloc(0x60)
            self.img.p16(cls + 0x52, bestiary)
            self.img.p64(a.actor + ACTOR_CLASS, cls)
            ext = self.img.alloc(0x40)
            self.img.p16(ext + 0x14, steal_left)
            self.img.p64(a.actor + ACTOR_EXT, ext)
        return a

    def hunters_insight(self, knowledge: dict[int, bytes], always=0, settings=True):
        """HI's hook jump, a code block shaped like HI's, its save block and settings."""
        save = self.img.alloc(4 + 0x200 * 4)
        self.img.p32(save, 0x200)
        for bestiary, known in knowledge.items():
            self.img.put(save + 4 + 4 * bestiary, known)
        cave_va = 0x2E10000
        flags_at = self.d.ILCT_TiHiOffsets() >> 16
        words_va = cave_va + 0x180
        disp = (words_va + 4) - (cave_va + flags_at + 14)
        code = ctypes.create_string_buffer(0x200)
        self.assertEqual(self.d.ILCT_TiHiCode(code, 0x200, save, disp), 1)
        cave = bytearray(code.raw)
        if not settings:
            cave[flags_at] = 0xCC   # no flag reader
        else:
            cave[0x180:0x188] = struct.pack('<II', 60, always)
        self.img.put_global(cave_va, bytes(cave))
        rel = cave_va - (HI_HOOK + 5)
        self.img.put_global(HI_HOOK, b'\xe9' + struct.pack('<i', rel))
        return save

    def names(self, items: dict[int, str]):
        """Item tables and section 61 with these names (group 1)."""
        strings = [encode(n) for n in items.values()]
        sec = text_section([[encode('unused')], strings])
        tables = {}
        for first, stride, link_off, global_va, _s in ITEM_TABLES:
            rows = []
            for item, _name in [(i, n) for i, n in items.items() if first <= i < first + 0x1000]:
                while len(rows) <= item - first:
                    rows.append(b'\0' * stride)
                row = bytearray(stride)
                struct.pack_into('<H', row, link_off, link(1, list(items).index(item)))
                rows[item - first] = bytes(row)
            tables[global_va] = st2e_table(rows or [b'\0' * stride], stride)
        self.text = TextWorld(self.img, sec, tables)

    def build(self, snap, now=NOW) -> View:
        v = View()
        self.assertEqual(self.d.ILCT_TiBuild(ctypes.byref(snap), now, ctypes.byref(v)), 1)
        return v

    def snap_for(self, a, flags=SNAP_WORLD | SNAP_PARTY | SNAP_LIBRA_KNOWN | SNAP_TICKS, seat=1, u=None, members=()):
        u = u or unit(handle=FOE_CLASS | a.index, level=18)
        seats = [(0, 0)] * 4
        seats[seat] = (u.handle, NOW)
        return snap_with([u], seats, flags=flags, members=members)

    def test_view_of_a_foe(self):
        loot = loot_record(drops=(0x2001, 0x2002, 0xFFFF, 0x1005, 0xFFFF), steals=(0x0003, 0xFFFF, 0xFFFF))
        a = self.foe(loot=loot, bestiary=12, steal_left=0b110, block=1 << 4)
        self.hunters_insight({12: bytes([0b001, 0b01001, 0, 0])})
        self.names({0x0003: 'Hi-Potion', 0x1005: 'Green Beret', 0x2001: 'Wolf Pelt', 0x2002: 'Fire Stone'})
        v = self.build(self.snap_for(a, flags=SNAP_WORLD | SNAP_LIBRA_KNOWN | SNAP_LIBRA | SNAP_TICKS))
        self.assertEqual(v.name, b'Hyena')
        self.assertEqual(v.seat, 2, 'seat index 1 = P2')
        self.assertTrue(v.flags & VIEW_SHOWN and v.flags & VIEW_NAME and v.flags & VIEW_DROPS)
        self.assertTrue(v.flags & VIEW_REVEAL and v.flags & VIEW_LIBRA)
        self.assertTrue(v.flags & VIEW_IMMUNE)
        self.assertEqual([(d.kind, d.state, d.flags, d.text) for d in v.drops[:v.drop_count]], [
            (DROP, KNOWN, 0, b'Wolf Pelt'), (DROP, UNKNOWN, 0, b'???'), (DROP, KNOWN, RARE, b'Green Beret'),
            (STEAL, KNOWN, TAKEN, b'Hi-Potion')])
        self.assertEqual([(v.status_id[k], v.status_flags[k]) for k in range(v.status_count)], [(4, ST_IMMUNE)],
                         'the status block mask: immune, shown with Libra')
        st = Status()
        self.d.ILCT_TiStatus(ctypes.byref(st))
        self.assertEqual((st.hi, st.text_ready), (HI_FOUND, 1))

    def test_target_info_only_while_engaged_and_2_s_after(self):
        """0.1.60 item 6: nothing without an engagement; shown while engaged; kept ENGAGE_HOLD_MS (2 s) after the last
        engaged beat, then gone (0.1.59 kept the sticky last seat target up for good)."""
        a = self.foe(loot=loot_record())
        snap = self.snap_for(a)
        try:
            self.d.ILCT_TiForceEngaged(0)
            self.assertFalse(self.build(snap).flags & VIEW_SHOWN, 'a seat target, but no engagement')
            self.d.ILCT_TiForceEngaged(1)
            self.assertTrue(self.build(snap, NOW + 20).flags & VIEW_SHOWN)
            self.d.ILCT_TiForceEngaged(0)
            self.assertTrue(self.build(snap, NOW + 2020).flags & VIEW_SHOWN, 'held 2 s after the last engaged beat')
            self.assertFalse(self.build(snap, NOW + 2021).flags & VIEW_SHOWN, 'then gone, with no sticky target')
        finally:
            self.d.ILCT_TiForceEngaged(1)

    def test_drops_update_as_items_are_found(self):
        loot = loot_record(drops=(0x2001,) + (0xFFFF,) * 4)
        a = self.foe(loot=loot, bestiary=40)
        save = self.hunters_insight({40: bytes(4)})
        self.names({0x2001: 'Wolf Pelt'})
        snap = self.snap_for(a)
        self.assertEqual(self.build(snap).drops[0].text, b'???')
        self.img.put(save + 4 + 4 * 40 + 1, b'\x01')   # HI records the drop
        self.assertEqual(self.build(snap, NOW + 20).drops[0].text, b'Wolf Pelt')

    def test_hunters_insight_absent_foreign_and_always(self):
        loot = loot_record(drops=(0x2001,) + (0xFFFF,) * 4)
        a = self.foe(loot=loot)
        self.names({0x2001: 'Wolf Pelt'})
        self.img.put_global(HI_HOOK, HI_HOOK_STOCK)
        v = self.build(self.snap_for(a))
        self.assertFalse(v.flags & VIEW_DROPS, 'HI not running: no drops list')
        save, settings = u64(), u64()
        self.assertEqual(self.d.ILCT_TiFindHi(ctypes.byref(save), ctypes.byref(settings)), HI_ABSENT)
        self.img.put_global(HI_HOOK, b'\x90' * 5)
        self.assertEqual(self.d.ILCT_TiFindHi(ctypes.byref(save), ctypes.byref(settings)), HI_FOREIGN)
        expected = self.hunters_insight({}, always=1)
        self.assertEqual(self.d.ILCT_TiFindHi(ctypes.byref(save), ctypes.byref(settings)), HI_FOUND)
        self.assertEqual((save.value, settings.value), (expected, 0x2E10180 - 0x120000 + self.img.base))
        self.d.ILCT_TiReset()
        self.d.ILCT_TiSetImageBase(self.img.base)
        v = self.build(self.snap_for(a))
        self.assertEqual(v.drops[0].text, b'Wolf Pelt', "HI's alwaysShowItems")

    def test_unknown_bestiary_shows_every_item(self):
        a = self.foe(loot=loot_record(drops=(0x2001,) + (0xFFFF,) * 4), bestiary=0xFFFF)
        self.hunters_insight({}, settings=False)
        self.names({0x2001: 'Wolf Pelt'})
        self.assertEqual(self.build(self.snap_for(a)).drops[0].text, b'Wolf Pelt', "HI's own rule")

    def test_party_target_reveals_and_has_no_drops(self):
        a = self.foe(loot=loot_record(drops=(0x2001,) + (0xFFFF,) * 4))
        self.hunters_insight({})
        u = unit(handle=FOE_CLASS | a.index, flags=UNIT_PRESENT | UNIT_ACTOR | UNIT_PARTY)
        v = self.build(self.snap_for(a, flags=SNAP_WORLD, u=u))
        self.assertTrue(v.flags & VIEW_PARTY and v.flags & VIEW_REVEAL)
        self.assertFalse(v.flags & VIEW_LIBRA)
        self.assertFalse(v.flags & VIEW_DROPS)

    def test_anti_libra_follows_the_native_test(self):
        a = self.foe()
        u = unit(handle=FOE_CLASS | a.index, flags=UNIT_PRESENT | UNIT_ACTOR | UNIT_ANTI_LIBRA)
        libra = SNAP_WORLD | SNAP_LIBRA_KNOWN | SNAP_LIBRA
        self.img.put_global(ANTI_LIBRA_TEST, ANTI_LIBRA_STOCK)
        v = self.build(self.snap_for(a, flags=libra, u=u))
        self.assertEqual(v.flags & (VIEW_REVEAL | VIEW_ANTI_LIBRA), VIEW_ANTI_LIBRA)
        self.d.ILCT_TiReset()
        self.d.ILCT_TiSetImageBase(self.img.base)
        self.img.put_global(ANTI_LIBRA_TEST, b'\x90' * 7)   # patched out (TIBB's noAntiLibra)
        v = self.build(self.snap_for(a, flags=libra, u=u))
        self.assertEqual(v.flags & (VIEW_REVEAL | VIEW_ANTI_LIBRA | VIEW_LIBRA), VIEW_REVEAL | VIEW_LIBRA)

    def test_danger_name(self):
        a = self.foe()
        member = unit(handle=0x00100000, level=20, flags=UNIT_PRESENT)
        v = self.build(self.snap_for(a, u=unit(handle=FOE_CLASS | a.index, level=30), members=[member]))
        self.assertTrue(v.flags & VIEW_DANGER, '10 levels above the party')
        v = self.build(self.snap_for(a, u=unit(handle=FOE_CLASS | a.index, level=29), members=[member]))
        self.assertFalse(v.flags & VIEW_DANGER)

    def test_ko_fades_and_a_gone_target_is_held(self):
        a = self.foe()
        snap = self.snap_for(a)
        self.assertTrue(self.build(snap).flags & VIEW_SHOWN)
        ko = self.snap_for(a, u=unit(handle=FOE_CLASS | a.index, hp=0))
        v = self.build(ko, NOW + 20)
        self.assertTrue(v.flags & VIEW_KO)
        self.assertEqual(v.fade_from_ms, NOW + 20)
        self.assertEqual(self.build(ko, NOW + 60).fade_from_ms, NOW + 20, 'the fade keeps its start')
        nobody = snap_with([], [])
        v = self.build(nobody, NOW + 100)
        self.assertTrue(v.flags & VIEW_SHOWN and v.flags & VIEW_KO, 'held, still fading')
        self.d.ILCT_TiReset()
        self.d.ILCT_TiSetImageBase(self.img.base)
        self.build(snap, NOW)
        v = self.build(nobody, NOW + 100)
        self.assertEqual(v.flags & (VIEW_SHOWN | VIEW_GONE), VIEW_SHOWN | VIEW_GONE)
        self.assertEqual(v.fade_from_ms, NOW + 100)
        self.assertTrue(self.build(nobody, NOW + 100 + GONE_HOLD_MS + KO_FADE_MS - 1).flags & VIEW_SHOWN)
        self.assertFalse(self.build(nobody, NOW + 100 + GONE_HOLD_MS + KO_FADE_MS).flags & VIEW_SHOWN, 'released')

    def test_a_new_epoch_forgets_the_last_target(self):
        a = self.foe()
        self.build(self.snap_for(a))
        other = snap_with([], [])
        other.epoch = EPOCH + 1
        self.assertFalse(self.build(other, NOW + 10).flags & VIEW_SHOWN)

    def test_unreadable_parts_are_left_out(self):
        a = self.foe()
        self.img.p64(a.actor + ACTOR_UNIT, 0)
        v = self.build(self.snap_for(a))
        self.assertTrue(v.flags & VIEW_SHOWN)
        self.assertFalse(v.flags & VIEW_NAME, 'no unit block: no name, never a guess')
        self.assertFalse(v.flags & VIEW_DROPS)

    def test_names_refused_with_a_non_zero_resource_base(self):
        self.names({0x2001: 'Wolf Pelt'})
        out = ctypes.create_string_buffer(32)
        self.assertEqual(self.d.ILCT_TiItemName(0x2001, out, 32), 1)
        self.assertEqual(out.value, b'Wolf Pelt')
        self.img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0x10000))
        self.assertEqual(self.d.ILCT_TiItemName(0x2001, out, 32), 0)

    def test_names_bounds(self):
        self.names({0x0001: 'Potion', 0x2001: 'Wolf Pelt'})
        out = ctypes.create_string_buffer(32)
        self.assertEqual(self.d.ILCT_TiItemName(0x0001, out, 32), 1)
        self.assertEqual(out.value, b'Potion')
        self.assertEqual(self.d.ILCT_TiItemName(0x0005, out, 32), 0, 'beyond the table')
        self.assertEqual(self.d.ILCT_TiItemName(0x3001, out, 32), 0, 'no table for the range')
        self.assertEqual(self.d.ILCT_TiItemName(0x1001, out, 32), 0, 'an equipment table without the row')


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
@unittest.skipUnless(BATTLE_PACK.is_file(), 'set ILC_BATTLE_PACK to an extracted battle_pack.bin')
class BattlePackNamesTest(unittest.TestCase):
    """Item names through the relocated sections of a real battle pack, against an independent parse of the file."""

    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()
        data = BATTLE_PACK.read_bytes()
        n = struct.unpack_from('<I', data, 0)[0]
        offs = list(struct.unpack_from(f'<{n}I', data, 4)) + [len(data)]
        cls.sections = {k: data[offs[k]:offs[k + 1]] for k in (13, 18, 32, 61)}

    def setUp(self):
        self.d.ILCT_TiReset()
        self.img = Image()
        self.d.ILCT_TiSetImageBase(self.img.base)
        tables = {g: self.sections[s] for _f, _st, _l, g, s in ITEM_TABLES}
        self.text = TextWorld(self.img, self.sections[61], tables)

    def tearDown(self):
        self.d.ILCT_TiSetImageBase(0)
        self.text.close()
        self.img.close()

    def expected(self, item: int) -> str | None:
        sec = self.sections[61]
        for first, stride, link_off, _g, s in ITEM_TABLES:
            if first <= item < first + 0x1000:
                t = self.sections[s]
                rows, row_stride, start = struct.unpack_from('<I', t, 4)[0], struct.unpack_from('<H', t, 8)[0], \
                    struct.unpack_from('<I', t, 12)[0]
                if row_stride != stride or item - first >= rows:
                    return None
                lk = struct.unpack_from('<H', t, start + (item - first) * stride + link_off)[0]
                g, e = lk >> 11, lk & 0x7FF
                count, header = struct.unpack_from('<II', sec, 0)
                ends = struct.unpack_from(f'<{count}I', sec, 8)
                begin = header if g == 0 else ends[g - 1]
                blob = sec[begin:ends[g]]
                m = struct.unpack_from('<I', blob, 0)[0]
                if e >= m:
                    return None
                o = struct.unpack_from(f'<{m + 1}I', blob, 4)
                return decode_game(blob[o[e] + 2:o[e + 1]][:48]) or None
        return None

    def test_names_match_the_file(self):
        checked = 0
        for item in list(range(0x0000, 0x0040)) + list(range(0x1000, 0x1100, 7)) + list(range(0x2000, 0x2200, 5)):
            want = self.expected(item)
            out = ctypes.create_string_buffer(24)
            got = self.d.ILCT_TiItemName(item, out, 24)
            with self.subTest(hex(item)):
                if want:
                    self.assertEqual(got, 1)
                    self.assertEqual(out.value.decode(), want[:23].rstrip(' '))
                    checked += 1
        self.assertGreater(checked, 100)


# ---- the native window given back (target_info_native.*) --------------------------------------------------------
GB_SRC = HUD_SRC / 'target_info_native.cpp'
GB_HDR = HUD_SRC / 'target_info_native.h'
# Facts (restated): the UI manager, the HUD window and its handler, the TARGET INFO owner, the window handler, the
# target mode window, the drawn / pause / frontend globals, the registry.
UI_MANAGER_PTR, HUD_HANDLER, WINDOW_HANDLER, TMODE_PTR = 0x209BE80, 0x2BE490, 0x2C0870, 0x2B47730
UI_HUD, UI_WINDOW, UI_ID, UI_STATE = 0x8FA0, 0x9FD0, 0x9FD8, 0x9FDE
WIN_TI_FLAGS, WIN_ID, WIN_FLAGS, TMODE_TARGET = 0x280, 0x288, 0x40, 0xC4
RENDER_FLAGS, PAUSE_PRIMARY, PAUSE_SECONDARY, FRONTEND_ACTIVE = 0x22C7F90, 0x209AD28, 0x209AD30, 0x2092734
REGISTRY = 0x2098E10
PINS_UNKNOWN, PINS_OK, PINS_OFF = 0, 1, 2
PHASE_IDLE, PHASE_PENDING, PHASE_GIVEN = 0, 1, 2
OFF_MS, REHIDE_MS, GIVEUP_TICKS = 500, 250, 10
# The nine verify rows: (va, length, pattern) as measured on the pinned exe, one hit each in .text.
TI_V = {
    'TI-V1': (0x2BE290, 67, '40 53 48 83 ec 20 f6 81 7e 05 00 00 01 48 8b d9 74 15 e8 ?? ?? ?? ?? 48 85 c0 74 0b 48 8b '
                            'd0 48 8b cb e8 ?? ?? ?? ?? 48 8b 8b 70 05 00 00 48 85 c9 74 2b 0f b7 81 80 02 00 00 a8 01 '
                            '75 20 a8 04 74 1c e8'),
    'TI-V2': (0x2BE4D4, 20, '48 8d 8b c0 0a 00 00 e8 ?? ?? ?? ?? 33 c0 48 83 c4 20 5b c3'),
    'TI-V3': (0x2C0F49, 38, '48 63 81 78 05 00 00 4c 8d 4c 24 20 48 89 44 24 28 4c 8d 05 ?? ?? ?? ?? 0f b7 81 7e 05 00 '
                            '00 48 8b fa 48 c1 e8 02'),
    'TI-V4': (0x2BFD40, 17, '0f b7 81 80 02 00 00 44 8b ea 48 8b f9 a8 04 0f 85'),
    'TI-V5': (0x2BFEC1, 28, '8b 8f 88 02 00 00 e8 ?? ?? ?? ?? 48 85 c0 0f 85 ?? ?? ?? ?? 66 83 8f 80 02 00 00 04'),
    'TI-V6': (0x3588CB, 23, '8b f9 c1 ef 14 c1 e8 10 81 e7 ff 07 00 00 83 e0 0f 0f b7 d9 83 f8 05'),
    'TI-V7': (0x3C9432, 25, '8b d1 b9 06 00 00 00 e8 ?? ?? ?? ?? 48 8b 05 ?? ?? ?? ?? 39 98 c4 00 00 00'),
    'TI-V8': (0x244865, 15, '83 61 40 ef 48 8b 01 c7 44 24 20 09 00 00 00'),
    'TI-V9': (0x28FE40, 62, '48 89 5c 24 08 57 48 83 ec 20 8b f9 8b da 48 8b 0d ?? ?? ?? ?? 48 85 c9 75 0d 33 c0 48 8b '
                            '5c 24 30 48 83 c4 20 5f c3 48 81 c1 a0 8f 00 00 e8 ?? ?? ?? ?? 48 8b 0d ?? ?? ?? ?? 41 83 '
                            'c9 ff'),
}


def pattern_regex(pattern: str) -> bytes:
    return b''.join(b'.' if t == '??' else re.escape(bytes([int(t, 16)])) for t in pattern.split())


class GiveBackStaticTest(unittest.TestCase):
    def test_ti_v_rows_are_verify_only_info_rows(self):
        rows = {r['id']: r for r in pe.site_rows()}
        legacy = {s['id']: s for s in
                  json.loads((COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8'))['sites']}
        for sid, (va, length, pattern) in TI_V.items():
            with self.subTest(sid):
                r = rows[sid]
                self.assertEqual((r['kind'], r['policy'], r['subsystem'], r['rva'], r['length']),
                                 ('VERIFY', 'INFO', 'RENDER', va - 0x120000, length),
                                 'a mismatch refuses nothing: the give-back compares the rows itself')
                self.assertFalse(r['flags'] & 0x08, 'OPTIONAL is a PATCH flag; an INFO row never refuses anyway')
                self.assertIsNotNone(re.fullmatch(pattern_regex(pattern), r['expected'], re.S), 'bytes fit the pattern')
                sig = legacy[sid]['signature']
                self.assertEqual((sig['pattern'].lower(), sig['site_offset'], sig['hits_in_text']), (pattern, 0, 1))
        gen = (COOP / 'tools' / 'gen_site_table.py').read_text(encoding='utf-8')
        self.assertIn("*{f'TI-V{k}' for k in range(1, 10)}", gen)

    def test_address_constants_are_tagged(self):
        for path in (GB_SRC, GB_HDR):
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                self.assertEqual(pe.untagged_constants(line), [], f'{path.name}:{n}: {line.strip()}')

    def test_game_thread_code_never_blocks_allocates_or_calls_the_game(self):
        text = re.sub(r'//.*', '', GB_SRC.read_text(encoding='utf-8'))   # code only
        for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                          'memcpy(', 'memset(', 'snprintf', 'reinterpret_cast<int', 'call_game', 'guard::call',
                          'gtq::push'):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertNotIn('using fn', text)
        # Exactly three kinds of store: the owner's id and state, and the window's TI flags (bit 2).
        stores = re.findall(r'store<(\w+)>\(([^,]+),', text)
        targets = sorted({(w, a.strip()) for w, a in stores})
        self.assertEqual(targets, [('uint16_t', 'ui + gf::UI_OWNER + gf::OWNER_STATE'),
                                   ('uint16_t', 'window + gf::WINDOW_TI_FLAGS'),
                                   ('uint32_t', 'ui + gf::UI_OWNER + gf::OWNER_ID')])

    def test_wiring(self):
        tick = (COOP / 'src' / 'runtime' / 'tick.cpp').read_text(encoding='utf-8')
        self.assertEqual(tick.count('hud::target_info::giveback::tick(patch::now_ms(), epoch);'), 2,
                         'the UI tick and the stale tick')
        sample = (HUD_SRC / 'target_info_sample.cpp').read_text(encoding='utf-8')
        self.assertIn('giveback::sample(now_ms);', sample[sample.index('void step(uint64_t now_ms)'):])
        ti = (HUD_SRC / 'target_info.cpp').read_text(encoding='utf-8')
        body = ti[ti.index('int32_t ti01('):]
        self.assertIn('if (!native_hidden_now(patch::now_ms())) {', body[:body.index('\n}\n')],
                      'TI-01 and the give-back decide with one predicate')
        self.assertIn('giveback::attach(image_base);', ti)
        snap = (HUD_SRC / 'snapshot.cpp').read_text(encoding='utf-8')
        self.assertIn('target_info::giveback::aim(r)', snap)
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("SOURCES += ['src/hud/target_info_native.cpp']", build)


@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the pinned exe, read only)')
class GiveBackPinnedTest(unittest.TestCase):
    def test_each_ti_v_pattern_is_unique_in_text_at_its_row(self):
        exe = (Path(GAME) / 'x64' / 'FFXII_TZA.exe').read_bytes()
        e = struct.unpack_from('<I', exe, 0x3C)[0]
        nsec = struct.unpack_from('<H', exe, e + 6)[0]
        opt = struct.unpack_from('<H', exe, e + 20)[0]
        text = None
        for k in range(nsec):
            s = e + 24 + opt + 40 * k
            if exe[s:s + 8].rstrip(b'\0') == b'.text':
                vsize, va, rsize, raw = struct.unpack_from('<IIII', exe, s + 8)
                text = (va, exe[raw:raw + rsize])
        self.assertIsNotNone(text)
        for sid, (va, length, pattern) in TI_V.items():
            with self.subTest(sid):
                hits = [0x120000 + text[0] + m.start() for m in re.finditer(pattern_regex(pattern), text[1], re.S)]
                self.assertEqual(hits, [va])
        # The globals the rows pin: the target mode window and the UI manager (rel32 from each instruction's end).
        rows = {r['id']: r for r in pe.site_rows()}
        v7, v9 = rows['TI-V7']['expected'], rows['TI-V9']['expected']
        self.assertEqual(0x3C9432 + 19 + struct.unpack_from('<i', v7, 15)[0], TMODE_PTR)
        self.assertEqual(0x28FE40 + 21 + struct.unpack_from('<i', v9, 17)[0], UI_MANAGER_PTR)


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
@unittest.skipUnless(CAN_BUILD, 'requires the MinGW-w64 toolchain')
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the patch layer is installed on the twin)')
class GiveBackGateTest(unittest.TestCase):
    """The gate's real expression (co-op ON and RENDER live), not the forced one."""

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        tdrv.declare(cls.hw.d)   # ILCT_Ctl* signatures
        cls.d = shared_dll()

    def setUp(self):
        self.hw.setup(attach=False)
        self.d.ILCT_TiGbReset()   # gate not forced
        self.hw.d.ILCT_CtlReset()

    def tearDown(self):
        self.hw.d.ILCT_CtlReset()
        self.d.ILCT_TiGbReset()

    def test_gate_follows_coop_state_and_render(self):
        d, h = self.d, self.hw.d
        self.assertEqual(d.ILCT_TiGbGateOpen(), 1, 'co-op ON, LIVE, armed, RENDER armed and not faulted')
        cases = {
            'co-op releasing or off (F8, first step)': lambda: (h.ILCT_CtlKill(1), h.ILCT_CtlStep(tdrv.NOW)),
            'co-op off (F8, release done)': lambda: (h.ILCT_CtlKill(1), h.ILCT_CtlStep(tdrv.NOW),
                                                    h.ILCT_CtlStep(tdrv.NOW + 10_000)),
            'RENDER disarmed': lambda: h.ILCT_PatchApplySubsystems(0xFF & ~tdrv.RENDER),
            'RENDER faulted': lambda: h.ILCT_PatchFault(tdrv.RENDER),
            'DRY mode': lambda: h.ILCT_PatchSetMode(0),
            'liveness disarmed': lambda: h.ILCT_PatchSetArmed(0),
        }
        for name, shut in cases.items():
            with self.subTest(name):
                self.setUp()
                self.assertEqual(d.ILCT_TiGbGateOpen(), 1)
                shut()
                if name.startswith('co-op'):
                    self.assertNotEqual(h.ILCT_CtlState(), 0, 'the kill switch took effect')
                self.assertEqual(d.ILCT_TiGbGateOpen(), 0, name)
        with self.subTest('forced open wins (host tests only)'):
            self.setUp()
            h.ILCT_PatchSetMode(0)
            d.ILCT_TiGbForceGate(1)
            self.assertEqual(d.ILCT_TiGbGateOpen(), 1)


class GiveBackTest(unittest.TestCase):
    """The give-back against a synthetic game: the UI manager with its HUD window and TARGET INFO owner, the target mode
    window, the registry with a foe. The HUD window's update (which consumes the pending create) is played by
    hud_update()."""

    EPOCH = 7
    T0 = 1_000_000

    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        d = self.d
        d.ILCT_TiReset()
        d.ILCT_TiGbReset()
        d.ILCT_CtlReset()
        sizes = (u32 * 4)()
        self.assertEqual(d.ILCT_TiGbSizes(sizes, 4), 2)
        self.assertEqual((sizes[0], sizes[1]), (ctypes.sizeof(GbCounters), ctypes.sizeof(GbStatus)))
        self.img = Image()
        self.world = GameWorld(self.img)
        img, ui = self.img, self.world.ui
        self.ui = ui
        img.p64(ui + UI_HUD, img.va(HUD_HANDLER))
        img.p32(ui + UI_ID, 0x55)
        img.p16(ui + UI_STATE, 0x0006)       # extras and handle form left from an earlier window
        img.put_global(RENDER_FLAGS, struct.pack('<I', 0x08))
        img.put_global(PAUSE_PRIMARY, bytes(16))
        img.put_global(FRONTEND_ACTIVE, struct.pack('<I', 0))
        # Registry 0: head, table (the actor manager GameWorld made), flags, limit.
        img.put_global(REGISTRY, struct.pack('<Q', 1))
        img.put_global(REGISTRY + 0x10, b'\x01')
        img.put_global(REGISTRY + 0x20, struct.pack('<i', 64))
        self.foe = self.world.add_actor(10, 9, handle_class=FOE_CLASS)
        self.foe2 = self.world.add_actor(11, 9, handle_class=FOE_CLASS)
        for a in (self.foe, self.foe2):
            img.p16(a.entry + 0x16, FOE_CLASS >> 20)   # generation 3
        self.x = FOE_CLASS | 10
        self.tmode = img.alloc(0x100)
        img.put_global(TMODE_PTR, struct.pack('<Q', self.tmode))
        self.aim_at(self.x)
        d.ILCT_TiGbSetImageBase(img.base)
        d.ILCT_TiGbForcePins(PINS_OK)
        d.ILCT_TiSetAttached(1)
        d.ILCT_TiGbForceLive(1)
        d.ILCT_TiGbForceGate(1)   # co-op ON and RENDER live (the gate's own tests shut it)
        d.ILCT_TiSetP1Owned(1)    # the co-op menus own P1

    def tearDown(self):
        self.d.ILCT_TiGbReset()
        self.d.ILCT_TiReset()
        self.img.close()

    # ---- the synthetic game ----
    def aim_at(self, target, focus=True):
        self.img.p32(self.tmode + WIN_FLAGS, 0x10 if focus else 0)
        self.img.p32(self.tmode + TMODE_TARGET, target)

    def r16(self, address):
        return struct.unpack('<H', ctypes.string_at(address, 2))[0]

    def r32(self, address):
        return struct.unpack('<I', ctypes.string_at(address, 4))[0]

    def r64(self, address):
        return struct.unpack('<Q', ctypes.string_at(address, 8))[0]

    def owner(self):
        return self.r32(self.ui + UI_ID), self.r16(self.ui + UI_STATE), self.r64(self.ui + UI_WINDOW)

    def hud_update(self):
        """0x2BE290: a pending create builds a window for the owner's id; a window flagged invalid (and not closing)
        is closed."""
        ui = self.ui
        state = self.r16(ui + UI_STATE)
        if state & 1:
            win = self.img.alloc(0x300)
            self.img.p64(win, self.img.va(WINDOW_HANDLER))
            self.img.p32(win + WIN_ID, self.r32(ui + UI_ID))
            self.img.p64(ui + UI_WINDOW, win)
            self.img.p16(ui + UI_STATE, state & ~3)
        win = self.r64(ui + UI_WINDOW)
        if win and (self.r16(win + WIN_TI_FLAGS) & 5) == 4:
            self.img.p64(ui + UI_WINDOW, 0)

    def open_window(self, flags=0, handler=WINDOW_HANDLER):
        win = self.img.alloc(0x300)
        self.img.p64(win, self.img.va(handler))
        self.img.p16(win + WIN_TI_FLAGS, flags)
        self.img.p64(self.ui + UI_WINDOW, win)
        return win

    def tick(self, t, epoch=None):
        self.d.ILCT_TiGbTick(self.T0 + t, self.EPOCH if epoch is None else epoch)

    def stats(self) -> GbCounters:
        c = GbCounters()
        self.d.ILCT_TiGbStats(ctypes.byref(c))
        return c

    def status(self) -> GbStatus:
        s = GbStatus()
        self.d.ILCT_TiGbStatus(ctypes.byref(s))
        return s

    def stop_ours(self, at=0):
        """Ours live, then not live from `at`."""
        self.d.ILCT_TiGbForceLive(1)
        self.tick(at - 1)
        self.d.ILCT_TiGbForceLive(0)
        self.tick(at)

    def given_back(self, at=0):
        self.stop_ours(at)
        self.tick(at + OFF_MS)

    # ---- A: give-back ----
    # ---- F8 gate: F8 / co-op off = the give-back writes nothing ----
    def test_gate_shut_writes_nothing_while_aiming(self):
        self.d.ILCT_TiGbForceGate(0)
        before = self.owner()
        self.stop_ours(0)
        for t in range(0, OFF_MS + 400, 16):
            self.tick(t)
        self.assertEqual(self.owner(), before, 'F8 while aiming: no pending create')
        c = self.stats()
        self.assertEqual((c.opens, c.cancels, c.rehides), (0, 0, 0))
        self.assertEqual(self.status().phase, PHASE_IDLE)

    def test_gate_shut_leaves_a_pending_create_to_the_game(self):
        self.given_back()
        self.assertEqual(self.owner(), (self.x, 0x0001, 0))
        self.d.ILCT_TiGbForceGate(0)
        self.tick(OFF_MS + 16)
        self.assertEqual(self.owner(), (self.x, 0x0001, 0), 'not withdrawn: the HUD update finishes it natively')
        c = self.stats()
        self.assertEqual((c.cancels, c.gated, self.status().phase), (0, 1, PHASE_IDLE))
        self.hud_update()
        self.tick(OFF_MS + 32)
        self.assertNotEqual(self.owner()[2], 0, 'the native window opens on its own')
        self.assertEqual(self.stats().opened, 0, 'not ours to count')

    def test_gate_shut_does_not_rehide_a_given_back_window(self):
        self.given_back()
        self.hud_update()
        self.tick(OFF_MS + 16)
        win = self.owner()[2]
        self.assertNotEqual(win, 0)
        self.d.ILCT_TiGbForceGate(0)
        self.d.ILCT_TiGbForceLive(1)
        for t in range(OFF_MS + 32, OFF_MS + 1000, 16):
            self.tick(t)
        self.assertEqual(self.owner()[2], win, 'gate shut: the window is left alone')
        self.assertEqual(self.stats().rehides, 0)

    def test_gate_reopened_waits_off_ms_again(self):
        # The off clock restarts when the gate opens, so the native window does not flash next to
        # ours while ours takes its first beats after F8 / RENDER comes back.
        self.d.ILCT_TiGbForceGate(0)
        self.stop_ours(0)
        self.tick(OFF_MS)
        self.assertEqual(self.stats().opens, 0)
        before = self.owner()
        self.d.ILCT_TiGbForceGate(1)
        reopen = OFF_MS + 16
        for t in range(reopen, reopen + OFF_MS, 16):
            self.tick(t)
        self.assertEqual(self.owner(), before, 'gate just opened: the 500 ms rule starts afresh')
        self.tick(reopen + OFF_MS)
        self.assertEqual(self.owner(), (self.x, 0x0001, 0), 'ours still off 500 ms after the gate opened: given back')
        self.assertEqual(self.stats().opens, 1)

    def test_gate_reopened_with_ours_live_clears_a_leftover_pending(self):
        self.given_back()
        self.d.ILCT_TiGbForceGate(0)
        self.tick(OFF_MS + 16)
        self.assertEqual(self.owner()[1] & 1, 1, 'left for the game while shut')
        self.d.ILCT_TiGbForceGate(1)
        self.d.ILCT_TiGbForceLive(1)
        reopen = OFF_MS + 32
        self.tick(reopen)
        self.tick(reopen + REHIDE_MS - 1)
        self.assertEqual(self.owner()[1] & 1, 1, 'ours not live for 250 ms since the gate opened yet')
        self.tick(reopen + REHIDE_MS)
        self.assertEqual(self.owner()[1] & 1, 0, 'cleared under ours')
        self.assertEqual(self.stats().pending_cleared, 1)

    def test_gives_back_after_ours_stopped_for_500_ms(self):
        self.stop_ours(0)
        for t in (100, 300, OFF_MS - 1):
            self.tick(t)
        self.assertEqual(self.owner(), (0x55, 0x0006, 0), 'not durably stopped yet: nothing written')
        self.tick(OFF_MS)
        self.assertEqual(self.owner(), (self.x, 0x0001, 0), 'id = X, then (w & ~6) | 1: the deferred create')
        self.assertEqual((self.stats().opens, self.status().phase, self.status().target), (1, PHASE_PENDING, self.x))
        self.hud_update()
        self.tick(OFF_MS + 16)
        win = self.owner()[2]
        self.assertNotEqual(win, 0)
        self.assertEqual(self.r32(win + WIN_ID), self.x, 'the HUD update created the window for the aimed foe')
        c = self.stats()
        self.assertEqual((c.opens, c.opened, c.cancels, c.giveups), (1, 1, 0, 0))
        self.assertEqual(self.status().phase, PHASE_GIVEN)
        # Once per target-mode session: the window closing (the game's own close) does not bring a second write.
        self.img.p64(self.ui + UI_WINDOW, 0)
        for t in range(OFF_MS + 32, OFF_MS + 400, 16):
            self.tick(t)
        self.assertEqual(self.stats().opens, 1)

    def test_every_precondition_has_a_falsifier(self):
        cases = {
            'target mode window null': lambda: self.img.put_global(TMODE_PTR, struct.pack('<Q', 0)),
            'target mode without focus': lambda: self.aim_at(self.x, focus=False),
            'no target': lambda: self.aim_at(0),
            'not registry 0': lambda: self.aim_at(0x00010000 | (self.x & 0xFFF0FFFF)),
            'stale generation': lambda: self.img.p16(self.foe.entry + 0x16, 4),
            'no actor': lambda: self.img.p64(self.foe.entry + 0x30, 0),
            'no battle record': lambda: self.img.p64(self.foe.actor + 0x698, 0),
            'registry disabled': lambda: self.img.put_global(REGISTRY + 0x10, b'\x00'),
            'native window open': lambda: self.img.p64(self.ui + UI_WINDOW, self.img.alloc(0x300)),
            'create already pending': lambda: self.img.p16(self.ui + UI_STATE, 0x0001),
            'HUD window absent': lambda: self.img.p64(self.ui + UI_HUD, 0),
            'UI not drawn': lambda: self.img.put_global(RENDER_FLAGS, struct.pack('<I', 0)),
            'pause (primary)': lambda: self.img.put_global(PAUSE_PRIMARY, struct.pack('<Q', 0x1234)),
            'pause (secondary)': lambda: self.img.put_global(PAUSE_SECONDARY, struct.pack('<Q', 0x1234)),
            'frontend open': lambda: self.img.put_global(FRONTEND_ACTIVE, struct.pack('<I', 1)),
            'a TI-V row off': lambda: self.d.ILCT_TiGbForcePins(PINS_OFF),
            'pins not compared yet': lambda: self.d.ILCT_TiGbForcePins(PINS_UNKNOWN),
            'TI-01 not attached': lambda: self.d.ILCT_TiSetAttached(0),
            'no UI manager': lambda: self.img.put_global(UI_MANAGER_PTR, struct.pack('<Q', 0)),
            'gate shut (F8 / RENDER off)': lambda: self.d.ILCT_TiGbForceGate(0),
        }
        for name, falsify in cases.items():
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                falsify()
                before = self.owner()
                self.given_back()
                for t in (OFF_MS + 16, OFF_MS + 32):
                    self.tick(t)
                self.assertEqual(self.owner(), before, 'nothing written')
                self.assertEqual(self.stats().opens, 0)
        with self.subTest('control'):
            self.tearDown()
            self.setUp()
            self.given_back()
            self.assertEqual(self.owner(), (self.x, 1, 0))

    def test_still_live_or_not_durably_stopped_writes_nothing(self):
        for t in range(0, 2000, 100):
            self.tick(t)
        self.assertEqual((self.owner(), self.stats().opens), ((0x55, 0x0006, 0), 0), 'ours live: no give-back')
        # Flapping: never off for 500 ms without a break.
        for t in range(2000, 4000, 100):
            self.d.ILCT_TiGbForceLive(1 if (t // 100) % 4 == 0 else 0)
            self.tick(t)
        self.assertEqual(self.stats().opens, 0)

    def test_an_unresolved_target_is_rejected_once(self):
        self.img.p16(self.foe.entry + 0x16, 4)
        self.given_back()
        for t in range(OFF_MS + 16, OFF_MS + 200, 16):
            self.tick(t)
        self.assertEqual((self.stats().rejected, self.stats().opens), (1, 0))
        self.aim_at(FOE_CLASS | 11)   # cycles to a foe that resolves
        self.tick(OFF_MS + 300)
        self.assertEqual(self.owner()[:2], (FOE_CLASS | 11, 1))

    # ---- cancel and give-up ----
    def test_cancelled_when_ours_comes_back(self):
        self.given_back()
        self.d.ILCT_TiGbForceLive(1)
        self.tick(OFF_MS + 16)
        self.assertEqual(self.owner(), (self.x, 0x0000, 0), 'bit 0 cleared (the id stays, harmless)')
        self.assertEqual((self.stats().cancels, self.status().phase), (1, PHASE_IDLE))

    def test_cancelled_when_focus_is_lost_or_the_aim_moves(self):
        for name, change in (('focus lost', lambda: self.aim_at(self.x, focus=False)),
                             ('aim moved', lambda: self.aim_at(FOE_CLASS | 11)),
                             ('window gone', lambda: self.img.put_global(TMODE_PTR, struct.pack('<Q', 0)))):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.given_back()
                change()
                self.tick(OFF_MS + 16)
                self.assertEqual(self.owner()[1] & 1, 0, 'pending withdrawn')
                self.assertEqual(self.stats().cancels, 1)

    def test_cancel_never_touches_a_request_the_game_made_since(self):
        self.given_back()
        self.img.p32(self.ui + UI_ID, FOE_CLASS | 11)   # a native request rewrote the owner (pending kept)
        self.aim_at(FOE_CLASS | 11)
        self.tick(OFF_MS + 16)
        self.assertEqual(self.owner()[:2], (FOE_CLASS | 11, 1), "the game's pending create stays")
        self.assertEqual(self.stats().cancels, 1)

    def test_given_up_after_ten_ticks_without_the_window(self):
        self.given_back()
        for k in range(1, GIVEUP_TICKS):
            self.tick(OFF_MS + 16 * k)
        self.assertEqual(self.owner()[1] & 1, 1, 'still waiting for the HUD update')
        self.tick(OFF_MS + 16 * GIVEUP_TICKS)
        self.assertEqual(self.owner()[1] & 1, 0)
        c = self.stats()
        self.assertEqual((c.giveups, c.opens), (1, 1))
        for k in range(GIVEUP_TICKS + 1, 3 * GIVEUP_TICKS):
            self.tick(OFF_MS + 16 * k)
        self.assertEqual(self.stats().opens, 1, 'no retry loop in the same target-mode session')
        # A new target-mode session (focus lost, then back) may give back again.
        self.aim_at(self.x, focus=False)
        self.tick(OFF_MS + 1000)
        self.aim_at(self.x)
        self.tick(OFF_MS + 1016)
        self.assertEqual(self.stats().opens, 2)

    def test_superseded_by_the_game(self):
        self.given_back()
        self.img.p16(self.ui + UI_STATE, 0)   # a cursor end cleared bits 0..1
        self.tick(OFF_MS + 16)
        self.tick(OFF_MS + 32)
        c = self.stats()
        self.assertEqual((c.superseded, c.cancels, c.opens), (1, 0, 1))

    def test_a_new_ui_manager_forgets_the_pending_create(self):
        self.given_back()
        ui2 = self.img.alloc(0x12000, page=True)
        self.img.p64(ui2 + UI_HUD, self.img.va(HUD_HANDLER))
        self.img.put_global(UI_MANAGER_PTR, struct.pack('<Q', ui2))
        self.tick(OFF_MS + 16)
        self.assertEqual(self.status().phase, PHASE_PENDING, 'a fresh owner: given back into the new one')
        self.assertEqual((self.r32(ui2 + UI_ID), self.r16(ui2 + UI_STATE)), (self.x, 1))

    # ---- B: re-hide ----
    def test_rehide_closes_a_native_window_under_ours(self):
        win = self.open_window()
        self.tick(0)
        self.tick(REHIDE_MS - 1)
        self.assertEqual(self.r16(win + WIN_TI_FLAGS), 0, 'live, but not for 250 ms yet')
        self.tick(REHIDE_MS)
        self.assertEqual(self.r16(win + WIN_TI_FLAGS), 4, 'bit 2: the game closes it on its next update')
        self.tick(REHIDE_MS + 16)
        self.assertEqual(self.stats().rehides, 1, 'once')
        self.hud_update()
        self.assertEqual(self.owner()[2], 0, 'closed through the stale-target branch')

    def test_rehide_leaves_closing_and_foreign_windows(self):
        for name, kw in (('closing', {'flags': 1}), ('not a TARGET INFO window', {'handler': 0x2C0880})):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                win = self.open_window(**kw)
                before = self.r16(win + WIN_TI_FLAGS)
                self.tick(0)
                self.tick(REHIDE_MS + 50)
                self.assertEqual(self.r16(win + WIN_TI_FLAGS), before)
                self.assertEqual(self.stats().rehides, 0)

    def test_rehide_clears_a_pending_create(self):
        self.img.p16(self.ui + UI_STATE, 0x0005)
        self.tick(0)
        self.tick(REHIDE_MS)
        self.assertEqual(self.owner()[1], 0x0004, 'bit 0 only')
        self.assertEqual(self.stats().pending_cleared, 1)

    def test_control_nothing_to_rehide(self):
        self.tick(0)
        self.tick(REHIDE_MS + 100)
        c = self.stats()
        self.assertEqual((c.rehides, c.pending_cleared), (0, 0))
        self.assertEqual(self.owner(), (0x55, 0x0006, 0))

    def test_round_trip_give_back_rehide_give_back(self):
        self.given_back()
        self.hud_update()
        self.tick(OFF_MS + 16)
        win = self.owner()[2]
        self.d.ILCT_TiGbForceLive(1)
        self.tick(OFF_MS + 100)
        self.tick(OFF_MS + 100 + REHIDE_MS)
        self.assertEqual(self.r16(win + WIN_TI_FLAGS) & 4, 4, 'ours back: the given-back window closes')
        self.hud_update()
        self.d.ILCT_TiGbForceLive(0)
        self.tick(2000)
        self.tick(2000 + OFF_MS)
        self.assertEqual(self.stats().opens, 2, 'ours took over in between: a later stop gives back again')

    # ---- the pins, the predicate, the aim ----
    def test_pins_compare_every_ti_v_row(self):
        d = self.d
        d.ILCT_TiGbForcePins(-1)
        rows = {r['id']: r for r in pe.site_rows()}
        for sid, (va, _length, _p) in TI_V.items():
            self.img.put_global(va, rows[sid]['expected'])
        d.ILCT_TiGbSample(self.T0)
        self.assertEqual((self.status().pins, self.status().pin_row), (PINS_OK, -1))
        self.given_back()
        self.assertEqual(self.stats().opens, 1, 'pins compared and stock: the give-back acts')
        self.img.put_global(TI_V['TI-V5'][0] + 6, b'\xe9')   # another mod rewrote the resolve call
        d.ILCT_TiGbSample(self.T0 + 100)
        self.assertEqual(self.status().pins, PINS_OK, 'compared again only every 5 s')
        d.ILCT_TiGbSample(self.T0 + 5000)
        index = {r['id']: k for k, r in enumerate(pe.site_rows())}
        self.assertEqual((self.status().pins, self.status().pin_row), (PINS_OFF, index['TI-V5']))
        self.assertEqual(d.ILCT_TiGbPinRow(4), index['TI-V5'])
        c = self.stats()
        self.assertEqual((c.pin_checks, c.pin_off), (2, 1))
        # Pins off: the pending create is withdrawn and nothing new is written.
        self.tick(OFF_MS + 16)
        self.assertEqual(self.owner()[1] & 1, 0)
        self.aim_at(self.x, focus=False)
        self.tick(OFF_MS + 32)
        self.aim_at(self.x)
        self.tick(OFF_MS + 2000)
        self.assertEqual(self.stats().opens, 1)

    def test_the_predicate_is_ti01s(self):
        d = self.d
        d.ILCT_TiGbForceLive(-1)
        now = d.ILCT_TiNow()
        d.ILCT_TiSetLast(now, now)
        self.assertEqual(d.ILCT_TiGbLiveNow(now), 1, 'element on, co-op on, attached, drawn and presented')
        d.ILCT_TiSetEnabled(0)
        self.assertEqual(d.ILCT_TiGbLiveNow(now), 0, 'element off')
        d.ILCT_TiSetEnabled(1)
        d.ILCT_TiSetP1Owned(0)
        self.assertEqual(d.ILCT_TiGbLiveNow(now), 0, 'P1 on the stock menus: not hidden, so no re-hide')
        d.ILCT_TiSetP1Owned(1)
        d.ILCT_TiSetEnabled(1)
        d.ILCT_TiSetLast(now - 1000, now)
        self.assertEqual(d.ILCT_TiGbLiveNow(now), 0, 'ours stopped drawing')
        # The tick follows it: ours stopped drawing -> give-back 500 ms later.
        self.d.ILCT_TiGbTick(now, self.EPOCH)
        self.d.ILCT_TiGbTick(now + OFF_MS, self.EPOCH)
        self.assertEqual(self.owner()[:2], (self.x, 1))

    def test_the_aim(self):
        d, base = self.d, self.img.base
        self.assertEqual(d.ILCT_TiGbAim(base), self.x)
        self.aim_at(self.x, focus=False)
        self.assertEqual(d.ILCT_TiGbAim(base), 0, 'no focus')
        self.aim_at(self.x)
        self.img.p16(self.foe.entry + 0x16, 4)
        self.assertEqual(d.ILCT_TiGbAim(base), 0, 'stale handle')
        self.img.p16(self.foe.entry + 0x16, 3)
        d.ILCT_TiGbForcePins(PINS_OFF)
        self.assertEqual(d.ILCT_TiGbAim(base), 0, 'a TI-V row off: no aim')
        d.ILCT_TiGbForcePins(PINS_OK)
        self.assertEqual(d.ILCT_TiGbAim(base), self.x)
        self.assertEqual(self.stats().aims, 2)


if __name__ == '__main__':
    unittest.main()
