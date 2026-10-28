"""Job stat trainings as a config for The Insurgent's Stat Lores (x64/scripts/config/TheInsurgentsStatLoresConfig.lua).

Stat Lores sums every config row whose augment a character holds, per stat type, so one augment may carry several
rows. The native config has one row per lore; the trainings replace the rows of their 24 augments and keep the rest.
"""
import struct

CONFIG = 'x64/scripts/config/TheInsurgentsStatLoresConfig.lua'
TYPES = {'hp': 'hp', 'mp': 'mp', 'strength': 'strength', 'magickPower': 'magickPower', 'vitality': 'vitality',
         'speed': 'speed', 'attackPower': 'attackPower', 'defense': 'defense', 'magickResist': 'magickResist',
         'evadeParry': 'evadeParry', 'evadeWeapon': 'evadeWeapon', 'evadeShield': 'evadeShield',
         'magickEvadeShield': 'magickEvadeShield'}
# Augment bit of every stat lore (Stat Lores helpers.lua), in the native config's order, with its native row.
NATIVE = ([(f'hpLore{i}', a, 'hp', v) for i, a, v in zip(range(1, 13), range(74, 86),
                                                          (30, 70, 110, 150, 190, 230, 270, 310, 350, 390, 435, 500))]
          + [(f'battleLore{i}', a, 'strength', 1) for i, a in
             zip(range(1, 17), (64, 65, 66, 67, 68, 35, 42, 43, 44, 45, 46, 47, 60, 61, 62, 63))]
          + [(f'magickLore{i}', a, 'magickPower', 1) for i, a in
             zip(range(1, 17), (69, 70, 71, 72, 73, 87, 97, 98, 99, 100, 101, 102, 19, 22, 24, 26))])
AUGMENT_BITS = {name: bit for name, bit, _, _ in NATIVE}


def require(value, message):
    if not value:
        raise ValueError(message)


def steps(patch):
    require(patch.get('schemaVersion') == 1, 'Unsupported stat-training recipe')
    trainings = patch['trainings']
    require(len(trainings) == 12 and len({t['job'] for t in trainings}) == 12, 'Expected one training per job')
    out = []
    for t in trainings:
        require(len(t['steps']) == 2, f"{t['job']}: expected training I and II")
        for n, step in enumerate(t['steps'], 1):
            require(step['augment'] in AUGMENT_BITS, f"{t['job']}: unknown augment {step['augment']}")
            require(step['stats'] and all(k in TYPES and type(v) is int and 0 < v <= 999
                                          for k, v in step['stats'].items()), f"{t['job']}: invalid stats")
            require(type(step['lp']) is int and 0 < step['lp'] <= 255, f"{t['job']}: invalid LP")
            out.append({**step, 'job': t['job'], 'name': f"{t['name']} {'I' * n}"})
    require(len({s['licenseId'] for s in out}) == len({s['augment'] for s in out}) == 24, 'Duplicate training')
    return out


def check_licenses(licenses, patch):
    """Each training license must grant exactly the augment its rows are written for (battle pack section 12)."""
    count, stride, start = struct.unpack_from('<III', licenses, 4)
    require(licenses[:4] == b'st2e' and (count, stride, start) == (361, 24, 32), 'Unsupported license table')
    for step in steps(patch):
        at = start + stride * step['licenseId']
        contents = [v for v in struct.unpack_from('<8H', licenses, at + 8) if v != 0xFFFF]
        require(licenses[at + 5] == 17 and contents == [AUGMENT_BITS[step['augment']]],
                f"License {step['licenseId']} does not grant {step['augment']}")


def render(patch):
    trained = {s['augment']: s for s in steps(patch)}
    rows = []
    for name, _, kind, value in NATIVE:
        if name in trained:
            rows += [f"    {{augments.{name}, types.{TYPES[k]}, {v}}}, -- {trained[name]['name']} ({trained[name]['job']})"
                     for k, v in trained[name]['stats'].items()]
        else:
            rows.append(f'    {{augments.{name}, types.{kind}, {value}}},')
    head = ('-- Ivalice Legacy job stat trainings (generated from patches/stat_training.json). Rows for the same augment\n'
            '-- add up; lores that are not trainings keep their native values.\n')
    return (head + 'local function config(augments, types)\n  local lores = {\n' + '\n'.join(rows)
            + '\n  }\n\n  return lores\nend\n\nreturn config\n').encode()
