"""Tips in the Clan Primer's Traveler's Tips (book 4, menuhandbook_tutorial*).

Files (us): menuhandbook_tutorial.bin "hctgf" = header (+0x0A u16 entry-table offset, +0x0E u16 entry count),
category title IDs, 12-byte entries {u32 0x10000 | title ID, u32 category, u32 0}, then a footer the Tips book does
not use; menuhandbook_tutorial.dat "hchkg" = u32 count, 8-byte checks in entry order; menuhandbook_tutorial000.dat
"hdatg" = +0x0A u16 count, +0x0C u32 -1, +0x10 u16 table {text base, one offset per record, 0}, 16-byte records
{u8 1 (strings), u8 0, u16 page, u32 text offset from the base, 8-byte layout}, then the text block (each text = u16
header offset 4, u16 0, body, a zero, padding to four bytes); penelosdiary.bin = the titles.

A patch carries one tip under 'tip' or several under 'tips'; all are appended in one pass, in list order, to
whatever the sources already hold: the 16 native entries, or the 17-entry result of proficiency_tip.json deployed
on this machine. Source and result SHA-256 pin every patch to the exact file state it extends; a null result hash
marks a draft to be pinned (and then re-verified) before the patch is used or shipped.

Titles go into penelosdiary rows that are empty AND unreferenced (free_title_rows): row 661 (ID 0x16E7) holds the
Proficiency tip; rows 654-657 (IDs 0x16E0-0x16E3) hold the four Weapon Families tips. Both allocations were found
the same way: the row's string is empty and its ID appears in no handbook book's entry or category tables. The
0x16E0-0x16FB ID run serves the Clan Primer books; rows 658-659 (IDs 0x16E4-0x16E5) are the bestiary book's two
category titles and are NOT free, and row 660 (ID 0x16E6) is the last spare of the run.

A check is evaluated by 0x575E80: byte 0 (unless 0xFF) is a 4-bit value index that must equal byte 1; byte 2
(unless 0xFF) is a flag bit of the menu-info block (0x315620, 256 bits at +0x334C) that must be set; u16 +4
(unless 0xFFFF) is a menu feature; u16 +6 (unless 0xFFFF) is the minimum story progress. 0xFF is the format's own
no-test convention: a tip whose check byte 2 is 0xFF (patch flag 255) runs no flag test — an ungated tip, shown
unconditionally. No native check uses bytes 0-2. The Proficiency tip waits on flag bit 254, set by its runtime
while the Apprentice's Writ is owned; the Weapon Families tips are ungated (flag 255).
"""
import hashlib
import struct

from .key_items import GLYPHS as KEY_GLYPHS, set_strings

BOOK = 'ps2data/image/ff12/myoshiok/us/handbook/menuhandbook_tutorial'
TITLES = 'ps2data/image/ff12/test_battle/us/binaryfile/penelosdiary.bin'
DEPLOY = 'mods/deploy/ff12data/'
SOURCES = (BOOK + '.bin', BOOK + '.dat', BOOK + '000.dat', TITLES)
GLYPHS = {**KEY_GLYPHS, ':': 0xAA, '!': 0x99, '&': 0xA0}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(text):
    require(isinstance(text, str) and text and all(c in GLYPHS for c in text), f'Unsupported tip glyph in {text!r}')
    return bytes(GLYPHS[c] for c in text)


def check_tip(tip):
    require(type(tip['titleRow']) is int and type(tip['titleId']) is int and 0 < tip['titleId'] < 0x10000,
            'Bad title row')
    require(tip['category'] in (0, 1, 2, 3), 'Tip category must be one of the four native categories')
    require(type(tip['flag']) is int and 0 <= tip['flag'] <= 255,
            'Tip flag must be a bit index 0-254, or 255 for an ungated tip (check byte 0xFF = no flag test)')
    require(0 < len(tip['title']) <= 30, 'Bad tip title')
    lines = tip['body'].split('\n')
    require(0 < len(lines) <= 16 and all(len(line) <= 56 for line in lines), 'Tip body lines must be 56 characters or less')
    encode(tip['title']), encode(tip['body'])
    return tip


