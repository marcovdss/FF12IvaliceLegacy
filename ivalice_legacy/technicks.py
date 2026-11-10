"""Technick coverage and append-only native action-help updates.

Only string pointers for the selected records and text-container end fields may
change. Shared strings, record IDs, targets, prices and all other text survive.
"""
import hashlib
import struct

from .key_items import GLYPHS as BASE_GLYPHS

GLYPHS = {**BASE_GLYPHS, '%': 158}


def help_layout(raw):
    if raw[:4] != b'st2e' or len(raw) < 32:
        raise ValueError('Expected ST2E action help')
    count, stride, start, zero, text = struct.unpack_from('<IIIII', raw, 4)
    if (stride, start, zero, text) != (4, 32, 0, 32 + count * 4):
        raise ValueError('Unsupported action-help records')
    tables, first, end = struct.unpack_from('<III', raw, text)
    if (tables, first) != (1, 12):
        raise ValueError('Unsupported action-help text container')
    sub = text + first
    n = struct.unpack_from('<I', raw, sub)[0]
    offsets = struct.unpack_from(f'<{n + 1}I', raw, sub + 4)
    if n != count or offsets[0] != 4 + 4 * (n + 1) or end != first + offsets[-1]:
        raise ValueError('Unsupported action-help strings')
    data_end = sub + offsets[-1]
    if data_end > len(raw) or any(raw[data_end:]) or len(raw) - data_end > 3:
        raise ValueError('Unexpected action-help tail')
    if any(not offsets[0] <= o < offsets[-1] for o in offsets[:-1]):
        raise ValueError('Action-help pointer outside strings')
    return text, sub, offsets


def help_strings(raw):
    _, sub, offsets = help_layout(raw)
    result = []
    for offset in offsets[:-1]:
        candidate = raw[sub + offset:sub + offsets[-1]]
        if b'\0' not in candidate:
            raise ValueError('Unterminated action-help string')
        result.append(candidate.split(b'\0', 1)[0])
    return result


def replace_help(raw, replacements, expected_sha256):
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Action-help source changed; review before patching')
    old = help_strings(raw)
    text, sub, offsets = help_layout(raw)
    out = bytearray(raw[:sub + offsets[-1]])
    for index, value in sorted(replacements.items()):
        if type(index) is not int or not 0 <= index < len(old):
            raise ValueError('Invalid action-help record')
        if not isinstance(value, str) or not 0 < len(value) <= 48 or '\n' in value:
            raise ValueError('Action help must fit a compact 48-glyph line')
        try:
            encoded = bytes(GLYPHS[c] for c in value)
        except KeyError as error:
            raise ValueError('Unsupported action-help glyph') from error
        struct.pack_into('<I', out, sub + 4 + index * 4, len(out) - sub)
        out += encoded + b'\0'
    size = len(out) - sub
    struct.pack_into('<I', out, sub + 4 + len(old) * 4, size)
    struct.pack_into('<I', out, text + 8, 12 + size)
    out += bytes((-size) % 4)
    result = bytes(out)
    updated = help_strings(result)
    if any(updated[i] != old[i] for i in range(len(old)) if i not in replacements):
        raise ValueError('Unselected action-help string changed')
    return result


def validate_registry(registry):
    rows = registry['commands']
    if len(rows) != 24 or {r['action'] for r in rows} != set(range(158, 182)):
        raise ValueError('All 24 native Technicks must be accounted for once')
    if len({r['index'] for r in rows}) != 24 or any(r['action'] != r['index'] + 158 for r in rows):
        raise ValueError('Technick identity mismatch')
    for row in rows:
        if row['decision'] not in ('keep', 'existing', 'improve') or not row['reason']:
            raise ValueError('Every Technick needs a reasoned decision')
        if not 0 < len(row['help']) <= 48 or any(c not in GLYPHS for c in row['help']):
            raise ValueError('Invalid compact Technick help')
    return rows
