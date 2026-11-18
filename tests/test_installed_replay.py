"""Re-run the existing rule suites against the INSTALLED script files instead of the repository prototypes.

The suites below read prototypes/<name>.lua (and prototypes/forge_*_<n>.lua) through Path.read_text. While one of these
replay classes runs, those reads are served from the game folder (IVALICE_LEGACY_GAME): the installed
x64/scripts/IvaliceLegacy/<name>.lua and config/TheInsurgentsForgeConfig/functions/<n>.lua. The installed
proficiency_runtime.lua is the composed file, so a suite that exercises the Montblanc topic is left out.

KNOWN lists a test whose failure on the installed files is understood (the repository is ahead of the installed
release); it must fail there, and the day the release installs the fix it turns into an unexpected success and the
entry has to go.

Skips without IVALICE_LEGACY_GAME (see deployed_scripts.py).
"""
import functools
import pathlib
import re
import unittest

import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import deployed_scripts as D

import test_attack_area_filter as tarea
import test_base_jobs_release_0135_proficiency as t0135
import test_combat_balance as tbalance
import test_damage_formula as tdamage
import test_family_identities as tfamily
import test_hybrid_engine as thengine
import test_hybrid_lines as thlines
import test_hybrid_proficiency as thprof
import test_magicks as tmagicks
import test_mp_economy as tmp
import test_proficiency as tprof
import test_recovery_rules as trecovery
import test_technicks as ttech
import test_weapons_release_0134 as t0134
import test_weapons_release_b as tb

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROTOTYPES = (ROOT / 'prototypes').as_posix() + '/'
PACKAGE = (ROOT / 'package/x64/scripts').as_posix() + '/'
_real_read_text = pathlib.Path.read_text

# Installed behaviour that differs from the repository in the current repository: test id -> why.
WHY = {
    'costs':
        'installed 0.5.x still carries Braced, Open, Reckless, rod Lure and Long haft (removed in the repository 0.6.0, ships in a later release)',
    'cleave':
        'installed Cleave II-2..4 clears Reckless; the repository Breaks the foe instead (ships in a later release)',
    'boost':
        'installed Counterflow ignores Counter Boost on a pole without the licence (row 10 ships in a later release)',
    'cadence':
        'installed Cadence uses its own charge queue; the repository routes it through the runtime (row 11, later release)',
    'mp':
        'installed mp_economy 0.2.0 still charges the Magick surcharge (removed in the repository 0.3.0, later release)',
    'dmg':
        'installed damage_formula 0.7.0 (the repository is 0.8.2: Reckless removed, and body armour never makes a hit bigger than none)',
    'hellfire':
        'installed Hellfire I hears counters only; the repository (Proficiency 0.5.9) also hears a physical hit you take, so the stage count differs (balance follow-up 2026-11-17, ships in a later release)',
}
KNOWN = {  # each entry is an expected failure against the INSTALLED scripts; delete it when the release that fixes it is installed
    'InstalledHybridLinesTest.test_every_stage_of_every_line_fires_on_its_own_event':
        WHY['hellfire'],
    'InstalledProficiencyRulesTest.test_040_levels':
        WHY['cleave'],
    'InstalledProficiencyRulesTest.test_audit_042':
        WHY['cleave'],
    'InstalledHybridEngineApplyTest.test_quick_arms_another_units_next_action_and_cleave_breaks_the_foe':
        WHY['cleave'],
    'InstalledFamilyIdentityTests.test_cadence_uses_the_proficiency_runtime_charge_route_when_loaded':
        WHY['cadence'],
    'InstalledFamilyIdentityTests.test_counterflow_counter_boost_without_the_licence_uses_speed_times_two':
        WHY['boost'],
    'InstalledFamilyIdentityTests.test_counterflow_survives_a_forge_without_the_counter_boost_field':
        WHY['boost'],
    'InstalledFamilyIdentityTests.test_no_family_costs_the_wielder_on_the_evade_roll':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_a_defender_with_no_weapon_evade_parries_a_counter_it_rolled':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_a_failing_reader_logs_one_error_per_identity':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_a_katana_combo_leaves_the_wielder_its_evasion':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_an_overflowing_rod_heal_draws_nothing_to_the_caster':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_axe_swings_never_raise_the_damage_the_wielder_takes':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_counter_parry_leaves_fists_and_foes_alone':
        WHY['costs'],
    'InstalledReleaseBehaviourTests.test_the_reckless_state_and_api_are_gone_but_sunder_still_dents':
        WHY['costs'],
    'InstalledEconomyMathTest.test_a_spell_costs_what_the_game_charges_at_any_magick':
        WHY['mp'],
    'InstalledEconomyMathTest.test_paid_follows_the_native_rule':
        WHY['mp'],
    'InstalledEconomyMathTest.test_return_cap_is_half_the_cast':
        WHY['mp'],
    'InstalledDamageFormulaTests.test_ordinary_hits_are_forge_own':
        WHY['dmg'],
    'InstalledDamageFormulaTests.test_body_armour_never_increases_damage':
        WHY['dmg'],
    'InstalledDamageFormulaTests.test_light_and_mystic_floors_still_bind_and_heavy_is_unchanged':
        WHY['dmg'],
    'InstalledReturnCapTest.test_a_new_cast_gets_a_new_budget':
        WHY['mp'],
    'InstalledReturnCapTest.test_area_spell_targets_a_second_apart_share_one_budget':
        WHY['mp'],
    'InstalledReturnCapTest.test_cap_follows_what_the_game_charged':
        WHY['mp'],
    'InstalledReturnCapTest.test_every_target_of_one_cast_shares_the_budget':
        WHY['mp'],
    'InstalledReturnCapTest.test_overflow_loop_is_capped':
        WHY['mp'],
    'InstalledReturnCapTest.test_spell_drains_come_out_of_the_budget':
        WHY['mp'],
    'InstalledSurchargeAdapterTest.test_no_cast_is_ever_charged_a_surcharge':
        WHY['mp'],
    'InstalledSurchargeAdapterTest.test_ready_line_states_the_returns_cap_and_nothing_logs_a_charge':
        WHY['mp'],
    'InstalledSurchargeAdapterTest.test_the_adapter_no_longer_reads_a_campaign_or_a_clan_rank':
        WHY['mp'],
}


