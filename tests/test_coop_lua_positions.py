"""The co-op settings store addresses bridge record fields by position in the schema (field order is part of the ABI).
This checks the kind and range at each position, so a reordered or retyped field fails here instead of silently
writing the wrong byte."""
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'

EXPECT = {  # (schema struct, POSITIONS key): name -> (kind, min, max); containers have no range
    ('ilc_settings', 'settings'): {
        'moduleEnabled': ('VAL', 0, 1), 'fullRoster': ('VAL', 0, 1), 'keyboardSeat': ('VAL', 0, 4),
        'colourMode': ('VAL', 0, 1), 'logLevel': ('VAL', 0, 1), 'networkMode': ('VAL', 0, 0),
        'aliasCount': ('VAL', 0, 16), 'characterColour': ('ARR', 0, 6), 'killSwitch': ('RECS',),
        'economy': ('REC',), 'seats': ('RECS',), 'aliases': ('RECS',), 'subsystemsArmed': ('VAL', 0, 255)},
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
               'bootFlags': ('VAL', 0, 0xFFFFFFFF)},
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


if __name__ == '__main__':
    unittest.main()
