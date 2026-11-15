"""Host tests for the battle log's per-hit feed, kill reward and chain (native/coop/src/hud/battle_log*). No game run,
nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy).

* rows: HR-01/02/03/05 and KR-01 are OPTIONAL RENDER detours with their verify-only gates (HR-V1..HR-V5, KR-V1), CH-V1
  and CH-V2 are the chain read's own checks (INFO); every expected byte is the exe's, every signature has one hit; no
  row covers the call at 0x30760a another mod rewrites; the facts the rows pin;
* pinned exe: the flag nibble reaches HR-02 in eax untouched on both paths, the kill pop-up's arguments, the chain
  reset's stores and the counter's display rule;
* the handlers, called directly: copy-only (every saved register unchanged, RUN_ORIGINAL), what each copies, the
  bracket on its own thread, the Forge-thread exclusion (a number from another thread, outside a bracket or from a stale
  bracket is counted, never copied), declined when off;
* the model: the outcome tags, tag-only lines, exact amounts (and the diff they absorb), the pending attribution of
  the Forge's hits (the real attacker over the heuristic), pending hits nobody claimed, the kill line with EXP, LP and
  the chain;
* the pipeline: handlers -> ring -> assembler -> model -> view (attribution and merge with tags, an outcome without a
  bracket, a bracket whose exit never came, the kill line);
* the chain on the image twin: count and level from their globals, the display rule, off when a verify row or a value
  is not what it should be;
* unicorn on the hooked twin: each of the five windows with its handler attached runs exactly as stock (registers,
  flags, memory) and leaves the expected event in the ring; HR-03 from another thread leaves nothing.
"""
from __future__ import annotations

import ctypes
import random
import re
import struct
import threading
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_hud_battle_log as tbl
import test_coop_hud_driver as tdrv
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from test_coop_hud_battle_log import (ATTACK, BALTHIER, BITE, CURE, FIRA, FRAN, HELL_BLASTER, HYENA_A, HYENA_B,
                                      HYENA_C, KO, LK_DAMAGE, LK_HEAL, LK_KILL, LF_PARTY_HIT, NAMES, NATIVE, PENELO,
                                      SEATS, SIDE_FOE, SIDE_PARTY, T0, TAG_BLOCK, TAG_CRIT, TAG_IMMUNE, TAG_MISS,
                                      TAG_PARRY, TAG_WEAK, VIEW_CHAIN, VIEW_HITS, VIEW_KILLS, VIEW_PINNED, Counters,
                                      Event, Line, Scene, Status, View, Who, count_in_text, read_exe, stats, who)
from test_coop_patch_engine import GAME, TWIN_BASE, checked

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
EPOCH = tdrv.EPOCH

# ---- facts (restated) ---------------------------------------------------------------------------------------------------
FN_HIT, HIT_OUTCOME_SITE, HIT_EXIT, FN_NUMBER, FN_KILL_POPUP = 0x307300, 0x307601, 0x307B91, 0x3283D0, 0x28FB80
OTHER_PATCH, OTHER_PATCH_LEN = 0x30760A, 5        # Hunter's Insight rewrites the call 0x3216e0 here
HIT_CALLER, KILL_CALLER = 0x319362, 0x312518
CHAIN_LEVEL, CHAIN_COUNT, CHAIN_SHOWN_PLUS, CHAIN_RESET, CHAIN_SHOWN = 0x22C3158, 0x22C315C, 0x1E02814, 0x3183A0, 0x229E40
EQUIP_TABLE_PTR = 0x2EBF0F8
HOOKS = {   # row: (VA, expected bytes, hook window)
    'HR-01': (FN_HIT, '40 55 53 56 57', 5),
    'HR-02': (HIT_OUTCOME_SITE, '4d 8b c5 48 8b d3', 6),
    'HR-03': (FN_NUMBER, '48 89 5c 24 08', 5),
    'HR-05': (HIT_EXIT, '48 8b 8d a0 00 00 00', 7),
    'KR-01': (FN_KILL_POPUP, '48 89 5c 24 08', 5),
}
GATES = {'HR-V1': ('HR-02', 0x3075C7, 58), 'HR-V2': ('HR-02', 0x30760F, 10), 'HR-V3': ('HR-01', 0x31935C, 11),
         'HR-V6': ('HR-01', 0x319349, 11), 'HR-V4': ('HR-03', 0x3283D5, 26), 'HR-V5': ('HR-05', 0x307B98, 15),
         'KR-V1': ('KR-01', 0x31250A, 19)}
CHAIN_ROWS = {'CH-V1': (CHAIN_RESET, 42), 'CH-V2': (CHAIN_SHOWN, 7)}
EV_HIT_BEGIN, EV_HIT_OUTCOME, EV_HIT_NUMBER, EV_HIT_END, EV_KILL = 0x8001, 0x8002, 0x8003, 0x8004, 0x8005
HK_ACTOR, HK_TARGET, HK_OUTCOME, HK_ELEMENT, HK_AFFINITY, HK_WEAPON, HK_RECORDS = 1, 2, 4, 8, 0x10, 0x20, 0x40
HROW_BEGIN, HROW_OUTCOME, HROW_NUMBER, HROW_END, HROW_KILL = 1, 2, 4, 8, 0x10
ALL_ROWS = 0x1F
H_BEGIN, H_OUTCOME, H_NUMBER, H_END, H_KILL = 1, 2, 3, 5, 6   # ILCT_BlHitHandler selectors
OUT_HIT, OUT_PARRY, OUT_EVADE, OUT_SHIELD, OUT_MISS, OUT_IMMUNE, OUT_REFLECTED, OUT_SAFETY, OUT_SKIPPED = \
    0, 1, 2, 3, 6, 7, 8, 9, 11
HIT_CRIT, HIT_COUNTER = 1, 4
BRACKET_MS, PENDING_MS, ABSORB_MS = 1000, 400, 1000
ACTOR_HANDLE, ACTOR_BATTLE, SLOT_OUTCOME, ROW_ELEMENT, REC_WEAPON, REC_AFFINITY = 0x08, 0x698, 0x08, 0x13, 0x50, 0x40
UNKNOWN = 0xFF


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


HookPayload = S('HookPayload', [('serial', u32), ('actor', u32), ('target', u32), ('action', u16), ('outcome', u8),
                                ('flags', u8), ('actor_record', u64), ('target_record', u64), ('value', i32),
                                ('value2', i32), ('weapon', u16), ('kind', u8), ('element', u8),
                                ('affinity', u8 * 3), ('known', u8)])
Hit = S('Hit', [('serial', u32), ('action', u16), ('outcome', u8), ('flags', u8), ('time_ms', u64), ('caster', Who),
                ('target', Who), ('weak', u8), ('pinned', u8), ('amount_count', u8), ('reserved', u8),
                ('amounts', i32 * 4), ('total', i32), ('reserved2', u32), ('action_name', ctypes.c_char * 24)])
Kill = S('Kill', [('time_ms', u64), ('victim', Who), ('exp', u32), ('lp', u32), ('chain_known', u8),
                  ('chain_level', u8), ('chain', u16), ('reserved', u32)])

