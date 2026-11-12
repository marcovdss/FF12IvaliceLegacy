"""Bridge ABI v1 of the co-op module, through the real DLL (no game run, nothing deployed).

The record layouts, ranges, field ids and enum values come from native/coop/schema/bridge-abi-v1.json, which
build.py regenerates from include/ilc/bridge_abi.h and refuses to build on any difference.

* schema: committed JSON, HELLO schema id and native defaults agree;
* settings: staging, safe point, readback, generation rules (repeat, stale, conflict, latest wins, re-sync);
* validation: every inbound scalar field driven past its range, every reserved byte set, the cross-field rules;
* commands, queries, events (bounded queue, drop-oldest, partial pops);
* memory.execute shape: 8 slots, dirty upper halves refused, garbage in slots 5-7 ignored;
* fault safety: unreadable/unwritable buffers give FAULT; a guard-page fuzz finds no read or write outside
  inLen/outCap; nothing the module accepts is out of range;
* load: 4 threads x 10,000 GET_STATUS with concurrent pushes and safe points; p99 < 1 ms; handles flat;
* static: no thread_local; the probe's guarded range covers only its load and store; golden DLL hash.

ILC_REQUIRE_NATIVE=1 makes a missing toolchain a failure instead of a skip.
"""
from __future__ import annotations

import ctypes
import json
import random
import re
import struct
import threading
import time
import unittest

from coop_native_support import (CANARY, COOP, SCHEMA, Bridge, GuardedBuffer, built_dll, fresh_dll, manifest,
                                 native_test, quiet_faulthandler)

try:
    import capstone
except ImportError:
    capstone = None
try:
    import psutil
except ImportError:
    psutil = None

S = SCHEMA
OP = S.enums['op']
E = S.enums['error']
ERROR_CODES = set(E.values())
SETTINGS_SIZE = S.size('ilc_settings')
ACK = S.e('ack_disposition', 'STAGED'), S.e('ack_disposition', 'DUPLICATE')
LOC_KILL = S.e('location', 'KILL_SWITCH')
LOC_ALIAS0 = S.e('location', 'ALIAS_0')


def settings(generation: int, base: bytes | None = None) -> bytearray:
    record = bytearray(base if base is not None else S.defaults())
    struct.pack_into('<I', record, S.locate('ilc_settings', ['header', 'generation'])[0], generation)
    return record


def put(record: bytearray, path: list, value: int, struct_name: str = 'ilc_settings'):
    offset, field, _ = S.locate(struct_name, path)
    fmt = {1: 'B', 2: 'H', 4: 'I'}[field['elemSize']]
    struct.pack_into('<' + fmt, record, offset, value)


def command(cmd: str, seat: int = 0, arg0: int = 0, arg1: int = 0, request: int = 1) -> bytes:
    return bytes(S.encode('ilc_command', {
        'header': S.header('COMMAND', S.size('ilc_command'), request),
        'command': S.e('command', cmd), 'seat': seat, 'arg0': arg0, 'arg1': arg1}))


def expected_location(path: list) -> tuple[int, int | None]:
    """(outer, inner) the module reports for an element path; inner None = element index of the leaf."""
    if path[0] == 'kill_switch':
        return LOC_KILL, path[1]
    if path[0] == 'aliases':
        return LOC_ALIAS0 + path[1], None
    if path[0] == 'seats':
        if path[2] == 'bindings':
            return path[1], path[3]
        return path[1], None
    return 0, None


class BridgeCase(unittest.TestCase):
    """Each test gets a fresh module instance (its own DLL copy)."""

    def setUp(self):
        self.b = Bridge(fresh_dll())

    def ok(self, op, data=None, out_cap=4096):
        status, out = self.b.call(op, data, out_cap)
        self.assertGreaterEqual(status, 0, f'op {op} failed with {status}')
        return out

    def status(self) -> dict:
        return S.decode('ilc_status', self.ok(OP['GET_STATUS']))

    def push(self, record) -> tuple[int, dict | None]:
        status, out = self.b.call(OP['SET_SETTINGS'], bytes(record))
        return status, (S.decode('ilc_settings_ack', out) if status >= 0 else None)

    def safe_point(self) -> dict:
        return S.decode('ilc_safe_point_result', self.ok(OP['TEST_SAFE_POINT']))

    def events(self, out_cap=4096) -> tuple[dict, list[dict]]:
        out = self.ok(OP['POP_EVENTS'], out_cap=out_cap)
        head = S.decode('ilc_events_reply', out + bytes(S.size('ilc_events_reply') - len(out)))
        return head, head['events'][:head['count']]

    def reject(self, record, code, field=None, outer=None, inner=None):
        before = self.status()
        status, _ = self.push(record)
        self.assertEqual(status, code, f'expected {code}, got {status}')
        after = self.status()
        self.assertEqual(after['rejects'], before['rejects'] + 1)
        self.assertEqual(after['last_reject_code'], code)
        self.assertEqual((after['accepted_generation'], after['applied_generation']),
                         (before['accepted_generation'], before['applied_generation']), 'a reject changed state')
        if field is not None:
            self.assertEqual(after['last_reject_field'], field,
                             f'{S.field_name(after["last_reject_field"])} != {S.field_name(field)}')
        location = after['last_reject_location']
        if outer is not None:
            self.assertEqual(location >> 8, outer)
        if inner is not None:
            self.assertEqual(location & 0xFF, inner)
        return after


