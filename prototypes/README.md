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
