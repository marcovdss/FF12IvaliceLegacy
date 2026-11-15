"""Host tests for the guest battle-menu model (native/coop/src/menu). No game run, nothing deployed.

* tables: section files parsed the way the External File Loader would hand them to the game; the live reader on
  synthetic game memory through the real table globals, rows formed in 32 bits with a zero resource base (a non-zero
  base, a wrapping table or unreadable rows refused; an optional table's error reported, not fatal), and a catalog
  built from those live tables equal to the one built from the files;
* catalogs per licence state on a fixture shaped like Legacy's tables (Legacy's Attack with its area radius, its
  Technick list): magicks owned and licensed (or needing no licence), technicks by acquired nodes, Quickenings by
  Mist bits or Quickening licences, Espers, Items in the player's saved order with their available count (held-back
  stock greyed), the fixed-list story guests (7..0x19, 0x1B..0x27) against party id 6, which uses licences;
* the restriction table: KO/Stone/Stop/Sleep/Confuse/Disable/X-Zone, Berserk (Attack only), Silence, the area's
  command seals, Dismiss, and MP greying with the game's effective cost (Offering, Parsimony, Spellspring,
  Channeling, the Quickening/Esper exemption, kinds paid with charges), restated exhaustively;
* the model: open, navigation, pages, target mode, submit and toggle events, and selection kept across catalog
  rebuilds (reorder, a page that disappears, a pending action refreshed, removed or disabled, an invalid tree);
* Gambits on/off: the identity proof table, requests, results and sounds;
* with ILC_BATTLE_PACK (the Steam battle_pack.bin) and ILC_BATTLE_PACK_DIR (a folder of loose sections, e.g.
  Legacy's deployed battle_pack.bin.dir), the same catalog on the real tables, every regular character and several
  licence states, checked against a restatement of the listing rules.
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import struct
import unittest
from pathlib import Path

from coop_native_support import native_test
from coop_session_support import Image
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import untagged_constants

u8, u16, i16, u32, i32, u64 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_int16, ctypes.c_uint32, ctypes.c_int32,
                               ctypes.c_uint64)
P = ctypes.POINTER
ROOT = Path(__file__).resolve().parents[1]
MENU_SRC = ROOT / 'native' / 'coop' / 'src' / 'menu'
BATTLE_PACK = os.environ.get('ILC_BATTLE_PACK')
BATTLE_PACK_DIR = os.environ.get('ILC_BATTLE_PACK_DIR')


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


ActionRule = S('ActionRule', [('present', u8), ('cost', u8), ('area', u8), ('targeting', u8), ('kind', u8),
                              ('reserved', u8), ('rules', u16), ('target_flags', u16), ('reserved2', u16)])
ContentRow = S('ContentRow', [('action', u16), ('display', u16), ('sort', u8), ('reserved', u8 * 3)])
LicenceNode = S('LicenceNode', [('type', u8), ('count', u8), ('action', u16 * 8)])
Tables = S('Tables', [('status', u32), ('action_count', u32), ('licence_count', u32), ('item_count', u32),
                      ('magick_count', u32), ('technick_count', u32), ('fixed_known', u32),
                      ('channeling_known', u32), ('channeling', u16 * 3), ('ready', u16), ('optional_status', i32),
                      ('reserved', u32), ('signature', u64),
                      ('actions', ActionRule * 1024), ('licences', LicenceNode * 368), ('items', ContentRow * 64),
                      ('magicks', ContentRow * 96), ('technicks', ContentRow * 32), ('fixed', (u16 * 10) * 40)])
Shared = S('Shared', [('menu_flags', u32), ('magick_owned', u8 * 12), ('technick_owned', u8 * 4),
                      ('mist_owned', u8 * 4), ('seals', u8), ('order_known', u8), ('order_count', u8),
                      ('reserved', u8), ('item_counts', u16 * 64), ('item_order', u16 * 64), ('gil', i32)])
Seat = S('Seat', [('member', u16), ('record_state', u8), ('side', u8), ('mp', i16), ('charges', u8),
                  ('esper_owner', u8), ('status', u32), ('licences', u8 * 46), ('magick_licences', u8 * 23),
                  ('augments', u8 * 12), ('magick', u8), ('reserved', u8 * 2), ('gil_reserved', i32)])
Entry = S('Entry', [('kind', u8), ('flags', u8), ('page', u8), ('groups', u8), ('action', u16), ('display', u16),
                    ('quantity', u16), ('cost', i16), ('default_group', u8), ('area', u8), ('role', u8),
                    ('cost_flags', u8)])
PageSlot = S('PageSlot', [('first', u16), ('count', u16), ('present', u8), ('reserved', u8 * 3)])
Catalog = S('Catalog', [('valid', u32), ('member', u16), ('used', u16), ('signature', u64), ('pages', PageSlot * 12),
                        ('entries', Entry * 256)])
Event = S('Event', [('kind', u8), ('page', u8), ('index', u16), ('action', u16), ('group', u8), ('reserved', u8)])
Commit = S('Commit', [('changed', u8), ('depth', u8), ('target_kept', u8), ('target_cancelled', u8)])
Frame = S('Frame', [('page', u8), ('reserved', u8), ('index', u16)])
Model = S('Model', [('mode', u8), ('depth', u8), ('target_group', u8), ('reserved', u8), ('target_index', u16),
                    ('target_count', u16), ('stack', Frame * 4), ('pending', Entry), ('pages', Catalog)])
Identity = S('Identity', [('member', u32), ('handle', u32), ('actor', u64), ('physics', u64), ('record', u64),
                          ('party_block', u64)])
Gambits = S('Gambits', [('known', u8), ('enabled', u8), ('in_flight', u8), ('desired', u8), ('last_result', i32),
                        ('sound', u32), ('requests', u32), ('applied', u32), ('refused', u32)])
LAYOUTS = [ActionRule, ContentRow, LicenceNode, Tables, Shared, Seat, Entry, PageSlot, Catalog, Event, Commit, Model,
           Identity, Gambits]

T_ACTIONS, T_LICENCES, T_MEMBERS, T_ITEMS, T_MAGICKS, T_TECHNICKS, T_AUGMENTS = range(7)
SECTION_OF = {T_LICENCES: 12, T_ACTIONS: 14, T_MEMBERS: 16, T_ITEMS: 18, T_MAGICKS: 29, T_TECHNICKS: 30,
              T_AUGMENTS: 58}
TABLES_OK, TABLES_MISSING, TABLES_UNREADABLE, TABLES_BAD_LAYOUT, TABLES_MOVED, TABLES_BAD_BASE = 0, -1, -2, -3, -4, -5
BUILD_OK, NO_TABLES, NO_MEMBER, NO_FIXED = 0, -1, -2, -3
(PG_ROOT, PG_MAGICKS, PG_WHITE, PG_BLACK, PG_TIME, PG_GREEN, PG_ARCANE, PG_TECHNICKS, PG_MIST, PG_QUICKENINGS,
 PG_SUMMONS, PG_ITEMS) = range(12)
PG_NONE = 0xFF
EK_LABEL, EK_PAGE, EK_ACTION, EK_TOGGLE = range(4)
EF_ENABLED, EF_SELF_ONLY, EF_REVIVE, EF_PARTY_FIRST, EF_MP_SHORT, EF_SEALED, EF_COST_UNKNOWN, EF_HELD_BACK = (
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80)
TG_FOES, TG_NEUTRALS, TG_PARTY, TG_RESERVE, TG_SELF = range(5)
(ROLE_ACTION, ROLE_ATTACK, ROLE_MAGICKS, ROLE_MIST, ROLE_DISMISS, ROLE_GAMBITS, ROLE_ITEMS, ROLE_SCHOOL,
 ROLE_TECHNICKS, ROLE_QUICKENING, ROLE_SUMMON, ROLE_EMPTY) = range(12)
MODE_CLOSED, MODE_LIST, MODE_TARGET = range(3)
(EV_IGNORED, EV_OPENED, EV_CLOSED, EV_CURSOR, EV_PAGE_OPENED, EV_PAGE_CLOSED, EV_TARGET_OPENED, EV_TARGET_CLOSED,
 EV_TARGET_MOVED, EV_TARGET_GROUP, EV_SUBMIT, EV_TOGGLE_GAMBITS, EV_INVALID_TARGET) = range(13)
OP_OPEN, OP_CLOSE, OP_TOGGLE, OP_NAV, OP_PAGE, OP_CONFIRM, OP_BACK, OP_TARGETS, OP_GROUP = range(9)
GB_PENDING, GB_APPLIED = 0, 1
GB_STALE_IDENTITY, GB_STALE_PHYSICS, GB_INVALID_MEMBER, GB_STALE_RECORD, GB_VERIFY_FAILED, GB_BLOCKED = (
    0x11, 0x12, 0x13, 0x14, 0x15, 0x16)
GB_BUSY, GB_UNKNOWN = 0x20, 0x21
SAMPLE_OK, SAMPLE_NO_STATE, SAMPLE_UNREADABLE, SAMPLE_MOVED, SAMPLE_STALE = 0, -1, -2, -3, -4

# Game facts the tests build (VAs of the pinned build, offsets inside game objects).
RESOURCE_BASE_PTR, ACTION_TABLE_PTR, AUGMENT_TABLE_PTR = 0x1F83530, 0x2EBF138, 0x2EBF040
# The table globals the battle-pack loader registers (licences, party members, items, magicks, technicks).
LICENCE_TABLE_PTR, MEMBER_TABLE_PTR, ITEM_TABLE_PTR, MAGICK_TABLE_PTR, TECHNICK_TABLE_PTR = (
    0x2EBF038, 0x2EBF130, 0x2EBF100, 0x2EBF0F0, 0x2EBF0E8)
KNOWN_GLOBALS = {T_ACTIONS: ACTION_TABLE_PTR, T_LICENCES: LICENCE_TABLE_PTR, T_MEMBERS: MEMBER_TABLE_PTR,
                 T_ITEMS: ITEM_TABLE_PTR, T_MAGICKS: MAGICK_TABLE_PTR, T_TECHNICKS: TECHNICK_TABLE_PTR,
                 T_AUGMENTS: AUGMENT_TABLE_PTR}
LOCATION_PTR, BATTLE_STATE_PTR, COMMAND_SEALS = 0x2092758, 0x2EBF190, 0x22C314C
ITEM_ORDER_OWNER_PTR, ITEM_ORDER_OFFSET = 0x20927C0, 0x2092764
MENU_FLAGS, ITEM_COUNTS, MAGICK_OWNED, TECHNICK_OWNED, MIST_OWNED, ITEM_ORDER_COUNT = (
    0x2204, 0x4748, 0x4F10, 0x4F1C, 0x4F80, 0x47B8)
MEMBER_RECORDS, MEMBER_RECORD_SIZE = 0x08, 0x1C8
PARTY_GIL, REC_MAGICK, ACTOR_GIL_RESERVED = 0x08, 0x2B, 0xE58
SEAL_ATTACK, SEAL_MAGICK, SEAL_TECHNICK, SEAL_ITEM = 0x04, 0x08, 0x10, 0x20
ST_KO, ST_STONE, ST_SILENCE, ST_BERSERK, ST_DISABLE = 1 << 0, 1 << 1, 1 << 9, 1 << 27, 1 << 13
HARD_BITS = (0, 1, 3, 4, 5, 13, 31)
ATTACK, DISMISS = 0x96, 0x113
QUICKENING_BASE = (0xD0, 0xDC, 0xD3, 0xD6, 0xD9, 0xDF)
REVIVALS = (0x05, 0x0F, 0x58, 0xAE)

SIGNATURES = {
    'ILCT_MenuSizes': ([P(u32), u32], u32),
    'ILCT_MenuTablesReset': ([], None),
    'ILCT_MenuTablesSection': ([u32, ctypes.c_char_p, u64], i32),
    'ILCT_MenuTablesFinish': ([], i32),
    'ILCT_MenuTablesCopy': ([P(Tables)], None),
    'ILCT_MenuKnownGlobals': ([P(u64)], None),
    'ILCT_MenuTablesRead': ([u64, P(u64)], i32),
    'ILCT_MenuBuild': ([P(Shared), P(Seat), P(Catalog)], i32),
    'ILCT_MenuCost': ([u32, P(Seat)], i32),
    'ILCT_MenuSignature': ([P(Shared), P(Seat)], u64),
    'ILCT_MenuSeatReset': ([u32], None),
    'ILCT_MenuSeatRefresh': ([u32, P(Shared), P(Seat), P(Commit)], i32),
    'ILCT_MenuSeatCommit': ([u32, P(Catalog), P(Commit)], i32),
    'ILCT_MenuSeatOp': ([u32, u32, i32, u32, P(Event)], i32),
    'ILCT_MenuSeatModel': ([u32, P(Model)], None),
    'ILCT_MenuSeatCounters': ([u32, P(u64)], None),
    'ILCT_MenuGambitCheck': ([P(Identity)], i32),
    'ILCT_MenuGambitRequest': ([u32, P(Identity), u32], i32),
    'ILCT_MenuGambitComplete': ([u32, i32, u32], None),
    'ILCT_MenuGambitObserve': ([u32, u32], None),
    'ILCT_MenuGambitState': ([u32, P(Gambits)], None),
    'ILCT_MenuReadShared': ([u64, P(Shared)], i32),
    'ILCT_MenuReadSeat': ([u64, u64, u32, u32, u32, P(Seat)], i32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


# ---- section files ---------------------------------------------------------------------------------------------
def st2e(rows: list[bytes], stride: int) -> bytes:
    head = bytearray(32)
    head[:4] = b'st2e'
    struct.pack_into('<IHHI', head, 4, len(rows), stride, 0, 32)
    return bytes(head) + b''.join(r.ljust(stride, b'\0')[:stride] for r in rows)


def section_rows(section: bytes) -> list[bytes]:
    count, stride, _, first = struct.unpack_from('<IHHI', section, 4)
    return [section[first + k * stride:first + (k + 1) * stride] for k in range(count)]


def action_row(cost=0, area=0, targeting=0, kind=0, rules=0, target_flags=0) -> bytes:
    row = bytearray(60)
    row[0x06], row[0x0A], row[0x0C], row[0x1E] = area, cost, targeting, kind
    struct.pack_into('<H', row, 0x2C, rules)
    struct.pack_into('<H', row, 0x36, target_flags)
    return bytes(row)


def licence_row(node_type, actions=()) -> bytes:
    row = bytearray(24)
    row[5] = node_type
    values = list(actions) + [0xFFFF] * (8 - len(actions))
    struct.pack_into('<8H', row, 8, *values)
    return bytes(row)


def bits(size: int, *indices: int) -> bytes:
    raw = bytearray(size)
    for i in indices:
        raw[i // 8] |= 1 << (i % 8)
    return bytes(raw)


# ---- a fixture shaped like Legacy's tables -----------------------------------------------------------------------
TECHNICKS_JSON = ROOT / 'patches' / 'technicks.json'


class Fixture:
    """Small tables with the field layout and flag values of Legacy's own rows: its Attack (area radius 4, Legacy's
    value; the Steam table has 0), its Technick list (patches/technicks.json: index -> action), revival and esuna-
    type actions with their target flags, Quickenings and Espers paid with charges, a story guest's fixed actions."""

    # magick index -> (action, school, cost, sort key, needs no licence)
    MAGICKS = {0: (0x00, 0, 8, 2, False), 1: (0x01, 0, 6, 1, False), 2: (0x05, 0, 20, 3, False),
               3: (0x30, 2, 24, 0, False), 4: (0x3A, 3, 8, 0, False), 5: (0x40, 1, 10, 0, False),
               6: (0x48, 4, 12, 0, True), 7: (0x0F, 0, 52, 4, False)}
    # licence node -> (type, actions); summary flags 6/7/8/9 stand for nodes 182/183/184/185
    LICENCES = {182: (9, (0x00, 0x01)), 183: (9, (0x05,)), 184: (10, (0x40,)), 185: (11, (0x30, 0x3A)),
                208: (12, (0x01,)), 211: (12, (0x0F,)), 276: (14, (0x9F,)), 280: (14, (0x9E,))}
    QUICKENING_NODES = range(0, 10)       # type 19
    ESPER_NODES = range(18, 31)           # type 20

    def __init__(self):
        technicks = json.loads(TECHNICKS_JSON.read_text(encoding='utf-8'))['commands']
        self.technick_actions = [t['action'] for t in sorted(technicks, key=lambda t: t['index'])]
        actions = [action_row() for _ in range(543)]
        for index, (action, school, cost, _sort, free) in self.MAGICKS.items():
            actions[action] = action_row(cost=cost, targeting=0x4C, kind=1,
                                         rules=school | (0x40 if action in REVIVALS else 0) | (0x2000 if free else 0),
                                         target_flags=0x30)
        for k, action in enumerate(self.technick_actions):
            actions[action] = action_row(targeting=0x88 if k else 0x24, kind=2, area=6 if k == 2 else 0)
        actions[ATTACK] = action_row(area=4, targeting=0x8C, rules=0x6428)
        for k in range(64):
            actions[0x52 + k] = action_row(targeting=0x4C, kind=3, rules=0x3020, target_flags=0x30)
        actions[0x58] = action_row(targeting=0x2C, kind=3, rules=0x2040, target_flags=0x32)
        for q in range(0xD0, 0xE2):
            actions[q] = action_row(cost=1, area=10, targeting=0x88, kind=6, rules=0x400, target_flags=0x10)
        for e in range(0x106, 0x113):
            actions[e] = action_row(cost=1, targeting=0x41, kind=5, rules=0x100)
        actions[DISMISS] = action_row(targeting=0x10, kind=255, rules=0x100)
        self.actions = actions
        licences = [licence_row(1) for _ in range(361)]
        for n in self.QUICKENING_NODES:
            licences[n] = licence_row(19)
        for n in self.ESPER_NODES:
            licences[n] = licence_row(20, (0x106 + n - 18,))
        for n, (node_type, acts) in self.LICENCES.items():
            licences[n] = licence_row(node_type, acts)
        self.licences = licences
        # items: index -> action 0x52 + index; item 5 shows another action's name; sort keys reverse items 1 and 2
        items = []
        for k in range(64):
            row = bytearray(12)
            row[0] = {1: 2, 2: 1}.get(k, k)
            row[7] = 0x52 + k
            struct.pack_into('<H', row, 8, 0x200 if k == 5 else 0x52 + k)
            items.append(bytes(row))
        self.items = items
        magicks = [bytes(8) for _ in range(81)]
        for index, (action, _school, _cost, sort, _free) in self.MAGICKS.items():
            row = bytearray(8)
            row[2], row[6] = action, sort
            magicks[index] = bytes(row)
        self.magicks = magicks
        technick_rows = []
        for k, action in enumerate(self.technick_actions):
            row = bytearray(8)
            row[2], row[6] = action, 24 - k  # listed in reverse index order
            technick_rows.append(bytes(row))
        self.technicks = technick_rows
        members = [bytes(128) for _ in range(40)]
        guest = bytearray(128)
        struct.pack_into('<10H', guest, 0x58, 0, 1, 0x8001, 0x3000, 0x3006, 0x4000, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
        members[7] = bytes(guest)  # a fixed-list story guest
        members[6] = bytes(guest)  # party id 6 holds a gift list too, but uses licences like the regulars
        self.members = members

    def sections(self) -> dict[int, bytes]:
        return {T_ACTIONS: st2e(self.actions, 60), T_LICENCES: st2e(self.licences, 24),
                T_MEMBERS: st2e(self.members, 128), T_ITEMS: st2e(self.items, 12),
                T_MAGICKS: st2e(self.magicks, 8), T_TECHNICKS: st2e(self.technicks, 8)}


def load_sections(d, sections: dict[int, bytes]) -> int:
    d.ILCT_MenuTablesReset()
    for which, raw in sections.items():
        assert d.ILCT_MenuTablesSection(which, raw, len(raw)) == 1, which
    return d.ILCT_MenuTablesFinish()


def shared(menu_flags=0x1, magicks=(), technicks=(), mist=(), items=None, seals=0, order=None, gil=0) -> Shared:
    s = Shared()
    s.menu_flags = menu_flags
    s.gil = gil
    s.magick_owned[:] = bits(12, *magicks)
    s.technick_owned[:] = bits(4, *technicks)
    s.mist_owned[:] = bits(4, *mist)
    s.seals = seals
    for index, qty in (items or {}).items():
        s.item_counts[index] = qty
    if order is not None:
        s.order_known, s.order_count = 1, len(order)
        for k, index in enumerate(order):
            s.item_order[k] = index
    return s


def seat(member=0, licences=(), summary=(), mp=999, status=0, augments=(), side=0, gambits=True,
         esper_owner=False, charges=3, magick=0, gil_reserved=0) -> Seat:
    s = Seat()
    s.member = member
    s.charges = charges          # Mist charges: enough for any Quickening or Esper unless a test says otherwise
    s.magick = magick
    s.gil_reserved = gil_reserved
    s.record_state = 0x04 if gambits else 0
    s.side = side
    s.mp = mp
    s.status = status
    # The magick licence summary (+0x1AA) is bytes 22..44 of the licence bits (+0x194): summary flag f is
    # licence bit 176 + f. Both views are filled from one array, as the record holds them.
    raw = bytearray(bits(46, *licences))
    for flag in summary:
        raw[22 + flag // 8] |= 1 << (flag % 8)
    s.licences[:] = bytes(raw)
    s.magick_licences[:] = bytes(raw[22:45])
    s.augments[:] = bits(12, *augments)
    s.esper_owner = 1 if esper_owner else 0
    return s


def pages_of(c: Catalog) -> dict[int, list[Entry]]:
    out = {}
    for page in range(12):
        slot = c.pages[page]
        if slot.present:
            out[page] = [c.entries[slot.first + k] for k in range(slot.count)]
    return out


def actions_on(c: Catalog, page: int) -> list[int]:
    return [e.action for e in pages_of(c).get(page, []) if e.kind == EK_ACTION]


def root_roles(c: Catalog) -> list[int]:
    return [e.role for e in pages_of(c)[PG_ROOT]]


def root_entry(c: Catalog, role: int) -> Entry:
    return next(e for e in pages_of(c)[PG_ROOT] if e.role == role)


def enabled(e: Entry) -> bool:
    return bool(e.flags & EF_ENABLED)


class NativeCase(unittest.TestCase):
    d = None

    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def build(self, sh: Shared, st: Seat, expect=BUILD_OK) -> Catalog:
        c = Catalog()
        self.assertEqual(self.d.ILCT_MenuBuild(ctypes.byref(sh), ctypes.byref(st), ctypes.byref(c)), expect)
        return c

    def op(self, seat_index, op, a=0, b=0) -> Event:
        e = Event()
        self.assertEqual(self.d.ILCT_MenuSeatOp(seat_index, op, a, b, ctypes.byref(e)), 0)
        return e

    def model(self, seat_index=1) -> Model:
        m = Model()
        self.d.ILCT_MenuSeatModel(seat_index, ctypes.byref(m))
        return m

    def selected(self, seat_index=1) -> Entry | None:
        m = self.model(seat_index)
        if m.mode != MODE_LIST or m.depth == 0:
            return None
        f = m.stack[m.depth - 1]
        slot = m.pages.pages[f.page]
        return m.pages.entries[slot.first + f.index] if f.index < slot.count else None


class FixtureCase(NativeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixture = Fixture()
        assert load_sections(cls.d, cls.fixture.sections()) == TABLES_OK


# ---- layouts ----------------------------------------------------------------------------------------------------
@native_test
class LayoutTest(NativeCase):
    def test_struct_mirrors_match_the_module(self):
        sizes = (u32 * 32)()
        n = self.d.ILCT_MenuSizes(sizes, 32)
        self.assertEqual(n, len(LAYOUTS))
        for k, layout in enumerate(LAYOUTS):
            self.assertEqual(sizes[k], ctypes.sizeof(layout), layout.__name__)

    def test_every_address_constant_is_tagged(self):
        problems = []
        for path in sorted(MENU_SRC.glob('*.[ch]*')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                problems += [f'{path.name}:{number}: {lit}' for lit in untagged_constants(line)]
        self.assertEqual(problems, [])
        self.assertEqual(untagged_constants('x = 0x80000003;'), ['0x80000003'], 'instrument')

    def test_the_menu_never_writes_or_calls_game_memory(self):
        # The data model only reads: no game function pointers are called, nothing is written through the reader.
        for path in sorted(MENU_SRC.glob('*.cpp')):
            text = path.read_text(encoding='utf-8')
            for forbidden in ('reinterpret_cast<void (*', 'reinterpret_cast<int (*', 'VirtualProtect', 'WriteProcess'):
                self.assertNotIn(forbidden, text, path.name)

    def test_every_table_global_is_the_one_the_pack_loader_registers(self):
        g = (u64 * 7)()
        self.d.ILCT_MenuKnownGlobals(g)
        self.assertEqual({k: g[k] for k in range(7)}, KNOWN_GLOBALS)


# ---- tables ------------------------------------------------------------------------------------------------------
@native_test
class TablesTest(NativeCase):
    def test_fixture_tables_parse_and_sign(self):
        fx = Fixture()
        self.assertEqual(load_sections(self.d, fx.sections()), TABLES_OK)
        t = Tables()
        self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
        self.assertEqual((t.action_count, t.licence_count, t.item_count, t.magick_count, t.technick_count),
                         (543, 361, 64, 81, 24))
        self.assertEqual(t.actions[ATTACK].area, 4)
        self.assertEqual([t.technicks[k].action for k in range(24)], fx.technick_actions)
        self.assertEqual(t.items[5].display, 0x200)
        self.assertEqual(t.licences[182].count, 2)
        self.assertEqual(list(t.licences[182].action)[:2], [0x00, 0x01])
        self.assertEqual(t.licences[0].type, 19)
        self.assertEqual(t.fixed_known, 1)
        self.assertEqual(list(t.fixed[7])[:6], [0, 1, 0x8001, 0x3000, 0x3006, 0x4000])
        self.assertNotEqual(t.signature, 0)
        first = t.signature
        fx.actions[ATTACK] = action_row(area=0, targeting=0x8C, rules=0x6428)  # the Steam value
        self.assertEqual(load_sections(self.d, fx.sections()), TABLES_OK)
        self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
        self.assertNotEqual(t.signature, first, 'one byte of the action table changes the signature')

    def test_a_required_table_missing_refuses_and_optional_ones_do_not(self):
        fx = Fixture()
        sections = fx.sections()
        for required in (T_ACTIONS, T_LICENCES, T_ITEMS, T_MAGICKS, T_TECHNICKS):
            partial = {k: v for k, v in sections.items() if k != required}
            self.assertEqual(load_sections(self.d, partial), TABLES_MISSING, required)
        partial = {k: v for k, v in sections.items() if k != T_MEMBERS}
        self.assertEqual(load_sections(self.d, partial), TABLES_OK)
        t = Tables()
        self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
        self.assertEqual(t.fixed_known, 0)

    def test_foreign_layouts_are_refused(self):
        fx = Fixture()
        self.d.ILCT_MenuTablesReset()
        short_rows = st2e([r[:0x30] for r in fx.actions], 0x30)
        self.assertEqual(self.d.ILCT_MenuTablesSection(T_ACTIONS, short_rows, len(short_rows)), 0)
        too_many = st2e(fx.items + fx.items, 12)
        self.assertEqual(self.d.ILCT_MenuTablesSection(T_ITEMS, too_many, len(too_many)), 0)
        truncated = st2e(fx.licences, 24)[:-100]
        self.assertEqual(self.d.ILCT_MenuTablesSection(T_LICENCES, truncated, len(truncated)), 0)
        not_st2e = b'xxxx' + st2e(fx.magicks, 8)[4:]
        self.assertEqual(self.d.ILCT_MenuTablesSection(T_MAGICKS, not_st2e, len(not_st2e)), 0)

    def test_build_refuses_without_tables_and_for_members_without_a_menu(self):
        self.d.ILCT_MenuTablesReset()
        self.build(shared(), seat(), expect=NO_TABLES)
        fx = Fixture()
        sections = {k: v for k, v in fx.sections().items() if k != T_MEMBERS}
        self.assertEqual(load_sections(self.d, sections), TABLES_OK)
        for guest in (7, 15, 0x19, 0x1B, 0x27):
            self.build(shared(), seat(member=guest), expect=NO_FIXED)
        for other in (0x1A, 0x28, 0xFFFF):
            self.build(shared(), seat(member=other), expect=NO_MEMBER)
        for licensed in (0, 5, 6):
            self.build(shared(), seat(member=licensed))


_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.VirtualAlloc.restype = ctypes.c_void_p
_k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32, u32]
_k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32]
_k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32, ctypes.POINTER(u32)]


class LowHeap:
    """Memory below 2 GiB, where the game keeps the battle pack: its rows are addressed with 32 bits."""

    def __init__(self, size=1 << 20):
        self.base = 0
        for candidate in range(0x10000000, 0x7F000000, 0x01000000):
            got = _k32.VirtualAlloc(candidate, size, 0x3000, 0x04)  # MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE
            if got:
                self.base = got
                break
        if not self.base:
            raise OSError('no free range below 2 GiB')
        self.top, self.end = self.base, self.base + size

    def alloc(self, size: int, page: bool = False) -> int:
        align = 0x1000 if page else 16
        self.top = (self.top + align - 1) & ~(align - 1)
        address, self.top = self.top, self.top + size
        if self.top > self.end:
            raise MemoryError('low heap full')
        return address

    def close(self):
        if self.base:
            _k32.VirtualFree(self.base, 0, 0x8000)
            self.base = 0


def augment_section(values=(5, 10, 15), rows=129) -> bytes:
    augments = [bytes(8) for _ in range(rows)]
    for k, value in enumerate(values):
        row = bytearray(8)
        struct.pack_into('<H', row, 4, value)
        augments[91 + k] = bytes(row)
    return st2e(augments, 8)


def place_tables(img: Image, heap: LowHeap, sections: dict[int, bytes], globals_map=None) -> dict:
    """Each table as the pack loader leaves it: a header (anywhere) whose +0x0C holds the 32-bit address of its rows
    (below 2 GiB), its global holding a qword pointer to the header. The resource base stays 0."""
    globals_map = KNOWN_GLOBALS if globals_map is None else globals_map
    placed = {}
    for which, raw in sections.items():
        count, stride, _, first = struct.unpack_from('<IHHI', raw, 4)
        rows = raw[first:first + count * stride]
        row_at = heap.alloc(len(rows), page=True)
        Image.put(row_at, rows)
        header = img.alloc(16)
        img.put(header, b'st2e' + struct.pack('<IHHI', count, stride, 0, row_at))
        img.put_global(globals_map[which], struct.pack('<Q', header))
        placed[which] = (header, row_at, len(rows))
    img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0))
    return placed


