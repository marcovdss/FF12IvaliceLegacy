# Experimental hybrid-board core

This C++ core selects a private license-board layout from a character's two
original job IDs. It supports66 unordered pairs while preserving native job IDs,
license ownership and LP. The current API supplies private grids, view bindings,
freshness checks and an ID/context-coherence guard. It does not replace the
game's purchase eligibility rules.

This source is excluded from the normal mod package. The separate Paladin pilot
now has a compiled routing adapter, emitted x64 thunks and guarded Lua installer.
It remains an uninstalled test candidate pending actual loader/UI/campaign
observation. Only native pair0/4 has an authored layout; this is not66jobs.

Build on Windows with MSYS2 UCRT64 GCC16.2.0:

```powershell
python native/hybrid_boards/build.py --compiler C:/msys64/ucrt64/bin/g++.exe
```

The build pins the compiler version and emits an import-free DLL, an image
preserving its RVAs, export offsets and SHA-256 manifests under `build/`.
`image.py` rejects features requiring OS-loader support. No game assets are
needed or included. The exercised core paths were checked offline at three
allocation bases; this does not establish live integration.

`bootstrap.lua` is an inert module: loading it performs no native calls. Its
explicit `prepare` function accepts trusted validated image bytes and export
offsets, checks the copied bytes, initializes isolated core state, and returns
an allocation handle. Its close operation requires no active callbacks or other
users. It does not install hooks or configure game pointers. Loader API behavior
has been exercised through emulator-backed doubles; installed-loader behavior
and complete teardown remain unverified.

`adapter.cpp`, `sites.py` and `thunks.py` implement the pinned per-view routing,
native purchase validation, single-board controls and Paladin header. The pilot
uses the exact source license union, so native membership and respec/refund
routines remain untouched. `loader.lua` and `hooks.lua` perform startup preflight,
copy/readback, relocation and a guarded patch transaction. Rollback requires
quiescence; restoring patch bytes alone does not prove calls have stopped. There
is deliberately no live unload API. Allocations remain owned until process exit.

`pilot-gate.json` pins the offline-tested candidate, not an in-game certification.
The build consumes only reviewed own-code images and an authored recipe; it
never starts the game or deploys files.
