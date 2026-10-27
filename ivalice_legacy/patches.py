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
    """Small same-size edits to field scripts: pushed 16-bit constants (spawn-chance thresholds) and whole
    instructions (for example a call replaced by a push of the same length).

    Every edit names the instruction's position in the script section; the old bytes must match before anything
    changes, so a moved or different script is rejected instead of patched blindly.
    """
    files = {}
    for king in patch['kings']:
        raw = read(king['file'])
        require(raw, king['sourceFileSha256'], king['file'])
        section = bytearray(ebp_section(raw, king['section']))
        require(bytes(section), king['sourceSectionSha256'], f"{king['file']} section {king['section']}")
        for edit in king.get('immediates', []):
            at = edit['sectionByteOffset']
            if section[at] != PUSH_IMM16 or struct.unpack_from('<h', section, at + 1)[0] != edit['before']:
                raise ValueError(f"{king['king']}: unexpected instruction at {at:#x}")
            struct.pack_into('<h', section, at + 1, edit['after'])
        for edit in king.get('replacements', []):
            at, before, after = edit['sectionByteOffset'], bytes.fromhex(edit['before']), bytes.fromhex(edit['after'])
            if len(before) != len(after) or section[at:at + len(before)] != before:
                raise ValueError(f"{king['king']}: unexpected instruction at {at:#x}")
            section[at:at + len(after)] = after
        rel = f"{king['file']}.dir/section_{king['section']:03}.bin"
        if rel in files:
            raise ValueError(f'{rel} edited twice')
        files[rel] = bytes(section)
    return files


def shop_stock(shop_rows, story):
    """Items a shop sells at a story value: the first row whose condition holds, plus every later row.

    When no condition holds the game still selects the last row, so the last row is always sold.
    Only unconditional rows (condition 0) and story rows (condition 2: story >= threshold) are modelled."""
    for index, row in enumerate(shop_rows):
        if row['condition'] not in (0, 2):
            raise ValueError('only unconditional and story-gated shop rows are supported')
        if row['condition'] == 0 or story >= row['threshold'] or index == len(shop_rows) - 1:
            return [item for later in shop_rows[index:] for item in later['items']]
    return []


def story_stock(section, patch):
    """Add items to shops at story thresholds: each item goes only in the row for the story value where it unlocks.

    The game sells the first matching row plus every later row, so with rows in descending threshold order an item
    stored in its own row is sold from that point on. Each touched shop is rewritten as rows sorted by the story
    value at which they actually start selling (native shops sometimes interleave unconditional rows), with an
    unconditional baseline last; its stock at every story value is otherwise unchanged. A copy of a placed item in a
    later-unlocking row of the same shop is removed; earlier offers stay. New row and item tables are appended after
    the existing data and the shop re-pointed; every other byte is kept.
    """
    require(section, patch['sourceSectionSha256'], f"{patch['file']} section {patch['section']} (opening stock)")
    shops = shop_table(section)
    out = bytearray(section)
    checks = []
    for entry in patch['shops']:
        shop = shops[entry['shop']]
        native = shop['rows']
        values = sorted({0, *[r['threshold'] for r in native if r['condition'] == 2]})
        starts = {}
        for story in values:
            for index, row in enumerate(native):
                if row['condition'] == 0 or story >= row['threshold'] or index == len(native) - 1:
                    for later in range(index, len(native)):
                        starts.setdefault(later, story)
                    break
        rows = [{'condition': 2 if starts[i] else 0, 'threshold': starts[i], 'items': list(r['items'])}
                for i, r in enumerate(native)]
        if not any(r['condition'] == 0 for r in rows):
            rows.append({'condition': 0, 'threshold': 0, 'items': []})
        rows.sort(key=lambda r: -r['threshold'])
        for story in [*values, 32767]:
            if Counter(shop_stock(rows, story)) != Counter(shop_stock(native, story)):
                raise AssertionError(f"shop {entry['shop']}: reordering changed its stock")
        placed = set()
        for add in entry['rows']:
            for item in add['add']:
                placed.add(item['id'])
                for row in rows:
                    if row['threshold'] > add['story']:
                        row['items'] = [i for i in row['items'] if i != item['id']]
                row = next((r for r in rows if r['threshold'] == add['story']), None)
                if row is None:
                    row = {'condition': 2 if add['story'] else 0, 'threshold': add['story'], 'items': []}
                    rows.append(row)
                    rows.sort(key=lambda r: -r['threshold'])
                if item['id'] not in row['items']:
                    row['items'].append(item['id'])
        checks.append((entry, native, rows, placed))

        while len(out) % 4:
            out.append(0)
        events = len(out)
        out.extend(b'\x64\x01\x00\x00\x08\x00' + struct.pack('<HI', len(rows), events + 12))
        out.extend(bytes(8 * len(rows)))
        for index, row in enumerate(rows):
            while len(out) % 4:
                out.append(0)
            header = len(out)
            out.extend(b'\x64\x02\x00\x00\x02\x00' + struct.pack('<HI', len(row['items']), header + 12))
            out.extend(struct.pack(f"<{len(row['items'])}H", *row['items']))
            struct.pack_into('<BBHI', out, events + 12 + 8 * index, row['condition'], 0, row['threshold'], header)
        struct.pack_into('<I', out, shop['offset'] + 4, events)
    while len(out) % 4:
        out.append(0)
    result = bytes(out)
    check_story_stock(section, result, checks)
    require(result, patch['resultSectionSha256'], 'story stock result')
    return result