@native_test
class LiveTablesTest(NativeCase):
    """The live reader: through the real globals, row = (u32)(u32 [header+0x0C] + id * u16 [header+0x08]), base 0."""

    def setUp(self):
        self.img = Image()
        self.heap = LowHeap()
        self.fx = Fixture()
        self.sections = self.fx.sections()
        self.sections[T_AUGMENTS] = augment_section()
        self.headers = place_tables(self.img, self.heap, self.sections)

    def tearDown(self):
        self.img.close()
        self.heap.close()

    def read(self, globals_map=None) -> int:
        globals_map = KNOWN_GLOBALS if globals_map is None else globals_map
        g = (u64 * 7)(*[globals_map.get(k, 0) for k in range(7)])
        return self.d.ILCT_MenuTablesRead(self.img.base, g)

    def tables(self) -> Tables:
        t = Tables()
        self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
        return t

    def test_tables_read_from_memory_equal_the_section_files(self):
        self.assertEqual(self.read(), TABLES_OK)
        live = self.tables()
        self.assertEqual(list(live.channeling), [5, 10, 15], 'section 58 has 129 rows: Channeling is read')
        self.assertEqual((live.channeling_known, live.optional_status), (1, TABLES_OK))
        self.assertEqual(load_sections(self.d, self.fx.sections()), TABLES_OK)
        files = self.tables()
        for name in ('action_count', 'licence_count', 'item_count', 'magick_count', 'technick_count',
                     'fixed_known'):
            self.assertEqual(getattr(live, name), getattr(files, name), name)
        for name in ('actions', 'licences', 'items', 'magicks', 'technicks', 'fixed'):
            self.assertEqual(bytes(getattr(live, name)), bytes(getattr(files, name)), name)

    def test_a_catalog_built_from_live_tables_equals_the_one_from_the_files(self):
        states = [(shared(magicks=(0, 1, 2, 3), technicks=range(24), mist=(0, 18), items={0: 3, 5: 1}),
                   seat(member=0, licences=(0, 18, 276, 280), summary=(6, 7, 9))),
                  (shared(menu_flags=0), seat(member=7)),
                  (shared(magicks=(0, 1)), seat(member=6, summary=(6,)))]
        self.assertEqual(self.read(), TABLES_OK)
        live = [self.build(sh, st) for sh, st in states]
        sections = self.fx.sections()
        sections[T_AUGMENTS] = augment_section()
        self.assertEqual(load_sections(self.d, sections), TABLES_OK)
        for (sh, st), got in zip(states, live):
            want = self.build(sh, st)
            self.assertEqual(got.used, want.used, st.member)
            self.assertEqual(bytes(got.entries)[:16 * got.used], bytes(want.entries)[:16 * want.used], st.member)
            self.assertEqual(bytes(got.pages), bytes(want.pages), st.member)
        self.assertEqual(actions_on(live[1], PG_WHITE), [0x00], 'the fixed-list guest from the live member table')

    def test_a_non_zero_resource_base_fails_closed(self):
        self.img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0x1000))
        self.assertEqual(self.read(), TABLES_BAD_BASE)
        self.assertEqual(self.tables().ready, 0)
        self.build(shared(), seat(), expect=NO_TABLES)

    def test_rows_are_addressed_in_32_bits_and_a_wrapping_table_is_refused(self):
        header, row_at, _ = self.headers[T_ITEMS]
        self.img.p32(header + 0x0C, 0xFFFFFF00)  # the rows would cross 4 GiB
        self.assertEqual(self.read(), TABLES_BAD_LAYOUT)
        self.img.p32(header + 0x0C, row_at)
        self.img.put(header + 0x10, struct.pack('<I', 0xDEAD))  # bytes past +0x0C are not part of the address
        self.assertEqual(self.read(), TABLES_OK)

    def test_unreadable_rows_fail_closed(self):
        _, row_at, size = self.headers[T_ITEMS]
        old = u32()
        self.assertTrue(_k32.VirtualProtect(row_at, size, 0x01, ctypes.byref(old)))
        try:
            self.assertEqual(self.read(), TABLES_UNREADABLE)
        finally:
            _k32.VirtualProtect(row_at, size, 0x04, ctypes.byref(old))

    def test_an_absurd_row_count_is_a_bad_layout(self):
        header, _, _ = self.headers[T_ACTIONS]
        self.img.p32(header + 4, 100000)
        self.assertEqual(self.read(), TABLES_BAD_LAYOUT)

    def test_an_optional_table_error_is_reported_and_not_fatal(self):
        header, _, _ = self.headers[T_MEMBERS]
        self.img.p32(header + 4, 39)  # fewer rows than party ids
        self.assertEqual(self.read(), TABLES_OK)
        t = self.tables()
        self.assertEqual((t.ready, t.fixed_known, t.optional_status), (1, 0, TABLES_BAD_LAYOUT))
        self.build(shared(), seat(member=7), expect=NO_FIXED)
        self.build(shared(), seat(member=0))


