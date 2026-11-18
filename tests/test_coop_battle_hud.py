"""Host tests for the Battle HUD settings: the schema fields, the Identify binding, the native wiring of every element
switch (native/coop/src/hud/battle_hud.*), the page's report, and the Mod Menu's Battle HUD page (page.lua, store.lua).
No game run, nothing deployed.

* schema: IDENTIFY is an action P1 may bind; the defaults give each guest a key and a pad control no other action of
  that seat uses (and nothing that reaches the game: guest input is isolated), P1 none; the HUD fields and their
  defaults (Battle HUD on, every element on, log All, no expand button);
* native: each element bit sets exactly its own switch (and nothing else); Battle HUD off turns every one off; the log
  fields pass through and the expanded state survives a new record; Identify presses reach the diamonds only while
  they are on; the frame-hook call bytes are classified (stock, ours, Vision's NOPs, foreign) into the report;
* Lua: the store keeps the Battle HUD state in coop.json and in the record both ways, repairs bad values one by one;
  the page has every row, the status line follows the report and Insurgent's Vision's file (read only), and a toggle
  changes only its own element.
"""
from __future__ import annotations

import ctypes
import json
import re
import unittest
from pathlib import Path

from coop_native_support import SCHEMA, native_test
from test_coop_input_service import fresh_test_dll
from coop_page_support import PageEnv

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
SRC = COOP / 'src'
LUA = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'
ENTRY = ROOT / 'package/x64/scripts/IvaliceLegacyCoop.lua'

S = SCHEMA
ACT = S.enums['action']
BK = S.enums['binding_kind']
PADC = S.enums['pad_control']
HUD = S.enums['hud_element']
REP = S.enums['hud_report']
LF = S.enums['log_filter']
ALL = sum(HUD.values())

u32, u64, i32 = ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

# native::part, party_rows::part (restated from the headers; checked below)
N_SHRINK, N_CIRCLES = 0x01, 0x02
PR_EDGE, PR_GLOW, PR_GHOST, PR_ACTION, PR_TIMERS, PR_CURSORS = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
CALL_UNREADABLE, CALL_STOCK, CALL_OTHER, CALL_NOPS, CALL_FOREIGN = range(5)
LAYOUT_REDUCED, LAYOUT_FOREIGN = 4, 6
SITE_STOCK, SITE_OURS, SITE_VISION, SITE_FOREIGN = 1, 2, 3, 4

SWITCH = ('native', 'native_parts', 'rows', 'rows_parts', 'ti', 'log', 'log_button', 'log_filter', 'log_seat', 'lines',
          'diamonds', 'rings', 'log_expanded')
# element -> the switch words it changes when it alone goes off (from the defaults)
ALONE_OFF = {
    'PARTY_SHRINK': {'native_parts': N_CIRCLES},
    'CIRCLES_OFF': {'native_parts': N_SHRINK},
    'SEAT_EDGE': {'rows_parts': 0x3F & ~PR_EDGE},
    'LOW_HP_GLOW': {'rows_parts': 0x3F & ~PR_GLOW},
    'DAMAGE_GHOST': {'rows_parts': 0x3F & ~PR_GHOST},
    'ACTION_LINE': {'rows_parts': 0x3F & ~PR_ACTION},
    'TARGET_INFO': {'ti': 0},
    'BATTLE_LOG': {'log': 0},
    'SEAT_LINES': {'lines': 0},
    'SEAT_DIAMONDS': {'diamonds': 0},
    'AOE_RING': {'rings': 0},
    'PARTY_TIMERS': {'rows_parts': 0x3F & ~PR_TIMERS},
    'GUEST_CURSORS': {'rows_parts': 0x3F & ~PR_CURSORS},
}


def defaults_record() -> dict:
    return S.decode('ilc_settings', S.defaults())


