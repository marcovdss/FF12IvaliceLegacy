"""Shared pieces of the session-core host tests: struct mirrors, the test DLL, and synthetic game memory.

No game run, nothing deployed. The "game" here is a synthetic memory image built in this process: a reserved
range the size of the game image (globals are committed page by page at their VA offsets) plus a heap of objects
(battle state, location and control blocks, the actor manager, actors, battle records). Pages can be made
unreadable on purpose, so the fault-safe reads are exercised for real.
"""
from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes

from coop_native_support import native_test  # noqa: F401  (re-exported for the test modules)
from test_coop_input_service import fresh_test_dll

u8, i8, u16, u32, i32, u64, i64 = (ctypes.c_uint8, ctypes.c_int8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int32,
                                   ctypes.c_uint64, ctypes.c_int64)
NONE = 0xFFFF
UNKNOWN, NO, YES = -1, 0, 1
IMAGE_VA = 0x120000
IMAGE_SIZE = 0x2F76000

# Game structure facts (the same numbers the module reads; see the module's game facts header).
BATTLE_PTR, CONTROL_PTR, COMMAND_ACTOR, COMMAND_FLAG = 0x2EBF190, 0x2099D70, 0x2092730, 0x2092734
LOCATION_PTR, AREA_TABLE_PTR, UI_MANAGER_PTR, ACTOR_MANAGER_PTR = 0x2092758, 0x2ADD100, 0x209BE80, 0x2098E18
FOLLOW_LIST_PRIMARY, FOLLOW_LIST_SECONDARY = 0x1E09350, 0x1EEC3C0
PARTY_CLASS, KO_CLASS, LEADER_CLASS = 0x00100000, 0x00200000, 0x00500000
ACTION_FOLLOW, ACTION_IDLE = 0x4124, 0x4021
ST_KO, ST_STONE, ST_STOP, ST_SLEEP, ST_CONFUSE = 1 << 0, 1 << 1, 1 << 3, 1 << 4, 1 << 5
ST_DISABLE, ST_IMMOBILIZE, ST_BERSERK, ST_XZONE = 1 << 13, 1 << 14, 1 << 27, 1 << 31


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


WorldSample = S('WorldSample', [('roots', u8), ('control_enabled', u8), ('menu_open', u8), ('modal_active', u8),
                                ('town', u8), ('chocobo', u8), ('location_read', u8), ('reserved0', u8),
                                ('leader', u32), ('reserved1', u32), ('battle_state', u64), ('current', u16 * 9),
                                ('ui', u16 * 9), ('location_current', u32), ('location_ui', u32),
                                ('reserved2', u8 * 4)])
PartyActor = S('PartyActor', [('index', u32), ('handle', u32), ('actor', u64), ('phys', u64)])
PartyActors = S('PartyActors', [('slot', PartyActor * 4), ('found', u32), ('scanned', u32), ('manager_ok', u8),
                                ('reserved', u8 * 7)])
Identity = S('Identity', [('member', u16 * 4), ('in_session', u8), ('reserved', u8 * 7)])
WorldView = S('WorldView', [('loading', u8), ('cutscene', u8), ('p1_gameplay', u8), ('gameplay', u8),
                            ('p1_is_leader', u8), ('p1_menu', u8), ('movement', u8), ('camera', u8),
                            ('guest_menu', u8 * 4), ('summon_seat', i8), ('leader_seat', i8), ('reason', u8),
                            ('signature_valid', u8), ('p1_member', u16), ('reserved', u16), ('location', u32),
                            ('battle_state', u64)])
SteerFacts = S('SteerFacts', [('enabled', u8), ('alive', i8), ('actor_present', u8), ('identity_current', u8),
                              ('layout_coherent', u8), ('allow_leader', u8), ('member', u16), ('leader', u32)])
Token = S('Token', [('seat', u8), ('reserved0', u8), ('member', u16), ('actor_generation', u32),
                    ('world_epoch', u32), ('handle', u32), ('actor', u64), ('phys', u64)])
Sample = S('Sample', [('of', Token), ('token', Token), ('sample', u64), ('available', u8), ('alive', i8), ('materialized', u8),
                      ('layout_coherent', u8), ('cutscene', u8), ('command_blocked', i8), ('follows_list', u8),
                      ('reserved0', u8), ('leader', u32), ('action_state', i32), ('target', i64), ('action', i32),
                      ('descriptor_action', i32)])
