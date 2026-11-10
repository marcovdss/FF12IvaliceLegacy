# Combat modules, diagnostics and offline prototypes

This folder contains both shipped modules and older offline prototypes. Proficiency and the Attack area filter
ship through their dedicated builders; diagnostic modules have separate optional packages. The remaining Clan
experiments below are offline and do not add a production Clan menu, purchases or quests. `clan_rank.lua` reads Clan rank;
`clan_ledger.lua` persists unlocks but is not wired in.

`clan_status.lua` explores a single ordinary-attack path: native Attack 150 with
Dagger 4206 and formula 21. The caller supplies personal ownership, rule definitions
and a trusted actor context. Base class keys and canonical unordered pairs match
the schema-1 Python progression contract; hybrids do not inherit base rules.

The two wrappers work together. Function171 retains its original calculation,
then may set only scratch `onHitRate`. Function182 receives a temporary helper
proxy that contributes a selected status mask; its original immunity and
applicability rules still execute. Actor and shared equipment records are not
modified. Existing native status payloads or nonzero native proc rates decline
the additional effect so their rates are not amplified accidentally.

The caller must provide `context(caster)`, `nativeRate(caster)`,
`nativeMask(caster)`, `ledger` and `rules`. Context classification is a caller
responsibility, not established by a numeric actor ID. Rules contain a unique
`id`, exact `classKey`, one of `blind`/`silence`/`slow`, and an integral
`onHitRate` from 1 to 100. No production rules or default balance values are shipped.
Ambiguous owned matches and invalid rule fields grant nothing. Both accessories
and every weapon other than the pinned Dagger are excluded from this prototype.
Rule and owned-ID lists must be plain dense arrays; sparse or keyed lists are
rejected rather than hiding a conflicting match after an iteration hole.

Re-resolve inputs on every action and clear the caller's ownership when a save
changes. The module keeps no previous actor/class/proc cache. Its intended
synchronous call assumes ownership and jobs do not mutate between 171 and 182;
mid-action changes, reflection, multihit timing and live classification still
require integration proof. Native status duration, rendering and final application
are separate from passing a request to a native helper boundary.

The repository's Lua unit tests use Lupa Lua5.5 when available. They verify pure
selection/proxy contracts, not a running game. This is not a drop-in replacement
for arbitrary other providers' overrides of the same Forge functions.

## Diagnostic observer

`clan_diagnostic.lua` is a separate, bonus-free observer prototype, also excluded
from normal packages. Its caller supplies read-only `legacyMode()` and canonical
`identity(caster)` adapters plus `emit(line)`. `new(deps)` returns an observer with
`load(path)`, `reset()`, `observe(formula,caster)` and `wrap(original)` methods.
Only a basename exactly matching `LEGCY_ddd` opens the campaign gate. Every load
attempt clears previous state; callers must invalidate it on every native save
load and new-game transition. Loader callback ordering requires integration
verification. The mode adapter must return literal `true` on each observation.

Ordinary Attack observations record the canonical character, complete unordered
job combination, weapon and formula. Hunter and Chemist singles are labeled as
base pilots; other valid combinations provide controls without requiring a job
change. Logs are deduplicated and capped at 256 observations per load. No ownership,
status effects, balance values or save handler are supplied. The wrapper invokes
the original exactly once before observing, preserves all results and isolates
diagnostic errors. A canonical pointer match remains an integration input,
not proof of every native actor classification or lifetime.

`forge_diagnostic_171.lua` is the optional Forge configuration adapter for this
observer. Its candidate package places that file at
`x64/scripts/config/TheInsurgentsForgeConfig/functions/171.lua` and the observer at
`x64/scripts/IvaliceLegacy/clan_diagnostic.lua`. It loads the installed original
Forge 171 rather than redistributing it. This configuration path must be unowned
before installation; existing overrides require a separate compatibility review.
The adapter checks the pinned executable's fixed mapping and both Legacy save
namespace strings, registers load/new-game invalidation, and never registers a
save writer. The tested source expects Lua Loader 2.0.1 and Forge 1.0.3; native
callback order and actual loaded-path format remain unverified. Both prototype
files remain excluded from the ordinary Legacy build.

