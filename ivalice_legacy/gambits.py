"""Gambit labels, help text and link repairs, plus renames in the same word-table format.

Text is applied through the record indices the gambit table actually references:
a name index is not a row number. Unselected strings keep their exact bytes, and
every condition, target type, price, page and order survives except the listed fixes.
"""
import hashlib
import struct

from .technicks import help_layout, help_strings

GLYPHS = {' ': 4, '\n': 2, '%': 0x9E, '/': 0xA2, '+': 0xA4, '-': 0xA5, '=': 0xA6, ',': 0xA7, '.': 0xA8,
          ':': 0xAA, "'": 0xAC, '<': 0xB2, '≥': 0xC4,
          **{str(i): 0x85 + i for i in range(10)},
          **{chr(65 + i): 0x20 + i for i in range(26)},
          **{chr(97 + i): 0x3A + i for i in range(26)}}
NAME_LIMIT = 32
HELP_LINE_LIMIT = 48
HELP_LINES = 2
TABLE_ROWS = 256
TABLE_STRIDE = 32
HELP_BASE = 16000
PLURAL_S = b'\x09' + bytes([GLYPHS['s']])


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(text):
    require(isinstance(text, str) and text, 'Expected nonempty text')
    require(all(c in GLYPHS for c in text), f'Unsupported glyph in {text!r}')
    return bytes(GLYPHS[c] for c in text)


def validate_name(name):
    require(0 < len(name) <= NAME_LIMIT and '\n' not in name, f'Gambit name does not fit: {name!r}')


def validate_help(text):
    lines = text.split('\n')
    require(len(lines) <= HELP_LINES and all(0 < len(line) <= HELP_LINE_LIMIT for line in lines),
            f'Gambit help does not fit: {text!r}')


def _entries(table):
    count = struct.unpack_from('<I', table)[0]
    require(count and 4 + count * 4 <= len(table), 'Invalid word table')
    slots = list(struct.unpack_from(f'<{count}I', table, 4)) + [len(table)]
    require(all(a <= b for a, b in zip(slots, slots[1:])), 'Word table entries are out of order')
    return count, slots, [table[slots[i]:slots[i + 1]] for i in range(count)]


def _parts(entry):
    pieces = entry[2:].split(b'\0')
    require(len(pieces) >= 4 and not any(pieces[3:]), 'Unexpected word entry layout')
    return entry[:2], pieces[0], pieces[1], pieces[2]


def _join(prefix, name, plural, article):
    raw = prefix + name + b'\0' + plural + b'\0' + article + b'\0'
    return raw + bytes(-len(raw) % 4)


def word_name(table, index):
    return _parts(_entries(table)[2][index])[1]


def replace_names(table, names, *, regular_forms=()):
    """Rebuild a word table; regular_forms also gives new names a plain plural and article."""
    count, slots, entries = _entries(table)
    for index, name in names.items():
        require(type(index) is int and 0 <= index < count, 'Invalid word index')
        prefix, _, plural, article = _parts(entries[index])
        if index in regular_forms:
            plural = PLURAL_S
            article = encode('an ' if name[0].lower() in 'aeiou' else 'a ')
        entries[index] = _join(prefix, encode(name), plural, article)
    out = bytearray(table[:slots[0]])
    offsets = []
    for raw in entries:
        offsets.append(len(out))
        out += raw
    struct.pack_into(f'<{count}I', out, 4, *offsets)
    result = bytes(out)
    _, _, updated = _entries(result)
    untouched = set(range(count)) - set(names)
    require(all(_entries(table)[2][i] == updated[i] for i in untouched), 'Unselected word entry changed')
    return result


def replace_help_text(raw, replacements):
    """Rewrite the string block in order; unselected strings keep their exact bytes."""
    old = help_strings(raw)
    text, sub, offsets = help_layout(raw)
    for index, value in replacements.items():
        require(type(index) is int and 0 <= index < len(old), 'Invalid help record')
        validate_help(value)
    out = bytearray(raw[:sub + offsets[0]])
    for index, value in enumerate(old):
        struct.pack_into('<I', out, sub + 4 + index * 4, len(out) - sub)
        out += (encode(replacements[index]) if index in replacements else value) + b'\0'
    size = len(out) - sub
    struct.pack_into('<I', out, sub + 4 + len(old) * 4, size)
    struct.pack_into('<I', out, text + 8, 12 + size)
    out += bytes(-size % 4)
    result = bytes(out)
    updated = help_strings(result)
    require(all(updated[i] == old[i] for i in range(len(old)) if i not in replacements),
            'Unselected help string changed')
    return result


def table_rows(raw):
    count, stride = struct.unpack_from('<IH', raw, 4)
    start = struct.unpack_from('<I', raw, 12)[0]
    require((count, stride) == (TABLE_ROWS, TABLE_STRIDE) and start + count * stride <= len(raw),
            'Unexpected gambit table')
    return start


def apply_field_corrections(raw, corrections):
    start = table_rows(raw)
    out = bytearray(raw)
    for fix in corrections:
        position = start + fix['row'] * TABLE_STRIDE + fix['offset']
        require(struct.unpack_from('<H', out, position)[0] == fix['before'],
                f"Gambit row {fix['row']} is not the reviewed source")
        struct.pack_into('<H', out, position, fix['after'])
    return bytes(out)


def build(table, names, help_file, patch):
    """Return the patched gambit table, name table and help file after full validation."""
    pins = patch['sourcePins']
    for key, raw in (('table', table), ('names', names), ('help', help_file)):
        require(sha(raw) == pins[key]['sha256'], f'Gambit {key} source changed; review before patching')
    records = patch['records']
    require(len({r['row'] for r in records}) == len(records), 'Repeated gambit row')
    require(len({r['nameIndex'] for r in records}) == len(records), 'Repeated gambit name')
    require(len({r['helpIndex'] for r in records}) == len(records), 'Repeated gambit help')
    start = table_rows(table)
    fixed = apply_field_corrections(table, patch['fieldCorrections'])
    for record in records:
        row = start + record['row'] * TABLE_STRIDE
        require(struct.unpack_from('<H', fixed, row + 20)[0] & 0x7FF == record['nameIndex'], 'Gambit name link mismatch')
        require(struct.unpack_from('<H', fixed, row + 4)[0] == HELP_BASE + record['helpIndex'], 'Gambit help link mismatch')
        validate_name(record['name'])
    new_names = replace_names(names, {r['nameIndex']: r['name'] for r in records})
    new_help = replace_help_text(help_file, {r['helpIndex']: r['help'] for r in records})
    return fixed, new_names, new_help
