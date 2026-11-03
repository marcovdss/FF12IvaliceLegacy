"""Proficiency Writs as real key items: names, Key Items menu descriptions and the section-35 item rows.

Key items are content IDs 0x8000 + index. Their records are battle pack sections 33, 34, 35 and 36 in that order
(64 + 32 + 416 + 0 rows), so index 496 is section 35 row 400. A section-35 row is u16 index (0 = unused), u16 sort,
u8 icon, u8 0, u16 help link, u16 name link. Names are battle pack section 61 sub-table 12 (entry = two zero bytes,
name, plural and article, each ended by a zero, padded to four bytes). The Key Items menu description of index i is
record i of listhelp_daijinamono.bin (st2e rows, then a one-sub-table text block whose records are offsets to
shared zero-terminated strings). Every other record keeps its exact bytes; the help links of section 35 are left alone (they are shared
with licence descriptions).
"""
import hashlib
import struct

PACK = 'ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'
LISTHELP = 'ps2data/image/ff12/test_battle/us/binaryfile/listhelp_daijinamono.bin'
NAMES_SUBTABLE = 12
SECTION35_FIRST_INDEX = 96
KEY_ITEM_ICON = 0x72          # the icon of most native key items (Tomaj's writ among them)
GLYPHS = {' ': 4, "'": 0xAC, ',': 167, '.': 168, '-': 165, '\n': 2,
          **{str(i): 133 + i for i in range(10)},
          **{chr(65 + i): 32 + i for i in range(26)}, **{chr(97 + i): 58 + i for i in range(26)}}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(text):
    require(isinstance(text, str) and text and all(c in GLYPHS for c in text), f'Unsupported key-item glyph in {text!r}')
    return bytes(GLYPHS[c] for c in text)


def check_writs(patch):
    items = patch['items']
    require(len(items) == 3 and len({i['index'] for i in items}) == 3, 'Expected three Writs')
    for item in items:
        require(type(item['index']) is int and 400 <= item['index'] < 512, 'Writ index out of range')
        require(item['contentId'] == 0x8000 + item['index'], 'Writ content ID must be 0x8000 + index')
        require(0 < len(item['name']) <= 24 and item['article'] in ('a', 'an'), f"Bad Writ name {item['name']!r}")
        lines = item['description'].split('\n')
        require(1 <= len(lines) <= 2 and all(0 < len(line) <= 56 for line in lines), f"Bad description for {item['name']}")
        encode(item['name']), encode(item['description'])
    return items


def _subtable(section61, index):
    tables = struct.unpack_from('<I', section61, 0)[0]
    bounds = [struct.unpack_from('<I', section61, 4 + 4 * i)[0] for i in range(tables)] + [len(section61)]
    return section61[bounds[index]:bounds[index + 1]]


def key_item_names(section61, patch):
    """Sub-table 12 of the word table, rebuilt with new offsets and the Writ names."""
    require(sha(section61) == patch['wordTableSha256'], 'Word table source hash mismatch')
    table = _subtable(section61, NAMES_SUBTABLE)
    count = struct.unpack_from('<I', table, 0)[0]
    require(count == 512, 'Unsupported key-item name table')
    slots = [struct.unpack_from('<I', table, 4 + 4 * i)[0] for i in range(count + 1)]
    require(slots[0] == 4 + 4 * (count + 1) and slots[-1] == len(table), 'Unsupported key-item name header')
    require(all(a < b and a % 4 == 0 for a, b in zip(slots, slots[1:])), 'Unsupported key-item name offsets')
    entries = [table[slots[i]:slots[i + 1]] for i in range(count)]
    for item in check_writs(patch):
        body = b'\0\0' + encode(item['name']) + b'\0' + b'\0' + encode(item['article'] + ' ') + b'\0'
        entries[item['index']] = body + bytes(-len(body) % 4)
    out = bytearray(table[:slots[0]])
    offsets = []
    for entry in entries:
        offsets.append(len(out))
        out += entry
    struct.pack_into(f'<{count + 1}I', out, 4, *offsets, len(out))
    result = bytes(out)
    require(sha(result) == patch['resultSha256']['names'], 'Key-item name result hash mismatch')
    return result


def key_item_rows(section35, patch):
    """Section 35 with the Writ rows marked in use (their own index) and given the native key-item icon."""
    require(sha(section35) == patch['section35Sha256'], 'Section 35 source hash mismatch')
    require(section35[:4] == b'st2e', 'Expected ST2E section 35')
    count, stride, start = struct.unpack_from('<III', section35, 4)
    require((count, stride) == (416, 10), 'Unsupported section 35 layout')
    out = bytearray(section35)
    for item in check_writs(patch):
        row = item['index'] - SECTION35_FIRST_INDEX
        at = start + stride * row
        index, sort, icon, zero, help_link, name_link = struct.unpack_from('<HHBBHH', section35, at)
        require(index == 0 and icon == 0 and zero == 0, f"Writ row {row} is already in use")
        require(name_link == 0x6000 | item['index'], f'Writ row {row} does not name key item {item["index"]}')
        struct.pack_into('<HHBBHH', out, at, item['index'], sort, KEY_ITEM_ICON, 0, help_link, name_link)
    result = bytes(out)
    require(sha(result) == patch['resultSha256']['section35'], 'Section 35 result hash mismatch')
    return result


