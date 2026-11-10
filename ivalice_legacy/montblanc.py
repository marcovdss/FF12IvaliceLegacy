"""Append a native Montblanc help branch without relocating existing VM data.

The original conversation code remains byte-exact in a new code region. Its
relative function targets survive; only its main menu dispatch label changes.
"""
import hashlib
import struct

from .key_items import GLYPHS as BASE_GLYPHS

GLYPHS = {**BASE_GLYPHS, '!': 0x99}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def field_strings(raw):
    require(len(raw) >= 4, 'Truncated field text')
    first = struct.unpack_from('<I', raw)[0] & 0xffff
    require(first % 4 == 0 and 0 < first <= len(raw), 'Invalid field directory')
    pointers = list(struct.unpack_from(f'<{first // 4}I', raw))
    offsets = [p & 0xffff for p in pointers]
    require(offsets[0] == first and all(a <= b for a, b in zip(offsets, offsets[1:])), 'Invalid field pointers')
    require(all(first <= p < len(raw) for p in offsets), 'Field pointer outside data')
    bounds = offsets + [len(raw)]
    return pointers, [raw[a:b] for a, b in zip(bounds, bounds[1:])]


def encode_body(text):
    require(isinstance(text, str) and text and all(c in GLYPHS for c in text), 'Unsupported dialogue glyph')
    lines = text.split('\n')
    require(len(lines) <= 3 and all(0 < len(line) <= 44 for line in lines), 'Dialogue exceeds compact box budget')
    return bytes(GLYPHS[c] for c in text)


def patch_text(raw, spec):
    require(digest(raw) == spec['textSha256'], 'HQ text changed; review before patching')
    pointers, old = field_strings(raw)
    require(len(old) == spec['textCount'], 'HQ text count changed')
    choice = old[55]
    marker = bytes.fromhex('0e83808080')
    require(choice.count(marker) == 1 and choice.count(b'\x8b') == 1, 'Montblanc choices changed')
    label = spec['choice']
    require(0 < len(label) <= 40 and all(c in GLYPHS for c in label), 'Invalid choice label')
    updated = choice.replace(marker, bytes.fromhex('0e84808080'))
    updated = updated.replace(b'\x8b', b'\xa3' + bytes(GLYPHS[c] for c in label) + b'\x8b')
    prefix = choice.split(b'\x02', 1)[0] + b'\x02'
    require(len(prefix) == 10, 'Unexpected Montblanc speaker prefix')
    new = list(old)
    new[55] = updated
    new += [prefix + encode_body(page) + b'\0' for page in spec['pages']]
    count = len(new)
    output = bytearray(count * 4)
    for index, value in enumerate(new):
        require(len(output) < 65536, 'Field text exceeds 16-bit pointer range')
        flags = pointers[index] & 0xffff0000 if index < len(old) else 0
        struct.pack_into('<I', output, 4 * index, flags | len(output))
        output += value
    require(len(output) < 65536, 'Field text exceeds 16-bit size')
    output += bytes((-len(output)) % 64)
    result = bytes(output)
    _, check = field_strings(result)
    require(all(check[i] == old[i] for i in range(len(old)) if i != 55), 'Unselected field text changed')
    return result


def instructions(raw, sizes):
    pc, output = 0, []
    while pc < len(raw):
        op = raw[pc]
        require(op < len(sizes), 'Unknown VM instruction')
        size = max(sizes[op], 1)
        require(size in (1, 3) and pc + size <= len(raw), 'Truncated VM instruction')
        arg = struct.unpack_from('<H', raw, pc + 1)[0] if size == 3 else None
        output.append((pc, op, arg))
        pc += size
    return output


