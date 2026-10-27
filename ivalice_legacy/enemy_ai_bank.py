"""Preserving ARD section-3 battle-logic banks and source-pinned rule edits.

Only the twelve documented group-jump objects (16662..16673) and four slot
jumps (16658..16661) are interpreted. Unknown rule fields and bank bytes remain
opaque. This module returns section overlays; it never writes game files.
"""
from dataclasses import dataclass, field
import hashlib
import struct

from .formats import ard_section

RULE = struct.Struct('<HhhHHHHHHHHH')
SLOT_JUMPS = range(16658, 16662)
GROUP_JUMPS = range(16662, 16674)
FLAG_TESTS = {13824, 13825, 13826, 14080, 14081, 14082}
FLAG_WRITES = {16713, 16725, 16726, 16727}


def _integer(value, minimum, maximum, what):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{what} must be an integer in {minimum}..{maximum}')
    return value


def _hex(value, what, size=None):
    if not isinstance(value, str):
        raise ValueError(f'{what} must be hexadecimal bytes')
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f'{what} must be hexadecimal bytes') from error
    if size is not None and len(raw) != size:
        raise ValueError(f'{what} must contain {size} bytes')
    return raw


def _pin(raw, digest, what):
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f'{what} requires a complete sha256 pin')
    if hashlib.sha256(raw).hexdigest() != digest.lower():
        raise ValueError(f'{what} sha256 mismatch')


@dataclass(frozen=True)
class Rule:
    action_object: int
    parameter: int = 0
    flags: int = 0
    conditions: tuple = ((0, 0, 0), (0, 0, 0), (0, 0, 0))

    @classmethod
    def from_bytes(cls, raw):
        if len(raw) != 24:
            raise ValueError('a rule must contain exactly 24 bytes')
        fields = RULE.unpack(raw)
        return cls(*fields[:3], tuple(tuple(fields[i:i + 3]) for i in (3, 6, 9)))

    def to_bytes(self):
        _integer(self.action_object, 0, 65535, 'action object')
        _integer(self.parameter, -32768, 32767, 'action parameter')
        _integer(self.flags, -32768, 32767, 'rule flags')
        if len(self.conditions) != 3 or any(len(c) != 3 for c in self.conditions):
            raise ValueError('a rule requires three complete condition triples')
        values = []
        for condition in self.conditions:
            values.extend(_integer(v, 0, 65535, 'condition field') for v in condition)
        return RULE.pack(self.action_object, self.parameter, self.flags, *values)


@dataclass
class Script:
    group_sizes: list
    rules: list
    trailing: bytes = b''

    def to_bytes(self):
        if len(self.group_sizes) != 32:
            raise ValueError('a script requires all 32 group sizes')
        groups = bytes(_integer(v, 0, 255, 'group size') for v in self.group_sizes)
        if sum(groups) != len(self.rules):
            raise ValueError('group sizes do not match rule count')
        return groups + b''.join(r.to_bytes() for r in self.rules) + self.trailing