# ---- catalogs per licence state --------------------------------------------------------------------------------
@native_test
class LicenceStateTest(FixtureCase):
    def test_nothing_owned_or_licensed(self):
        for member in range(6):
            with self.subTest(member=member):
                c = self.build(shared(), seat(member=member))
                self.assertEqual(root_roles(c), [ROLE_ATTACK, ROLE_MAGICKS, ROLE_GAMBITS, ROLE_ITEMS])
                self.assertTrue(enabled(root_entry(c, ROLE_ATTACK)))
                self.assertTrue(enabled(root_entry(c, ROLE_MAGICKS)), 'Magicks opens even with every school locked')
                self.assertFalse(enabled(root_entry(c, ROLE_ITEMS)))
                rows = pages_of(c)[PG_MAGICKS]
                self.assertEqual([e.page for e in rows], [PG_WHITE, PG_BLACK, PG_TIME, PG_GREEN, PG_ARCANE,
                                                         PG_TECHNICKS])
                self.assertFalse(any(enabled(e) for e in rows))
                for page in (PG_WHITE, PG_TECHNICKS, PG_ITEMS):
                    only = pages_of(c)[page]
                    self.assertEqual([(e.kind, e.role, enabled(e)) for e in only], [(EK_LABEL, ROLE_EMPTY, False)])
                self.assertNotIn(PG_MIST, pages_of(c))

    def test_menu_flags_decide_the_magicks_and_mist_rows(self):
        c = self.build(shared(menu_flags=0), seat())
        self.assertEqual(root_roles(c), [ROLE_ATTACK, ROLE_GAMBITS, ROLE_ITEMS])
        for flag in (1 << 7, 1 << 8):
            c = self.build(shared(menu_flags=flag), seat())
            self.assertEqual(root_roles(c), [ROLE_ATTACK, ROLE_MIST, ROLE_GAMBITS, ROLE_ITEMS])
            self.assertFalse(enabled(root_entry(c, ROLE_MIST)), 'Mist with no rows is shown but closed')
            self.assertEqual(pages_of(c)[PG_MIST], [])

    def test_magicks_need_ownership_and_the_live_licence_summary(self):
        cases = [
            ('owned, licensed', (0, 1), (6,), [0x01, 0x00]),         # sort keys: Blindna 1, Cure 2
            ('owned, not licensed', (0, 1), (), []),
            ('licensed, not owned', (), (6,), []),
            ('one of two owned', (1,), (6,), [0x01]),
            ('second licence', (0, 1, 2), (6, 7), [0x01, 0x00, 0x05]),
        ]
        for name, owned, summary, expected in cases:
            with self.subTest(name):
                c = self.build(shared(magicks=owned), seat(summary=summary))
                self.assertEqual(actions_on(c, PG_WHITE), expected)
                school = pages_of(c)[PG_MAGICKS][0]
                self.assertEqual(enabled(school), bool(expected))

    def test_summary_flags_name_their_nodes(self):
        # Flag f stands for node 176 + f (flag 6 = node 182), except flags 32..42, which name the higher spells'
        # nodes in their own order: flag 32 (licence bit 208) is node 211.
        c = self.build(shared(magicks=(0, 1, 7)), seat(licences=(182,)))
        self.assertEqual(actions_on(c, PG_WHITE), [0x01, 0x00])
        c = self.build(shared(magicks=(0, 1, 7)), seat(summary=(32,)))
        self.assertEqual(actions_on(c, PG_WHITE), [0x0F], "node 211's spell, not node 208's")
        c = self.build(shared(magicks=(0, 1, 7)), seat(licences=(211,)))
        self.assertEqual(actions_on(c, PG_WHITE), [0x01], 'bit 211 is flag 35, which names node 208')

    def test_each_school_and_the_licence_free_magick(self):
        c = self.build(shared(magicks=(3, 4, 5, 6)), seat(summary=(8, 9)))
        self.assertEqual(actions_on(c, PG_BLACK), [0x40])
        self.assertEqual(actions_on(c, PG_TIME), [0x30])
        self.assertEqual(actions_on(c, PG_GREEN), [0x3A])
        self.assertEqual(actions_on(c, PG_ARCANE), [0x48], 'needs no licence')
        self.assertFalse(enabled(pages_of(c)[PG_MAGICKS][0]), 'White has nothing')

    def test_technicks_need_ownership_and_an_acquired_node(self):
        first, second = self.fixture.technick_actions[0], self.fixture.technick_actions[1]
        c = self.build(shared(technicks=(0, 1)), seat(licences=(280,)))
        self.assertEqual(actions_on(c, PG_TECHNICKS), [first])
        c = self.build(shared(technicks=(0, 1)), seat(licences=(276, 280)))
        self.assertEqual(actions_on(c, PG_TECHNICKS), [second, first], 'sort keys list index 1 first')
        c = self.build(shared(technicks=()), seat(licences=(276, 280)))
        self.assertEqual(actions_on(c, PG_TECHNICKS), [])
        self.assertTrue(enabled(pages_of(c)[PG_MAGICKS][5]) is False)

    def test_each_character_has_its_own_quickenings(self):
        for member in range(6):
            base = QUICKENING_BASE[member]
            with self.subTest(member=member):
                bit = base - 0xD0
                c = self.build(shared(mist=(bit, bit + 2)), seat(member=member))
                self.assertEqual(actions_on(c, PG_QUICKENINGS), [base, base + 2])
                self.assertEqual(root_roles(c), [ROLE_ATTACK, ROLE_MAGICKS, ROLE_MIST, ROLE_GAMBITS, ROLE_ITEMS])
                self.assertEqual([e.role for e in pages_of(c)[PG_MIST]], [ROLE_QUICKENING])
                # Quickening licences: as many as acquired (at most three), from the first.
                c = self.build(shared(), seat(member=member, licences=(0, 1)))
                self.assertEqual(actions_on(c, PG_QUICKENINGS), [base, base + 1])
                c = self.build(shared(), seat(member=member, licences=(0, 1, 2, 3, 4)))
                self.assertEqual(actions_on(c, PG_QUICKENINGS), [base, base + 1, base + 2])
                for e in pages_of(c)[PG_QUICKENINGS]:
                    self.assertEqual(e.area, 0, 'a Quickening is single-target in the menu')

    def test_espers_need_the_mist_bit_and_their_licence(self):
        c = self.build(shared(mist=(18, 20, 30)), seat(licences=(18, 30)))
        self.assertEqual(actions_on(c, PG_SUMMONS), [0x106, 0x112])
        self.assertEqual([e.role for e in pages_of(c)[PG_MIST]], [ROLE_SUMMON])
        c = self.build(shared(mist=(18,)), seat(licences=()))
        self.assertNotIn(PG_SUMMONS, pages_of(c))

    def test_dismiss_replaces_mist_for_the_summoner_only(self):
        c = self.build(shared(mist=(18,)), seat(licences=(18,), esper_owner=True))
        self.assertEqual(root_roles(c), [ROLE_ATTACK, ROLE_MAGICKS, ROLE_DISMISS, ROLE_GAMBITS, ROLE_ITEMS])
        dismiss = root_entry(c, ROLE_DISMISS)
        self.assertEqual((dismiss.action, dismiss.groups), (DISMISS, 0))
        self.assertTrue(dismiss.flags & EF_SELF_ONLY and enabled(dismiss))
        c = self.build(shared(mist=(18,)), seat(licences=(18,), esper_owner=False))
        self.assertIn(ROLE_MIST, root_roles(c))

    def test_items_held_listed_with_quantities_and_shown_names(self):
        c = self.build(shared(items={0: 3, 1: 0x400 | 7, 2: 1, 5: 2}), seat())
        rows = pages_of(c)[PG_ITEMS]
        # Sort keys put item 2 before item 1; the counter's upper bits are held back: 0x400|7 -> 7 owned, 6 left.
        self.assertEqual([(e.action, e.display, e.quantity) for e in rows],
                         [(0x52, 0x52, 3), (0x54, 0x54, 1), (0x53, 0x53, 6), (0x57, 0x200, 2)])
        self.assertTrue(enabled(root_entry(c, ROLE_ITEMS)))

    def test_items_are_greyed_on_the_available_count(self):
        # owned = w & 0x3FF, available = owned - (w >> 10): the last Potion another seat queued is not offered.
        for word, quantity, usable in ((1, 1, True), (0x400 | 1, 0, False), (0x800 | 1, 0, False),
                                       (0x400 | 2, 1, True), (0xFC00 | 0x3FF, 0x3FF - 63, True)):
            with self.subTest(word=hex(word)):
                c = self.build(shared(items={0: word}), seat())
                e = pages_of(c)[PG_ITEMS][0]
                self.assertEqual((e.action, e.quantity, enabled(e)), (0x52, quantity, usable))
                self.assertEqual(bool(e.flags & EF_HELD_BACK), not usable)

    def test_items_follow_the_saved_order_only_when_it_names_every_held_item(self):
        held = {0: 1, 1: 1, 2: 1}
        c = self.build(shared(items=held, order=[2, 9, 0, 1]), seat())
        self.assertEqual(actions_on(c, PG_ITEMS), [0x54, 0x52, 0x53], 'saved order, unheld entries skipped')
        c = self.build(shared(items=held, order=[2, 0]), seat())
        self.assertEqual(actions_on(c, PG_ITEMS), [0x52, 0x54, 0x53], 'incomplete: sort keys')
        c = self.build(shared(items=held, order=[2, 0, 2, 1]), seat())
        self.assertEqual(actions_on(c, PG_ITEMS), [0x52, 0x54, 0x53], 'a repeated index: sort keys')

    def test_target_rules_come_from_the_action_table(self):
        c = self.build(shared(magicks=(0, 2), items={6: 1}), seat(summary=(6, 7)))
        attack = root_entry(c, ROLE_ATTACK)
        self.assertEqual(attack.area, 4, "Legacy's Attack area radius")
        self.assertEqual(attack.groups, (1 << TG_FOES) | (1 << TG_PARTY) | (1 << TG_NEUTRALS))
        self.assertEqual(attack.default_group, TG_FOES)
        cure = next(e for e in pages_of(c)[PG_WHITE] if e.action == 0x00)
        self.assertEqual(cure.default_group, TG_PARTY)
        self.assertTrue(cure.flags & EF_PARTY_FIRST)
        raise_ = next(e for e in pages_of(c)[PG_WHITE] if e.action == 0x05)
        self.assertTrue(raise_.flags & EF_REVIVE)
        phoenix = pages_of(c)[PG_ITEMS][0]
        self.assertEqual((phoenix.action, phoenix.default_group), (0x58, TG_PARTY))
        self.assertTrue(phoenix.groups & (1 << TG_RESERVE), 'target flag bit 5: reserve members')
        self.assertTrue(phoenix.flags & EF_REVIVE)

    def test_a_story_guest_uses_its_fixed_actions(self):
        first_technick = self.fixture.technick_actions[0]
        for sh in (shared(menu_flags=0), shared(menu_flags=1, magicks=(0, 1, 2), technicks=range(24))):
            with self.subTest(flags=sh.menu_flags):
                c = self.build(sh, seat(member=7, licences=(0, 1, 18, 276), summary=(6, 7)))
                self.assertIn(ROLE_MAGICKS, root_roles(c), 'fixed actions open Magicks without the menu flag')
                self.assertEqual(actions_on(c, PG_WHITE), [0x00])
                self.assertEqual(actions_on(c, PG_TECHNICKS), [first_technick])
                self.assertNotIn(PG_QUICKENINGS, pages_of(c))
                self.assertNotIn(PG_SUMMONS, pages_of(c))

    def test_party_id_6_uses_ownership_and_licences_not_its_gift_list(self):
        # Its row holds 0x3000 and 0x3006 (a gift list): the game checks party ownership and licences instead.
        c = self.build(shared(menu_flags=1), seat(member=6))
        self.assertEqual(actions_on(c, PG_WHITE), [], 'nothing owned: nothing listed, whatever the row says')
        c = self.build(shared(menu_flags=1, magicks=(0, 1)), seat(member=6, summary=(6,)))
        self.assertEqual(actions_on(c, PG_WHITE), [0x01, 0x00])

    def test_the_signature_follows_every_input(self):
        sh, st = shared(magicks=(0,)), seat(summary=(6,))
        base = self.d.ILCT_MenuSignature(ctypes.byref(sh), ctypes.byref(st))
        self.assertEqual(base, self.build(sh, st).signature)
        changes = [lambda: setattr(st, 'mp', 7), lambda: setattr(st, 'status', ST_SILENCE),
                   lambda: setattr(sh, 'seals', SEAL_ITEM), lambda: sh.item_counts.__setitem__(3, 1),
                   lambda: setattr(st, 'esper_owner', 1), lambda: st.licences.__setitem__(1, 1)]
        for change in changes:
            sh, st = shared(magicks=(0,)), seat(summary=(6,))
            change()
            self.assertNotEqual(self.d.ILCT_MenuSignature(ctypes.byref(sh), ctypes.byref(st)), base)