def installed_read_text(self, *args, **kwargs):
    path = self.as_posix()
    if path.startswith(PROTOTYPES):
        name = path[len(PROTOTYPES):]
        forge = re.match(r'forge_.*_(\d+)\.lua$', name)
        if forge and D.installed(f'{D.FORGE_CONFIG}/{forge.group(1)}.lua'):
            return D.forge(forge.group(1))
        if '/' not in name and not forge and D.installed(f'{D.IVALICE}/{name}'):
            return D.read(f'{D.IVALICE}/{name}')
    if path.startswith(PACKAGE):
        rel = path[len(PACKAGE):]
        if D.installed(f'{D.SCRIPTS}/{rel}'):
            return D.read(f'{D.SCRIPTS}/{rel}')
    return _real_read_text(self, *args, **kwargs)


class Replay:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pathlib.Path.read_text = installed_read_text

    @classmethod
    def tearDownClass(cls):
        pathlib.Path.read_text = _real_read_text
        super().tearDownClass()


def replay(module, name):
    base = getattr(module, name)
    new = type('Installed' + name, (Replay, base), {'__module__': __name__})
    for test in list(vars(new)) + [n for n in dir(base) if n.startswith('test_')]:
        key = f'{new.__name__}.{test}'
        if key in KNOWN:
            # wrap a fresh function: expectedFailure flags the function object itself, and flagging the base class's
            # own test would turn its (passing) repository run into an unexpected success
            original = getattr(base, test)
            setattr(new, test, unittest.expectedFailure(functools.wraps(original)(
                lambda self, _original=original: _original(self))))
    return unittest.skipUnless(D.have('proficiency', 'hybrid_proficiency', 'family_identities', 'mp_economy',
                                      'damage_formula', 'proficiency_runtime'), D.SKIP_REASON)(new)


InstalledProficiencyRulesTest = replay(tprof, 'ProficiencyRulesTest')
InstalledProficiencyApplyTest = replay(tprof, 'ProficiencyApplyTest')
InstalledHybridProficiencyTest = replay(thprof, 'HybridProficiencyTest')
InstalledHybridLinesTest = replay(thlines, 'HybridLinesTest')
InstalledHybridEngineTest = replay(thengine, 'HybridEngineTest')
InstalledHybridEngineApplyTest = replay(thengine, 'HybridEngineApplyTest')
InstalledHybridEngineHookTest = replay(thengine, 'HybridEngineHookTest')
InstalledFamilyIdentityTests = replay(tfamily, 'FamilyIdentityTests')
InstalledReleaseBehaviourTests = replay(t0134, 'ReleaseBehaviourTests')
InstalledRecoveryDecideTest = replay(trecovery, 'RecoveryDecideTest')
InstalledPushBudgetTest = replay(trecovery, 'PushBudgetTest')
InstalledEconomyMathTest = replay(tmp, 'EconomyMathTest')
InstalledRulesTest = replay(t0135, 'RulesTest')
InstalledSuppressionCapTest = replay(t0135, 'SuppressionCapTest')
InstalledWeaponsRulesTest = replay(tb, 'RulesTest')
InstalledWeaponsRuntimeTest = replay(tb, 'RuntimeTest')

InstalledDamageFormulaTests = replay(tdamage, 'DamageFormulaTests')
InstalledTechnickRulesTest = replay(ttech, 'TechnickRulesTest')
InstalledMagicksAdapterTest = replay(tmagicks, 'AdapterTest')
InstalledMagicksTableTest = replay(tmagicks, 'TableTest')
InstalledCombatBalanceTests = replay(tbalance, 'CombatBalanceTests')
InstalledAttackAreaFilterTests = replay(tarea, 'AttackAreaFilterTests')
InstalledForge228AdapterTests = replay(tarea, 'Forge228AdapterTests')
InstalledReturnCapTest = replay(tmp, 'ReturnCapTest')
InstalledSurchargeAdapterTest = replay(tmp, 'SurchargeAdapterTest')


if __name__ == '__main__':
    unittest.main()
