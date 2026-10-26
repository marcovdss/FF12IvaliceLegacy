# Offline combat prototypes

These modules are excluded from installable packages and have no loader entrypoint.
They do not add a Clan menu, purchases, quest/rank readers or persisted progress.

`clan_status.lua` explores a single ordinary-attack path: native Attack150 with
Dagger4206 and formula21. The caller supplies personal ownership, rule definitions
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
`onHitRate` from1 to100. No production rules or default balance values are shipped.
Ambiguous owned matches and invalid rule fields grant nothing. Both accessories
and every weapon other than the pinned Dagger are excluded from this prototype.
Rule and owned-ID lists must be plain dense arrays; sparse or keyed lists are
rejected rather than hiding a conflicting match after an iteration hole.

Re-resolve inputs on every action and clear the caller's ownership when a save
changes. The module keeps no previous actor/class/proc cache. Its intended
synchronous call assumes ownership and jobs do not mutate between171 and182;
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
change. Logs are deduplicated and capped at256 observations per load. No ownership,
status effects, balance values or save handler are supplied. The wrapper invokes
the original exactly once before observing, preserves all results and isolates
diagnostic errors. A canonical pointer match remains an integration input,
not proof of every native actor classification or lifetime.

`forge_diagnostic_171.lua` is the optional Forge configuration adapter for this
observer. Its candidate package places that file at
`x64/scripts/config/TheInsurgentsForgeConfig/functions/171.lua` and the observer at
`x64/scripts/IvaliceLegacy/clan_diagnostic.lua`. It loads the installed original
Forge171 rather than redistributing it. This configuration path must be unowned
before installation; existing overrides require a separate compatibility review.
The adapter checks the pinned executable's fixed mapping and both Legacy save
namespace strings, registers load/new-game invalidation, and never registers a
save writer. The tested source expects Lua Loader2.0.1 and Forge1.0.3; native
callback order and actual loaded-path format remain unverified. Both prototype
files remain excluded from the ordinary Legacy build.

## Pursuit pilot

`clan_pursuit.lua` is the first Clan line meant for play: a base Hunter's
ordinary Attack with a dagger (native weapon category 12) deals 25% more damage
to a target already hindered by Blind, Poison, Silence, Slow, Sap, Disease,
Immobilize, Disable or Oil. It wraps Forge66, the shared dagger/bow damage
function: the original runs first, then only the current formula's scratch
`power` is scaled, the same lever Forge uses for its native Sleep bonus, which
stays separate and multiplies. Bows reach Forge66 too and are excluded by
category. Hybrids receive their own lines, so a Hunter pair gets nothing here.

`forge_pursuit_66.lua` is its Forge configuration adapter
(`x64/scripts/config/TheInsurgentsForgeConfig/functions/66.lua`, with the module
at `x64/scripts/IvaliceLegacy/clan_pursuit.lua`). It loads the installed
original Forge66, activates only after a strict `LEGCY_ddd` load in Legacy mode,
and logs up to 32 activations per load. Clan rank, quests, purchases and
persistence are not built: in this pilot every base Hunter has the line.