# ---- restrictions ------------------------------------------------------------------------------------------------
def restriction_inputs(**seat_changes):
    sh = shared(menu_flags=0x1, magicks=(0,), technicks=(1,), mist=(0, 18), items={0: 3})
    st = seat(licences=(18, 276), summary=(6,), mp=99, **seat_changes)
    return sh, st


@native_test
class RestrictionTest(FixtureCase):
    """The restriction table: what each status and seal greys."""

    def tree(self, seals=0, **seat_changes) -> Catalog:
        sh, st = restriction_inputs(**seat_changes)
        sh.seals = seals
        return self.build(sh, st)

    def test_unrestricted_control(self):
        c = self.tree()
        self.assertTrue(all(enabled(e) for e in pages_of(c)[PG_ROOT]))
        self.assertTrue(enabled(pages_of(c)[PG_WHITE][0]))
        self.assertTrue(enabled(pages_of(c)[PG_TECHNICKS][0]))
        self.assertTrue(enabled(pages_of(c)[PG_ITEMS][0]))

    def test_each_hard_status_greys_everything(self):
        for b in HARD_BITS:
            with self.subTest(bit=b):
                c = self.tree(status=1 << b)
                self.assertFalse(any(enabled(e) for e in pages_of(c)[PG_ROOT]))
                for page in (PG_WHITE, PG_TECHNICKS, PG_ITEMS, PG_QUICKENINGS, PG_SUMMONS):
                    self.assertFalse(any(enabled(e) for e in pages_of(c)[page]), page)
                self.assertTrue(all(e.flags & EF_SEALED for e in pages_of(c)[PG_ROOT]))

    def test_berserk_leaves_only_attack(self):
        c = self.tree(status=ST_BERSERK, esper_owner=True)
        for e in pages_of(c)[PG_ROOT]:
            self.assertEqual(enabled(e), e.role == ROLE_ATTACK, e.role)
        self.assertFalse(enabled(pages_of(c)[PG_TECHNICKS][0]))
        self.assertFalse(enabled(pages_of(c)[PG_ITEMS][0]))

    def test_berserk_never_overrides_a_harder_status_or_the_attack_seal(self):
        c = self.tree(status=ST_BERSERK | ST_DISABLE)
        self.assertFalse(any(enabled(e) for e in pages_of(c)[PG_ROOT]))
        c = self.tree(status=ST_BERSERK, seals=SEAL_ATTACK)
        self.assertFalse(enabled(root_entry(c, ROLE_ATTACK)))

    def test_silence_greys_the_schools_and_keeps_technicks(self):
        c = self.tree(status=ST_SILENCE)
        self.assertFalse(enabled(pages_of(c)[PG_WHITE][0]))
        rows = pages_of(c)[PG_MAGICKS]
        self.assertFalse(any(enabled(e) for e in rows[:5]))
        self.assertTrue(enabled(rows[5]))
        self.assertTrue(enabled(pages_of(c)[PG_TECHNICKS][0]))
        self.assertTrue(enabled(root_entry(c, ROLE_MAGICKS)), 'Technicks still reachable')

    def test_silence_closes_magicks_when_nothing_else_is_left(self):
        sh, st = restriction_inputs(status=ST_SILENCE)
        sh.technick_owned[:] = bytes(4)
        c = self.build(sh, st)
        self.assertFalse(enabled(root_entry(c, ROLE_MAGICKS)))

    def test_each_seal_greys_its_own_family(self):
        expect = {SEAL_ATTACK: 'attack', SEAL_MAGICK: 'schools', SEAL_TECHNICK: 'technicks', SEAL_ITEM: 'items'}
        for seal, family in expect.items():
            with self.subTest(family):
                c = self.tree(seals=seal)
                self.assertEqual(enabled(root_entry(c, ROLE_ATTACK)), family != 'attack')
                self.assertEqual(enabled(root_entry(c, ROLE_ITEMS)), family != 'items')
                self.assertEqual(enabled(pages_of(c)[PG_MAGICKS][0]), family != 'schools')
                self.assertEqual(enabled(pages_of(c)[PG_MAGICKS][5]), family != 'technicks')
                self.assertEqual(enabled(pages_of(c)[PG_ITEMS][0]), family != 'items')
                self.assertTrue(enabled(root_entry(c, ROLE_MIST)))
                self.assertTrue(enabled(root_entry(c, ROLE_GAMBITS)))

    def test_the_attack_seal_does_not_block_dismiss(self):
        c = self.tree(seals=SEAL_ATTACK, esper_owner=True)
        self.assertFalse(enabled(root_entry(c, ROLE_ATTACK)))
        self.assertTrue(enabled(root_entry(c, ROLE_DISMISS)))

    def test_gambits_row_stays_under_seals_and_silence(self):
        for kwargs in ({'seals': 0x3C}, {'status': ST_SILENCE}):
            c = self.tree(**kwargs)
            self.assertTrue(enabled(root_entry(c, ROLE_GAMBITS)), kwargs)


