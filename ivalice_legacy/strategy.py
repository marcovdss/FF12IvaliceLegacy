"""Add bounded encounter branches without replacing the recipient's rules or phase links."""
from .enemy_ai_bank import Rule, Script

SELF, FLAG_OFF = 4, 14083
ENABLE, IDLE, JUMP = 16674, 16417, 16662
ONCE, ACT = 0x0800, 0x4800


def conditions(values):
    values = tuple(tuple(c) for c in values)
    if len(values) > 3 or any(len(c) != 3 for c in values):
        raise ValueError('a rule supports at most three complete conditions')
    return values + ((0, 0, 0),) * (3 - len(values))


def enrich(script, entry, target, action, trigger, target_conditions, flag, windup_flag=None, parameter=0,
           entry_index=0):
    """Return a copy with a once-per-actor branch and its exact removal recipe.

    Predicate evaluation stays at the branch entry. Once entered, changing HP/status
    after the action cannot prevent the completion flag from being written. Separate
    wind-up and completion flags prevent alternating idle/action loops on rescans.
    Flags must already have been reserved against the actor's whole bank and map.
    """
    integer = lambda v, lo, hi: type(v) is int and lo <= v <= hi
    if not integer(entry, 0, 11) or not integer(target, 0, 11) or entry == target:
        raise ValueError('invalid branch groups')
    if not script.group_sizes[entry] or script.group_sizes[target]:
        raise ValueError('entry must exist and branch destination must be empty')
    if not integer(entry_index, 0, script.group_sizes[entry]):
        raise ValueError('invalid entry insertion index')
    if not integer(action, 0, 542) or not integer(flag, 0, 31):
        raise ValueError('invalid action or completion flag')
    if windup_flag is not None and (not integer(windup_flag, 0, 31) or windup_flag == flag):
        raise ValueError('invalid wind-up flag')
    guard = (SELF, FLAG_OFF, flag)
    jump = Rule(JUMP + target, 100, 0x40, conditions([*trigger, guard]))
    steps = []
    if windup_flag is not None:
        windup = (SELF, FLAG_OFF, windup_flag)
        steps += [Rule(IDLE, 2, ONCE, conditions([windup])),
                  Rule(ENABLE, windup_flag, ONCE, conditions([windup]))]
    steps += [Rule(action, parameter, ACT, conditions([*target_conditions, guard])),
              Rule(ENABLE, flag, ONCE, conditions([guard])),
              Rule(IDLE, 2, ONCE), Rule(JUMP + entry, 100)]
    groups, pos = [], 0
    for size in script.group_sizes:
        groups.append(list(script.rules[pos:pos + size]))
        pos += size
    if pos != len(script.rules) or len(groups[entry]) >= 255:
        raise ValueError('invalid or full entry group')
    groups[entry].insert(entry_index, jump)
    groups[target] = steps
    result = Script([len(g) for g in groups], [r for g in groups for r in g], script.trailing)
    result.to_bytes()
    recipe = {'entry': entry, 'target': target, 'entryIndex': entry_index, 'entryRule': jump.to_bytes().hex(),
              'steps': [s.to_bytes().hex() for s in steps], 'flag': flag, 'windupFlag': windup_flag}
    if undo(result, recipe).to_bytes() != script.to_bytes():
        raise AssertionError('encounter enrichment failed preservation proof')
    return result, recipe


def undo(script, recipe):
    """Remove one exact enrichment, refusing a stale/edited recipe."""
    groups, pos = [], 0
    for size in script.group_sizes:
        groups.append(list(script.rules[pos:pos + size])); pos += size
    entry, target = recipe['entry'], recipe['target']
    index = recipe['entryIndex']
    if not 0 <= index < len(groups[entry]) or groups[entry][index].to_bytes().hex() != recipe['entryRule']:
        raise ValueError('enrichment entry changed')
    if [r.to_bytes().hex() for r in groups[target]] != recipe['steps']:
        raise ValueError('enrichment steps changed')
    groups[entry].pop(index); groups[target] = []
    return Script([len(g) for g in groups], [r for g in groups for r in g], script.trailing)