SIGNATURES = {
    'ILCT_BlHitHandler': ([u32, P(th.HookCtx), u32], i32), 'ILCT_BlSetHitRows': ([u32], None),
    'ILCT_BlHitRowIndex': ([u32], i32), 'ILCT_BlSetBracket': ([u32, u64, u64], None), 'ILCT_BlThreadId': ([], u64),
    'ILCT_BlAttach': ([u64], i32), 'ILCT_BlRingAddresses': ([P(u64), P(u64)], None),
    'ILCT_BlOutcomeTags': ([u32], u32), 'ILCT_BlModelHit': ([P(Hit)], u32), 'ILCT_BlModelKill': ([P(Kill)], u32),
    'ILCT_BlChain': ([P(u16), P(u8)], i32), 'ILCT_SetFixedClock': ([u64], None),
}


def declare(d):
    tbl.declare(d)
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def dll():
    return declare(tbl.shared_dll())


def payload(e) -> HookPayload:
    return HookPayload.from_buffer_copy(bytes(e.args))


def side_of(h):
    return SIDE_PARTY if h in SEATS else SIDE_FOE


def actor_who(h) -> Who:
    return who(h, side_of(h), SEATS.get(h, 0), NAMES[h])


def make_hit(caster, target, action, outcome=OUT_HIT, flags=0, weak=0, amounts=None, t=T0) -> Hit:
    h = Hit()
    h.serial, h.action, h.outcome, h.flags, h.time_ms, h.weak = 1, action, outcome, flags, t, weak
    h.caster, h.target = actor_who(caster), actor_who(target)
    h.action_name = tbl.ACTION_NAMES.get(action, b'')
    if amounts:
        h.pinned, h.amount_count = 1, len(amounts)
        for k, a in enumerate(amounts):
            h.amounts[k] = a
        h.total = sum(amounts)
    return h


def make_kill(victim, exp, lp, chain=None, t=T0) -> Kill:
    k = Kill()
    k.time_ms, k.victim, k.exp, k.lp = t, actor_who(victim), exp, lp
    if chain is not None:
        k.chain_known, k.chain, k.chain_level = 1, chain[0], chain[1]
    return k


def code_only(text_: str) -> str:
    text_ = re.sub(r'"(?:\\.|[^"\\])*"', '""', text_)
    return '\n'.join(line.split('//', 1)[0] for line in text_.splitlines())