def seat_bindings(rec, seat) -> list[tuple[int, int, int]]:
    out = []
    for i, b in enumerate(rec['seats'][seat - 1]['bindings']):
        if b['kind']:
            out.append((i // 4, b['kind'], b['code']))
    return out


# ---- schema and defaults (static) -------------------------------------------------------------------------------------
class SchemaTest(unittest.TestCase):
    def test_identify_is_an_action_p1_may_bind(self):
        self.assertEqual(ACT['IDENTIFY'], 19)
        self.assertEqual(S.const['ACTIONS'], 20)
        self.assertEqual(S.const['SEAT_BINDINGS'], 80)
        self.assertTrue(S.const['P1_ACTION_MASK'] & (1 << ACT['IDENTIFY']))

    def test_identify_defaults_conflict_with_nothing(self):
        rec = defaults_record()
        self.assertEqual([b for b in seat_bindings(rec, 1) if b[0] == ACT['IDENTIFY']], [],
                         'P1: no default (each button of P1\'s pad also reaches the game)')
        for seat in (2, 3, 4):
            with self.subTest(seat=seat):
                rows = seat_bindings(rec, seat)
                mine = {(k, c) for a, k, c in rows if a == ACT['IDENTIFY']}
                self.assertEqual(mine, {(BK['KEY'], ord('T')), (BK['PAD'], PADC['BACK'])})
                others = {(k, c) for a, k, c in rows if a != ACT['IDENTIFY']}
                self.assertFalse(mine & others, 'no other action of the seat uses the same key or control')

    def test_no_game_or_menu_path_reads_identify(self):
        """The firewall, the P1 parity pulses, the menus, the frontend gate and the isolation never see IDENTIFY."""
        readers = set()
        for p in SRC.rglob('*.cpp'):
            if 'ILC_ACTION_IDENTIFY' in p.read_text(encoding='utf-8'):
                readers.add(p.relative_to(SRC).as_posix())
        self.assertEqual(readers, {'hud/battle_hud.cpp'})

    def test_hud_fields_and_defaults(self):
        rec = defaults_record()
        self.assertEqual(rec['hud_enabled'], 1)
        self.assertEqual(rec['hud_elements'], ALL & ~HUD['PARTY_TIMERS'], 'the default is the Standard preset')
        self.assertEqual(rec['log_filter'], LF['ALL'])
        self.assertEqual(rec['log_filter_seat'], 1)
        self.assertEqual(rec['log_expand_control'], 0)
        self.assertEqual(ALL, 0x1FFF)
        self.assertEqual(set(LF), {'ALL', 'PARTY', 'FOES', 'STATUSES', 'MINE'})
        fields = {f['name']: f for f in S.json['structs']['ilc_settings']['fields']}
        self.assertEqual((fields['log_filter_seat']['min'], fields['log_filter_seat']['max']), (1, S.const['ACTIVE_SEATS_MAX']))
        self.assertEqual(fields['log_expand_control']['max'], max(PADC.values()))
        self.assertEqual(fields['hud_elements']['max'], ALL)

    def test_parts_restated(self):
        n = (SRC / 'hud/native.h').read_text(encoding='utf-8')
        self.assertIn('PART_SHRINK = 0x01, PART_CIRCLES = 0x02', n)
        pr = (SRC / 'hud/party_rows.h').read_text(encoding='utf-8')
        for name, v in (('EDGE', PR_EDGE), ('GLOW', PR_GLOW), ('GHOST', PR_GHOST), ('ACTION', PR_ACTION),
                        ('TIMERS', PR_TIMERS), ('CURSORS', PR_CURSORS)):
            self.assertRegex(pr, rf'PART_{name} = 0x{v:02X},')

    def test_wiring(self):
        boot = (SRC / 'boot/boot.cpp').read_text(encoding='utf-8')
        self.assertEqual(boot.count('hud::battle_hud::configure_from_settings('), 2)
        for block in re.findall(r'hud::rings::configure_from_settings\((\w+)\);\s+hud::battle_hud::configure_from_settings\((\w+)\)',
                                boot):
            self.assertEqual(block[0], block[1])
        self.assertEqual(len(re.findall(r'hud::rings::configure_from_settings\(\w+\);\s+hud::battle_hud::configure', boot)), 2,
                         'after every element configured itself: the Battle HUD sets their switches last')
        self.assertLess(boot.index('hud::battle_hud::step(now)'), boot.index('hud::world_marks::step(now)'),
                        'Identify reaches the diamonds in the same beat')
        self.assertIn('hud::battle_hud::attach(g_host.image_base)', boot)
        self.assertIn('hud::battle_hud::stop()', boot)
        bridge = (SRC / 'bridge/bridge.cpp').read_text(encoding='utf-8')
        self.assertIn('st.hud_report = hud::battle_hud::report();', bridge)

    def test_game_thread_free_and_no_blocking(self):
        src = (SRC / 'hud/battle_hud.cpp').read_text(encoding='utf-8')
        for word in ('Sleep(', 'WaitFor', 'malloc', 'new ', 'EnterCriticalSection', 'memcpy'):
            self.assertNotIn(word, src)


# ---- native ------------------------------------------------------------------------------------------------------------
class Counters(ctypes.Structure):
    _fields_ = [('configured', u64), ('identifies', u64), ('identify_off', u64), ('reports', u64)]


class EdgeCursor(ctypes.Structure):
    _fields_ = [('run', u32), ('seq', u32 * 4)]


def declare(d):
    sig = {
        'ILCT_BhDefaults': ([ctypes.c_void_p], None),
        'ILCT_BhEffective': ([ctypes.c_char_p], u32),
        'ILCT_BhConfigure': ([ctypes.c_char_p], None),
        'ILCT_BhElements': ([], u32),
        'ILCT_BhSwitches': ([P(u32), u32], u32),
        'ILCT_BhReset': ([], None),
        'ILCT_BhToggleExpanded': ([], None),
        'ILCT_BhClassify': ([ctypes.c_char_p, u64, u64], u32),
        'ILCT_BhReportFrom': ([u32, i32, u32, u32, i32], u32),
        'ILCT_BhAttach': ([u64], None),
        'ILCT_BhSetCallBytes': ([ctypes.c_char_p], None),
        'ILCT_BhReportNow': ([], None),
        'ILCT_BhReport': ([], u32),
        'ILCT_BhStep': ([u64], None),
        'ILCT_BhStop': ([], None),
        'ILCT_BhIdentifyPending': ([], u32),
        'ILCT_BhStats': ([P(Counters)], None),
        'ILCT_EdgeTestReset': ([], None),
        'ILCT_EdgeReset': ([u32], None),
        'ILCT_EdgeRecord': ([u32, P(u32)], None),
    }
    for name, (args, res) in sig.items():
        f = getattr(d, name)
        f.argtypes = args
        f.restype = res
    return d


@native_test
class NativeSwitchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def setUp(self):
        self.d.ILCT_BhReset()

    def record(self, **fields) -> bytes:
        rec = defaults_record()
        rec['header']['generation'] = 1
        rec.update(fields)
        return bytes(S.encode('ilc_settings', rec))

    def switches(self) -> dict:
        out = (u32 * 13)()
        self.assertEqual(self.d.ILCT_BhSwitches(out, 13), 13)
        return dict(zip(SWITCH, list(out)))

    def configure(self, **fields) -> dict:
        self.d.ILCT_BhConfigure(self.record(**fields))
        return self.switches()

    def test_defaults_turn_on_the_standard_elements(self):
        """The default record is the Mod Menu's Standard preset: every element but the status timers."""
        native = (ctypes.c_uint8 * S.size('ilc_settings'))()
        self.d.ILCT_BhDefaults(native)
        self.assertEqual(bytes(native), S.defaults(), 'the native defaults are the schema defaults')
        sw = self.configure()
        self.assertEqual(sw, {'native': 1, 'native_parts': N_SHRINK | N_CIRCLES, 'rows': 1, 'rows_parts': 0x3F & ~PR_TIMERS,
                              'ti': 1, 'log': 1, 'log_button': 0, 'log_filter': 0, 'log_seat': 1, 'lines': 1,
                              'diamonds': 1, 'rings': 1, 'log_expanded': 0})
        self.assertEqual(self.d.ILCT_BhElements(), ALL & ~HUD['PARTY_TIMERS'])
        sw = self.configure(hud_elements=ALL)
        self.assertEqual((sw['rows_parts'], self.d.ILCT_BhElements()), (0x3F, ALL), 'Full: every element')

    def test_battle_hud_off_turns_every_element_off(self):
        sw = self.configure(hud_enabled=0)
        for k in ('native', 'native_parts', 'rows', 'ti', 'log', 'lines', 'diamonds', 'rings'):
            self.assertEqual(sw[k], 0, k)
        self.assertEqual(self.d.ILCT_BhElements(), 0)
        self.assertEqual(self.d.ILCT_BhEffective(self.record(hud_enabled=0)), 0)
        on = self.configure()
        self.assertEqual((on['native'], on['rows'], on['rings']), (1, 1, 1), 'and back on with the next record')

    def test_each_element_turns_off_only_its_own_switch(self):
        base = self.configure(hud_elements=ALL)
        self.assertEqual(set(ALONE_OFF), set(HUD), 'every element has a row here')
        for name, bit in HUD.items():
            with self.subTest(element=name):
                sw = self.configure(hud_elements=ALL & ~bit)
                changed = {k: v for k, v in sw.items() if base[k] != v}
                self.assertEqual(changed, ALONE_OFF[name])
                self.assertEqual(self.d.ILCT_BhElements(), ALL & ~bit)

    def test_party_timers_and_hands_follow_their_own_elements(self):
        """0.1.53: the status timers and the guests' party-target hands are elements (PARTY_TIMERS, GUEST_CURSORS),
        no longer hard-wired on; the party rows themselves stay on while the HUD is on."""
        sw = self.configure(hud_elements=0)
        self.assertEqual(sw['rows_parts'], 0)
        self.assertEqual((sw['rows'], sw['native'], sw['native_parts']), (1, 1, 0))
        sw = self.configure(hud_elements=HUD['PARTY_TIMERS'])
        self.assertEqual(sw['rows_parts'], PR_TIMERS)
        sw = self.configure(hud_elements=HUD['GUEST_CURSORS'])
        self.assertEqual(sw['rows_parts'], PR_CURSORS)
        sw = self.configure(hud_enabled=0)
        self.assertEqual((sw['rows'], sw['rows_parts']), (0, 0), 'Battle HUD off: every element off')

    def test_log_fields_pass_through_and_the_expanded_state_is_kept(self):
        sw = self.configure(log_expand_control=PADC['R3'], log_filter=LF['MINE'], log_filter_seat=3)
        self.assertEqual((sw['log_button'], sw['log_filter'], sw['log_seat']), (PADC['R3'], LF['MINE'], 3))
        self.d.ILCT_BhToggleExpanded()
        self.assertEqual(self.switches()['log_expanded'], 1, 'instrument: the button toggled it')
        sw = self.configure(log_filter=LF['FOES'])
        self.assertEqual((sw['log_expanded'], sw['log_filter'], sw['log_button']), (1, LF['FOES'], 0),
                         'a new record keeps what the button did')

    def test_effective_elements_drop_unknown_bits(self):
        rec = bytearray(self.record())
        off = next(f['offset'] for f in S.json['structs']['ilc_settings']['fields'] if f['name'] == 'hud_elements')
        rec[off:off + 4] = (0xFFFFFFFF).to_bytes(4, 'little')
        self.assertEqual(self.d.ILCT_BhEffective(bytes(rec)), ALL)

    # ---- Identify ----
    def press(self, seq, seat_actions: dict):
        masks = (u32 * 28)()
        for seat, pressed in seat_actions.items():
            masks[(seat - 1) * 7 + 3] = pressed
        self.d.ILCT_EdgeRecord(seq, masks)

    def test_identify_reaches_the_seat_diamond(self):
        self.d.ILCT_EdgeTestReset()
        self.d.ILCT_EdgeReset(7)
        self.configure()
        self.press(1, {})
        self.d.ILCT_BhStep(1000)      # the cursor's first use seeds it: nothing
        self.assertEqual(self.d.ILCT_BhIdentifyPending(), 0)
        self.press(2, {2: 1 << ACT['IDENTIFY']})
        self.press(3, {3: 1 << ACT['CONFIRM']})
        self.d.ILCT_BhStep(1020)
        self.assertEqual(self.d.ILCT_BhIdentifyPending(), 0b0010, 'seat 2 (bit 1); a CONFIRM is no Identify')
        c = Counters()
        self.d.ILCT_BhStats(ctypes.byref(c))
        self.assertEqual((c.identifies, c.identify_off), (1, 0))

    def test_identify_does_nothing_with_the_diamonds_off(self):
        self.d.ILCT_EdgeTestReset()
        self.d.ILCT_EdgeReset(9)
        self.configure(hud_elements=ALL & ~HUD['SEAT_DIAMONDS'])
        self.press(1, {})
        self.d.ILCT_BhStep(1000)
        self.press(2, {1: 1 << ACT['IDENTIFY'], 2: 1 << ACT['IDENTIFY']})
        self.d.ILCT_BhStep(1020)
        self.assertEqual(self.d.ILCT_BhIdentifyPending(), 0)
        c = Counters()
        self.d.ILCT_BhStats(ctypes.byref(c))
        self.assertEqual((c.identifies, c.identify_off), (0, 2))

    # ---- report ----
    def test_call_classes(self):
        site, draw = 0x1402BE7DA, 0x1402BF230
        rel = (draw - (site + 5)) & 0xFFFFFFFF
        stock = bytes([0xE8]) + rel.to_bytes(4, 'little')
        self.assertEqual(stock, bytes([0xE8, 0x51, 0x0A, 0x00, 0x00]), 'the game\'s own bytes at 0x2BE7DA')
        cases = [(stock, CALL_STOCK), (bytes([0xE8, 0x10, 0x20, 0x30, 0x40]), CALL_OTHER),
                 (b'\x90' * 5, CALL_NOPS), (bytes([0x0F, 0x1F, 0x44, 0x00, 0x00]), CALL_NOPS),
                 (bytes([0x66, 0x90, 0x0F, 0x1F, 0x00]), CALL_NOPS), (bytes([0x0F, 0x1F, 0x40, 0x00, 0x90]), CALL_NOPS),
                 (bytes([0x90, 0x90, 0x90, 0x90, 0xCC]), CALL_FOREIGN), (bytes([0xE9, 0x51, 0x0A, 0x00, 0x00]), CALL_FOREIGN),
                 (bytes([0x0F, 0x1F, 0x44, 0x00, 0x01]), CALL_FOREIGN)]
        for raw, want in cases:
            with self.subTest(raw=raw.hex()):
                self.assertEqual(self.d.ILCT_BhClassify(raw, site, draw), want)

    def test_report_table(self):
        V, F, L = REP['VISION_NO_COMBAT_LOG'], REP['FRAME_HOOK_OFF'], REP['PARTY_LAYOUT_FOREIGN']
        cases = [((CALL_OTHER, 1, LAYOUT_REDUCED, SITE_OURS), 0),
                 ((CALL_NOPS, 0, LAYOUT_REDUCED, SITE_OURS), V | F),
                 ((CALL_NOPS, 1, LAYOUT_REDUCED, SITE_OURS), V | F),
                 ((CALL_STOCK, 0, LAYOUT_REDUCED, SITE_OURS), F),
                 ((CALL_OTHER, 0, LAYOUT_REDUCED, SITE_OURS), F),
                 ((CALL_FOREIGN, 1, LAYOUT_REDUCED, SITE_OURS), F),
                 ((CALL_UNREADABLE, 0, LAYOUT_REDUCED, SITE_OURS), 0),
                 ((CALL_OTHER, 1, LAYOUT_FOREIGN, SITE_OURS), L),
                 ((CALL_OTHER, 1, LAYOUT_REDUCED, SITE_VISION), REP['CIRCLES_BY_VISION']),
                 ((CALL_OTHER, 1, LAYOUT_REDUCED, SITE_FOREIGN), REP['CIRCLES_FOREIGN']),
                 ((CALL_OTHER, 1, LAYOUT_REDUCED, SITE_STOCK), 0)]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(self.d.ILCT_BhReportFrom(*args, 1), want)
        # Hooked but not drawing (RENDER off or faulted, or the hook not attached): RENDER_OFF, never with FRAME_HOOK_OFF.
        R = REP['RENDER_OFF']
        for args, want in [((CALL_OTHER, 1, LAYOUT_REDUCED, SITE_OURS), R),
                           ((CALL_UNREADABLE, 0, LAYOUT_REDUCED, SITE_OURS), R),
                           ((CALL_STOCK, 0, LAYOUT_REDUCED, SITE_OURS), F),
                           ((CALL_NOPS, 1, LAYOUT_REDUCED, SITE_OURS), V | F),
                           ((CALL_OTHER, 1, LAYOUT_FOREIGN, SITE_VISION), R | L | REP['CIRCLES_BY_VISION'])]:
            with self.subTest(args=args, drawing=0):
                self.assertEqual(self.d.ILCT_BhReportFrom(*args, 0), want)

    def test_report_follows_the_bytes_and_is_quiet_before_attach(self):
        self.d.ILCT_BhSetCallBytes(b'\x90' * 5)
        self.d.ILCT_BhReportNow()
        self.assertEqual(self.d.ILCT_BhReport(), 0, 'not attached: nothing to say')
        self.d.ILCT_BhAttach(0x140000000)
        self.d.ILCT_BhReportNow()
        self.assertEqual(self.d.ILCT_BhReport() & (REP['VISION_NO_COMBAT_LOG'] | REP['FRAME_HOOK_OFF']),
                         REP['VISION_NO_COMBAT_LOG'] | REP['FRAME_HOOK_OFF'])
        self.d.ILCT_BhSetCallBytes(bytes([0xE8, 0x51, 0x0A, 0x00, 0x00]))
        self.d.ILCT_BhStep(5000)   # the first step refreshes at once
        self.assertEqual(self.d.ILCT_BhReport() & (REP['VISION_NO_COMBAT_LOG'] | REP['FRAME_HOOK_OFF']), REP['FRAME_HOOK_OFF'],
                         'stock call, HD-01 not hooked here: the frame hook is off, Vision is not the cause')
        self.d.ILCT_BhSetCallBytes(None)


# ---- Lua ---------------------------------------------------------------------------------------------------------------
def lua_env():
    lua = LuaRuntime(unpack_returned_tuples=True)
    load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
    schema = load((LUA / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
    C = load((LUA / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
    return lua, load, C


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class StoreTest(unittest.TestCase):
    def setUp(self):
        self.lua, load, self.C = lua_env()
        self.saved = {}
        config = self.lua.eval('''function(saved) return {
          loadJson = function(p) return saved[p] end,
          saveJson = function(p, d) saved[p] = d; return true end} end''')(self.lua.table())
        self.config = config
        self.mod = load((LUA / 'store.lua').read_text(encoding='utf-8'), 'store')
        self.st = self.mod.create(self.lua.table_from({'codec': self.C, 'config': config}))
        self.st.load()

    def test_defaults(self):
        h = self.st.state.battleHud
        self.assertIs(h.enabled, True)
        for k in self.mod.HUD_ELEMENTS.values():
            self.assertIs(h.elements[k], k != 'partyTimers', k)   # the Standard preset: every element but the timers
        self.assertEqual((h.log.expandButton, h.log.filter, h.log.filterSeat), ('NONE', 'ALL', 1))
        self.assertEqual(set(self.mod.HUD_ELEMENT_BIT.values()), set(HUD), 'one key per schema element')
        self.assertEqual(len(self.mod.HUD_ELEMENTS), len(HUD))

    def test_record_round_trip(self):
        st = self.st.copy(self.st.state)
        st.battleHud.enabled = False
        st.battleHud.elements.seatLines = False
        st.battleHud.elements.circlesOff = False
        st.battleHud.log.expandButton = 'R3'
        st.battleHud.log.filter = 'MINE'
        st.battleHud.log.filterSeat = 2
        back = self.lua.eval('''function(C, T, st)
            local r = C.decode('ilc_settings', T.encode(st, 5))
            return {hud_enabled = r.hud_enabled, hud_elements = r.hud_elements, log_expand_control = r.log_expand_control,
                    log_filter = r.log_filter, log_filter_seat = r.log_filter_seat}
          end''')(self.C, self.st, st)
        rec = {k: back[k] for k in ('hud_enabled', 'hud_elements', 'log_expand_control', 'log_filter', 'log_filter_seat')}
        self.assertEqual(rec['hud_enabled'], 0)
        # from the default state (the Standard preset: no status timers)
        self.assertEqual(rec['hud_elements'], ALL & ~HUD['PARTY_TIMERS'] & ~HUD['SEAT_LINES'] & ~HUD['CIRCLES_OFF'])
        self.assertEqual((rec['log_expand_control'], rec['log_filter'], rec['log_filter_seat']), (PADC['R3'], LF['MINE'], 2))
        ok = self.st.check(st)
        self.assertTrue(ok)

    def test_bad_values_fall_back_one_by_one(self):
        doc = self.lua.eval('''function() return {schema = 1, battleHud = {enabled = 'yes',
            elements = {seatLines = false, lowHpGlow = 3}, log = {expandButton = 'XYZ', filter = 'FOES', filterSeat = 5}}} end''')()
        self.config.loadJson = self.lua.eval('function(d) return function() return d end end')(doc)
        st = self.mod.create(self.lua.table_from({'codec': self.C, 'config': self.config}))
        st.load()
        h = st.state.battleHud
        self.assertIs(h.enabled, True, 'not a boolean: default')
        self.assertIs(h.elements.seatLines, False, 'kept')
        self.assertIs(h.elements.lowHpGlow, True, 'not a boolean: default')
        self.assertEqual((h.log.expandButton, h.log.filter, h.log.filterSeat), ('NONE', 'FOES', 1))


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class PageTest(unittest.TestCase):
    """The Battle HUD part of the page (HUD > status, preset, Elements, Battle log, colours) with the real modules, the
    enforcing Mod Menu double and a Vision settings table."""

    def setUp(self):
        self.env = PageEnv()
        self.P, self.store, self.page_mod = self.env.P, self.env.store, self.env.page_mod

    def elements(self):
        return {k: self.store.state.battleHud.elements[k] for k in self.store.HUD_ELEMENTS.values()}

    def native_hud_bits(self):
        decode = self.env.lua.eval(
            "function(C, T) local r = C.decode('ilc_settings', T.encode(T.state, 1)); return r.hud_elements end")
        return decode(self.env.C, self.store)

    def test_every_row(self):
        env = self.env
        self.assertEqual(env.sub_ids('hud'), ['hudStatus', 'hudPreset', 'hudTheme', 'hudElements', 'hudLog', 'colourMode'])
        self.assertEqual(env.sub_ids('hudElements'), ['hud_partyTimers', 'hud_guestCursors', 'hud_seatLines',
                                                      'hud_seatDiamonds', 'hud_battleLog', 'hud_targetInfo'])
        self.assertEqual(env.sub_ids('hudLog'), ['hudLogCompact', 'hudLogExpanded', 'hudLogFade', 'hudLogExpand',
                                                 'hudLogFilter', 'hudLogSeat'])
        for gone in ('hudOn', 'hudIdentify1', 'visionMinimap', 'visionOverheadHp', 'visionCombatLog', 'hud_circlesOff',
                     'hud_aoeRing', 'hud_partyShrink', 'hudDivider_party'):
            self.assertIsNone(env.row(gone), gone)
        self.assertIsNotNone(env.row('hudTestDraw'), 'the test draw lives under Advanced > Developer')
        self.assertIn('hud', list(self.page_mod.SETTINGS_ROWS.values()), 'hidden with the other settings pages')

    def test_the_presets_set_the_element_bits(self):
        P, mod = self.P, self.page_mod
        self.assertEqual(list(mod.HUD_PRESET_LABELS.values()), ['Off', 'Light', 'Standard', 'Full', 'Custom'])
        self.assertEqual(P.presetIndex(), 2, 'the shipped default (every element but the timers) reads as Standard')
        sets = {n: set(mod.HUD_PRESETS[n].values()) for n in ('Light', 'Standard', 'Full')}
        self.assertEqual(sets['Full'], set(self.store.HUD_ELEMENTS.values()), 'Full is every element')
        self.assertLess(sets['Light'], sets['Standard'])
        self.assertLess(sets['Standard'], sets['Full'])
        for n, keep in sets.items():
            self.assertIn('circlesOff', keep, "the game's target circles stay hidden in " + n)
            self.assertIn('aoeRing', keep, 'the area ring while aiming stays in ' + n)
        self.assertEqual(sets['Full'] - sets['Standard'], {'partyTimers'})
        self.assertEqual(sets['Standard'] - sets['Light'],
                         {'damageGhost', 'actionLine', 'seatLines', 'seatDiamonds', 'targetInfo', 'battleLog'})
        for index, n in ((1, 'Light'), (2, 'Standard'), (3, 'Full')):
            P.onChange('IvaliceLegacyCoop', 'hudPreset', index)
            self.assertEqual({k for k, v in self.elements().items() if v}, sets[n], n)
            self.assertIs(self.store.state.battleHud.enabled, True)
            self.assertEqual(P.presetIndex(), index)
            self.assertEqual(self.native_hud_bits(), sum(HUD[self.store.HUD_ELEMENT_BIT[k]] for k in sets[n]), n)
        P.onChange('IvaliceLegacyCoop', 'hudPreset', 0)
        self.assertIs(self.store.state.battleHud.enabled, False)
        self.assertEqual(P.presetIndex(), 0)
        self.assertEqual({k for k, v in self.elements().items() if v}, sets['Full'], 'Off leaves the elements alone')
        P.onChange('IvaliceLegacyCoop', 'hudPreset', 2)
        self.assertEqual(P.presetIndex(), 2)

    def test_editing_an_element_shows_custom_and_the_preset_comes_back(self):
        P = self.P
        P.onChange('IvaliceLegacyCoop', 'hudPreset', 2)
        self.assertEqual(P.presetIndex(), 2)
        P.onChange('IvaliceLegacyCoop', 'hud_seatDiamonds', 0)
        self.assertEqual(P.presetIndex(), 4, 'Custom')
        P.onChange('IvaliceLegacyCoop', 'hudPreset', 4)      # choosing Custom changes nothing
        self.assertIs(self.store.state.battleHud.elements.seatDiamonds, False)
        P.onChange('IvaliceLegacyCoop', 'hud_seatDiamonds', 1)
        self.assertEqual(P.presetIndex(), 2, 'back to the same set of elements: Standard again')
        P.onChange('IvaliceLegacyCoop', 'hud_partyTimers', 1)
        self.assertEqual(P.presetIndex(), 3, 'Standard plus the timers is Full')
        self.store.state.battleHud.elements.circlesOff = False        # a file that shows the game's circles
        self.assertEqual(P.presetIndex(), 4)

    def test_a_toggle_changes_only_its_element(self):
        before = self.store.copy(self.store.state)
        self.P.onChange('IvaliceLegacyCoop', 'hud_seatDiamonds', 0)
        h = self.store.state.battleHud
        self.assertIs(h.elements.seatDiamonds, False)
        for k in self.store.HUD_ELEMENTS.values():
            if k != 'seatDiamonds':
                self.assertIs(h.elements[k], before.battleHud.elements[k], k)
        self.assertIs(h.enabled, True)
        self.P.onChange('IvaliceLegacyCoop', 'hudLogFilter', 4)
        self.assertEqual(self.store.state.battleHud.log.filter, 'MINE')
        self.P.onChange('IvaliceLegacyCoop', 'hudLogSeat', 3)
        self.assertEqual(self.store.state.battleHud.log.filterSeat, 4, 'a fourth player can be the one shown')
        expand = list(self.P.expandOptions().values())
        self.assertEqual(expand[0], 'NONE')
        self.assertEqual(len(expand), 1 + len(PADC))
        self.P.onChange('IvaliceLegacyCoop', 'hudLogExpand', expand.index('R3'))
        self.assertEqual(self.store.state.battleHud.log.expandButton, 'R3')

    def test_the_log_rows_and_their_rules(self):
        env = self.env
        log = self.store.state.battleHud.log
        self.assertEqual(env.options('hudLogCompact'), [str(n) for n in range(1, 9)])
        self.assertEqual((env.options('hudLogExpanded')[0], env.options('hudLogExpanded')[-1]), ('5', '20'))
        self.assertEqual((env.options('hudLogFade')[0], env.options('hudLogFade')[-1]), ('3 s', '15 s'))
        self.assertEqual((log.compactLines, log.expandedLines, log.fadeSeconds), (5, 20, 6))
        self.assertEqual([env.row(r).default for r in ('hudLogCompact', 'hudLogExpanded', 'hudLogFade')], [4, 15, 3])
        self.P.onChange('IvaliceLegacyCoop', 'hudLogCompact', 2)
        self.P.onChange('IvaliceLegacyCoop', 'hudLogExpanded', 0)
        self.P.onChange('IvaliceLegacyCoop', 'hudLogFade', 12)
        log = self.store.state.battleHud.log
        self.assertEqual((log.compactLines, log.expandedLines, log.fadeSeconds), (3, 5, 15))
        self.P.onChange('IvaliceLegacyCoop', 'hudLogCompact', 7)       # 8 > 5: refused by the page, nothing sent
        self.assertEqual(self.store.state.battleHud.log.compactLines, 3)
        self.assertIn('compact log cannot have more', env.last_note())
        self.P.onChange('IvaliceLegacyCoop', 'hudLogCompact', 4)       # 5 <= 5
        self.assertEqual(self.store.state.battleHud.log.compactLines, 5)
        self.P.onChange('IvaliceLegacyCoop', 'hudLogExpanded', 2)      # 7
        self.P.onChange('IvaliceLegacyCoop', 'hudLogCompact', 7)       # 8 > 7
        self.P.onChange('IvaliceLegacyCoop', 'hudLogExpanded', 0)      # 5 < 5? equal: fine
        self.assertEqual(self.store.state.battleHud.log.expandedLines, 5)
        mod = self.page_mod

        def span(name):
            f = env.C.field('ilc_settings', name)
            return (f.min, f.max)
        self.assertEqual(tuple(mod.COMPACT_LINES.values()), span('log_lines_compact'))
        self.assertEqual(tuple(mod.EXPANDED_LINES.values()), span('log_lines_expanded'))
        self.assertEqual(tuple(mod.FADE_SECONDS.values()), span('log_fade_s'))

    def test_the_log_player_row_follows_the_filter(self):
        self.assertIs(self.env.row('hudLogSeat').visible, False, 'registered hidden: the filter is All')
        self.P.refresh()
        self.P.onChange('IvaliceLegacyCoop', 'hudLogFilter', 4)
        self.P.refresh()
        self.assertIs(self.env.menu.rows['hudLogSeat'].visible, True)
        self.P.onChange('IvaliceLegacyCoop', 'hudLogFilter', 0)
        self.P.refresh()
        self.assertIs(self.env.menu.rows['hudLogSeat'].visible, False)

    def test_player_colours_is_one_row(self):
        self.assertEqual(self.env.options('colourMode'), ['By character', 'By player'])
        self.assertEqual(self.env.row('colourMode').default, 0, 'native default: by character')
        self.P.onChange('IvaliceLegacyCoop', 'colourMode', 1)
        self.assertEqual(self.store.state.colours.mode, 'PLAYER')
        for gone in ('playerColour1', 'playerColour2', 'characterColour_VAAN', 'uiSounds1', 'seatToggleKey2'):
            self.assertIsNone(self.env.row(gone), gone)

    def status(self, report, ready=True, **native):
        env = self.env
        if ready:
            env.go_ready(hudReport=report, **native)
        else:
            env.bridge.status.state = 'starting'
            env.P.native = None
        return env.P.hudStatusText()

    def test_status_line_follows_the_report_and_vision(self):
        self.assertEqual(self.status(0), 'On')
        self.assertEqual(self.status(REP['VISION_NO_COMBAT_LOG'] | REP['FRAME_HOOK_OFF']),
                         "Turn off Vision's No Combat Log")
        self.assertEqual(self.status(REP['FRAME_HOOK_OFF']), 'Not drawing: another mod holds the HUD hook')
        self.assertEqual(self.status(REP['PARTY_LAYOUT_FOREIGN'] | REP['CIRCLES_BY_VISION']),
                         'On; party list kept as another mod set it; circles hidden by Vision')
        self.assertEqual(self.status(0, ready=False), 'n/a')
        self.env.vision_doc.noCombatLog.state = True
        self.P.readVision()
        self.assertEqual(self.status(0, ready=False), "Turn off Vision's No Combat Log",
                         "Vision's file alone says it is on (even before the module answers)")
        self.env.vision_doc.noCombatLog.state = False
        self.P.readVision()
        self.store.state.battleHud.enabled = False
        self.assertEqual(self.status(0), 'Off')
        self.assertEqual(self.status(REP['RENDER_OFF']), 'Off', 'the preset is the reason, not an error')

    def test_render_off_names_the_real_reason(self):
        """The first session's HUD said "stopped after an error" while the cause was the developer override."""
        render = self.env.C.enums.subsystem.RENDER
        off = REP['RENDER_OFF']
        no_render = 0xFF & ~render
        self.assertEqual(self.status(off), 'Not drawing: the HUD could not start')
        self.assertEqual(self.status(off, appliedSubsystems=no_render), 'Not drawing: render is not running')
        self.assertEqual(self.status(off, appliedSubsystems=no_render, coopSwitch=2), 'Not drawing: co-op is switched off')
        self.store.state.diagnostics.disarm.render = True
        self.store.state.diagnostics.developer = False
        self.assertEqual(self.status(off, appliedSubsystems=no_render), 'Not drawing: render is not running',
                         'the override only counts while Developer options is On')
        self.store.state.diagnostics.developer = True
        self.assertEqual(self.status(off, appliedSubsystems=no_render),
                         'Not drawing: render switched off by developer override')
        self.P.explainHud()
        note = self.env.last_note()
        self.assertIn('switched off by the developer override', note)
        self.assertIn('Advanced', note)
        self.assertNotIn('stopped after an error', note)

    def test_vision_file_is_only_read(self):
        page = (LUA / 'page.lua').read_text(encoding='utf-8')
        entry = ENTRY.read_text(encoding='utf-8')
        self.assertIn(self.page_mod.VISION_PATH, entry)
        self.assertNotRegex(entry + page, r'saveJson\([^)]*Vision')
        self.assertIn('config.loadJson(path)', entry)


if __name__ == '__main__':
    unittest.main()