Intent = S('Intent', [('token', Token), ('created_ms', u64), ('deadline_ms', u64), ('next_attempt_ms', u64),
                      ('requested_sample', u64), ('attempts', u32), ('has_request', u8), ('waiting', u8),
                      ('reserved', u8 * 2)])
Ownership = S('Ownership', [('owner', Token * 4), ('has_owner', u8 * 4), ('intent_count', u32),
                            ('intent', Intent * 8)])
Effect = S('Effect', [('kind', u8), ('reason', u8), ('reserved', u8 * 6), ('token', Token)])
EffectList = S('EffectList', [('count', u32), ('dropped', u32), ('item', Effect * 48)])
WorldEpochs = S('WorldEpochs', [('epoch', u32), ('invalid_since', u8), ('has_signature', u8), ('reserved', u8 * 2),
                                ('sig_a', u64), ('sig_b', u64)])
GenEntry = S('GenEntry', [('actor', u64), ('phys', u64), ('handle', u32), ('generation', u32), ('next', u32),
                          ('epoch', u32), ('known', u8), ('present', u8), ('alive', i8), ('reserved', u8 * 5)])
Generations = S('Generations', [('e', GenEntry * 16)])
SeatEntry = S('SeatEntry', [('flags', u16), ('member', u16), ('party_slot', u8), ('reserved', u8 * 3),
                            ('handle', u32), ('actor_generation', u32), ('actor', u64), ('phys', u64)])
RecordHead = S('RecordHead', [('epoch', u32), ('flags', u32), ('generation', u64)])
Record = S('Record', [('head', RecordHead), ('world_epoch', u32), ('facts', u32), ('location', u32), ('leader', u16),
                      ('phase', u8), ('reason', u8), ('seat', SeatEntry * 4)])
SeatConfig = S('SeatConfig', [('character', u8 * 4), ('has_input', u8), ('parked', u8), ('enabled', u8),
                              ('reserved', u8)])
Lifecycle = S('Lifecycle', [('logical', u8), ('movement', u8), ('shared_camera', u8), ('reserved', u8)])
DebugView = S('DebugView', [('world', WorldSample), ('view', WorldView), ('configured', Identity), ('live', Identity),
                            ('life', Lifecycle), ('world_epoch', u32), ('session_epoch', u32), ('generation', u64),
                            ('effects_total', u32), ('phase', u32), ('party', PartyActors), ('owners', Ownership)])
Counters = S('Counters', [(n, u64) for n in ('steps', 'scans', 'epoch_bumps', 'generation_bumps',
                                             'identity_adoptions', 'acquires_refused', 'effects_dropped', 'reads',
                                             'read_failures')])
PendingEffect = S('PendingEffect', [('epoch', u32), ('reserved', u32), ('fx', Effect)])
EdgeCursor = S('EdgeCursor', [('run', u32), ('seq', u32 * 4)])
EdgeDelta = S('EdgeDelta', [('from_seq', u32), ('to_seq', u32), ('ticks', u32), ('pad_pressed', u32),
                            ('pad_released', u32), ('pad_held', u32), ('act_pressed', u32), ('act_released', u32),
                            ('act_repeated', u32), ('act_held', u32), ('overrun', u8), ('reseeded', u8),
                            ('reserved', u8 * 2)])
Reacquire = S('Reacquire', [('active', u8), ('pending', u8), ('reserved', u16), ('ready_samples', u32)])

# The order of ILCT_SesSizes.
LAYOUTS = [WorldSample, PartyActor, PartyActors, Identity, WorldView, SteerFacts, Token, Sample, Intent, Ownership,
           Effect, EffectList, WorldEpochs, Generations, SeatEntry, Record, SeatConfig, DebugView, Counters,
           PendingEffect, EdgeCursor, EdgeDelta, Reacquire, Lifecycle]

# Session constants (session.h).
PHASE = {'STOPPED': 0, 'NO_WORLD': 1, 'LOADING': 2, 'CUTSCENE': 3, 'MENU': 4, 'FIELD': 5, 'PARTY_DOWN': 6}
GATE = {'WORLD': 0x1, 'P1_GAMEPLAY': 0x2, 'GAMEPLAY': 0x4, 'MOVEMENT': 0x8, 'CAMERA': 0x10, 'P1_MENU': 0x20,
        'GUEST_MENU': 0x40, 'LOGICAL': 0x80, 'SHARED_CAMERA': 0x100, 'GUESTS_MOVE': 0x200, 'INTERACTION': 0x400,
        'PARTY_DOWN': 0x800}
