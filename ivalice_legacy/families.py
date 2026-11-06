"""Family signatures: what makes a family fight the way it does, as checked operations on its battle-logic scripts.

Three operations, all built from patterns native scripts already use:
- swap: attack, move and turn rules choose a different opponent, the way native scripts pair Lowest Defense across them.
- prefer: a copy of a rule with another selector goes in front of it, so the original stays as the fallback.
- branch: a jump at the top of the engaged group leads to a small group of steps, usually guarded by an AI flag so it runs
  once, the way Flowering Cactoid and Ash Wyrm guard their recovery.

The plan holds coordinates and our own rules, never another mod's bytes. Rules are placed by group, and group jumps are by
group number, so inserting rules cannot redirect an existing jump.
"""
from dataclasses import replace

from .enemy_ai_bank import Bank, Rule

FOE = 1
JUMP_BASE, JUMPABLE_GROUPS = 16662, 12
EMPTY = (0, 0, 0)


def area_entries(plan):
    """{area: [{family, script, ops}]}, rejecting a script named twice."""
    areas, seen = {}, set()
    for family in plan['families']:
        for row in family['scripts']:
            key = row['area'], row['script']
            if key in seen:
                raise ValueError(f'family script named twice: {key}')
            seen.add(key)
            areas.setdefault(row['area'], []).append({'family': family['family'], 'script': row['script'], 'ops': row['ops']})
    return areas


def _conditions(triples):
    triples = [tuple(t) for t in triples]
    if len(triples) > 3:
        raise ValueError('a rule holds at most three conditions')
    return tuple(triples + [EMPTY] * (3 - len(triples)))


def _swap(rule, before, after, param=0):
    hits = [i for i, (target, condition, _) in enumerate(rule.conditions) if target == FOE and condition in before]
    if len(hits) != 1:
        raise ValueError(f'rule {rule.action_object} has {len(hits)} foe selectors in {before}')
    conditions = list(rule.conditions)
    conditions[hits[0]] = (FOE, after, param)
    return replace(rule, conditions=tuple(conditions))


def _group_start(script, group):
    return sum(script.group_sizes[:group])


def _apply_script(script, ops):
    """Swaps change rules in place; inserts are collected against original indices, then placed."""
    rules, sizes = list(script.rules), list(script.group_sizes)
    inserts, appended = {}, []

    def group_of(index):
        total = 0
        for group, size in enumerate(sizes):
            total += size
            if index < total:
                return group
        raise ValueError(f'rule {index} is outside the script')

    for op in ops:
        kind = op['op']
        if kind == 'swap':
            for at in op['rules']:
                rules[at] = _swap(rules[at], op['before'], op['after'])
        elif kind == 'prefer':
            at = op['rule']
            source = rules[at]
            copy = _swap(source, op['before'], op['after'], op.get('param', 0))
            inserts.setdefault(at, []).append((group_of(at), copy))
        elif kind == 'branch':
            entry = op['group']
            if not 0 <= entry < JUMPABLE_GROUPS or not sizes[entry]:
                raise ValueError(f'branch entry group {entry} is empty')
            target = op.get('reuse')
            if target is None:
                target = max(i for i, size in enumerate(sizes) if size) + 1 + len(appended)
                if target >= JUMPABLE_GROUPS:
                    raise ValueError('no jumpable group left for a branch')
                steps = [Rule(s[0], s[1], s[2], _conditions(s[3])) for s in op['steps']]
                steps.append(Rule(JUMP_BASE + entry, 100, 0, _conditions([])))
                appended.append((target, steps))
            elif not sizes[target]:
                raise ValueError(f'branch reuses empty group {target}')
            jump = Rule(JUMP_BASE + target, op.get('chance', 100), op.get('flags', 0x40), _conditions(op['when']))
            inserts.setdefault(_group_start(script, entry), []).insert(0, (entry, jump))
        else:
            raise ValueError(f'unknown family operation {kind}')
    result = []
    for at, rule in enumerate(rules):
        for group, extra in inserts.get(at, ()):
            result.append(extra)
            sizes[group] += 1
        result.append(rule)
    for target, steps in appended:
        result += steps
        sizes[target] = len(steps)
    script.rules, script.group_sizes = result, sizes


def apply(section, entries, unit_links=()):
    """Return the section with each entry's operations applied; every other script stays byte for byte."""
    bank = Bank.from_bytes(section)
    before = [s.to_bytes() for s in bank.scripts]
    touched = {entry['script'] for entry in entries}
    if len(touched) != len(entries):
        raise ValueError('script edited twice')
    for entry in entries:
        _apply_script(bank.scripts[entry['script']], entry['ops'])
    bank.validate_jumps(unit_links)
    output = bank.to_bytes()
    parsed = Bank.from_bytes(output)
    for i, script in enumerate(parsed.scripts):
        if i not in touched and script.to_bytes() != before[i]:
            raise AssertionError('an unnamed script changed')
    if parsed.to_bytes() != output:
        raise AssertionError('family bank failed its round trip')
    return output