@native_test
class SchemaTest(BridgeCase):
    def test_committed_schema_matches_header_and_hello(self):
        built_dll()
        self.assertEqual(manifest()['schema']['sha256'], S.sha256)
        hello = S.decode('ilc_hello_reply', self.ok(OP['HELLO']))
        self.assertEqual(struct.pack('>2Q', *hello['schema_id']).hex(), S.sha256[:32])
        self.assertEqual(struct.pack('>2Q', *hello['source_id']).hex(), manifest()['sourceId'][:32])
        self.assertEqual(hello['header']['abi_version'], S.const['ABI_VERSION'])
        self.assertEqual(hello['settings_size'], SETTINGS_SIZE)
        expected_mask = sum(1 << v for k, v in OP.items() if v < 32)
        self.assertEqual(hello['ops_mask'], expected_mask)
        self.assertEqual((hello['module_major'], hello['module_minor'], hello['module_patch']), (0, 0, 2))

    def test_no_struct_has_implicit_padding(self):
        for name, st in S.structs.items():
            end = 0
            for f in st['fields']:
                self.assertEqual(f['offset'], end, f'{name}.{f["name"]}')
                end += f['size']
            self.assertEqual(end, st['size'], name)

    def test_enum_typed_fields_follow_their_enums(self):
        # The header derives these limits from the enum lists and constants; this pins the result.
        top = lambda e: max(S.enums[e].values())  # noqa: E731
        expected = {
            ('ilc_binding', 'kind'): (0, top('binding_kind')),
            ('ilc_seat_device', 'kind'): (0, top('device_kind')),
            ('ilc_seat_device', 'xinput_slot'): (0, S.const['XINPUT_SLOTS'] - 1),
            ('ilc_device_alias', 'xinput_slot'): (0, S.const['XINPUT_SLOTS'] - 1),
            ('ilc_seat_settings', 'colour'): (0, top('colour')),
            ('ilc_settings', 'character_colour'): (0, top('colour')),
            ('ilc_settings', 'colour_mode'): (0, top('colour_mode')),
            ('ilc_settings', 'kbm_seat'): (0, S.const['SEATS']),
            ('ilc_settings', 'alias_count'): (0, S.const['ALIASES_MAX']),
            ('ilc_device_identity', 'name_len'): (0, S.const['NAME_MAX']),
            ('ilc_command', 'command'): (min(S.enums['command'].values()), top('command')),
            ('ilc_command', 'seat'): (0, S.const['SEATS']),
        }
        for (struct_name, field), (lo, hi) in expected.items():
            f = S.field(struct_name, field)
            self.assertEqual((f['min'], f['max']), (lo, hi), f'{struct_name}.{field}')
        self.assertEqual(S.field('ilc_device_identity', 'name')['count'], S.const['NAME_MAX'])
        self.assertEqual(len(S.enums['action']), S.const['ACTIONS'])
        self.assertIn('re-sync with GET_STATUS', S.json['rules']['fault'])

    def test_inbound_records_have_no_64_bit_scalars(self):
        for name in ('ilc_settings', 'ilc_command'):
            for path, f, owner in S.inbound_scalar_fields(name):
                self.assertLessEqual(f['elemSize'], 4, f'{owner}.{f["name"]}')

    def test_defaults_are_what_get_settings_returns_and_validate(self):
        out = self.ok(OP['GET_SETTINGS'])
        self.assertEqual(out, S.defaults())
        st = self.status()
        self.assertEqual((st['applied_generation'], st['accepted_generation'], st['staged_pending']), (0, 0, 0))
        self.assertEqual(st['applied_digest'], S.digest(S.defaults()))
        status, ack = self.push(settings(1))
        self.assertEqual(status, S.size('ilc_settings_ack'))
        self.assertEqual(ack['disposition'], S.e('ack_disposition', 'STAGED'))

    def test_defaults_content(self):
        d = S.decode('ilc_settings', S.defaults())
        ch = S.enums['character']
        self.assertEqual([seat['character'] for seat in d['seats']], [ch['AUTO'], ch['BASCH'], ch['BALTHIER'], ch['AUTO']])
        self.assertEqual(d['seats'][0]['device']['kind'], S.e('device_kind', 'XINPUT'))
        self.assertEqual([seat['device']['kind'] for seat in d['seats'][1:]], [0, 0, 0])
        self.assertEqual(d['kbm_seat'], 2)
        self.assertEqual(d['kill_switch'][0], {'kind': S.e('binding_kind', 'KEY'), 'code': 0x77})
        toggle = S.e('action', 'TOGGLE_CONTROL') * 4
        # P4 cannot play in this release (ACTIVE_SEATS_MAX): no join key, no binding at all.
        self.assertEqual([seat['bindings'][toggle]['code'] for seat in d['seats'][1:]], [0x75, 0x76, 0])
        self.assertTrue(all(b['kind'] == 0 for b in d['seats'][3]['bindings']))


@native_test
class GenerationTest(BridgeCase):
    def test_stage_then_apply_at_the_safe_point(self):
        record = settings(5)
        put(record, ['log_level'], 1)
        status, ack = self.push(record)
        self.assertEqual((ack['disposition'], ack['accepted_generation'], ack['applied_generation'],
                          ack['staged_pending'], ack['header']['generation']), (ACK[0], 5, 0, 1, 5))
        self.assertEqual(self.ok(OP['GET_SETTINGS']), S.defaults(), 'staged settings leaked before the safe point')
        result = self.safe_point()
        self.assertEqual((result['result'], result['applied_generation']), (S.e('safe_point_outcome', 'APPLIED'), 5))
        self.assertEqual(self.ok(OP['GET_SETTINGS']), bytes(record))
        st = self.status()
        self.assertEqual((st['applied_generation'], st['staged_pending'], st['applies']), (5, 0, 1))
        self.assertEqual(st['applied_digest'], S.digest(record))
        self.assertEqual(self.safe_point()['result'], S.e('safe_point_outcome', 'IDLE'))
        _, events = self.events()
        self.assertEqual([(e['kind'], e['generation'], e['a']) for e in events],
                         [(S.e('event_kind', 'SETTINGS_APPLIED'), 5, S.digest(record))])

    def test_repeat_is_a_no_op_before_and_after_apply(self):
        record = settings(3)
        self.assertEqual(self.push(record)[1]['disposition'], ACK[0])
        self.assertEqual(self.push(record)[1]['disposition'], ACK[1])
        self.safe_point()
        before = self.status()
        _, ack = self.push(record)  # a script restart re-sending the same record
        self.assertEqual((ack['disposition'], ack['staged_pending']), (ACK[1], 0))
        after = self.status()
        self.assertEqual({k: after[k] for k in ('applied_generation', 'applies', 'staged_pending', 'rejects')},
                         {k: before[k] for k in ('applied_generation', 'applies', 'staged_pending', 'rejects')})

    def test_older_generation_rejected_and_applied_persists(self):
        self.push(settings(10))
        self.safe_point()
        older = settings(9)
        put(older, ['log_level'], 1)
        self.reject(older, E['STALE_GENERATION'], S.field_id('ilc_header', 'generation'))
        self.assertEqual(self.ok(OP['GET_SETTINGS']), bytes(settings(10)))
        self.assertEqual(self.safe_point()['result'], S.e('safe_point_outcome', 'IDLE'))

    def test_same_generation_other_content_rejected(self):
        self.push(settings(4))
        other = settings(4)
        put(other, ['colour_mode'], 1)
        self.reject(other, E['GENERATION_CONFLICT'], S.field_id('ilc_header', 'generation'))

    def test_generation_zero_rejected(self):
        self.reject(settings(0), E['BAD_FIELD'], S.field_id('ilc_header', 'generation'))

    def test_subsystem_arm_mask(self):
        bits = S.enums['subsystem']
        every = 0
        for value in bits.values():
            every |= value
        self.assertEqual(len(bits), 8)
        self.assertEqual(S.decode('ilc_settings', S.defaults())['subsystems_armed'], every, 'defaults: all armed')
        self.assertEqual(self.status()['applied_subsystems'], every)
        record = settings(1)
        put(record, ['subsystems_armed'], every & ~bits['CAMERA'] & ~bits['RENDER'])  # bisect: two disarmed
        self.assertEqual(self.push(record)[1]['disposition'], ACK[0])
        self.assertEqual(self.status()['applied_subsystems'], every, 'applied before the safe point')
        self.safe_point()
        self.assertEqual(self.status()['applied_subsystems'], every & ~bits['CAMERA'] & ~bits['RENDER'])
        applied = S.decode('ilc_settings', self.ok(OP['GET_SETTINGS']))
        self.assertEqual(applied['subsystems_armed'], every & ~bits['CAMERA'] & ~bits['RENDER'])
        record = settings(2)
        put(record, ['subsystems_armed'], every | 0x100)  # an undefined bit
        self.reject(record, E['BAD_FIELD'], S.field_id('ilc_settings', 'subsystems_armed'))
        record = settings(3)
        put(record, ['subsystems_armed'], 0)  # everything disarmed is valid
        self.assertEqual(self.push(record)[1]['disposition'], ACK[0])

    def test_latest_staged_wins(self):
        self.push(settings(6))
        seven = settings(7)
        put(seven, ['all_characters_at_start'], 0)
        self.push(seven)
        self.assertEqual(self.status()['accepted_generation'], 7)
        self.safe_point()
        self.assertEqual(self.ok(OP['GET_SETTINGS']), bytes(seven))
        self.reject(settings(6), E['STALE_GENERATION'])

    def test_invalid_newer_generation_keeps_the_staged_one(self):
        self.push(settings(2))
        bad = settings(3)
        put(bad, ['network_mode'], 1)
        self.reject(bad, E['BAD_FIELD'])
        self.assertEqual(self.status()['accepted_generation'], 2)
        self.safe_point()
        self.assertEqual(self.ok(OP['GET_SETTINGS']), bytes(settings(2)))

    def test_resync_by_digest_after_a_script_restart(self):
        # The page restarts, reads HELLO's accepted generation and the status digest, and sends nothing when its
        # record body already matches; otherwise it sends accepted + 1.
        self.push(settings(8))
        self.safe_point()
        hello = S.decode('ilc_hello_reply', self.ok(OP['HELLO']))
        self.assertEqual((hello['accepted_generation'], hello['applied_generation']), (8, 8))
        st = self.status()
        self.assertEqual(st['accepted_digest'], S.digest(settings(99)))  # body digest ignores the header
        changed = settings(hello['accepted_generation'] + 1)
        put(changed, ['seats', 2, 'colour'], 1)
        self.assertEqual(self.push(changed)[1]['disposition'], ACK[0])