def patch_script(raw, spec, sizes):
    require(digest(raw) == spec['scriptSha256'], 'HQ script changed; review before patching')
    require(struct.unpack_from('<H', raw)[0] >= 8, 'Unsupported VM descriptor version')
    logical_end = struct.unpack_from('<I', raw, 4)[0]
    require(logical_end <= len(raw) and not any(raw[logical_end:]), 'Invalid VM tail')
    table = struct.unpack_from('<I', raw, 0x18)[0]
    count = struct.unpack_from('<I', raw, table)[0]
    require(0 <= spec['thread'] < count, 'Missing Montblanc thread')
    descriptor = table + 4 + 48 * spec['thread']
    code_start, native_count = struct.unpack_from('<II', raw, descriptor + 8)
    label_table = struct.unpack_from('<I', raw, descriptor + 20)[0]
    nlabels = struct.unpack_from('<I', raw, label_table)[0]
    labels = list(struct.unpack_from(f'<{nlabels}I', raw, label_table + 4))
    require(nlabels == spec['labelCount'], 'Montblanc labels changed')
    code = raw[code_start:code_start + spec['codeBytes']]
    native = instructions(code, sizes)
    require(len(native) == native_count == spec['instructionCount'], 'Montblanc code extent changed')
    boundaries = {o for o, _, _ in native}
    require(all(p in boundaries for p in labels), 'Native label outside instructions')
    require(labels[spec['dispatchLabel']] == spec['dispatchOffset'], 'Main dispatch changed')
    require(spec['menuOffset'] in boundaries, 'Menu return outside instructions')
    pool_at = struct.unpack_from('<I', raw, 0x20)[0]
    pool_count = struct.unpack_from('<I', raw, pool_at)[0]
    pool = list(struct.unpack_from(f'<{pool_count}i', raw, pool_at + 4))
    require(pool_count + len(spec['pages']) <= 65536, 'Integer pool exceeds VM operand range')
    help_label, menu_label = nlabels, nlabels + 1
    extra = bytearray()

    def emit(op, arg):
        extra.extend(struct.pack('<BH', op, arg))

    dispatch_offset = len(code)
    for choice, destination in ((0, spec['marksLabel']), (1, spec['jobsLabel']), (2, help_label)):
        emit(0x4f, choice)
        emit(0x52, destination)
    emit(0x51, spec['leaveLabel'])
    help_offset = len(code) + len(extra)
    commands = spec['commands']
    for index, _ in enumerate(spec['pages']):
        for command in ('mouth', 'nod'):
            emit(0x4f, 1)
            emit(0x5d, commands[command])
        emit(0x4f, 0)
        emit(0x4e, pool_count + index)
        emit(0x5d, commands['message'])
        emit(0x4f, 0)
        emit(0x4f, 1)
        emit(0x5d, commands['sync'])
        for command in ('mouth', 'nod'):
            emit(0x4f, 0)
            emit(0x5d, commands[command])
    emit(0x51, menu_label)
    labels[spec['dispatchLabel']] = dispatch_offset
    labels += [help_offset, spec['menuOffset']]
    pool += [0x01000000 + spec['textCount'] + i for i in range(len(spec['pages']))]
    new_code = code + extra
    decoded = instructions(new_code, sizes)
    boundaries = {o for o, _, _ in decoded}
    require(all(p in boundaries for p in labels), 'New label outside instructions')
    out = bytearray(raw[:logical_end])

    def append(data):
        out.extend(bytes((-len(out)) % 4))
        offset = len(out)
        out.extend(data)
        return offset

    new_start = append(new_code)
    new_labels = append(struct.pack('<I', len(labels)) + struct.pack(f'<{len(labels)}I', *labels))
    new_pool = append(struct.pack('<I', len(pool)) + struct.pack(f'<{len(pool)}i', *pool))
    struct.pack_into('<II', out, descriptor + 8, new_start, len(decoded))
    struct.pack_into('<I', out, descriptor + 20, new_labels)
    struct.pack_into('<I', out, 0x20, new_pool)
    out += bytes((-len(out)) % 16)
    struct.pack_into('<I', out, 4, len(out))
    out += bytes((-len(out)) % 64)
    allowed = set(range(4, 8)) | set(range(0x20, 0x24)) | set(range(descriptor + 8, descriptor + 16)) | set(range(descriptor + 20, descriptor + 24))
    require(all(a == b for i, (a, b) in enumerate(zip(raw[:logical_end], out)) if i not in allowed), 'Original VM data changed')
    return bytes(out)
