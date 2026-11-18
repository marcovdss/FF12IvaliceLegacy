"""The co-op settings store addresses bridge record fields by position in the schema (field order is part of the ABI).
This checks the kind and range at each position, so a reordered or retyped field fails here instead of silently
writing the wrong byte."""
import unittest
from pathlib import Path

from coop_menu_double import MENU_DOUBLE

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'

EXPECT = {  # (schema struct, POSITIONS key): name -> (kind, min, max); containers have no range
    ('ilc_settings', 'settings'): {
        'moduleEnabled': ('VAL', 0, 1), 'keyboardSeat': ('VAL', 0, 4),
        'colourMode': ('VAL', 0, 1), 'logLevel': ('VAL', 0, 1), 'networkMode': ('VAL', 0, 0),
        'aliasCount': ('VAL', 0, 16), 'keepRunning': ('VAL', 0, 1), 'characterColour': ('ARR', 0, 6), 'hudTestDraw': ('VAL', 0, 1), 'developer': ('VAL', 0, 1),
        'killSwitch': ('RECS',),
        'economy': ('REC',), 'seats': ('RECS',), 'aliases': ('RECS',), 'subsystemsDisarm': ('VAL', 0, 255),
        'hudEnabled': ('VAL', 0, 1), 'logExpand': ('VAL', 0, 24), 'logFilter': ('VAL', 0, 4), 'logFilterSeat': ('VAL', 1, 4),
        'hudElements': ('VAL', 0, 0x1FFF), 'sharedCamera': ('VAL', 0, 1), 'guestInteractions': ('VAL', 0, 1),
        'townView': ('VAL', 0, 1), 'waitPause': ('VAL', 0, 1), 'guestPause': ('VAL', 0, 1),
        'logLinesCompact': ('VAL', 1, 8), 'logLinesExpanded': ('VAL', 5, 20), 'logFadeSeconds': ('VAL', 3, 15),
        'hudTheme': ('BYTES',)},
    ('ilc_seat_settings', 'seat'): {
        'character': ('VAL', 0, 255), 'colour': ('VAL', 0, 6), 'uiSounds': ('VAL', 0, 1), 'parked': ('VAL', 0, 1),
        'device': ('REC',), 'bindings': ('RECS',)},
    ('ilc_economy', 'economy'): {
        'present': ('VAL', 0, 1), 'major': ('VAL', 0, 255), 'minor': ('VAL', 0, 255), 'patch': ('VAL', 0, 255),
        'divisor': ('VAL', 0, 25500), 'cap': ('VAL', 0, 1600)},
    ('ilc_seat_device', 'device'): {'kind': ('VAL', 0, 2), 'slot': ('VAL', 0, 3), 'sdl': ('REC',)},
    ('ilc_device_identity', 'identity'): {
        'guid': ('BYTES',), 'uid': ('BYTES',), 'ordinal': ('VAL', 0, 255), 'hasUid': ('VAL', 0, 1),
        'nameLen': ('VAL', 0, 96), 'name': ('BYTES',)},
    ('ilc_device_alias', 'alias'): {'slot': ('VAL', 0, 3), 'sdl': ('REC',)},
}


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class StorePositions(unittest.TestCase):
    def test_positions_match_the_schema(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        schema = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        store = load((COOP / 'store.lua').read_text(encoding='utf-8'), 'store')
        for (sname, key), fields in EXPECT.items():
            pos = store.POSITIONS[key]
            fields_list = schema.structs[sname].fields
            for name, want in fields.items():
                f = fields_list[pos[name] + 1]
                where = f'{sname} position of {name} is {f.name}'
                self.assertEqual(f.kind, want[0], where)
                if len(want) == 3:
                    self.assertEqual((f.min, f.max), want[1:], where)

    def test_every_position_is_in_range(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        schema = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        store = load((COOP / 'store.lua').read_text(encoding='utf-8'), 'store')
        for (sname, key) in EXPECT:
            count = len(schema.structs[sname].fields)
            for name, index in store.POSITIONS[key].items():
                self.assertTrue(0 <= index < count, f'{key}.{name}')


# The page's views of the native replies (views.lua) address fields the same way. (kind, min, max); 'REC'/'RECS' have no range.
VIEW_EXPECT = {
    'status': {'state': ('VAL', 0, 0xFFFFFFFF), 'reason': ('VAL', 0, 0xFFFFFFFF), 'buildFlags': ('VAL', 0, 0xFFFFFFFF),
               'acceptedGeneration': ('VAL', 0, 0xFFFFFFFF), 'acceptedDigest': ('VAL', 0, 0xFFFFFFFF),
               'appliedGeneration': ('VAL', 0, 0xFFFFFFFF), 'appliedDigest': ('VAL', 0, 0xFFFFFFFF),
               'stagedPending': ('VAL', 0, 1), 'eventsPending': ('VAL', 0, 0xFFFFFFFF),
               'lastRejectCode': ('VAL', 0, 0), 'lastRejectField': ('VAL', 0, 0xFFFFFFFF),
               'appliedSubsystems': ('VAL', 0, 0xFFFFFFFF), 'reasonDetail': ('VAL', 0, 0xFFFFFFFF),
               'bootFlags': ('VAL', 0, 0xFFFFFFFF), 'coopSwitch': ('VAL', 0, 2), 'hudReport': ('VAL', 0, 255)},
    'seats': {'sessionState': ('VAL', 0, 0xFFFFFFFF), 'epoch': ('VAL', 0, 0xFFFFFFFF), 'count': ('VAL', 0, 4),
              'seats': ('RECS',)},
    'seatView': {'seat': ('VAL', 1, 4), 'flags': ('VAL', 0, 255), 'configuredCharacter': ('VAL', 0, 255),
                 'liveMember': ('VAL', 0, 255), 'deviceKind': ('VAL', 0, 2), 'xinputSlot': ('VAL', 0, 3),
                 'colour': ('VAL', 0, 6), 'uiSounds': ('VAL', 0, 1), 'generation': ('VAL', 0, 0xFFFFFFFF)},
    'devices': {'serviceState': ('VAL', 0, 1), 'inventorySession': ('VAL', 0, 0xFFFFFFFF), 'ageMs': ('VAL', 0, 0xFFFFFFFF),
                'count': ('VAL', 0, 16), 'entries': ('RECS',)},
    'entry': {'entryId': ('VAL', 0, 0xFFFFFFFF), 'kind': ('VAL', 0, 2), 'xinputSlot': ('VAL', 0, 255),
              'connected': ('VAL', 0, 1), 'ownerSeat': ('VAL', 0, 4), 'flags': ('VAL', 0, 255), 'sdl': ('REC',)},
    'event': {'seq': ('VAL', 0, 0xFFFFFFFF), 'kind': ('VAL', 0, 65535), 'seat': ('VAL', 0, 4),
              'generation': ('VAL', 0, 0xFFFFFFFF), 'code': ('VAL', 0, 0), 'field': ('VAL', 0, 0xFFFFFFFF),
              'location': ('VAL', 0, 0xFFFFFFFF), 'a': ('VAL', 0, 0xFFFFFFFF), 'b': ('VAL', 0, 0xFFFFFFFF)},
    'identity': {'guid': ('BYTES',), 'uid': ('BYTES',), 'ordinal': ('VAL', 0, 255), 'hasUid': ('VAL', 0, 1),
                 'nameLen': ('VAL', 0, 96), 'name': ('BYTES',)},
}


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class ViewPositions(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        load = self.lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        self.schema = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.views = load((COOP / 'views.lua').read_text(encoding='utf-8'), 'views')

    def test_view_positions_match_the_schema(self):
        for key, fields in VIEW_EXPECT.items():
            sname = self.views.STRUCTS[key]
            pos = self.views.POSITIONS[key]
            self.assertEqual(set(pos.keys()) - set(fields), set(), f'{key}: positions without an expectation')
            fields_list = self.schema.structs[sname].fields
            for name, want in fields.items():
                if name not in pos:
                    continue
                f = fields_list[pos[name] + 1]
                where = f'{sname} position of {key}.{name} is {f.name}'
                self.assertEqual(f.kind, want[0], where)
                if len(want) == 3:
                    self.assertEqual((f.min, f.max), want[1:], where)

    def test_every_view_position_is_in_range(self):
        for key, struct in self.views.STRUCTS.items():
            count = len(self.schema.structs[struct].fields)
            for name, index in self.views.POSITIONS[key].items():
                self.assertTrue(0 <= index < count, f'{key}.{name}')

    def test_view_names_resolve_to_the_schema_names_we_expect(self):
        """A renamed native field is allowed (the page does not use names); this documents today's names."""
        names = {('status', 'reason'): 'reason', ('status', 'reasonDetail'): 'reason_detail',
                 ('seatView', 'flags'): 'flags', ('entry', 'ownerSeat'): 'owner_seat', ('event', 'generation'): 'generation'}
        for (key, name), want in names.items():
            f = self.schema.structs[self.views.STRUCTS[key]].fields[self.views.POSITIONS[key][name] + 1]
            self.assertEqual(f.name, want)


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class KillSwitchStatusRow(unittest.TestCase):
    """The Status row shows the module's kill-switch state (status coop_switch) while the module is otherwise fine."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        load = self.lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        schema = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.C = load((COOP / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
        self.S = load((COOP / 'statusview.lua').read_text(encoding='utf-8'), 'statusview')
        self.table = self.lua.eval('function(t) return t end')

    def classify(self, switch, state='READY', reason=0):
        native = self.lua.table_from({'stateName': state, 'reason': reason, 'reasonDetail': 0, 'bootFlags': 0,
                                      'coopSwitch': switch})
        bridge = self.lua.table_from({'state': 'ready'})
        return self.S.classify(bridge, native, self.C)

    def test_each_switch_state_on_the_status_row(self):
        on = self.classify(0)
        self.assertEqual((on.kind, on.switch), ('ok', 'ON'))
        releasing = self.classify(1)
        self.assertEqual((releasing.kind, releasing.text), ('switched_off', 'Co-op switching off'))
        off = self.classify(2)
        self.assertEqual((off.kind, off.text), ('switched_off', 'Co-op off (kill switch)'))
        self.assertIn('F8', off.advice)
        self.assertFalse(self.S.blocks_settings(off.kind), 'the settings stay reachable while co-op is off')

    def test_a_real_problem_wins_over_the_switch(self):
        blocked = self.classify(2, state='BLOCKED')
        self.assertEqual(blocked.kind, 'blocked')

    def test_live_and_dry_armed_reasons(self):
        """The module reports DRY_ARMED while nothing beyond the CORE rows can act and LIVE once an armed subsystem may
        act. The page shows "Ready (developer override: parts off)" and "Ready"; before FF12HinitDone (state ARMED) both read "Starting";
        the kill switch's text still wins over either."""
        dry, live = self.C.enums.reason.DRY_ARMED, self.C.enums.reason.LIVE
        self.assertEqual(live, 17)
        ready_dry = self.classify(0, reason=dry)
        ready_live = self.classify(0, reason=live)
        self.assertEqual((ready_dry.kind, ready_dry.text), ('ok', 'Ready (developer override: parts off)'))
        self.assertEqual((ready_live.kind, ready_live.text), ('ok', 'Ready'))
        self.assertEqual(ready_live.detail, 'reason: Live')
        self.assertEqual(self.classify(0, state='ARMED', reason=live).text, 'Starting')
        self.assertEqual(self.classify(0, state='ARMED', reason=dry).text, 'Starting')
        self.assertEqual(self.classify(2, reason=live).text, 'Co-op off (kill switch)')
        self.assertFalse(self.S.blocks_settings(ready_live.kind))



@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class GuestInteractionsDiagnostics(unittest.TestCase):
    """Boot flag INTERACTIONS_FAILED: the status view reports it, and the page's Diagnostics row "Guest interactions"
    is shown only while it is set. The page runs with the real modules and a recording Mod Menu."""

    HARNESS = MENU_DOUBLE + r"""
local load, S, C, V, B, ST, D, F, page = ...
local calls = {}
local mm = newMenu(calls)
local P = page.create({modmenu = mm, store = ST, bridge = B, codec = C, views = V, flows = F, dialogs = D,
                       statusview = S})
assert(P.start())
return P, calls
"""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        load = self.lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        schema = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.C = load((COOP / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
        self.S = load((COOP / 'statusview.lua').read_text(encoding='utf-8'), 'statusview')
        self.flag = self.C.enums.boot_flag.INTERACTIONS_FAILED
        self.residue = self.C.enums.boot_flag.TOGETHER_RESIDUE
        views = load((COOP / 'views.lua').read_text(encoding='utf-8'), 'views').new(self.C)
        bridge = load((COOP / 'bridge.lua').read_text(encoding='utf-8'), 'bridge').create(
            self.lua.table_from({'codec': self.C, 'after': self.lua.eval('function() end')}))
        config = self.lua.eval('{loadJson = function() return nil end, saveJson = function() return true end}')
        store = load((COOP / 'store.lua').read_text(encoding='utf-8'), 'store').create(
            self.lua.table_from({'codec': self.C, 'config': config}))
        store.load()
        dialogs = load((COOP / 'dialogs.lua').read_text(encoding='utf-8'), 'dialogs').create(self.lua.table())
        flows = load((COOP / 'flows.lua').read_text(encoding='utf-8'), 'flows').create(self.lua.table_from(
            {'store': store, 'bridge': bridge, 'codec': self.C, 'views': views, 'dialogs': dialogs, 'seed': 1}))
        page = load((COOP / 'page.lua').read_text(encoding='utf-8'), 'page')
        run = self.lua.eval('function(src, ...) return assert(load(src, "@harness", "t", _ENV))(...) end')
        self.bridge = bridge
        self.P, self.calls = run(self.HARNESS, load, self.S, self.C, views, bridge, store, dialogs, flows, page)

    def classify(self, flags):
        native = self.lua.table_from({'stateName': 'READY', 'reason': 0, 'reasonDetail': 0, 'bootFlags': flags,
                                      'coopSwitch': 0})
        return self.S.classify(self.lua.table_from({'state': 'ready'}), native, self.C)

    def row(self, rows, wanted):
        for k in range(1, len(rows) + 1):
            r = rows[k]
            if r.id == wanted:
                return r
            if r.sub is not None:
                found = self.row(r.sub, wanted)
                if found is not None:
                    return found
        return None

    def visibility(self, row_id):
        """The last visibility the page sent for the row, or None when it sent none."""
        out = None
        for k in range(1, len(self.calls.visible) + 1):
            v = self.calls.visible[k][row_id]
            if v is not None:
                out = v
        return out

    def redraw(self, flags):
        self.P.view = self.classify(flags)
        self.P.refresh()

    def test_the_status_view_reports_the_flag(self):
        self.assertEqual(self.flag, 0x0400)
        self.assertIs(self.classify(self.flag).interactionsFailed, True)
        self.assertIs(self.classify(0).interactionsFailed, False, 'instrument: no flag, not failed')
        self.assertIs(self.classify(self.residue).interactionsFailed, False, 'another flag is not this one')
        self.assertIs(self.classify(self.flag | self.residue).interactionsFailed, True)
        self.assertEqual(self.classify(self.flag).kind, 'ok', 'the rest of co-op still counts as running')

    def test_the_diagnostics_row_is_shown_only_while_the_flag_is_set(self):
        row = self.row(self.calls.tables[1].schema, 'diagInteractions')
        self.assertIsNotNone(row, 'instrument: the page has the row')
        self.assertIs(row.visible, False, 'registered hidden')
        self.redraw(0)
        self.assertIn(self.visibility('diagInteractions'), (None, False), 'no flag: still hidden')
        self.redraw(self.flag)
        self.assertIs(self.visibility('diagInteractions'), True, 'shown once the flag is set')
        self.assertEqual(self.P.rows.diagInteractions.status(None), 'Unavailable')
        self.assertIn(self.visibility('diagResidue'), (None, False), 'the residue row does not follow it')
        self.redraw(self.flag)
        sent = len(self.calls.visible)
        self.redraw(self.flag)
        self.assertEqual(len(self.calls.visible), sent, 'unchanged: nothing sent again')
        self.redraw(0)
        self.assertIs(self.visibility('diagInteractions'), False, 'hidden again without the flag')
        self.assertEqual(self.P.rows.diagInteractions.status(None), 'Ready')


if __name__ == '__main__':
    unittest.main()
