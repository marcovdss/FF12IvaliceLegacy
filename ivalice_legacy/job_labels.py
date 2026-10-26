"""Lossless, source-pinned native English job-label patches."""
import hashlib
import struct


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode_label(text):
    glyphs = {' ': 4, **{chr(65+i): 32+i for i in range(26)},
              **{chr(97+i): 58+i for i in range(26)}}
    require(isinstance(text, str) and bool(text), 'Expected a nonempty label')
    require(all(c in glyphs for c in text), 'Unsupported English label glyph')
    return bytes(glyphs[c] for c in text) + b'\0'


def encode_description_line(text):
    glyphs = {' ': 4, '-': 165, ',': 167, '.': 168, ':': 170,
              **{str(i): 133+i for i in range(10)},
              **{chr(65+i): 32+i for i in range(26)},
              **{chr(97+i): 58+i for i in range(26)}}
    require(isinstance(text, str) and bool(text) and all(c in glyphs for c in text),
            'Unsupported English description glyph')
    return bytes(glyphs[c] for c in text)


def text_records(raw):
    """Return complete encoded spans, including aliased entries and control bytes."""
    parsed = parse_text(raw)
    bounds = sorted(set(parsed['pointers']))
    ends = dict(zip(bounds, bounds[1:]))
    return {value: raw[parsed['group']+p:parsed['group']+ends.get(p,p)]
            for value,p in zip(parsed['ids'],parsed['pointers'])}


def description_replacements(raw, patch):
    records = text_records(raw)
    require({r['stringId'] for r in patch['changes']} == set(range(22000,22012))
            and len(patch['changes']) == 12, 'Expected twelve native description IDs')
    replacements = {}
    for row in patch['changes']:
        value = row['stringId']
        require(value in records, 'Unknown native description ID')
        before = records[value]
        require(sha(before) == row['sourceEntrySha256'], 'Description entry source hash mismatch')
        parts = before.split(b'\x02')
        prompt = b'\x0f\x48\x80\x04' + encode_label('Preview License Board')
        require(len(parts) == 3 and parts[2] == prompt, 'Unexpected native description format/prompt')
        lines = row['lines']
        require(isinstance(lines,list) and len(lines) == 2, 'Expected two description lines')
        encoded = [encode_description_line(s) for s in lines]
        require(all(len(a) <= len(b) for a,b in zip(encoded,parts)), 'Description line exceeds native limit')
        replacements[value] = (before,b'\x02'.join([*encoded,parts[2]]))
    return replacements


def parse_text(raw):
    require(len(raw) >= 48 and raw[:4] == b'st2e', 'Expected ST2E text')
    count, stride, entry = struct.unpack_from('<III', raw, 4)
    text = struct.unpack_from('<I', raw, 20)[0]
    require(stride == 4 and 0 < count < 10000 and entry == 32, 'Unexpected text directory')
    require(text == entry + count*4 and text+12 <= len(raw), 'Unexpected text section')
    groups, group_offset, text_length = struct.unpack_from('<III', raw, text)
    require(groups == 1 and group_offset == 12 and text+text_length <= len(raw), 'Expected one bounded text group')
    group = text + group_offset
    require(text_length >= 16 and group+4 <= text+text_length, 'Truncated text group')
    require(struct.unpack_from('<I', raw, group)[0] == count, 'Text count mismatch')
    directory_end = group + 4 + 4*(count+1)
    require(directory_end <= text+text_length, 'Truncated text offsets')
    pointers = list(struct.unpack_from(f'<{count+1}I', raw, group+4))
    require(min(pointers) == directory_end-group and pointers[-1]+group == text+text_length,
            'Unexpected text bounds')
    require(all(directory_end-group <= p <= pointers[-1] for p in pointers), 'Out-of-bounds text pointer')
    require(len(raw)-(group+pointers[-1]) <= 3 and not any(raw[group+pointers[-1]:]), 'Unexpected text trailer')
    ids = list(struct.unpack_from(f'<{count}I', raw, entry))
    require(len(ids) == len(set(ids)), 'Duplicate string IDs')
    return {'count': count, 'text': text, 'textLength': text_length, 'group': group,
            'ids': ids, 'pointers': pointers, 'stringsStart': directory_end,
            'stringsEnd': group+pointers[-1]}


def apply_text_patch(raw, patch):
    require(sha(raw) == patch['sourceSha256'], 'Job label source hash mismatch')
    parsed = parse_text(raw)
    pointers = parsed['pointers']
    positions = {value: parsed['group']+pointers[i] for i, value in enumerate(parsed['ids'])}
    changes = patch['changes']
    require(len(changes) == len({row['stringId'] for row in changes}), 'Duplicate label patch ID')
    edits = []
    descriptions = patch.get('kind') == 'descriptions'
    replacements = description_replacements(raw,patch) if descriptions else {
        row['stringId']: (encode_label(row['expected']), encode_label(row['replacement'])) for row in changes}
    changed_ids = {value for value,(before,after) in replacements.items() if before != after}
    for row in changes:
        value = row['stringId']
        require(value in positions, f'Unknown string ID {value}')
        start = positions[value]
        before, after = replacements[value]
        require(raw[start:start+len(before)] == before, f'Unexpected source label {value}')
        require(start+len(before) <= parsed['stringsEnd'], 'Label runs past text group')
        if before != after:
            aliases = {i for i, p in positions.items() if p == start}
            require(aliases <= changed_ids, 'Replacement would alter an unrequested aliased label')
            edits.append((start, start+len(before), after))
    if not edits:
        return raw
    edits.sort()
    require(all(a[1] <= b[0] for a, b in zip(edits, edits[1:])), 'Overlapping label edits')
    for start, end, _ in edits:
        require(not any(start < parsed['group']+p < end for p in pointers), 'Text pointer enters edited label')
    def relocate(position):
        return position + sum(len(data)-(end-start) for start, end, data in edits if end <= position)
    output = bytearray()
    position = 0
    for start, end, after in edits:
        output.extend(raw[position:start]); output.extend(after); position = end
    output.extend(raw[position:parsed['stringsEnd']])
    new_end = len(output)
    for i, old in enumerate(pointers):
        struct.pack_into('<I', output, parsed['group']+4+4*i,
                         relocate(parsed['group']+old)-parsed['group'])
    struct.pack_into('<I', output, parsed['text']+8, new_end-parsed['text'])
    output.extend(b'\0' * (-len(output) % 4))
    result = bytes(output)
    check = parse_text(result)
    require(check['ids'] == parsed['ids'], 'String directory changed')
    before_records, after_records = text_records(raw), text_records(result)
    require(all(after_records[value] == (replacements[value][1] if value in changed_ids else data)
                for value,data in before_records.items()), 'Unexpected text record change')
    return result


def apply_labels(read, patches):
    from .base_jobs import safe_path
    files = {}
    for patch in patches:
        relative = safe_path(patch['archivePath'])
        require(relative not in files, 'Duplicate text output resource')
        files[relative] = apply_text_patch(read(relative), patch)
    return files
