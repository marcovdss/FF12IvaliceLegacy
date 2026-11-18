"""The 2026-11-16 weapons and accessories review (Marco: "downsides, never punishments"): what the shipped text and the
modules now say, and that the removed rules are gone from every source. Behaviour is covered next to each module:
test_family_identities, test_weapons_release_0134, test_combat_balance, test_mp_economy, test_accessory_effects."""
import json
from pathlib import Path
import re
import unittest

from ivalice_legacy import clan_primer as CP

ROOT = Path(__file__).resolve().parents[1]
GONE = ('Reckless', 'Braced', 'Open', 'Long haft', 'recklessTaken', 'recklessDent', 'clearReckless', 'BRACED_PUSH',
        'OPEN_WINDOW', 'LURE_SCALE')


def text(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def family_lines():
    src = text('prototypes/family_identities.lua')
    block = src[src.index('M.FAMILY_LINES = {'):src.index('function M.familyLine')]
    out = {}
    for m in re.finditer(r"^  (\w+) = \{'([^']+)', (['\"])(.*?)\3\},$", block, re.M):
        out[m.group(1)] = (m.group(2), m.group(4))
    return out


class FamilyTextTests(unittest.TestCase):
    def test_the_family_lines_describe_the_new_rules(self):
        lines = family_lines()
        self.assertEqual(len(lines), 18)
        sword, great, katana = lines['sword'][1], lines['greatsword'][1], lines['katana'][1]
        self.assertIn('no answer to armor', sword)
        self.assertNotIn('charge back', sword)
        self.assertIn('hits its target in full and every foe around it for half of that hit', great)
        self.assertIn("slow, most greatswords can't reach flying foes, and an axe or a spear does more against a lone armored foe", great)
        self.assertIn("full Defense", katana)
        self.assertNotIn('no evasion', katana)
        self.assertIn('most have no parry of their own', lines['spear'][1])
        self.assertNotIn('never parries', lines['spear'][1])
        self.assertIn('weakest attack and the least Magick', lines['rod'][1])
        self.assertNotIn('Lure', lines['rod'][1])
        self.assertIn('barely parries', lines['axe'][1])
        self.assertIn('Breaks go on', lines['axe'][1])
        self.assertNotIn('stronger', lines['axe'][1])
        self.assertIn("Strength doesn't raise them", lines['gun'][1])
        self.assertIn('slowest weapon to charge', lines['gun'][1])
        for name in ('dagger', 'ninjaSword'):
            self.assertIn('keep less of their force', lines[name][1])

    def test_no_family_line_names_a_removed_rule(self):
        for name, (_, line) in family_lines().items():
            for word in ('Reckless', 'Braced', 'Lure', 'Long haft', 'no evasion'):
                self.assertNotIn(word, line, name)

    def test_the_tips_say_the_same_and_fit_the_message_font(self):
        patch = json.loads(text('patches/families_tip.json'))
        bodies = [t['body'] for t in CP.tips(patch)]          # validates the glyphs, 32 lines and 56 columns
        joined = '\n'.join(CP.visible(b) for b in bodies)
        for word in ('Reckless', 'Braced', 'Lure', 'Long haft', 'no evasion', 'stronger'):
            self.assertNotIn(word, joined, word)
        for glyph in '%()+/;':
            self.assertNotIn(glyph, joined, 'the message font has no ' + glyph)
        flat = ' '.join(joined.split())
        for phrase in ('An attack hits its target in full and every foe around it for half of that hit',
                       'most cannot reach flying foes, and an axe or a spear does more against a lone armored foe',
                       'frequent combos and a little evade',
                       'Every combo hit meets the foe\'s full Defense',
                       'a katana leaves no hand for a shield',
                       'A sword has no answer to armor or to a foe\'s evasion',
                       'The Breaks go on after that',
                       'Heavy armor softens a blow by up to half, light by up to 40 percent and mystic by up to 30, unless your other Defense already softens it more than that',   # damage formula 0.8.2 (review 2026-11-18)
                       'A counter always gets a parry chance of at least 30 percent, except for fists',
                       'Counter Boost quadruples the chance',
                       'a Warrior at low HP cuts it open'):
            self.assertIn(phrase, flat)
        self.assertTrue(all(len(CP.visible(b).split('\n')) <= CP.MAX_BODY_LINES for b in bodies))

    def test_cleave_ii_text_breaks_the_foe(self):
        src = text('prototypes/proficiency.lua')
        code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('--'))
        self.assertNotIn('Reckless', code)       # the player-facing stage texts live in code lines
        self.assertIn('and Break the foe', src)
        self.assertIn('the foe. They also give one ally Bravery at half duration', src)

    def test_the_removed_rules_are_gone_from_every_runtime_source(self):
        for rel in ('prototypes/family_identities.lua', 'prototypes/damage_formula.lua', 'prototypes/proficiency.lua',
                    'prototypes/proficiency_runtime.lua', 'prototypes/mp_economy.lua', 'prototypes/forge_diagnostic_261.lua',
                    'package/x64/scripts/IvaliceLegacy/combat_balance.lua'):
            src = text(rel)
            code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('--'))
            for word in GONE:
                if word in ('Reckless', 'Braced', 'Open', 'Long haft'):
                    continue            # the words appear in comments that say what was removed
                self.assertNotIn(word, code, (rel, word))
            self.assertNotIn('recklessStep', code, rel)

    def test_ranger_text_names_the_attacker_it_stops_and_resonance_refunds_half(self):
        hyb = text('prototypes/hybrid_proficiency.lua')
        self.assertIn('may Stop the attacker 15%', hyb)
        self.assertIn('Slows another foe, may Stop the attacker', hyb)
        self.assertNotIn('may Stop it 15% of the time and gives the party Shell', hyb)
        self.assertIn("(ev == 'struck' and 'the attacker' or 'it')", hyb)
        pro = text('prototypes/proficiency.lua')
        self.assertIn('refunds half its MP cost', pro)
        self.assertNotIn('refunds 60%', pro)
        self.assertIn('(L[2] >= 4 and 50 or (L[2] >= 3 and 40 or 30))', pro)

    def test_module_versions(self):
        self.assertIn("M.version = '0.6.0'", text('prototypes/family_identities.lua'))
        self.assertIn("version = '0.8.2'", text('prototypes/damage_formula.lua'))
        self.assertIn("version = '0.3.0'", text('prototypes/mp_economy.lua'))
        self.assertIn("version = '0.3.0'", text('package/x64/scripts/IvaliceLegacy/combat_balance.lua'))
        self.assertIn("version = '0.2.0'", text('prototypes/accessory_effects.lua'))


if __name__ == '__main__':
    unittest.main()
