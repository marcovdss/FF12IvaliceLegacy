"""Host tests for the battle log (native/coop/src/hud/battle_log*). No game run, nothing deployed; the exe is only read
(IVALICE_LEGACY_GAME, a copy) to check the pinned bytes the feed row and its facts name.

* static: the LG-01 row (a MinHook detour at the message emitter's entry, RENDER, OPTIONAL), its signature; the facts
  restated; every address constant tagged; the game-thread file never blocks, allocates or calls the C runtime and
  LG-01's handler writes no register; the model touches no game memory; wiring (driver pass, boot order, build);
* pinned exe: the entry bytes, the emitter's own first test on block +0x28, where a skipped log draw resumes;
* the event ring: order and sequence numbers, the argument copy (unreadable block = zeros), a lapped ring counts its
  losses, four producer threads against the reader (no mixed entry), an 8-hour soak stays flat;
* LG-01: copy-only (every saved register unchanged, RUN_ORIGINAL), declined when off;
* the model, replayed from recorded sequences: attacks merged (x2 and amounts), attribution to the actor's latest
  action event, area spill, damage taken in red on the victim's line, healing, unattributed Poison/Regen, statuses
  with icons, HOLDING on Lure, wear-off, KO (message and diff make one line), kill and reward line, foe readies
  linger and take their damage, WEAK (element seam), CRIT/MISS hints (seam), the merge window, the history bound;
* presentation: hold/linger/fade timing, the compact choice (5 newest, important lines kept, the oldest dimmed),
  filters, the expanded view (20, no fade), the header on Target Info's line, the glow dot (targetline_p, white
  palette, tinted), icons, the pass budget, the log zone at 20 lines;
* settings: defaults, clamps, the expand toggle;
* the native log: while the log is live the frame hook draws the layer and skips the native log draw (RESUME at the
  call's return), otherwise it calls around as before.
"""
from __future__ import annotations

import ctypes
import re
import struct
import threading
import unittest
from pathlib import Path

import test_coop_frontend as tfe
import test_coop_hud as th
import test_coop_hud_driver as tdrv
import test_coop_hud_target_info as tti
import test_coop_patch_engine as pe
from coop_native_support import CAN_BUILD, REQUIRE_NATIVE, quiet_faulthandler
from test_coop_patch_engine import GAME, TWIN_BASE

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'
NATIVE = CAN_BUILD or REQUIRE_NATIVE

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

# ---- facts (restated) ---------------------------------------------------------------------------------------------------
FEED, FEED_BYTES, ACTION_BOUND = 0x46AB10, bytes.fromhex('405657415648 83ec20'), 0x1FF
FEED_SIGNATURE = '40 56 57 41 56 48 83 ec 20 41 81 78 28 ff 01 00 00'
H0_SITE, LOG_DRAW, LOG_DRAW_RETURN = 0x2BE7DA, 0x2BF230, 0x2BE7DF
RING, EVENT_ARGS = 256, 0x30
EV_ARGS = 1
SIDE_PARTY, SIDE_FOE = 1, 2
OBS_PRESENT, OBS_TARGET = 1, 2
LK_DAMAGE, LK_HEAL, LK_ACTION, LK_STATUS, LK_KO, LK_REVIVE, LK_KILL, LK_REWARD, LK_STEAL, LK_SYSTEM = range(1, 11)
LF_IMPORTANT, LF_PARTY_HIT, LF_ATTACK, LF_SINGLE = 1, 2, 4, 8
TAG_CRIT, TAG_WEAK, TAG_MISS, TAG_HOLDING, TAG_BLOCK, TAG_PARRY, TAG_IMMUNE = 1, 2, 4, 8, 0x10, 0x20, 0x40
DOT_GAMBIT, DOT_FOE, DOT_REWARD, DOT_SYSTEM = 5, 6, 7, 8
CAT_PARTY, CAT_FOE, CAT_STATUS, CAT_REWARD, CAT_SYSTEM = 1, 2, 4, 8, 16
FILTER_ALL, FILTER_PARTY, FILTER_FOES, FILTER_STATUSES, FILTER_MINE = range(5)
VIEW_EXPANDED, VIEW_FEED, VIEW_HITS, VIEW_PINNED, VIEW_KILLS, VIEW_CHAIN = 1, 2, 4, 8, 0x10, 0x20
ICON_OFF = 0x80
ATTRIB_MS, MERGE_MS, REWARD_MS, KO_MERGE_MS = 6000, 2500, 4000, 2000
LIFE_MS, LINGER_MS, FADE_MS, HEADER_HOLD_MS, HINT_MS, LIVE_MS = 6000, 12000, 1000, 20000, 500, 250
HISTORY, COMPACT_LINES, EXPANDED_LINES = 32, 5, 20
MAX_COMMANDS_LOG, MENU_RESERVE, LOG_FLOOR = 44, 48, 12
ATLAS_PARTYTOP_4_C, ATLAS_TARGETLINE_P = 1, 8
HEADER_Y, LINES_DY, LINE_PITCH, LOG_X = 50, 34, 30, 1920 - 47 - 420
LOG_ZONE = (1360, 16, 544, 680)    # the menus keep clear of it (menus_draw.cpp log_zone)
EPOCH, NOW = tdrv.EPOCH, tdrv.NOW
T0 = 100_000

# Status bits.
KO, STONE, DOOM, POISON, LURE, PROTECT, SHELL, REGEN = 0, 1, 6, 8, 17, 18, 19, 25
ATTACK = 150


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Event = S('Event', [('stamp', u32), ('msg', u16), ('flags', u16), ('time_ms', u64), ('key', u64),
                    ('args', u8 * EVENT_ARGS)])
Who = S('Who', [('handle', u32), ('side', u8), ('seat', u8), ('member', u16), ('name', ctypes.c_char * 24)])
Message = S('Message', [('seq', u32), ('msg', u16), ('action', u16), ('time_ms', u64), ('value', i32), ('status', i32),
                        ('level', i32), ('item', i32), ('count', i32), ('reserved', u32), ('a', Who), ('b', Who),
                        ('action_name', ctypes.c_char * 24), ('item_name', ctypes.c_char * 24)])
UnitObs = S('UnitObs', [('handle', u32), ('side', u8), ('seat', u8), ('flags', u8), ('weakness', u8), ('hp', u32),
                        ('hp_max', u32), ('status', u32), ('target', u32), ('action_now', u16), ('member', u16),
                        ('name', ctypes.c_char * 24), ('action_name', ctypes.c_char * 24)])
Line = S('Line', [('id', u32), ('cls', u8), ('flags', u8), ('tags', u8), ('dot', u8), ('categories', u8), ('seats', u8),
                  ('actor_tone', u8), ('target_tone', u8), ('count', u8), ('target_count', u8),
                  ('amount_count', u8), ('icon_count', u8), ('icons', u8 * 4), ('action', u16), ('reserved', u16),
                  ('actor', u32), ('target', u32), ('amounts', i32 * 4), ('born_ms', u64), ('touched_ms', u64),
                  ('actor_name', ctypes.c_char * 24), ('body', ctypes.c_char * 24),
                  ('target_name', ctypes.c_char * 24), ('extra', ctypes.c_char * 24), ('exp', u32), ('lp', u16),
                  ('chain', u16), ('chain_level', u8), ('reward', u8), ('reserved3', u8 * 6)])
View = S('View', [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64), ('filter', u8),
                  ('filter_seat', u8), ('line_count', u8), ('reserved', u8), ('seat_colour', u32 * 4),
                  ('reserved2', u32), ('lines', Line * HISTORY)])
Counters = S('Counters', [(n, u64) for n in ('pushed', 'declined', 'drained', 'filtered', 'unresolved', 'lines',
                                             'published', 'withdrawn', 'drawn', 'skipped', 'capped', 'toggles',
                                             'native_hidden', 'hits', 'numbers', 'foreign', 'kills', 'chain_off')])
Status = S('Status', [('attached', u8), ('feed', u8), ('enabled', u8), ('expanded', u8), ('hit_rows', u8),
                      ('chain', u8), ('reserved', u8 * 2)])
Settings = S('Settings', [('enabled', u8), ('expanded', u8), ('expand_control', u8), ('filter', u8),
                          ('filter_seat', u8), ('reserved', u8 * 3)])
RingView = S('RingView', [('written', u32), ('read', u32), ('lost', u64), ('torn', u64), ('unreadable', u64)])
Frame = th.Frame