@native_test
class FieldValidationTest(BridgeCase):
    def test_every_inbound_scalar_field_out_of_range(self):
        cases = 0
        generation = 1
        for path, f, owner in S.inbound_scalar_fields('ilc_settings'):
            base = S.locate('ilc_settings', path)[0]
            if f['kind'] == 'PAD':
                bad_values = [1]
            else:
                limit = (1 << (8 * f['elemSize'])) - 1
                bad_values = [v for v in (f['min'] - 1, f['max'] + 1) if 0 <= v <= limit]
            outer, inner = expected_location(path)
            for e in range(f['count']):
                for value in bad_values:
                    record = settings(generation)
                    generation += 1
                    fmt = {1: 'B', 2: 'H', 4: 'I'}[f['elemSize']]
                    struct.pack_into('<' + fmt, record, base + e * f['elemSize'], value)
                    with self.subTest(path=path, element=e, value=value):
                        self.reject(record, E['BAD_FIELD'], f['fieldId'], outer, inner if inner is not None else e)
                    cases += 1
        self.assertGreater(cases, 500)

    def test_every_command_field_out_of_range(self):
        for path, f, owner in S.inbound_scalar_fields('ilc_command'):
            limit = (1 << (8 * f['elemSize'])) - 1
            values = [1] if f['kind'] == 'PAD' else [v for v in (f['min'] - 1, f['max'] + 1) if 0 <= v <= limit]
            for value in values:
                for e in range(f['count']):
                    record = bytearray(command('KILL_SWITCH'))
                    offset = S.locate('ilc_command', path)[0] + e * f['elemSize']
                    struct.pack_into('<' + {1: 'B', 2: 'H', 4: 'I'}[f['elemSize']], record, offset, value)
                    with self.subTest(path=path, element=e, value=value):
                        status, _ = self.b.call(OP['COMMAND'], bytes(record))
                        self.assertEqual(status, E['BAD_FIELD'])
                        self.assertEqual(self.status()['last_reject_field'], f['fieldId'])


