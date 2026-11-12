"""Three active seats in this release: P4 exists in every seat array but can never play.

The schema constant ACTIVE_SEATS_MAX (3) is what the Lua side reads. Native refuses P4 as the keyboard/mouse seat, as
the owner of any device, and as the seat of any command (PAIRING_START included), each with BAD_FIELD attributed to
seat 4; GET_SEATS never reports P4 as PLAYING; the native defaults give P4 no join key and no pad binding. No game run,
nothing deployed: the real DLL is loaded into this process.
"""
from __future__ import annotations

import struct
import unittest

from coop_native_support import native_test
from test_coop_bridge_abi import OP, E, S, BridgeCase, command, put, settings

P4 = 3  # seat index
ACTIVE = S.const['ACTIVE_SEATS_MAX']
SEATF_PLAYING = S.e('seat_flag', 'PLAYING')


@native_test
class SeatLimitTest(BridgeCase):
    def test_schema_constant(self):
        self.assertEqual(ACTIVE, 3)
        self.assertEqual(S.const['SEATS'], 4, 'seat arrays keep four entries')
        self.assertEqual(S.json['constants']['ACTIVE_SEATS_MAX'], 3)

    def test_defaults_give_p4_nothing(self):
        record = S.defaults()
        offset, field, _ = S.locate('ilc_settings', ['seats', P4, 'bindings'])
        bindings = record[offset:offset + field['size']]
        self.assertEqual(bindings, bytes(len(bindings)), 'P4 has a default binding (join key or pad)')
        self.assertEqual(record[S.locate('ilc_settings', ['seats', P4, 'device', 'kind'])[0]], S.e('device_kind', 'NONE'))
        self.assertLessEqual(record[S.locate('ilc_settings', ['kbm_seat'])[0]], ACTIVE)
        status, _ = self.push(settings(1))
        self.assertGreater(status, 0, 'the defaults must validate')

    def assert_seat4_event(self, kind: str, code: int, field: int):
        _, events = self.events()
        last = events[-1]
        self.assertEqual((last['kind'], last['seat'], last['code'], last['field'], last['location'] >> 8),
                         (S.e('event_kind', kind), 4, code, field, P4))

    def test_kbm_seat_4_is_rejected(self):
        record = settings(1)
        put(record, ['kbm_seat'], 4)
        self.reject(record, E['BAD_FIELD'], S.field_id('ilc_settings', 'kbm_seat'), outer=P4, inner=0)
        self.assert_seat4_event('SETTINGS_REJECTED', E['BAD_FIELD'], S.field_id('ilc_settings', 'kbm_seat'))
        put(record, ['kbm_seat'], 3)
        self.assertGreater(self.push(record)[0], 0, 'kbm_seat 3 is an active seat')

    def test_a_seat4_device_is_rejected(self):
        kind_field = S.field_id('ilc_seat_device', 'kind')
        record = settings(1)
        put(record, ['seats', P4, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
        put(record, ['seats', P4, 'device', 'xinput_slot'], 3)
        self.reject(record, E['BAD_FIELD'], kind_field, outer=P4, inner=0)
        self.assert_seat4_event('SETTINGS_REJECTED', E['BAD_FIELD'], kind_field)
        # The same pad on P3 is fine.
        record = settings(1)
        put(record, ['seats', 2, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
        put(record, ['seats', 2, 'device', 'xinput_slot'], 3)
        self.assertGreater(self.push(record)[0], 0)

    def test_every_command_for_seat_4_is_rejected(self):
        seat_field = S.field_id('ilc_command', 'seat')
        args = {'PARK': (1, 0), 'MOVE_SEAT_NOW': (0, 0), 'PAIRING_START': (1, 1), 'PAIRING_CANCEL': (0, 0),
                'CAPTURE_START': (0, 0), 'CAPTURE_CANCEL': (0, 0), 'RESTORE_DEFAULTS': (0, 0), 'KILL_SWITCH': (0, 0)}
        self.assertEqual(set(args), set(S.enums['command']))
        for request, (name, (arg0, arg1)) in enumerate(sorted(args.items()), 1):
            with self.subTest(name):
                status, _ = self.b.call(OP['COMMAND'], command(name, 4, arg0, arg1, request))
                self.assertEqual(status, E['BAD_FIELD'])
                st = self.status()
                self.assertEqual((st['last_reject_field'], st['last_reject_location'] >> 8), (seat_field, P4))
                self.assert_seat4_event('COMMAND_REJECTED', E['BAD_FIELD'], seat_field)

    def test_pairing_start_for_seat_4_is_rejected_and_seat_3_is_not(self):
        status, _ = self.b.call(OP['COMMAND'], command('PAIRING_START', 4, 1, 1, 7))
        self.assertEqual(status, E['BAD_FIELD'])
        status, _ = self.b.call(OP['COMMAND'], command('PAIRING_START', 3, 1, 1, 8))
        self.assertNotEqual(status, E['BAD_FIELD'], 'P3 may pair (NOT_READY while the input service is stopped)')

    def seats(self) -> list[dict]:
        return S.decode('ilc_seats_reply', self.ok(OP['GET_SEATS']))['seats']

    def test_seat_4_is_never_playing(self):
        self.assertFalse(self.seats()[P4]['flags'] & SEATF_PLAYING, 'defaults')
        # Every active seat playing (P1 pad, P2 keyboard, P3 pad), P4 left as it must be: still not PLAYING.
        record = settings(1)
        put(record, ['seats', 2, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
        put(record, ['seats', 2, 'device', 'xinput_slot'], 2)
        self.assertGreater(self.push(record)[0], 0)
        self.safe_point()
        flags = [s['flags'] for s in self.seats()]
        self.assertTrue(all(f & SEATF_PLAYING for f in flags[:ACTIVE]), flags)
        self.assertFalse(flags[P4] & SEATF_PLAYING)
        # No accepted record can make P4 own an input.
        for path, value in ((['kbm_seat'], 4), (['seats', P4, 'device', 'kind'], S.e('device_kind', 'XINPUT'))):
            record = settings(2)
            put(record, path, value)
            self.assertEqual(self.push(record)[0], E['BAD_FIELD'], path)
        self.assertFalse(self.seats()[P4]['flags'] & SEATF_PLAYING)


if __name__ == '__main__':
    unittest.main()