def body_of(text_: str, signature: str) -> str:
    body = text_[text_.index(signature):]
    return body[:body.index('\n}\n')]


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_rows_in_the_legacy_table_and_the_generator(self):
        legacy = (COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8')
        gen = (COOP / 'tools' / 'gen_site_table.py').read_text(encoding='utf-8')
        optional = gen[gen.index('OPTIONAL_BY_DECISION = {'):gen.index('INTERIOR_WAIVED')]
        gates = gen[gen.index('GATES = {'):gen.index('VERIFY_INFO = ')]
        info = gen[gen.index('VERIFY_INFO = '):gen.index('\n', gen.index('VERIFY_INFO = '))]
        for sid in HOOKS:
            self.assertIn(f'"id": "{sid}"', legacy)
            self.assertIn(f"'{sid}':", optional, f'{sid}: OPTIONAL (another mod there skips it, refuses nothing)')
        for sid, (gated, _va, _n) in GATES.items():
            self.assertIn(f"'{sid}': '{gated}'", gates)
        for sid in CHAIN_ROWS:
            self.assertIn(f"'{sid}'", info, f'{sid}: information for the patch layer, the read checks it itself')

    def test_handlers_are_copy_only(self):
        text_ = (HUD_SRC / 'battle_log.cpp').read_text(encoding='utf-8')
        for name in ('hr01', 'hr02', 'hr03', 'hr05', 'kr01'):
            body = body_of(text_, f'int32_t {name}(patch::ctx* c')
            with self.subTest(name):
                self.assertEqual(re.findall(r'c->\w+\s*=[^=]', body), [], 'no saved register is written')
                self.assertNotIn('RESUME', body)
                self.assertEqual(len(re.findall(r'return patch::RUN_ORIGINAL;', body)),
                                 len(re.findall(r'\breturn\b', body)), 'every exit runs the original')
                self.assertNotIn('copy_to_caller', body)
        hr03 = body_of(text_, 'int32_t hr03(patch::ctx* c')
        self.assertIn('open_here(self, now)', hr03, "an amount only from the bracket's own thread")
        self.assertIn('g_counters.foreign', hr03)
        kr01 = code_only(body_of(text_, 'int32_t kr01(patch::ctx* c'))
        self.assertNotIn('peek(', kr01, 'KR-01 reads registers only')
        reads = code_only(body_of(text_, 'bool peek('))
        self.assertIn('copy_from_caller', reads, 'game memory only through the fault-safe probe')

    def test_facts_restated(self):
        facts = (HUD_SRC / 'battle_log_facts.h').read_text(encoding='utf-8')
        for needle in ('FN_HIT = 0x307300', 'HIT_OUTCOME_SITE = 0x307601', 'HIT_EXIT = 0x307B91',
                       'HIT_OTHER_PATCH = 0x30760A', 'FN_NUMBER = 0x3283D0', 'SLOT_OUTCOME = 0x08',
                       'ROW_ELEMENT = 0x13', 'REC_WEAPON = 0x50', 'REC_AFFINITY = 0x40', 'EQUIP_TABLE_PTR = 0x2EBF0F8',
                       'EQUIP_ELEMENT = 0x1E', 'FN_KILL_POPUP = 0x28FB80', 'CHAIN_LEVEL = 0x22C3158',
                       'CHAIN_COUNT = 0x22C315C', 'CHAIN_SHOWN_PLUS = 0x1E02814', 'FN_CHAIN_RESET = 0x3183A0',
                       'FN_CHAIN_SHOWN = 0x229E40', 'OUT_MISS = 6', 'OUT_IMMUNE = 7', 'OUT_SKIPPED = 11'):
            self.assertIn(needle, facts)
        self.assertIn('EQUIP_TABLE_PTR = 0x2EBF0F8', facts)
        self.assertIn('0x2EBF0F8},   // VA: equipment', (HUD_SRC / 'target_info_facts.h').read_text(encoding='utf-8'),
                      'the same table the Target Info names items from')

    def test_the_sampler_reads_the_chain_after_the_drain_and_behind_its_rows(self):
        sample = code_only((HUD_SRC / 'battle_log_sample.cpp').read_text(encoding='utf-8'))
        feed = body_of(sample, 'void feed(game_reader& r')
        self.assertLess(feed.index('ring_drain('), feed.index('chain_sample('))
        self.assertLess(feed.index('chain_sample('), feed.index('hook_event('))
        chain = body_of(sample, 'chain_read chain_sample(')
        self.assertIn('row_stock(r, reset) && row_stock(r, shown)', chain)
        for forbidden in ('copy_to_caller', 'memory_write', 'VirtualProtect'):
            self.assertNotIn(forbidden, sample)


@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class RowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = pe.site_rows()
        cls.index = {r['id']: k for k, r in enumerate(cls.rows)}

    def test_hook_rows(self):
        for sid, (va, expected, window) in HOOKS.items():
            r = self.rows[self.index[sid]]
            with self.subTest(sid):
                self.assertEqual((r['rva'], r['kind'], r['policy'], r['subsystem']),
                                 (va - 0x120000, 'DETOUR', 'PATCH', 'RENDER'))
                self.assertEqual(r['flags'], 0x08, 'OPTIONAL, no interior reference')
                self.assertEqual(checked(r), bytes.fromhex(expected))
                self.assertEqual(r['hook_len'], window)
                self.assertEqual(read_exe(va, window), bytes.fromhex(expected))

    def test_gates_and_chain_rows(self):
        for sid, (gated, va, n) in GATES.items():
            r = self.rows[self.index[sid]]
            with self.subTest(sid):
                self.assertEqual((r['rva'], r['length'], r['kind'], r['policy']), (va - 0x120000, n, 'VERIFY', 'GATE'))
                self.assertEqual(int(r['gate']), self.index[gated])
                self.assertEqual(r['expected'], read_exe(va, n))
        for sid, (va, n) in CHAIN_ROWS.items():
            r = self.rows[self.index[sid]]
            with self.subTest(sid):
                self.assertEqual((r['rva'], r['length'], r['kind'], r['policy']), (va - 0x120000, n, 'VERIFY', 'INFO'))
                self.assertEqual(r['expected'], read_exe(va, n))

    def test_nothing_covers_the_call_another_mod_rewrites(self):
        a0, a1 = OTHER_PATCH - 0x120000, OTHER_PATCH - 0x120000 + OTHER_PATCH_LEN
        for r in self.rows:
            end = r['rva'] + (r['hook_len'] if r['policy'] == 'PATCH' else len(checked(r)))
            with self.subTest(r['id']):
                self.assertFalse(r['rva'] < a1 and a0 < end, f'{r["id"]} covers 0x30760a..0x30760e')
        r = self.rows[self.index['HR-02']]
        self.assertEqual(r['rva'] + r['hook_len'], 0x307607 - 0x120000, 'HR-02 ends three bytes before it')
        self.assertEqual(read_exe(0x307607, 3), bytes.fromhex('48 8b cf'), 'mov rcx, rdi between the two')
        self.assertEqual(read_exe(OTHER_PATCH, 1), b'\xE8', 'the call itself (stock in the exe)')

    def test_signatures_have_one_hit(self):
        legacy = (COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8')
        for sid in list(HOOKS) + list(GATES) + list(CHAIN_ROWS):
            block = legacy[legacy.index(f'"id": "{sid}"'):]
            sig = re.search(r'"pattern": "([^"]+)"', block).group(1)
            with self.subTest(sid):
                rx = b''.join(b'.' if x == '??' else re.escape(bytes([int(x, 16)])) for x in sig.split())
                b = tbl._exe()
                _n, _va, _vs, ra, rs = next(s for s in tbl._sections(b) if s[0] == b'.text')
                self.assertEqual(len(re.findall(rx, b[ra:ra + rs], re.S)), 1)


@unittest.skipUnless(GAME and pe.capstone is not None, 'requires IVALICE_LEGACY_GAME and capstone')
class PinnedCodeTest(unittest.TestCase):
    def decode(self, va, n):
        cs = pe.capstone.Cs(pe.capstone.CS_ARCH_X86, pe.capstone.CS_MODE_64)
        return list(cs.disasm(read_exe(va, n), va))

    def test_the_flag_nibble_reaches_hr02_untouched(self):
        # The native path loads eax at 0x3075c3; the Forge jumps to 0x3075c7 with eax set. Up to HR-02 eax is only tested.
        insns = self.decode(0x3075C3, HIT_OUTCOME_SITE - 0x3075C3)
        self.assertEqual((insns[0].mnemonic, insns[0].op_str), ('mov', 'eax, dword ptr [rsp + 0x68]'))
        for i in insns[1:]:
            with self.subTest(hex(i.address)):
                self.assertIn(i.mnemonic, ('test', 'je', 'jne', 'or'))
                if i.mnemonic == 'or':
                    self.assertTrue(i.op_str.startswith('dword ptr [r14 + 8]'), 'only the slot is written')
        self.assertEqual(insns[-1].address + insns[-1].size, HIT_OUTCOME_SITE)
        window = self.decode(HIT_OUTCOME_SITE, 9 + 5 + 5)
        self.assertEqual([(i.mnemonic, i.op_str) for i in window[:5]],
                         [('mov', 'r8, r13'), ('mov', 'rdx, rbx'), ('mov', 'rcx, rdi'), ('call', '0x3216e0'),
                          ('movzx', 'eax, byte ptr [r14 + 8]')])

    def test_the_only_call_of_the_hit_function(self):
        i = self.decode(0x31935F, 8)
        self.assertEqual([(x.mnemonic, x.op_str) for x in i], [('movzx', 'ecx, bp'), ('call', '0x307300')])
        self.assertEqual(count_in_text(b'\xE8' + struct.pack('<i', FN_HIT - (HIT_CALLER + 5))), 1)

    def test_the_kill_pop_up_arguments(self):
        insns = self.decode(0x31250A, KILL_CALLER + 5 - 0x31250A)
        self.assertEqual([(i.mnemonic, i.op_str) for i in insns],
                         [('mov', 'ecx, dword ptr [r14 + 8]'), ('test', 'eax, eax'), ('mov', 'r8d, ebx'),
                          ('cmovne', 'edi, ebp'), ('mov', 'edx, edi'), ('call', '0x28fb80')])

    def test_the_chain_reset_names_the_globals_and_the_display_rule(self):
        insns = self.decode(CHAIN_RESET, 42)
        stores = [i.address + i.size + struct.unpack('<i', i.bytes[3:7])[0] for i in insns
                  if i.mnemonic == 'mov' and i.bytes[:3] == b'\x48\x89\x05']
        self.assertEqual(stores, [0x22C3170, 0x22C3160, CHAIN_LEVEL], 'level and count stored as one qword')
        helper = self.decode(CHAIN_SHOWN, 7)
        self.assertEqual(helper[0].address + helper[0].size + struct.unpack('<i', helper[0].bytes[2:6])[0],
                         CHAIN_SHOWN_PLUS)
        self.assertEqual(helper[1].mnemonic, 'ret')


# ---- the handlers, called directly ----------------------------------------------------------------------------------------
class Mem:
    """Actors, records, a hit slot and an action row in host memory (the game, as the handlers see it)."""

    def __init__(self):
        self.keep = []

    def buf(self, n):
        b = ctypes.create_string_buffer(n)
        self.keep.append(b)
        return b

    def actor(self, handle, record):
        a = self.buf(0x800)
        struct.pack_into('<I', a, ACTOR_HANDLE, handle)
        struct.pack_into('<Q', a, ACTOR_BATTLE, ctypes.addressof(record) if record is not None else 0)
        return a

    def record(self, side, member=0, weak=0, absorb=0, immune=0, weapon=0x1000):
        r = self.buf(0x200)
        r[4], r[5] = bytes([member]), bytes([side])
        r[REC_AFFINITY], r[REC_AFFINITY + 1], r[REC_AFFINITY + 3] = bytes([weak]), bytes([absorb]), bytes([immune])
        struct.pack_into('<H', r, REC_WEAPON, weapon)
        return r

    def slot(self, outcome):
        s = self.buf(0x10)
        s[SLOT_OUTCOME] = bytes([outcome])
        return s

    def row(self, element):
        r = self.buf(0x40)
        r[ROW_ELEMENT] = bytes([element])
        return r


def ctx(**regs) -> th.HookCtx:
    c = th.HookCtx()
    for k, name in enumerate(n for n, _ in th.HookCtx._fields_ if n != 'xmm'):
        setattr(c, name, 0x1111_0000 + k)
    for k in range(12):
        c.xmm[k] = 0x2222_0000 + k
    for name, value in regs.items():
        setattr(c, name, value & 0xFFFF_FFFF_FFFF_FFFF)
    return c


def a(buf) -> int:
    return ctypes.addressof(buf)


@native_test
class HandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = dll()

    def setUp(self):
        self.d.ILCT_BlReset()
        self.d.ILCT_BlSetHitRows(ALL_ROWS)
        m = self.m = Mem()
        self.frec = m.record(0, member=2, weapon=0x1005)
        self.hrec = m.record(1, weak=0x01, absorb=0x02, immune=0x04)
        self.fran = m.actor(FRAN, self.frec)
        self.hyena = m.actor(HYENA_C, self.hrec)

    def call(self, which, c, expect=0):
        before = bytes(c)
        self.assertEqual(self.d.ILCT_BlHitHandler(which, ctypes.byref(c), EPOCH), expect, 'RUN_ORIGINAL')
        self.assertEqual(bytes(c), before, 'every saved register, the flags and the resume slot unchanged')

    def drain(self):
        arr = (Event * 256)()
        lost = u32()
        n = self.d.ILCT_BlRingDrain(arr, 256, ctypes.byref(lost))
        return [arr[k] for k in range(n)]

    def begin(self):
        c = ctx(rcx=0xABCD_0000 | FIRA, rdx=a(self.fran), r8=a(self.hyena))
        self.call(H_BEGIN, c)

    def test_the_thread_id_is_the_tebs(self):
        self.assertEqual(self.d.ILCT_BlThreadId(), ctypes.windll.kernel32.GetCurrentThreadId())
        seen = []
        t = threading.Thread(target=lambda: seen.append((self.d.ILCT_BlThreadId(),
                                                         ctypes.windll.kernel32.GetCurrentThreadId())))
        t.start()
        t.join()
        self.assertEqual(seen[0][0], seen[0][1])
        self.assertNotEqual(seen[0][0], self.d.ILCT_BlThreadId())

    def test_hr01_opens_this_threads_bracket(self):
        self.begin()
        (e,) = self.drain()
        p = payload(e)
        self.assertEqual((e.msg, e.key, e.flags), (EV_HIT_BEGIN, self.d.ILCT_BlThreadId(), 1))
        self.assertEqual((p.serial, p.actor, p.target, p.action, p.known), (1, FRAN, HYENA_C, FIRA,
                                                                             HK_ACTOR | HK_TARGET))
        self.begin()
        self.assertEqual(payload(self.drain()[0]).serial, 2, 'a new serial per hit')

    def test_hr02_copies_the_outcome_and_what_weak_needs(self):
        slot, row = self.m.slot(OUT_HIT), self.m.row(0x01)
        self.begin()
        c = ctx(rdi=a(self.fran), rbx=a(self.hyena), r15=0x7777_0000 | FIRA, r14=a(slot), r13=a(row),
                rax=0xFFFF_FFF0 | HIT_CRIT | HIT_COUNTER)
        self.call(H_OUTCOME, c)
        e = self.drain()[1]
        p = payload(e)
        self.assertEqual(e.msg, EV_HIT_OUTCOME)
        self.assertEqual((p.serial, p.actor, p.target, p.action, p.outcome, p.flags),
                         (1, FRAN, HYENA_C, FIRA, OUT_HIT, HIT_CRIT | HIT_COUNTER), 'inside the bracket of HR-01')
        self.assertEqual((p.actor_record, p.target_record), (a(self.frec), a(self.hrec)))
        self.assertEqual((p.element, list(p.affinity), p.weapon), (0x01, [0x01, 0x02, 0x04], 0x1005))
        self.assertEqual(p.known, 0x7F)
        self.assertEqual(stats(self.d).hits, 1)

    def test_hr02_with_unreadable_pointers_still_reports(self):
        c = ctx(rdi=8, rbx=8, r15=FIRA, r14=8, r13=0, rax=0)
        self.call(H_OUTCOME, c)
        p = payload(self.drain()[0])
        self.assertEqual((p.serial, p.known, p.actor, p.target), (0, 0, 0, 0), 'no bracket, nothing read: zeros')

    def test_a_number_only_from_the_brackets_own_thread(self):
        d = self.d
        number = ctx(rcx=a(self.hyena), rdx=(-880) & 0xFFFF_FFFF, r8=0, r9=0)
        self.call(H_NUMBER, number)                       # no bracket open: not copied
        self.begin()

        def on_other_thread():
            c = ctx(rcx=a(self.hyena), rdx=(-999) & 0xFFFF_FFFF, r8=0, r9=0)
            d.ILCT_BlHitHandler(H_NUMBER, ctypes.byref(c), EPOCH)
            k = ctx(rcx=HYENA_C, rdx=120, r8=2)
            d.ILCT_BlHitHandler(H_KILL, ctypes.byref(k), EPOCH)

        t = threading.Thread(target=on_other_thread)     # the Forge's Lua thread, while the game thread waits
        t.start()
        t.join()
        self.call(H_NUMBER, number)                       # the bracket's own thread: copied
        self.call(H_END, ctx())
        self.call(H_NUMBER, number)                       # after the exit: not copied
        events = self.drain()
        self.assertEqual([e.msg for e in events], [EV_HIT_BEGIN, EV_KILL, EV_HIT_NUMBER, EV_HIT_END])
        n = payload(events[2])
        self.assertEqual((n.serial, n.actor, n.value, n.value2, n.kind), (1, HYENA_C, -880, 0, 0))
        self.assertEqual(payload(events[3]).serial, 1)
        self.assertNotEqual(events[1].key, events[0].key, 'the kill reward from any thread')
        c = stats(d)
        self.assertEqual((c.numbers, c.foreign, c.kills), (1, 3, 1))

    def test_a_stale_or_foreign_bracket_pins_nothing(self):
        d = self.d
        me = d.ILCT_BlThreadId()
        d.ILCT_SetFixedClock(gt.NOW)
        number = ctx(rcx=a(self.hyena), rdx=5, r8=1, r9=0)
        for thread, opened, label in ((me + 1, gt.NOW, "another thread's bracket"),
                                      (me, gt.NOW - BRACKET_MS - 1, 'older than BRACKET_MS: its exit was missed'),
                                      (me, gt.NOW + 5, 'stamped after now')):
            with self.subTest(label):
                d.ILCT_BlSetBracket(7, thread, opened)
                before = stats(d).foreign
                self.call(H_NUMBER, number)
                self.assertEqual(stats(d).foreign, before + 1)
        self.assertEqual(self.drain(), [])
        d.ILCT_BlSetBracket(7, me, gt.NOW - BRACKET_MS)       # control: the oldest bracket that still pins
        self.call(H_NUMBER, number)
        self.assertEqual([payload(e).serial for e in self.drain()], [7])
        d.ILCT_BlSetBracket(7, me + 1, gt.NOW)
        self.call(H_END, ctx())
        self.assertEqual(self.drain(), [], "HR-05 does not close another thread's bracket")

    def test_kr01_copies_registers_only(self):
        self.call(H_KILL, ctx(rcx=HYENA_C, rdx=1200, r8=12))
        p = payload(self.drain()[0])
        self.assertEqual((p.actor, p.value, p.value2, p.known), (HYENA_C, 1200, 12, 0))

    def test_declined_when_off(self):
        d = self.d
        d.ILCT_BlSetHitRows(0)
        for which in (H_BEGIN, H_OUTCOME, H_NUMBER, H_END, H_KILL):
            self.call(which, ctx(rcx=HYENA_C, rdx=a(self.fran), r8=a(self.hyena), rdi=a(self.fran),
                                 rbx=a(self.hyena), r14=a(self.m.slot(0))))
        d.ILCT_BlSetHitRows(ALL_ROWS)
        d.ILCT_BlConfigure(0, 0, 0, 0, 1)
        self.call(H_KILL, ctx(rcx=HYENA_C))
        self.assertEqual(self.drain(), [])
        self.assertEqual(stats(d).declined, 6)

    def test_the_bracket_rows_need_both_ends(self):
        d = self.d
        s = Status()
        for rows, want in ((ALL_ROWS, ALL_ROWS), (HROW_BEGIN | HROW_NUMBER | HROW_OUTCOME, HROW_OUTCOME),
                           (HROW_END | HROW_NUMBER | HROW_KILL, HROW_KILL), (HROW_BEGIN | HROW_END, 0x09)):
            d.ILCT_BlSetHitRows(rows)
            d.ILCT_BlStatus(ctypes.byref(s))
            self.assertEqual(s.hit_rows, want, hex(rows))
        rows = pe.site_rows()
        index = {r['id']: k for k, r in enumerate(rows)}
        for bit, sid in ((HROW_BEGIN, 'HR-01'), (HROW_OUTCOME, 'HR-02'), (HROW_NUMBER, 'HR-03'), (HROW_END, 'HR-05'),
                         (HROW_KILL, 'KR-01')):
            self.assertEqual(d.ILCT_BlHitRowIndex(bit), index[sid])


# ---- the model --------------------------------------------------------------------------------------------------------------
@native_test
class ModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def hit(self, *args, **kw):
        return self.d.ILCT_BlModelHit(ctypes.byref(make_hit(*args, **kw)))

    def kill(self, *args, **kw):
        return self.d.ILCT_BlModelKill(ctypes.byref(make_kill(*args, **kw)))

    def test_outcome_tags(self):
        table = {0: 0, 1: TAG_PARRY, 2: TAG_BLOCK, 3: TAG_BLOCK, 4: TAG_BLOCK, 5: TAG_BLOCK, 6: TAG_MISS,
                 7: TAG_IMMUNE, 8: 0, 9: TAG_IMMUNE, 10: TAG_BLOCK, 11: 0, 0xFF: 0}
        self.assertEqual({k: self.d.ILCT_BlOutcomeTags(k) for k in table}, table)

    def test_outcomes_without_damage_and_their_merge(self):
        s = Scene(self.d).unit(BALTHIER, 2362, target=HYENA_C, action=ATTACK).unit(HYENA_C, 2782).sample(T0)
        self.assertNotEqual(self.hit(BALTHIER, HYENA_C, ATTACK, OUT_MISS, t=T0 + 100), 0)
        self.assertEqual(s.texts(), ['Balthier attacks Hyena C  MISS'])
        self.hit(BALTHIER, HYENA_C, ATTACK, OUT_SHIELD, t=T0 + 600)
        self.hit(BALTHIER, HYENA_C, ATTACK, OUT_PARRY, t=T0 + 900)
        self.assertEqual(s.texts(), ['Balthier attacks Hyena C x3  MISS  BLOCK  PARRY'])
        self.hit(FRAN, HYENA_C, FIRA, OUT_SAFETY, t=T0 + 1000)
        self.assertEqual(s.texts()[-1], 'Fran: Fira on Hyena C  IMMUNE')
        self.assertEqual(self.hit(FRAN, HYENA_C, FIRA, OUT_SKIPPED, t=T0 + 1100), 0, 'a skipped hit says nothing')
        self.assertEqual(len(s.lines()), 2)

    def test_exact_amounts_and_the_diff_they_absorb(self):
        s = Scene(self.d).unit(FRAN, 1325).unit(BALTHIER, 2362, target=HYENA_C, action=ATTACK)
        s.unit(HYENA_C, 2782).sample(T0)
        self.hit(FRAN, HYENA_C, FIRA, OUT_HIT, flags=HIT_CRIT, weak=1, amounts=[-880], t=T0 + 100)
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena C  880  CRIT  WEAK'])
        s.set(HYENA_C, hp=1902).sample(T0 + 120)
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena C  880  CRIT  WEAK'], 'the diff the number showed: absorbed')
        s.set(HYENA_C, hp=1500).sample(T0 + 300)
        self.assertEqual(s.texts()[-1], 'Balthier attacks Hyena C  402', 'a change no number showed: the diff path')
        self.hit(FRAN, HYENA_C, FIRA, OUT_HIT, amounts=[-500], t=T0 + 1000)
        s.set(HYENA_C, hp=800).sample(T0 + 1020)          # 700: 500 shown, 200 more from elsewhere
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena C x2  880, 500  CRIT  WEAK',
                                     'Balthier attacks Hyena C x2  402, 200'])
        self.hit(PENELO, FRAN, CURE, OUT_HIT, amounts=[348], t=T0 + 1100)
        s.set(FRAN, hp=1325).sample(T0 + 1120)            # Fran was at full: nothing to absorb from, no line
        self.assertEqual(s.texts()[-1], 'Penelo: Cure on Fran  +348')
        self.assertEqual(s.lines()[-1].cls, LK_HEAL)

    def test_damage_on_a_party_member_with_its_number(self):
        s = Scene(self.d).unit(FRAN, 1325).unit(HYENA_B, 2000).sample(T0)
        self.hit(HYENA_B, FRAN, BITE, OUT_HIT, amounts=[-357], t=T0 + 100)
        line = s.lines()[-1]
        self.assertEqual(tbl.text(self.d, line), 'Hyena B: Bite on Fran  -357')
        self.assertTrue(line.flags & LF_PARTY_HIT)
        s.set(FRAN, hp=968).sample(T0 + 120)
        self.assertEqual(len(s.lines()), 1)

    def test_a_pending_hit_names_the_real_attacker(self):
        # The Forge's numbers come from its Lua thread: the hit has no amount; the next HP change of the victim is it.
        s = Scene(self.d).unit(FRAN, 1325).unit(HYENA_A, 2000, target=FRAN).unit(HYENA_B, 2000, target=FRAN)
        s.sample(T0)
        s.msg(T0 + 50, 14, a=HYENA_A, action=HELL_BLASTER)   # the heuristic would pick Hyena A (its event is newer)
        self.assertEqual(self.hit(HYENA_B, FRAN, BITE, OUT_HIT, flags=HIT_CRIT, weak=1, t=T0 + 100), 0, 'pending')
        s.set(FRAN, hp=968).sample(T0 + 120)
        line = s.lines()[-1]
        self.assertEqual(line.actor, HYENA_B)
        self.assertEqual(tbl.text(self.d, line), 'Hyena B: Bite on Fran  -357  CRIT  WEAK')
        self.assertTrue(line.flags & LF_PARTY_HIT)
        # Two hits of one caster before the diff: one pending entry, counted twice.
        self.hit(HYENA_B, FRAN, BITE, t=T0 + 3000)
        self.hit(HYENA_B, FRAN, BITE, t=T0 + 3050)
        s.set(FRAN, hp=600).sample(T0 + 3100)
        self.assertEqual(tbl.text(self.d, s.lines()[-1]), 'Hyena B: Bite on Fran x2  -368, ...',
                         'two hits, one diff: the amount is their sum, the count says two')
        # A heal never carries WEAK.
        self.hit(PENELO, FRAN, CURE, weak=1, t=T0 + 4000)
        s.set(FRAN, hp=900).sample(T0 + 4020)
        self.assertEqual((s.lines()[-1].cls, s.lines()[-1].tags), (LK_HEAL, 0))

    def test_a_pending_hit_nobody_claimed(self):
        s = Scene(self.d).unit(FRAN, 1325).sample(T0)
        self.hit(FRAN, HYENA_C, FIRA, OUT_HIT, weak=1, t=T0 + 100)       # Hyena C is not followed yet
        s.sample(T0 + 100 + PENDING_MS - 1)
        self.assertEqual(s.texts(), [], 'still pending')
        s.sample(T0 + 100 + PENDING_MS + 1)
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena C  WEAK'], 'the hit is named, without an amount')

    def test_the_kill_line(self):
        s = Scene(self.d).unit(BALTHIER, 2362, target=HYENA_A, action=ATTACK).unit(HYENA_A, 300).sample(T0)
        s.set(HYENA_A, hp=0, status=1 << KO).sample(T0 + 100)
        self.assertNotEqual(self.kill(HYENA_A, 120, 2, chain=(12, 1), t=T0 + 110), 0)
        s.msg(T0 + 200, 38, value=120)
        s.msg(T0 + 250, 36, item_name=b'Wolf Pelt', count=1)
        kills = [x for x in s.lines() if x.cls == LK_KILL]
        self.assertEqual(len(kills), 1, 'the diff, the reward and the loot make one line')
        self.assertEqual(tbl.text(self.d, kills[0]), 'Hyena A defeated  120 EXP 2 LP  Chain 12 Lv 2  120 gil, Wolf Pelt')
        self.assertEqual((kills[0].exp, kills[0].lp, kills[0].chain, kills[0].chain_level, kills[0].reward),
                         (120, 2, 12, 1, 3))
        # The reward first, the message after: still one line.
        self.kill(HYENA_B, 80, 1, t=T0 + 5000)
        s.msg(T0 + 5100, 16, b=HYENA_B)
        kills = [x for x in s.lines() if x.cls == LK_KILL]
        self.assertEqual([tbl.text(self.d, x) for x in kills][-1], 'Hyena B defeated  80 EXP 1 LP')
        self.assertEqual(len(kills), 2)
        # No reward (the no-EXP mode), no chain known: the plain kill line.
        self.kill(HYENA_C, 0, 0, t=T0 + 9000)
        self.assertEqual(s.texts()[-1], 'Hyena C defeated')
        self.assertEqual(self.kill(FRAN, 10, 1, t=T0 + 9100), 0, 'never a party member')


