"""Source-pinned field edits to the equipment table (battle_pack section 13).

Section 13 is an ST2E table: u32 row count at +4, u16 row size at +8, u32 first-row offset at +12, and at +24
the start of the 24-byte attribute blocks (stat bonuses, granted/immune statuses, element affinities) that rows
address by byte offset. Every edit states the value it expects to find; a different game version, another mod's
data or a typo is rejected instead of patched. Attribute blocks can be shared by several items, so a block edit
must name every item that uses it.
"""
import hashlib
import struct

from .formats import battle_pack_section

STATUS = ['KO', 'Stone', 'Petrify', 'Stop', 'Sleep', 'Confuse', 'Doom', 'Blind', 'Poison', 'Silence', 'Sap', 'Oil',
          'Reverse', 'Disable', 'Immobilize', 'Slow', 'Disease', 'Lure', 'Protect', 'Shell', 'Haste', 'Bravery',
          'Faith', 'Reflect', 'Invisible', 'Regen', 'Float', 'Berserk', 'Bubble', 'HP Critical', 'Libra', 'X-Zone']
ELEMENTS = ['Fire', 'Lightning', 'Ice', 'Earth', 'Water', 'Wind', 'Holy', 'Dark']
CATEGORIES = ['Unarmed', 'Sword', 'Greatsword', 'Katana', 'Ninja sword', 'Spear', 'Pole', 'Bow', 'Crossbow', 'Gun',
              'Axe', 'Hammer', 'Dagger', 'Rod', 'Staff', 'Mace', 'Measure', 'Hand-bomb', 'Shield', 'Head armor',
              'Body armor', 'Accessory', 'Accessory (special)', 'Arrow', 'Bolt', 'Shot', 'Bomb']
FIRST_ID = 0x1000

# field: (byte offset in the row, struct format, value kind)
COMMON = {'gil': (18, '<H', 'int')}
WEAPON = {'range': (24, 'B', 'int'), 'attack': (26, 'B', 'int'), 'knockback': (27, 'B', 'int'),
          'comboOrCrit': (28, 'B', 'int'), 'evade': (29, 'B', 'int'), 'elements': (30, 'B', ELEMENTS),
          'onHitRate': (31, 'B', 'int'), 'statusEffects': (32, '<I', STATUS), 'chargeTime': (39, 'B', 'int')}
SHIELD = {'evade': (24, 'B', 'int'), 'magickEvade': (25, 'B', 'int')}
ARMOR = {'defense': (24, 'B', 'int'), 'magickResist': (25, 'B', 'int'), 'augment': (26, 'B', 'int')}
AMMO = {'attack': (26, 'B', 'int'), 'evade': (29, 'B', 'int'), 'elements': (30, 'B', ELEMENTS),
        'onHitRate': (31, 'B', 'int'), 'statusEffects': (32, '<I', STATUS)}
ATTRIBUTE = {'hp': (0, '<H', 'int'), 'mp': (2, '<H', 'int'), 'strength': (4, 'B', 'int'), 'magick': (5, 'B', 'int'),
             'vitality': (6, 'B', 'int'), 'speed': (7, 'B', 'int'), 'status': (8, '<I', STATUS),
             'immune': (12, '<I', STATUS), 'absorb': (16, 'B', ELEMENTS), 'nullify': (17, 'B', ELEMENTS),
             'half': (18, 'B', ELEMENTS), 'weak': (19, 'B', ELEMENTS), 'boost': (20, 'B', ELEMENTS)}
HITS_FLYING = 0x04  # flags byte at row +7
CATEGORY_AT, FLAGS_AT, LINK_AT = 9, 7, 40


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fields_for(category):
    if category <= 17:
        return {**COMMON, **WEAPON}
    if category == 18:
        return {**COMMON, **SHIELD}
    if category <= 22:
        return {**COMMON, **ARMOR}
    return {**COMMON, **AMMO}


def layout(section):
    if section[:4] != b'st2e' or len(section) < 28:
        raise ValueError('expected the equipment ST2E table')
    count, size = struct.unpack_from('<IH', section, 4)
    entry = struct.unpack_from('<I', section, 12)[0]
    attributes = struct.unpack_from('<I', section, 24)[0]
    if size < 44 or entry + count * size > attributes or attributes > len(section):
        raise ValueError('unsupported equipment table layout')
    return count, size, entry, attributes


def encode(kind, value):
    if kind == 'int':
        if type(value) is not int or value < 0:
            raise ValueError(f'invalid value {value!r}')
        return value
    if not isinstance(value, list) or len(set(value)) != len(value) or any(v not in kind for v in value):
        raise ValueError(f'invalid flag list {value!r}')
    return sum(1 << kind.index(v) for v in value)


