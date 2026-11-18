"""MP economy 0.3.0: a spell costs what the game charges (the Magick surcharge is gone; Marco, 2026-11-16), and one cast
returns at most half of its cost as MP (Proficiency)."""
from pathlib import Path
import unittest

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class EconomyMathTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.M = self.lua.execute(source('mp_economy'))

    def test_a_spell_costs_what_the_game_charges_at_any_magick(self):
        M = self.M
        self.assertEqual(M.version, '0.3.0')
        for magick in (0, 20, 30, 57, 99, 200):
            self.assertEqual(M.total(26, magick), 26)       # Penelo's Cura at any Magick: the listed price
            self.assertEqual(M.surcharge(26, magick), 0)
            self.assertEqual(M.factor(magick), 1)
        self.assertEqual(M.total(46, 200), 46)
        self.assertEqual(M.surcharge(0, 99), 0)
        # the co-op menu reads DIVISOR and CAP from this file: a cap of 1 shows the game's own price
        self.assertEqual((M.DIVISOR, M.CAP), (30, 1))

    def test_return_cap_is_half_the_cast(self):
        self.assertEqual(self.M.returnCap(26, 57), 13)
        self.assertEqual(self.M.returnCap(18, 20), 9)
        self.assertEqual(self.M.returnCap(26, 200), 13)     # Magick no longer moves it

    def test_only_magicks_with_a_cost_are_priced(self):
        t = self.lua.table_from
        M = self.M
        self.assertTrue(M.priced(t({'category': 1, 'mpOrMistCost': 26})))
        self.assertFalse(M.priced(t({'category': 1, 'mpOrMistCost': 0})))
        self.assertFalse(M.priced(t({'category': 6, 'mpOrMistCost': 2})))   # Quickening mist
        self.assertFalse(M.priced(t({'category': 3, 'mpOrMistCost': 0})))   # item
        self.assertFalse(M.priced(None))


    def test_paid_follows_the_native_rule(self):
        self.lua.globals().MP_SRC = source('mp_economy')
        self.lua.execute(r"""
          local M = load(MP_SRC)()
          local function aug(list, params)
            local set = {}
            for _, n in ipairs(list) do set[n] = true end
            return function(n) return set[n] == true end, function(bit) return (params or {})[bit] or 10 end
          end
          assert(M.paid(26, aug({})) == 26)
          assert(M.paid(26, aug({'channeling1', 'channeling2', 'channeling3'})) == 18)   -- Penelo's Cura
          assert(M.paid(30, aug({'channeling1'}, {[93]=50})) == 14)                     -- float32 steps: 14, not 15
          assert(M.paid(28, aug({'channeling1'}, {[93]=50})) == 14)                     -- and 14, not 13
          assert(M.paid(26, aug({'parsimony'})) == 13)
          assert(M.paid(26, aug({'parsimony', 'channeling1'})) == 11)                    -- 26 -> 23 -> 11
          assert(M.paid(26, aug({'offering', 'parsimony'})) == 0)                        -- gil instead of MP
          assert(M.paid(26, aug({'spellspring'})) == 0)
          assert(M.paid(26, nil) == 26 and M.paid(0, aug({})) == 0)
          assert(M.surcharge(26, 57, 18) == 0 and M.returnCap(26, 57, 18) == 9)           -- no surcharge: half of what was paid
          assert(M.surcharge(26, 57) == 0)                                                -- no paid given: listed cost
        """)