@dataclass
class Bank:
    scripts: list
    unknown: int = 100
    prefix: bytes = b''
    trailing: bytes = b''

    @classmethod
    def from_bytes(cls, raw):
        if len(raw) < 8:
            raise ValueError('truncated battle-logic bank')
        count, unknown = struct.unpack_from('<HH', raw)
        directory_end = 8 + 4 * count
        if directory_end > len(raw):
            raise ValueError('truncated script directory')
        offsets = struct.unpack_from(f'<{count + 1}i', raw, 4)
        if (offsets[0] < directory_end or offsets[-1] > len(raw)
                or any(a >= b for a, b in zip(offsets, offsets[1:]))):
            raise ValueError('unsupported overlapping, aliased or unordered script bounds')
        scripts = []
        for start, end in zip(offsets, offsets[1:]):
            if start + 32 > end:
                raise ValueError('truncated script group table')
            groups = list(raw[start:start + 32])
            stop = start + 32 + 24 * sum(groups)
            if stop > end:
                raise ValueError('rules overrun script boundary')
            rules = [Rule.from_bytes(raw[p:p + 24]) for p in range(start + 32, stop, 24)]
            scripts.append(Script(groups, rules, raw[stop:end]))
        return cls(scripts, unknown, raw[directory_end:offsets[0]], raw[offsets[-1]:])

    def to_bytes(self):
        _integer(len(self.scripts), 0, 65535, 'script count')
        _integer(self.unknown, 0, 65535, 'bank header field')
        parts = [script.to_bytes() for script in self.scripts]
        position = 8 + 4 * len(parts) + len(self.prefix)
        offsets = [position]
        for part in parts:
            position += len(part)
            offsets.append(position)
        if position > 0x7fffffff:
            raise ValueError('script directory exceeds signed offset range')
        return (struct.pack('<HH', len(parts), self.unknown)
                + struct.pack(f'<{len(offsets)}i', *offsets)
                + self.prefix + b''.join(parts) + self.trailing)

    def validate_jumps(self, unit_links=()):
        """Validate groups and resolved slot destinations; return unresolved slot references.

        Each unit link is its complete four-script A/B/C/D tuple. An unreferenced
        script has no actor context in which a slot jump can be resolved.
        """
        users = {i: [] for i in range(len(self.scripts))}
        for links in unit_links:
            if len(links) != 4:
                raise ValueError('unit AI links require four slots')
            for i in links:
                _integer(i, 0, len(self.scripts) - 1, 'unit AI link')
                users[i].append(links)
        unresolved = []
        for i, script in enumerate(self.scripts):
            script.to_bytes()
            for j, rule in enumerate(script.rules):
                action = rule.action_object
                if action in GROUP_JUMPS and script.group_sizes[action - 16662] == 0:
                    raise ValueError(f'script {i} rule {j} jumps to an empty group')
                if action in SLOT_JUMPS:
                    slot = action - 16658
                    if not users[i]:
                        unresolved.append({'script': i, 'rule': j, 'slot': slot})
                    for links in users[i]:
                        destination = links[slot]
                        if not self.scripts[destination].rules:
                            raise ValueError(f'script {i} rule {j} jumps to an empty script')
        return unresolved

    def flag_usage(self):
        """Static references, not proof that any flag is unused by EBP/native code.

        Numeric write targets are candidates from the rule's active flag-target
        conditions. Interpreter target-selection/lifecycle must still be audited.
        """
        rows = []
        for i, script in enumerate(self.scripts):
            reads, writes, numeric_reads, numeric_writes, unresolved = set(), set(), set(), set(), []
            evidence = []
            for j, rule in enumerate(script.rules):
                found = False
                if rule.action_object in (16674, 16675):
                    writes.add(rule.parameter); found = True
                targets = set()
                for target, condition, parameter in rule.conditions:
                    if (target, condition) == (0, 0):
                        continue
                    if condition in (13827, 14083):
                        reads.add(parameter); found = True
                    scope = _flag_target(target)
                    if scope is not None:
                        targets.add(scope)
                        if condition in FLAG_TESTS:
                            numeric_reads.add(scope); found = True
                if rule.action_object in FLAG_WRITES:
                    numeric_writes.update(targets); found = True
                    if not targets:
                        unresolved.append(j)
                if found:
                    evidence.append({'rule': j, 'rawHex': rule.to_bytes().hex()})
            rows.append({'script': i, 'aiFlagsRead': sorted(reads), 'aiFlagsWritten': sorted(writes),
                         'numericFlagsRead': sorted(numeric_reads),
                         'numericFlagWriteTargets': sorted(numeric_writes),
                         'unresolvedNumericWrites': unresolved, 'evidence': evidence})
        return {'scripts': rows, 'limit': 'Static references only; EBP/native ownership and target selection '
                'must be checked before reserving flags.'}


def _flag_target(target):
    for lo, hi, name in ((12288, 12543, 'global'), (32768, 33023, 'mapIcon'),
                         (36864, 36879, 'battleLogic')):
        if lo <= target <= hi:
            return f'{name}:{target - lo}'
    return None


