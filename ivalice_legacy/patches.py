"""Apply the patch definitions in patches/*.json to unmodified game files."""
from collections import Counter
import hashlib
import struct

from .formats import battle_pack_section, ebp_section, shop_table


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(data, expected, what):
    actual = sha256(data)
    if actual != expected:
        raise ValueError(f'{what} differs from the supported game version (sha256 {actual[:16]}…, '
                         f'expected {expected[:16]}…). Build from an unmodified, up-to-date Steam install.')


def opening_stock(read, patch):
    """Make each listed item part of its shop's unconditional baseline row.

    Listed items are removed from the shop's later (story-gated) rows so they are not offered twice, and
    appended to the baseline row in the given order. Changed item lists are written after the existing data
    (4-byte aligned) and their headers re-pointed; every other byte, including all row conditions, is kept.
    """
    pack = read(patch['file'])
    require(pack, patch['sourceFileSha256'], patch['file'])
    original = battle_pack_section(pack, patch['section'])
    require(original, patch['sourceSectionSha256'], f"{patch['file']} section {patch['section']}")
    shops = shop_table(original)
    header_users = Counter(row['listHeader'] for shop in shops for row in shop['rows'])
    out = bytearray(original)

    def write_items(row, items):
        if items == row['items']:
            return
        header = row['listHeader']
        if header_users[header] != 1:
            raise ValueError('refusing to edit an item list shared by several rows')
        struct.pack_into('<H', out, header + 6, len(items))
        if items:
            while len(out) % 4:
                out.append(0)
            struct.pack_into('<I', out, header + 8, len(out))
            out.extend(struct.pack(f'<{len(items)}H', *items))

    for entry in patch['shops']:
        shop = shops[entry['shop']]
        wanted = [item['id'] for item in entry['availableFromStart']]
        *later, baseline = shop['rows']
        if baseline['condition'] != 0:
            raise ValueError(f"shop {entry['shop']} has no unconditional baseline row")
        for row in later:
            write_items(row, [i for i in row['items'] if i not in wanted])
        write_items(baseline, baseline['items'] + [i for i in wanted if i not in baseline['items']])
    while len(out) % 16:
        out.append(0)
    result = bytes(out)
    check_opening_stock(original, result, patch)
    return {f"{patch['file']}.dir/section_{patch['section']:03}.bin": result}


def check_opening_stock(original, result, patch):
    before, after = shop_table(original), shop_table(result)
    touched = {entry['shop'] for entry in patch['shops']}
    for b, a in zip(before, after):
        if result[b['offset']:b['offset'] + 8] != original[b['offset']:b['offset'] + 8]:
            raise AssertionError(f"shop {b['shop']} header changed")
        for rb, ra in zip(b['rows'], a['rows']):
            if result[rb['rowOffset']:rb['rowOffset'] + 8] != original[rb['rowOffset']:rb['rowOffset'] + 8]:
                raise AssertionError(f"shop {b['shop']} row {rb['row']} condition changed")
        if b['shop'] not in touched and [r['items'] for r in b['rows']] != [r['items'] for r in a['rows']]:
            raise AssertionError(f"untouched shop {b['shop']} changed")
    for entry in patch['shops']:
        rows = after[entry['shop']]['rows']
        for item in entry['availableFromStart']:
            if item['id'] not in rows[-1]['items'] or any(item['id'] in r['items'] for r in rows[:-1]):
                raise AssertionError(f"{item['name']} not placed once in shop {entry['shop']}'s baseline")
    if patch.get('resultSectionSha256'):
        require(result, patch['resultSectionSha256'], 'opening stock result')


def ability_chests(read, patch):
    """Set each listed chest's spawn chance; one byte per chest, everything else unchanged."""
    files = {}
    for chest in patch['chests']:
        raw = read(chest['file'])
        require(raw, chest['sourceFileSha256'], chest['file'])
        section = bytearray(ebp_section(raw, chest['section']))
        require(bytes(section), chest['sourceSectionSha256'], f"{chest['file']} section {chest['section']}")
        at = chest['sectionByteOffset']
        if section[at] != chest['spawnChanceBefore']:
            raise ValueError(f"{chest['ability']} chest: unexpected spawn chance {section[at]}")
        section[at] = chest['spawnChanceAfter']
        result = bytes(section)
        if chest.get('resultSectionSha256'):
            require(result, chest['resultSectionSha256'], f"{chest['ability']} chest result")
        files[f"{chest['file']}.dir/section_{chest['section']:03}.bin"] = result
    return files


PUSH_IMM16 = 0x4F  # field-script VM: push signed 16-bit immediate (opcode byte + little-endian operand)


def kings(read, patch):
    """Change pushed 16-bit constants in field scripts (spawn-chance thresholds and similar).

    Every edit names the instruction's position in the script section; the opcode and the old value must match
    before anything changes, so a moved or different script is rejected instead of patched blindly.
    """
    files = {}
    for king in patch['kings']:
        raw = read(king['file'])
        require(raw, king['sourceFileSha256'], king['file'])
        section = bytearray(ebp_section(raw, king['section']))
        require(bytes(section), king['sourceSectionSha256'], f"{king['file']} section {king['section']}")
        for edit in king['immediates']:
            at = edit['sectionByteOffset']
            if section[at] != PUSH_IMM16 or struct.unpack_from('<h', section, at + 1)[0] != edit['before']:
                raise ValueError(f"{king['king']}: unexpected instruction at {at:#x}")
            struct.pack_into('<h', section, at + 1, edit['after'])
        rel = f"{king['file']}.dir/section_{king['section']:03}.bin"
        if rel in files:
            raise ValueError(f'{rel} edited twice')
        files[rel] = bytes(section)
    return files


APPLIERS = {'opening_stock': opening_stock, 'ability_chests': ability_chests, 'kings': kings}
