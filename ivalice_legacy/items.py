"""Source-pinned price edits to the consumable item table (battle_pack section 18).

Section 18 is an ST2E table of 64 rows (u32 row count at +4, u16 row size at +8, u32 first-row offset at +12). Each
row is one item content ID: u8 sort, three unknown bytes, u8 icon, two unknown bytes, u8 help link, u16 action ID,
u16 price. Only the price is edited here; the action link, sort and icon never change. Every edit states the price it
expects, so another mod's table or a different game version is rejected instead of patched.
"""
import struct

ITEM_COUNT = 64
PRICE = 10


def layout(section):
    if section[:4] != b'st2e' or len(section) < 28:
        raise ValueError('expected the item ST2E table')
    count, size = struct.unpack_from('<IH', section, 4)
    entry = struct.unpack_from('<I', section, 12)[0]
    if count != ITEM_COUNT or size < 12 or entry + count * size > len(section):
        raise ValueError('unsupported item table layout')
    return count, size, entry


def edit_prices(original, edits):
    """edits: [{'id', 'name', 'gil': [before, after]}]. Returns the table with only those prices changed."""
    count, size, entry = layout(original)
    out = bytearray(original)
    seen = set()
    for item in edits:
        index, what = item['id'], item.get('name', f"item {item['id']}")
        if type(index) is not int or not 0 <= index < count:
            raise ValueError(f'{what}: not an item ID')
        if index in seen:
            raise ValueError(f'{what} listed twice')
        seen.add(index)
        if set(item) - {'id', 'name', 'gil'}:
            raise ValueError(f'{what}: only prices can be edited')
        before, after = item['gil']
        if type(before) is not int or type(after) is not int or not 0 <= after <= 0xFFFF:
            raise ValueError(f'{what}: expected [before, after] prices')
        at = entry + index * size + PRICE
        if struct.unpack_from('<H', original, at)[0] != before:
            raise ValueError(f'{what}: unexpected price (expected {before})')
        struct.pack_into('<H', out, at, after)
    result = bytes(out)
    allowed = {entry + i * size + PRICE + k for i in seen for k in (0, 1)}
    if any(a != b for i, (a, b) in enumerate(zip(original, result)) if i not in allowed):
        raise AssertionError('bytes outside the listed prices changed')
    return result