def edit(out, original, at, spec, field, change, what):
    offset, fmt, kind = spec[field]
    if not isinstance(change, list) or len(change) != 2:
        raise ValueError(f'{what} {field}: expected [before, after]')
    before, after = (encode(kind, v) for v in change)
    if after >= 1 << 8 * struct.calcsize(fmt):
        raise ValueError(f'{what} {field}: {change[1]} does not fit')
    if struct.unpack_from(fmt, original, at + offset)[0] != before:
        raise ValueError(f'{what} {field}: unexpected value (expected {change[0]!r})')
    struct.pack_into(fmt, out, at + offset, after)
    return set(range(at + offset, at + offset + struct.calcsize(fmt)))


def equipment(read, patch):
    """Apply patch['items'] (row fields) and patch['attributes'] (shared attribute blocks)."""
    pack = read(patch['file'])
    if sha256(pack) != patch['sourceFileSha256']:
        raise ValueError(f"{patch['file']} differs from the supported game version")
    original = battle_pack_section(pack, patch['section'])
    if sha256(original) != patch['sourceSectionSha256']:
        raise ValueError(f"{patch['file']} section {patch['section']} differs from the supported game version")
    count, size, entry, attributes = layout(original)
    out = bytearray(original)
    changed = set()

    def row_at(content_id):
        index = content_id - FIRST_ID
        if not 0 <= index < count:
            raise ValueError(f'content {content_id:#x} is not equipment')
        return entry + index * size

    seen = set()
    for item in patch.get('items', []):
        what = item.get('name', f"{item['contentId']:#x}")
        if item['contentId'] in seen:
            raise ValueError(f'{what} listed twice')
        seen.add(item['contentId'])
        at = row_at(item['contentId'])
        category = original[at + CATEGORY_AT]
        if category >= len(CATEGORIES) or CATEGORIES[category] != item['category']:
            raise ValueError(f'{what} is not a {item["category"]}')
        spec = fields_for(category)
        for field, change in item['set'].items():
            if field == 'category':
                # Weapon family only (e.g. Kumbha already swings as a katana); equip, offhand and license fields stay.
                if (not isinstance(change, list) or len(change) != 2 or change[0] != item['category']
                        or change[1] not in CATEGORIES[1:18] or category > 17):
                    raise ValueError(f'{what} category: weapons only, [current, new weapon family]')
                out[at + CATEGORY_AT] = CATEGORIES.index(change[1])
                changed.add(at + CATEGORY_AT)
            elif field == 'hitsFlying':
                if category > 17 or [type(v) for v in change] != [bool, bool]:
                    raise ValueError(f'{what} hitsFlying: weapons only, [before, after] booleans')
                if bool(original[at + FLAGS_AT] & HITS_FLYING) != change[0]:
                    raise ValueError(f'{what} hitsFlying: unexpected value')
                out[at + FLAGS_AT] = out[at + FLAGS_AT] & ~HITS_FLYING | (HITS_FLYING if change[1] else 0)
                changed.add(at + FLAGS_AT)
            elif field in spec:
                changed |= edit(out, original, at, spec, field, change, what)
            else:
                raise ValueError(f'{what}: {field} cannot be set on a {item["category"]}')

    users = {}
    for index in range(count):
        link = struct.unpack_from('<I', original, entry + index * size + LINK_AT)[0]
        users.setdefault(link, set()).add(FIRST_ID + index)
    blocks = set()
    for block in patch.get('attributes', []):
        index = block['attributeIndex']
        at = attributes + 24 * index
        if index in blocks or not 0 <= index or at + 24 > len(original):
            raise ValueError(f'attribute block {index} invalid or listed twice')
        blocks.add(index)
        if set(block['users']) != users.get(24 * index, set()):
            raise ValueError(f'attribute block {index}: declared users differ from the items that use it')
        for field, change in block['set'].items():
            if field not in ATTRIBUTE:
                raise ValueError(f'attribute block {index}: unknown field {field}')
            changed |= edit(out, original, at, ATTRIBUTE, field, change, f'attribute block {index}')

    if {i for i, (a, b) in enumerate(zip(original, out)) if a != b} - changed:
        raise AssertionError('undeclared equipment byte changed')
    result = bytes(out)
    if patch.get('resultSectionSha256') and sha256(result) != patch['resultSectionSha256']:
        raise ValueError('equipment result differs from the reviewed build')
    return {f"{patch['file']}.dir/section_{patch['section']:03}.bin": result}
