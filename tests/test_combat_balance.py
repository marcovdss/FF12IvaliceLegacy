"""Greatsword cleave before native recoil, Proficiency and leech.

0.2.0 halved every hit of the swing. 0.3.0 (Marco, 2026-11-16): the aimed target takes the full hit and every foe the
sweep reaches takes half of that hit's total. The hit order of the aimed and swept foes is unknown until the game logs
it, so the module is tested in BOTH orders, and with an unreadable aim (the old halving).
"""
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'package/x64/scripts/IvaliceLegacy/combat_balance.lua'


def is_new():
    """The repository module is 0.3.0 or later; an installed (older) copy keeps the halving."""
    return "version = '0.3.0'" in PATH.read_text()


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class CombatBalanceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().balance = self.lua.execute(PATH.read_text())
        self.lua.execute('''
          bpack = {section13 = {count = 4, [0] = {category=2}, [1] = {category=1}, [2] = {category=7}, [3] = {category=3}}}
          native = function(f)
            f.seenNative = f.target.removedHp
            if f.doubleEdged then
              f.target.removedHp = f.target.removedHp * 2
              f.caster.removedHp = f.target.removedHp
            end
          end
          apply = balance.wrap165(native)
          function run(action, casterType, targetType, weapon, amount, double, skip)
            local f = {action=action, target={removedHp=amount}, caster={}, doubleEdged=double, skipState=skip}
            apply(f, {type=casterType, weapon=weapon}, {type=targetType})
            return f
          end
        ''')

    def test_greatsword_hit_without_a_readable_aim_keeps_the_old_halving(self):
        # no initialTargetPointer on the formula: the 0.2.0 rule (half of every hit) is the safe fallback
        f = self.lua.globals().run(150, 0, 1, 0x1000, 101, False, 0)
        self.assertEqual(f.target.removedHp, 50)
        self.assertEqual(f.seenNative, 50)

    def test_native_double_edged_recoil_sees_the_settled_damage(self):
        f = self.lua.globals().run(150, 0, 1, 0x1000, 200, True, 0)
        self.assertEqual((f.target.removedHp, f.caster.removedHp), (200, 200))   # an unreadable aim halves 400 -> 200

    def test_other_weapons_skills_foes_and_misses_are_unchanged(self):
        cases = [(150,0,1,0x1001,101,0), (165,0,1,0x1000,101,0), (150,1,0,0x1000,101,0),
                 (150,0,0,0x1000,101,0), (150,0,1,0x1000,-1,0), (150,0,1,0x1000,0,0),
                 (150,0,1,0x1000,101,1), (150,0,1,0xFFFF,101,0)]
        for action, caster, target, weapon, amount, skip in cases:
            with self.subTest(case=(action,caster,target,weapon,amount,skip)):
                f = self.lua.globals().run(action,caster,target,weapon,amount,False,skip)
                self.assertEqual(f.target.removedHp, amount)


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
@unittest.skipUnless(is_new(), 'combat_balance 0.3.0 rules (the installed copy still halves every hit)')
class SweepRuleTests(unittest.TestCase):
    """Marco's rule: the aimed foe takes the full hit; every swept foe takes half of the aimed hit's total."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().balance = self.lua.execute(PATH.read_text())
        self.lua.execute('''
          bpack = {section13 = {count = 3, [0] = {category=2}, [1] = {category=1}, [2] = {category=2}}}
          logs = {}
          print = function(s) logs[#logs + 1] = s end
          NOW = 5000000
          event = {getClock = function() return NOW end}
          balance.reset()
          native = function(f) f.target.removedHp = f.target.removedHp end
          apply = balance.wrap165(native)
          AIM = 0x5000
          -- one hit at this stage: `own` is what the damage stage settled for THIS target
          function hit(address, own, opts)
            opts = opts or {}
            local f = {action=150, skipState=opts.skip or 0, initialTargetPointer = opts.initial == nil and AIM or opts.initial,
                       target={removedHp=own}, caster={}}
            apply(f, {type=0, address=77, weapon=opts.weapon or 0x1000}, {type=1, address=address})
            return f.target.removedHp
          end
          function count(pattern) local n = 0 for _, l in ipairs(logs) do if l:find(pattern, 1, true) then n = n + 1 end end return n end
        ''')

    def test_aimed_first_the_aimed_foe_is_full_and_every_swept_foe_is_half_of_it(self):
        self.lua.execute('''
          assert(hit(0x5000, 400) == 400, 'the aimed target takes its full hit')
          assert(hit(0x6000, 90) == 200, 'a swept foe takes half of the aimed hit, not of its own 90')
          assert(hit(0x7000, 900) == 200, 'its own Defense does not matter: a weaker or stronger own hit changes nothing')
          assert(count('sweep order seen aimed-first') == 1, 'one swing is one observation, however many foes it swept')
          assert(count('sweep order=') == 0, 'one observation does not lock the order')
          NOW = NOW + 2000000
          assert(hit(0x5000, 100) == 100 and hit(0x6000, 10) == 50, 'the next swing records its own aimed hit')
          assert(count('sweep order=aimed-first') == 1, 'two matching swings lock it, logged once')
          NOW = NOW + 2000000
          assert(hit(0x5000, 100) == 100 and hit(0x6000, 10) == 50)
          assert(count('sweep order=') == 1)
        ''')

    def test_swept_first_falls_back_to_half_of_its_own_hit_and_locks_after_two_swings(self):
        self.lua.execute('''
          assert(hit(0x6000, 90) == 45, 'no aimed hit seen yet: half of its own hit')
          assert(hit(0x5000, 400) == 400, 'the aimed target is still full')
          assert(count('sweep order seen swept-first') == 1 and count('sweep order=') == 0, 'one swing does not lock')
          NOW = NOW + 2000000
          assert(hit(0x6000, 80) == 40 and hit(0x5000, 300) == 300)
          assert(count('sweep order=swept-first') == 1, 'the second matching swing locks the order')
          NOW = NOW + 20000
          assert(hit(0x6000, 70) == 35, 'locked: a swept foe never reads the previous swing')
          assert(hit(0x5000, 300) == 300)
          assert(count('sweep order=') == 1)
        ''')

    def test_one_odd_swing_does_not_lock_the_wrong_order(self):
        self.lua.execute('''
          assert(hit(0x6000, 90) == 45 and hit(0x5000, 400) == 400)       -- one swing that looked swept-first
          NOW = NOW + 2000000
          assert(hit(0x5000, 400) == 400 and hit(0x6000, 90) == 200)      -- then the usual order
          NOW = NOW + 2000000
          assert(hit(0x5000, 400) == 400 and hit(0x6000, 90) == 200)
          assert(count('sweep order=swept-first') == 0, 'never locked on one observation')
        ''')

    def test_a_missed_aimed_hit_never_makes_a_swept_foe_read_a_stale_record(self):
        self.lua.execute('''
          assert(hit(0x5000, 400) == 400 and hit(0x6000, 90) == 200)
          NOW = NOW + 3000000                       -- the next swing: the aimed foe dodged, so no aimed hit arrives
          assert(hit(0x6000, 90) == 45, 'stale record (3 s old): half of its own')
        ''')

    def test_a_hit_never_drops_below_one(self):
        self.lua.execute('''
          assert(hit(0x5000, 1) == 1 and hit(0x6000, 1) == 1, 'Excalipur: half of 1 is still a hit of 1')
          NOW = NOW + 3000000
          assert(hit(0x6000, 1) == 1)
        ''')

    def test_no_aim_named_first_hit_is_the_aimed_foe_and_later_ones_in_the_swing_take_half(self):
        self.lua.execute('''
          assert(hit(0x5000, 400, {initial = 0}) == 400, 'a counter or an unaimed lone swing is a full hit')
          assert(count('sweep role=none') == 1)
          assert(hit(0x6000, 90, {initial = 0}) == 200, 'a later unaimed hit of the same swing takes half of the recorded hit')
          assert(hit(0x7000, 900, {initial = 0}) == 200, 'whatever its own hit was')
          NOW = NOW + 2000000
          assert(hit(0x5000, 300, {initial = 0}) == 300, 'a new swing: its first unaimed hit is full again')
        ''')

    def test_roles_are_logged_once_each_with_the_aim_and_target_addresses(self):
        self.lua.execute('''
          hit(0x5000, 10); hit(0x6000, 10); hit(0x7000, 10); hit(0x5000, 10)
          assert(count('sweep role=aimed initial=20480 target=20480') == 1, table.concat(logs, '|'))
          assert(count('sweep role=swept initial=20480 target=24576') == 1)
        ''')

    def test_an_unreadable_aim_keeps_the_old_halving_and_never_raises(self):
        self.lua.execute('''
          local f = {action=150, skipState=0, target={removedHp=100}, caster={}}
          setmetatable(f, {__index = function(_, k) if k == 'initialTargetPointer' then error('no such field') end end})
          apply(f, {type=0, address=77, weapon=0x1000}, {type=1, address=0x5000})
          assert(f.target.removedHp == 50)
          assert(count('sweep role=unreadable') == 1)
        ''')

    def test_only_a_party_greatsword_attack_on_a_foe_is_touched(self):
        self.lua.execute('''
          assert(hit(0x5000, 400, {weapon = 0x1001}) == 400 and hit(0x6000, 400, {weapon = 0x1001}) == 400, 'a sword')
          assert(hit(0x6000, 400, {skip = 1}) == 400, 'a parried or missed hit')
          local f = {action=150, skipState=0, initialTargetPointer=0x5000, target={removedHp=400}, caster={}}
          apply(f, {type=1, address=77, weapon=0x1000}, {type=0, address=0x6000})
          assert(f.target.removedHp == 400, 'a foe striking a party member')
        ''')

    def test_native_stages_see_the_settled_amounts(self):
        self.lua.execute('''
          local seen = {}
          apply = balance.wrap165(function(f) seen[#seen + 1] = f.target.removedHp end)
          local function go(address, own)
            local f = {action=150, skipState=0, initialTargetPointer=0x5000, target={removedHp=own}, caster={}}
            apply(f, {type=0, address=77, weapon=0x1000}, {type=1, address=address})
          end
          go(0x5000, 400); go(0x6000, 90)
          assert(seen[1] == 400 and seen[2] == 200, 'Attack Boost, Proficiency and leech read these amounts')
        ''')
