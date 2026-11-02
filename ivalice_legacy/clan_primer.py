"""A Proficiency tip in the Clan Primer's Traveler's Tips (book 4, menuhandbook_tutorial*).

Files (us): menuhandbook_tutorial.bin "hctgf" = header (+0x0A u16 entry-table offset, +0x0E u16 entry count),
category title IDs, 12-byte entries {u32 0x10000 | title ID, u32 category, u32 0}, then a footer the Tips book does
not use; menuhandbook_tutorial.dat "hchkg" = u32 count, 8-byte checks in entry order; menuhandbook_tutorial000.dat
"hdatg" = +0x0A u16 count, +0x0C u32 -1, +0x10 u16 table {text base, one offset per record, 0}, 16-byte records
{u8 1 (strings), u8 0, u16 page, u32 text offset from the base, 8-byte layout}, then the text block (each text = u16
header offset 4, u16 0, body, a zero, padding to four bytes); penelosdiary.bin = the titles (row 661, ID 0x16E7, is
empty and unused).

A check is evaluated by 0x575E80: byte 0 (unless 0xFF) is a 4-bit value index that must equal byte 1; byte 2 (unless
0xFF) is a flag bit of the menu-info block (0x315620, 256 bits at +0x334C) that must be set; u16 +4 (unless 0xFFFF)
is a menu feature; u16 +6 (unless 0xFFFF) is the minimum story progress. No native check uses bytes 0-2, so the tip
waits on one flag bit that the Proficiency runtime sets while the Apprentice's Writ is owned.
"""
import hashlib
import struct

from .key_items import GLYPHS as KEY_GLYPHS, set_strings

BOOK = 'ps2data/image/ff12/myoshiok/us/handbook/menuhandbook_tutorial'
TITLES = 'ps2data/image/ff12/test_battle/us/binaryfile/penelosdiary.bin'
GLYPHS = {**KEY_GLYPHS, ':': 0xAA, '!': 0x99, '&': 0xA0}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(text):
    require(isinstance(text, str) and text and all(c in GLYPHS for c in text), f'Unsupported tip glyph in {text!r}')
    return bytes(GLYPHS[c] for c in text)


def check_tip(patch):
    tip = patch['tip']
    require(type(tip['titleRow']) is int and type(tip['titleId']) is int and 0 < tip['titleId'] < 0x10000,
            'Bad title row')
    require(tip['category'] in (0, 1, 2, 3), 'Tip category must be one of the four native categories')
    require(type(tip['flag']) is int and 0 <= tip['flag'] < 256, 'Tip flag must be a bit index 0-255')
    require(0 < len(tip['title']) <= 30, 'Bad tip title')
    lines = tip['body'].split('\n')
    require(0 < len(lines) <= 16 and all(len(line) <= 56 for line in lines), 'Tip body lines must be 56 characters or less')
    encode(tip['title']), encode(tip['body'])
    return tip


def tip_title(raw, patch):
    """penelosdiary.bin with the tip's title in its empty row."""
    require(sha(raw) == patch['titlesSha256'], 'Clan Primer titles source hash mismatch')
    tip = check_tip(patch)
    count, stride, start = struct.unpack_from('<III', raw, 4)
    row = struct.unpack_from('<HH', raw, start + stride * tip['titleRow'])
    require(row == (tip['titleId'], 1), 'Title row does not hold the expected ID')
    result = set_strings(raw, count, {tip['titleRow']: encode(tip['title'])}, 'Clan Primer titles')
    require(sha(result) == patch['resultSha256']['titles'], 'Clan Primer titles result hash mismatch')
    return result