@native_test
class CrossFieldTest(BridgeCase):
    gen = 0

    def rec(self) -> bytearray:
        self.gen += 1
        return settings(self.gen)

    def sdl(self, record, path_prefix, guid_byte, name=b'Pad', ordinal=0, uid=None):
        offset, _, _ = S.locate('ilc_settings', path_prefix + ['guid'])
        record[offset:offset + 16] = bytes([guid_byte]) * 16
        put(record, path_prefix + ['ordinal'], ordinal)
        if uid is not None:
            put(record, path_prefix + ['has_uid'], 1)
            o, _, _ = S.locate('ilc_settings', path_prefix + ['uid'])
            record[o:o + 16] = uid
        put(record, path_prefix + ['name_len'], len(name))
        o, _, _ = S.locate('ilc_settings', path_prefix + ['name'])
        record[o:o + len(name)] = name

    def test_character_rules(self):
        cid = S.field_id('ilc_seat_settings', 'character')
        r = self.rec(); put(r, ['seats', 0, 'character'], S.e('character', 'NONE'))
        self.reject(r, E['BAD_FIELD'], cid, 0)
        r = self.rec(); put(r, ['seats', 1, 'character'], 6)
        self.reject(r, E['BAD_FIELD'], cid, 1)
        r = self.rec(); put(r, ['seats', 3, 'character'], S.e('character', 'BASCH'))  # P2 already Basch
        self.reject(r, E['CONFLICT'], cid, 3)
        r = self.rec(); put(r, ['seats', 3, 'character'], S.e('character', 'VAAN'))
        self.assertEqual(self.push(r)[0], S.size('ilc_settings_ack'))  # Vaan is a regular (D-14 is open)
        r = self.rec(); put(r, ['seats', 1, 'character'], S.e('character', 'NONE'))
        self.assertGreaterEqual(self.push(r)[0], 0)

    def test_p1_cannot_be_parked_and_keeps_an_input(self):
        r = self.rec(); put(r, ['seats', 0, 'parked'], 1)
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_seat_settings', 'parked'), 0)
        r = self.rec(); put(r, ['seats', 0, 'device', 'kind'], 0)
        self.reject(r, E['CONFLICT'], S.field_id('ilc_settings', 'kbm_seat'))
        put(r, ['kbm_seat'], 1)
        self.gen += 1; put(r, ['header', 'generation'], self.gen)
        self.assertGreaterEqual(self.push(r)[0], 0)  # keyboard-only P1 is fine
        r = self.rec(); put(r, ['seats', 2, 'parked'], 1)
        self.assertGreaterEqual(self.push(r)[0], 0)

    def test_device_ownership_is_alias_aware(self):
        kind = S.field_id('ilc_seat_device', 'kind')
        r = self.rec(); put(r, ['seats', 2, 'device', 'kind'], 1)  # XInput 0, same as P1
        self.reject(r, E['CONFLICT'], kind, 2)
        r = self.rec(); put(r, ['seats', 2, 'device', 'kind'], 1); put(r, ['seats', 2, 'device', 'xinput_slot'], 1)
        self.assertGreaterEqual(self.push(r)[0], 0)
        # An SDL pad that is an alias of XInput 0 is P1's pad.
        r = self.rec()
        put(r, ['seats', 1, 'device', 'kind'], 2)
        self.sdl(r, ['seats', 1, 'device', 'sdl'], 0x11)
        put(r, ['alias_count'], 1)
        self.sdl(r, ['aliases', 0, 'sdl'], 0x11)
        self.reject(r, E['CONFLICT'], kind, 1)
        put(r, ['aliases', 0, 'xinput_slot'], 2)
        self.gen += 1; put(r, ['header', 'generation'], self.gen)
        self.assertGreaterEqual(self.push(r)[0], 0)
        # Two seats on the same SDL identity.
        r = self.rec()
        for seat in (1, 2):
            put(r, ['seats', seat, 'device', 'kind'], 2)
            self.sdl(r, ['seats', seat, 'device', 'sdl'], 0x22, name=b'Same pad')
        self.reject(r, E['CONFLICT'], kind, 2)
        # Same GUID, different ordinal: two identical models.
        put(r, ['seats', 2, 'device', 'sdl', 'ordinal'], 1)
        self.gen += 1; put(r, ['header', 'generation'], self.gen)
        self.assertGreaterEqual(self.push(r)[0], 0)

    def test_identity_rules(self):
        ident = 'ilc_device_identity'
        r = self.rec(); put(r, ['seats', 1, 'device', 'kind'], 2)  # SDL with an all-zero GUID
        self.reject(r, E['BAD_FIELD'], S.field_id(ident, 'guid'), 1)
        r = self.rec(); put(r, ['seats', 1, 'device', 'kind'], 2)
        self.sdl(r, ['seats', 1, 'device', 'sdl'], 0x33)
        o, _, _ = S.locate('ilc_settings', ['seats', 1, 'device', 'sdl', 'uid'])
        r[o] = 1  # uid without has_uid
        self.reject(r, E['BAD_FIELD'], S.field_id(ident, 'uid'), 1)
        for name, bad_at in ((b'Pad\x00x', 3), (b'Pa\x07d', 2), (b'\xc3\x28', 0), (b'\xed\xa0\x80', 0),
                             (b'ok\xc2\x85', 2), (b'\xf4\x90\x80\x80', 0), (b'abc\xe2\x82', 3)):
            r = self.rec(); put(r, ['seats', 1, 'device', 'kind'], 2)
            self.sdl(r, ['seats', 1, 'device', 'sdl'], 0x44, name=name)
            with self.subTest(name=name):
                self.reject(r, E['BAD_FIELD'], S.field_id(ident, 'name'), 1, bad_at)
        r = self.rec(); put(r, ['seats', 1, 'device', 'kind'], 2)
        self.sdl(r, ['seats', 1, 'device', 'sdl'], 0x55, name='Pad mit Umlaut ä ✓ 手柄'.encode())
        o, _, _ = S.locate('ilc_settings', ['seats', 1, 'device', 'sdl', 'name'])
        r[o + 90] = 0x41  # past name_len
        self.reject(r, E['BAD_FIELD'], S.field_id(ident, 'name'), 1, 90)
        r[o + 90] = 0
        self.gen += 1; put(r, ['header', 'generation'], self.gen)
        self.assertGreaterEqual(self.push(r)[0], 0)
        # Identity bytes on a non-SDL device.
        r = self.rec(); put(r, ['seats', 0, 'device', 'sdl', 'ordinal'], 1)
        self.reject(r, E['BAD_FIELD'], S.field_id(ident, 'ordinal'), 0)
        r = self.rec(); put(r, ['seats', 2, 'device', 'xinput_slot'], 2)  # slot without XINPUT
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_seat_device', 'xinput_slot'), 2)

    def test_alias_rules(self):
        r = self.rec(); put(r, ['alias_count'], 2)
        self.sdl(r, ['aliases', 0, 'sdl'], 0x66)
        self.sdl(r, ['aliases', 1, 'sdl'], 0x66)
        put(r, ['aliases', 1, 'xinput_slot'], 1)
        self.reject(r, E['CONFLICT'], S.field_id('ilc_device_alias', 'sdl'), LOC_ALIAS0 + 1)
        r = self.rec(); put(r, ['aliases', 3, 'xinput_slot'], 1)  # beyond alias_count
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_device_alias', 'xinput_slot'), LOC_ALIAS0 + 3)
        r = self.rec(); put(r, ['alias_count'], 1)  # counted alias with an empty identity
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_device_identity', 'guid'), LOC_ALIAS0)

    def test_binding_rules(self):
        bindings = S.field_id('ilc_seat_settings', 'bindings')
        confirm = S.e('action', 'CONFIRM') * 4
        flee = S.e('action', 'FLEE') * 4
        cases = [  # (seat, index, kind, code, expected code)
            (1, confirm + 3, 1, 0, E['BAD_FIELD']),            # key 0
            (1, confirm + 3, 2, 3, E['BAD_FIELD']),            # mouse 3
            (1, confirm + 3, 3, 25, E['BAD_FIELD']),           # pad control 25
            (1, confirm + 3, 0, 5, E['BAD_FIELD']),            # NONE with a code
            (1, confirm + 3, 1, 0x20, E['CONFLICT']),          # Space twice in one action
            (0, flee, 3, S.e('pad_control', 'RT'), E['BAD_FIELD']),  # P1 may not bind FLEE
        ]
        for seat, index, kind, code, expected in cases:
            r = self.rec()
            put(r, ['seats', seat, 'bindings', index, 'kind'], kind)
            put(r, ['seats', seat, 'bindings', index, 'code'], code)
            with self.subTest(seat=seat, index=index, kind=kind, code=code):
                self.reject(r, expected, bindings, seat, index)
        r = self.rec(); put(r, ['seats', 0, 'bindings', confirm + 2, 'kind'], 1); put(r, ['seats', 0, 'bindings', confirm + 2, 'code'], 0x0D)
        self.assertGreaterEqual(self.push(r)[0], 0)

    def test_kill_switch_rules(self):
        ks = S.field_id('ilc_settings', 'kill_switch')
        r = self.rec(); put(r, ['kill_switch', 1, 'kind'], 3); put(r, ['kill_switch', 1, 'code'], 1)
        self.reject(r, E['BAD_FIELD'], ks, LOC_KILL, 1)  # no pad on the global key
        r = self.rec(); put(r, ['kill_switch', 2, 'kind'], 1); put(r, ['kill_switch', 2, 'code'], 0x77)
        self.reject(r, E['CONFLICT'], ks, LOC_KILL, 2)

    def test_economy_rules(self):
        r = self.rec(); put(r, ['economy', 'version_minor'], 2)
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_settings', 'economy'))
        r = self.rec(); put(r, ['economy', 'present'], 1); put(r, ['economy', 'cap_x100'], 300)
        self.reject(r, E['BAD_FIELD'], S.field_id('ilc_economy', 'divisor_x100'))
        put(r, ['economy', 'divisor_x100'], 3000); put(r, ['economy', 'version_minor'], 2)
        self.gen += 1; put(r, ['header', 'generation'], self.gen)
        self.assertGreaterEqual(self.push(r)[0], 0)