def set_strings(raw, rows, strings, what):
    """An ST2E file whose rows are followed by a one-sub-table text block (listhelp_*.bin, penelosdiary.bin):
    point the given (empty) records at new zero-terminated strings appended after the last string.

    Records are offsets to zero-terminated strings and strings may be shared (empty records point at an empty
    string; some texts are reused), so only the changed records' offsets, the end fields and the appended bytes
    differ from the source.
    """
    require(raw[:4] == b'st2e', f'Expected ST2E {what}')
    count, stride, start, zero, text = struct.unpack_from('<IIIII', raw, 4)
    require((count, stride, start, zero) == (rows, 4, 32, 0) and text == start + count * stride, f'Unsupported {what}')
    tables, first, end = struct.unpack_from('<III', raw, text)
    require((tables, first) == (1, 12), f'Unsupported {what} text block')
    sub_at = text + first
    n = struct.unpack_from('<I', raw, sub_at)[0]
    offsets = [struct.unpack_from('<I', raw, sub_at + 4 + 4 * i)[0] for i in range(n + 1)]
    data_end = offsets[-1]
    require(n == rows and offsets[0] == 4 + 4 * (n + 1) and end == first + data_end, f'Unsupported {what} records')
    require(all(offsets[0] <= o < data_end for o in offsets[:-1]), f'{what} offset outside its strings')
    require(len(raw) == sub_at + data_end + (-data_end) % 4 and not any(raw[sub_at + data_end:]), f'Unexpected {what} tail')
    sub = bytearray(raw[sub_at:sub_at + data_end])
    for i, data in sorted(strings.items()):
        require(0 <= i < n and raw[sub_at + offsets[i]] == 0, f'{what} record {i} is not empty')
        require(data and 0 not in data, f'Bad {what} string for record {i}')
        struct.pack_into('<I', sub, 4 + 4 * i, len(sub))
        sub += data + b'\0'
    struct.pack_into('<I', sub, 4 + 4 * n, len(sub))
    out = bytearray(raw[:sub_at])
    struct.pack_into('<I', out, text + 8, first + len(sub))
    out += sub + bytes((-len(sub)) % 4)
    return bytes(out)


def key_item_help(raw, patch):
    """listhelp_daijinamono.bin with the Writ descriptions appended (see set_strings)."""
    require(sha(raw) == patch['listhelpSha256'], 'Key-item help source hash mismatch')
    strings = {item['index']: encode(item['description']) for item in check_writs(patch)}
    result = set_strings(raw, 512, strings, 'key-item help')
    require(sha(result) == patch['resultSha256']['listhelp'], 'Key-item help result hash mismatch')
    return result


def build_files(archive, patch, pack_section):
    """{deploy path: bytes} for the three Writ resources, all read from the pinned archive."""
    packed = archive.read(PACK)
    deploy = 'mods/deploy/ff12data/'
    return {
        deploy + PACK + '.dir/section_061.bin.dir/section_012.bin': key_item_names(pack_section(packed, 61), patch),
        deploy + PACK + '.dir/section_035.bin': key_item_rows(pack_section(packed, 35), patch),
        deploy + LISTHELP: key_item_help(archive.read(LISTHELP), patch),
    }


PROFICIENCY_VERSION = '0.3.13'
PROFICIENCY_LUA = {
    'x64/scripts/config/TheInsurgentsForgeConfig/functions/165.lua': 'prototypes/forge_proficiency_165.lua',
    'x64/scripts/IvaliceLegacy/proficiency.lua': 'prototypes/proficiency.lua',
    'x64/scripts/IvaliceLegacy/clan_rank.lua': 'prototypes/clan_rank.lua',
    'x64/scripts/IvaliceLegacy/proficiency_runtime.lua': 'prototypes/proficiency_runtime.lua',
    **{f'x64/scripts/config/TheInsurgentsForgeConfig/functions/{n}.lua': f'prototypes/forge_proficiency_{n}.lua'
       for n in (240, 110, 170, 182, 22)},
}


def build(game, out):
    """Proficiency package: the runtime and Forge adapters, the three Writ key-item resources and the Clan Primer
    tip (four handbook/title files)."""
    import json
    from pathlib import Path
    from .__main__ import ROOT, write_package
    from .formats import battle_pack_section
    from .vbf import VBF
    patch = json.loads((ROOT/'patches/proficiency_writs.json').read_text(encoding='utf-8'))
    archive = VBF(Path(game)/'FFXII_TZA.vbf')
    files = {rel: (ROOT/src).read_bytes() for rel, src in PROFICIENCY_LUA.items()}
    files.update(build_files(archive, patch, battle_pack_section))
    from . import clan_primer
    tip = json.loads((ROOT/'patches/proficiency_tip.json').read_text(encoding='utf-8'))
    files.update(clan_primer.build_files(archive, tip))

    stem = f'IvaliceLegacy-Proficiency-{PROFICIENCY_VERSION}'
    return write_package(files, out, stem, 'Ivalice Legacy Proficiency', PROFICIENCY_VERSION,
                         {'writs': [{k: i[k] for k in ('contentId', 'name')} for i in patch['items']]})