FACT = {'TOWN': 0x1, 'CHOCOBO': 0x2, 'MENU': 0x4, 'MODAL': 0x8, 'CONTROL': 0x10, 'SUMMON': 0x20, 'SCAN_OK': 0x40}
SEATF = {'IN_SESSION': 0x1, 'MANUAL': 0x2, 'COHERENT': 0x4, 'ACTOR': 0x8, 'ALIVE': 0x10, 'ALIVE_KNOWN': 0x20,
         'WALKS': 0x40, 'STEERS': 0x80, 'LEADER': 0x100, 'MENU': 0x200, 'KO_PROMOTED': 0x400}
FX = {'RELEASE': 1, 'CLEAR': 2, 'ASK_FOLLOW': 3, 'ACQUIRE': 4, 'CONFIRMED': 5, 'ABANDONED': 6}
WHY = {'NONE': 0, 'TRANSFER': 1, 'WORLD': 2, 'GENERATION': 3, 'INELIGIBLE': 4, 'ALREADY_FOLLOWING': 5,
       'ACCEPTED': 6, 'BOUNDED': 7, 'ELIGIBLE': 8, 'SHUTDOWN': 9, 'BOUNDARY': 10}
CHAR_AUTO, CHAR_NONE = 0xFE, 0xFF

P = ctypes.POINTER
_SIGNATURES = {
    'ILCT_SesSizes': ([P(u32), u32], u32),
    'ILCT_SesLifecycle': ([i32, i32, P(i8), P(u8), u32], u32),
    'ILCT_SesSteers': ([P(SteerFacts)], i32),
    'ILCT_SesKeepMapGap': ([i32, i32, i32, u32, u32, u32, u32, u32, i32], i32),
    'ILCT_SesKeepStatusGap': ([i32, i32, i32, u32, u32, i32, u32, u32, u32, i32], i32),
    'ILCT_SesMemberInBoth': ([u32, P(u16), P(u16)], i32),
    'ILCT_SesCacheSurvives': ([i32, u32, P(u16), P(u16), i32], i32),
    'ILCT_SesGuestsMaster': ([i32, u32], i32),
    'ILCT_SesMovementControl': ([i32, i32, i32, i32], u32),
    'ILCT_SesCameraGate': ([i32, i32, i32, i32, i32], i32),
    'ILCT_SesCameraReacquire': ([i32, i32, P(Reacquire), i32, u32], None),
    'ILCT_SesPositionsReady': ([P(ctypes.c_float), u32], i32),
    'ILCT_SesPartyDown': ([P(i8), u32], i32),
    'ILCT_SesEvaluate': ([P(Identity), P(WorldSample), i32, P(WorldView)], None),
    'ILCT_SesWorldValid': ([P(WorldView)], i32),
    'ILCT_SesGuestMayMove': ([P(WorldView), P(Identity), P(WorldSample), u32], i32),
    'ILCT_SesP1MayMove': ([P(WorldView), P(WorldSample)], i32),
    'ILCT_SesResolveIdentity': ([P(Identity), P(u16), P(u16), u32, P(Identity)], i32),
    'ILCT_SesIdentityFromParty': ([P(Identity), P(WorldSample), P(Identity)], i32),
    'ILCT_SesP1Member': ([P(Identity), P(u16), P(u16), u32], u32),
    'ILCT_SesKoPromoted': ([P(Identity), u32, i32, i32], i32),
    'ILCT_SesSummonOwner': ([P(Identity), P(u16), P(u16), u32], i32),
    'ILCT_SesSummonedEsper': ([P(u16), P(u16)], u32),
    'ILCT_HoPlan': ([P(Ownership), P(Token), P(u8), P(u8), P(Sample), u32, u64, u32, P(EffectList)], None),
    'ILCT_HoAdvance': ([P(Ownership), P(Sample), u32, u64, u32, P(EffectList)], None),
    'ILCT_HoEnd': ([P(Ownership), u32, P(EffectList)], None),
    'ILCT_HoRefuse': ([P(Ownership), P(Token), i32, u64], None),
    'ILCT_HoSampleFlags': ([P(Sample)], u32),
    'ILCT_WorldEpochs': ([P(WorldEpochs), i32, u64, u64], u32),
    'ILCT_Generations': ([P(Generations), u32, u32, u64, u64, u32, u32, i32, i32, P(Token)], i32),
    'ILCT_SesSampleWorld': ([u64, P(WorldSample), P(u32)], u32),
    'ILCT_SesScanParty': ([u64, P(PartyActors), P(u32)], u32),
    'ILCT_SesActorInParty': ([u64, u64], i32),
    'ILCT_SesActorFacts': ([u64, u64, i32], i32),
    'ILCT_SesSlotHolds': ([u64, u32, P(PartyActor)], i32),
    'ILCT_SesReset': ([], None),
    'ILCT_SesStart': ([u64, u64], None),
    'ILCT_SesConfigure': ([P(SeatConfig)], None),
    'ILCT_SesSetParked': ([u32], None),
    'ILCT_SesStep': ([u64], None),
    'ILCT_SesStop': ([], None),
    'ILCT_SesRecord': ([P(Record)], i32),
    'ILCT_SesEpoch': ([], u32),
    'ILCT_SesGeneration': ([], u64),
    'ILCT_GtqGeneration': ([], u64),
    'ILCT_SesDebug': ([P(DebugView)], None),
    'ILCT_SesTakeEffects': ([P(PendingEffect), u32], u32),
    'ILCT_SesStats': ([P(Counters)], None),
    'ILCT_SesRecordSeq': ([], u32),
    'ILCT_EdgeTestReset': ([], None),
    'ILCT_EdgeReset': ([u32], None),
    'ILCT_EdgeRecord': ([u32, P(u32)], None),
    'ILCT_EdgeSince': ([P(EdgeCursor), u32, P(EdgeDelta)], i32),
    'ILCT_EdgeNewest': ([], u32),
    'ILCT_EdgeStress': ([u32, u32, u32, u32, u32, P(u32), u32, P(u32)], u32),
    'ILCT_GtqReap': ([], u32),
    'ILCT_SesReadScalar': ([u64, u32, P(u64), P(u32)], i32),
    'ILCT_ProbeTearDrill': ([u32, u64, u64, u32, u32], u32),
    'ILCT_SesForceMovementLive': ([i32], None),
    'ILCT_SesConfigureFromSettings': ([ctypes.c_void_p], None),
    'ILCT_GtqWorld': ([], u64),
    'ILCT_GtqAwait': ([u32, u64, u32, ctypes.c_void_p], i32),
    'ILCT_GtqHoldConsumer': ([i32], i32),
}


