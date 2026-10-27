"""Renames in the equipment-name sub-table of the word table (battle pack section 61, sub-table 1).

Each entry is two zero bytes, the name, its plural form and its article, each ended by a zero, padded to four bytes.
Renamed entries keep a "pair of" form; others take "a"/"an" by the new name's first letter and a plain "s" plural.
The sub-table is rebuilt with new offsets, so a name may be longer than the one it replaces.
"""
import hashlib
import struct

from .job_labels import encode_label

NAMES_SUBTABLE = 1
PLURAL_S = b'\x09' + encode_label('s')[:-1]


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def subtable(section61, index):
    tables = struct.unpack_from('<I', section61, 0)[0]
    bounds = [struct.unpack_from('<I', section61, 4 + 4 * i)[0] for i in range(tables)] + [len(section61)]
    return section61[bounds[index]:bounds[index + 1]]


def entry(name, plural, article):
    raw = b'\0\0' + encode_label(name) + plural + b'\0' + article + b'\0'
    return raw + bytes(-len(raw) % 4)


def rename(old, name):
    require(old[:2] == b'\0\0', 'Unexpected name entry prefix')
    parts = old[2:].split(b'\0')
    _, plural, article = parts[0], parts[1], parts[2]
    pair = encode_label('a pair of')[:-1]
    if article.startswith(pair):
        return entry(name, plural, article)
    article = encode_label('an' if name[0].lower() in 'aeiou' else 'a')[:-1] + bytes([4])  # trailing space glyph
    return entry(name, PLURAL_S, article)


def item_names(section61, patch):
    """Return sub-table 1 with the patch's equipment names replaced."""
    require(sha(section61) == patch['sourceSha256'], 'Word table source hash mismatch')
    table = subtable(section61, NAMES_SUBTABLE)
    count = struct.unpack_from('<I', table, 0)[0]
    slots = [struct.unpack_from('<I', table, 4 + 4 * i)[0] for i in range(count)] + [len(table)]
    entries = [table[slots[i]:slots[i + 1]] for i in range(count)]
    seen = set()
    for change in patch['changes']:
        i = change['index']
        require(0 <= i < count and i not in seen, 'Invalid or repeated name index')
        seen.add(i)
        require(entries[i].hex() == change['beforeHex'], f'Unexpected name entry {i}')
        entries[i] = rename(entries[i], change['replacement'])
    head = table[:slots[0]]
    out = bytearray(head)
    offsets = []
    for raw in entries:
        offsets.append(len(out))
        out += raw
    struct.pack_into(f'<{count}I', out, 4, *offsets)
    result = bytes(out)
    require(sha(result) == patch['resultSha256'], 'Equipment name result hash mismatch')
    return result