@native_test
class ProtocolTest(BridgeCase):
    def test_header_and_length_errors(self):
        good = settings(1)
        cases = [
            (bytes(good[:-1]), None, E['BAD_LENGTH']),
            (bytes(good) + b'\0', None, E['BAD_LENGTH']),
            (bytes(good), 'magic', E['BAD_HEADER']),
            (bytes(good), 'abi_version', E['ABI_MISMATCH']),
            (bytes(good), 'record_type', E['BAD_TYPE']),
            (bytes(good), 'size', E['BAD_LENGTH']),
        ]
        for data, field, code in cases:
            record = bytearray(data)
            if field:
                offset, f, _ = S.locate('ilc_settings', ['header', field])
                struct.pack_into('<' + {2: 'H', 4: 'I'}[f['size']], record, offset,
                                 {'magic': 0x12345678, 'abi_version': 2, 'record_type': 5, 'size': SETTINGS_SIZE - 1}[field])
            with self.subTest(field=field, length=len(record)):
                status, _ = self.b.call(OP['SET_SETTINGS'], bytes(record))
                self.assertEqual(status, code)
        _, events = self.events()
        self.assertEqual([e['code'] for e in events], [c for _, _, c in cases])
        self.assertTrue(all(e['kind'] == S.e('event_kind', 'SETTINGS_REJECTED') for e in events))

    def test_argument_boundaries(self):
        # inLen exactly at the cap is not TOO_LARGE (it fails the op's length rule); one more is.
        big = bytes(S.const['MAX_RECORD'])
        self.assertEqual(self.b.call(OP['SET_SETTINGS'], big)[0], E['BAD_LENGTH'])
        self.assertEqual(self.b.call(OP['HELLO'], big)[0], E['BAD_LENGTH'])
        self.assertEqual(self.b.call(OP['SET_SETTINGS'], big + b'\0')[0], E['TOO_LARGE'])
        # header.size below inLen: refused, records must be exact.
        hello = bytes(S.encode('ilc_header', S.header('HELLO', 16)))
        self.assertEqual(self.b.call(OP['HELLO'], hello)[0], S.size('ilc_hello_reply'))
        short = bytearray(hello); struct.pack_into('<I', short, 8, 12)
        self.assertEqual(self.b.call(OP['HELLO'], bytes(short))[0], E['BAD_LENGTH'])
        record = settings(1); put(record, ['header', 'size'], SETTINGS_SIZE - 16)
        self.assertEqual(self.b.call(OP['SET_SETTINGS'], bytes(record))[0], E['BAD_LENGTH'])
        # Null pointers, query ops with input, unknown ops, small outputs.
        self.assertEqual(self.b.raw(OP['GET_STATUS'], None, 0, None, 4096), E['BAD_ARGS'])
        self.assertEqual(self.b.raw(OP['SET_SETTINGS'], None, SETTINGS_SIZE, None, 4096), E['BAD_ARGS'])
        for op in ('GET_STATUS', 'GET_SEATS', 'GET_DEVICES', 'POP_EVENTS', 'GET_SETTINGS', 'TEST_SAFE_POINT'):
            self.assertEqual(self.b.call(OP[op], b'\0' * 16)[0], E['BAD_LENGTH'], op)
        for op in (0, 9, 0x7F00, 0x7F02, 0xFFFFFFFF):
            self.assertEqual(self.b.call(op)[0], E['UNKNOWN_OP'])
        sizes = {'HELLO': 'ilc_hello_reply', 'GET_STATUS': 'ilc_status', 'GET_SEATS': 'ilc_seats_reply',
                 'GET_SETTINGS': 'ilc_settings', 'TEST_SAFE_POINT': 'ilc_safe_point_result'}
        for op, record in sizes.items():
            self.assertEqual(self.b.call(OP[op], out_cap=S.size(record) - 1)[0], E['OUT_TOO_SMALL'], op)
            self.assertEqual(self.b.call(OP[op], out_cap=S.size(record))[0], S.size(record), op)
        self.assertEqual(self.b.call(OP['SET_SETTINGS'], bytes(settings(1)), out_cap=31)[0], E['OUT_TOO_SMALL'])
        self.assertEqual(self.status()['accepted_generation'], 0, 'OUT_TOO_SMALL consumed the record')

    def test_commands_validate_then_answer_not_ready_or_run(self):
        valid = [('PARK', 0, 1, 0), ('PARK', 3, 0, 0), ('MOVE_SEAT_NOW', 2, 4, 0), ('PAIRING_START', 2, 77, 3),
                 ('PAIRING_CANCEL', 3, 0, 0), ('CAPTURE_START', 1, S.e('action', 'CONFIRM'), 3),
                 ('CAPTURE_START', 3, S.e('action', 'FLEE'), 0), ('CAPTURE_CANCEL', 2, 0, 0),
                 ('RESTORE_DEFAULTS', 0, 0, 0)]
        for cmd, seat, a0, a1 in valid:
            with self.subTest(cmd=cmd, seat=seat):
                self.assertEqual(self.b.call(OP['COMMAND'], command(cmd, seat, a0, a1))[0], E['NOT_READY'])
        # The kill switch is handled by the module itself (the co-op thread runs it next beat): accepted, not queued.
        for a0 in (1, 0):
            with self.subTest(cmd='KILL_SWITCH', arg0=a0):
                self.assertEqual(self.b.call(OP['COMMAND'], command('KILL_SWITCH', 0, a0, 0))[0], 0)
        self.assertEqual(self.status()['rejects'], 0, 'NOT_READY counted as a reject')
        cmd_fields = {n: S.field_id('ilc_command', n) for n in ('seat', 'arg0', 'arg1')}
        invalid = [('PARK', 1, 1, 0, 'seat'), ('PARK', 0, 2, 0, 'arg0'), ('MOVE_SEAT_NOW', 0, 1, 0, 'seat'),
                   ('MOVE_SEAT_NOW', 2, 6, 0, 'arg0'), ('PAIRING_START', 2, 0, 0, 'arg0'),
                   ('CAPTURE_START', 1, S.e('action', 'FLEE'), 0, 'arg0'), ('CAPTURE_START', 2, 19, 0, 'arg0'),
                   ('CAPTURE_START', 2, 0, 4, 'arg1'), ('PAIRING_CANCEL', 2, 1, 0, 'arg0'),
                   ('RESTORE_DEFAULTS', 1, 0, 1, 'arg1'), ('KILL_SWITCH', 1, 0, 0, 'seat')]
        for cmd, seat, a0, a1, field in invalid:
            with self.subTest(cmd=cmd, field=field):
                self.assertEqual(self.b.call(OP['COMMAND'], command(cmd, seat, a0, a1, request=40))[0], E['BAD_FIELD'])
                self.assertEqual(self.status()['last_reject_field'], cmd_fields[field])
        self.assertEqual(self.b.call(OP['COMMAND'], command('PARK', 0, 0, 0, request=0))[0], E['BAD_FIELD'])
        _, events = self.events()
        self.assertEqual(len(events), len(invalid) + 1)
        self.assertEqual({e['kind'] for e in events}, {S.e('event_kind', 'COMMAND_REJECTED')})
        self.assertEqual(events[0]['generation'], 40)

    def test_seats_and_devices_views(self):
        seats = S.decode('ilc_seats_reply', self.ok(OP['GET_SEATS']))
        f = S.enums['seat_flag']
        self.assertEqual((seats['session_state'], seats['count'], seats['header']['generation']), (0, 4, 0))
        self.assertEqual([v['flags'] for v in seats['seats']],
                         [f['HAS_DEVICE'] | f['PLAYING'] | f['LOCKED'], f['HAS_KBM'] | f['PLAYING'], 0, 0])
        self.assertEqual([v['live_member'] for v in seats['seats']], [0xFF] * 4)
        record = settings(1); put(record, ['seats', 1, 'parked'], 1)
        self.push(record); self.safe_point()
        seats = S.decode('ilc_seats_reply', self.ok(OP['GET_SEATS']))
        self.assertEqual(seats['seats'][1]['flags'], f['HAS_KBM'] | f['PARKED'])
        out = self.ok(OP['GET_DEVICES'])
        self.assertEqual(len(out), 32)
        devices = S.decode('ilc_devices_reply', out + bytes(S.size('ilc_devices_reply') - 32))
        self.assertEqual((devices['service_state'], devices['count'], devices['header']['size']),
                         (S.e('service_state', 'NOT_READY'), 0, 32))