def session_dll():
    d = fresh_test_dll()
    for name, (args, res) in _SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def layout4(*members) -> ctypes.Array:
    values = list(members) + [NONE] * (9 - len(members))
    return (u16 * 9)(*values)


def identity(p1, *guests, in_session=None) -> Identity:
    id_ = Identity()
    members = [p1] + list(guests) + [NONE] * (3 - len(guests))
    for s in range(4):
        id_.member[s] = members[s] if s < len(members) else NONE
    if in_session is None:
        in_session = 1 | sum(1 << (k + 1) for k, g in enumerate(guests) if g != NONE)
    id_.in_session = in_session
    return id_


# ---- synthetic memory ----------------------------------------------------------------------------------------
_k32 = ctypes.WinDLL('kernel32', use_last_error=True)
_k32.VirtualAlloc.restype = ctypes.c_void_p
_k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
_k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
_k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
MEM_COMMIT, MEM_RESERVE, MEM_DECOMMIT, MEM_RELEASE = 0x1000, 0x2000, 0x4000, 0x8000
PAGE_NOACCESS, PAGE_READWRITE, PAGE_GUARD = 0x01, 0x04, 0x100
PAGE = 4096


class Image:
    """A reserved range standing in for the game image (globals committed on demand) plus an object heap."""

    HEAP = 8 << 20

    def __init__(self):
        self.base = _k32.VirtualAlloc(None, IMAGE_SIZE, MEM_RESERVE, PAGE_NOACCESS)
        self.heap = _k32.VirtualAlloc(None, self.HEAP, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)
        if not self.base or not self.heap:
            raise OSError('VirtualAlloc failed')
        self.top = self.heap
        self.committed: set[int] = set()

    def close(self):
        for address in (self.base, self.heap):
            if address:
                _k32.VirtualFree(address, 0, MEM_RELEASE)
        self.base = self.heap = 0

    def va(self, va: int) -> int:
        return self.base + va - IMAGE_VA

    def put_global(self, va: int, data: bytes):
        address = self.va(va)
        first, last = address & ~(PAGE - 1), (address + len(data) - 1) & ~(PAGE - 1)
        for page in range(first, last + PAGE, PAGE):
            if page not in self.committed:
                if not _k32.VirtualAlloc(page, PAGE, MEM_COMMIT, PAGE_READWRITE):
                    raise OSError('commit failed')
                self.committed.add(page)
        ctypes.memmove(address, data, len(data))

    def alloc(self, size: int, page: bool = False) -> int:
        align = PAGE if page else 16
        self.top = (self.top + align - 1) & ~(align - 1)
        address = self.top
        self.top += (size + PAGE - 1) & ~(PAGE - 1) if page else size
        if self.top > self.heap + self.HEAP:
            raise MemoryError('synthetic heap full')
        return address

    @staticmethod
    def put(address: int, data: bytes):
        ctypes.memmove(address, data, len(data))

    def p64(self, address, value):
        self.put(address, struct.pack('<Q', value))

    def p32(self, address, value):
        self.put(address, struct.pack('<I', value & 0xFFFFFFFF))

    def p16(self, address, value):
        self.put(address, struct.pack('<H', value & 0xFFFF))

    def p8(self, address, value):
        self.put(address, bytes([value & 0xFF]))

    @staticmethod
    def protect(address: int, size: int, protection: int):
        old = wintypes.DWORD()
        if not _k32.VirtualProtect(address & ~(PAGE - 1), size, protection, ctypes.byref(old)):
            raise OSError('VirtualProtect failed')


