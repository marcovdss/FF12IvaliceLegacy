"""Source-pinned, byte-only license purchase-price patches."""
import hashlib
import struct


def apply_license_lp(raw, patch, jobs):
    def require(value, message):
        if not value:
            raise ValueError(message)
    require(patch['schemaVersion'] == 1, 'Unsupported license LP patch')
    require(hashlib.sha256(raw).hexdigest() == patch['sourceSha256'], 'License LP source hash mismatch')
    require(len(raw) >= 32 and raw[:4] == b'st2e', 'Expected license ST2E table')
    count, stride, start = struct.unpack_from('<III', raw, 4)
    require((count, stride, start) == (361, 24, 32) and start+count*stride <= len(raw),
            'Unsupported license table layout')
    changes = patch['changes']
    require(changes and len({c['licenseId'] for c in changes}) == len(changes),
            'Expected distinct license LP changes')
    result = bytearray(raw)
    offsets = set()
    for change in changes:
        lid, before, after = (change[k] for k in ('licenseId', 'beforeLp', 'afterLp'))
        require(all(type(v) is int for v in (lid, before, after)) and 0 <= lid < count
                and 0 <= before <= 255 and 0 < after <= 255 and before != after, 'Invalid license LP change')
        owners = [j['name'] for j in jobs if any(c['licenseId'] == lid for c in j['cells'])]
        require(owners == [change['owner']], 'License LP change must have its sole declared owner')
        offset = start+stride*lid+4
        require(raw[offset] == before, 'Unexpected license LP before value')
        result[offset] = after
        offsets.add(offset)
    require({i for i, (a,b) in enumerate(zip(raw,result)) if a != b} == offsets,
            'Undeclared license-table byte changed')
    result = bytes(result)
    require(hashlib.sha256(result).hexdigest() == patch['resultSha256'], 'License LP result hash mismatch')
    return result