# ---- MP: the game's effective cost -------------------------------------------------------------------------------
def f32(x: float) -> float:
    return struct.unpack('<f', struct.pack('<f', x))[0]


def effective_cost(base, kind, action, augments, side, channeling, known=True):
    """The game's effective MP cost, restated (single-precision steps, truncation toward zero)."""
    if action > 0x21E:
        return 0
    if kind in (5, 6):
        return 0
    party = side != 1
    if 32 in augments and party:
        return 0
    if party and (0xD0 <= action <= 0xE1 or 0x106 <= action <= 0x112):
        return base
    if 32 in augments:
        return int(f32(f32(f32(base) * 64.0) / 50.0)) * 10   # the gil price, returned as it is (no halving)
    else:
        present = [k for k in range(3) if 91 + k in augments]
        if present and not known:
            return -1
        s = sum(channeling[k] for k in present)
        b = f32(base)
        cost = int(f32(b - f32(f32(b / 100.0) * f32(s))))
    if 51 in augments:
        return 0
    if 36 in augments:
        return int(cost / 2)
    return cost


@native_test
class CostTest(NativeCase):
    AUGMENT_SETS = [(), (36,), (51,), (91,), (92,), (93,), (91, 92, 93), (36, 91), (36, 51), (51, 92), (32,),
                    (32, 36), (32, 51), (32, 91)]

    def tables(self, channeling=None):
        fx = Fixture()
        sections = fx.sections()
        self.d.ILCT_MenuTablesReset()
        for which, raw in sections.items():
            self.d.ILCT_MenuTablesSection(which, raw, len(raw))
        if channeling is not None:
            augments = [bytes(8) for _ in range(128)]
            for k, value in enumerate(channeling):
                row = bytearray(8)
                struct.pack_into('<H', row, 4, value)
                augments[91 + k] = bytes(row)
            raw = st2e(augments, 8)
            self.assertEqual(self.d.ILCT_MenuTablesSection(T_AUGMENTS, raw, len(raw)), 1)
        self.assertEqual(self.d.ILCT_MenuTablesFinish(), TABLES_OK)
        return fx

    def test_cost_restated_over_every_base_and_augment_set(self):
        channeling = (5, 10, 15)
        fx = self.tables(channeling)
        # one magick row re-costed per base value; plus a Quickening, an Esper, an item and an id past the table
        for base in range(256):
            fx.actions[0x00] = action_row(cost=base, targeting=0x4C, kind=1)
            fx.actions[0xD3] = action_row(cost=base, kind=1)    # a Quickening id with a magick kind
            fx.actions[0x107] = action_row(cost=base, kind=1)   # an Esper id with a magick kind
            fx.actions[0x52] = action_row(cost=base, kind=5)    # paid with charges
            raw = st2e(fx.actions, 60)
            self.d.ILCT_MenuTablesSection(T_ACTIONS, raw, len(raw))
            self.d.ILCT_MenuTablesFinish()
            for augments in self.AUGMENT_SETS:
                for side in (0, 1):
                    st = seat(augments=augments, side=side)
                    for action, kind in ((0x00, 1), (0xD3, 1), (0x107, 1), (0x52, 5)):
                        want = effective_cost(base, kind, action, set(augments), side, channeling)
                        got = self.d.ILCT_MenuCost(action, ctypes.byref(st))
                        if got != want:
                            self.fail(f'base {base} augments {augments} side {side} action {action:#x}: '
                                      f'{got} != {want}')
        self.assertEqual(self.d.ILCT_MenuCost(0x21F, ctypes.byref(seat())), 0)
        self.assertEqual(self.d.ILCT_MenuCost(0x220, ctypes.byref(seat())), 0)

    def test_known_values(self):
        self.tables((5, 10, 15))
        rows = [((), 8), ((36,), 4), ((51,), 0), ((91,), 7), ((91, 92, 93), 5), ((36, 91, 92, 93), 2), ((32,), 0)]
        for augments, want in rows:
            with self.subTest(augments=augments):
                self.assertEqual(self.d.ILCT_MenuCost(0x00, ctypes.byref(seat(augments=augments))), want)

    def test_unknown_channeling_values_give_an_unknown_cost(self):
        self.tables(None)
        self.assertEqual(self.d.ILCT_MenuCost(0x00, ctypes.byref(seat(augments=(92,)))), -1)
        self.assertEqual(self.d.ILCT_MenuCost(0x00, ctypes.byref(seat(augments=(36,)))), 4)

    def test_magicks_grey_below_the_effective_cost(self):
        self.tables((5, 10, 15))
        sh = shared(magicks=(0,))
        for mp, augments, short in ((7, (), True), (8, (), False), (4, (36,), False), (3, (36,), True),
                                    (0, (51,), False), (-1, (51,), True), (5, (91, 92, 93), False),
                                    (4, (91, 92, 93), True)):
            with self.subTest(mp=mp, augments=augments):
                c = self.build(sh, seat(summary=(6,), mp=mp, augments=augments))
                cure = pages_of(c)[PG_WHITE][0]
                self.assertEqual(not enabled(cure), short)
                self.assertEqual(bool(cure.flags & EF_MP_SHORT), short)
                self.assertTrue(enabled(pages_of(c)[PG_MAGICKS][0]), 'the school row ignores MP')

    def test_an_unknown_cost_is_not_greyed(self):
        self.tables(None)
        c = self.build(shared(magicks=(0,)), seat(summary=(6,), mp=0, augments=(91,)))
        cure = pages_of(c)[PG_WHITE][0]
        self.assertTrue(enabled(cure))
        self.assertTrue(cure.flags & EF_COST_UNKNOWN)
        self.assertEqual(cure.cost, -1)

    def test_every_action_is_greyed_by_the_admissions_mp_check(self):
        # The game's admission compares MP with the effective cost of any action, not only magicks: a technick with
        # an MP cost greys like a spell; an item without one stays usable.
        fx = self.tables((5, 10, 15))
        fx.actions[fx.technick_actions[0]] = action_row(cost=50, targeting=0x24, kind=2)
        raw = st2e(fx.actions, 60)
        self.d.ILCT_MenuTablesSection(T_ACTIONS, raw, len(raw))
        self.d.ILCT_MenuTablesFinish()
        c = self.build(shared(technicks=(0,), items={0: 1}), seat(licences=(280,), mp=0))
        technick = pages_of(c)[PG_TECHNICKS][0]
        self.assertFalse(enabled(technick))
        self.assertTrue(technick.flags & EF_MP_SHORT)
        self.assertEqual(technick.cost, 50)
        self.assertTrue(enabled(pages_of(c)[PG_ITEMS][0]))
        c = self.build(shared(technicks=(0,), items={0: 1}), seat(licences=(280,), mp=50))
        self.assertTrue(enabled(pages_of(c)[PG_TECHNICKS][0]))


# ---- the model -----------------------------------------------------------------------------------------------------
def hand_entry(action=0, kind=EK_ACTION, page=PG_NONE, flags=EF_ENABLED, groups=1 << TG_PARTY,
               default_group=TG_PARTY, quantity=0, role=ROLE_ACTION) -> Entry:
    e = Entry()
    e.kind, e.flags, e.page, e.groups, e.action, e.display = kind, flags, page, groups, action, action
    e.default_group, e.quantity, e.role = default_group, quantity, role
    return e


def hand_catalog(pages: dict[int, list[Entry]], member=0) -> Catalog:
    c = Catalog()
    c.valid, c.member = 1, member
    used = 0
    for page, rows in pages.items():
        c.pages[page].first, c.pages[page].count, c.pages[page].present = used, len(rows), 1
        for e in rows:
            c.entries[used] = e
            used += 1
    c.used = used
    return c


