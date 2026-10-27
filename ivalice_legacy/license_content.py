"""Source-pinned moves of equipment between license nodes, and in-place license name changes."""
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


def license_names(section61, patch):
    """Return the license-name sub-table of the word table with names replaced in their existing slots."""
    require(sha(section61) == patch['sourceSha256'], 'Word table source hash mismatch')
    tables = struct.unpack_from('<I', section61, 0)[0]
    bounds = [struct.unpack_from('<I', section61, 4 + 4 * i)[0] for i in range(tables)] + [len(section61)]
    table = bytearray(section61[bounds[NAMES_SUBTABLE]:bounds[NAMES_SUBTABLE + 1]])
    entries = struct.unpack_from('<I', table, 0)[0]
    require(entries == 362, 'Unsupported license-name table')
    slots = [struct.unpack_from('<I', table, 4 + 4 * i)[0] for i in range(entries)] + [len(table)]
    from .job_labels import encode_label
    changed = set()
    for change in patch['changes']:
        lid = change['licenseId']
        start, end = slots[lid], slots[lid + 1]
        require(table[start:end].hex() == change['beforeHex'], f'Unexpected name slot for license {lid}')
        text = b'\0\0' + encode_label(change['replacement'])
        require(len(text) <= end - start, 'Replacement name does not fit its slot')
        table[start:end] = text + bytes(end - start - len(text))
        changed |= set(range(start, end))
    original = section61[bounds[NAMES_SUBTABLE]:bounds[NAMES_SUBTABLE + 1]]
    require({i for i, (a, b) in enumerate(zip(original, table)) if a != b} <= changed, 'Undeclared name byte changed')
    result = bytes(table)
    require(sha(result) == patch['resultSha256'], 'License name result hash mismatch')
    return result