@native_test
class EventQueueTest(BridgeCase):
    def test_reject_events_name_the_seat_only_for_seat_fields(self):
        cases = [  # (path, value, expected event seat)
            (['network_mode'], 1, 0),                          # settings level
            (['alias_count'], 17, 0),
            (['economy', 'present'], 2, 0),
            (['kill_switch', 1, 'kind'], 4, 0),                # binding, but in the kill switch
            (['aliases', 0, 'xinput_slot'], 1, 0),             # alias level
            (['seats', 0, 'colour'], 7, 1),                    # P1
            (['seats', 2, 'device', 'kind'], 3, 3),            # P3's device
            (['seats', 3, 'bindings', 5, 'kind'], 4, 4),       # P4's binding
            (['seats', 1, 'device', 'sdl', 'has_uid'], 2, 2),  # P2's identity
        ]
        for i, (path, value, seat) in enumerate(cases):
            record = settings(i + 1)
            put(record, path, value)
            self.assertEqual(self.push(record)[0], E['BAD_FIELD'], path)
        record = settings(50); put(record, ['header', 'generation'], 0)
        self.push(record)  # header-level reject
        _, events = self.events()
        self.assertEqual([e['seat'] for e in events], [seat for _, _, seat in cases] + [0])

    def test_bounded_drop_oldest_and_partial_pops(self):
        capacity = S.const['EVENT_QUEUE']
        bad = settings(1); put(bad, ['network_mode'], 1)
        for _ in range(capacity + 10):
            self.b.call(OP['SET_SETTINGS'], bytes(bad))
        st = self.status()
        self.assertEqual((st['events_pending'], st['events_dropped']), (capacity, 10))
        head, events = self.events(out_cap=32 + 5 * 32 + 31)  # room for 5 events, not 6
        self.assertEqual((head['count'], head['pending_after'], head['dropped_total']), (5, capacity - 5, 10))
        self.assertEqual([e['seq'] for e in events], list(range(11, 16)))  # 1..10 were dropped
        self.assertEqual(self.b.call(OP['POP_EVENTS'], out_cap=31)[0], E['OUT_TOO_SMALL'])
        seqs = [e['seq'] for e in events]
        while True:
            head, more = self.events()
            if not head['count']:
                break
            seqs += [e['seq'] for e in more]
        self.assertEqual(seqs, list(range(11, 11 + capacity)))
        self.assertEqual(self.status()['events_pending'], 0)