@native_test
class SelectionPreservationTest(NativeCase):
    """Catalog commits keep the guest where it was (restated from the guest-menu catalog tests)."""

    SEAT = 1

    def setUp(self):
        self.d.ILCT_MenuSeatReset(self.SEAT)

    def commit(self, c: Catalog) -> tuple[int, Commit]:
        r = Commit()
        return self.d.ILCT_MenuSeatCommit(self.SEAT, ctypes.byref(c), ctypes.byref(r)), r

    def test_selected_action_follows_a_reorder(self):
        a, b = hand_entry(1), hand_entry(2)
        self.commit(hand_catalog({PG_ROOT: [a, b]}))
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        status, _ = self.commit(hand_catalog({PG_ROOT: [b, a]}))
        self.assertEqual(status, 0)
        self.assertEqual(self.selected().action, 2)
        self.assertEqual(self.model().stack[0].index, 0)

    def test_a_submenu_removed_by_new_capabilities_is_dropped(self):
        mist_row = hand_entry(kind=EK_PAGE, page=PG_MIST, role=ROLE_MIST)
        self.commit(hand_catalog({PG_ROOT: [mist_row], PG_MIST: [hand_entry(3)]}))
        self.op(self.SEAT, OP_OPEN)
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_PAGE_OPENED)
        status, r = self.commit(hand_catalog({PG_ROOT: []}))
        m = self.model()
        self.assertEqual((status, m.mode, m.depth, m.stack[0].page, r.depth), (0, MODE_LIST, 1, PG_ROOT, 1))

    def test_a_page_no_longer_linked_from_its_parent_is_dropped(self):
        mist_row = hand_entry(kind=EK_PAGE, page=PG_MIST, role=ROLE_MIST)
        self.commit(hand_catalog({PG_ROOT: [mist_row], PG_MIST: [hand_entry(3)]}))
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_CONFIRM)
        self.commit(hand_catalog({PG_ROOT: [hand_entry(1)], PG_MIST: [hand_entry(3)]}))
        self.assertEqual(self.model().depth, 1)

    def test_a_pending_action_is_refreshed_without_leaving_target_mode(self):
        self.commit(hand_catalog({PG_ROOT: [hand_entry(9, quantity=2)]}))
        self.op(self.SEAT, OP_OPEN)
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_TARGET_OPENED)
        status, r = self.commit(hand_catalog({PG_ROOT: [hand_entry(9, quantity=4)]}))
        m = self.model()
        self.assertEqual((status, r.target_kept, r.target_cancelled), (0, 1, 0))
        self.assertEqual((m.mode, m.pending.quantity), (MODE_TARGET, 4))

    def test_the_target_is_cancelled_when_the_action_disappears(self):
        self.commit(hand_catalog({PG_ROOT: [hand_entry(9)]}))
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_CONFIRM)
        _, r = self.commit(hand_catalog({PG_ROOT: []}))
        m = self.model()
        self.assertEqual((r.target_cancelled, m.mode, m.pending.action, m.pending.kind), (1, MODE_LIST, 0, 0))

    def test_the_target_is_cancelled_when_the_game_disables_the_action(self):
        self.commit(hand_catalog({PG_ROOT: [hand_entry(9)]}))
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_CONFIRM)
        _, r = self.commit(hand_catalog({PG_ROOT: [hand_entry(9, flags=0)]}))
        self.assertEqual((r.target_cancelled, self.model().mode), (1, MODE_LIST))

    def test_an_invalid_tree_changes_nothing(self):
        original = hand_catalog({PG_ROOT: [hand_entry(1)]})
        self.commit(original)
        self.op(self.SEAT, OP_OPEN)
        before = bytes(self.model())
        broken = hand_catalog({PG_ROOT: [hand_entry(kind=EK_PAGE, page=PG_MIST)]})  # links a page it lacks
        status, _ = self.commit(broken)
        self.assertEqual(status, -1)
        self.assertEqual(bytes(self.model()), before)
        no_root = hand_catalog({PG_MIST: [hand_entry(1)]})
        self.assertEqual(self.commit(no_root)[0], -1)
        invalid = hand_catalog({PG_ROOT: [hand_entry(1)]})
        invalid.valid = 0
        self.assertEqual(self.commit(invalid)[0], -1)

    def test_a_missing_row_keeps_the_position_on_the_next_selectable_row(self):
        rows = [hand_entry(1), hand_entry(2), hand_entry(3)]
        self.commit(hand_catalog({PG_ROOT: rows}))
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 2, 1)
        label = hand_entry(kind=EK_LABEL, flags=0, role=ROLE_EMPTY)
        self.commit(hand_catalog({PG_ROOT: [hand_entry(1), hand_entry(2), label]}))
        self.assertEqual(self.model().stack[0].index, 0, 'past the end wraps to the next selectable row')

    def test_a_closed_menu_takes_the_new_catalog_and_stays_closed(self):
        self.commit(hand_catalog({PG_ROOT: [hand_entry(1)]}))
        _, r = self.commit(hand_catalog({PG_ROOT: [hand_entry(2)]}))
        m = self.model()
        self.assertEqual((m.mode, r.changed, m.pages.entries[0].action), (MODE_CLOSED, 1, 2))
        _, r = self.commit(hand_catalog({PG_ROOT: [hand_entry(2)]}))
        self.assertEqual(r.changed, 0)


@native_test
class NavigationTest(FixtureCase):
    SEAT = 2

    def setUp(self):
        self.d.ILCT_MenuSeatReset(self.SEAT)
        sh = shared(magicks=(0, 1, 3), mist=(0,), items={0: 2, 1: 1})
        st = seat(member=0, summary=(6, 9), mp=99)
        r = Commit()
        self.assertEqual(self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(sh), ctypes.byref(st), ctypes.byref(r)), 1)

    def test_open_navigate_and_wrap(self):
        e = self.op(self.SEAT, OP_OPEN)
        self.assertEqual((e.kind, e.page, e.index), (EV_OPENED, PG_ROOT, 0))
        self.assertEqual(self.selected(self.SEAT).role, ROLE_ATTACK)
        self.assertEqual(self.op(self.SEAT, OP_NAV, -1, 1).index, 4, 'wraps to Items')
        self.assertEqual(self.op(self.SEAT, OP_NAV, 1, 1).index, 0)
        self.assertEqual(self.op(self.SEAT, OP_NAV, -1, 0).kind, EV_IGNORED, 'a held repeat stops at the top')

    def test_pages_open_and_close(self):
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        e = self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual((e.kind, e.page), (EV_PAGE_OPENED, PG_MAGICKS))
        e = self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual((e.kind, e.page), (EV_PAGE_OPENED, PG_WHITE))
        self.assertEqual(self.selected(self.SEAT).action, 0x01)
        e = self.op(self.SEAT, OP_BACK)
        self.assertEqual((e.kind, e.page, e.index), (EV_PAGE_CLOSED, PG_MAGICKS, 0))
        self.op(self.SEAT, OP_BACK)
        self.assertEqual(self.op(self.SEAT, OP_BACK).kind, EV_CLOSED)

    def test_a_locked_school_is_navigable_but_does_not_open(self):
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.assertEqual(self.selected(self.SEAT).page, PG_BLACK)
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_IGNORED)
        self.assertEqual(self.model(self.SEAT).stack[1].page, PG_MAGICKS)

    def test_page_jumps_stop_at_the_ends(self):
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.op(self.SEAT, OP_CONFIRM)                 # Magicks & Technicks
        self.op(self.SEAT, OP_NAV, 5, 1)
        self.assertEqual(self.op(self.SEAT, OP_PAGE, 1, 10).kind, EV_IGNORED, 'already at the last row')
        self.assertEqual(self.op(self.SEAT, OP_PAGE, -1, 3).index, 2)
        self.assertEqual(self.op(self.SEAT, OP_PAGE, -1, 10).index, 0)

    def test_gambits_row_emits_a_toggle(self):
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 3, 1)
        self.assertEqual(self.selected(self.SEAT).role, ROLE_GAMBITS)
        e = self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual(e.kind, EV_TOGGLE_GAMBITS)
        self.assertEqual(self.model(self.SEAT).mode, MODE_LIST, 'the toggle keeps the menu open')

    def test_target_mode_submit_and_back(self):
        self.op(self.SEAT, OP_OPEN)
        e = self.op(self.SEAT, OP_CONFIRM)                                  # Attack
        self.assertEqual((e.kind, e.action, e.group), (EV_TARGET_OPENED, ATTACK, TG_FOES))
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_INVALID_TARGET, 'no candidates yet')
        self.op(self.SEAT, OP_TARGETS, 3)
        self.assertEqual(self.op(self.SEAT, OP_NAV, -1, 1).index, 2)
        e = self.op(self.SEAT, OP_GROUP, 1, (1 << TG_PARTY) | (1 << TG_FOES))
        self.assertEqual((e.kind, e.group), (EV_TARGET_GROUP, TG_PARTY), 'foes first, neutrals have nobody')
        self.op(self.SEAT, OP_TARGETS, 2)
        e = self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual((e.kind, e.action, e.group, e.index), (EV_SUBMIT, ATTACK, TG_PARTY, 0))
        self.assertEqual(self.model(self.SEAT).mode, MODE_CLOSED)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual(self.op(self.SEAT, OP_BACK).kind, EV_TARGET_CLOSED)
        self.assertEqual(self.model(self.SEAT).mode, MODE_LIST)

    def test_a_self_only_action_submits_at_once(self):
        sh = shared(mist=(18,))
        st = seat(member=0, licences=(18,), esper_owner=True)
        self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(sh), ctypes.byref(st), None)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 2, 1)
        self.assertEqual(self.selected(self.SEAT).role, ROLE_DISMISS)
        e = self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual((e.kind, e.action, e.group), (EV_SUBMIT, DISMISS, TG_SELF))

    def test_disabled_rows_do_nothing(self):
        sh = shared(magicks=(0, 1, 3))
        st = seat(member=0, summary=(6, 9), status=ST_SILENCE)
        self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(sh), ctypes.byref(st), None)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_IGNORED, 'Magicks closed: nothing left')