@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class SurchargeAdapterTest(unittest.TestCase):
    """The real 261 adapter with the real diagnostic and economy modules, Forge's 261 stubbed."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {'scripts/IvaliceLegacy/foe_profile_diagnostic.lua': source('foe_profile_diagnostic'),
                   'scripts/IvaliceLegacy/mp_economy.lua': source('mp_economy'),
                   'scripts/TheInsurgentsForge/functions/261.lua': 'return function() ORIGINAL = (ORIGINAL or 0) + 1 end'}
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.globals().adapter = source('forge_diagnostic_261')
        self.lua.execute('''
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          logs = {}
          print = function(s) logs[#logs+1] = s end
          event = {registerEventSync=function() return true end, getClock=function() return 1 end}
          bpack = {section14={[4]={category=1, mpOrMistCost=26}, [82]={category=3, mpOrMistCost=0},
                              [208]={category=6, mpOrMistCost=1}, [150]={category=0, mpOrMistCost=0}}}
          fn = real_load(adapter, 'adapter', 't', _ENV)()
          charged = {}
          helpers = {modifyMp=function(u, amount) charged[#charged+1] = {u=u, amount=amount} end}
          function cast(t)
            local caster = {type=t.casterType or 0, address=100, magickPower=t.magick or 57, currentMp=t.mp or 200,
                            currentHp=1, maxHp=1}
            local target = {type=0, address=t.target or 300, identifier=0}
            local formula = {action=t.action or 4, causeType=t.cause or 0, initialTarget={address=t.initial or 300},
                             caster={battleUnitWork={address=0}}, target={battleUnitWork={address=0}}}
            charged = {}
            fn(formula, caster, target, {}, {}, helpers)
            return #charged, charged[1] and charged[1].amount or 0
          end
        ''')

    def run_cast(self, **kw):
        return self.lua.eval('cast')(self.lua.table_from(kw))

    def test_no_cast_is_ever_charged_a_surcharge(self):
        self.assertEqual(self.run_cast(), (0, 0))                          # Penelo's Cura at Magick 57: the game's price only
        self.assertEqual(self.run_cast(magick=150), (0, 0))
        self.assertEqual(self.run_cast(mp=10), (0, 0))
        self.assertEqual(self.run_cast(target=301, initial=300), (0, 0))
        self.assertEqual(self.lua.eval('ORIGINAL'), 4)                     # Forge's 261 always runs

    def test_foes_items_and_quickenings_are_native_too(self):
        self.assertEqual(self.run_cast(casterType=1), (0, 0))
        self.assertEqual(self.run_cast(action=82), (0, 0))
        self.assertEqual(self.run_cast(action=208), (0, 0))
        self.assertEqual(self.run_cast(cause=2), (0, 0))

    def test_ready_line_states_the_returns_cap_and_nothing_logs_a_charge(self):
        self.run_cast()
        logs = list(self.lua.eval('logs').values())
        self.assertTrue(any('Legacy MP: economy 0.3.0 ready (spells cost what the game charges, returns <= 50% per cast)' in s
                            for s in logs), logs)
        self.assertFalse(any('surcharge' in s for s in logs))

    def test_the_adapter_no_longer_reads_a_campaign_or_a_clan_rank(self):
        self.lua.execute("IvaliceLegacyProficiency = {active=function() return true end, inCampaign=function() return true end}")
        self.assertEqual(self.run_cast(), (0, 0))
        self.lua.execute("IvaliceLegacyProficiency = nil")


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class ReturnCapTest(unittest.TestCase):
    """Proficiency MP handed back by one cast is capped at half of what the cast cost; foe drains are free."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': source(n)
                   for n in ('proficiency', 'proficiency_runtime', 'clan_rank', 'hybrid_proficiency', 'mp_economy')}
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.execute('''
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          memory = {u8={}, u32={}, u64={}, readArray=function() return {} end, execute=function() return 0 end}
          arg = {s32=0}
          NOW = 1000000
          event = {registerEventSync=function() return true end, registerEventAsync=function() return true end,
                   registerLoadHandler=function() return true end, registerSaveHandler=function() return true end,
                   getClock=function() return NOW end, executeAfterMs=function() end}
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], 'runtime', 't', _ENV)()
          bpack = {section14={[4]={category=1, mpOrMistCost=26}, [5]={category=1, mpOrMistCost=20},
                              [82]={category=3, mpOrMistCost=0}}}
          gained = {}
          helpers = {modifyMp=function(u, amount) gained[#gained+1] = {u=u, amount=amount} end, modifyHp=function() end}
          caster = {type=0, address=100, magickPower=57}
          function give(action, others, now)
            if now then NOW = now end
            gained = {}
            local a = {others=others, mpBudget=R.castBudget(caster, action, NOW)}
            R.apply(a, nil, caster, {}, helpers, nil)
            local out = {}
            for i, g in ipairs(gained) do out[i] = g.amount end
            return out
          end
        ''')

    def amounts(self, code):
        return list(self.lua.execute(code).values())

    def test_overflow_loop_is_capped(self):
        # Cura (26) costs 26 at any Magick: at most 13 MP back, however much it overflowed
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}})"), [13])

    def test_cap_follows_what_the_game_charged(self):
        # a Parsimony caster's Cura costs 13: at most 6 back. Offering (gil, no MP): none.
        self.lua.execute("caster.permanentAugments = {parsimony=1}")
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}}, 2000000)"), [6])
        self.lua.execute("caster.permanentAugments = {offering=1}")
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}}, 4000000)"), [])   # cap 0: nothing handed back
        self.lua.execute("caster.permanentAugments = nil")

    def test_every_target_of_one_cast_shares_the_budget(self):
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=8}})"), [8])
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=8}}, NOW + 1000)"), [5])
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=200, amount=8}}, NOW + 1000)"), [])

    def test_a_new_cast_gets_a_new_budget(self):
        self.lua.execute("give(4, {{op='mp', unit=100, amount=75}})")
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}}, NOW + 1000000)"), [13])
        self.assertEqual(self.amounts("return give(5, {{op='mp', unit=100, amount=75}}, NOW + 10)"), [10])

    def test_foe_drain_is_free(self):
        self.assertEqual(self.amounts(
            "return give(4, {{op='mp', unit=900, amount=-40}, {op='mp', unit=100, amount=40}})"), [-40, 40])

    def test_area_spell_targets_a_second_apart_share_one_budget(self):
        # Cura on the party: Forge 222 sees the initial target once, then each target heals ~1 s later.
        self.lua.execute("""
          function cast(initial, target) R.startCast({causeType=0, action=4, initialTarget={address=initial}}, caster,
                                                     {address=target}) end
          cast(300, 300)""")
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}})"), [13])
        for later in (1000000, 2000000, 3000000):
            self.lua.execute("cast(300, %d)" % (300 + later // 1000000))     # other targets: same cast
            self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}}, NOW + %d)" % 1000000), [])
        self.lua.execute("cast(300, 300)")                                  # the next Cura
        self.assertEqual(self.amounts("return give(4, {{op='mp', unit=100, amount=75}}, NOW + 1000000)"), [13])

    def test_reflected_or_foe_casts_do_not_start_a_cast(self):
        self.lua.execute("""R.startCast({causeType=0, action=4, initialTarget={address=300}}, caster, {address=300})""")
        first = self.lua.eval("R.castId(caster)")
        self.lua.execute("""R.startCast({causeType=2, action=4, initialTarget={address=300}}, caster, {address=300})
          R.startCast({causeType=0, action=4, initialTarget={address=300}}, {type=1, address=100}, {address=300})
          R.startCast({causeType=0, action=150, initialTarget={address=300}}, caster, {address=300})  -- a counter""")
        self.assertEqual(self.lua.eval("R.castId(caster)"), first)
        self.assertIsNone(self.lua.eval("R.castId(caster, 150)"))          # weapon hits are never a cast
        self.assertEqual(self.lua.eval("R.castId(caster, 4)"), first)

    def test_spell_drains_come_out_of_the_budget(self):
        # Hellfire-type drain on an area spell: each foe's drain draws on the cast's 13 MP budget.
        self.lua.execute("""R.startCast({causeType=0, action=4, initialTarget={address=300}}, caster, {address=300})
          function drain(amount)
            local f = {caster={}, target={}}
            local a = {drainMp=amount, mpBudget=R.castBudget(caster, 4, NOW)}
            R.apply(a, f, caster, {}, helpers, nil)
            return f.caster.addedMp, f.target.removedMp
          end""")
        self.assertEqual(self.lua.eval("drain")(160), (13, 13))
        self.assertEqual(self.lua.eval("drain")(160), (None, None))       # budget spent: nothing written

    def test_items_and_technicks_are_not_capped(self):
        self.assertEqual(self.amounts("return give(82, {{op='mp', unit=100, amount=75}})"), [75])
        self.assertEqual(self.amounts("return give(150, {{op='mp', unit=100, amount=75}})"), [75])


    def test_a_drain_adds_to_mp_already_queued(self):
        # 0.1.23: a Proficiency drain no longer replaces an MP change an earlier stage queued on the formula.
        self.lua.execute(r"""
          local f = {target={mpUpdateState=1, addedMp=0, removedMp=30}, caster={mpUpdateState=1, addedMp=30, removedMp=0}}
          R.apply({drainMp=7}, f, caster, {}, helpers, {address=0})
          assert(f.target.removedMp == 37 and f.caster.addedMp == 37, f.target.removedMp .. ' ' .. f.caster.addedMp)
          local g = {target={mpUpdateState=0, removedMp=99}, caster={mpUpdateState=0, addedMp=99}}
          R.apply({drainMp=7}, g, caster, {}, helpers, {address=0})
          assert(g.target.removedMp == 7 and g.caster.addedMp == 7)   -- nothing queued: stale fields ignored
          local h = {target={mpUpdateState=1, addedMp=0, removedMp=5}, caster={}}
          R.apply({targetMpLoss=4}, h, caster, {}, helpers, {address=0})
          assert(h.target.removedMp == 9)
        """)

if __name__ == '__main__':
    unittest.main()