def tips(patch):
    """The patch's tips in append order ('tips' list, or the single legacy 'tip'), all validated."""
    raw = patch.get('tips', [patch['tip']] if 'tip' in patch else [])
    require(isinstance(raw, list) and raw and all(isinstance(t, dict) for t in raw),
            'Patch must hold one tip (tip) or a non-empty list of tips (tips)')
    out = [check_tip(t) for t in raw]
    rows = [t['titleRow'] for t in out]
    ids = [t['titleId'] for t in out]
    require(len(set(rows)) == len(rows) and len(set(ids)) == len(ids), 'Duplicate tip title row or ID')
    return out


def pin(patch, key, result):
    """Enforce the patch's recorded result hash; a null marks a draft patch, to be pinned before shipping."""
    declared = patch['resultSha256'][key]
    require(declared is None or declared == sha(result), f'Tips {key} result hash mismatch')
    return result


def free_title_rows(titles, books):
    """penelosdiary rows that are empty and whose title ID no handbook book lists.

    A row is only usable for a new tip title if the game nowhere reads its ID: an empty row whose ID some book
    lists would hand that book's entry our new text. `books` are the raw bytes of every handbook book (.bin); only
    "hctgf" books have entry and category title tables (u32 0x10000 | ID at +0x0A table, +0x18 categories).
    """
    count, stride, start, zero, text = struct.unpack_from('<IIIII', titles, 4)
    require((stride, start, zero) == (4, 32, 0) and text == start + count * stride, 'Unsupported titles layout')
    require(struct.unpack_from('<II', titles, text) == (1, 12), 'Unsupported titles text block')
    sub_at = text + 12
    offsets = [struct.unpack_from('<I', titles, sub_at + 4 + 4 * i)[0] for i in range(count + 1)]
    require(all(o < offsets[-1] for o in offsets[:-1]), 'Titles offset outside its strings')
    used = set()
    for raw in books:
        require(raw[:8] == b'hctgf\0\0\0', 'free_title_rows expects hctgf books')
        table = struct.unpack_from('<H', raw, 0x0A)[0]
        entries, cats = struct.unpack_from('<H', raw, 0x0E)[0], struct.unpack_from('<H', raw, 0x12)[0]
        require(0x18 + 4 * cats <= table and table + 12 * entries <= len(raw), 'Unsupported book layout')
        used |= {struct.unpack_from('<I', raw, table + 12 * i)[0] for i in range(entries)}
        used |= {struct.unpack_from('<I', raw, 0x18 + 4 * i)[0] for i in range(cats)}
    ids = [struct.unpack_from('<HH', titles, start + stride * i)[0] for i in range(count)]
    return [i for i in range(count) if titles[sub_at + offsets[i]] == 0 and (0x10000 | ids[i]) not in used]


def tip_title(raw, patch):
    """penelosdiary.bin with every tip's title in its empty row (one pass, strings appended at the end)."""
    require(sha(raw) == patch['titlesSha256'], 'Clan Primer titles source hash mismatch')
    strings = {}
    for tip in tips(patch):
        count, stride, start = struct.unpack_from('<III', raw, 4)
        row = struct.unpack_from('<HH', raw, start + stride * tip['titleRow'])
        require(row == (tip['titleId'], 1), 'Title row does not hold the expected ID')
        strings[tip['titleRow']] = encode(tip['title'])
    result = set_strings(raw, count, strings, 'Clan Primer titles')
    return pin(patch, 'titles', result)


def tip_list(raw, patch):
    """menuhandbook_tutorial.bin with the tips appended as the last entries."""
    require(sha(raw) == patch['listSha256'], 'Tips list source hash mismatch')
    new = tips(patch)
    require(raw[:8] == b'hctgf\0\0\0' and raw[8] == 4, 'Expected the Traveler\'s Tips list')
    table, count = struct.unpack_from('<H', raw, 0x0A)[0], struct.unpack_from('<H', raw, 0x0E)[0]
    end = table + 12 * count
    require(table == 0x30 and count >= 16 and raw[end:] == bytes(4), 'Unsupported Tips list layout')
    ids = {struct.unpack_from('<I', raw, table + 12 * i)[0] for i in range(count)}
    appended = b''
    for tip in new:
        require(0x10000 | tip['titleId'] not in ids, 'Tip title already listed')
        ids.add(0x10000 | tip['titleId'])
        appended += struct.pack('<III', 0x10000 | tip['titleId'], tip['category'], 0)
    out = bytearray(raw[:end]) + appended + raw[end:]
    struct.pack_into('<H', out, 0x0E, count + len(new))
    return pin(patch, 'list', bytes(out))