# ---- the pipeline: handlers -> ring -> assembler -> model -> view -----------------------------------------------------------
@native_test
class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = dll()

    def setUp(self):
        d = self.d
        d.ILCT_BlReset()
        d.ILCT_BlSetNamesOnly(1)
        d.ILCT_BlSetImageBase(0x10000)    # no game here: every table and global read fails closed
        d.ILCT_BlSetHitRows(ALL_ROWS)
        m = self.m = Mem()
        self.frec = m.record(0, member=2)
        self.hrec = m.record(1, weak=0x01)
        self.brec = m.record(1)
        self.fran = m.actor(FRAN, self.frec)
        self.hyena = m.actor(HYENA_C, self.hrec)
        self.bite = m.actor(HYENA_B, self.brec)
        self.t = T0
        self.step(1325)

    def snap(self, hp: int) -> tdrv.Snapshot:
        s = tdrv.snapshot(epoch=EPOCH, sampled=self.t, flags=0x3)
        s.member_count = 1
        u = s.members[0]
        u.handle, u.member, u.flags, u.seat, u.hp, u.hp_max = FRAN, 2, 0x1 | 0x2 | 0x10, 2, hp, 1325
        return s

    def step(self, hp=1325, dt=20) -> View:
        self.t += dt
        v = View()
        self.assertEqual(self.d.ILCT_BlStep(ctypes.byref(self.snap(hp)), self.t, ctypes.byref(v)), 1)
        return v

    def handler(self, which, **regs):
        self.d.ILCT_SetFixedClock(self.t + 5)   # the events' clock is the beat's clock (the module clock)
        c = ctx(**regs)
        self.assertEqual(self.d.ILCT_BlHitHandler(which, ctypes.byref(c), EPOCH), 0)

    def hit(self, caster, target, action, outcome, flags=0, element=0x01, numbers=(), foreign=(), end=True):
        slot, row = self.m.slot(outcome), self.m.row(element)
        self.handler(H_BEGIN, rcx=action, rdx=a(caster), r8=a(target))
        self.handler(H_OUTCOME, rdi=a(caster), rbx=a(target), r15=action, r14=a(slot), r13=a(row), rax=flags)
        if foreign:
            self.d.ILCT_SetFixedClock(self.t + 5)

            def lua():
                for amount in foreign:
                    c = ctx(rcx=a(target), rdx=amount & 0xFFFF_FFFF, r8=0, r9=0)
                    self.d.ILCT_BlHitHandler(H_NUMBER, ctypes.byref(c), EPOCH)
            t = threading.Thread(target=lua)
            t.start()
            t.join()
        for unit, amount, kind in numbers:
            self.handler(H_NUMBER, rcx=a(unit), rdx=amount & 0xFFFF_FFFF, r8=1 if amount > 0 else 0, r9=kind)
        if end:
            self.handler(H_END)

    def lines(self, v):
        return [v.lines[k] for k in range(v.line_count)]

    def test_exact_numbers_attribution_and_merge_with_tags(self):
        self.hit(self.fran, self.hyena, FIRA, OUT_HIT, flags=HIT_CRIT, foreign=[-999],
                 numbers=[(self.hyena, -880, 0), (self.fran, -50, 0), (self.hyena, -30, 1)])
        v = self.step()
        (line,) = [x for x in self.lines(v) if x.actor == FRAN]
        self.assertEqual((line.cls, line.target, line.action, line.amount_count, line.amounts[0]),
                         (LK_DAMAGE, HYENA_C, FIRA, 1, -880),
                         "Fran's own number and the MP number are not the hit's; the Lua thread's is not copied")
        self.assertEqual(line.tags, TAG_CRIT | TAG_WEAK, 'fire on a fire-weak foe, critical')
        c = stats(self.d)
        self.assertEqual((c.hits, c.numbers, c.foreign), (1, 3, 1))
        self.assertTrue(v.flags & VIEW_HITS and v.flags & VIEW_PINNED and v.flags & VIEW_KILLS)
        self.assertFalse(v.flags & VIEW_CHAIN, 'no game here: the chain stays off')
        self.hit(self.fran, self.hyena, FIRA, OUT_MISS)
        v = self.step()
        (line,) = [x for x in self.lines(v) if x.actor == FRAN]
        self.assertEqual((line.count, line.tags, line.amount_count), (2, TAG_CRIT | TAG_WEAK | TAG_MISS, 1))

    def test_the_forges_hit_takes_the_diff(self):
        self.hit(self.bite, self.fran, BITE, OUT_HIT, foreign=[-357])   # its number only on the Lua thread
        v = self.step(968)
        hits = [x for x in self.lines(v) if x.cls == LK_DAMAGE]
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].actor, hits[0].target, hits[0].amounts[0]), (HYENA_B, FRAN, -357))
        self.assertTrue(hits[0].flags & LF_PARTY_HIT)
        self.assertEqual(stats(self.d).numbers, 0)

    def test_an_outcome_without_a_bracket(self):
        slot = self.m.slot(OUT_EVADE)
        self.handler(H_OUTCOME, rdi=a(self.fran), rbx=a(self.hyena), r15=ATTACK, r14=a(slot), r13=0, rax=0)
        v = self.step()
        (line,) = self.lines(v)
        self.assertEqual((line.actor, line.target, line.tags), (FRAN, HYENA_C, TAG_BLOCK))

    def test_a_bracket_whose_exit_never_came(self):
        self.hit(self.fran, self.hyena, FIRA, OUT_IMMUNE, end=False)
        self.assertEqual(self.lines(self.step()), [], 'still open')
        v = self.step(dt=BRACKET_MS + 5)
        (line,) = self.lines(v)
        self.assertEqual(line.tags, TAG_IMMUNE)

    def test_the_kill_reward_line(self):
        self.d.ILCT_SetFixedClock(self.t + 5)

        def lua():
            c = ctx(rcx=HYENA_C, rdx=120, r8=2)
            self.d.ILCT_BlHitHandler(H_KILL, ctypes.byref(c), EPOCH)
        t = threading.Thread(target=lua)
        t.start()
        t.join()
        v = self.step()
        (line,) = self.lines(v)
        self.assertEqual((line.cls, line.actor, line.exp, line.lp, line.reward), (LK_KILL, HYENA_C, 120, 2, 1))
        self.assertTrue(tbl.text(self.d, line).endswith('defeated  120 EXP 2 LP'))


