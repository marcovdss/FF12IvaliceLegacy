"""Mana Leech Silence fix. These replay the repository prototypes (the installed files still carry the
old behaviour; see test_installed_replay.KNOWN) through real Forge hook calls.
"""
from pathlib import Path
import unittest

import runtime_world

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def repo(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ManaLeechSilenceTest(unittest.TestCase):
    """Mana Leech I-2: "A foe drained down to 0 MP is Silenced". The per-hit MP cap (2% of the receiver's max MP) can
    cut a drain short, and then the foe is not drained dry."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        runtime_world.world(self.lua, loader=repo)

    def hit(self, caster_max_mp, foe_mp):
        return self.lua.execute(r'''
          PARTY_IDS = {1}
          local gm = party(1, {job1=P.JOB.greenMage, maxMp=%d, mp=10})
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000, mp=%d, maxMp=100})
          local f = F({initial=900, target={removedHp=1000, identifier=9, work=works[900]}})
          R.hook165(f, gm, foe, classes, helpers)
          return f.target.addedStatusEffects[STATUS.silence], f.target.removedMp
        ''' % (caster_max_mp, foe_mp))

    def test_a_drain_the_cap_cuts_short_does_not_silence(self):
        silence, drained = self.hit(300, 40)         # cap = 6 MP: the foe keeps 34
        self.assertEqual((silence, drained), (0, 6))

    def test_a_drain_that_empties_the_foe_silences_it(self):
        silence, drained = self.hit(3000, 40)        # cap = 60 MP: the whole pool goes
        self.assertEqual((silence, drained), (1, 40))

    def test_a_foe_with_no_mp_is_never_silenced_by_a_drain(self):
        silence, drained = self.hit(3000, 0)
        self.assertEqual(silence, 0)


if __name__ == '__main__':
    unittest.main()
