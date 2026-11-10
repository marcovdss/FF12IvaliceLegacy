"""Magicks: the party's spell power, Expose and Shear. Foes keep theirs, through Forge's own functions."""
import json
import os
import re
import struct
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / 'package/x64/scripts/IvaliceLegacy/magicks.lua'
ADAPTERS = ROOT / 'package/x64/scripts/config/TheInsurgentsForgeConfig/functions'
GAME = os.environ.get('IVALICE_LEGACY_GAME')
# Forge's own functions, copied in spirit: what a fixture must stand for. The real files are read when the game is present.
ORIGINAL_62 = '''
return function(formula, caster, target, functions, classes, helpers)
  formula.power = ((caster.magickPower * (caster.magickPower + caster.level)) / 256) + 2
  local action = bpack.section14[formula.action]
  local extraAmount = action.power / 8
  if extraAmount >= 0.01 then extraAmount = helpers.getRandomNumber(extraAmount * 100) / 100 end
  formula.multiplier = action.power + extraAmount
  formula.modifier = target.magickResist
end
'''
ORIGINAL_BREAK = '''
return function(formula, caster, target, functions, classes, helpers)
  if target.%s > 0 then formula.target.%s = target.%s * 0.9 else formula.skipState = 1; formula.outcomeType = 6 end
end
'''
ORIGINAL_181 = '''
return function(formula, caster, target, functions, classes, helpers)
  if target.lands then formula.target.addedStatusEffects[17] = 1 else formula.skipState = 1 end
end
'''


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.g = self.lua.globals()
        self.lua.execute('bpack = {section14 = setmetatable({}, {__index = function(_, a) return {power = 200} end})}')
        table = TABLE.read_text(encoding='utf-8')
        originals = {'62': ORIGINAL_62, '138': ORIGINAL_BREAK % (('defense',) * 3), '139': ORIGINAL_BREAK % (('magickResist',) * 3),
                     '181': ORIGINAL_181}
        self.printed = []
        self.g.print = lambda s: self.printed.append(s)

        def fake_load(path, *_):
            if path.endswith('scripts/IvaliceLegacy/magicks.lua'):
                return self.lua.eval('function(src) return load(src) end')(table)
            name = path.rsplit('/', 1)[-1][:-4]
            if name not in originals:
                return None  # like the real loadfile for a missing file (e.g. 181's Proficiency runtime)
            return self.lua.eval('function(src) return load(src) end')(originals[name])
        self.g.loadfile = fake_load
        self.lua.execute('IvaliceLegacyMagicks = nil')

    def adapter(self, name):
        src = (ADAPTERS / f'{name}.lua').read_text(encoding='utf-8')
        return self.lua.execute(src)

    def run62(self, action, caster_type):
        fn = self.adapter('62')
        self.g.fn = fn
        return self.lua.execute(f'''
          local f = {{action = {action}}}
          fn(f, {{type = {caster_type}, magickPower = 50, level = 40}}, {{magickResist = 10}}, nil, nil, {{getRandomNumber = function(n) return 0 end}})
          return f
        ''')

    def test_party_cast_uses_the_table(self):
        f = self.run62(33, 0)                                # Flare
        self.assertEqual(f.multiplier, 98)
        self.assertEqual(f.modifier, 10)                     # Forge's own work stays
        self.assertAlmostEqual(f.power, 50 * 90 / 256 + 2)

    def test_foes_keep_native_power(self):
        self.assertEqual(self.run62(33, 1).multiplier, 200)  # the fixture's native row

    def test_spell_not_in_the_table_stays_native(self):
        self.assertEqual(self.run62(18, 0).multiplier, 200)  # Fire

    def test_random_bonus_is_an_eighth_of_the_new_power(self):
        fn = self.adapter('62')
        self.g.fn = fn
        seen = self.lua.execute('''
          local asked = {}
          local f = {action = 33}
          fn(f, {type = 0, magickPower = 50, level = 40}, {magickResist = 10}, nil, nil,
             {getRandomNumber = function(n) asked[#asked + 1] = n; return 0 end})
          return asked[#asked]
        ''')
        self.assertEqual(seen, 98 / 8 * 100)

    def test_expose_and_shear(self):
        for name, field in (('138', 'defense'), ('139', 'magickResist')):
            self.g.fn = self.adapter(name)
            party = self.lua.execute(f'local f = {{target = {{}}}}; fn(f, {{type = 0}}, {{{field} = 100}}); return f.target.{field}')
            foe = self.lua.execute(f'local f = {{target = {{}}}}; fn(f, {{type = 1}}, {{{field} = 100}}); return f.target.{field}')
            self.assertAlmostEqual(party, 70)
            self.assertAlmostEqual(foe, 90)
            none = self.lua.execute(f'local f = {{target = {{}}}}; fn(f, {{type = 0}}, {{{field} = 0}}); return f.skipState')
            self.assertEqual(none, 1)                        # nothing to take: Forge's own miss

    def decoy(self, action=70, caster=0, lands='true'):
        self.g.fn = self.adapter('181')
        return self.lua.execute(f'''
          local f = {{action = {action}, target = {{addedStatusEffects = {{}}, statusEffectDurations = {{}}}}}}
          fn(f, {{type = {caster}}}, {{lands = {lands}}})
          return f.target.statusEffectDurations[17], f.target.addedStatusEffects[17], f.skipState
        ''')

    def test_party_decoy_lasts_about_45_seconds(self):
        self.assertEqual(self.decoy(), (27000, 1, None))

    def test_only_the_partys_decoy_is_longer(self):
        self.assertEqual(self.decoy(caster=1), (None, 1, None))       # a foe's Decoy keeps the status table's time
        self.assertEqual(self.decoy(action=37), (None, 1, None))      # another status spell

    def test_decoy_refused_by_forge_stays_refused(self):
        self.assertEqual(self.decoy(lands='false'), (None, None, 1))

    def test_bad_table_leaves_spells_native(self):
        self.g.loadfile = None
        real = self.lua.eval('function(src) return load(src) end')(ORIGINAL_62)

        def broken(path, *_):
            return real if path.endswith('functions/62.lua') else None
        self.g.loadfile = broken
        self.g.fn = self.adapter('62')
        self.assertTrue(any('stay native' in s for s in self.printed))