SIGNATURES = {
    'ILCT_BlSizes': ([P(u32), u32], u32), 'ILCT_BlReset': ([], None), 'ILCT_BlSetImageBase': ([u64], None),
    'ILCT_BlSetAttached': ([i32], None), 'ILCT_BlSetNamesOnly': ([i32], None), 'ILCT_BlFeedRow': ([], i32),
    'ILCT_BlRingPush': ([u32, u64, u64, u64], None), 'ILCT_BlRingDrain': ([P(Event), u32, P(u32)], u32),
    'ILCT_BlRingStats': ([P(RingView)], None), 'ILCT_BlRingBytes': ([], u64),
    'ILCT_BlRingSoak': ([u64, u32, P(u32)], u64),
    'ILCT_BlModelReset': ([], None), 'ILCT_BlModelMessage': ([P(Message)], u32),
    'ILCT_BlModelUnits': ([u64, P(UnitObs), u32, i32], None), 'ILCT_BlModelHint': ([u32, u32, u64], None),
    'ILCT_BlModelLines': ([P(Line), u32], u32),
    'ILCT_BlLineText': ([P(Line), ctypes.c_char_p, u32], u32), 'ILCT_BlTimeAlpha': ([P(Line), u64], u32),
    'ILCT_BlPasses': ([P(Line), u32, u32], i32), 'ILCT_BlSelect': ([P(View), u64, P(u8), P(u8), u32], u32),
    'ILCT_BlHeaderShown': ([P(View), u64], i32),
    'ILCT_BlCompose': ([P(View), u64, P(Frame), u32, u32], u32),
    'ILCT_BlBuildView': ([P(View), u32, u64, u32, u32, u32, P(u32)], None),
    'ILCT_BlConfigure': ([u32, u32, u32, u32, u32], None), 'ILCT_BlCurrent': ([P(Settings)], None),
    'ILCT_BlDefaults': ([P(Settings)], None), 'ILCT_BlToggle': ([], None),
    'ILCT_BlHandler': ([P(th.HookCtx), u32], i32), 'ILCT_BlStep': ([P(tdrv.Snapshot), u64, P(View)], i32),
    'ILCT_BlPublish': ([P(View)], None), 'ILCT_BlLatest': ([P(View)], i32), 'ILCT_BlReplaces': ([u32, u64], i32),
    'ILCT_BlDraw': ([P(tdrv.Snapshot), u64, P(Frame)], u32), 'ILCT_BlStats': ([P(Counters)], None),
    'ILCT_BlStatus': ([P(Status)], None), 'ILCT_BlResolve': ([u64, P(Who)], i32),
}
_dll: dict = {}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def shared_dll():
    if 'd' not in _dll:
        _dll['d'] = declare(ctypes.WinDLL(str(pe.built()['test'])))
    return _dll['d']


def stats(d) -> Counters:
    c = Counters()
    d.ILCT_BlStats(ctypes.byref(c))
    return c


def who(handle=0, side=0, seat=0, name=b'', member=0xFFFF) -> Who:
    w = Who()
    w.handle, w.side, w.seat, w.member, w.name = handle, side, seat, member, name
    return w


# ---- a cast for the replays ----------------------------------------------------------------------------------------------
BALTHIER, FRAN, ASHE, PENELO = 0x0010_0001, 0x0010_0002, 0x0010_0003, 0x0010_0004
HYENA_A, HYENA_B, HYENA_C, QUEEN = 0x0030_0001, 0x0030_0002, 0x0030_0003, 0x0030_0009
NAMES = {BALTHIER: b'Balthier', FRAN: b'Fran', ASHE: b'Ashe', PENELO: b'Penelo', HYENA_A: b'Hyena A',
         HYENA_B: b'Hyena B', HYENA_C: b'Hyena C', QUEEN: b'Mimic Queen'}
SEATS = {BALTHIER: 1, FRAN: 2, ASHE: 3, PENELO: 0}
FIRA, ICE_ACT, CURE, PROTECT_ACT, BITE, HELL_BLASTER, UNKNOWN_ELEMENT = 0x40, 0x41, 0x30, 0x31, 0x60, 0x70, 0x50
ACTION_NAMES = {FIRA: b'Fira', ICE_ACT: b'Blizzara', CURE: b'Cure', PROTECT_ACT: b'Protect', BITE: b'Bite',
                HELL_BLASTER: b'Hell Blaster', UNKNOWN_ELEMENT: b'Ruin', ATTACK: b''}


class Scene:
    """Units sampled at given times and feed messages, through the pure model."""

    def __init__(self, d, element=False):
        self.d = d
        self.element = 1 if element else 0
        self.units: dict[int, dict] = {}
        d.ILCT_BlModelReset()

    def unit(self, handle, hp, hp_max=None, target=0, action=0xFFFF, status=0, weakness=0):
        party = handle in SEATS
        self.units[handle] = dict(handle=handle, side=SIDE_PARTY if party else SIDE_FOE, seat=SEATS.get(handle, 0),
                                  hp=hp, hp_max=hp_max or max(hp, 1), target=target, action=action, status=status,
                                  weakness=weakness)
        return self

    def set(self, handle, **kw):
        self.units[handle].update(kw)
        return self

    def sample(self, t):
        arr = (UnitObs * len(self.units))()
        for k, u in enumerate(self.units.values()):
            o = arr[k]
            o.handle, o.side, o.seat, o.flags = u['handle'], u['side'], u['seat'], OBS_PRESENT | OBS_TARGET
            o.weakness, o.hp, o.hp_max, o.status, o.target = u['weakness'], u['hp'], u['hp_max'], u['status'], u['target']
            o.action_now, o.member = u['action'], 0xFFFF
            o.name = NAMES[u['handle']]
            o.action_name = ACTION_NAMES.get(u['action'], b'') if u['action'] != 0xFFFF else b''
        self.d.ILCT_BlModelUnits(t, arr, len(self.units), self.element)
        return self

    def msg(self, t, msg, a=0, b=0, action=0xFFFF, item_name=b'', value=0, status=0, count=0, level=0, item=0) -> int:
        m = Message()
        m.msg, m.time_ms, m.action, m.value, m.status, m.count, m.level, m.item = (msg, t, action, value, status,
                                                                                  count, level, item)
        for slot, h in (('a', a), ('b', b)):
            if h:
                setattr(m, slot, who(h, SIDE_PARTY if h in SEATS else SIDE_FOE, SEATS.get(h, 0), NAMES[h]))
        m.action_name = ACTION_NAMES.get(action, b'')
        m.item_name = item_name
        return self.d.ILCT_BlModelMessage(ctypes.byref(m))

    def lines(self) -> list:
        arr = (Line * HISTORY)()
        n = self.d.ILCT_BlModelLines(arr, HISTORY)
        return [arr[k] for k in range(n)]

    def texts(self) -> list[str]:
        return [text(self.d, line) for line in self.lines()]


def text(d, line) -> str:
    buf = ctypes.create_string_buffer(200)
    d.ILCT_BlLineText(ctypes.byref(line), buf, 200)
    return buf.value.decode('ascii')


def make_line(id_, cls=LK_DAMAGE, touched=T0, flags=0, categories=CAT_PARTY, seats=1, actor=b'Vaan', body=b'',
              amounts=(), dot=1, icons=()) -> Line:
    line = Line()
    line.id, line.cls, line.flags, line.categories, line.seats, line.dot = id_, cls, flags, categories, seats, dot
    line.born_ms, line.touched_ms, line.count = touched, touched, 1
    line.actor_name, line.body, line.actor_tone = actor, body, 1
    line.action = 0xFFFF
    for k, a in enumerate(amounts):
        line.amounts[k] = a
    line.amount_count = len(amounts)
    for k, i in enumerate(icons):
        line.icons[k] = i
    line.icon_count = len(icons)
    return line


def make_view(lines, flags=0, filter_=FILTER_ALL, seat=1, epoch=EPOCH, built=T0) -> View:
    v = View()
    v.epoch, v.flags, v.built_ms, v.filter, v.filter_seat = epoch, flags, built, filter_, seat
    v.seat_colour[0], v.seat_colour[1] = th.rgba(0x78, 0x5F, 0x19), th.rgba(0x23, 0x41, 0x78)
    v.seat_colour[2], v.seat_colour[3] = th.rgba(0x1E, 0x69, 0x37), th.rgba(0x55, 0x32, 0x76)
    for k, line in enumerate(lines):
        v.lines[k] = line
    v.line_count = len(lines)
    return v


def code_only(path: Path) -> str:
    """The source without comments and string literals (prose and names are not calls)."""
    text_ = path.read_text(encoding='utf-8')
    text_ = re.sub(r'"(?:\\.|[^"\\])*"', '""', text_)
    return '\n'.join(line.split('//', 1)[0] for line in text_.splitlines())