# ---- the chain on the image twin -----------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ChainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.twin = pe.Twin.get()
        cls.d = declare(cls.twin.dll)

    def setUp(self):
        self.twin.reset()
        self.d.ILCT_BlReset()
        self.d.ILCT_BlSetImageBase(TWIN_BASE)

    def tearDown(self):
        self.twin.reset()
        self.d.ILCT_BlReset()

    def chain(self):
        shown, level = u16(), u8()
        ok = self.d.ILCT_BlChain(ctypes.byref(shown), ctypes.byref(level))
        return (shown.value, level.value) if ok else None

    def test_count_level_and_the_display_rule(self):
        plus = struct.unpack('<I', ctypes.string_at(CHAIN_SHOWN_PLUS, 4))[0]
        self.assertNotEqual(plus, 0, 'the shipped value: the counter shows count + 1')
        self.twin.poke(CHAIN_LEVEL, struct.pack('<ii', 2, 11))
        self.assertEqual(self.chain(), (12, 2))
        self.twin.poke(CHAIN_SHOWN_PLUS, struct.pack('<I', 0))
        self.assertEqual(self.chain(), (11, 2))
        self.twin.poke(CHAIN_LEVEL, struct.pack('<ii', 0, 0))
        self.assertEqual(self.chain(), (0, 0))

    def test_off_when_a_value_or_a_verify_row_is_not_what_it_should_be(self):
        for values in ((4, 3), (-1, 3), (1, 1000), (1, -2)):
            with self.subTest(values=values):
                self.twin.poke(CHAIN_LEVEL, struct.pack('<ii', *values))
                self.assertIsNone(self.chain())
        self.twin.poke(CHAIN_LEVEL, struct.pack('<ii', 1, 5))
        self.assertIsNotNone(self.chain())
        for va, n in ((CHAIN_RESET, 42), (CHAIN_SHOWN, 7)):
            with self.subTest(row=hex(va)):
                original = ctypes.string_at(va + n - 1, 1)
                self.twin.poke(va + n - 1, bytes([original[0] ^ 0xFF]))
                self.assertIsNone(self.chain(), 'another mod rewrote it: the chain stays off')
                self.twin.poke(va + n - 1, original)
                self.assertIsNotNone(self.chain())
        self.assertGreater(stats(self.d).chain_off, 0)


