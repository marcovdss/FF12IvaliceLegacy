"""Source-pinned field edits to the action table (battle_pack section 14).

Section 14 is an ST2E table: u32 row count at +4, u16 row size at +8, u32 first-row offset at +12. One row per
action ID (543 in the Steam release); magick, Technick and item content link to their action by ID, and that link
is never changed here. Every edit states the value it expects to find, so a different game version, another mod's
data or a typo is rejected instead of patched. Only the listed bytes may change.

Ivalice Together reads some of these fields (target flags, MP, area radius, menu category) from its own private
catalogue, so a change to them is a deliberate experiment until the fork's catalogue matches.
"""
import hashlib
import struct

from .formats import battle_pack_section

# field: (byte offset in the row, struct format)
FIELDS = {'range': (5, 'B'), 'aoeRange': (6, 'B'), 'aoePositioningRange': (7, 'B'), 'formula': (8, 'B'),
          'chargeTime': (9, 'B'), 'power': (16, 'B')}
ACTION_COUNT = 543


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def layout(section):
    if section[:4] != b'st2e' or len(section) < 28:
        raise ValueError('expected the action ST2E table')
    count, size = struct.unpack_from('<IH', section, 4)
    entry = struct.unpack_from('<I', section, 12)[0]
    if count != ACTION_COUNT or size < 10 or entry + count * size > len(section):
        raise ValueError('unsupported action table layout')
    return count, size, entry


def action_fields(read, patch):
    """Apply patch['actions']: [{'id', 'name', 'set': {field: [before, after]}}]."""
    pack = read(patch['file'])
    if sha256(pack) != patch['sourceFileSha256']:
        raise ValueError(f"{patch['file']} differs from the supported game version")
    original = battle_pack_section(pack, patch['section'])
    if sha256(original) != patch['sourceSectionSha256']:
        raise ValueError(f"{patch['file']} section {patch['section']} differs from the supported game version")
    count, size, entry = layout(original)
    out = bytearray(original)
    changed, seen = set(), set()
    for action in patch['actions']:
        index = action['id']
        what = action.get('name', f'action {index}')
        if type(index) is not int or not 0 <= index < count:
            raise ValueError(f'{what}: not an action ID')
        if index in seen:
            raise ValueError(f'{what} listed twice')
        seen.add(index)
        at = entry + index * size
        for field, change in action['set'].items():
            if field not in FIELDS:
                raise ValueError(f'{what}: unsupported field {field}')
            if not isinstance(change, list) or len(change) != 2 or any(type(v) is not int for v in change):
                raise ValueError(f'{what} {field}: expected [before, after]')
            offset, fmt = FIELDS[field]
            if not 0 <= change[1] < 1 << 8 * struct.calcsize(fmt):
                raise ValueError(f'{what} {field}: {change[1]} does not fit')
            if struct.unpack_from(fmt, original, at + offset)[0] != change[0]:
                raise ValueError(f'{what} {field}: unexpected value (expected {change[0]})')
            struct.pack_into(fmt, out, at + offset, change[1])
            changed |= set(range(at + offset, at + offset + struct.calcsize(fmt)))
    result = bytes(out)
    if any(a != b for i, (a, b) in enumerate(zip(original, result)) if i not in changed) or len(result) != len(original):
        raise AssertionError('bytes outside the listed fields changed')
    if sha256(result) != patch['resultSectionSha256']:
        raise ValueError(f"section {patch['section']} result differs from the reviewed recipe")
    return {f"{patch['file']}.dir/section_{patch['section']:03d}.bin": result}