class TableTest(unittest.TestCase):
    def rows(self):
        text = TABLE.read_text(encoding='utf-8')
        return [(int(a), n, int(b), int(c), w) for a, n, b, c, w in
                re.findall(r"\[(\d+)\] = \{'([\w ]+)', (\d+), (\d+), '(.*)'\},", text)]

    def test_every_spell_has_its_own_reason_and_comes_down(self):
        rows = self.rows()
        self.assertEqual(len(rows), 19)
        self.assertEqual(len({r[4] for r in rows}), 19)      # no blanket reason
        for action, name, native, power, why in rows:
            self.assertLess(power, native, name)
            self.assertGreater(len(why), 20, name)

    def test_siblings_keep_their_order(self):
        p = {n: c for _, n, _, c, _ in self.rows()}
        self.assertLess(p['Fira'], p['Thundara'])
        self.assertLess(p['Thundara'], p['Blizzara'])
        self.assertLess(p['Firaga'], p['Thundaga'])
        self.assertLess(p['Thundaga'], p['Blizzaga'])
        self.assertLess(p['Ardor'], p['Scathe'])

    @unittest.skipUnless(GAME and (Path(GAME) / 'FFXII_TZA.vbf').is_file(), 'set IVALICE_LEGACY_GAME')
    def test_native_values_and_ids_are_the_game_s(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from ivalice_legacy.formats import battle_pack_section
        from ivalice_legacy.vbf import VBF
        pack = VBF(Path(GAME) / 'FFXII_TZA.vbf').read('ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin')
        section = battle_pack_section(pack, 14)
        count, size = struct.unpack_from('<IH', section, 4)
        entry = struct.unpack_from('<I', section, 12)[0]
        for action, name, native, _, _ in self.rows():
            at = entry + action * size
            self.assertEqual(section[at + 8], 7, name)       # formula 7, the one that runs function 62
            self.assertEqual(section[at + 16], native, name)

    @unittest.skipUnless(GAME and (Path(GAME) / 'x64/scripts/TheInsurgentsForge/functions/62.lua').is_file(), 'set IVALICE_LEGACY_GAME')
    def test_forge_functions_are_what_the_adapters_expect(self):
        f = Path(GAME) / 'x64/scripts/TheInsurgentsForge'
        self.assertIn('formula.multiplier = action.power + extraAmount', (f / 'functions/62.lua').read_text())
        self.assertIn('formula.target.defense = target.defense * 0.9', (f / 'functions/138.lua').read_text())
        self.assertIn('formula.target.magickResist = target.magickResist * 0.9', (f / 'functions/139.lua').read_text())
        self.assertRegex((f / 'formulas.lua').read_text(), r'\[7\] = \{62,')


if __name__ == '__main__':
    unittest.main()