# ---- the patch layer on the twin: gates and OPTIONAL rows ------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class GateTwinTest(unittest.TestCase):
    MISSING = {'HR-01': HROW_BEGIN | HROW_END | HROW_NUMBER, 'HR-05': HROW_BEGIN | HROW_END | HROW_NUMBER,
               'HR-03': HROW_NUMBER, 'HR-02': HROW_OUTCOME, 'KR-01': HROW_KILL}

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.twin = cls.hw.twin
        cls.d = declare(cls.hw.d)
        cls.index = {r['id']: k for k, r in enumerate(pe.site_rows())}

    def setUp(self):
        self.twin.reset()

    def tearDown(self):
        self.twin.reset()
        self.d.ILCT_BlReset()

    def hooked(self, sid) -> bool:
        return bool(self.twin.info(self.index[sid]).trampoline)

    def rows_after_attach(self) -> int:
        self.d.ILCT_BlReset()
        self.d.ILCT_BlAttach(TWIN_BASE)
        s = Status()
        self.d.ILCT_BlStatus(ctypes.byref(s))
        return s.hit_rows

    def install(self, gated_off):
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off), (pe.OK, gated_off))
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, pe.OK)

    def test_all_stock_hooks_all_five(self):
        self.install(0)
        self.assertTrue(all(self.hooked(sid) for sid in HOOKS))
        self.assertEqual(self.rows_after_attach(), ALL_ROWS)

    def test_the_rewrite_next_to_hr02_skips_nothing(self):
        # Hunter's Insight replaces the call at 0x30760a with a call into its own code: no row of ours sees it.
        self.twin.poke(OTHER_PATCH, b'\xE8\x11\x22\x33\x44')
        self.install(0)
        self.assertTrue(all(self.hooked(sid) for sid in HOOKS))
        self.assertEqual(self.rows_after_attach(), ALL_ROWS)

    def test_each_gate_skips_only_its_row(self):
        for sid, (gated, va, n) in GATES.items():
            with self.subTest(sid):
                self.twin.reset()
                at = va + n - 1
                self.twin.poke(at, bytes([ctypes.string_at(at, 1)[0] ^ 0xFF]))
                self.install(1)
                self.assertFalse(self.hooked(gated))
                self.assertTrue(all(self.hooked(other) for other in HOOKS if other != gated), 'the rest installs')
                self.assertEqual(self.rows_after_attach(), ALL_ROWS & ~self.MISSING[gated])

    def test_a_rewritten_hook_row_is_skipped_not_refused(self):
        for sid, (va, _expected, _window) in HOOKS.items():
            with self.subTest(sid):
                self.twin.reset()
                self.twin.poke(va, b'\xE9\x00\x00\x00\x00')   # another mod's jump at the same place
                self.install(1)
                self.assertFalse(self.hooked(sid))
                self.assertEqual(ctypes.string_at(va, 5), b'\xE9\x00\x00\x00\x00', 'its bytes stay')
                self.assertEqual(self.rows_after_attach(), ALL_ROWS & ~self.MISSING[sid])

    def test_a_chain_row_that_differs_refuses_nothing(self):
        for va, n in CHAIN_ROWS.values():
            self.twin.poke(va + n - 1, b'\x90')
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off, report.info_mismatch), (pe.OK, 0, 2))