def check_story_stock(original, result, checks):
    touched = {entry['shop'] for entry, *_ in checks}
    pointers = {shop['offset'] + 4 + i for shop in shop_table(original) if shop['shop'] in touched for i in range(4)}
    if any(a != b for i, (a, b) in enumerate(zip(original, result)) if i not in pointers):
        raise AssertionError('story stock changed bytes outside the touched shop pointers')
    after = shop_table(result)
    for before, b in zip(shop_table(original), after):
        if b['shop'] not in touched and [r['items'] for r in b['rows']] != [r['items'] for r in before['rows']]:
            raise AssertionError(f"untouched shop {b['shop']} changed")
    for entry, native, rows, placed in checks:
        written = after[entry['shop']]['rows']
        if [(r['condition'], r['threshold'], r['items']) for r in written] != \
                [(r['condition'], r['threshold'], r['items']) for r in rows]:
            raise AssertionError(f"shop {entry['shop']} was not written as planned")
        stories = {0, 32767, *[r['threshold'] for r in native + written]}
        for story in sorted({max(0, s + d) for s in stories for d in (-1, 0, 1)}):
            old, new = Counter(shop_stock(native, story)), Counter(shop_stock(written, story))
            if {i: n for i, n in old.items() if i not in placed} != {i: n for i, n in new.items() if i not in placed}:
                raise AssertionError(f"shop {entry['shop']}: stock of an unplaced item changed at story {story}")
            if any(n > 1 for n in Counter(i for r in written for i in r['items'] if i in placed).values()):
                raise AssertionError(f"shop {entry['shop']}: a placed item is listed twice")
        for add in entry['rows']:
            for item in add['add']:
                if item['id'] not in shop_stock(written, add['story']):
                    raise AssertionError(f"{item['name']} not sold in shop {entry['shop']} at story {add['story']}")
                earlier = add['story'] and item['id'] in shop_stock(written, add['story'] - 1)
                if earlier and item['id'] not in shop_stock(native, add['story'] - 1):
                    raise AssertionError(f"{item['name']} sold in shop {entry['shop']} before story {add['story']}")


def shops(read, patches):
    """Battle_pack section 39: opening stock, then story stock on top of it."""
    files = opening_stock(read, patches['opening_stock'])
    (name, section), = files.items()
    return {name: story_stock(section, patches['story_stock'])}


APPLIERS = {'opening_stock+story_stock': shops, 'ability_chests': ability_chests, 'kings': kings}


