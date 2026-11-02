"""Source-pinned moves of equipment between license nodes, and license name changes."""
import hashlib
import struct

EMPTY = 0xFFFF
NAMES_SUBTABLE = 3  # battle pack section 61 (word table), sub-table 3: one name per license node


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def apply_license_contents(raw, patch):
    """Move items between license nodes: every item leaves one node and joins another; none is created or lost."""
    require(len(raw) >= 32 and raw[:4] == b'st2e', 'Expected license ST2E table')
    count, stride, start = struct.unpack_from('<III', raw, 4)
    require((count, stride, start) == (361, 24, 32), 'Unsupported license table layout')
    result = bytearray(raw)
    changed, removed, added = set(), [], []
    seen = set()
    for change in patch['changes']:
        lid, before, after = change['licenseId'], change['before'], change['after']
        require(type(lid) is int and 0 <= lid < count and lid not in seen, 'Invalid or repeated license node')
        seen.add(lid)
        require(all(type(v) is int and 0 <= v < EMPTY for v in before + after) and len(after) <= 8
                and len(set(after)) == len(after), 'Invalid license contents')
        at = start + stride * lid + 8
        current = [v for v in struct.unpack_from('<8H', raw, at) if v != EMPTY]
        require(current == before, f'Unexpected contents for license {lid}')
        require(raw[start + stride * lid + 5] in range(1, 8), 'Only equipment licenses may change contents')
        struct.pack_into('<8H', result, at, *(after + [EMPTY] * (8 - len(after))))
        changed |= set(range(at, at + 16))
        removed += [v for v in before if v not in after]
        added += [v for v in after if v not in before]
    require(sorted(removed) == sorted(added), 'Items must move between licenses, not appear or vanish')
    require({i for i, (a, b) in enumerate(zip(raw, result)) if a != b} <= changed, 'Undeclared license byte changed')
    result = bytes(result)
    require(sha(result) == patch['resultSha256'], 'License contents result hash mismatch')
    return result


def name_entry(name):
    """A license-name entry as the game lays them out: two zero bytes, the name and its terminator, then at least
    three zero bytes so the next entry starts on four bytes."""
    from .job_labels import encode_label
    body = b'\0\0' + encode_label(name) + b'\0\0\0'
    return body + bytes(-len(body) % 4)


def license_names(section61, patch):
    """Return the license-name sub-table of the word table with names replaced.

    The sub-table is rebuilt with new offsets, so a name may be longer than the one it replaces (as the equipment-name
    sub-table already is); every other entry keeps its exact bytes.
    """
    require(sha(section61) == patch['sourceSha256'], 'Word table source hash mismatch')
    tables = struct.unpack_from('<I', section61, 0)[0]
    bounds = [struct.unpack_from('<I', section61, 4 + 4 * i)[0] for i in range(tables)] + [len(section61)]
    table = section61[bounds[NAMES_SUBTABLE]:bounds[NAMES_SUBTABLE + 1]]
    entries = struct.unpack_from('<I', table, 0)[0]
    require(entries == 362, 'Unsupported license-name table')
    # Header: count, one offset per entry, then the end offset (the sub-table's total length).
    slots = [struct.unpack_from('<I', table, 4 + 4 * i)[0] for i in range(entries + 1)]
    require(slots[0] == 4 + 4 * (entries + 1) and slots[-1] == len(table), 'Unsupported name header')
    require(all(a < b and a % 4 == 0 for a, b in zip(slots, slots[1:])), 'Unsupported name offsets')
    bodies = [table[slots[i]:slots[i + 1]] for i in range(entries)]
    seen = set()
    for change in patch['changes']:
        lid = change['licenseId']
        require(type(lid) is int and 0 <= lid < entries and lid not in seen, 'Invalid or repeated license name')
        seen.add(lid)
        require(bodies[lid].hex() == change['beforeHex'], f'Unexpected name slot for license {lid}')
        require(isinstance(change['replacement'], str) and 0 < len(change['replacement']) <= 24,
                f'License name for {lid} must be 1-24 characters')
        bodies[lid] = name_entry(change['replacement'])
    out = bytearray(table[:slots[0]])
    offsets = []
    for body in bodies:
        offsets.append(len(out))
        out += body
    struct.pack_into(f'<{entries + 1}I', out, 4, *offsets, len(out))
    result = bytes(out)
    require(sha(result) == patch['resultSha256'], 'License name result hash mismatch')
    return result
