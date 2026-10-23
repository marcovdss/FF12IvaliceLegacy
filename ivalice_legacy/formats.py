"""Section containers used by the patched files.

battle_pack.bin: u32 section count, then count + 1 u32 offsets.
EBP field scripts: 'EBP2' header, twenty u32 section offsets at +16; section 0 is the script.
The External File Loader replaces a whole section with a loose file at <file>.dir/section_NNN.bin.
"""
import struct


def battle_pack_section(pack, index):
    count = struct.unpack_from('<I', pack)[0]
    offsets = struct.unpack_from(f'<{count + 1}I', pack, 4)
    if not 0 <= index < count or offsets != tuple(sorted(offsets)) or offsets[-1] > len(pack):
        raise ValueError('unexpected battle_pack layout')
    return pack[offsets[index]:offsets[index + 1]]


def ebp_section(raw, index):
    if raw[:4] != b'EBP2':
        raise ValueError('not an EBP2 file')
    offsets = struct.unpack_from('<20I', raw, 16)
    start = offsets[index]
    if not start:
        raise ValueError(f'EBP section {index} is empty')
    end = min([x for x in offsets if x > start] + [len(raw)])
    return raw[start:end]


def shop_table(section):
    """Shop data (battle_pack section 39): shops -> stock rows ('events') -> item lists.

    Each table header is 6 magic bytes, u16 count, u32 offset. A stock row has a condition code, an offset
    parameter, a u16 threshold and a pointer to its item list. The game selects the first row whose
    condition holds and sells that row plus every later row; the last row is the unconditional baseline.
    """
    def header(pos, magic, stride):
        if section[pos:pos + 6] != magic:
            raise ValueError(f'bad shop table header at {pos:#x}')
        count, offset = struct.unpack_from('<HI', section, pos + 6)
        if offset + count * stride > len(section):
            raise ValueError('shop table out of range')
        return count, offset

    count, base = header(0, b'\x64\x00\x00\x00\x08\x00', 8)
    shops = []
    for i in range(count):
        pos = base + 8 * i
        owner, _, event_table = struct.unpack_from('<HHI', section, pos)
        n_events, events = header(event_table, b'\x64\x01\x00\x00\x08\x00', 8)
        rows = []
        for j in range(n_events):
            ep = events + 8 * j
            code, offset_param, threshold, list_header = struct.unpack_from('<BBHI', section, ep)
            n_items, items = header(list_header, b'\x64\x02\x00\x00\x02\x00', 2)
            rows.append({'row': j, 'rowOffset': ep, 'condition': code, 'conditionOffset': offset_param,
                         'threshold': threshold, 'listHeader': list_header,
                         'items': list(struct.unpack_from(f'<{n_items}H', section, items))})
        shops.append({'shop': i, 'offset': pos, 'owner': owner, 'rows': rows})
    return shops


def ard_section(raw, index):
    """Return the complete AR03 section, including its trailing bytes."""
    if len(raw) < 48 or raw[:8] != b'FF12AR03':
        raise ValueError('not an AR03 file')
    offsets = struct.unpack_from('<10I', raw, 8)
    if not 0 <= index < 10 or not offsets[index]:
        raise ValueError('missing AR03 section')
    if any(offset and not 48 <= offset < len(raw) for offset in offsets):
        raise ValueError('AR03 section offset out of range')
    start = offsets[index]
    return raw[start:min([offset for offset in offsets if offset > start] + [len(raw)])]


def ai_rule_offset(section, script, row):
    """Locate a zero-based 24-byte battle-logic rule after its 32 group counts."""
    if len(section) < 4:
        raise ValueError('truncated AI bank')
    count = struct.unpack_from('<H', section)[0]
    if not 0 <= script < count or 4 + (count + 1) * 4 > len(section):
        raise ValueError('invalid AI script directory')
    offsets = struct.unpack_from(f'<{count + 1}i', section, 4)
    start, end = offsets[script:script + 2]
    if not 4 + (count + 1) * 4 <= start <= end <= len(section) or start + 32 > end:
        raise ValueError('invalid AI script bounds')
    rules = sum(section[start:start + 32])
    if start + 32 + rules * 24 > end or not 0 <= row < rules:
        raise ValueError('invalid AI rule bounds')
    return start + 32 + row * 24