class Actor:
    """One actor with its manager entry, root, physics object and battle record, each in its own page so a test
    can make any of them unreadable."""

    def __init__(self, img: Image, index: int, slot: int, hp=100, status=0, handle_class=PARTY_CLASS,
                 position=(10.0, 2.0, 30.0)):
        self.img = img
        self.index = index
        self.entry = img.alloc(PAGE, page=True)
        self.actor = img.alloc(2 * PAGE, page=True)
        self.root = img.alloc(PAGE, page=True)
        self.phys = img.alloc(PAGE, page=True)
        self.battle = img.alloc(PAGE, page=True)
        self.descriptor = img.alloc(64)
        img.p64(self.entry + 0x30, self.actor)
        self.set_slot(slot)
        img.p32(self.actor + 0x08, handle_class | index)
        img.p64(self.actor + 0x10, self.root)
        img.p64(self.root + 0xB8, self.phys)
        img.p64(self.actor + 0x698, self.battle)
        self.set_position(position)
        self.set_hp(hp)
        self.set_status(status)
        # Idle, untargeted, attached to the primary follower list: ready to be asked to follow.
        img.p8(self.actor + 0x6B4, 1)
        img.p64(self.actor + 0x708, self.descriptor)
        img.p16(self.descriptor, ACTION_FOLLOW)
        img.p32(self.actor + 0x710, 0)
        img.p16(self.actor + 0x714, ACTION_IDLE)
        img.p64(self.actor + 0x6E8, img.va(FOLLOW_LIST_PRIMARY))
        img.p64(self.actor + 0x6F0, 0)

    def set_slot(self, slot):
        self.img.p8(self.entry + 0x12, slot)

    def set_handle(self, value):
        self.img.p32(self.actor + 0x08, value)

    def set_position(self, xyz, phys_xyz=None):
        self.img.put(self.actor + 0xD0, struct.pack('<3f', *xyz))
        self.img.put(self.phys, struct.pack('<3f', *(phys_xyz if phys_xyz is not None else xyz)))

    def set_hp(self, hp):
        self.img.p32(self.battle + 0x48, hp)

    def set_status(self, timed=0, lasting=0):
        self.img.p32(self.battle + 0x64, timed)
        self.img.p32(self.battle + 0x3C, lasting)

    def set_action(self, state=1, target=0, action=ACTION_IDLE, descriptor_action=ACTION_FOLLOW, listed=True):
        self.img.p8(self.actor + 0x6B4, state)
        self.img.p32(self.actor + 0x710, target)
        self.img.p16(self.actor + 0x714, action)
        self.img.p16(self.descriptor, descriptor_action)
        self.img.p64(self.actor + 0x6E8, self.img.va(FOLLOW_LIST_PRIMARY) if listed else 0)