# ---- static ------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_lg01_row(self):
        legacy = (COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8')
        self.assertIn('"id": "LG-01"', legacy)
        self.assertIn(f'"pattern": "{FEED_SIGNATURE}", "site_offset": 0, "hits_in_text": 1', legacy)
        gen = (COOP / 'tools' / 'gen_site_table.py').read_text(encoding='utf-8')
        self.assertIn("'LG-01':", gen[gen.index('OPTIONAL_BY_DECISION'):gen.index('INTERIOR_WAIVED')])
        inc = (COOP / 'src' / 'patch' / 'sites.inc').read_text(encoding='utf-8')
        row = next(line for line in inc.splitlines() if '"LG-01"' in line)
        self.assertIn('0x34AB10u, 9, ILC_SITE_KIND_DETOUR, ILC_SITE_POLICY_PATCH, ILC_SITE_SUBSYS_RENDER, 0x08', row)
        self.assertIn('"\\x40\\x56\\x57\\x41\\x56\\x48\\x83\\xEC\\x20", 5,', row, 'hook window: the three pushes')

    @unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
    def test_lg01_parsed(self):
        rows = {r['id']: r for r in pe.site_rows()}
        r = rows['LG-01']
        self.assertEqual((r['rva'], r['length'], r['kind'], r['policy'], r['subsystem']),
                         (FEED - 0x120000, 9, 'DETOUR', 'PATCH', 'RENDER'))
        self.assertEqual(r['expected'], FEED_BYTES)
        self.assertTrue(r['flags'] & 0x08, 'OPTIONAL: another mod hooking the entry skips the row, refuses nothing')
        self.assertFalse(r['flags'] & 0x01, 'no interior reference into the hook window')

    def test_address_constants_are_tagged(self):
        for name in ('battle_log_facts.h', 'battle_log.h', 'battle_log.cpp', 'battle_log_model.cpp',
                     'battle_log_sample.cpp', 'battle_log_internal.h'):
            for n, line in enumerate((HUD_SRC / name).read_text(encoding='utf-8').splitlines(), 1):
                self.assertEqual(pe.untagged_constants(line), [], f'{name}:{n}: {line.strip()}')

    def test_facts_restated(self):
        facts = (HUD_SRC / 'battle_log_facts.h').read_text(encoding='utf-8')
        for needle in ('FN_MESSAGE = 0x46AB10', 'ARGS_SIZE = 0x30', 'ARG_ACTOR_A = 0x00', 'ARG_ACTOR_B = 0x08',
                       'ARG_STATUS = 0x18', 'ARG_ITEM = 0x20', 'ARG_ACTION = 0x28', 'ACTION_BOUND = 0x1FF',
                       'REC_MEMBER = 0x04', 'REC_SIDE = 0x05', 'ACTOR_TARGET = 0x710', 'ACTOR_ACTION = 0x714',
                       'ACTOR_BATTLE = 0x698', 'UNIT_NAME = 0xF8', 'UNIT_INSTANCE = 0x100',
                       'ACTOR_MANAGER_PTR = 0x2098E18', 'MSG_TEMPLATES = 102'):
            self.assertIn(needle, facts)
        # The same facts other modules read.
        self.assertIn('ACTOR_ACTION = 0x714', (HUD_SRC / 'party_rows.cpp').read_text(encoding='utf-8'))
        self.assertIn('ACTOR_MANAGER_PTR = 0x2098E18',
                      (COOP / 'src' / 'targets' / 'targets_facts.h').read_text(encoding='utf-8'))
        hud = (HUD_SRC / 'hud_facts.h').read_text(encoding='utf-8')
        self.assertIn('LOG_DRAW_RETURN = 0x2BE7DF', hud)

    def test_game_thread_code_never_blocks_allocates_or_calls_the_c_runtime(self):
        text_ = code_only(HUD_SRC / 'battle_log.cpp')
        for forbidden in ('Sleep(', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                          'copy_to_caller', 'memcpy(', 'memset(', 'session::', 'game_reader', 'snprintf', 'strlen'):
            self.assertNotIn(forbidden, text_, forbidden)
        model = code_only(HUD_SRC / 'battle_log_model.cpp')
        for forbidden in ('game_reader', 'copy_from_caller', 'load_from_caller', 'session::', 'malloc', 'new ',
                          'Sleep(', 'WaitFor'):
            self.assertNotIn(forbidden, model, forbidden)
        sample = code_only(HUD_SRC / 'battle_log_sample.cpp')
        for forbidden in ('memory_write', 'copy_to_caller', 'VirtualProtect', 'WriteProcessMemory', 'Sleep(', 'WaitFor'):
            self.assertNotIn(forbidden, sample, forbidden)

    def test_the_feed_handler_is_copy_only(self):
        text_ = (HUD_SRC / 'battle_log.cpp').read_text(encoding='utf-8')
        body = text_[text_.index('int32_t lg01(patch::ctx* c'):]
        body = body[:body.index('\n}\n')]
        self.assertEqual(re.findall(r'c->\w+\s*=[^=]', body), [], 'no saved register is written')
        self.assertEqual(body.count('return patch::RUN_ORIGINAL;'), 2, 'the original always runs')
        self.assertNotIn('RESUME', body)
        push = text_[text_.index('void ring_push('):]
        push = push[:push.index('\n}\n')]
        self.assertIn('copy_from_caller(e.args', push, 'the argument block is read through the fault-safe probe')

    def test_wiring(self):
        driver = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        body = driver[driver.index('void pass_battle_log'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('battle_log::draw(cv, in);', body)
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        self.assertLess(boot.index('hud::snapshot::step(now);'), boot.index('hud::battle_log::step(now);'))
        self.assertLess(boot.index('hud::menus_draw::step(now);'), boot.index('hud::battle_log::step(now);'),
                        "after the menus' step: their name cache is checked for this epoch first")
        self.assertLess(boot.index('hud::battle_log::attach('), boot.index('gtq::seal();'))
        self.assertIn('hud::battle_log::stop();', boot)
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("'src/hud/battle_log.cpp', 'src/hud/battle_log_model.cpp', 'src/hud/battle_log_sample.cpp'",
                      build)
        self.assertIn("'testsupport/battle_log_tests.cpp'", build)
        replay = (HUD_SRC / 'replay.cpp').read_text(encoding='utf-8')
        h0 = replay[replay.index('int32_t h0(patch::ctx* c'):]
        h0 = h0[:h0.index('\n}\n')]
        self.assertIn('battle_log::replaces_native(epoch', h0)
        self.assertIn('c->resume = game(facts::LOG_DRAW_RETURN);', h0)
        self.assertLess(h0.index('if (draw_frame(epoch))'), h0.index('return patch::RESUME;'),
                        'the native log is skipped only once the layer was drawn in its place')


@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the pinned exe, read only)')
class PinnedBytesTest(unittest.TestCase):
    def test_entry_and_the_emitters_first_test(self):
        b = read_exe(FEED, 22)
        self.assertEqual(b[:9], FEED_BYTES, 'push rsi; push rdi; push r14; sub rsp, 0x20')
        self.assertEqual(b[9:17], bytes.fromhex('41817828ff010000'), 'cmp dword [r8+0x28], 0x1ff: the action bound')
        self.assertEqual(b[17:22], bytes.fromhex('4d8bf08bfa'), 'mov r14, r8; mov edi, edx: block and message id')

    def test_signature_is_unique(self):
        self.assertEqual(count_in_text(bytes.fromhex(FEED_SIGNATURE)), 1)

    def test_a_skipped_log_draw_resumes_after_the_call(self):
        call = read_exe(H0_SITE, 7)
        self.assertEqual(call[0], 0xE8)
        self.assertEqual(H0_SITE + 5 + struct.unpack('<i', call[1:5])[0], LOG_DRAW)
        self.assertEqual(call[5:7], bytes.fromhex('33c0'), 'xor eax, eax at the return address')
        self.assertEqual(H0_SITE + 5, LOG_DRAW_RETURN)


def _exe() -> bytes:
    if '_exe' not in _dll:
        _dll['_exe'] = (Path(GAME) / 'x64' / 'FFXII_TZA.exe').read_bytes()
    return _dll['_exe']


def _sections(b: bytes):
    pe_off = struct.unpack_from('<I', b, 60)[0]
    n = struct.unpack_from('<H', b, pe_off + 6)[0]
    opt = struct.unpack_from('<H', b, pe_off + 20)[0]
    out = []
    for k in range(n):
        o = pe_off + 24 + opt + 40 * k
        vs, va, rs, ra = struct.unpack_from('<IIII', b, o + 8)
        out.append((b[o:o + 8].rstrip(b'\0'), va, vs, ra, rs))
    return out


def read_exe(va: int, n: int) -> bytes:
    b = _exe()
    rva = va - 0x120000
    for _name, sva, vs, ra, _rs in _sections(b):
        if sva <= rva < sva + vs:
            return b[ra + rva - sva:ra + rva - sva + n]
    raise AssertionError(f'{va:#x} not in the image')


def count_in_text(pattern: bytes) -> int:
    b = _exe()
    _name, _va, _vs, ra, rs = next(s for s in _sections(b) if s[0] == b'.text')
    return len(re.findall(re.escape(pattern), b[ra:ra + rs]))


# ---- the ring and LG-01 ----------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class RingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def drain(self, cap=RING):
        arr = (Event * RING)()
        lost = u32()
        n = self.d.ILCT_BlRingDrain(arr, cap, ctypes.byref(lost))
        return [arr[k] for k in range(n)], lost.value

    def ring(self) -> RingView:
        v = RingView()
        self.d.ILCT_BlRingStats(ctypes.byref(v))
        return v

    def test_struct_mirrors(self):
        out = (u32 * 16)()
        n = self.d.ILCT_BlSizes(out, 16)
        self.assertEqual(list(out[:n])[:12],
                         [ctypes.sizeof(Event), ctypes.sizeof(Who), ctypes.sizeof(Message), ctypes.sizeof(UnitObs),
                          ctypes.sizeof(Line), ctypes.sizeof(View), ctypes.sizeof(Counters), ctypes.sizeof(Status),
                          ctypes.sizeof(Settings), ctypes.sizeof(RingView), View.lines.offset,
                          Line.actor_name.offset])
        self.assertEqual(out[13], ctypes.sizeof(Frame))

    def test_order_sequence_numbers_and_the_copy(self):
        blocks = [ctypes.create_string_buffer(bytes(range(k, k + EVENT_ARGS)), EVENT_ARGS) for k in range(5)]
        for k, blk in enumerate(blocks):
            self.d.ILCT_BlRingPush(13 + k, 0x1000 + k, ctypes.addressof(blk), T0 + k)
        events, lost = self.drain()
        self.assertEqual(lost, 0)
        self.assertEqual([e.stamp // 2 for e in events], [1, 2, 3, 4, 5], 'sequence numbers from 1, in order')
        self.assertEqual([e.msg for e in events], [13, 14, 15, 16, 17])
        self.assertEqual([e.key for e in events], [0x1000 + k for k in range(5)])
        self.assertEqual([e.time_ms for e in events], [T0 + k for k in range(5)])
        for k, e in enumerate(events):
            self.assertEqual(e.flags, EV_ARGS)
            self.assertEqual(bytes(e.args), bytes(range(k, k + EVENT_ARGS)))
        self.assertEqual(self.drain(), ([], 0), 'drained once')
        v = self.ring()
        self.assertEqual((v.written, v.read, v.lost), (5, 5, 0))

    def test_an_unreadable_block_is_zeros_and_counted(self):
        self.d.ILCT_BlRingPush(16, 1, 8, T0)      # address 8: never readable
        self.d.ILCT_BlRingPush(16, 2, 0, T0)      # no block
        events, _ = self.drain()
        self.assertEqual([(e.flags, bytes(e.args)) for e in events], [(0, bytes(EVENT_ARGS))] * 2)
        self.assertEqual(self.ring().unreadable, 2)

    def test_a_lapped_ring_counts_its_losses(self):
        blk = ctypes.create_string_buffer(EVENT_ARGS)
        for k in range(300):
            self.d.ILCT_BlRingPush(15, k, ctypes.addressof(blk), T0 + k)
        events, lost = self.drain()
        self.assertEqual((len(events), lost), (RING, 300 - RING))
        self.assertEqual([e.key for e in events], list(range(300 - RING, 300)), 'the newest RING, oldest first')
        self.assertEqual(self.ring().lost, 300 - RING)

    def test_a_partial_drain_keeps_the_rest(self):
        blk = ctypes.create_string_buffer(EVENT_ARGS)
        for k in range(10):
            self.d.ILCT_BlRingPush(15, k, ctypes.addressof(blk), T0)
        first, _ = self.drain(4)
        rest, _ = self.drain()
        self.assertEqual([e.key for e in first + rest], list(range(10)))

    def test_four_producers_against_the_reader_never_mix_an_entry(self):
        d = self.d
        per, threads = 20000, 4
        blocks = []
        for t in range(threads):
            blocks.append([ctypes.create_string_buffer(struct.pack('<Q', (t << 32) | k) * 6, EVENT_ARGS)
                           for k in range(64)])
        seen, bad = [], []
        done = threading.Event()

        def producer(t):
            for k in range(per):
                d.ILCT_BlRingPush(15, (t << 32) | (k % 64), ctypes.addressof(blocks[t][k % 64]), k)

        def reader():
            arr = (Event * RING)()
            lost = u32()
            while True:
                stop = done.is_set()
                n = d.ILCT_BlRingDrain(arr, RING, ctypes.byref(lost))
                for k in range(n):
                    e = arr[k]
                    if bytes(e.args) != struct.pack('<Q', e.key) * 6:
                        bad.append(e.key)
                    seen.append(e.stamp // 2)
                if stop and n == 0:
                    break

        workers = [threading.Thread(target=producer, args=(t,)) for t in range(threads)]
        r = threading.Thread(target=reader)
        r.start()
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        done.set()
        r.join()
        self.assertEqual(bad, [], 'every drained entry is one producer\'s whole copy')
        self.assertEqual(seen, sorted(seen), 'drained in sequence order')
        v = self.ring()
        self.assertEqual(v.written, per * threads)
        self.assertEqual(len(seen) + v.lost + (v.written - v.read), per * threads,
                         'every message drained, counted lost, or still waiting')

    def test_eight_hour_soak_stays_flat(self):
        # 8 hours of a busy fight at ten messages per second, drained every beat (bursts of up to 37 messages).
        total = 8 * 3600 * 10
        behind = u32()
        drained = self.d.ILCT_BlRingSoak(total, 37, ctypes.byref(behind))
        v = self.ring()
        self.assertEqual((drained, v.lost, v.written), (total, 0, total))
        self.assertLessEqual(behind.value, RING)
        self.assertEqual(self.d.ILCT_BlRingBytes(), RING * ctypes.sizeof(Event), 'a fixed ring: 256 entries')
        # A reader that falls behind loses the oldest, never grows anything.
        self.d.ILCT_BlReset()
        drained = self.d.ILCT_BlRingSoak(10000, 1000, ctypes.byref(behind))
        v = self.ring()
        self.assertEqual(drained + v.lost, 10000)
        self.assertEqual(drained, 10 * RING)


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class FeedHandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def ctx(self, block) -> th.HookCtx:
        c = th.HookCtx()
        for k, name in enumerate(n for n, _ in th.HookCtx._fields_ if n not in ('xmm',)):
            setattr(c, name, 0x1111_0000 + k)
        for k in range(12):
            c.xmm[k] = 0x2222_0000 + k
        c.rcx, c.rdx, c.r8 = 0xC0FFEE, 14, ctypes.addressof(block)
        c.index = self.d.ILCT_BlFeedRow()
        return c

    def test_copy_only(self):
        block = ctypes.create_string_buffer(bytes(range(0x30)), 0x30)
        self.d.ILCT_BlSetAttached(1)
        c = self.ctx(block)
        before = bytes(c)
        self.assertEqual(self.d.ILCT_BlHandler(ctypes.byref(c), EPOCH), 0, 'RUN_ORIGINAL')
        self.assertEqual(bytes(c), before, 'every saved register, the flags and the resume slot unchanged')
        self.assertEqual(bytes(block), bytes(range(0x30)), 'the block is only read')
        arr = (Event * 4)()
        lost = u32()
        self.assertEqual(self.d.ILCT_BlRingDrain(arr, 4, ctypes.byref(lost)), 1)
        e = arr[0]
        self.assertEqual((e.msg, e.key, e.flags, bytes(e.args)), (14, 0xC0FFEE, EV_ARGS, bytes(range(0x30))))
        self.assertEqual(stats(self.d).pushed, 1)

    def test_declined_when_not_attached_or_off(self):
        block = ctypes.create_string_buffer(0x30)
        c = self.ctx(block)
        self.assertEqual(self.d.ILCT_BlHandler(ctypes.byref(c), EPOCH), 0)
        self.d.ILCT_BlSetAttached(1)
        self.d.ILCT_BlConfigure(0, 0, 0, 0, 1)
        self.assertEqual(self.d.ILCT_BlHandler(ctypes.byref(c), EPOCH), 0)
        v = RingView()
        self.d.ILCT_BlRingStats(ctypes.byref(v))
        self.assertEqual(v.written, 0, 'nothing copied')
        self.assertEqual(stats(self.d).declined, 2)


# ---- the model, replayed ----------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class ModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def test_attacks_merge_into_one_line(self):
        s = Scene(self.d).unit(BALTHIER, 2362, target=HYENA_C, action=ATTACK).unit(HYENA_C, 2782).sample(T0)
        s.set(HYENA_C, hp=2370).sample(T0 + 500)
        s.set(HYENA_C, hp=1644).sample(T0 + 1500)
        lines = s.lines()
        self.assertEqual(s.texts(), ['Balthier attacks Hyena C x2  412, 726'])
        line = lines[0]
        self.assertEqual((line.cls, line.count, line.dot, line.actor_tone), (LK_DAMAGE, 2, 1, 1))
        self.assertTrue(line.flags & LF_ATTACK and line.flags & LF_SINGLE)
        self.assertFalse(line.flags & LF_PARTY_HIT, 'a foe took it: no red')
        self.assertEqual(list(line.amounts[:2]), [-412, -726])

    def test_attribution_goes_to_the_latest_action_event(self):
        s = Scene(self.d).unit(BALTHIER, 2362, target=HYENA_C, action=ATTACK)
        s.unit(FRAN, 1325, target=HYENA_C).unit(HYENA_C, 2782).sample(T0)
        s.msg(T0 + 100, 13, a=FRAN, action=FIRA)            # Fran begins casting Fira: no line yet
        self.assertEqual(s.texts(), [])
        s.set(HYENA_C, hp=1902).sample(T0 + 2000)
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena C  880'])
        # After ATTRIB_MS the event is too old: the next hit is the actor aiming with an attack (Balthier first).
        s.set(HYENA_C, hp=1500).sample(T0 + 100 + ATTRIB_MS + MERGE_MS + 1)
        self.assertEqual(s.texts()[-1], 'Balthier attacks Hyena C  402')

    def test_an_area_action_takes_its_other_victims(self):
        s = Scene(self.d).unit(FRAN, 1325, target=HYENA_A).unit(HYENA_A, 2000).unit(HYENA_B, 2000).sample(T0)
        s.msg(T0 + 50, 13, a=FRAN, action=FIRA)
        s.set(HYENA_B, hp=1098).set(HYENA_A, hp=1120).sample(T0 + 1500)   # B listed after A, but only A is aimed at
        self.assertEqual(s.texts(), ['Fran: Fira on Hyena A +1  880, 902'])
        line = s.lines()[0]
        self.assertEqual(line.target_count, 2)
        self.assertFalse(line.flags & LF_SINGLE, 'two targets: no "x2"')

    def test_damage_taken_is_red_on_the_victims_line(self):
        s = Scene(self.d).unit(FRAN, 1325).unit(HYENA_B, 900, target=FRAN, action=BITE).sample(T0)
        s.set(FRAN, hp=968).sample(T0 + 300)
        self.assertEqual(s.texts(), ['Hyena B: Bite on Fran  -357'])
        line = s.lines()[0]
        self.assertTrue(line.flags & LF_PARTY_HIT)
        self.assertEqual((line.dot, line.actor_tone, line.target_tone), (DOT_FOE, 6, 2))
        self.assertEqual(line.seats, 0b10, "Fran's seat: the Mine filter finds it")
        self.assertTrue(line.categories & CAT_FOE)

    def test_healing_from_a_gambit_ally(self):
        s = Scene(self.d).unit(PENELO, 1528, target=FRAN).unit(FRAN, 968, hp_max=1325).sample(T0)
        s.msg(T0 + 10, 13, a=PENELO, action=CURE)
        s.set(FRAN, hp=1316).sample(T0 + 1200)
        self.assertEqual(s.texts(), ['Penelo: Cure on Fran  +348'])
        line = s.lines()[0]
        self.assertEqual((line.cls, line.dot), (LK_HEAL, DOT_GAMBIT))

    def test_poison_and_regen_name_an_unattributed_change(self):
        s = Scene(self.d).unit(FRAN, 1000, status=1 << POISON).sample(T0)
        s.set(FRAN, hp=988).sample(T0 + 1000)
        s.set(FRAN, hp=976).sample(T0 + 2000)
        self.assertEqual(s.texts(), ['Fran: Poison x2  -12, -12'])
        s = Scene(self.d).unit(ASHE, 900, status=1 << REGEN).sample(T0)
        s.set(ASHE, hp=940).sample(T0 + 500)
        self.assertEqual(s.texts(), ['Ashe: Regen  +40'])
        s = Scene(self.d).unit(ASHE, 900).sample(T0)
        s.set(ASHE, hp=800).sample(T0 + 500)
        self.assertEqual(s.texts(), ['Ashe  -100'], 'nobody known: the victim line alone')

    def test_status_lands_with_its_icon_on_the_casters_line(self):
        s = Scene(self.d).unit(PENELO, 1528, target=FRAN).unit(FRAN, 1325).sample(T0)
        s.msg(T0 + 10, 13, a=PENELO, action=PROTECT_ACT)
        s.set(FRAN, status=1 << PROTECT).sample(T0 + 900)
        self.assertEqual(s.texts(), ['Penelo: Protect on Fran'])
        line = s.lines()[0]
        self.assertEqual((line.cls, line.icon_count, line.icons[0]), (LK_STATUS, 1, PROTECT))
        self.assertTrue(line.categories & CAT_STATUS)

    def test_lure_is_holding(self):
        s = Scene(self.d).unit(ASHE, 1444).sample(T0)
        s.set(ASHE, status=1 << LURE).sample(T0 + 200)
        line = s.lines()[0]
        self.assertEqual((line.icons[0], line.tags), (LURE, TAG_HOLDING))
        self.assertEqual(s.texts(), ['Ashe Lure  HOLDING'])

    def test_statuses_wearing_off_share_a_line(self):
        s = Scene(self.d).unit(FRAN, 1325, status=(1 << PROTECT) | (1 << SHELL)).sample(T0)
        s.set(FRAN, status=0).sample(T0 + 100)
        line = s.lines()[0]
        self.assertEqual(list(line.icons[:line.icon_count]), [PROTECT | ICON_OFF, SHELL | ICON_OFF])
        self.assertEqual(s.texts(), ['Fran Protect off Shell off'])

    def test_ko_message_and_diff_make_one_line(self):
        s = Scene(self.d).unit(FRAN, 300).unit(HYENA_B, 900, target=FRAN, action=BITE).sample(T0)
        s.set(FRAN, hp=0, status=1 << KO).sample(T0 + 300)
        s.msg(T0 + 600, 16, b=FRAN)
        kos = [line for line in s.lines() if line.cls == LK_KO]
        self.assertEqual(len(kos), 1)
        self.assertTrue(kos[0].flags & LF_IMPORTANT)
        self.assertEqual(text(self.d, kos[0]), 'Fran has fallen')
        self.assertIn('Hyena B: Bite on Fran  -300', s.texts())

    def test_kill_line_gathers_loot_and_gil(self):
        s = Scene(self.d).unit(BALTHIER, 2000, target=HYENA_A, action=ATTACK).unit(HYENA_A, 200).sample(T0)
        s.set(HYENA_A, hp=0, status=1 << KO).sample(T0 + 400)
        s.msg(T0 + 800, 36, item_name=b'Wolf Pelt', count=1)
        s.msg(T0 + 900, 38, value=120)
        s.msg(T0 + 950, 16, b=HYENA_A)          # the native "has fallen" joins the kill line
        kills = [line for line in s.lines() if line.cls == LK_KILL]
        self.assertEqual(len(kills), 1)
        self.assertEqual(text(self.d, kills[0]), 'Hyena A defeated  Wolf Pelt, 120 gil')
        self.assertEqual((kills[0].dot, kills[0].categories & CAT_REWARD), (DOT_REWARD, CAT_REWARD))
        s.msg(T0 + 400 + REWARD_MS + 1, 36, item_name=b'Fire Stone', count=2)
        self.assertEqual(s.texts()[-1], 'Obtained Fire Stone x2', 'too late for the kill line: its own line')

    def test_a_foes_readies_lingers_and_takes_its_damage(self):
        s = Scene(self.d).unit(FRAN, 1325).unit(QUEEN, 9000, target=FRAN, action=HELL_BLASTER).sample(T0)
        s.msg(T0 + 100, 14, a=QUEEN, action=HELL_BLASTER)
        self.assertEqual(s.texts(), ['Mimic Queen readies Hell Blaster'])
        self.assertTrue(s.lines()[0].flags & LF_IMPORTANT)
        s.set(FRAN, hp=125).sample(T0 + 1900)
        self.assertEqual(s.texts(), ['Mimic Queen: Hell Blaster on Fran  -1200'])
        line = s.lines()[0]
        self.assertEqual(line.cls, LK_DAMAGE)
        self.assertTrue(line.flags & LF_IMPORTANT and line.flags & LF_PARTY_HIT)

    def test_a_party_members_cast_shows_no_line_of_its_own(self):
        s = Scene(self.d)
        self.assertEqual(s.msg(T0, 13, a=FRAN, action=FIRA), 0)
        self.assertEqual(s.msg(T0, 14, a=FRAN, action=FIRA), 0)
        self.assertNotEqual(s.msg(T0, 15, a=FRAN, action=CURE), 0, '"uses": the line its effects merge into')
        self.assertNotEqual(s.msg(T0, 13, a=HYENA_B, action=BITE), 0, "a foe's cast is a tell")

    def test_weak_needs_a_known_element(self):
        s = Scene(self.d, element=True).unit(FRAN, 1325, target=HYENA_C).unit(HYENA_C, 2782, weakness=0x01).sample(T0)
        s.msg(T0 + 10, 13, a=FRAN, action=FIRA)
        s.set(HYENA_C, hp=1902).sample(T0 + 1000)
        self.assertEqual(s.lines()[-1].tags, TAG_WEAK)
        self.assertTrue(s.texts()[-1].endswith('880  WEAK'))
        s.msg(T0 + 4000, 13, a=FRAN, action=UNKNOWN_ELEMENT)
        s.set(HYENA_C, hp=1500).sample(T0 + 5000)
        self.assertEqual(s.lines()[-1].tags, 0, 'unknown element: no tag (the live reader has no element source)')
        s.msg(T0 + 8000, 13, a=FRAN, action=ICE_ACT)
        s.set(HYENA_C, hp=1200).sample(T0 + 9000)
        self.assertEqual(s.lines()[-1].tags, 0, 'ice on a fire-weak foe')

    def test_crit_and_miss_hints_are_a_seam(self):
        s = Scene(self.d).unit(BALTHIER, 2000, target=HYENA_C, action=ATTACK).unit(HYENA_C, 2782).sample(T0)
        s.set(HYENA_C, hp=2000).sample(T0 + 100)
        self.d.ILCT_BlModelHint(HYENA_C, TAG_CRIT, T0 + 200)
        self.assertEqual(s.lines()[-1].tags, TAG_CRIT)
        self.assertTrue(s.texts()[-1].endswith('782  CRIT'))
        # A hint before the damage line joins it when it appears within HINT_MS; later, it does not.
        self.d.ILCT_BlModelHint(HYENA_C, TAG_MISS, T0 + 10_000)
        s.set(HYENA_C, hp=1900).sample(T0 + 10_000 + HINT_MS)
        self.assertEqual(s.lines()[-1].tags & TAG_MISS, TAG_MISS)
        self.d.ILCT_BlModelHint(HYENA_C, TAG_MISS, T0 + 20_000)
        s.set(HYENA_C, hp=1800).sample(T0 + 20_000 + HINT_MS + 1)
        self.assertEqual(s.lines()[-1].tags & TAG_MISS, 0)

    def test_the_merge_window(self):
        s = Scene(self.d).unit(BALTHIER, 2000, target=HYENA_C, action=ATTACK).unit(HYENA_C, 2782).sample(T0)
        s.set(HYENA_C, hp=2700).sample(T0 + 100)
        s.set(HYENA_C, hp=2600).sample(T0 + 100 + MERGE_MS)
        self.assertEqual(len(s.lines()), 1, 'within MERGE_MS of the last change: merged')
        s.set(HYENA_C, hp=2500).sample(T0 + 100 + 2 * MERGE_MS + 1)
        self.assertEqual(s.texts(), ['Balthier attacks Hyena C x2  82, 100', 'Balthier attacks Hyena C  100'])

    def test_amounts_listed_then_counted(self):
        s = Scene(self.d).unit(BALTHIER, 2000, target=HYENA_C, action=ATTACK).unit(HYENA_C, 2782).sample(T0)
        for k in range(1, 7):
            s.set(HYENA_C, hp=2782 - 10 * k).sample(T0 + 100 * k)
        self.assertEqual(s.texts(), ['Balthier attacks Hyena C x6  10, 10, 10, 10, ...'])

    def test_history_is_bounded(self):
        s = Scene(self.d)
        for k in range(40):
            s.msg(T0 + k * 10, 74, a=HYENA_B)           # calls for help
        lines = s.lines()
        self.assertEqual(len(lines), HISTORY)
        ids = [line.id for line in lines]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids[-1] - ids[0], HISTORY - 1, 'the newest HISTORY lines')

    def test_system_and_steal_lines(self):
        s = Scene(self.d)
        s.msg(T0, 97)
        s.msg(T0 + 1, 45, a=BALTHIER, b=HYENA_C, item_name=b'Hi-Potion')
        s.msg(T0 + 2, 6, a=FRAN, action=FIRA)
        s.msg(T0 + 3, 62, b=HYENA_C)
        self.assertEqual(s.texts(), ['Back attack!', 'Balthier stole Hi-Potion from Hyena C',
                                     'Fran Fira (interrupted)', 'Hyena C Paling: physical immune'])
        self.assertTrue(s.lines()[0].flags & LF_IMPORTANT)


# ---- presentation -------------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class PresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def alpha(self, line, now) -> int:
        return self.d.ILCT_BlTimeAlpha(ctypes.byref(line), now)

    def select(self, v, now):
        idx, al = (u8 * 20)(), (u8 * 20)()
        n = self.d.ILCT_BlSelect(ctypes.byref(v), now, idx, al, 20)
        return list(idx[:n]), list(al[:n])

    def compose(self, v, now=T0, prefill=0, prefill_text=0) -> tuple[Frame, int]:
        f = Frame()
        added = self.d.ILCT_BlCompose(ctypes.byref(v), now, ctypes.byref(f), prefill, prefill_text)
        return f, added

    def test_hold_linger_and_fade(self):
        plain = make_line(1, touched=T0)
        important = make_line(2, touched=T0, flags=LF_IMPORTANT)
        self.assertEqual(self.alpha(plain, T0 + LIFE_MS), 0x80)
        self.assertEqual(self.alpha(plain, T0 + LIFE_MS + FADE_MS // 2), 0x40)
        self.assertEqual(self.alpha(plain, T0 + LIFE_MS + FADE_MS), 0)
        self.assertEqual(self.alpha(important, T0 + LIFE_MS + FADE_MS), 0x80, 'important lines linger')
        self.assertEqual(self.alpha(important, T0 + LINGER_MS + FADE_MS // 4), 0x60)
        self.assertEqual(self.alpha(important, T0 + LINGER_MS + FADE_MS), 0)

    def test_compact_shows_the_five_newest_oldest_first_the_oldest_dimmed(self):
        v = make_view([make_line(k + 1, touched=T0 + 100 * k) for k in range(7)])
        idx, al = self.select(v, T0 + 700)
        self.assertEqual(idx, [2, 3, 4, 5, 6], 'newest at the bottom')
        self.assertEqual(al, [58, 92, 0x80, 0x80, 0x80])

    def test_an_important_line_is_kept_over_an_ordinary_one(self):
        lines = [make_line(1, touched=T0, flags=LF_IMPORTANT)] + [make_line(k + 2, touched=T0 + 100 + k)
                                                                   for k in range(6)]
        idx, al = self.select(make_view(lines), T0 + 1000)
        self.assertEqual(idx, [0, 3, 4, 5, 6], 'the readies line stays; the oldest ordinary ones go')
        self.assertEqual(al[0], 0x80, 'no position dimming for an important line')
        # Once it has faded, the newest five again.
        idx, _ = self.select(make_view(lines), T0 + LINGER_MS + FADE_MS)
        self.assertEqual(idx, [], 'everything faded')

    def test_faded_lines_leave_and_the_header_holds(self):
        v = make_view([make_line(1, touched=T0), make_line(2, touched=T0 + 3000)])
        self.assertEqual(self.select(v, T0 + LIFE_MS + FADE_MS)[0], [1])
        self.assertEqual(self.d.ILCT_BlHeaderShown(ctypes.byref(v), T0 + 3000 + HEADER_HOLD_MS), 1)
        self.assertEqual(self.d.ILCT_BlHeaderShown(ctypes.byref(v), T0 + 3001 + HEADER_HOLD_MS), 0)
        self.assertEqual(self.d.ILCT_BlHeaderShown(ctypes.byref(make_view([], flags=VIEW_EXPANDED)), T0), 1)

    def test_filters(self):
        party = make_line(1, categories=CAT_PARTY, seats=0b01)
        foe = make_line(2, categories=CAT_FOE, seats=0b10)
        status = make_line(3, categories=CAT_STATUS | CAT_PARTY, seats=0b100)
        reward = make_line(4, categories=CAT_REWARD, seats=0)
        table = {FILTER_ALL: [1, 1, 1, 1], FILTER_PARTY: [1, 0, 1, 1], FILTER_FOES: [0, 1, 0, 0],
                 FILTER_STATUSES: [0, 0, 1, 0]}
        for f, want in table.items():
            got = [self.d.ILCT_BlPasses(ctypes.byref(line), f, 1) for line in (party, foe, status, reward)]
            self.assertEqual(got, want, f)
        for seat, want in ((1, [1, 0, 0, 0]), (2, [0, 1, 0, 0]), (3, [0, 0, 1, 0]), (4, [0, 0, 0, 0])):
            got = [self.d.ILCT_BlPasses(ctypes.byref(line), FILTER_MINE, seat) for line in (party, foe, status, reward)]
            self.assertEqual(got, want, f'mine P{seat}')

    def test_expanded_shows_twenty_without_fade(self):
        lines = [make_line(k + 1, touched=T0 + k) for k in range(25)]
        idx, al = self.select(make_view(lines, flags=VIEW_EXPANDED), T0 + 60_000)
        self.assertEqual(idx, list(range(5, 25)))
        self.assertEqual(set(al), {0x80})
        idx, _ = self.select(make_view(lines, flags=VIEW_EXPANDED, filter_=FILTER_FOES), T0)
        self.assertEqual(idx, [], 'the filter applies')

    def test_compact_drawing(self):
        lines = [make_line(1, touched=T0, actor=b'Hyena B', body=b'Bite', amounts=(-357,), dot=DOT_FOE,
                           flags=LF_PARTY_HIT | LF_SINGLE),
                 make_line(2, touched=T0, cls=LK_STATUS, actor=b'Ashe', icons=(LURE,), dot=3)]
        lines[0].actor_tone, lines[0].target_tone, lines[0].target_count = 6, 2, 1
        lines[0].target_name, lines[0].target, lines[0].actor = b'Fran', FRAN, HYENA_B
        lines[1].tags, lines[1].actor_tone = TAG_HOLDING, 3
        v = make_view(lines)
        f, added = self.compose(v)
        self.assertEqual(added, f.count)
        texts = tti.frame_texts(f)
        header = texts[0]
        self.assertEqual((header[1], header[2]), (HEADER_Y, 'BATTLE LOG'))
        self.assertEqual(header[0] + 14 * 10 * 75 // 100, LOG_X + 420, 'right-aligned on the header box')
        bars = tti.frame_kind(f, th.CMD_BAR)
        self.assertEqual((bars[0].x, bars[0].y, bars[0].w), (LOG_X, HEADER_Y + 20, 420), "the rule, on TI's line")
        dots = [c for c in tti.frame_kind(f, th.CMD_SPRITE) if c.atlas == ATLAS_TARGETLINE_P]
        self.assertEqual(len(dots), 2)
        for k, dot in enumerate(dots):
            self.assertEqual((dot.page, dot.sheet, dot.cell, dot.reserved), (0, 4, 1, 7), 'glow dot, white palette')
        y0, y1 = HEADER_Y + LINES_DY, HEADER_Y + LINES_DY + LINE_PITCH
        self.assertEqual(sorted(d.y + 3 for d in dots), [y0, y1])
        copper = th.rgba(0x74, 0x40, 0x1D)
        self.assertIn(copper, [d.colour for d in dots], 'a foe: copper')
        self.assertIn(th.rgba(0x1E, 0x69, 0x37), [d.colour for d in dots], 'seat 3: its colour')
        line0 = [t for t in texts if t[1] == y0]
        self.assertEqual([t[2] for t in line0], ['Hyena B', ': Bite on ', 'Fran', '-357'])
        self.assertEqual(line0[0][3], th.rgba(0x78, 0x70, 0x68), 'a foe in the foe colour')
        self.assertEqual(line0[-1][3], th.rgba(0x78, 0x2D, 0x2D), 'damage taken in red')
        self.assertEqual(line0[2][3], th.rgba(0x23, 0x41, 0x78), "the victim in seat 2's colour")
        self.assertEqual(line0[0][0], LOG_X + 24)
        icons = [c for c in tti.frame_kind(f, th.CMD_SPRITE) if c.atlas == ATLAS_PARTYTOP_4_C]
        self.assertEqual([(c.page, c.sheet, c.cell, c.y) for c in icons], [(3, 0, LURE, y1 + 1)])
        line1 = [t for t in texts if t[1] == y1]
        self.assertEqual([t[2] for t in line1], ['Ashe', '  HOLDING'])
        self.assertLess(icons[0].x, line1[1][0], 'the status icon before the tag')
        self.assertGreater(icons[0].x, line1[0][0])

    def test_expanded_drawing_is_two_commands_a_line_inside_the_log_zone(self):
        lines = [make_line(k + 1, touched=T0, actor=b'Balthier', body=b'Attack', amounts=(-412, -726),
                           icons=(PROTECT,)) for k in range(25)]
        v = make_view(lines, flags=VIEW_EXPANDED)
        f, added = self.compose(v)
        self.assertEqual(added, 2 + 2 * EXPANDED_LINES)
        self.assertLessEqual(added, MAX_COMMANDS_LOG)
        x, y, w, h = LOG_ZONE
        for k in range(f.count):
            c = f.cmds[k]
            self.assertGreaterEqual(c.x, x)
            self.assertLessEqual(c.y + (c.h or 26), y + h, 'every line inside the zone the menus keep clear of')
        texts = tti.frame_texts(f)
        self.assertEqual(texts[-1][2], 'Balthier: Attack  412, 726 Protect')
        for _, _, s_, _ in texts[1:]:
            self.assertLessEqual(LOG_X + 24 + len(s_) * 11, 1904 + 11)

    def test_the_header_names_a_filter(self):
        f, _ = self.compose(make_view([make_line(1, touched=T0, seats=0b10)], filter_=FILTER_MINE, seat=2))
        self.assertEqual(tti.frame_texts(f)[0][2], 'BATTLE LOG - MINE P2')
        f, _ = self.compose(make_view([], flags=VIEW_EXPANDED, filter_=FILTER_PARTY))
        self.assertEqual([t[2] for t in tti.frame_texts(f)], ['BATTLE LOG - PARTY'])

    def test_nothing_to_show_draws_nothing(self):
        f, added = self.compose(make_view([make_line(1, touched=T0)]), now=T0 + HEADER_HOLD_MS + 1)
        self.assertEqual((added, f.count), (0, 0))

    def test_the_budget_drops_the_oldest_lines_first(self):
        lines = [make_line(k + 1, touched=T0 + k, actor=f'Actor{k}'.encode(), body=b'Fira', amounts=(-100,),
                           icons=(POISON,)) for k in range(5)]
        for k, line in enumerate(lines):
            line.target_count, line.target_name, line.target, line.actor = 1, b'Hyena', HYENA_A, k + 1
        v = make_view(lines)
        f, added = self.compose(v, prefill=150)
        self.assertLessEqual(added, LOG_FLOOR, 'short of room: the floor')
        texts = [t[2] for t in tti.frame_texts(f)]
        self.assertIn('Actor4', texts, 'the newest line is drawn')
        self.assertNotIn('Actor0', texts, 'the oldest goes first')
        self.assertGreater(stats(self.d).capped, 0)
        f, added = self.compose(v, prefill=0)
        self.assertLessEqual(added, MAX_COMMANDS_LOG)
        self.assertGreaterEqual(th.MAX_COMMANDS - f.count, MENU_RESERVE, 'room is left for the menus')
        # A short text pool: still the newest line, in one run.
        f, added = self.compose(v, prefill=0, prefill_text=th.TEXT_POOL - 700 - 40)
        self.assertGreater(added, 0)

    def test_line_text_forms(self):
        heal = make_line(1, cls=LK_HEAL, actor=b'Penelo', body=b'Cure', amounts=(348,))
        heal.target_count, heal.target_name, heal.target, heal.actor = 1, b'Fran', FRAN, PENELO
        self.assertEqual(text(self.d, heal), 'Penelo: Cure on Fran  +348')
        kill = make_line(2, cls=LK_KILL, actor=b'Hyena A', body=b'defeated')
        kill.extra = b'96 gil'
        self.assertEqual(text(self.d, kill), 'Hyena A defeated  96 gil')
        ko = make_line(3, cls=LK_KO, actor=b'Fran', body=b'turns to stone')
        self.assertEqual(text(self.d, ko), 'Fran turns to stone')
        tags = make_line(4, actor=b'Vaan', body=b'Attack', amounts=(-9,))
        tags.tags = TAG_CRIT | TAG_WEAK | TAG_MISS | TAG_HOLDING
        self.assertEqual(text(self.d, tags), 'Vaan: Attack  9  CRIT  WEAK  MISS  HOLDING')
        for ch in ''.join(text(self.d, x) for x in (heal, kill, ko, tags)):
            self.assertIn(ch, th.CHARS, 'every character is in the game encoding')


# ---- settings ----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()

    def current(self) -> Settings:
        s = Settings()
        self.d.ILCT_BlCurrent(ctypes.byref(s))
        return s

    def test_defaults_and_clamps(self):
        s = self.current()
        self.assertEqual((s.enabled, s.expanded, s.expand_control, s.filter, s.filter_seat), (1, 0, 0, FILTER_ALL, 1))
        self.d.ILCT_BlConfigure(7, 9, 99, 42, 9)
        s = self.current()
        self.assertEqual((s.enabled, s.expanded, s.expand_control, s.filter, s.filter_seat), (1, 1, 0, FILTER_ALL, 1))
        self.d.ILCT_BlConfigure(1, 0, 5, FILTER_MINE, 3)
        s = self.current()
        self.assertEqual((s.expand_control, s.filter, s.filter_seat), (5, FILTER_MINE, 3))

    def test_toggle(self):
        self.d.ILCT_BlToggle()
        self.assertEqual(self.current().expanded, 1)
        self.d.ILCT_BlToggle()
        self.assertEqual(self.current().expanded, 0)

    def test_a_disabled_log_publishes_nothing_and_drains_the_ring(self):
        snap = tdrv.snapshot(epoch=EPOCH, sampled=T0)
        v = View()
        self.assertEqual(self.d.ILCT_BlStep(ctypes.byref(snap), T0, ctypes.byref(v)), 1)
        self.assertEqual((v.epoch, v.line_count, v.flags & VIEW_EXPANDED), (EPOCH, 0, 0))
        self.assertEqual(self.d.ILCT_BlReplaces(EPOCH, T0 + LIVE_MS), 1)
        self.assertEqual(self.d.ILCT_BlReplaces(EPOCH, T0 + LIVE_MS + 1), 0)
        self.assertEqual(self.d.ILCT_BlReplaces(EPOCH + 1, T0), 0)
        self.d.ILCT_BlConfigure(0, 0, 0, 0, 1)
        blk = ctypes.create_string_buffer(EVENT_ARGS)
        self.d.ILCT_BlRingPush(16, 1, ctypes.addressof(blk), T0)
        self.assertEqual(self.d.ILCT_BlStep(ctypes.byref(snap), T0 + 20, ctypes.byref(v)), 0)
        self.assertEqual(self.d.ILCT_BlLatest(ctypes.byref(v)), 0, 'withdrawn')
        self.assertEqual(self.d.ILCT_BlReplaces(EPOCH, T0 + 20), 0, 'the native log draws again')
        r = RingView()
        self.d.ILCT_BlRingStats(ctypes.byref(r))
        self.assertEqual(r.read, r.written)

    def test_no_world_withdraws(self):
        snap = tdrv.snapshot(epoch=EPOCH, sampled=T0)
        v = View()
        self.d.ILCT_BlStep(ctypes.byref(snap), T0, ctypes.byref(v))
        self.assertEqual(self.d.ILCT_BlStep(None, T0 + 20, ctypes.byref(v)), 0)
        self.assertEqual(self.d.ILCT_BlLatest(ctypes.byref(v)), 0)
        snap.flags = 0
        self.assertEqual(self.d.ILCT_BlStep(ctypes.byref(snap), T0 + 40, ctypes.byref(v)), 0)


# ---- the pipeline: snapshot rows + ring -> view -> pass ----------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()

    def setUp(self):
        self.d.ILCT_BlReset()
        self.d.ILCT_BlSetNamesOnly(1)
        self.d.ILCT_BlSetImageBase(0x10000)   # no game here: every game read fails closed

    def snap(self, hp: int, now: int) -> tdrv.Snapshot:
        s = tdrv.snapshot(epoch=EPOCH, sampled=now, flags=0x3)
        s.member_count = 1
        u = s.members[0]
        u.handle, u.member, u.flags, u.seat, u.hp, u.hp_max = FRAN, 2, 0x1 | 0x2 | 0x10, 2, hp, 1325
        return s

    def test_feed_and_diffs_reach_the_view_and_the_pass(self):
        d = self.d
        v = View()
        self.assertEqual(d.ILCT_BlStep(ctypes.byref(self.snap(1325, T0)), T0, ctypes.byref(v)), 1)
        rec = ctypes.create_string_buffer(0x10)      # Fran's battle record: side 0, party id 2
        rec[4], rec[5] = b'\x02', b'\x00'
        block = ctypes.create_string_buffer(EVENT_ARGS)
        struct.pack_into('<Q', block, 0x08, ctypes.addressof(rec))
        struct.pack_into('<i', block, 0x28, 0)
        d.ILCT_BlRingPush(16, 0, ctypes.addressof(block), T0 + 10)        # Fran has fallen
        struct.pack_into('<i', block, 0x28, ACTION_BOUND)
        d.ILCT_BlRingPush(15, 0, ctypes.addressof(block), T0 + 11)        # dropped: the emitter's own bound
        d.ILCT_BlRingPush(200, 0, ctypes.addressof(block), T0 + 12)       # not a template: dropped
        self.assertEqual(d.ILCT_BlStep(ctypes.byref(self.snap(1000, T0 + 20)), T0 + 20, ctypes.byref(v)), 1)
        c = stats(d)
        self.assertEqual((c.drained, c.filtered), (3, 2))
        names = [(v.lines[k].cls, v.lines[k].actor) for k in range(v.line_count)]
        self.assertIn((LK_KO, FRAN), names, "the record resolved to Fran's handle through the snapshot row")
        self.assertIn((LK_DAMAGE, FRAN), names, 'the diff: an unattributed hit on Fran')
        f = Frame()
        added = d.ILCT_BlDraw(ctypes.byref(self.snap(1000, T0 + 20)), T0 + 20, ctypes.byref(f))
        self.assertGreater(added, 0, 'the pass draws the published view')
        other = self.snap(1000, T0 + 20)
        other.epoch = EPOCH + 1
        self.assertEqual(d.ILCT_BlDraw(ctypes.byref(other), T0 + 20, ctypes.byref(f)), 0, 'another epoch: nothing')
        self.assertEqual(d.ILCT_BlDraw(ctypes.byref(self.snap(1000, T0 + 400)), T0 + 400, ctypes.byref(f)), 0,
                         'a stale view: nothing')

    def test_an_unreadable_record_resolves_to_nothing(self):
        w = Who()
        self.assertEqual(self.d.ILCT_BlResolve(8, ctypes.byref(w)), 0)
        self.assertEqual(self.d.ILCT_BlResolve(0, ctypes.byref(w)), 0)
        foe = ctypes.create_string_buffer(0x10)
        foe[5] = b'\x01'
        self.assertEqual(self.d.ILCT_BlResolve(ctypes.addressof(foe), ctypes.byref(w)), 1)
        self.assertEqual((w.side, w.handle, w.name), (SIDE_FOE, 0, b'?'), 'a unit we cannot follow: named "?"')


# ---- the native log: replaced while the log is live -----------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class NativeLogTest(unittest.TestCase):
    """h0 on host memory (the frame driver's harness): with a fresh battle-log view the layer is drawn at the hook and
    the native log draw is skipped (RESUME at the call's return); otherwise the hook calls around as before. Every
    case composes an empty frame: nothing of the game's runs here."""

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = tdrv.declare(cls.hw.d)
        tfe.declare(cls.d)
        cls.bl = shared_dll()
        cls.mgr = ctypes.create_string_buffer(0x4100)
        cls.ctx = (u8 * 0xA0)()
        cls.arena = (u64 * 3)()

    def setUp(self):
        d = self.d
        self.hw.setup(reset_hud=True)
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_CtlReset()
        d.ILCT_FePresenterReset()
        self.bl.ILCT_BlReset()
        a = ctypes.addressof
        struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx))
        struct.pack_into('<h', self.ctx, 0x58, 0x80)
        struct.pack_into('<h', self.ctx, 0x5A, 0x80)
        self.arena[0], self.arena[1], self.arena[2] = 0x10_0000, 0x10_0000, 0x10_0000 + th.RING
        self.hw.twin.poke(th.RENDER_ARENA, struct.pack('<Q', a(self.arena)))
        self.hw.twin.poke(th.UI_READY, struct.pack('<I', 1))
        self.hw.twin.poke(tdrv.RENDER_FLAGS, struct.pack('<I', tdrv.RENDER_UI))
        d.ILCT_HudSnapPublish(ctypes.byref(tdrv.snapshot()))
        tdrv.publish_menu_view(d)   # the co-op thread's (empty) menu view: a presented frame stamps

    def tearDown(self):
        self.bl.ILCT_BlReset()
        tdrv.withdraw_menu_view(self.d)
        self.d.ILCT_HudDrvReset()
        self.d.ILCT_CtlReset()
        self.d.ILCT_FePresenterReset()
        self.hw.twin.reset()

    def call(self, epoch=EPOCH) -> tuple[int, th.HookCtx]:
        c = th.HookCtx()
        c.rcx, c.rdx = ctypes.addressof(self.mgr), ctypes.addressof(self.ctx)
        c.index = self.hw.row
        with quiet_faulthandler():
            r = self.d.ILCT_HudCall(ctypes.byref(c), epoch)
            if r == 2:
                self.d.ILCT_HudAfter(ctypes.byref(c), epoch)
        return r, c

    def publish_log(self, built=NOW, epoch=EPOCH):
        self.bl.ILCT_BlPublish(ctypes.byref(make_view([], epoch=epoch, built=built)))

    def test_a_live_log_skips_the_native_log_draw(self):
        self.publish_log()
        r, c = self.call()
        self.assertEqual(r, 1, 'RESUME: the native log draw is skipped')
        self.assertEqual(c.resume, TWIN_BASE + LOG_DRAW_RETURN - 0x120000, 'resumes at the call\'s return')
        self.assertEqual(stats(self.bl).native_hidden, 1)
        self.assertEqual(self.d.ILCT_FePresenterDrawn(), NOW, 'the layer was presented in its place')

    def test_otherwise_the_native_log_draws(self):
        for case, prep in (('no view', lambda: None),
                           ('element off', lambda: (self.publish_log(), self.bl.ILCT_BlConfigure(0, 0, 0, 0, 1))),
                           ('stale view', lambda: self.publish_log(built=NOW - LIVE_MS - 1)),
                           ('other epoch', lambda: self.publish_log(epoch=EPOCH + 1))):
            with self.subTest(case):
                self.setUp()
                prep()
                r, c = self.call()
                self.assertEqual((r, c.resume), (2, 0), 'called around: the native log, then the layer')
                self.tearDown()

    def test_no_layer_means_stock(self):
        self.publish_log()
        self.d.ILCT_HudSnapWithdraw()
        r, _ = self.call()
        self.assertEqual(r, 0, 'the layer is not drawn: the hook is stock, the native log draws')


if __name__ == '__main__':
    unittest.main()
