"""Four human seats (0.1.53): P1..P4 all play.

The schema constant ACTIVE_SEATS_MAX (4) is what the Lua side reads and equals SEATS, so no seat array has an entry
that cannot play. Native accepts P4 as the keyboard/mouse seat, as the owner of a device and as the seat of every
command (PAIRING_START included); a seat number above 4 is refused by the field's own range. GET_SEATS reports P4 as
PLAYING once it owns an input. The native defaults give P4 the guest layout with the join key F9 (F6/F7 are P2/P3,
F8 the co-op on/off key) and the colour amethyst. No game run, nothing deployed: the real DLL is loaded into this
process.
"""
from __future__ import annotations

import unittest

from coop_native_support import native_test
from test_coop_bridge_abi import OP, E, S, BridgeCase, command, put, settings

P4 = 3  # seat index
ACTIVE = S.const['ACTIVE_SEATS_MAX']
SEATF_PLAYING = S.e('seat_flag', 'PLAYING')
TOGGLE = S.e('action', 'TOGGLE_CONTROL') * S.const['BINDINGS_PER_ACTION']
F9 = 0x78


@native_test
class SeatLimitTest(BridgeCase):
    def test_schema_constant(self):
        self.assertEqual(ACTIVE, 4)
        self.assertEqual(S.const['SEATS'], 4, 'seat arrays keep four entries')
        self.assertEqual(S.json['constants']['ACTIVE_SEATS_MAX'], 4)
        fields = {f['name']: f for f in S.json['structs']['ilc_settings']['fields']}
        self.assertEqual((fields['kbm_seat']['min'], fields['kbm_seat']['max']), (0, 4))
        self.assertEqual((fields['log_filter_seat']['min'], fields['log_filter_seat']['max']), (1, 4))

    def test_defaults_give_p4_the_guest_layout(self):
        record = S.defaults()
        d = S.decode('ilc_settings', record)
        p4 = d['seats'][P4]
        self.assertEqual((p4['bindings'][TOGGLE]['kind'], p4['bindings'][TOGGLE]['code']), (S.e('binding_kind', 'KEY'), F9))
        pads = {(i // 4, b['code']) for i, b in enumerate(p4['bindings']) if b['kind'] == S.e('binding_kind', 'PAD')}
        self.assertIn((S.e('action', 'MOVE_UP'), S.e('pad_control', 'LS_UP')), pads)
        self.assertIn((S.e('action', 'QUICKENING'), S.e('pad_control', 'X')), pads)
        self.assertEqual(p4['device']['kind'], S.e('device_kind', 'NONE'), 'P4 starts without a pad (paired in game)')
        self.assertEqual(p4['character'], S.e('character', 'AUTO'))
        self.assertEqual(p4['colour'], S.e('colour', 'AMETHYST'))
        status, _ = self.push(settings(1))
        self.assertGreater(status, 0, 'the defaults must validate')

    def test_kbm_seat_4_is_accepted(self):
        record = settings(1)
        put(record, ['kbm_seat'], 4)
        self.assertGreater(self.push(record)[0], 0, 'kbm_seat 4 is an active seat')
        record = settings(2)
        put(record, ['kbm_seat'], 5)
        self.reject(record, E['BAD_FIELD'], S.field_id('ilc_settings', 'kbm_seat'))

    def test_a_seat4_device_is_accepted(self):
        record = settings(1)
        put(record, ['seats', P4, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
        put(record, ['seats', P4, 'device', 'xinput_slot'], 3)
        self.assertGreater(self.push(record)[0], 0)
        # One pad, one owner still holds across all four seats.
        record = settings(2)
        for seat in (2, P4):
            put(record, ['seats', seat, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
            put(record, ['seats', seat, 'device', 'xinput_slot'], 3)
        self.reject(record, E['CONFLICT'], S.field_id('ilc_seat_device', 'kind'), outer=P4, inner=0)

    def test_p4_join_key_clashes_like_the_others(self):
        """F9 is P4's alone: the kill switch on F9, or another guest's join key on F9, is a CONFLICT."""
        ks = S.field_id('ilc_settings', 'kill_switch')
        record = settings(1)
        put(record, ['kill_switch', 1, 'kind'], S.e('binding_kind', 'KEY'))
        put(record, ['kill_switch', 1, 'code'], F9)
        self.reject(record, E['CONFLICT'], ks, outer=S.e('location', 'KILL_SWITCH'), inner=1)
        record = settings(2)
        put(record, ['seats', 1, 'bindings', TOGGLE + 1, 'kind'], S.e('binding_kind', 'KEY'))
        put(record, ['seats', 1, 'bindings', TOGGLE + 1, 'code'], F9)
        self.reject(record, E['CONFLICT'], S.field_id('ilc_seat_settings', 'bindings'), outer=P4, inner=TOGGLE)

    def test_every_command_for_seat_4_passes_the_seat_check(self):
        seat_field = S.field_id('ilc_command', 'seat')
        args = {'PARK': (1, 0), 'MOVE_SEAT_NOW': (0, 0), 'PAIRING_START': (1, 1), 'PAIRING_CANCEL': (0, 0),
                'CAPTURE_START': (0, 0), 'CAPTURE_CANCEL': (0, 0), 'RESTORE_DEFAULTS': (0, 0)}
        for request, (name, (arg0, arg1)) in enumerate(sorted(args.items()), 1):
            with self.subTest(name):
                status, _ = self.b.call(OP['COMMAND'], command(name, 4, arg0, arg1, request))
                self.assertNotEqual(status, E['BAD_FIELD'], 'seat 4 may be commanded (NOT_READY while stopped)')
        # Seat-0-only commands still refuse seat 4 (their own rule), and seat 5 is out of the field's range.
        for name, arg0 in (('KILL_SWITCH', 1), ('STALL_COOP_THREAD', 3000)):
            with self.subTest(name):
                status, _ = self.b.call(OP['COMMAND'], command(name, 4, arg0, 0, 50))
                self.assertEqual(status, E['BAD_FIELD'])
                self.assertEqual(self.status()['last_reject_field'], seat_field)
        status, _ = self.b.call(OP['COMMAND'], command('PAIRING_START', 5, 1, 1, 60))
        self.assertEqual(status, E['BAD_FIELD'])

    def seats(self) -> list[dict]:
        return S.decode('ilc_seats_reply', self.ok(OP['GET_SEATS']))['seats']

    def test_seat_4_plays_once_it_owns_an_input(self):
        self.assertFalse(self.seats()[P4]['flags'] & SEATF_PLAYING, 'defaults: no pad yet')
        # Every seat playing: P1 pad, P2 keyboard, P3 and P4 pads.
        record = settings(1)
        for seat, slot in ((2, 2), (P4, 3)):
            put(record, ['seats', seat, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
            put(record, ['seats', seat, 'device', 'xinput_slot'], slot)
        self.assertGreater(self.push(record)[0], 0)
        self.safe_point()
        flags = [s['flags'] for s in self.seats()]
        self.assertTrue(all(f & SEATF_PLAYING for f in flags), flags)
        # Parked, P4 is not playing.
        record = settings(2)
        for seat, slot in ((2, 2), (P4, 3)):
            put(record, ['seats', seat, 'device', 'kind'], S.e('device_kind', 'XINPUT'))
            put(record, ['seats', seat, 'device', 'xinput_slot'], slot)
        put(record, ['seats', P4, 'parked'], 1)
        self.assertGreater(self.push(record)[0], 0)
        self.safe_point()
        self.assertFalse(self.seats()[P4]['flags'] & SEATF_PLAYING)


if __name__ == '__main__':
    unittest.main()