class GameWorld:
    """Battle state, location, control, menu, modal, area table and actor manager in a synthetic image."""

    MANAGER_SLOTS = 64

    def __init__(self, img: Image):
        self.img = img
        self.battle = img.alloc(0x6000, page=True)
        self.location = img.alloc(0x2000, page=True)
        self.control = img.alloc(PAGE, page=True)
        self.ui = img.alloc(0x12000, page=True)
        self.modal_object = img.alloc(0x2000, page=True)
        self.areas = img.alloc(0x2000, page=True)
        self.manager = img.alloc(8 + 8 * self.MANAGER_SLOTS, page=True)
        self.actors: dict[int, Actor] = {}
        img.put_global(COMMAND_ACTOR, struct.pack('<II', 0, 0))
        img.put_global(UI_MANAGER_PTR, struct.pack('<Q', self.ui))
        img.p64(self.ui + 0x10F80, self.modal_object)
        img.put_global(AREA_TABLE_PTR, struct.pack('<Q', self.areas))
        img.p32(self.areas + 4, 0x100)
        img.put_global(ACTOR_MANAGER_PTR, struct.pack('<Q', self.manager))
        img.put_global(FOLLOW_LIST_PRIMARY, bytes(16))
        img.put_global(FOLLOW_LIST_SECONDARY, bytes(16))
        self.roots(True)
        self.layout([], [])
        self.leader(NONE)
        self.place(100, 100)
        self.control_enabled(True)

    # ---- world flags ----
    def roots(self, present: bool):
        self.img.put_global(BATTLE_PTR, struct.pack('<Q', self.battle if present else 0))
        self.img.put_global(LOCATION_PTR, struct.pack('<Q', self.location if present else 0))
        self.img.put_global(CONTROL_PTR, struct.pack('<Q', self.control if present else 0))

    def layout(self, current, ui=None):
        ui = current if ui is None else ui
        for which, members in ((1, current), (2, ui)):
            values = list(members) + [NONE] * (9 - len(members))
            self.img.put(self.battle + 0x5A48 + which * 0x12, struct.pack('<9H', *values))

    def leader(self, member):
        self.img.p32(self.battle + 0x5AA4, member)

    def place(self, current, ui=None):
        self.img.p32(self.location + 0x1244, current)
        self.img.p32(self.location + 0x125C, current if ui is None else ui)

    def control_enabled(self, on: bool):
        self.img.p8(self.control + 0x14, 1 if on else 0)

    def menu(self, open_: bool):
        self.img.put_global(COMMAND_ACTOR, struct.pack('<II', 0x00200013 if open_ else 0, 1 if open_ else 0))

    def modal(self, on: bool):
        self.img.p32(self.ui + 0x10F68, 0x40 if on else 0)
        self.img.p16(self.modal_object + 0x1D7A, 0x08 if on else 0)

    def town_flag(self, on: bool):
        self.img.p8(self.location + 0x1262, 1 if on else 0)

    def area_town(self, area: int, on: bool):
        self.img.p8(self.areas + 0x0A + area * 3, 1 if on else 0)

    def chocobo(self, on: bool):
        self.img.p32(self.battle + 0x5B04, 0x02 if on else 0)

    # ---- actors ----
    def add_actor(self, index: int, slot: int, **kw) -> Actor:
        a = Actor(self.img, index, slot, **kw)
        self.actors[index] = a
        self.img.p64(self.manager + 8 + index * 8, a.entry)
        self.count(max(self.actors) + 1)
        return a

    def remove_actor(self, index: int):
        self.actors.pop(index)
        self.img.p64(self.manager + 8 + index * 8, 0)

    def count(self, n: int):
        self.img.p32(self.manager, n)

    def manager_pointer(self, present: bool):
        self.img.put_global(ACTOR_MANAGER_PTR, struct.pack('<Q', self.manager if present else 0))

    def party(self, members_by_slot: list[int], hp=None, base_index=0) -> list[Actor]:
        """A field party: layouts, leader = slot 0, one actor per slot (manager index base_index + slot)."""
        self.layout(members_by_slot)
        self.leader(members_by_slot[0])
        out = []
        for slot, _member in enumerate(members_by_slot):
            out.append(self.add_actor(base_index + slot, slot, hp=100 if hp is None else hp[slot],
                                      position=(10.0 + slot, 2.0, 30.0)))
        return out