@native_test
class RefreshTest(FixtureCase):
    SEAT = 3

    def setUp(self):
        self.d.ILCT_MenuSeatReset(self.SEAT)

    def refresh(self, sh, st) -> tuple[int, Commit]:
        r = Commit()
        return self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(sh), ctypes.byref(st), ctypes.byref(r)), r

    def counters(self):
        c = (u64 * 6)()
        self.d.ILCT_MenuSeatCounters(self.SEAT, c)
        return {'signature': c[0], 'status': ctypes.c_int64(c[1]).value, 'builds': c[2], 'unchanged': c[3],
                'kept': c[4], 'identity_closes': c[5]}

    def test_rebuilds_only_when_an_input_changes(self):
        sh, st = shared(magicks=(0,)), seat(summary=(6,))
        self.assertEqual(self.refresh(sh, st)[0], 1)
        self.assertEqual(self.refresh(sh, st)[0], 0)
        st.mp = 1
        self.assertEqual(self.refresh(sh, st)[0], 1)
        self.assertEqual((self.counters()['builds'], self.counters()['unchanged']), (2, 1))

    def test_mp_drop_greys_the_selected_spell_and_keeps_the_cursor(self):
        sh, st = shared(magicks=(0, 1)), seat(summary=(6,), mp=99)
        self.refresh(sh, st)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.assertEqual(self.selected(self.SEAT).action, 0x00)
        st.mp = 7
        self.refresh(sh, st)
        sel = self.selected(self.SEAT)
        self.assertEqual(sel.action, 0x00)
        self.assertFalse(enabled(sel))
        self.assertEqual([f.page for f in self.model(self.SEAT).stack[:3]], [PG_ROOT, PG_MAGICKS, PG_WHITE])

    def test_an_item_used_up_while_targeting_cancels_the_target(self):
        sh, st = shared(items={0: 1, 1: 1}), seat()
        self.refresh(sh, st)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 3, 1)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_CONFIRM)
        self.assertEqual(self.model(self.SEAT).mode, MODE_TARGET)
        sh.item_counts[0] = 0
        _, r = self.refresh(sh, st)
        self.assertEqual((r.target_cancelled, self.model(self.SEAT).mode), (1, MODE_LIST))
        self.assertEqual(self.selected(self.SEAT).action, 0x53, 'the cursor moves to the next item')

    def test_an_item_count_change_keeps_the_target(self):
        sh, st = shared(items={0: 3}), seat()
        self.refresh(sh, st)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 3, 1)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_CONFIRM)
        sh.item_counts[0] = 2
        _, r = self.refresh(sh, st)
        m = self.model(self.SEAT)
        self.assertEqual((r.target_kept, m.mode, m.pending.quantity), (1, MODE_TARGET, 2))

    def test_berserk_mid_menu_closes_the_open_page_family(self):
        sh, st = shared(technicks=(0,)), seat(licences=(280,))
        self.refresh(sh, st)
        self.op(self.SEAT, OP_OPEN)
        self.op(self.SEAT, OP_NAV, 1, 1)
        self.op(self.SEAT, OP_CONFIRM)
        self.op(self.SEAT, OP_NAV, 5, 1)
        self.op(self.SEAT, OP_CONFIRM)
        st.status = ST_BERSERK
        self.refresh(sh, st)
        sel = self.selected(self.SEAT)
        self.assertFalse(enabled(sel))
        self.assertEqual(self.op(self.SEAT, OP_CONFIRM).kind, EV_IGNORED)

    def test_a_character_change_closes_the_menu(self):
        sh = shared()
        self.refresh(sh, seat(member=0))
        self.op(self.SEAT, OP_OPEN)
        status, _ = self.refresh(sh, seat(member=2))
        self.assertEqual(status, 1)
        m = self.model(self.SEAT)
        self.assertEqual((m.mode, m.pages.member), (MODE_CLOSED, 2))
        self.assertEqual(self.counters()['identity_closes'], 1)

    def test_a_failed_build_after_a_character_change_serves_nothing(self):
        sh = shared(items={0: 1})
        self.refresh(sh, seat(member=0))
        self.op(self.SEAT, OP_OPEN)
        status, _ = self.refresh(sh, seat(member=0x40))
        self.assertEqual(status, NO_MEMBER)
        m = self.model(self.SEAT)
        self.assertEqual(m.mode, MODE_CLOSED, 'the member changed, so the menu closed')
        self.assertEqual(self.counters()['kept'], 1)
        self.assertEqual((m.pages.valid, self.counters()['signature']), (0, 0),
                         "the previous character's pages are gone")
        self.assertEqual(self.op(self.SEAT, OP_OPEN).kind, EV_IGNORED, 'nothing to open')
        status, _ = self.refresh(sh, seat(member=0))
        self.assertEqual((status, self.model(self.SEAT).pages.member), (1, 0), 'a good build serves again')


# ---- Gambits -------------------------------------------------------------------------------------------------------
def identity(member=1, handle=0x00100003, actor=0x5000, physics=0x6000, block=0x100000, record=None) -> Identity:
    i = Identity()
    i.member, i.handle, i.actor, i.physics, i.party_block = member, handle, actor, physics, block
    i.record = block + MEMBER_RECORDS + member * MEMBER_RECORD_SIZE if record is None else record
    return i


@native_test
class GambitTest(NativeCase):
    SEAT = 1

    def setUp(self):
        self.d.ILCT_MenuSeatReset(self.SEAT)

    def state(self) -> Gambits:
        g = Gambits()
        self.d.ILCT_MenuGambitState(self.SEAT, ctypes.byref(g))
        return g

    def test_identity_proof_table(self):
        rows = [
            ('every member 0..5', [identity(member=m) for m in range(6)], GB_APPLIED),
            ('a story guest or Esper', [identity(member=m) for m in (6, 14, 27)], GB_INVALID_MEMBER),
            ('no handle', [identity(handle=0)], GB_STALE_IDENTITY),
            ('no actor', [identity(actor=0)], GB_STALE_IDENTITY),
            ('no physics', [identity(physics=0)], GB_STALE_PHYSICS),
            ('no record', [identity(record=0)], GB_STALE_RECORD),
            ('no party block', [identity(block=0, record=0x5000)], GB_STALE_RECORD),
            ("another member's record", [identity(member=1, record=0x100000 + 8 + 2 * 0x1C8)], GB_STALE_RECORD),
            ('a record off by one byte', [identity(record=0x100000 + 8 + 0x1C8 + 1)], GB_STALE_RECORD),
        ]
        for name, ids, want in rows:
            for i in ids:
                with self.subTest(name, member=i.member):
                    self.assertEqual(self.d.ILCT_MenuGambitCheck(ctypes.byref(i)), want)

    def test_mirror_request_result_and_sounds(self):
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), 0), GB_UNKNOWN)
        self.d.ILCT_MenuGambitObserve(self.SEAT, 0x04)
        self.assertEqual((self.state().known, self.state().enabled), (1, 1))
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), 0), GB_PENDING)
        g = self.state()
        self.assertEqual((g.in_flight, g.desired), (1, 0))
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), 0), GB_BUSY)
        self.d.ILCT_MenuGambitObserve(self.SEAT, 0x04)  # a refresh while in flight does not decide
        self.d.ILCT_MenuGambitComplete(self.SEAT, GB_APPLIED, 0)
        g = self.state()
        self.assertEqual((g.in_flight, g.enabled, g.sound, g.last_result, g.applied), (0, 0, 0x13, GB_APPLIED, 1))
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), 0), GB_PENDING)
        self.d.ILCT_MenuGambitComplete(self.SEAT, GB_APPLIED, 1)
        self.assertEqual((self.state().enabled, self.state().sound), (1, 0x12))

    def test_refusals_keep_the_state_and_play_nothing(self):
        self.d.ILCT_MenuGambitObserve(self.SEAT, 0x00)
        for status in (ST_KO, ST_STONE, ST_KO | ST_SILENCE):
            self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), status), GB_BLOCKED)
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity(member=7)), 0),
                         GB_INVALID_MEMBER)
        self.assertEqual(self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), ST_SILENCE), GB_PENDING)
        for result in (0x10, GB_STALE_IDENTITY, GB_STALE_PHYSICS, GB_STALE_RECORD, GB_BLOCKED):
            self.d.ILCT_MenuGambitComplete(self.SEAT, result, 1)
            g = self.state()
            self.assertEqual((g.enabled, g.sound, g.last_result, g.in_flight), (0, 0, result, 0))
            self.d.ILCT_MenuGambitRequest(self.SEAT, ctypes.byref(identity()), 0)
        self.d.ILCT_MenuGambitComplete(self.SEAT, GB_APPLIED, 7)  # applied, but the read-back is not 0/1
        self.assertEqual((self.state().last_result, self.state().enabled), (GB_VERIFY_FAILED, 0))

    def test_refresh_mirrors_the_record_bit(self):
        fx = Fixture()
        load_sections(self.d, fx.sections())
        self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(shared()), ctypes.byref(seat(gambits=False)), None)
        self.assertEqual((self.state().known, self.state().enabled), (1, 0))
        self.d.ILCT_MenuSeatRefresh(self.SEAT, ctypes.byref(shared()), ctypes.byref(seat(gambits=True)), None)
        self.assertEqual(self.state().enabled, 1)


# ---- live samples ----------------------------------------------------------------------------------------------
@native_test
class LiveSampleTest(NativeCase):
    def setUp(self):
        self.img = Image()
        self.location = self.img.alloc(0x3000)
        self.block = self.img.alloc(0x6000)
        self.img.put_global(LOCATION_PTR, struct.pack('<Q', self.location))
        self.img.put_global(BATTLE_STATE_PTR, struct.pack('<Q', self.block))
        self.img.put_global(COMMAND_SEALS, bytes([SEAL_ITEM]))
        self.img.p32(self.location + MENU_FLAGS, 0x181)
        self.img.p32(self.location + PARTY_GIL, 0x80000005)   # read as the game compares it: signed
        self.img.p16(self.block + ITEM_COUNTS + 2 * 3, 0x400 | 9)
        self.img.put(self.block + MAGICK_OWNED, bits(12, 0, 95))
        self.img.put(self.block + TECHNICK_OWNED, bits(4, 23))
        self.img.put(self.block + MIST_OWNED, bits(4, 18))
        # The saved order sits at a 32-bit address (relative to the resource base, which is 0).
        self.heap = LowHeap(0x10000)
        self.order = self.heap.alloc(0x80)
        self.owner = self.img.alloc(0x5000)
        self.img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0))
        self.img.put_global(ITEM_ORDER_OWNER_PTR, struct.pack('<Q', self.owner))
        self.img.put_global(ITEM_ORDER_OFFSET, struct.pack('<I', self.order))
        self.img.p32(self.owner + ITEM_ORDER_COUNT, 3)
        Image.put(self.order, struct.pack('<3H', 3, 1, 2))

    def tearDown(self):
        self.img.close()
        self.heap.close()

    def shared_sample(self):
        s = Shared()
        return self.d.ILCT_MenuReadShared(self.img.base, ctypes.byref(s)), s

    def test_shared_state(self):
        status, s = self.shared_sample()
        self.assertEqual(status, SAMPLE_OK)
        self.assertEqual((s.menu_flags, s.seals, s.item_counts[3]), (0x181, SEAL_ITEM, 0x400 | 9))
        self.assertEqual((bytes(s.magick_owned), bytes(s.technick_owned), bytes(s.mist_owned)),
                         (bits(12, 0, 95), bits(4, 23), bits(4, 18)))
        self.assertEqual((s.order_known, s.order_count, list(s.item_order)[:3]), (1, 3, [3, 1, 2]))
        self.assertEqual(s.gil, -0x7FFFFFFB)

    def test_the_item_order_needs_a_zero_resource_base(self):
        self.img.put_global(RESOURCE_BASE_PTR, struct.pack('<Q', 0x40))
        status, s = self.shared_sample()
        self.assertEqual((status, s.order_known), (SAMPLE_OK, 0), 'a non-zero base: the order is not trusted')

    def test_the_item_order_is_optional(self):
        self.img.p32(self.owner + ITEM_ORDER_COUNT, 65)
        status, s = self.shared_sample()
        self.assertEqual((status, s.order_known, s.order_count), (SAMPLE_OK, 0, 0))
        self.img.put_global(ITEM_ORDER_OWNER_PTR, struct.pack('<Q', 0))
        self.assertEqual(self.shared_sample()[1].order_known, 0)

    def test_missing_blocks_and_unreadable_fields(self):
        self.img.put_global(BATTLE_STATE_PTR, struct.pack('<Q', 0))
        self.assertEqual(self.shared_sample()[0], SAMPLE_NO_STATE)
        self.img.put_global(BATTLE_STATE_PTR, struct.pack('<Q', self.block))
        Image.protect(self.block + ITEM_COUNTS, 0x80, 0x01)
        try:
            status, s = self.shared_sample()
            self.assertEqual(status, SAMPLE_UNREADABLE)
            self.assertEqual(bytes(s), bytes(ctypes.sizeof(Shared)), 'nothing half-read is returned')
        finally:
            Image.protect(self.block + ITEM_COUNTS, 0x80, 0x04)

    def actor(self, member, handle=0x00100002, record=None):
        actor = self.img.alloc(ACTOR_GIL_RESERVED + 4)
        self.img.p32(actor + 0x08, handle)
        rec = self.block + MEMBER_RECORDS + member * MEMBER_RECORD_SIZE if record is None else record
        self.img.p64(actor + 0x698, rec)
        return actor, rec

    def seat_sample(self, actor, handle, member, esper=0):
        s = Seat()
        return self.d.ILCT_MenuReadSeat(self.img.base, actor, handle, member, esper, ctypes.byref(s)), s

    def test_seat_state_through_the_bound_actor(self):
        actor, rec = self.actor(2)
        self.img.p8(rec + 0x00, 0x04)
        self.img.p8(rec + 0x05, 0)
        self.img.p16(rec + 0x4C, 0xFFFE)           # -2: the admission check compares MP signed
        self.img.p8(rec + 0x4E, 3)
        self.img.p32(rec + 0x64, ST_SILENCE)
        self.img.p32(rec + 0x3C, ST_BERSERK)
        self.img.put(rec + 0x68, bits(12, 36))
        self.img.put(rec + 0x78, bits(12, 91))
        licences = bits(46, 18, 182, 276)    # bit 182 = summary flag 6 (+0x1AA is byte 22 of +0x194)
        self.img.put(rec + 0x194, licences)
        self.img.p8(rec + REC_MAGICK, 57)
        self.img.p32(actor + ACTOR_GIL_RESERVED, 1280)
        status, s = self.seat_sample(actor, 0x00100002, 2, esper=1)
        self.assertEqual(status, SAMPLE_OK)
        self.assertEqual((s.member, s.record_state, s.mp, s.charges, s.status, s.esper_owner),
                         (2, 0x04, -2, 3, ST_SILENCE | ST_BERSERK, 1))
        self.assertEqual(bytes(s.augments), bits(12, 36, 91), 'timed and lasting augments together')
        self.assertEqual((bytes(s.licences), bytes(s.magick_licences)), (licences, licences[22:45]))
        self.assertEqual(s.magick_licences[0], 0x40, 'summary flag 6')
        self.assertEqual((s.magick, s.gil_reserved), (57, 1280), 'Magick and the gil put aside for a queued action')

    def test_a_stale_actor_or_foreign_record_is_refused(self):
        actor, _ = self.actor(2)
        self.assertEqual(self.seat_sample(actor, 0x00100099, 2)[0], SAMPLE_STALE)
        self.assertEqual(self.seat_sample(actor, 0x00100002, 3)[0], SAMPLE_STALE, "another member's record")
        actor, _ = self.actor(9)
        self.assertEqual(self.seat_sample(actor, 0x00100002, 9)[0], SAMPLE_OK, "a story guest's party-block record")
        actor, _ = self.actor(9, record=self.img.alloc(0x1C8))
        self.assertEqual(self.seat_sample(actor, 0x00100002, 9)[0], SAMPLE_STALE, 'any other record is refused')
        actor, _ = self.actor(0x28, record=self.img.alloc(0x1C8))
        self.assertEqual(self.seat_sample(actor, 0x00100002, 0x28)[0], SAMPLE_STALE, 'past the getter bound')
        actor, _ = self.actor(1, record=0)
        self.assertEqual(self.seat_sample(actor, 0x00100002, 1)[0], SAMPLE_STALE)
        self.assertEqual(self.seat_sample(0, 0x00100002, 1)[0], SAMPLE_NO_STATE)

    def test_live_sample_to_catalog(self):
        fx = Fixture()
        load_sections(self.d, fx.sections())
        actor, rec = self.actor(0)
        self.img.put(rec + 0x194, bits(46, 182, 280))
        self.img.p16(rec + 0x4C, 50)
        self.img.put(self.block + MAGICK_OWNED, bits(12, 0, 1))
        self.img.put(self.block + TECHNICK_OWNED, bits(4, 0))
        _, sh = self.shared_sample()
        _, st = self.seat_sample(actor, 0x00100002, 0)
        c = Catalog()
        self.assertEqual(self.d.ILCT_MenuBuild(ctypes.byref(sh), ctypes.byref(st), ctypes.byref(c)), BUILD_OK)
        self.assertEqual(actions_on(c, PG_WHITE), [0x01, 0x00])
        self.assertEqual(actions_on(c, PG_TECHNICKS), [fx.technick_actions[0]])
        self.assertEqual(actions_on(c, PG_ITEMS), [0x55])
        self.assertFalse(enabled(root_entry(c, ROLE_ITEMS)), 'the area seals items')