def tip_list(raw, patch):
    """menuhandbook_tutorial.bin with the tip appended as the last entry."""
    require(sha(raw) == patch['listSha256'], 'Tips list source hash mismatch')
    tip = check_tip(patch)
    require(raw[:8] == b'hctgf\0\0\0' and raw[8] == 4, 'Expected the Traveler\'s Tips list')
    table, count = struct.unpack_from('<H', raw, 0x0A)[0], struct.unpack_from('<H', raw, 0x0E)[0]
    end = table + 12 * count
    require((table, count) == (0x30, 16) and raw[end:] == bytes(4), 'Unsupported Tips list layout')
    ids = {struct.unpack_from('<I', raw, table + 12 * i)[0] for i in range(count)}
    require(0x10000 | tip['titleId'] not in ids, 'Tip title already listed')
    out = bytearray(raw[:end]) + struct.pack('<III', 0x10000 | tip['titleId'], tip['category'], 0) + raw[end:]
    struct.pack_into('<H', out, 0x0E, count + 1)
    result = bytes(out)
    require(sha(result) == patch['resultSha256']['list'], 'Tips list result hash mismatch')
    return result


def tip_checks(raw, patch):
    """menuhandbook_tutorial.dat with the tip's check: only its flag bit."""
    require(sha(raw) == patch['checksSha256'], 'Tips checks source hash mismatch')
    tip = check_tip(patch)
    require(raw[:8] == b'hchkg\0\0\0', 'Expected HCHKG')
    count = struct.unpack_from('<I', raw, 8)[0]
    require(count == 16 and len(raw) == 12 + 8 * count, 'Unsupported Tips checks layout')
    check = bytes((0xFF, 0xFF, tip['flag'], 0x00)) + b'\xff\xff\xff\xff'
    out = bytearray(raw) + check
    struct.pack_into('<I', out, 8, count + 1)
    result = bytes(out)
    require(sha(result) == patch['resultSha256']['checks'], 'Tips checks result hash mismatch')
    return result


def tip_pages(raw, patch):
    """menuhandbook_tutorial000.dat with one more page record and the tip's text at the end of the text block."""
    require(sha(raw) == patch['pagesSha256'], 'Tips pages source hash mismatch')
    tip = check_tip(patch)
    require(raw[:8] == b'hdatg\0\0\0' and raw[12:16] == b'\xff\xff\xff\xff', 'Expected HDATG')
    count = struct.unpack_from('<H', raw, 0x0A)[0]
    table = [struct.unpack_from('<H', raw, 0x10 + 2 * i)[0] for i in range(count + 2)]
    base, records, terminator = table[0], table[1:-1], table[-1]
    require(count == 16 and terminator == 0 and records == [0x10 + 2 * (count + 2) + 16 * i for i in range(count)]
            and base == records[-1] + 16, 'Unsupported Tips pages layout')
    recs = [raw[r:r + 16] for r in records]
    for i, rec in enumerate(recs):
        require(rec[0] == 1 and rec[1] == 0 and struct.unpack_from('<H', rec, 2)[0] == i, f'Unexpected page record {i}')
    block = raw[base:]
    require(len(block) % 4 == 0, 'Unaligned Tips text block')
    text = struct.pack('<HH', 4, 0) + encode(tip['body']) + bytes(1)  # header offset 4; newline glyph 0x02
    text += bytes(-len(text) % 4)
    new = bytearray(recs[-1])
    struct.pack_into('<HI', new, 2, count, len(block))
    count += 1
    head = bytearray(raw[:0x10])
    struct.pack_into('<H', head, 0x0A, count)
    table_size = 2 * (count + 2)
    first = 0x10 + table_size + (-(0x10 + table_size) % 4)
    new_records = [first + 16 * i for i in range(count)]
    new_base = new_records[-1] + 16
    out = head + struct.pack(f'<{count + 2}H', new_base, *new_records, 0)
    out += bytes(first - len(out))
    out += b''.join(recs) + bytes(new) + block + text
    result = bytes(out)
    require(sha(result) == patch['resultSha256']['pages'], 'Tips pages result hash mismatch')
    return result


def build_files(archive, patch):
    deploy = 'mods/deploy/ff12data/'
    return {
        deploy + BOOK + '.bin': tip_list(archive.read(BOOK + '.bin'), patch),
        deploy + BOOK + '.dat': tip_checks(archive.read(BOOK + '.dat'), patch),
        deploy + BOOK + '000.dat': tip_pages(archive.read(BOOK + '000.dat'), patch),
        deploy + TITLES: tip_title(archive.read(TITLES), patch),
    }
