"""Licence-board help for the job stat trainings, written into the word table's help sub-table in place.

The board shows a licence's effect through its augment: battle pack section 12 grants the augment, section 58 row
+2 holds the augment's help reference (sub-table = ref >> 11, record = ref & 0x7FF), section 61 sub-table 10 holds the
text. Each record is u16 flags, glyph bytes, a 00 terminator and padding. New texts reuse their record's existing
slot, so no offset in the table moves.
"""
import hashlib
import struct

HELP_SUBTABLE = 10
GLYPHS = {' ': 4, '+': 166, ',': 167, '.': 168, **{str(i): 133 + i for i in range(10)},
          **{chr(65 + i): 32 + i for i in range(26)}, **{chr(97 + i): 58 + i for i in range(26)}}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(text):
    require(isinstance(text, str) and text and all(c in GLYPHS for c in text), f'Unsupported help glyph in {text!r}')
    return bytes(GLYPHS[c] for c in text)


def help_record(section58, augment):
    count, stride, start = struct.unpack_from('<III', section58, 4)
    require(section58[:4] == b'st2e' and stride == 8 and 0 <= augment < count, 'Unsupported augment table')
    ref = struct.unpack_from('<H', section58, start + stride * augment + 2)[0]
    require(ref >> 11 == HELP_SUBTABLE, f'Augment {augment} help is not in sub-table {HELP_SUBTABLE}')
    return ref & 0x7FF


def license_help(section58, section61, patch, steps):
    """steps: training steps with 'augmentBit' and 'help'. Returns the rewritten help sub-table."""
    require(sha(section61) == patch['sourceSha256'], 'Word table source hash mismatch')
    tables = struct.unpack_from('<I', section61, 0)[0]
    bounds = [struct.unpack_from('<I', section61, 4 + 4 * i)[0] for i in range(tables)] + [len(section61)]
    table = bytearray(section61[bounds[HELP_SUBTABLE]:bounds[HELP_SUBTABLE + 1]])
    entries = struct.unpack_from('<I', table, 0)[0]
    require(entries == 129, 'Unsupported help table')
    slots = [struct.unpack_from('<I', table, 4 + 4 * i)[0] for i in range(entries)] + [len(table)]
    changed, seen = set(), set()
    for step in steps:
        index = help_record(section58, step['augmentBit'])
        require(index not in seen, f'Help record {index} written twice')
        seen.add(index)
        start, end = slots[index], slots[index + 1]
        body = table[start:end]
        require(0 in body[2:], f'Help record {index} has no terminator')
        record = bytes(body[:2]) + encode(step['help']) + b'\0'
        require(len(record) <= end - start, f"Help for {step['name']} does not fit its {end - start}-byte slot")
        table[start:end] = record + bytes(end - start - len(record))
        changed |= set(range(start, end))
    original = section61[bounds[HELP_SUBTABLE]:bounds[HELP_SUBTABLE + 1]]
    require({i for i, (a, b) in enumerate(zip(original, table)) if a != b} <= changed, 'Undeclared help byte changed')
    result = bytes(table)
    require(sha(result) == patch['resultSha256'], 'License help result hash mismatch')
    return result