# ---- the real tables ---------------------------------------------------------------------------------------------
def pack_sections(pack_path: str, loose_dir: str | None) -> dict[int, bytes]:
    """Sections as the game loads them: the pack's own, replaced by any loose section_NNN.bin."""
    pack = Path(pack_path).read_bytes()
    count = struct.unpack_from('<I', pack)[0]
    offsets = struct.unpack_from(f'<{count + 1}I', pack, 4)
    out = {}
    for which, index in SECTION_OF.items():
        raw = pack[offsets[index]:offsets[index + 1]]
        loose = Path(loose_dir) / f'section_{index:03d}.bin' if loose_dir else None
        if loose is not None and loose.is_file():
            raw = loose.read_bytes()
        out[which] = raw
    return out


def bit_of(raw: bytes, index: int) -> bool:
    byte, shift = divmod(index, 8)
    return index >= 0 and byte < len(raw) and (raw[byte] >> shift) & 1 == 1


class Listing:
    """The listing rules restated over raw sections (independent of the module's parser)."""

    def __init__(self, sections):
        self.actions = section_rows(sections[T_ACTIONS])
        self.licences = section_rows(sections[T_LICENCES])
        self.magicks = section_rows(sections[T_MAGICKS])
        self.technicks = section_rows(sections[T_TECHNICKS])
        self.items = section_rows(sections[T_ITEMS])

    def rules(self, action):
        return struct.unpack_from('<H', self.actions[action], 0x2C)[0]

    def node_actions(self, node):
        row = self.licences[node]
        if not (9 <= row[5] <= 14 or row[5] == 20):
            return set()
        return {v for v in struct.unpack_from('<8H', row, 8) if v != 0xFFFF}

    def summary_nodes(self, summary):
        special = dict(zip(range(32, 43), (211, 209, 210, 208, 212, 213, 214, 216, 215, 217, 218)))
        nodes = set()
        for flag in range(23 * 8):
            if bit_of(summary, flag):
                if 6 <= flag <= 31 or 179 <= flag <= 183:
                    nodes.add(flag + 176)
                elif flag in special:
                    nodes.add(special[flag])
        return nodes

    def school(self, sh, st, school):
        licensed = set()
        for node in self.summary_nodes(bytes(st.magick_licences)):
            if node < len(self.licences):
                licensed |= self.node_actions(node)
        rows = [(r[6], k, r[2]) for k, r in enumerate(self.magicks)
                if self.rules(r[2]) & 7 == school and bit_of(bytes(sh.magick_owned), k)
                and (r[2] in licensed or self.rules(r[2]) & 0x2000)]
        return [a for _, _, a in sorted(rows)]

    def technick_list(self, sh, st):
        licensed = set()
        for node in range(min(len(self.licences), 368)):
            if bit_of(bytes(st.licences), node):
                licensed |= self.node_actions(node)
        rows = [(r[6], k, r[2]) for k, r in enumerate(self.technicks)
                if bit_of(bytes(sh.technick_owned), k) and (r[2] in licensed or self.rules(r[2]) & 0x2000)]
        return [a for _, _, a in sorted(rows)]

    def item_list(self, sh):
        rows = [(r[0], k, r[7]) for k, r in enumerate(self.items) if sh.item_counts[k] & 0x3FF]
        return [a for _, _, a in sorted(rows)]


@native_test
@unittest.skipUnless(BATTLE_PACK, 'set ILC_BATTLE_PACK (and ILC_BATTLE_PACK_DIR for loose sections) for the '
                                  'real tables')
class RealTablesTest(NativeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sections = pack_sections(BATTLE_PACK, BATTLE_PACK_DIR)
        cls.listing = Listing(cls.sections)
        cls.status = load_sections(cls.d, cls.sections)

    def test_tables_parse(self):
        self.assertEqual(self.status, TABLES_OK)
        t = Tables()
        self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
        self.assertEqual(t.action_count, len(self.listing.actions))
        self.assertEqual(t.item_count, 64)
        self.assertEqual(t.magick_count, len(self.listing.magicks))
        self.assertEqual(t.technick_count, len(self.listing.technicks))
        self.assertEqual(t.fixed_known, 1)
        augments = section_rows(self.sections[T_AUGMENTS])
        self.assertGreaterEqual(len(augments), 129, 'section 58')
        self.assertEqual(t.channeling_known, 1, 'all augment rows accepted')
        self.assertEqual(list(t.channeling), [struct.unpack_from('<H', augments[k], 4)[0] for k in (91, 92, 93)])

    def test_the_real_member_table_gives_the_fixed_list_guests_only_their_list(self):
        members = section_rows(self.sections[T_MEMBERS])
        sh = shared(menu_flags=0x181, magicks=range(96), technicks=range(32))
        for member in list(range(7, 0x1A)) + list(range(0x1B, 0x28)):
            with self.subTest(member=hex(member)):
                listed = set(struct.unpack_from('<10H', members[member], 0x58))
                c = self.build(sh, seat(member=member, licences=range(368), summary=range(184)))
                magicks = sorted(self.listing.magicks[v - 0x3000][2] for v in listed
                                 if 0x3000 <= v < 0x3000 + len(self.listing.magicks))
                got = sorted(a for k in range(5) for a in actions_on(c, PG_WHITE + k))
                self.assertEqual(got, magicks, 'everything owned and licensed: still only the fixed list')

    def test_every_character_and_licence_state_matches_the_listing_rules(self):
        rng = random.Random(1234)
        states = [('nothing', 0.0), ('a quarter', 0.25), ('half', 0.5), ('everything', 1.0)]
        for member in range(6):
            for name, share in states:
                with self.subTest(member=member, licences=name):
                    pick = lambda n: [k for k in range(n) if rng.random() < share]  # noqa: E731
                    sh = shared(menu_flags=0x181, magicks=pick(96), technicks=pick(32), mist=pick(31),
                                items={k: rng.randint(1, 99) for k in pick(64)})
                    st = seat(member=member, licences=pick(368), summary=pick(184), mp=rng.randint(0, 999))
                    c = self.build(sh, st)
                    for school in range(5):
                        self.assertEqual(actions_on(c, PG_WHITE + school), self.listing.school(sh, st, school))
                    self.assertEqual(actions_on(c, PG_TECHNICKS), self.listing.technick_list(sh, st))
                    self.assertEqual(actions_on(c, PG_ITEMS), self.listing.item_list(sh))
                    if share == 1.0:  # instrument: the full state lists something on every kind of page
                        listed = sum(len(actions_on(c, PG_WHITE + k)) for k in range(5))
                        self.assertGreater(listed, 40)
                        self.assertGreater(len(actions_on(c, PG_TECHNICKS)), 20)
                        self.assertEqual(len(actions_on(c, PG_ITEMS)), 64)
                        self.assertEqual(len(actions_on(c, PG_QUICKENINGS)), 3)
                        self.assertEqual(len(actions_on(c, PG_SUMMONS)), 13)
                    for page in range(PG_WHITE, PG_ARCANE + 1):
                        for e in pages_of(c)[page]:
                            if e.kind == EK_ACTION:
                                row = self.listing.actions[e.action]
                                self.assertEqual(e.cost, row[0x0A], 'no augments: the base cost')
                                self.assertEqual(enabled(e), st.mp >= row[0x0A])

    def test_entries_carry_the_loaded_action_rules(self):
        c = self.build(shared(), seat())
        attack = root_entry(c, ROLE_ATTACK)
        self.assertEqual(attack.area, self.listing.actions[ATTACK][0x06])
        if BATTLE_PACK_DIR and (Path(BATTLE_PACK_DIR) / 'section_014.bin').is_file():
            steam = section_rows(pack_sections(BATTLE_PACK, None)[T_ACTIONS])
            changed = [k for k, (a, b) in enumerate(zip(steam, self.listing.actions)) if a != b]
            self.assertTrue(changed, 'the loose action table differs from the pack')
            t = Tables()
            self.d.ILCT_MenuTablesCopy(ctypes.byref(t))
            for k in changed:
                row = self.listing.actions[k]
                self.assertEqual((t.actions[k].cost, t.actions[k].area, t.actions[k].targeting, t.actions[k].kind),
                                 (row[0x0A], row[0x06], row[0x0C], row[0x1E]), hex(k))

    def test_restrictions_on_the_real_tables(self):
        sh = shared(menu_flags=0x181, magicks=range(96), technicks=range(32), mist=range(31), items={0: 1})
        st = seat(licences=range(368), summary=range(184))
        for b in HARD_BITS:
            st.status = 1 << b
            c = self.build(sh, st)
            self.assertFalse(any(enabled(e) for e in pages_of(c)[PG_ROOT]), b)
        st.status = ST_BERSERK
        c = self.build(sh, st)
        self.assertEqual([enabled(e) for e in pages_of(c)[PG_ROOT]],
                         [e.role == ROLE_ATTACK for e in pages_of(c)[PG_ROOT]])


if __name__ == '__main__':
    unittest.main()