@native_test
class MemoryExecuteShapeTest(BridgeCase):
    """memory.execute passes 8 full 64-bit slots: op/inLen/outCap must fit in 32 bits, slots 5-7 are ignored."""

    def eight(self):
        u64, ptr = ctypes.c_uint64, ctypes.c_void_p
        return ctypes.CFUNCTYPE(ctypes.c_int32, u64, ptr, u64, ptr, u64, u64, u64, u64)(self.b.address)

    def test_slots_five_to_eight_are_ignored(self):
        fn = self.eight()
        record = bytes(settings(1))
        in_buf = ctypes.create_string_buffer(record, len(record))
        out = (ctypes.c_ubyte * 4096)()
        junk = (0xDEADBEEFCAFEF00D, 0x5A5A5A5A5A5A5A5A, 0xFFFFFFFFFFFFFFFF)
        self.assertEqual(fn(OP['SET_SETTINGS'], in_buf, len(record), ctypes.addressof(out), 4096, *junk),
                         S.size('ilc_settings_ack'))
        self.assertEqual(fn(OP['TEST_SAFE_POINT'], None, 0, ctypes.addressof(out), 4096, *junk),
                         S.size('ilc_safe_point_result'))
        self.assertEqual(fn(OP['GET_STATUS'], None, 0, ctypes.addressof(out), 4096, *junk), S.size('ilc_status'))
        self.assertEqual(S.decode('ilc_status', bytes(out))['applied_generation'], 1)

    def test_dirty_upper_halves_are_refused_and_write_nothing(self):
        fn = self.eight()
        record = bytes(settings(1))
        in_buf = ctypes.create_string_buffer(record, len(record))
        for op in (OP['HELLO'], OP['SET_SETTINGS'], OP['GET_STATUS'], OP['POP_EVENTS'], 9):
            data_len = len(record) if op == OP['SET_SETTINGS'] else 0
            for dirty in ((1 << 32, 0, 0), (0, 1 << 32, 0), (0, 0, 1 << 32), (0xDEADBEEF << 32, 0, 0)):
                out = (ctypes.c_ubyte * 4096)()
                ctypes.memset(out, CANARY, 4096)
                status = fn(op | dirty[0], in_buf if data_len else None, data_len | dirty[1],
                            ctypes.addressof(out), 4096 | dirty[2], 0, 0, 0)
                with self.subTest(op=op, dirty=[hex(d) for d in dirty]):
                    self.assertEqual(status, E['BAD_ARGS'])
                    self.assertEqual(bytes(out), bytes([CANARY]) * 4096)
        self.assertEqual(self.status()['accepted_generation'], 0)


@native_test
class FaultSafetyTest(BridgeCase):
    def test_unreadable_and_unwritable_buffers_give_fault(self):
        record = bytes(settings(1))
        with quiet_faulthandler():
            inbound = GuardedBuffer(len(record))
            out = GuardedBuffer(4096)
            try:
                inbound.write(record)
                inbound.protect_all(0x01)  # the whole record page becomes NOACCESS
                self.assertEqual(self.b.raw(OP['SET_SETTINGS'], inbound.address, len(record), out.address, 4096),
                                 E['FAULT'])
                inbound.protect_all(0x04)
                # A record whose claimed length runs into the guard page.
                self.assertEqual(self.b.raw(OP['SET_SETTINGS'], inbound.address + 8, len(record), out.address, 4096),
                                 E['FAULT'])
                out.protect_all(0x02)  # read-only output
                self.assertEqual(self.b.raw(OP['GET_STATUS'], None, 0, out.address, 4096), E['FAULT'])
                self.assertEqual(self.b.raw(OP['HELLO'], None, 0, out.address, 4096), E['FAULT'])
                out.protect_all(0x04)
                self.assertGreater(self.b.raw(OP['GET_STATUS'], None, 0, out.address, 4096), 0)
            finally:
                inbound.close()
                out.close()
        st = self.status()
        self.assertEqual(st['accepted_generation'], 0)
        self.assertEqual(st['last_reject_code'], E['FAULT'])

    def test_guard_pages_are_refused_without_being_touched(self):
        # A guard page belongs to its owner: the module must refuse it before reading, so the guard survives.
        k32 = GuardedBuffer._kernel32()

        class MBI(ctypes.Structure):
            _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                        ('AllocationProtect', ctypes.c_uint32), ('PartitionId', ctypes.c_uint16),
                        ('RegionSize', ctypes.c_size_t), ('State', ctypes.c_uint32),
                        ('Protect', ctypes.c_uint32), ('Type', ctypes.c_uint32)]

        k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
        k32.VirtualQuery.restype = ctypes.c_size_t

        def guarded(address) -> bool:
            info = MBI()
            self.assertEqual(k32.VirtualQuery(address, ctypes.byref(info), ctypes.sizeof(info)), ctypes.sizeof(info))
            return bool(info.Protect & 0x100)

        record = bytes(settings(1))
        inbound, out = GuardedBuffer(len(record)), GuardedBuffer(4096)
        try:
            inbound.write(record)
            inbound.protect_all(0x104)  # READWRITE | GUARD
            out.protect_all(0x104)
            with quiet_faulthandler():
                self.assertEqual(self.b.raw(OP['SET_SETTINGS'], inbound.address, len(record), out.address, 4096),
                                 E['FAULT'])
                self.assertEqual(self.b.raw(OP['GET_STATUS'], None, 0, out.address, 4096), E['FAULT'])
            self.assertTrue(guarded(inbound.address), 'the input guard page was consumed')
            self.assertTrue(guarded(out.address), 'the output guard page was consumed')
        finally:
            inbound.close()
            out.close()
        self.assertEqual(self.status()['accepted_generation'], 0)

    def test_guard_page_fuzz(self):
        rng = random.Random(0x1C0B)
        ops = list(OP.values()) + [0, 9, 0x7F00, rng.getrandbits(32)]
        valid = bytes(settings(1))
        cmd = command('KILL_SWITCH', 0, 1, 0, 5)
        accepted_records = []
        generation = 1
        counts = {}
        with quiet_faulthandler():
            for iteration in range(6000):
                op = rng.choice(ops)
                choice = rng.random()
                if choice < 0.35:
                    data = bytearray(valid)
                    generation += 1
                    struct.pack_into('<I', data, 12, generation)
                    for _ in range(rng.randint(1, 6)):
                        data[rng.randrange(16, len(data))] = rng.getrandbits(8)
                    if rng.random() < 0.1:
                        data[rng.randrange(0, 16)] = rng.getrandbits(8)
                    op = OP['SET_SETTINGS'] if rng.random() < 0.9 else op
                elif choice < 0.5:
                    data = bytearray(cmd)
                    data[rng.randrange(16, 32)] = rng.getrandbits(8)
                    op = OP['COMMAND'] if rng.random() < 0.9 else op
                elif choice < 0.6:
                    data = bytearray(valid[:rng.randrange(0, len(valid))])
                else:
                    data = bytearray(rng.getrandbits(8) for _ in range(rng.choice((0, 0, 16, 32, rng.randrange(0, 4096)))))
                in_len = len(data)
                if rng.random() < 0.05 and in_len:
                    in_len = rng.randrange(0, in_len)  # claim less than is there: must read only in_len
                out_cap = rng.choice((0, 16, 31, 32, 64, 95, 96, 111, 112, 3487, 3488, 3491, 3492, 4096, rng.randrange(0, 4097)))
                lead = rng.random() < 0.3
                inbound = GuardedBuffer(max(in_len, 1), lead=lead)
                out = GuardedBuffer(max(out_cap, 1), lead=lead)
                try:
                    inbound.write(bytes(data[:in_len]))
                    status = self.b.raw(op, inbound.address if in_len else None, in_len, out.address, out_cap)
                    written = out.read(max(status, 0)) if status > 0 else b''
                finally:
                    inbound.close()
                    out.close()
                counts[status] = counts.get(status, 0) + 1
                self.assertNotEqual(status, E['FAULT'], f'iteration {iteration}: read or write outside the buffers')
                self.assertTrue(status in ERROR_CODES or 0 <= status <= out_cap, f'iteration {iteration}: {status}')
                if op == OP['SET_SETTINGS'] and status >= 0:
                    accepted_records.append(bytes(data))
                if op == OP['COMMAND']:
                    self.assertLessEqual(status, 0)  # no command reply in v1: accepted (0, KILL) or an error
                if rng.random() < 0.05:
                    self.b.call(OP['TEST_SAFE_POINT'])
                del written
        # Property: nothing the module accepted has an out-of-range scalar or a non-zero reserved byte.
        for record in accepted_records:
            for path, f, owner in S.inbound_scalar_fields('ilc_settings'):
                offset = S.locate('ilc_settings', path)[0]
                values = record[offset:offset + f['size']]
                if f['kind'] == 'PAD':
                    self.assertEqual(values, bytes(f['size']), path)
                else:
                    fmt = '<' + {1: 'B', 2: 'H', 4: 'I'}[f['elemSize']] * f['count']
                    for v in struct.unpack(fmt, values):
                        self.assertTrue(f['min'] <= v <= f['max'], (path, v))
        self.assertGreater(sum(c for s, c in counts.items() if s < 0), 3000)
        st = self.status()
        self.assertEqual(st['accepted_generation'] >= st['applied_generation'], True)
        self.record_counts = counts