# Enemy stats row (AR03 section 7, 56-byte st2e rows): field -> (offset, format)
ENEMY_STATS = {'hp': (0x20, '<I'), 'mp': (0x24, '<H'), 'strength': (0x26, 'B'), 'magick': (0x27, 'B'),
               'vitality': (0x28, 'B'), 'speed': (0x29, 'B'), 'defense': (0x2B, 'B'), 'magickResist': (0x2C, 'B'),
               'attack': (0x2D, 'B'), 'lp': (0x2F, 'B'), 'gil': (0x30, '<I'), 'exp': (0x34, '<I')}


def enemy_stats(read, patch):
    """Set fields of explicit enemy stats rows in one source-pinned area (AR03 section 7).

    Every change names its row, the unit it belongs to and the value it replaces; a row shared by another unit, an
    unexpected value or a byte outside the declared fields refuses the build."""
    from .formats import ard_section
    raw = read(patch['file'])
    require(raw, patch['sourceFileSha256'], patch['file'])
    original = ard_section(raw, patch['section'])
    require(original, patch['sourceSectionSha256'], 'enemy stats source')
    if patch['section'] != 7 or original[:4] != b'st2e':
        raise ValueError('expected the enemy stats table (section 7)')
    count, stride, base = struct.unpack_from('<III', original, 4)
    if stride != 56 or base + count * stride > len(original):
        raise ValueError('unexpected enemy stats layout')
    unit_rows = ard_section(raw, 4)
    if unit_rows[:4] != b'st2e':
        raise ValueError('expected the enemy unit table (section 4)')
    n_units, unit_stride, unit_base = struct.unpack_from('<III', unit_rows, 4)
    users = Counter(struct.unpack_from('<H', unit_rows, unit_base + i * unit_stride + 34)[0] for i in range(n_units))
    out = bytearray(original)
    allowed, seen = set(), set()
    for edit in patch['rows']:
        if not 0 <= edit['row'] < count or edit['row'] in seen:
            raise ValueError('invalid or repeated stats row')
        seen.add(edit['row'])
        if users[edit['row']] != edit.get('users', 1):
            raise ValueError(f"{edit['unit']}: stats row {edit['row']} is used by {users[edit['row']]} units")
        at = base + edit['row'] * stride
        for field, (before, after) in edit['set'].items():
            offset, fmt = ENEMY_STATS[field]
            if struct.unpack_from(fmt, original, at + offset)[0] != before:
                raise ValueError(f"{edit['unit']} {field}: unexpected value (expected {before})")
            if not 0 <= after < 1 << 8 * struct.calcsize(fmt):
                raise ValueError(f"{edit['unit']} {field}: {after} does not fit")
            struct.pack_into(fmt, out, at + offset, after)
            allowed |= set(range(at + offset, at + offset + struct.calcsize(fmt)))
    if {i for i, (a, b) in enumerate(zip(original, out)) if a != b} - allowed:
        raise AssertionError('undeclared enemy stats byte changed')
    result = bytes(out)
    require(result, patch['resultSectionSha256'], 'enemy stats result')
    return {f"{patch['file']}.dir/section_{patch['section']:03}.bin": result}


def enemy_ai(read, patch):
    """Apply explicit condition-ID edits to one source-pinned AR03 AI bank."""
    from .formats import ard_section, ai_rule_offset
    raw = read(patch['file'])
    require(raw, patch['sourceFileSha256'], patch['file'])
    original = ard_section(raw, patch['section'])
    require(original, patch['sourceSectionSha256'], 'enemy AI source')
    out = bytearray(original)
    used = set()
    for edit in patch['conditions']:
        slot = edit['conditionSlot']
        if not 0 <= slot < 3:
            raise ValueError('invalid AI condition slot')
        rule = ai_rule_offset(original, edit['script'], edit['row'])
        if original[rule:rule + 24] != bytes.fromhex(edit['ruleBeforeHex']):
            raise ValueError('unexpected AI rule')
        at = rule + 8 + slot * 6
        if at in used or struct.unpack_from('<H', original, at)[0] != edit['before']:
            raise ValueError('duplicate or unexpected AI condition')
        used.add(at)
        struct.pack_into('<H', out, at, edit['after'])
    result = bytes(out)
    require(result, patch['resultSectionSha256'], 'enemy AI result')
    return {f"{patch['file']}.dir/section_{patch['section']:03}.bin": result}