# ---- unicorn on the hooked twin ------------------------------------------------------------------------------------------------
SCRATCH = pe.SCRATCH
TEB, CASTER, TARGET, CREC, TREC, SLOT, ROWA, FRAME = (SCRATCH + 0x50000 + 0x1000 * k for k in range(8))
TID = 0x4D2


@native_test
@unittest.skipUnless(GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires IVALICE_LEGACY_GAME, unicorn and capstone')
class HookWindowEmulationTest(unittest.TestCase):
    """Each hook window in unicorn on the hooked twin, our handler attached and its gate open, against the original
    bytes from the same random states: same continuation, registers, flags and memory (only our ring changes)."""

    TRIALS = 3

    @classmethod
    def setUpClass(cls):
        import test_coop_movement as mv
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        cls.hw.setup(attach=False)
        cls.d.ILCT_BlReset()
        cls.d.ILCT_BlAttach(TWIN_BASE)
        s = Status()
        cls.d.ILCT_BlStatus(ctypes.byref(s))
        assert s.hit_rows == ALL_ROWS, s.hit_rows   # every row hooked on the stock twin
        with quiet_faulthandler():
            cls.emu = mv.MoveEmulator(cls.hw.twin)
        cls.index = {r['id']: k for k, r in enumerate(pe.site_rows())}
        ring, write = u64(), u64()
        cls.d.ILCT_BlRingAddresses(ctypes.byref(ring), ctypes.byref(write))
        cls.ring, cls.write = ring.value, write.value

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_BlReset()
        cls.hw.twin.reset()

    def setUp(self):
        self.d.ILCT_BlSetBracket(0, 0, 0)

    def lay_out(self, uc, outcome=OUT_HIT):
        uc.mem_write(CASTER + ACTOR_HANDLE, struct.pack('<I', FRAN))
        uc.mem_write(CASTER + ACTOR_BATTLE, struct.pack('<Q', CREC))
        uc.mem_write(TARGET + ACTOR_HANDLE, struct.pack('<I', HYENA_C))
        uc.mem_write(TARGET + ACTOR_BATTLE, struct.pack('<Q', TREC))
        uc.mem_write(SLOT + SLOT_OUTCOME, bytes([outcome]))
        uc.mem_write(ROWA + ROW_ELEMENT, b'\x01')
        uc.mem_write(TREC + REC_AFFINITY, bytes([0x01, 0x00, 0x00, 0x00]))
        uc.mem_write(CREC + REC_WEAPON, struct.pack('<H', 0x1005))

    def newest(self, uc):
        w = struct.unpack('<I', bytes(uc.mem_read(self.write, 4)))[0]
        if w == 0:
            return (0, None)
        raw = bytes(uc.mem_read(self.ring + ((w - 1) % 256) * 72, 72))
        return (w, Event.from_buffer_copy(raw))

    def run_window(self, sid, regs, tid=TID, outcome=OUT_HIT):
        emu, uc, twin = self.emu, self.emu.uc, self.hw.twin
        row = self.index[sid]
        r = twin.rows[row]
        site = TWIN_BASE + r['rva']
        window = (site, site + r['hook_len'])
        results = []
        for trial in range(self.TRIALS):
            emu.refresh()
            emu.tick_clock(gt.NOW + 5)
            uc.reg_write(pe.X.UC_X86_REG_GS_BASE, TEB)
            uc.mem_write(TEB + 0x48, struct.pack('<Q', tid))
            self.lay_out(uc, outcome)
            state = pe.random_state(random.Random(0x4848C00 + row * 10 + trial))
            state['gpr'].update(regs)
            state['gpr']['RSP'] = (state['gpr']['RSP'] & ~0xF) - 8 if sid in ('HR-01', 'HR-03', 'KR-01') else \
                state['gpr']['RSP'] & ~0xF
            before = self.newest(uc)[0]
            patched = bytes(uc.mem_read(site, r['hook_len']))
            emu.write_code(site, checked(r)[:r['hook_len']])
            a_ = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or
                         window[0] <= x < window[1], state)
            emu.write_code(site, patched)
            emu.force_gate(row, True)
            try:
                with quiet_faulthandler():
                    b_ = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or
                                 site <= x < site + 5, state, probe=self.newest)
            finally:
                emu.force_gate(row, False)
            touched = set(a_['final']) | set(b_['final'])
            baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
            gt.compare(self, emu, f'{sid} trial {trial}', a_, b_, baseline, state['gpr']['RSP'])
            self.assertEqual(emu.unexpected, [], 'imports the emulator does not answer')
            self.assertEqual(b_['exit'], window[1], 'back after the window')
            w, e = b_['probe']
            results.append((w - before, e))
        return results

    def test_hr01(self):
        for added, e in self.run_window('HR-01', {'RCX': 0x5555_0000 | FIRA, 'RDX': CASTER, 'R8': TARGET}):
            p = payload(e)
            self.assertEqual((added, e.msg, e.key), (1, EV_HIT_BEGIN, TID))
            self.assertEqual((p.actor, p.target, p.action, p.known), (FRAN, HYENA_C, FIRA, HK_ACTOR | HK_TARGET))
            self.assertNotEqual(p.serial, 0)

    def test_hr02(self):
        self.d.ILCT_BlSetBracket(9, TID, gt.NOW)
        regs = {'RDI': CASTER, 'RBX': TARGET, 'R15': 0x6666_0000 | FIRA, 'R14': SLOT, 'R13': ROWA,
                'RAX': 0x1234_5670 | HIT_CRIT}
        for added, e in self.run_window('HR-02', regs, outcome=OUT_MISS):
            p = payload(e)
            self.assertEqual((added, e.msg), (1, EV_HIT_OUTCOME))
            self.assertEqual((p.serial, p.actor, p.target, p.action, p.outcome, p.flags, p.element),
                             (9, FRAN, HYENA_C, FIRA, OUT_MISS, HIT_CRIT, 0x01))
            self.assertEqual((p.actor_record, p.target_record, p.affinity[0], p.weapon, p.known),
                             (CREC, TREC, 0x01, 0x1005, 0x7F))

    def test_hr03_records_only_on_the_brackets_thread(self):
        self.d.ILCT_BlSetBracket(9, TID, gt.NOW)
        regs = {'RCX': TARGET, 'RDX': (-880) & 0xFFFF_FFFF, 'R8': 0, 'R9': 0}
        for added, e in self.run_window('HR-03', regs):
            p = payload(e)
            self.assertEqual((added, e.msg, e.key, p.serial, p.actor, p.value), (1, EV_HIT_NUMBER, TID, 9, HYENA_C,
                                                                                 -880))
        for added, _e in self.run_window('HR-03', regs, tid=TID + 1):
            self.assertEqual(added, 0, "another thread (the Forge's Lua): stock, nothing copied")

    def test_hr05(self):
        self.d.ILCT_BlSetBracket(9, TID, gt.NOW)
        for added, e in self.run_window('HR-05', {'RBP': FRAME}):
            self.assertEqual((added, e.msg, payload(e).serial), (1, EV_HIT_END, 9))
        for added, _e in self.run_window('HR-05', {'RBP': FRAME}, tid=TID + 1):
            self.assertEqual(added, 0, "another thread's bracket stays open")

    def test_kr01(self):
        for added, e in self.run_window('KR-01', {'RCX': HYENA_C, 'RDX': 120, 'R8': 2}, tid=TID + 7):
            p = payload(e)
            self.assertEqual((added, e.msg, e.key, p.actor, p.value, p.value2), (1, EV_KILL, TID + 7, HYENA_C, 120, 2))


if __name__ == '__main__':
    unittest.main()
