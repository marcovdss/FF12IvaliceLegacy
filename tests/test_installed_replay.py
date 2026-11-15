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
KNOWN = {
    'InstalledProficiencyRulesTest.test_mana_leech_uses_final_damage_and_can_drain_the_last_mp':
        'installed Mana Leech I-2 Silences before the per-hit MP cap (fixed in the repository, ships in a later release)',
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