## Pursuit pilot

`clan_pursuit.lua` is the first Clan line meant for play: a base Hunter's
ordinary Attack with a dagger (native weapon category 12) deals 25% more damage
to a target already hindered by Blind, Poison, Silence, Slow, Sap, Disease,
Immobilize, Disable or Oil. It wraps Forge 66, the shared dagger/bow damage
function: the original runs first, then only the current formula's scratch
`power` is scaled, the same lever Forge uses for its native Sleep bonus, which
stays separate and multiplies. Bows reach Forge 66 too and are excluded by
category. Hybrids receive their own lines, so a Hunter pair gets nothing here.

`forge_pursuit_66.lua` is its Forge configuration adapter
(`x64/scripts/config/TheInsurgentsForgeConfig/functions/66.lua`, with the module
at `x64/scripts/IvaliceLegacy/clan_pursuit.lua`). It loads the installed
original Forge 66, activates only after a strict `LEGCY_ddd` load in Legacy mode,
and logs up to 32 activations per load. Clan rank, quests, purchases and
persistence are not built: in this pilot every base Hunter has the line.

## Clan ledger

## Proficiency 0.4.6 (ships as its own package)

- `proficiency.lua` decides each job's Clan line: 12 Clan ranks = 3 stages x 4 levels (ranks 1-4 stage I, 5-8 II,
  9-12 III), applied by the character's current job to whatever they wield. All twelve base jobs have implemented rules at all
  144 levels. Offline coverage does not establish later-stage gameplay balance. It also writes Montblanc's lines.
- `proficiency_runtime.lua` is shared by eight Forge adapters (`forge_proficiency_<n>.lua` = `config/functions/<n>.lua`):
  165 physical hits, 240 dodge/parry/block/counter, 110 healing, 127 potions, 170 magick damage, 182 on-hit
  statuses, 22 evasion, and 222 target initialization (clears expired Stop extension budgets).
  Each runs the original first. Effects use Forge's own end-of-hit fields, Forge helpers on other units, or charge
  times.
- Legacy saves only (LEGCY_ddd + executable pins). The rewarded rank is stored beside the save
  (`IvaliceLegacyProficiency`). Leaving the Clan hall (location 302) with an unrewarded rank makes Montblanc speak in
  native dialogue windows and claims it.
- `clan_rank.lua` reads the rank without side effects.

## Attack area experiment 0.0.2: greatsword-only cleave

These two files ship in the Attack area experiment package.
- `forge_filter_228.lua` is Forge's `config/functions/228.lua`: 228 is the onCastPre/onHitPre check that silently skips
  units an action can't touch (skip state 1, outcome 9, no "Miss"). It runs the original 228 unchanged, then applies
  the same skip to Attack bystanders the filter rejects.
- `attack_area_filter.lua` decides per Attack hit. A bystander is any unit other than the formula's initial target.
  A party member's greatsword (category 2) keeps the sweep. Every other Attack, foes' included, skips bystanders.
- When the initial target is unknown (0), the hit is left alone. When the filter module is missing, the adapter
  falls back to single-target Attack. A filter error leaves the hit as native 228 left it.
- Logging is capped at 100 `Legacy cleave:` lines per load.

## Foe profile diagnostic 0.1.5: charge pushback and Attack area probes

0.1.5 keeps the 0.1.4 observers unchanged and adds two probes to the same Forge113 adapter, each run after the
original and the read-only observer, each in its own pcall:

- `charge_pushback_probe.lua` is the adapter's one write: a mechanism test of whether lowering a foe's current
  charge time (battle unit work +0x768) persists. It acts only on party Attack (action 150) hitting a mid-charge foe,
  alternates control/write within a load, lowers current by 1/4 of required (never to 0), at most 12 writes and
  60 lines per load. Lines: `Legacy charge pushback: n=… arm=control|write … unit=0x… before=cur/req written=…
  readback=…`.
