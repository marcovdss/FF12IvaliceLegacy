"""Source-pinned edits to the party-member templates (battle_pack section 16).

Section 16 is an ST2E table: u32 row count at +4, u16 row size at +8, u32 first-row offset at +12. Each 0x80-byte row
is one party member (index = member id 0..39). Row +0x0A..+0x13 holds the five starting equipment slots as u16 content
ids: 0 weapon, 1 off-hand, 2 head, 3 body, 4 accessory (0xFFFF = empty). The game copies them into the member's record
when the record is initialised, and a guest's gear returns to the inventory from that record when the guest leaves, so a
template slot decides which item a guest carries and hands over.

Every edit states the value it expects to find, and nothing but the named slots may change.
"""
import hashlib
import struct

from .formats import battle_pack_section

SLOTS = ('weapon', 'offhand', 'head', 'body', 'accessory')
EQUIPMENT_AT = 0x0A
EMPTY = 0xFFFF


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def parse(section):
    """Split the table into its header, rows and tail; serialize() joins them back byte for byte."""
    if section[:4] != b'st2e' or len(section) < 16:
        raise ValueError('expected the party-member ST2E table')
    count, size = struct.unpack_from('<IH', section, 4)
    entry = struct.unpack_from('<I', section, 12)[0]
    if size < EQUIPMENT_AT + 2 * len(SLOTS) or entry + count * size > len(section):
        raise ValueError('unsupported party-member table layout')
    rows = [section[entry + i * size: entry + (i + 1) * size] for i in range(count)]
    return {'head': section[:entry], 'rows': rows, 'tail': section[entry + count * size:], 'size': size}


def serialize(table):
    return table['head'] + b''.join(table['rows']) + table['tail']


def equipment_of(row):
    return dict(zip(SLOTS, struct.unpack_from('<5H', row, EQUIPMENT_AT)))


def party_templates(read, patch):
    """Apply patch['members']: [{'member': id, 'name': str, 'set': {slot: [before, after]}}]."""
    pack = read(patch['file'])
    if sha256(pack) != patch['sourceFileSha256']:
        raise ValueError(f"{patch['file']} differs from the supported game version")
    original = battle_pack_section(pack, patch['section'])
    if sha256(original) != patch['sourceSectionSha256']:
        raise ValueError(f"{patch['file']} section {patch['section']} differs from the supported game version")
    table = parse(original)
    if serialize(table) != original:
        raise AssertionError('party-member table does not rebuild byte for byte')
    rows = list(table['rows'])
    seen = set()
    for change in patch.get('members', []):
        member = change['member']
        what = change.get('name', f'member {member}')
        if type(member) is not int or not 0 <= member < len(rows) or member in seen:
            raise ValueError(f'{what}: invalid or repeated member')
        seen.add(member)
        row = bytearray(rows[member])
        for slot, edit in change['set'].items():
            if slot not in SLOTS:
                raise ValueError(f'{what}: unknown slot {slot}')
            if (not isinstance(edit, list) or len(edit) != 2
                    or any(type(v) is not int or not 0 <= v <= EMPTY for v in edit)):
                raise ValueError(f'{what} {slot}: expected [before, after] content ids')
            at = EQUIPMENT_AT + 2 * SLOTS.index(slot)
            if struct.unpack_from('<H', row, at)[0] != edit[0]:
                raise ValueError(f'{what} {slot}: unexpected value (expected {edit[0]:#x})')
            struct.pack_into('<H', row, at, edit[1])
        allowed = {EQUIPMENT_AT + 2 * SLOTS.index(s) + k for s in change['set'] for k in (0, 1)}
        if {i for i, (a, b) in enumerate(zip(rows[member], row)) if a != b} - allowed:
            raise AssertionError(f'{what}: undeclared byte changed')
        rows[member] = bytes(row)
    result = serialize({**table, 'rows': rows})
    if len(result) != len(original):
        raise AssertionError('party-member table changed size')
    if patch.get('resultSectionSha256') and sha256(result) != patch['resultSectionSha256']:
        raise ValueError('party-member result differs from the recorded build')
    return {f"{patch['file']}.dir/section_{patch['section']:03}.bin": result}