@native_test
class LoadTest(BridgeCase):
    def test_four_threads_poll_while_settings_change(self):
        threads, polls = 4, 10_000
        latencies = [[] for _ in range(threads)]
        regressions = []
        stop = threading.Event()
        proc = psutil.Process() if psutil else None
        handles_before = proc.num_handles() if proc else None
        mem_before = proc.memory_info().private if proc else None

        def poller(index):
            out = (ctypes.c_ubyte * 4096)()
            last = 0
            perf = time.perf_counter_ns
            for _ in range(polls):
                t0 = perf()
                status = self.b.raw(OP['GET_STATUS'], None, 0, ctypes.addressof(out), 4096)
                latencies[index].append(perf() - t0)
                if status != S.size("ilc_status"):
                    regressions.append(('status', status))
                    return
                applied = struct.unpack_from("<I", bytes(out[:16 + 32]), 40)[0]
                if applied < last:
                    regressions.append(('generation', last, applied))
                last = applied

        def writer():
            generation = 1
            out = (ctypes.c_ubyte * 4096)()
            while not stop.is_set():
                record = bytes(settings(generation))
                buf = ctypes.create_string_buffer(record, len(record))
                self.b.raw(OP['SET_SETTINGS'], buf, len(record), ctypes.addressof(out), 4096)
                self.b.raw(OP['TEST_SAFE_POINT'], None, 0, ctypes.addressof(out), 4096)
                generation += 1

        status_applied_at = S.field('ilc_status', 'applied_generation')['offset']
        self.assertEqual(status_applied_at, 40)
        workers = [threading.Thread(target=poller, args=(i,)) for i in range(threads)]
        w = threading.Thread(target=writer)
        w.start()
        for t in workers:
            t.start()
        for t in workers:
            t.join()
        stop.set()
        w.join()
        self.assertEqual(regressions, [])
        flat = sorted(x for lane in latencies for x in lane)
        p99 = flat[int(len(flat) * 0.99)] / 1e6
        self.assertLess(p99, 1.0, f'p99 {p99:.3f} ms')
        if proc:
            self.assertLessEqual(proc.num_handles() - handles_before, 8, 'handle count grew')
            self.assertLess(proc.memory_info().private - mem_before, 8 << 20, 'private bytes grew')
        st = self.status()
        self.assertGreater(st['applies'], 0)
        self.assertEqual(st['rejects'], 0)
        LoadTest.p99_ms = p99


@native_test
class StaticTest(unittest.TestCase):
    def test_no_thread_local_storage_in_sources(self):
        pattern = re.compile(r'\b(thread_local|_Thread_local|__thread)\b|__declspec\s*\(\s*thread\s*\)|emutls')
        for path in sorted((COOP / 'src').rglob('*')) + sorted((COOP / 'include').rglob('*')):
            if path.suffix in ('.h', '.cpp', '.c', '.S'):
                text = re.sub(r'//[^\n]*|/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
                self.assertIsNone(pattern.search(text), f'{path.name}: thread-local storage needs emutls/winpthreads')
        map_text = (built_dll().parent / 'ivalice-legacy-coop.map').read_text(errors='replace')
        self.assertNotIn('__emutls', map_text)
        self.assertNotIn('winpthread', map_text.lower())

    @unittest.skipUnless(capstone is not None, 'requires capstone')
    def test_probe_guard_covers_only_its_load_and_store(self):
        import audit_pe
        image = built_dll().read_bytes()
        pe = audit_pe.PE(image)
        symbols = {}
        for line in (built_dll().parent / 'ivalice-legacy-coop.map').read_text(errors='replace').splitlines():
            m = re.match(r'\s+0x([0-9a-f]{16})\s+(ilc_probe_\w+)\s*$', line)
            if m:
                symbols[m.group(2)] = int(m.group(1), 16) - pe.image_base
        begin, end = symbols['ilc_probe_guard_begin'], symbols['ilc_probe_guard_end']
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        listing = [f'{i.mnemonic} {i.op_str}' for i in md.disasm(image[pe.offset(begin):pe.offset(end)], begin)]
        self.assertEqual(listing, ['movzx r9d, byte ptr [rdx]', 'mov byte ptr [rcx], r9b'])
        fault = symbols['ilc_probe_fault']
        tail = [f'{i.mnemonic} {i.op_str}'.strip() for i in md.disasm(image[pe.offset(fault):pe.offset(fault) + 6], fault)]
        self.assertEqual(tail, ['mov eax, 1', 'ret'])

    def test_golden_dll_hash(self):
        golden = json.loads((COOP / 'golden.json').read_text(encoding='utf-8'))
        m = manifest()
        self.assertEqual(m['toolchain']['packages'], golden['packages'])
        self.assertEqual(m['sourceId'], golden['sourceId'], 'sources changed: run build.py --update-golden')
        self.assertEqual(m['schema']['sha256'], golden['schemaSha256'])
        self.assertEqual(m['dllSha256'], golden['dllSha256'], 'same sources and toolchain, different bytes')


if __name__ == '__main__':
    unittest.main()