def tip_checks(raw, patch):
    """menuhandbook_tutorial.dat with every tip's check appended. flag 255 writes check byte 2 = 0xFF, the
    format's no-flag-test convention: an ungated tip."""
    require(sha(raw) == patch['checksSha256'], 'Tips checks source hash mismatch')
    new = tips(patch)
    require(raw[:8] == b'hchkg\0\0\0', 'Expected HCHKG')
    count = struct.unpack_from('<I', raw, 8)[0]
    require(count >= 16 and len(raw) == 12 + 8 * count, 'Unsupported Tips checks layout')
    out = bytearray(raw) + b''.join(bytes((0xFF, 0xFF, tip['flag'], 0x00)) + b'\xff\xff\xff\xff' for tip in new)
    struct.pack_into('<I', out, 8, count + len(new))
    return pin(patch, 'checks', bytes(out))


def tip_pages(raw, patch):
    """menuhandbook_tutorial000.dat with one more page record per tip and the tips' texts at the end of the text
    block."""
    require(sha(raw) == patch['pagesSha256'], 'Tips pages source hash mismatch')
    new = tips(patch)
    require(raw[:8] == b'hdatg\0\0\0' and raw[12:16] == b'\xff\xff\xff\xff', 'Expected HDATG')
    count = struct.unpack_from('<H', raw, 0x0A)[0]
    table = [struct.unpack_from('<H', raw, 0x10 + 2 * i)[0] for i in range(count + 2)]
    base, records, terminator = table[0], table[1:-1], table[-1]
    table_size = 2 * (count + 2)
    records_start = 0x10 + table_size + (-(0x10 + table_size) % 4)  # the offset table is padded to four bytes
    require(count >= 16 and terminator == 0 and records == [records_start + 16 * i for i in range(count)]
            and base == records[-1] + 16, 'Unsupported Tips pages layout')
    recs = [raw[r:r + 16] for r in records]
    for i, rec in enumerate(recs):
        require(rec[0] == 1 and rec[1] == 0 and struct.unpack_from('<H', rec, 2)[0] == i, f'Unexpected page record {i}')
    block = raw[base:]
    require(len(block) % 4 == 0, 'Unaligned Tips text block')
    total = count + len(new)
    head = bytearray(raw[:0x10])
    struct.pack_into('<H', head, 0x0A, total)
    table_size = 2 * (total + 2)
    first = 0x10 + table_size + (-(0x10 + table_size) % 4)
    new_records = [first + 16 * i for i in range(total)]
    new_base = new_records[-1] + 16
    out = head + struct.pack(f'<{total + 2}H', new_base, *new_records, 0)
    out += bytes(first - len(out))
    added_records, added_text = bytearray(), bytearray()
    at = len(block)
    for j, tip in enumerate(new):
        text = struct.pack('<HH', 4, 0) + encode(tip['body']) + bytes(1)  # header offset 4; newline glyph 0x02
        text += bytes(-len(text) % 4)
        rec = bytearray(recs[-1])
        struct.pack_into('<HI', rec, 2, count + j, at)
        added_records += rec
        added_text += text
        at += len(text)
    out += b''.join(recs) + bytes(added_records) + block + bytes(added_text)
    return pin(patch, 'pages', bytes(out))


def build_files(read, patch):
    """{deploy path: bytes} for the four handbook/title files, sources read through `read(rel)`.

    `read` decides where the sources come from: an archive's read for the vanilla 16-entry state (the Proficiency
    package), or a reader that prefers the deployed loose files for patches that extend it (build-tips).
    """
    return {
        DEPLOY + BOOK + '.bin': tip_list(read(BOOK + '.bin'), patch),
        DEPLOY + BOOK + '.dat': tip_checks(read(BOOK + '.dat'), patch),
        DEPLOY + BOOK + '000.dat': tip_pages(read(BOOK + '000.dat'), patch),
        DEPLOY + TITLES: tip_title(read(TITLES), patch),
    }