- `attack_area_probe.lua` is read-only: one `Legacy attack area:` line per Attack hit with the caster's weapon
  category and whether the target is the caster's current target focus or a bystander caught by the area. It goes
  with the separate Attack area experiment (`patches/attack_area_experiment.json`, section 14).

## Foe profile diagnostic 0.1.4

`foe_profile_diagnostic.lua` retains the existing profile, charge and hit observers.
The additional `forge_diagnostic_261.lua` candidate wraps the installed original
Forge261 at `x64/scripts/config/TheInsurgentsForgeConfig/functions/261.lua`.
Default Forge middlewares invoke 261 at `onCastEnd`, independently of damage
function113, so non-damaging enemy casts reaching that path can be observed.
The original runs exactly once and its return values are preserved. Neither
observer writes game state, modifies damage or registers save data.

`Legacy action:` records the foe name ID, live unit-slot identifier (not an ARD
row index), action ID, target, caster HP percentage/max HP and host monotonic
clock. The raw clock is not converted to seconds: available loader documents
disagree about its units. Backward/invalid clock samples are rejected. Location
is the formula's numeric location when available; the area code is reported as
unknown. The observer is capped at 300 cast observations per load/new game.

This is a cast-resolution trace, not every AI decision or every action in the
engine. Movement, idle, cancelled casts, native-only specials and actions that
bypass Forge's cast path may be absent. One line is emitted per reachable cast
callback; exact callback multiplicity for area effects remains a play check.
There is no deduplication by action ID that could hide consecutive identical
casts. The existing113 observer continues to log hits separately.

Both adapters and their matching module remain excluded from ordinary builds.
An eventual diagnostic release must separately review ownership of config261,
preserve113 and package the matching 0.1.4 module; these source files are not
installed by editing this repository.

## Clan ledger

`clan_ledger.lua` records which Clan lines each main party member (0–5) has
unlocked for each full job identity (`"8"` for a base job, `"4:8"` for a pair),
kept per campaign in the Lua loader's save sidecar (`event.registerSaveHandler`
and `registerLoadHandler`, handler ID `IvaliceLegacyClan`). A Legacy save
without the sidecar loads an empty ledger; non-Legacy saves get none. An
unreadable sidecar is logged and kept on disk (the save handler returns nil)
until a new grant replaces it. The module is not wired into a loader entry
point yet; how lines are earned (Clan rank, hunts, costs) is undecided.

## Clan rank reader

`clan_rank.lua` returns the Clan rank, marks, Clan points and Sky Pirate's Den
count the game itself uses, from static reading of the executable. The game
does not store a rank: `0x315390` checks the save against a 12-row table at
`0xD105D0` (Clan points, marks, Den count) from rank 12 down. Clan points are a
u32 at save image `0xBA68`; marks come from the game's read-only counter
`0x316480`, which counts hunts that reached their required stage; the Den count
is 30 bits at save image `0xBA44`. The rank function itself is not called because
its Den helper `0x316930` writes Den state; the module repeats its rule instead.
Four table rows are pinned, so another executable is refused rather than misread.

`forge_diagnostic_171.lua` logs the result once per accepted Legacy load
(`clanRank=… marks=… clanPoints=… den=…`). A missing or failing reader only
changes that line. The values have not been compared with the Clan Primer
in-game yet, and when the load handler runs relative to the save copy is
unverified.

## Family identities (shipped from 0.1.14)

`family_identities.lua` decides, and `forge_family_8/10/68.lua` plus the updated 22/110/113/165/171
adapters apply, the nine weapon-family styles: Sunder (axe), Pierce (spear), Unseen (ninja sword),
Marksman (gun), Afflict (crossbow), Spellbreak (mace), Mend (rod), Counterflow (pole), Chain (katana).
Each is always on for a character-controlled wielder of that family, pcall-guarded, and inactive
outside a Legacy campaign. The Forge originals always run first (Counterflow moves a roll's input
before the original). Tests: `tests/test_family_identities.py`.