def _unit_links(raw):
    section = ard_section(raw, 4)
    if len(section) < 16 or section[:4] != b'st2e':
        raise ValueError('invalid ARD unit table')
    count, stride, start = struct.unpack_from('<III', section, 4)
    if stride != 88 or start < 16 or start + count * stride > len(section):
        raise ValueError('invalid ARD unit table bounds')
    return [struct.unpack_from('<4H', section, start + i * stride + 80) for i in range(count)]


def edit_bank(section, edits, unit_links=()):
    """Apply original-index operations with exact before bytes and explicit group tables.

    Script patch: {index, groupSizesBefore:[32], groupSizesAfter:[32], operations:[
      {op:replace|delete|insert, index:<original rule index>, beforeHex:<24 bytes>,
       afterHex:<24 bytes for insert/replace>}]}.
    An append insert uses index=len(source.rules), beforeHex=''. Indices refer to
    the original script; duplicate coordinates are rejected, including insert+
    replace at one coordinate. Insert-before requires the source anchor bytes.
    """
    bank = Bank.from_bytes(section)
    if not isinstance(edits, list):
        raise ValueError('scripts must be a list')
    touched = set()
    originals = [s.to_bytes() for s in bank.scripts]
    for edit in edits:
        index = _integer(edit['index'], 0, len(bank.scripts) - 1, 'script index')
        if index in touched:
            raise ValueError('script edited twice')
        touched.add(index)
        script = bank.scripts[index]
        if edit['groupSizesBefore'] != script.group_sizes:
            raise ValueError('unexpected source group sizes')
        groups = edit['groupSizesAfter']
        if not isinstance(groups, list) or len(groups) != 32:
            raise ValueError('groupSizesAfter requires all 32 counts')
        original = list(script.rules)
        operations = {}
        if not isinstance(edit['operations'], list):
            raise ValueError('operations must be a list')
        for operation in edit['operations']:
            kind = operation['op']
            if kind not in ('replace', 'delete', 'insert'):
                raise ValueError('unsupported rule operation')
            limit = len(original) if kind == 'insert' else len(original) - 1
            at = _integer(operation['index'], 0, limit, 'source rule index')
            if at in operations:
                raise ValueError('duplicate rule operation coordinate')
            expected = original[at].to_bytes() if at < len(original) else b''
            if _hex(operation['beforeHex'], 'beforeHex') != expected:
                raise ValueError('unexpected source rule bytes')
            after = None if kind == 'delete' else Rule.from_bytes(_hex(operation['afterHex'], 'afterHex', 24))
            operations[at] = kind, after
        result = []
        for at in range(len(original) + 1):
            kind, after = operations.get(at, (None, None))
            if kind in ('insert', 'replace'):
                result.append(after)
            if at < len(original) and kind not in ('delete', 'replace'):
                result.append(original[at])
        script.rules, script.group_sizes = result, list(groups)
        script.to_bytes()
    unresolved = bank.validate_jumps(unit_links)
    if any(r['script'] in touched for r in unresolved):
        raise ValueError('edited slot jump has no unit context')
    output = bank.to_bytes()
    reparsed = Bank.from_bytes(output)
    for i, script in enumerate(reparsed.scripts):
        if i not in touched and script.to_bytes() != originals[i]:
            raise AssertionError('an unnamed script changed')
    if reparsed.to_bytes() != output:
        raise AssertionError('edited bank failed preserving round trip')
    return output


def enemy_ai_bank(read, patch):
    """Return only a section-3 overlay from a fully source/result-pinned JSON patch."""
    if patch['section'] != 3:
        raise ValueError('enemy battle logic must target ARD section 3')
    raw = read(patch['file'])
    _pin(raw, patch['sourceFileSha256'], 'source file')
    section = ard_section(raw, 3)
    _pin(section, patch['sourceSectionSha256'], 'source section')
    result = edit_bank(section, patch['scripts'], _unit_links(raw))
    _pin(result, patch['resultSectionSha256'], 'result section')
    return {patch['file'] + '.dir/section_003.bin': result}
