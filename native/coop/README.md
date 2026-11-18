# Ivalice Legacy co-op module (native)

`ivalice-legacy-coop.dll` is the in-process co-op module, loaded by the FF12 External File Loader from
`x64/modules/`. It identifies itself to the loader and serves bridge ABI v1 (settings staging, status, seat and
device views, events). It contains the input service (`src/input/`), which stays **inert** until module start-up
starts it: before then no thread runs, XInput is not loaded and SDL is not initialized. It installs no hooks and
never reads or writes game memory. `DllMain` returns immediately; module start-up belongs in `FF12Hinit`, which is
empty for now.

## Exports

The loader treats all five `FF12H*` exports as optional and calls them in the order listed.

| Export | ABI |
|---|---|
| `ilc_efl_version FF12HgetVer(void)` | `{u32 major, minor, patch}`, 12 bytes, returned through the hidden pointer in RCX. The loader reads the 12 bytes at RAX, so RAX = RCX is required |
| `const char* FF12HgetName(void)` | Module name, `"Ivalice Legacy Co-op"` |
| `void FF12Hinit(void)` | Main thread, before WinMain, loader lock released. Inert: does nothing |
| `void FF12HinitDone(void)` | Loader watcher thread, once the game runs. Inert: does nothing |
| `void FF12Hcleanup(void)` | At exit, from the loader's ExitProcess hook. Inert: does nothing |
| `int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t inLen, void* out, uint64_t outCap)` | Lua ↔ native bridge, called through `memory.execute`. `op`, `inLen` and `outCap` must fit in 32 bits (else `BAD_ARGS`). Returns bytes written (≥ 0, ≤ `outCap`) or a negative error, and then writes nothing |

## Bridge ABI v1

`include/ilc/bridge_abi.h` is the single source of truth: constants, enums, record layouts and per-field ranges
are X-macro tables. The module compiles them into structs and range checks; `tools/bridge_schema.cpp` expands the
same tables into `schema/bridge-abi-v1.json`, which the Lua side and the tests read (`schema/codec.py` is a
schema-driven encoder/decoder). `build.py` regenerates the JSON on every build and fails if it differs from the
committed file; HELLO returns the JSON's SHA-256, so a Lua page can refuse a module built from another schema.

| Op | In | Out |
|---|---|---|
| 1 `HELLO` | nothing or one header | module version, state, ABI, settings size, accepted/applied generation, ops mask, source and schema ids |
| 2 `SET_SETTINGS` | `ilc_settings` (3,540 bytes) | ack: STAGED or DUPLICATE |
| 3 `COMMAND` | `ilc_command` | nothing. Seat commands (park, seat move, pairing, capture) are queued for the input service (0) and answer with an event; `NOT_READY` while the service is stopped, `BUSY` when its queue is full. `KILL_SWITCH` is taken by the co-op thread at its next beat (0). `STALL_COOP_THREAD` is a developer test (see "Diagnostics"). `RESTORE_DEFAULTS` answers `NOT_READY` until its subsystem exists |
| 4 `GET_STATUS` | nothing | state, generations, digests, event and reject counters, last reject, applied subsystem mask |
| 5 `GET_SEATS` | nothing | the configured seat view (no session yet) |
| 6 `GET_DEVICES` | nothing | the input service's inventory: `READY`, session, age and one entry per connected pad; `NOT_READY` and no entries while the service is stopped. `OUT_TOO_SMALL` if `outCap` < 32 + count × 148 |
| 7 `POP_EVENTS` | nothing | as many queued events as fit in `outCap` |
| 8 `GET_SETTINGS` | nothing | the applied settings (generation 0 = native defaults) |
| 9 `GET_COUNTERS` | nothing | `ilc_counters`: the module's diagnostic counters (see "Diagnostics" below). Read-only, answers in any state |
| 10 `PUSH_HUD_STATES` | `ilc_hud_states` | nothing: kept as the latest Legacy script states for the HUD overlay (foes, party words, proc tags), shown while fresh. Push from a script timer, never a sync hook |
| 0x7F01 `TEST_SAFE_POINT` | nothing | debug: runs the settings safe point; refused once the module's tick owns it |

Settings rules: every field is validated as untrusted (ranges, zero reserved bytes, UTF-8 names, one pad and one
character per seat, P1 always keeps an input). Hotkeys that would act twice on one press are refused with
`CONFLICT`: a kill-switch binding equal to a guest seat's `TOGGLE_CONTROL` binding (reported at that kill-switch
slot), two seats with the same `TOGGLE_CONTROL` binding (reported at the later seat's binding), and a kill-switch key
or mouse button equal to any key or mouse binding of the seat that owns the keyboard (reported at that kill-switch
slot; the right mouse button is P2's default CANCEL, so it can be the kill switch only once that binding moves), and
a guest's `TOGGLE_CONTROL` key or mouse button equal to any key or mouse binding of the seat that owns the keyboard
(reported at that guest's binding; the keyboard's own seat is exempt for its own join key). Only keyboard keys and
mouse buttons are compared; pad bindings belong to each seat's own pad and never clash. The compact battle log never
shows more lines than the expanded one (`log_lines_compact` > `log_lines_expanded` is a `CONFLICT`). "Co-op On" means
every subsystem works: the arm gates get every subsystem bit (input isolation, movement, party, camera, firewall and
menus, Wait, render, targets and cards) less the developer override `subsystems_disarm` (honoured only while
`developer` = 1) and less CAMERA while `shared_camera` is 0 (`src/runtime/options.h`); each subsystem must check its
bit at its safe point and stay stock while it is clear. A record lands in a staging slot
and becomes the applied settings
at the safe point, which only ever tries the lock. A repeat of the last accepted generation with the same bytes is
a no-op, an older generation is refused, and the applied settings persist whatever the caller does next. The
caller's buffers are read and written only through a fault-safe probe (`src/runtime/probe.cpp`), so a bad pointer
gives `FAULT` instead of a crash; guard pages are refused (VirtualQuery) without being touched. On `FAULT`, state
may have changed and `out` may hold a partial reply: re-sync with `GET_STATUS`. Every other error writes nothing.

## Layout

```
build.py            pinned build; schema generation and drift check; PE audit; --repro, --update-schema, --update-golden
audit_pe.py         export/import audit against pe-allowlist.json (standard library only)
pe-allowlist.json   the reviewed import list; denied network and platform-service DLLs, functions and strings
golden.json         expected DLL hash for the pinned toolchain and current sources
coop.def            export names and ordinals
include/ilc/        bridge ABI (single source), native defaults, EFL ABI, version
schema/             bridge-abi-v1.json (generated, committed) and codec.py
tools/              bridge_schema.cpp (host tool, not part of the DLL); gen_site_table.py (writes src/patch/sites.inc)
src/boot/           DllMain, EFL identity exports
src/bridge/         ILC_Bridge, validation
src/runtime/        fault-safe probe; game-thread job queue (game_thread.*) and the UI-tick handler (tick.*);
                    linked but not called until the boot wires them
src/input/          input service: XInput + SDL2 devices, identities, aliases, seat frames, inventory; per-consumer
                    edge cursors (edge_history.*)
src/session/        session core: fault-safe world reads, epochs and gates, the seat table, ownership hand-off,
                    the published session record
src/menu/           guest battle menus: catalogs from the game's loaded tables, the per-seat menu model, Gambits
                    state (data model); action costs as the game charges them plus Legacy's MP economy (cost.*);
                    help lines from the game's help text (help.*)
src/targets/        target candidates (Foes, Neutrals, Party, Reserve) and the per-seat target cursor
src/submit/         battle-menu requests as game-thread operations: actions, Gambits, Dismiss, combat indicator,
                    menu sounds; the request slots and the summon owner
src/frontend/       the co-op battle menus at run time: input bindings, external frontends, the native Party
                    request, the party-down policy, the firewall's menu report, the Wait request
src/wait/           the Wait engine: pause reason 0 taken and given back from the UI tick (WAIT)
src/hud/            the HUD canvas: frames built on the co-op thread, replayed by the frame hook with the game's own
                    text, sprite and bar functions (RENDER); runtime/guard.* is the fault guard around that replay;
                    relayout/circles/native: the reduced party list and target circles off, given back when not wanted;
                    driver/snapshot: the HudSnapshot, the element passes in order and the presenter stamp;
                    party_rows: the party list row additions and the low-HP glow (HD-02/HD-03)
                    menus_draw: the co-op battle menus in the native window style (the menu pass) and their view
src/overlay/        our own overlay (OV-01, RENDER): D3D11 drawing inside the game's frame (gpu.*), the quad batch
                    and its primitives (batch.*), the theme's SDF font (font.*), the HUD themes (theme*.*: the
                    built-ins from themes/<id>/font.inc and theme.json, player themes read with the same rules),
                    the themed composites (marks.*), the test card and the guests' command windows (scene.*), the
                    frame hook, the HUD heartbeat and the presenter stamp (overlay.*); overlay.hlsl -> shaders.inc
                    (tools/gen_overlay_shaders.py)
src/focus/          Keep running in background: the application stays active in windowed and borderless mode,
                    player 1's pads and mouse read neutral while another window has the focus (CORE rows)
src/patch/          patch layer around MinHook: generated site table (sites.inc), preflight, all-or-nothing
                    install and rollback, quiescence probe, arm flags and dry-arm mode, detour stubs, and the
                    fail-open handler template (detour.h); linked but not called until the boot wires it
third_party/sdl2/   SDL 2.32.10 static build recipe, our SDL_config.h, pins (sdl2.lock.json), licences
third_party/minhook/ MinHook 1.3.4 static build recipe and pins (minhook.lock.json)
testsupport/        host-test DLL only (never shipped): scripted device world, recorder, replay, ILCT_* exports
THIRD_PARTY_NOTICES.txt  licences of the statically linked third-party code; ships next to the DLL
```

## Build

Toolchain: MSYS2 UCRT64 `g++ 16.2.0 (Rev3)`, GNU ld `2.47.20260726`, and the pacman packages pinned in
`build.py` (`gcc 16.2.0-3`, `binutils 2.47-3`, `crt` and `headers 14.0.0.r353.g6df76fa52`). `build.py` refuses any
other version. Set `IVALICE_LEGACY_CXX` if g++ is not at `C:/msys64/ucrt64/bin/g++.exe`.

```
python native/coop/build.py                  # -> native/coop/build/ (DLL, map, manifest.json, audit.txt)
python native/coop/build.py --repro          # two clean builds must be byte-identical
python native/coop/build.py --update-schema  # after an intended change to bridge_abi.h
python native/coop/build.py --update-golden  # after an intended source change
python native/coop/audit_pe.py [dll]         # audit only
```

The DLL uses the normal mingw-w64 DLL start-up (`DllMainCRTStartup`) on the Universal CRT, with libgcc and
libstdc++ linked statically, no exceptions and no RTTI. Imports must equal `pe-allowlist.json`; any network or
platform-service import, a DLL or function name stored as a string, an ordinal-only export, or thread-local
storage fails the build.

## Tests

```
python -m pytest tests/test_coop_native_inert.py tests/test_coop_bridge_abi.py tests/test_coop_input_service.py
python -m pytest tests/test_coop_input_actions.py   # action layer and seat commands (same host-test DLL)
python -m pytest tests/test_coop_patch_engine.py   # needs IVALICE_LEGACY_GAME (exe read-only), unicorn, capstone
python -m pytest tests/test_coop_image_twin.py     # transaction drills with parked threads (same needs)
python -m pytest tests/test_coop_game_thread.py    # queue, tick, dry-arm equivalence (same needs)
python -m pytest tests/test_coop_job_expiry.py     # no job stays PENDING forever (same needs)
python -m pytest tests/test_coop_session.py        # session core on synthetic game memory
python -m pytest tests/test_coop_input_edges.py    # per-consumer edge cursors
python -m pytest tests/test_coop_call_island.py    # call-site islands: transaction, unwind walks (same needs)
python -m pytest tests/test_coop_input_isolation.py  # input isolation: handlers, game-code differential
python -m pytest tests/test_coop_control.py          # kill switch, seat toggles, ordered disarm
python -m pytest tests/test_coop_loopback.py         # seam codec: 1 h input and HUD replays on/off, 8 h soak (twin)
python -m pytest tests/test_coop_camera.py           # shared camera: maths, subsystem, hooks (unicorn)
python -m pytest tests/test_coop_party.py            # seat changes: rules, matrix, operation, watcher
python -m pytest tests/test_coop_interaction.py      # guest interactions: rules, handlers, window, routing (unicorn)
python -m pytest tests/test_coop_menu.py             # guest battle menus: catalogs, restrictions, model, Gambits
python -m pytest tests/test_coop_menu_cost.py        # costs vs the game's code, admission greys, MP economy, help (unicorn)
python -m pytest tests/test_coop_targets.py          # target candidates, party/reserve rules, cursor, live picker
python -m pytest tests/test_coop_submit.py           # request slots, stale checks, operations, Gambits (unicorn)
python -m pytest tests/test_coop_wait.py             # Wait rule (4-seat matrix), reason table, engine (unicorn)
python -m pytest tests/test_coop_frontend.py         # bindings, presenter guard, selectors, KO recovery, Quickening
python -m pytest tests/test_coop_focus.py            # keep running in background: rows, handlers, states (unicorn)
python -m pytest tests/test_coop_hud.py              # HUD canvas: rows, gates, budget, guard, native equivalence (unicorn)
python -m pytest tests/test_coop_hud_native.py       # reduced party list, target circles off, give-back (ILC_MRP_PACK)
python -m pytest tests/test_coop_hud_driver.py       # frame driver, presenter stamp, HudSnapshot, held menus (unicorn)
python -m pytest tests/test_coop_hud_menus.py        # co-op menus: native geometry, layout, pages, seat edges
python -m pytest tests/test_coop_hud_party_rows.py   # party rows: anchors, timers, numerals, low-HP glow (unicorn)
python -m pytest tests/test_coop_hud_target_info.py  # Target Info: Libra gating, last targeter, drops, TI-01
python -m pytest tests/test_coop_hud_battle_log.py   # battle log: ring, LG-01, replayed lines, fade, native log
python -m pytest tests/test_coop_hud_battle_log_hits.py   # battle log: per-hit feed, kill reward, chain, unicorn
python -m pytest tests/test_coop_hud_world_marks.py  # seat lines, seat diamonds, AoE-only rings (unicorn)
python -m pytest tests/test_coop_battle_hud.py       # Battle HUD settings: element switches, Identify, report, page
python -m pytest tests/test_coop_hud_layout.py       # couch layout: corners, boss gauge, every element at 3 resolutions
python -m pytest tests/test_coop_options.py          # player options, arm mask, Wait ANY/ALL, guest pause, hook.log
python -m pytest tests/test_coop_overlay.py         # overlay: font, batch, scene, WARP draws and state, OV-01, P1 native
python -m pytest tests/test_coop_seat_limit.py       # four seats: P4 as keyboard seat, device owner, command seat, F9
```

`test_coop_menu.py` also runs the catalog on the real tables when `ILC_BATTLE_PACK` names a `battle_pack.bin` and,
optionally, `ILC_BATTLE_PACK_DIR` a folder of loose `section_NNN.bin` files (as the External File Loader would load
them, e.g. Legacy's own); without them that class is skipped. `test_coop_menu_cost.py` checks every Legacy item's and
menu action's help line when `ILC_HELP_ACTION` names Legacy's `help_action.bin` (with `ILC_BATTLE_PACK`), and runs the
game's cost code in unicorn with `IVALICE_LEGACY_GAME`.

`test_coop_input_service.py` also builds the host-test DLL (`build.py --test-dll`, `ilc-coop-test.dll`): the
shipped sources plus `testsupport/`, with `ILCT_*` exports that drive the input service tick by tick on a
scripted device world, record every device call and replay a recording. It is never packaged.

## Input service (`src/input/`)

One thread, started by `ilc::input::start_live()` from the module's start-up thread (never from `DllMain`),
polls at 125 Hz on a high-resolution waitable timer:

- **XInput**: `XInputGetState` from `xinput1_4.dll` in the System32 folder, loaded by full path (the folder comes
  from `GetSystemDirectoryW`). A failed read is bridged with the last good sample for 0.100 s from the first
  failure, then the slot is disconnected; an absent slot is probed every 0.250 s.
- **SDL2 game controllers** (DualSense, DualShock, Switch Pro and other HID pads; SDL also lists XInput pads as
  `XInput#n`, which join that slot as one pad). Only SDL's game controller subsystem is initialized. Every hint
  is set at override priority, so `SDL_*` environment variables cannot change them: no signal handlers, no timer
  resolution change, no mapping file, no HIDAPI output reports for rumble or LEDs.
- **Identities and aliases**: GUID, uid (first 16 bytes of the SHA-256 of the lower-cased device path) and an
  ordinal for identical models. Configured XInput/SDL aliases form one pad group with one owner seat.
- **Frames**: one `ilc_seat_input_frame` per seat (`include/ilc/seat_input.h`): strengths after a radial
  deadzone of 0.24, held/pressed/released bits, the raw pad as an `XINPUT_GAMEPAD` image, and hold absorption on
  every source change. The session core reads frames through `seat_frame_source`, which a network transport can
  implement later.
- **Inventory**: `GET_DEVICES` returns one entry per connected physical pad, with its owner seat and the P1 lock
  bit, or `NOT_READY` while the service is stopped.

### Actions and seat commands

- **Actions** (`actions.h`): each tick turns the seat frames, the keyboard and the applied binding tables into one
  `ilc_seat_action_frame` per seat (held, pressed, released, repeated, absorbed, strength per action), read through
  `seat_action_source`. Up to four bindings per action; pad bindings use the seat's own pad (through its aliases);
  keys and mouse buttons count only for the keyboard seat and only while the game is in the foreground
  (`keyboard_live.cpp`: `GetForegroundWindow`/`GetWindowThreadProcessId`, `GetAsyncKeyState` for the bound keys
  only, no hook). One exception: a guest seat that does not own the keyboard still reads the keys of its own
  `TOGGLE_CONTROL` (drop-in/out; never a mouse button or another action), in the foreground only, as its own action
  edge without changing its prompts; they read as released while a capture reads the whole keyboard. So every
  drop-in/out reaches the co-op switches as an action edge; the switches read only the kill-switch key themselves
  (host-only). Menu directions repeat after 0.250 s, then every 0.100 s (fast: 0.100 s, then 0.035 s).
  Every discontinuity absorbs what is held until it is released: start, device change, binding change, keyboard
  moving to another seat, the window regaining the foreground (keys only), decide-button swap (confirm/cancel),
  seat move, park/unpark, the end of a pairing or a capture.
- **Seat commands** (`seat_control.h`), on the input thread, never editing settings: pairing proof (armed only
  when no A is down; the target's A opens a 0.200 s window; another pad answering is refused, an XInput mirror of
  the target is learned as its alias; Player 1's pad and another guest's pad are refused, the latter naming the
  seat so the menu can offer a swap; 20 s timeout), Player 1's guarded pad change (the new pad drives Player 1
  provisionally, a fresh A on it within 10 s confirms, settings within 5 s keep it, otherwise it reverts), binding
  capture (the next fresh press after everything held at the start is released; 20 s), live park, and seat move
  (absorbs and queues the move for the session). Outcomes are `PAIRING_RESULT`, `CAPTURE_RESULT` and
  `COMMAND_RESULT` events with the schema's `pairing_outcome` and `capture_outcome` enums.
- **The co-op on/off key** (`CAPTURE_START` with seat 0, `arg0` 0, `arg1` = kill-switch slot 0..3): the same capture
  rules (no capture on the start tick, held keys released first, keys pressed while the game is in the background
  released first, 20 s, `CAPTURE_CANCEL` seat 0 or any newer start cancels it), but only keyboard keys and the right
  mouse button count, read from the keyboard whichever seat owns it (or none). Pads, the left mouse button and the
  other mouse buttons' key codes (0x04-0x06) are ignored: they neither capture nor end it. Escape is a key like any
  other (there is no built-in cancel key). The result has seat 0 and action `CAPTURE_KILL_SWITCH_ACTION` (0xFF). The
  keyboard's seat reads neutral actions meanwhile and the captured key stays absorbed for it. The co-op switches
  (`src/control/`) do not act on the kill-switch or guest toggle keys from the moment the bridge queues the command
  until the beat after the capture ends (at most 22 s), so pressing the current key to re-bind it does nothing.

## SDL2 (static, `third_party/sdl2/`)

SDL 2.32.10 is compiled from the official release tarball by `third_party/sdl2/build_sdl2.py` into
`build/sdl2/libSDL2-ilc.a` (not committed). `sdl2.lock.json` pins the tarball SHA-256 (the release is signed by
Sam Lantinga, key `1528635D8053A57F77D1E08630A59377A7763BE6`), the source tree digest and the library SHA-256;
any difference stops the build. `SDL_config.h` is our build configuration: events, joystick and game controller
(DirectInput, XInput and HIDAPI drivers), HIDAPI, threads, timers and loadso only; `SDL_DYNAMIC_API` and SDL
assertions are off. SDL's own sources are not modified.

```
python native/coop/third_party/sdl2/build_sdl2.py --tarball PATH/SDL2-2.32.10.tar.gz   # once per machine
python native/coop/build.py                      # uses the cached library when it matches the lock
python native/coop/build.py --sdl2-tarball PATH  # or builds the library first
```

Licences: `THIRD_PARTY_NOTICES.txt` (SDL zlib licence, HIDAPI, yuv2rgb). It must ship next to the DLL.

`ILC_REQUIRE_NATIVE=1` turns a missing toolchain into failures instead of skips. With `IVALICE_LEGACY_GAME` set,
the emulation test also runs the installed loader modules' `FF12HgetVer` as a control (read-only).

## Patch layer (`src/patch/`, MinHook 1.3.4)

MinHook does the hook mechanics; `patch_engine.cpp` owns the rules around it. `sites.inc` is generated by
`tools/gen_site_table.py` from the reverse-engineered site table and the pinned exe (`--check` reports drift); for every PATCH row
it also pins every byte MinHook relocates (`hook_len`, `hook_tail`). Preflight is read-only (base 0x120000,
timestamp, SizeOfImage, every checked byte, INTERIOR flags, window overlaps) and a refusal never initialises
MinHook. Install creates every hook, enables them with one `MH_ApplyQueued`, and checks that each site holds
MinHook's jump to our stub; any failure disables everything and checks the original bytes again. Each site's stub
falls straight through to the trampoline while its gate is closed (site, subsystem, global arm, handler); the
open path saves the full register state and runs the handler only while the liveness record is fresh. Nothing
calls the layer yet; host tests reach it through the test DLL's `ILCT_Patch*` exports.

- **Quiescence.** Before MinHook is touched, every other thread is suspended (ids and handles in static arrays, so
  nothing allocates while a thread is suspended), each RIP is checked against the hook windows, and all are
  resumed. A thread inside a window means wait 2 ms and probe again, up to 8 probes; then `QUIESCE_BUSY` with
  nothing written. A thread that arrives after the clean probe is moved into the trampoline by MinHook's own
  freeze, and moved back by every rollback path. No hook window holds a call before its last instruction (tested),
  so no return address can point inside one.
- **Last look.** Just before the enable, every PATCH row is compared again; a change made meanwhile by another
  module aborts with `SITE_CHANGED`, nothing enabled.
- **Rollback.** After an enable, a stub's continuation is pointed at its site only where the original bytes are
  verified back; elsewhere it stays MinHook's trampoline (its buffer stays mapped).
- **MinHook 1.3.4 defect worked around.** `EnableHook()` freezes threads with `ACTION_ENABLE` even when it
  disables, so `MH_DisableHook` leaves a thread that sits in a trampoline there. The layer never calls it: it
  disables through `MH_QueueDisableHook` + `MH_ApplyQueued`, or removes an enabled hook with `MH_RemoveHook`, both of
  which move such a thread back.
- **Modes.** The layer starts **dry-armed** (`MODE_DRY`, the first-boot mode): hooks installed, every non-CORE gate
  closed and refused again in the dispatcher, so every co-op detour runs stock. Only the CORE rows run: the UI tick
  (UT-01), for the settings safe point (in DRY it runs no game-thread job), and the four Keep running in background
  rows, which act only on the player's own setting. `MODE_LIVE` lets the other gates open. The UI tick switches to
  `MODE_LIVE` once, at the safe point, after the first applied settings record (generation 1 or more) has had its
  `subsystems_armed` mask handed to the gates; the native defaults (generation 0) never go live and nothing returns
  to DRY. LIVE with a mask of 0 is still dry-armed in effect (every non-CORE row stock); the kill switch changes the
  mask, never the mode. Status reports `DRY_ARMED` while the layer is DRY or no armed, unfaulted subsystem is in the
  gates' mask, and `LIVE` otherwise.
- **Liveness.** `epoch` and `heartbeat_ms` sit in a seqlock written by one thread; a torn, stale (over 2 s) or
  disarmed sample is stock for that one call, never a latch. A fault latch per subsystem (or for everything) is one
  atomic OR, honoured by the dispatcher at once.
- **Handlers.** Build them with `fail_open<Record, Slot, Body>` (`detour.h`): it re-checks the subsystem, copies the
  subsystem's seqlock-published record, requires the record's epoch to match, and otherwise runs stock. A body must
  not block, allocate or raise. The stub saves the general registers, the flags and XMM0-5 only, never the upper
  halves of the YMM registers. A handler that calls UCRT `memcpy`/`memset` (which may use AVX) can clobber the
  upper halves of YMM0-5 (and, since the Win64 ABI makes every YMM upper half volatile, of YMM6-15 too). That is
  harmless at call-type sites, where those halves are dead across the call; a body at a mid-function site must
  not call UCRT string functions (use plain loops or `__builtin_memcpy` of a fixed small size).

## Call-site islands (`src/patch/call_island.*`, `src/patch/call_stubs.cpp`)

The 18 PATCH rows whose hook window holds a `call rel32` are not given to MinHook. Only the call's rel32 changes:
it points at a 16-byte jump-only island near the image, which jumps to a per-row entry in this DLL. The entry is
one indirect jump through a slot: the original callee while the row's gate is closed (stock: the callee returns
straight to the game, nothing of ours is on the stack), the row's slow path while it is open. The slow paths and
their shared body carry static unwind data (`.pdata`), so an unwind from a handler, or from a callee called
around, walks through our frames to the game's return address. Handlers see the registers at the callee's entry
and may run the callee (RUN_ORIGINAL), skip it (RESUME) or call it and inspect its result (CALL_AROUND +
`set_after`). Islands are prepared with MinHook's creates, the rel32s are written after MinHook's enable inside
one freeze of every other thread, and every rollback writes the pinned rel32s back. The island page stays mapped
once a rel32 has pointed at it. RSP is never changed on the way to the callee: it enters exactly as the game's own
call left it (16-byte alignment kept).

## Input isolation (`src/isolation/`, `src/input/pad_mirror.*`)

Hooks at the game's pad finalize calls (XInput, DirectInput, Steam), the keyboard finalize call, the pad focus
gate and the focus-loss dispatch. While a guest seat has a pad or the keyboard, or Player 1 plays on a pad only
SDL reads: P1's own XInput device passes untouched, every other pad object gets the neutral platform block before
the game's finalizer runs (edges release, nothing latches), and P1 on SDL is written into one host pad object's
platform block from the input service's per-seat pad mirror (presses since the last read included). Guests' flee
requests (the movement subsystem's flee word) set P1's flee control. The keyboard keeps only the shared keys (map,
speed, map transparency) while a guest or nobody owns it; a 240-update lease refilled every co-op beat gives the
keyboard back if the co-op thread stalls. The pad focus gate belongs to Keep running in background (`src/focus/`);
the focus-loss event rewrite was dropped (that setting keeps the game running instead). If the bytes before the
focus gate are not stock, that one hook is skipped at install and reported.

## Shared camera (`src/camera/`)

With two or more players on the field (P1 if alive, every manual guest that is present and not down) in a valid
world where the camera may run, one group camera frames them all: the focus follows the group centre (the upper
player kept in view) with a deadzone and a spring, leads along the group's motion, and the boom grows as they
spread (quickly) and shrinks back (slowly). It takes over only after the game's own camera has arrived (three ready
samples), so it never flies in across a load; a seat toggle resets it in place (snapped to the new group centre,
without handing back to the game's camera). The extra boom freezes against a wall the game
reports and backs off under collision pressure, and steps aside while P1 orbits the camera. The four calls of the
game's camera collision query go through call islands: a hit inside a capsule between two players (or right next to
the ray origin while the focus is away from every head) is asked again from just past it, so scenery between the
players does not collapse the boom. The focus store and the two boom multiplications of the primary rig take the
shared focus and rig boom + extra; the rig itself is never written. With fewer than two players, or anything stale,
every hook runs the game's own code.

## Game-thread queue and UI tick (`src/runtime/game_thread.*`, `src/runtime/tick.*`)

Game functions run only on the game thread, inside the UT-01 tick. Producers on any thread `push` a job (an
operation number from a table sealed at boot, never a pointer; issuer, epoch, generation, ttl, four arguments)
into a bounded lock-free ring (64 jobs; `PUSH_FULL` instead of waiting). Each frame the tick runs the settings
safe point, hands a newly applied `subsystems_armed` mask to the arm gates (try-lock, retried next frame), then
drains up to 16 jobs. Right before each job it re-samples liveness and the session generation: the job runs only
if its subsystem may act, its epoch and generation are current and it is younger than its ttl (at most 2 s);
otherwise it is rejected with the reason and never runs late. Every ticket gets exactly one result in a
128-entry completion table. In DRY mode queued jobs are rejected as `DISARMED` without running. Re-entry runs
stock. The stock tick call always follows exactly once. The tick is also where the layer goes LIVE (see "Modes"
above): once, right after the first applied record's mask is in, with a try-lock retried next frame.

No job stays `PENDING` forever: drain, flush and `reap()` share one consumer flag (taken without waiting). When the
game thread is not draining (stale liveness, disarmed, faulted, DRY), `reap()` on any other thread rejects, oldest
first, every job that can no longer run (`DISARMED`, or `EXPIRED` past its ttl) without running it. The co-op
thread reaps whenever the tick has not run for 100 ms, the shutdown path reaps right after disarming, and a
requester's `await_result()` reaps by itself once a job is past ttl + 250 ms. Because the reap is FIFO, the
bound for a terminal result is max(its own ttl, the ttl of the jobs ahead of it) + 250 ms after queuing, at most
about 2.25 s (ttl is capped at 2 s), given one reap that finds the consumer side free.

World-requiring operations (every operation not registered with `OPF_ANY_WORLD`) are refused at push
(`PUSH_NO_WORLD`) and rejected at drain (`REJECT_NO_WORLD`) unless the session core reports a valid world in exactly
the job's epoch (`set_world`): an epoch match alone is not a valid world, because loads, the title screen and
cutscenes are epochs of their own. `reap()` also retires jobs of an older epoch and jobs whose epoch is an invalid
stretch.

## Session core (`src/session/`)

Runs on the co-op thread (one step per heartbeat, 20 ms), never on a game thread, and only reads game memory, always
through the fault-safe probe (an unreadable page is an UNKNOWN sample, never a fault or a fact). Scalars are read
with one naturally aligned load of their own width (`load_from_checked`), so a value the game is writing is never
read half old, half new.

* **World:** battle state, party layouts (current and HUD), leader, area ids, control lock, command menu, modal,
  town and mount flags; the active party's actors from the actor manager (a full scan at most every 250 ms, sooner
  after a layout change or a missing actor; cached slots re-checked in O(1) between scans).
* **Epochs:** the world epoch counts coherent worlds (battle state + settled area). The session epoch, which the
  liveness record, game-thread jobs and published records carry, starts at 1 and moves at every boundary in either
  direction: map jump, load, title screen, game over, cutscene. Nothing prepared in one epoch acts in another.
* **Gates:** world valid, gameplay, movement, camera, P1 and guest battle menus, co-op logical, shared camera,
  guests' movement hooks, guest interaction (towns included), party down (the game's own recovery owns input).
* **Seat table:** `ILC_SEATS` entries, filled only below `ILC_ACTIVE_SEATS_MAX` (4: every seat). Per seat: character, party
  slot, actor and physics pointers (compare only), handle, incarnation, alive and movable state, whether it steers.
  A party or leader change made by the game or another mod is adopted once it has held for 200 ms (never during a
  KO promotion or a summon). P1's availability to lead is one bracketed sample (HP and battle pointer read before
  and after the status, 3 tries, else UNKNOWN); Berserk blocks it unless the movement subsystem is live. A settings
  record changes identity only through a seat's character or input: colours, sounds, park and the switch never move
  the epoch or release a guest, and unchanged seats keep their live character (AUTO fills, adopted swaps).
* **Contract:** an epoch match does not mean the world is valid; anything that touches world objects checks
  `GATE_WORLD`.
* **Ownership:** every old owner is released before a new one is acquired; each acquire is revalidated against the
  live actor (handle, physics, world); a released character is asked to follow its leader at most 4 times within
  a bounded window, waiting without writes while it is down, petrified, leading or busy.
* **Publication:** one record (`record_slot`, wait-free) per step, and the session generation to the game-thread
  queue (jobs made for an older seat layout are rejected). `GET_SEATS` reports the session state, epoch, each
  seat's live character and incarnation.

## Edge cursors (`src/input/edge_history.*`)

Every input tick also lands in a 256-tick history. Each consumer owns a cursor; `edges::since(cursor, seat)` returns
the edges (pad and action pressed, released, repeat pulses) of every tick after the cursor, ORed, plus the newest
held state, and moves the cursor. A slow reader (the game thread, a future transport) therefore never misses a tap,
and consumers never affect each other. A cursor that fell more than 256 ticks behind is told so (`overrun`); a
fresh cursor or a new service run re-seeds without replaying old presses.

## Guest movement (`src/movement/`)

A guest walks their own character with their seat's move actions; the AI keeps away from a controlled character.

- **Co-op thread** (`movement.cpp`, `planner.cpp`, `steering.cpp`), once per heartbeat after the session step: the
  session record says which actor each guest steers; the move action strengths, the walk/run toggle (an edge from
  the movement consumer's own cursor; an overrun or a new session epoch reseeds it) and the flee action give the
  request; the camera view matrix gives the basis; a few actor facts (scene root, fighting stance, execution
  motion and its target) come through the fault-safe reader. The stick follows the leader's response curve (no
  movement up to 0.40, a short ramp, then 1/3 of the running command, the running command from 0.92); the body
  turns at the leader's measured rate. Control starts from the actor's own headings. The result is one small
  steering record (`record_slot`) and a flee request word (epoch-tagged, read by the input detours).
- **Detours** (`hooks.cpp`, all through `fail_open`): MV-01 writes the command into the physics object and, for real
  input outside an execution motion, selects the steering path over an all-zero root-motion vector; MV-02 and
  MV-03 keep the guest's heading and facing in the two stores the game makes; MV-04 (a call island row) calls the
  game's facing setter for a guest's actor whose facing differs, then the original read; MV-05 evaluates the native
  follower lists as empty and MV-06 takes the no-approach exit (rbx restored first) for a controlled actor. Every
  detour compares pointers before it touches memory. MV-07 (gateways) is not armed here.
- **Follow request** (`OP_FOLLOW`, world-requiring game-thread operation): when the session releases a character,
  the request re-resolves the actor from its handle, checks its physics object, that it is idle, untargeted and on
  a native follower list, and changes its action and the action shadow from idle to "follow the leader".

```
python native/coop/third_party/minhook/build_minhook.py --tarball PATH/minhook-1.3.4.tar.gz   # once per machine
python native/coop/build.py --minhook-tarball PATH   # or let build.py build the library first
```

## Town visibility (`src/towns/`, PARTY)

In a town only player-controlled characters have a body: P1's character, the native leader, and the character of
every guest seat in session under manual control (story allies included). With co-op on and no guest seated, only
P1's leader appears. Outside towns nothing changes; menus always list the whole party.

- **TW-01** (0x326A34, the join point of the layout builder 0x3266C0 before its bind loop; a Legacy-only row from
  `tools/sites_legacy.json`): in the engine's town mode (the area's town bit, read as 0x377C20 reads it, and neither
  scripted single-body mode) it blanks the current and HUD slots of uncontrolled members; the game's own bind loop
  then unbinds those slot actors. Remove-only: it writes nothing when the leader is not in the current layout (the
  stock town collapse, Companions' Leader Only), never touches the default, menu or fifth layout, and checks the
  builder's registers and the party block pointer first.
- **Co-op thread** (`towns.cpp`): publishes the controlled set (town record) every heartbeat, and when the bodies no
  longer match it in a town (a guest joined, left or changed character; the leader changed) asks for one rebuild,
  debounced: shows after 40 ms, hides after 300 ms (longer than the session's identity settle, so a P1/guest swap
  never rebuilds), at most one applied rebuild per controlled set and world, 250 ms between attempts. A stock town
  arm (no Companions "Party In Towns") means guests cannot get a body there: no rebuild is asked.
- **Rebuild** (`OP_TOWN_REBUILD`, world-requiring): re-checks the session (valid world, field phase, player
  control), town mode and that no re-materialize task is in flight, then calls the stock re-materialize 0x37CA80.

## Guest interactions (`src/interaction/`, MOVEMENT; loot: PARTY)

Guests use the world like Player 1: gateways and doors, chests, save crystals, NPC talk and the shops a talk opens,
walk-over loot, in the field and in towns. The game has one interaction source (the controlled actor), one target
(the descriptor) and one consumer per frame; guests borrow them inside our own calls only, and Player 1's state is
put back before the frame goes on. Press only: a guest's CONFIRM starts an interaction, there is no guest prompt.

- **Gateways** (MV-07, call row, the trigger-volume pass 0x25B600): the stock pass for the controlled actor, then
  the same native pass once per steering guest with the controlled handle naming the guest, restored after each pass;
  never twice for one character, and no further pass once an area change is pending. **MV-08** (the collision
  class store): a guest's skeleton gets the leader's class, so trigger volumes treat it like the leader.
- **Private scans** (IX-01, call row, the generic scan 0x25C170 for doors, chests and crystals): before the stock
  scan for Player 1, one pass per steering guest. During a pass IX-03/IX-04 name the guest's entry as the source,
  IX-05..IX-10 measure the gates from it, and IX-02 sends the publication to the guest's private slots (published
  for the co-op thread with a pass tick). Outside a pass every one of those sites runs its original instructions.
- **Requests** (co-op thread): a guest's CONFIRM press, with the guest's character free (no weapon handling, no
  execution motion, idle or walking), becomes one request: the object its own scan found (a chest, a sleeping mimic
  woken with state 10, or a door/crystal/switch, for which Player 1's leader must be free because the game animates
  it), else NPC talk. One interaction at a time.
- **The window** (IX-20, call row, the consumer call 0x268D00): with a request claimed and Player 1's own trigger
  clear, it saves the source, the can-use byte and the descriptor; resolves the guest's entry from its handle now;
  publishes the guest's object descriptor, or runs the exact NPC scan 0x25B820 from the guest with the NPC guards
  IX-17..IX-19 armed for that call only (the NPCs' range bit, proximity timer and proximity script stay Player 1's);
  re-reads the target entry immediately before the call (the consumer dereferences it without a check: a stale or
  torn target is refused; after the auxiliary resolver it must still be the same entry, checked again in full);
  calls the consumer; restores everything. Refused without touching anything while an area change is pending or in
  progress, the UI is off, interactions are suppressed, an event is busy, the consumer's own gate refuses Player 1's
  controlled actor, or the seat layout changed since the press (a swap). The call's return address is read before
  the request is claimed. If the rows cannot attach, the boot flag `INTERACTIONS_FAILED` says so (Mod Menu
  diagnostics).
- **The hold** (co-op thread): after a guest's interaction ran, its character stands still (movement reads the hold)
  while its dialog, the queued menu, a frontend (the shop: frontend 10 active) or a native modal is up, and 80 ms
  after the last of them; one second if nothing opens. Once one has opened, that guest drives the native menus with
  its own pad: input isolation feeds the guest's pad through the host object and masks every other pad object,
  Player 1's own included (`ISO_DRIVER`); the keyboard is filtered unless that guest owns it. Player 1 waits. When
  the feeding seat changes (a drive starts or ends) its presses so far are consumed unused, so nothing made before
  the drive is replayed into it. The hold ends at once when the guest leaves, is parked or loses its pad, the world
  changes, or MOVEMENT may no longer act (the kill switch): it does not come back when co-op is switched on again.
- **Loot** (LT-01, call row, the ownership query 0x3273B0 at the walk-over pickup only): a seat-controlled actor's
  handle answers "party"; everything else asks the game. The query's seven other callers are untouched.

## Seat changes (`src/party/`, PARTY)

Who controls which character is the session core's identity: characters go to playing seats in seat order (a
seat's preferred character first, then the first free member in party order), every seat keeps its character
through party reorders, and a playing seat with no free regular is benched (`SEATF_BENCHED`: it keeps its seat and
input and claims the next free member, P1 > P2 > P3). Story guests are never offered; Player 1 always keeps a seat
and a character and drives the native leader. This subsystem adds the changes players ask for and makes every
change of hands atomic.

- **Requests** (co-op thread, `party.cpp`): the Mod Menu's move-seat command (through the input service) or
  `request_change(seat, member)`. Planned with `assign.h` (swap when taken; a benched seat only claims a free
  member; Player 1 asking for a guest's character, or a guest for Player 1's, is a leader change that exchanges the
  two), queued as `OP_PARTY_CHANGE`; a request that cannot run yet (load, cutscene, menu, a stale record) waits and is
  planned again for up to 5 s.
- **Operation** (`OP_PARTY_CHANGE`, world-requiring, in the UI-tick drain): re-reads the party, leader, seat bodies
  and frame state (no area change pending or in progress, no script roster edit, field ready, no frontend menu, no
  controlled-actor change pending, no summon/chocobo/scripted mode, the same location), plans again and refuses
  unless it is the same change. Then, in one callback: the leader setter 0x327850 for a leader change (applied by the
  game at the end of this frame), a steering remap for every guest that changed hands (`remap.h`: the movement
  detours use it on their record copy, so the guest drives its new body from the next frame), and the change posted
  to the session, which adopts it at its next step without the identity settle.
- **Leader watcher** (UI tick, before the drain and, through the call row's after-handler, after the stock tick):
  a leader change made by the game, a script or another mod (Companions' town leader cycle) onto a guest's
  character, while Player 1's character could still lead, becomes the same atomic Player 1 / guest exchange. Other
  external changes are left to the session's settled adoption. A town cycle onto a member without a body runs the
  town rebuild in the same callback.
- **Towns** (Companions' "Party In Towns" arm in place): an unseated roster member has no body there. Player 1's
  leader change and a guest's take-free onto that member are planned against the roster (default layout; its member
  record says whether it can lead); the operation writes the leader or lets TW-01 keep the member
  (`towns::allow_pending`, until the session names it), runs the town rebuild and steers the body it bound, in the
  same callback. If the rebuild does not run, the leader write is undone and the request is tried again. A member
  outside the roster is still refused.
- The steering remap is retired once the session adopted the change and the steering record names the new bodies
  (or after 250 ms, or at a new epoch). The shared camera keys its velocity history by seat and body, so a swap never
  becomes a camera lead.

## Guest battle menus (`src/menu/`, data model)

Each guest's battle menu is a model on the co-op thread: Attack, Magicks & Technicks (five schools and Technicks),
Mist (Quickening, Summon) or Dismiss while the guest's Esper is out, Gambits, Items. Nothing here reads input or calls
the game; it is the data the menu input, the target picker (`src/targets/`) and the requests (`src/submit/`) use.

- **Catalogs from the game, not a bundled copy** (`tables.*`, `live.*`): the action, licence, party-member, item,
  magick, technick and augment tables are read from the game's own loaded data through the globals the battle-pack
  loader registers (licences 0x2EBF038, party members 0x2EBF130, items 0x2EBF100, magicks 0x2EBF0F0, technicks
  0x2EBF0E8, actions 0x2EBF138, augments 0x2EBF040): a qword pointer to a header whose row is
  `(u32)(rows + id * size)`, formed in 32 bits like the game, with the resource base `[0x1F83530]` required to be 0
  (anything else is refused). Whatever Legacy replaced is what the game loaded, so renamed, re-costed and re-targeted
  actions and new items are listed with the game's own values. Entries carry action ids, never text: names and help
  come from the game's text at presentation. An error in an optional table (party members, augments) is reported in
  `optional_status` and leaves the catalog usable. The loader's registrations are reported at install (INFO rows), and
  the name lookup and availability check must be stock (REQUIRE rows).
- **What is listed** (`catalog.*`): magicks owned by the party and licensed by the character's live magick licence
  summary (or needing no licence), technicks owned and licensed by an acquired node, the character's own
  Quickenings (Mist bits or Quickening licences), Espers (Mist bit and licence), items held in the player's saved
  order (else by sort key) with their available count (owned minus what is held back; none left = greyed, so two
  seats cannot both take the last Potion). The story guests the game checks against a fixed list (party ids
  7..0x19 and 0x1B..0x27) use it; ids 0..6 and 0x1A use ownership and licences. Target groups, area radius and
  revival flags come from the action row.
- **Greys**: KO, Stone, Stop, Sleep, Confuse, Disable and X-Zone grey everything; Berserk leaves only Attack;
  Silence greys the schools; the area's command seals (0x22C314C) grey Attack, Magicks, Technicks or Items; every
  action is greyed when the game's admission check (0x303BD0) would refuse it on cost (`cost.*`): current MP (signed)
  below the MP cost (0x309110), Mist charges below the charge cost of an Esper or Quickening (0x308FF0), or, for a
  caster paying in gil (Offering), both the party's gil and the gil the actor already put aside for its queued action
  below the gil price (0x309060). The costs are the game's functions restated (the augment-adjusted cost 0x2F95D0:
  Offering, Parsimony, Spellspring, Channeling I-III in single precision; Quickening and Esper ids pay their base on
  the party side) and checked against the game's code in unicorn. An MP cost that cannot be computed (a Channeling
  value unread) is flagged and not greyed for MP: the admission decides at submission.
- **Legacy's MP economy** (`cost.*`): up to version 0.2.x a party member's Magick (menu kind 1, listed cost above 0)
  paid a surcharge after the cast, the game's own price times Magick / DIVISOR (never below it, at most CAP times it,
  rounded half up; version 0.1.x scaled the listed cost). Since 0.3.0 the surcharge is removed: CAP is 1, so the
  surcharge is always 0 and the menu shows the game's own price. It greys on the game's price, as the game admits the
  cast on it. The version, DIVISOR and CAP still come in the settings record (Lua reads Legacy's `mp_economy.lua`,
  `coop/economy.lua`); without them every number is the game's.
- **Help lines** (`help.*`): an action's help id (action row +0x00) is resolved the way the game's help lookup
  (0x2F9860) walks its banks (bank = id / 1000, loaded flag, bounds), read through the fault-safe reader, formatting
  tokens removed. Items show the help of the action they use. The menu view shows it in the help strip, fitted to
  the strip; a greyed row says why instead, and a row without game help keeps the view's own line.
- **Model** (`model.*`): closed / list / target, cursor per page, page stack; SUBMIT and TOGGLE_GAMBITS events.
  A new catalog keeps the guest where it was (pages still linked, the same row by identity, a pending action while
  it is still listed and enabled); an invalid catalog changes nothing; a change of character closes the menu and
  drops the previous character's pages, so a failed rebuild serves nothing.
  Catalogs are rebuilt only when their inputs' signature changes (`menus.*`).
- **Gambits** (`gambits.*`): the on/off state mirrors member record +0 bit 2; a toggle request is checked against
  the identity the game-thread operation proves again (a regular, a full handle, actor/physics/record, the record
  being the party block's record for that member) and refused under KO or Stone; results keep the toggle's codes;
  sounds 0x12 (on) and 0x13 (off).

## Input firewall and stock-frontend gate (`src/firewall/`, FIREWALL_MENUS)

The co-op thread composes one policy record every beat (after input isolation and guest interactions) from the
session record, the isolation record, the co-op menus' report (`post_menus`) and the players' edges; the hooks read
it wait-free (the dispatcher's epoch or one either side, else stock).

- **Per-device masks** (FW-01, the pad get-button 0x79D670 shared by the DirectInput, XInput and Steam pads): a
  masked semantic id reads 0 on that device. P1's device is a pointer comparison (every pad while no guest device
  is configured; else P1's own XInput object or isolation's host object, as isolation's pad visits found them);
  every other pad gets the other-slots mask. FW-02 (get-axis) zeroes P1's stick while the stock command tree is the
  active menu; FW-03/04/05 mask the mouse buttons, consume the wheel and clear mouse look for a guest on keyboard
  and mouse. The keyboard has no firewall row: isolation's keyboard filter keeps a guest's keyboard out.
- **Policy** (`policy.*`, pure): P1's COMMAND/CONFIRM/CANCEL are withheld while the co-op menus own P1's frontends
  and P1 has control; guest COMMAND always (except while native input is authorised: pause, modal, native menu,
  town, chocobo, Quickening, a guest driving a native menu); an open co-op menu owns the directions, LB/RB and
  PARTY, except P1's directions, RB and PARTY while P1 is on the stock frontends (the co-op menus drawn but not
  owning P1, P1's own menu closed, no Party request): then a guest's menu walls only the guests' pads, and P1's
  PARTY is never also turned into a Party request; the stock leader menu stays shut through P1's UP/DOWN and every guest direction, while P1's LEFT/RIGHT stay
  native.
- **Pulses**: one semantic at a time reads high on P1's device for 65 ms, then 40 ms low. P1's pad B becomes CANCEL
  and A on a highlighted world prompt (never a foe) CONFIRM while P1's co-op menu is closed; on P1's target page the
  native TARGET INFO window is closed at most every 0.5 s. Guests: START pauses; with a native screen open one edge
  per pulse in the order CANCEL > PARTY > CONFIRM > LB > RB > directions, round-robin across guests; a queued edge is
  dropped after 250 ms or when its screen closes. The decide setting (Circle or Cross confirms) is read every
  0.25 s and swaps every seat's pad A/B for CONFIRM/CANCEL in the action layer.
- **Frontend gate** (FG-01/05/06 by id, FG-02/03/04 behind them): the stock battle menu (1) and the command-tree
  variant (4) return 0 while the co-op menus own P1's frontends, and the leader select (3, the automatic KO path too)
  while the co-op leader selector can be shown; every other frontend passes. The co-op menus own anything only while
  their presenter is live (the overlay drew them within 250 ms): without it the report counts for nothing. FG-07 (the object initializer) only counts an object created with the command-tree
  or leader-window callback: substituting the empty callback would leave a live object in the tree slot.

The menu layer's report counts only while it is fresh (posted in the current epoch within the last two beats); a
switch edge, `start()` and `stop()` drop it, so a stalled menu layer hands P1's stock battle menu and leader select
back within two beats.

Tests: `tests/test_coop_firewall.py`.

## Target candidates (`src/targets/`)

When a seat's menu waits for a target the picker offers Foes (unit types 1, 4, 7) and Neutrals (type 3) within 24
units on the ground of the seat's character, nearest first, with the game's own copy letters (else A, B, ... among
copies sharing a name); the active party in HUD order (KO members only for actions allowed on KO allies, revivals
only on KO members, stoned members only where allowed); and the reserve from the native default and menu layouts
when they agree. A remedy opens on the first member carrying a status it cures, anything else on the issuer; the
cursor follows the issuer through a reorder and keeps any other candidate by identity; LB/RB cycle the groups with
selectable candidates.

## Battle-menu requests (`src/submit/`, FIREWALL_MENUS)

Confirmed actions (manual 0x311CD0, reserve 0x311B20), Gambits toggles (0x309B40 / 0x311AF0), the summon owner's
Dismiss (0x3138D0), the combat indicator (0x291010) and menu sounds (0x249C60) are game-thread operations drained in
the UI-tick callback. Their first bytes are checked at attach; a mismatch registers nothing and refuses every
request. Each seat has one request slot: queued, then one terminal result; a request without a result by its
deadline quarantines the slot until that ticket's late result arrives, and the seat's next request is refused
meanwhile. Right before the call the operation proves the job's epoch and generation, the issuer seat's character,
handle, actor and record, its status, the target, the game's availability check (magicks and technicks) and its
admission check (0x303BD0); any difference rejects without a game call. A summon request is remembered for 20 s to
name the Esper's owner (else the leader); only the owner's menu shows Dismiss. The combat indicator is tried 75 ms
after a submit, retried every 350 ms (a starting node gets 400 ms) and given up after 30 s. Menu sounds follow each
seat's setting, one per frame.

## Co-op battle menus at run time (`src/frontend/`, FIREWALL_MENUS)

Once per co-op beat, after the session step and before guest interactions, isolation and the firewall. Each
playing seat's action edges become menu commands: guests open with BATTLE_MENU (their CONFIRM in the field stays a
world interaction), Player 1 opens with CONFIRM unless A goes to a highlighted world prompt (P1's battle menu is a
co-op menu; the firewall's gate keeps the stock battle and leader menus shut); MENU_UP/DOWN move with repeat, LB/RB
and left/right page (P1: left/right, which its LB/RB are bound to) or cycle target groups, CONFIRM confirms, CANCEL
backs out, BATTLE_MENU closes. Catalogs come from the game's tables and the character's live state; target pages
from the live world; a confirmed action, Gambits toggle or Dismiss is a request (`src/submit/`). A guest's CONFIRM
inside its open menu is reported to guest interactions so it is not also an interaction.

External frontends (map, main menu, shops and dialogs, the pause screen, the native Party screen) suspend the menus
where they were: adopted after 0.04 s, resumed 0.20 s after they close (a modal or a loss of control restarts the
wait); a load, a cutscene or a role change closes every menu instead. A seat's PARTY outside native screens is a
native Party request: the menus are suspended, Wait gives its pause back, then the firewall pulses PARTY once from
that seat; it completes when the screen opens and is cancelled by the pause screen, another screen, a modal, a load
or cutscene, and after 1.25 s. With every active party member down the stock recovery or game-over screen owns the
battle UI (all native): every co-op menu closes, the report releases P1's stock menus, no pause is requested.

Every beat the layer posts the firewall's menu report: `p1_menus` while the co-op menus own P1's battle UI (menus
subsystem live, co-op on, submit's functions checked, tables read, a valid world, P1 has a character, no recovery),
so the frontend gate stays armed; anything else posts an empty report.

Presenter guard (`presenter.*`): the overlay stamps every frame on which it drew the co-op menu layer. The layer may
own P1's battle UI, open any menu or selector (guests included) and ask for a Wait pause only while that stamp is at
most 250 ms old. Our own overlay (`src/overlay/`) stamps it; with Player 1 on the game's own frontends (the shipped
default, `set_p1_native`) only the guests' menus open, and P1's stock battle menu, leader select and KO path stay
the game's.

Character selectors: with its menu closed, a seat's UP or DOWN alone opens Player 1's Change Leader or a guest's
Change Character (the live party's regular characters that can lead, HUD order; another seat's character only while
the seat's own can lead, as an exchange). CONFIRM hands the choice to the seat-change transaction
(`party::request_change`: one game-thread job, the leader setter and the steering in the same callback). The KO
recovery: while P1's character is down (and still the native leader) the selector opens by itself with the living
characters nobody holds and cannot be dismissed; while it has one to offer the stock leader select (id 3) is refused,
otherwise (nothing to offer, not shown, no presenter) P1's battle UI is given back and the stock KO path runs.

A native Quickening chain (a party member performing a Quickening action) is reported even without the presenter:
the firewall passes P1 through, and each guest whose character is in party slot 1 or 2 presses QUICKENING for that
slot's link (COMMAND, CONFIRM pulses on P1's device; P1's own button for that link is withheld).

## Keep running in background (`src/focus/`, CORE)

FF12 pauses itself when another window takes the focus. With the Mod Menu's Keep running in background setting on
(default on; it works solo and with the co-op switch off), FP-01 (the screen state's ApplyActive, 0x7A4150) holds the
application active in the Window and Borderless states, so the focus pause never starts; exclusive fullscreen and its
transition states run stock. The state's own active byte is set only while the window has the focus, so the first loop
FP-01 does not act on while unfocused (the setting off, a stale heartbeat) starts the stock pause. From boot the
setting stays off until the player's saved settings arrive from Lua (the native defaults record never turns it on), so
a saved "Off" holds from the first loop. While the window is unfocused, player 1's XInput pad (the pad focus gate, IN-06: a zero
gamepad under a flipped packet number, so held buttons release and the real packet reads new on refocus), DirectInput
pads (FP-03) and the DirectInput mouse (FP-04) read neutral; the keyboard and the cursor delta are cleared by the game
itself. Isolation's substitution host (P1 on an SDL-only pad, or a guest driving the native menus) gets the neutral
block too, and presses made meanwhile are consumed, not replayed on refocus. FP-V1 pins the only writer of the application's active byte (another mod's rewrite skips FP-01); FP-01 never
acts unless IN-06 is hooked. The rows have no subsystem bit: they run while dry-armed, act only on the setting, and
the setting off is stock.

## Our own overlay (`src/overlay/`, RENDER)

The game presents through one wrapper, 0x6234A0(renderer), called from one place: the Present case of the
render-command dispatcher. That call is OV-01 (a call row, OPTIONAL; OV-V1..OV-V3 gate it: the wrapper's prologue,
its Present call through the swap chain at renderer +0x118, and the D3D11CreateDeviceAndSwapChain arguments that put
device, immediate context and swap chain at +0x108/+0x110/+0x118). The handler runs on the render-ring consumer
thread that owns the immediate context, draws into the back buffer with our own objects, and runs the wrapper
(RUN_ORIGINAL). No DXGI vtable patch, no dummy device, no window of our own; no new import.

GPU side (`gpu.*`): one vertex and one pixel shader (shader model 4.0, precompiled by `tools/gen_overlay_shaders.py`
with System32's d3dcompiler_47.dll into `shaders.inc`; build.py refuses a stale one; the pixel shader picks each
quad's mode by floor(mode), the glass modes carrying their sheen and rim bias in the fraction), one dynamic vertex
buffer of 6,144 quads (`QUAD_CAP`), the font atlas (R8, immutable), a linear clamp sampler, premultiplied-alpha blend, no cull, no scissor,
depth off; created once per device, a failure final for that device. Per frame only the back buffer's render target
view is made and released (the swap chain's buffer is never held). Every state the draw binds is saved first and put
back after (input layout, topology, vertex buffer 0, VS/PS with class instances, GS/HS/DS nulled, PS resource and
sampler 0, rasterizer, viewports, scissors, blend with factor and mask, depth-stencil with reference, every render
target and the depth view; UAVs kept). Checks: the swap chain's device is the renderer's and the context is its
immediate one; the device is not removed; the back buffer is at least 640x360 in R8G8B8A8/B8G8R8A8 (UNORM or
sRGB), R10G10B10A2 or R16G16B16A16_FLOAT. Anything else draws nothing.

Space and look (`batch.*`, `font.*`, `theme.*`, `marks.*`): the HUD's 1920x1080 authoring units, one uniform scale,
centred. Every colour, size, window shape, tab, gauge and mark comes from the active HUD theme (Phase 8): three
built-ins compiled in from `src/overlay/themes/<id>/` (legacy-bold "Bold Modern", the default; legacy-humanist; legacy-
crisp; copied and pinned by `tools/gen_overlay_themes.py`, checked by build.py), and player themes read from
`x64/scripts/config/IvaliceLegacy/themes/<id>/` with the same strict rules (`theme.h`; players: `docs/THEMES-README.md`).
Text is UTF-8 from the theme's signed-distance-field atlas (body, title and numerals faces, kerning with accents
falling back to their base letter, small-size tracking, HUD text spaced clear of its outline); window glass computed per
pixel (rounded or chamfered, a 2..4-stop gradient as stacked bands, rim dimming toward the bottom, the diagonal sheen),
the outer keyline, inner line and halo as extra quads; slanted, rounded or ticked gauges with a hatched trail; the
glove, chevrons, seat diamond, dot and status icons as layered SDF recipes. The setting is `hud_theme` (Mod Menu: HUD >
HUD theme), the list `GET_THEMES`.

What it draws (`scene.*`): the test card while Developer options and the Diagnostics switch (Mod Menu: "Overlay
test card", settings `hud_test_draw`) are on; every seat's command window, target page and character selector from
the published menu view, one uniform window per seat in a fixed slot along the bottom (`scene::slot_layout`: 275 x 230
units from x 24, 16 apart, bottoms at y 1056, P1 leftmost; a closed menu leaves its slot empty). The window moves
towards the vanilla look, still procedural: blue gradient glass with a light rim, a tab "P2 - Fran" with the seat
number in the seat colour under a seat-colour accent, white outlined rows (greyed dimmed, Gambits ON in pale gold),
the white glove outlined in the seat colour, the help line in a footer. The slots end 12 units left of the stock party
list (209 left of the reduced one) and stay clear of Target Info and the log zone.

Player 1's window is ours only while P1 is not on the game's own frontends (`frontend::p1_native`). The frontend arms
P1 (`rules.h` `p1_arm_step`) once `overlay::live` (overlay_live, below) has held for 250 ms (`overlay::live_track`:
gaps of at most 250 ms do not break the streak), never under an open stock frontend or the pause screen; it disarms
when overlay_live has been false for more than 250 ms, the presenter is not live, a frame was refused within 250 ms
(`presenter::note_failed`), the layer idles or P1's window is left out (then P1 stays on vanilla for 5 s). Missed
frames alone never disarm (0.1.59's two-missed-frames rule brought the vanilla HUD back on the D-pad focus panel). Disarmed,
P1's co-op menu closes and the game's battle menu, leader select and KO path are P1's; a P1 window in the view is then
reported as left out.

Visibility: the UI tick (`overlay::note_ui_tick`, primary) refreshes the HUD heartbeat while the HUD window
[0x209be80] + 0x8fa0 (handler 0x2be490) is drawn with its own alpha above 0, the UI is ready and drawn, the field
gate is open (no frontend, or only P1's own battle frontends 1/3/4) and no pause pointer is set; HD-01 (the HUD
tree's log draw, `overlay::note_hud`) is a secondary source under the same field gate. The overlay draws only while
that heartbeat is at most 100 ms old and the field gate read in the frame hook itself is open, so it leaves with the
HUD the very frame a menu, the map, a cutscene or a fade starts. `overlay::live` (overlay_live) is co-op on, RENDER
live, an overlay frame presented within 250 ms, a heartbeat that fresh and the field gate open. The old replay path of the
HUD canvas below stays in the source and off (`driver::set_layer_enabled(false)`).

Presenter: on every frame with a fresh heartbeat, co-op on, FIREWALL_MENUS live, a fresh menu view and HUD snapshot
of this epoch and a ready GPU side (drawn, or nothing to draw), the handler calls `presenter::note_dropped(mask)`
(Player 1's window while P1 is native, and any window the batch had no room for), `note_drawn(now)` and
`note_run(now)`. Otherwise nothing is stamped, the run breaks and the menus idle 250 ms later.

Failure: the frame's work runs inside `guard::call`; a fault puts the saved state back (guarded again), latches
RENDER faulted and withdraws the stamp. The render thread never logs; `overlay::step` (co-op thread) writes each
recorded event to hook.log once (first frame with the back buffer's size and format, a creation failure with the
object and HRESULT, a mismatch, a bad back buffer, a fault) and, with Developer options on, a report every 30 s
(hooked, device, back buffer, frames drawn/empty/hidden/idle/busy/refused/faults, cost, frame time, heartbeat age,
stamps, guest windows).

`tests/test_coop_overlay.py`: the font (atlas digest, glyph boxes, measure and fit), the batch (primitives, fade,
capacity, the authoring space at 16:9 / 16:10 / 21:9, vertex expansion), the scene (the card inside the screen, the
seats' windows from a view: P1 drawn only when ours, every window in its own slot), the slots against the party list
in both modes, Target Info and the log, Player 1's arming rule and its give-back, the GPU side on a WARP device
(pixels drawn where the card is, the background untouched, every pipeline state put back, the fault drill's restore,
the mismatch, a small back buffer, an empty batch), OV-01 (heartbeat, presenter stamps and their refusals, the fault
latch, the log lines) and the frontend with Player 1 on the game's own frontends.

### The HUD elements on the overlay (`hud_scene.*`, `hide.*`, `feed.*`, `states.*`)

- `hud_scene`: the party list (bottom-right, from x 1290: right of the four seat slots in every party size), Target
  Info (top-left) and the battle log (top-right), composed into the quad batch after the seat windows, each within
  its own budget (900 / 800 / 1100 quads; `QUAD_CAP` 6144) and rolled back whole when it
  does not fit. Every face, size, colour and icon comes from the active HUD theme's tokens (`theme.h`). The party row's action line
  names the action only, never its target (the target lines show who targets what).
- `feed`: the co-op thread's record of what the snapshot lacks: names, MP and MP max (PH-V1), the action gauge as the
  native row fills it (PH-V2..PH-V4), the damage ghost, the chain. (The top-centre Wait line and its holders are gone:
  Marco, 2026-11-16, "not needed"; the Wait rule itself is unchanged.)
- `hide`: the hard blocker of the vanilla battle HUD. While overlay_live holds (and for 250 ms after, the fail-safe's
  hysteresis) every piece is hidden by the window draw bit: the party panel (not under P1's vanilla leader select or an
  Esper), the two help-line slots (while the field gate is open and P1's menu is ours), the TARGET INFO window (with
  Hunter's Insight's drops), both log windows (the first with its backing quad and the log manager) and the chain
  counter (a seam: its window is not located yet, so it is never touched). A piece follows its element's switch.
  Pieces are given back by the rule `handler unchanged and (flags & 0x402) == 2` once overlay_live has been false for
  over 250 ms, in the stale handler, after a CORE fault and at exit; gated by HH-V1..HH-V7. HD-01 also skips the stock
  log text whenever the overlay is live (`overlay::log_replaces_native`).
- `states`: the Legacy script states (PUSH_HUD_STATES, op 10): validated by the bridge, resolved from battle-record
  addresses to handles each beat, drawn while at most 1.5 s old. Producer: `family_identities.hudStates` read by
  `coop/hud_states.lua` every 250 ms from a timer.

## HUD canvas (`src/hud/`, RENDER)

The co-op HUD is drawn by the game itself. Once per frame the field/battle HUD tree sends the combat-log manager its
draw message (message 4 of its handler, inside the window draw that runs messages 3/4/5 only while the window and
every ancestor are visible). Its call to the log draw is the frame hook HD-01, a call row: the log draw runs as the
game's own call, then the after-handler replays the latest canvas frame on the game thread. The replay uses the draw
context of the manager's top-most ancestor (a window's context stays set while its children draw), so the canvas is
in the HUD tree's root space and neither slides nor fades with the log window, and it calls only the game's own
functions on a private copy of that context: text through the text parameter init and the text draw (the combat
log's own pair: font, outline, colour, size), sprites through the batch begin / batched sprite / batch end of the MRP
node draw (atlas by registry name, page.sheet and cell through the TIM2 eXt tables, CLUT and vertex tint, at most 12
sprites per batch as the node draw does), and a thin bar through the list selection bar (cap and stretch, any RGBA).

The co-op thread builds a frame (`hud::canvas`: up to 192 commands, 3 KB of text in the game's encoding; a character
the font table lacks becomes '?') and publishes it in one seqlock slot; the hook copies it out (a torn copy is
skipped, never waited for). Nothing is drawn, and the log draw alone runs, unless RENDER is live, a frame of this
epoch is at most 250 ms old, the context is the one the game handed the call and the HUD is not faded out, the UI is
ready, every native function's first bytes match (checked at attach, and by the GATE rows HD-V1..HD-V8 on HD-01),
the guard is installed and the frame fits the budget. HD-01 is OPTIONAL: another mod that rewrote the call (Insurgent's
Vision's No Combat Log) skips it instead of refusing the install; then nothing is drawn.

Budget. Text costs heap and CPU only; sprites and bars allocate commands in the game's 32 MiB render ring, which
wraps without failing. The hook measures the ring traffic between two frames and keeps a decaying peak; a frame is
replayed only while its own estimate stays under 512 KiB and peak plus estimate under 8 MiB (a quarter of the ring),
otherwise not at all. Fault guard (`runtime/guard.*`): the whole replay runs inside `guard::call`; a fault in our code
or in a game function it called resumes at the guard with every non-volatile register restored, the RENDER subsystem
is latched faulted and the hook is stock from the next frame. The work the faulting call left half-done in that
frame's draw batch is not undone.

The only producer in this build is the Diagnostics "HUD test draw" switch (off by default; the native defaults record
never turns it on): four lines in the seats' colours, a status icon and a bar, while a valid world exists. The
co-op layer itself is composed on the game thread by the frame driver (next section).

## HUD frame driver, presenter stamp and HudSnapshot (`src/hud/driver.*`, `snapshot.*`)

**HudSnapshot.** Each beat, after the co-op menus, the co-op thread samples the game into one plain struct and
publishes it through a seqlock: the party rows in the HUD's row order (party id, the seat holding it, HP and max HP,
both status masks, the remaining ticks of each timed status, level, weakness mask, actor handle and current target),
the leader, the targets (each seat's menu target first, then the party members' targets; deduplicated, foes read the
same way, the Anti-Libra augment bit raw), each seat's menu target with the time it was aimed, whether a party member
has Libra, the status tick scale and the layout mode (couch; online is an enum value nothing selects yet). Records
are read bracketed (masks and HP before and after); one that moves is read again and otherwise left out, never mixed,
and the party header (HUD layout and leader) follows the same rule. It is withdrawn without a world, with co-op off
or with RENDER not live.

**Frame driver.** When the frame hook runs, the driver decides whether the co-op layer is drawn this frame (a snapshot
of this epoch at most 250 ms old, co-op on, the game's UI shown) and then runs the element passes in a fixed order
into one canvas inside the draw guard: party extras, Target Info, battle log, and the menus last so they draw over
everything else (the menu pass only while FIREWALL_MENUS is live). The passes read only the snapshot. In this build
they are stubs that draw nothing. The Diagnostics test frame, if on, goes underneath.

**Presenter stamp.** The driver calls `presenter::note_drawn(now)` on every frame where the menu layer was presented:
the hook ran with a live draw context, every pass finished with the menu pass last, and the frame was drawn to its end
or had nothing to draw. That includes frames with no menu open, which is what lets a menu open at all. Nothing is
stamped when the hook is not reached, the UI is hidden, RENDER or FIREWALL_MENUS is not live, co-op is off, the
snapshot is missing or stale, the context is faded, or the frame is over budget. P1 then gets the stock menus back
once the stamp is older than 250 ms. A fault in a pass or in the replay latches RENDER and withdraws the stamp at once,
so the menus idle on the next beat.

**Suspended menus.** The main menu, the map and the pause screen stop the field HUD, so the presenter dies 250 ms
into them. The co-op menus suspended for such a screen (or with one up that may still be adopted) are held, not
closed. They keep their place, own nothing (no P1 frontend, no input, no Wait) and resume where they were once the
screen has closed and the layer draws again. Presenter death outside a suspension, a load or cutscene during the hold,
a party-down recovery, a Party request in flight, or a resume with nothing drawn close every menu as before.

## Co-op battle menus in the native window style (`src/hud/menus_draw*`, RENDER)

The frame driver's menu pass (the last pass) draws every co-op battle menu, target page and character selector.
Player 1's battle menu is a co-op menu, so it is drawn where the stock command window sits: rows bottom-anchored on
the field layout's list origin (group 14: 122, 1002), the character's name, LEADER and the leader flag at their
group 14/15 places, the help line on the group 11 strip (534, 924, 360, 36; the game's own top-left help line is
group 7, not this strip) and, on a target page, the group 30
footer's shoulder-button icons. A guest's window is the same battle glass (bt_win_0_c, nine pieces) with a thin
seat-colour edge on its left and its header in the seat colour; it slides out of the guest's own party row to the
left (150 ms, the native open table's time and offset) and a seat-colour line links it to that row. Guests' windows
share one column and are stacked in row order, so no window overlaps another, Player 1's window, help strip or
footer, the party list, Target Info or the expanded battle log. Every window shows the cursor hand, the selection bar
in the seat colour, greyed entries, MP costs and item counts, the Gambits ON/OFF plate, scroll arrows and the help
line; target pages list the candidates with the game's A..Z letters.

The co-op thread turns the menus into a view after each beat (names resolved from the game's own text through the
fault-safe reader: action and character names through the name directory, foe names through the unit's name text)
and publishes it through a seqlock. The pass copies it out and draws it as a pure function of the view, the
HudSnapshot (rows, seats, leader) and the frame time. It fits what the earlier passes left of the frame (at most 96
commands and 1,400 text bytes): fewer rows first, then the row links, the help glass and help lines, then whole
windows, Player 1's last. No view, a view of another epoch or older than 250 ms, or a withdrawn view draws no menu.

## Party list rows (`src/hud/party_rows.*`, RENDER)

The frame driver's party-extras pass adds to each native party row, anchored to the row the game lays out (row k of
n at G40.y - gap * (n - 1 - k), stock or reduced, mapped into the canvas with one scale): a thin edge in the row's
colour and the seat numeral (1-4, G for a gambit ally, struck through with its gambits off), a damage ghost on the HP
gauge (held 1.2 s, drained in 0.4 s), up to two timed statuses with the time left (the native icons), "Choosing..."
while the row's seat has its battle menu open, the target's name after the native action gauge when an action aims
at another party member, and each seat's party-target hand on the row its target page points at. A layout the
relayout does not recognise draws nothing. The co-op thread samples what the HudSnapshot does not carry (gambits,
the action performed, the seat choosing, the colours, the layout) into a small seqlock record; the pass reads only
that record and the snapshot.

The low-HP glow is the game's own HP number in red, pulsing: two optional call rows inside the party row update.
HD-02 (the HP digits call) forces the digits to be set again on a low-HP row (the HP cache set to -1 around the
call and put back after it) and HD-03 (the digit setter call) replaces the colour argument for that row only. A row
that recovers is set once more with the native colour. Legacy's number-colour script changes only the battle
pop-ups' type table and reads the palette; it shares no address with these rows. Gates: HD-V9/HD-V11 (the HP
digits function and the call's arguments) on HD-02, HD-V10 (the digit setter) on HD-03.

## Native HUD relayout and toggles (`src/hud/relayout.*`, `circles.*`, `native.*`, RENDER)

Two changes to the stock HUD, made while RENDER may act and the element is on, and given back otherwise:

* **Reduced party list.** The party rows, their leader band and glow, the per-member ACTION bars and the party
  selection panel are laid out by records of the field MRP (the live pack section at slot 1 of the MRP table). A
  game-thread job reads those records through the fault-safe probe and fingerprints them (SHA-256 of every rect, gap
  and text scale of groups 40-51). Only the stock layout, unchanged for 1.25 s in the same allocation, is rewritten
  to the reviewed reduced layout (75 % about a bottom-right anchor, action bar at fixed reduced values, digits 85 %,
  selection panel moved right); the computed values must hash to the reduced fingerprint before anything is written,
  every write checks the bytes it replaces, and the result is read back. Any other layout (another mod's edit, or the
  reduced layout this module did not write) is left alone and reported in the status view. Windows pick the records
  up when the game creates them (party rows on a HUD rebuild), as with stock values.
* **Target circles off.** The circle-list draw tests one byte per node at 0x28c5a0; the module changes that test's
  immediate from 1 to 0 with one compare-exchange (no instruction boundary moves). Insurgent's Vision's own patch
  there (`cmp eax,eax` + nop) is adopted without a write and never undone by us. The AoE aiming ring is a different
  list and draw and stays.

Give-back: the UI tick restores both on the frame they stop being wanted (kill switch, the RENDER switch, the element
switch, DRY), writing the saved stock bytes only into the same allocation while it still holds exactly our layout
(and the stock byte only over our own byte). The exit path does the same once the module is disarmed.

`test_coop_hud_native.py` runs the layout classes on the stock layout when `ILC_MRP_PACK` names an extracted,
unmodified `mrppack_ys.bin`; without it they are skipped (the circle and owner tests still run).

## Expanded Target Info (`src/hud/target_info*`, RENDER)

The frame driver's Target Info pass draws one target at the top-left in the native style (outlined text on the
world, no panel), its "TARGET INFO" header on the same line as the battle log's (`target_info::corner`): level and
name (red ten levels above the party), the HP bar with a damage ghost, the HP numbers and percentage, Weak element
icons (battle_4_c 10.0), the statuses on the target with their seconds and, greyed, the statuses its status block
mask refuses (record +0x38), the drops and a Libra badge or the native red X. The target is the one the last player
who targeted aimed at (the snapshot's seat targets and their `since_ms`), edged in that seat's colour. Libra gating
follows the native rule: a party target reveals; a foe reveals with an active Libra unless it has Anti-Libra and
the native test (CD-01) is stock. Numbers, percentage, weaknesses and immune statuses need it; name, level, bar,
statuses and drops do not. Drops follow Hunter's Insight: an item is named once obtained from that bestiary entry
(HI's own save block, found through its code at CD-02), else "???"; taken steals are dimmed; without HI there is
no drops list. Item names come from the game's loaded name text (section 61 and the item tables, relocated by the
battle-pack loader). The co-op thread composes the view once per beat (`target_info_sample.cpp`); the pass only
copies it and draws. Family/area, BOSS and Half/Null have seams but no data source yet.

While ours is live (element on, co-op on, the co-op menus owning P1 through the firewall's frontend gate, the pass
and the menu layer presented within 250 ms), TI-01 (the native
window's only open call, 0x2BE20E, OPTIONAL) turns each open request into the set routine's own close request
(r8d = 0, r9d = -1), so the native window and Hunter's Insight's block never open; otherwise it is stock and
`target_info_scale.lua` scales the native window as before. While P1 is on the stock battle menu (KO promotion, P1 down with nobody
free, the hand-back latch) the gate is clear, so the stock target list keeps its native window, and nothing re-hides it.

The native window given back (`target_info_native.*`, UI tick, data writes only): the game asks for that window
only while one of its target cursors holds focus, and the field target mode (the Target button) is the one that
outlives the co-op menus. When ours has not been live for 500 ms while the target mode holds focus on a target that
resolves (registry 0, generation checked, an actor with a battle record), and no native window is open or pending,
the owner's id (+0x578) and pending-create bit (+0x57E bit 0) are written: the HUD window's next update creates the
window itself (its own deferred create). It is withdrawn if ours comes back, the mode loses focus or aims elsewhere,
or after 10 ticks. Once ours has been live for 250 ms, a native window still open under it gets its invalid bit
(+0x280 bit 2) and the game closes it on its next update. All of it only while TI-01 is attached and the nine
verify-only rows TI-V1..TI-V9 (INFO in the patch layer) hold their pinned bytes, co-op is on (no F8) and RENDER is
live; otherwise nothing is written and the native window returns at the next target change, as before (a create
already requested is left for the game to finish; the timers start over when the gate opens). While the target mode holds focus (and P1's menu is not on a target page),
the snapshot uses its validated target as P1's seat target, so our Target Info follows the foe P1 aims at; when the
mode ends P1's normal target comes back.

`test_coop_hud_target_info.py` checks the item names against a real `battle_pack.bin` when `ILC_BATTLE_PACK` is set.

## Battle log (`src/hud/battle_log*`, RENDER)

The frame driver's log pass draws the battle log at the top-right in the native style (outlined text on the world,
no boxes), its "BATTLE LOG" header on Target Info's line (`target_info::corner`). Each line starts with the target
line's glow dot (targetline_p page 0 sheet 4 cell 1, white palette, tinted): the actor's seat colour, silver for a
gambit ally, copper for a foe, green for a kill or reward. Up to five lines, newest at the bottom; a line holds 6 s
after its last change and fades over 1 s, the two oldest positions dimmer; important lines (readies, KO, stone,
doom, X-Zone, back attack, shields) linger 12 s and are kept over ordinary ones. Expanded: the last twenty, no fade.
Filters (All, Party, Foes, Statuses, Mine) apply to both. Repeats merge ("attacks Hyena C x2  412, 726"), an area
action lists its other victims ("+1"), damage taken is red on the victim's line, healing green, tags CRIT, WEAK,
MISS, BLOCK, PARRY, IMMUNE and HOLDING (Lure) follow the amounts, status icons (partytop_4_c 3.0) mark a status
landing or wearing off, and a green line closes each kill with its EXP, LP, the chain counter, and the loot and gil the
feed reports ("Hyena A defeated  120 EXP 2 LP  Chain 12 Lv 2  Wolf Pelt").

Three sources feed one pure model (`battle_log_model.cpp`). LG-01, a copy-only detour at the battle message emitter's
entry (0x46AB10, OPTIONAL), which copies the message id, the producer context and the 0x30-byte argument block into
a bounded ring (256 entries, sequence numbers, lock-free for several producers) and always runs the original; and
HP/status diffs between two samples of the party rows, the snapshot's targets and the actors the feed named,
attributed to the caster of a pending hit on the victim, else to the actor whose target is the victim and whose
latest action event is the most recent (else its current action, Attack when unknown). And the per-hit feed, five
more copy-only OPTIONAL detours (each with verify-only gates on the code around it) writing into the same ring:
HR-02 in the hit function 0x307300 at 0x307601 (caster, target, action, the outcome byte for MISS, BLOCK, PARRY and
IMMUNE, the flag nibble for CRIT, the action row's element, or the weapon's for Attack, against the target's
affinities for WEAK); HR-01 at its entry and HR-05 at its common exit bracket one hit on the thread that applies it,
and HR-03 at the number pop-up 0x3283D0 copies an amount only from that thread while its bracket is open, so exact
numbers belong to their hit and the diff they already showed is absorbed. The Forge shows its numbers from its Lua
thread while the game thread waits inside the bracket: those are never copied, and the hit's diff takes its caster
and tags. KR-01 at the kill pop-up 0x28FB80 copies the victim, EXP and LP; the chain count and level are read from
their globals (0x22C3158) after each drain, while the chain reset (CH-V1) and the counter's display rule (CH-V2) are
stock. The call at 0x30760A that Hunter's Insight rewrites lies outside every row.
The co-op thread drains the ring, assembles each hit, resolves actors (battle record +0x05 side, +0x04 party id; units through the
actor manager) and names (the menus' name cache, the Target Info's item names), runs the model and publishes the
view; the pass copies it and draws within its own budget (44 commands, room left for the menus).

While the log is live (element on, a view of this epoch at most 250 ms old), the frame hook HD-01 draws the co-op
layer at the log draw's call and skips the native log draw (RESUME at the call's return, 0x2BE7DF); if the layer
cannot be drawn that frame, or the log is off, the native log draws as before. The settings (element, expand
button, filter, seat) come from the Battle HUD page (`battle_hud.h`); the expand button is any seat's press of the
bound pad control and toggles the expanded view.

## Battle HUD settings (`src/hud/battle_hud.*`)

The Mod Menu's Battle HUD page writes `hud_enabled`, `hud_elements` (one `ilc_hud_element_bit` per element),
`log_expand_control`, `log_filter` and `log_filter_seat`; `configure_from_settings` turns them into each element's own
switch, so an element that is off runs exactly as with its switch off: the reduced party list and target circles off
(`native::set_parts`, given back on the next UI tick), the party rows' edge and numeral, low-HP glow, damage ghost and
action line (`party_rows::set_parts`), Target Info, the battle log, seat lines, seat diamonds and the AoE-only ring
rule. Battle HUD off turns every element off; the co-op battle menus stay (guests act through them). The Diagnostics
test draw keeps its own field.

Identify (`ilc_action` IDENTIFY) flashes the seat's diamond: guests have Back and T by default (no other guest action
uses them, and guest input never reaches the game), Player 1 has none (every button of P1's pad also reaches the
game) and may bind one. The co-op thread reads the presses through its own edge cursor.

The page's status line comes from `ilc_status.hud_report`: the frame hook's call bytes at 0x2BE7DA classified every
second (stock, our island, a five-byte NOP run = Insurgent's Vision "No Combat Log", foreign), whether HD-01 is
hooked, a party layout another mod changed, and the target-circle site's class. Vision's own settings file is read
(never written) by the page for its minimap, overhead and combat-log toggles.

## Couch layout (`src/hud/layout.*`)

One table for the corners (1920x1080 authoring units): Target Info top-left and the battle log top-right on one
header line, Player 1's menu bottom-left, the party list bottom-right, guest menus sliding out of their rows. While
the native boss gauge shows (the snapshot's `SNAP_BOSS_GAUGE`, read from 0x2B62D88 +0xC0 -> +0xC8/+0xD0 -> +0x130 bit
0x10), both headers move down 54 units together; the expanded log keeps its zone's bottom and drops its oldest lines
(18 instead of 20). Text is never drawn below its authored scale. "Choosing..." (the party rows' action line) is shown
only for Player 1 and only while no guest window is open: it sits where guest windows slide out. The online mode is
an enum value that returns the same table (a seam).

## Diagnostics (`src/runtime/diag.*`)

For the Mod Menu's developer options and test sessions.

- **Counters.** `GET_COUNTERS` returns `ilc_counters`: a header, `layout` (`COUNTERS_LAYOUT`, bumped whenever a field is
  added, removed or moved) and about 140 named 32-bit values: thread ids, the mode and when it went live, liveness
  (armed, epoch, age, fresh), stale fallbacks (detour entries sent to stock by a stale or torn sample) and stale UI
  ticks, the subsystem masks (applied, in the gates, faulted, live), co-op thread beats and gaps, the stall counters,
  UI-tick and bridge timing maxima (microseconds), the hook transaction, the detours, the game-thread queue, the
  co-op switches and the on/off key capture hold, the session, party and towns, the HUD canvas and frame driver, the
  presenter, the Target Info give-back and its pins, the co-op menus, input isolation and the background setting.
  The field names are in the schema, so a script can print `name=value` for every field without knowing offsets.
  Counters are read without a lock: each field is untorn (one aligned load or an untorn copy), but fields are not one
  consistent snapshot and can disagree with each other (for example the canvas budget or the menus' state against a
  counter). Counters wrap at 32 bits. The op changes nothing and answers whatever the developer setting and the
  module state are.
- **Timing.** The UI tick measures its own run (the stock call excluded) in time-stamp counter ticks, with no import,
  and the counters op converts them with a calibration against the performance counter taken since `FF12Hinit`
  (0 until 50 ms have passed). The co-op thread and the bridge use the performance counter.
- **Stall test.** `STALL_COOP_THREAD` (seat 0, `arg0` 1..`STALL_MAX_MS` ms) is accepted only when the applied
  settings have `developer = 1` (`CONFLICT` otherwise, field `developer`), and answers `NOT_READY` while no co-op
  thread runs and `BUSY` while another stall is posted or running. The co-op thread takes it at the start of its next
  beat and waits that long on its stop event (a shutdown ends it at once). Nothing heartbeats meanwhile: once the last
  heartbeat is older than 2 s every detour runs stock (each such entry counts as a stale fallback) and the UI tick
  runs its stale clean-up (a Wait pause it owns, the reduced party list and the circles are given back). Nothing is
  latched: the Status keeps its state and reason, and the first beat after the sleep heartbeats again, so the detours
  act again from the next frame. The thread then reports `COMMAND_RESULT` (`a` = the command, `b` = milliseconds
  slept). A stall of 2 s or less never goes stale.

## Player options and hook.log (`src/runtime/options.*`, `src/runtime/log.*`)

The player options the subsystems read on any thread are copied out of each applied record into one 64-bit word
(`options::configure_from_settings`, on the boot thread and then on the co-op thread when the applied generation
changes): `shared_camera` (via the arm mask: CAMERA stays clear), `guest_interactions` (interaction: no ROUTING flag
and no request from a guest's CONFIRM; gateways and loot stay), `town_view` (towns: PARTY allows every member, so the
filter hides nobody and the planner only shows), `wait_pause` (the Wait rule: ALL or ANY participating seat in a menu),
`guest_pause` (the firewall drops a guest's PAUSE pulse, the frontend a guest's PARTY request; P1 is never affected),
and the battle log's `log_lines_compact`, `log_lines_expanded` and `log_fade_s` (through the battle log's settings
into its view; important lines hold twice the fade time). The HUD elements `PARTY_TIMERS` and `GUEST_CURSORS` are bits
of `hud_elements` like the others.

Diagnostic lines go to the loader's `hook.log`. The loader's logger, `simpleLog.dll` (`void _log(uint32_t level,
const char* text)`, level 0 info, 1 warning, 2 error; no format expansion), is looked up among the modules already
loaded and its export taken by name, so the import table is unchanged; a process without it (the host tests) drops the
lines, counted. `log::post` never blocks or allocates (a fixed ring under a try-lock); only the boot threads call
`log::drain` (FF12Hinit, the init thread after start-up, the co-op thread once per beat), never a detour or the game
thread. Normal level: the start-up result (state, reason, detail), what could not attach, refused settings records,
latched subsystem faults and the Battle HUD report while live. `log_level` 1 adds every applied record (its arm mask
and options), the co-op on/off switch and seat toggles.

## Wait engine (`src/wait/`, WAIT)

In the game's Wait battle mode time stops only while every participating seat's co-op menu is open (a participant:
in session, manual, a living character whose menu may open; benched, parked and down seats never count); Active
mode never pauses for co-op menus. The frontend layer publishes the request every beat. The engine runs first in
the UI-tick callback, before the job drain, and holds the game's own pause reason 0 the way stock Wait does for
P1's battle menu: acquire 0x226BE0(0, 0, 0, 0, -1, -1) + aggregate 0x226280; release 0x225CD0(0, -1) + the Wait
lock clear 0x182DA0 + aggregate. The reason has no reference count, so the engine borrows a reason someone else
holds, drops ownership when stock released it (and takes it again if still wanted), hands it over to a stock
reason-0 frontend instead of releasing, and releases as soon as a stock frontend with another reason is up. A
request that is not fresh (another epoch, older than 100 ms), the WAIT bit off (kill switch), Active mode or a
non-play game mode releases what it owns within one UI tick. The five functions' first bytes are pinned (verify-only
rows WT-V1..V5 and a check at attach); a mismatch disables the engine. The kill switch's ordered disarm waits for
the engine to give its pause back. A co-op thread stalled past the liveness window (the UI-tick handler no longer runs)
is covered by the UI-tick row's stale handler: it gives back (or hands over) a pause the engine owns, and nothing else.

## Loopback codec and soak (host-test build only)

The session core consumes and produces three message families: seat frames (input frames, action frames and the
input service's control view), seat commands and their results, and the views the HUD passes draw from. The
host-test DLL is built with `ILC_LOOPBACK_CODEC` and `testsupport/loopback.cpp`: it can run every record of those
families through an encoder and a decoder where it is handed over (`src/runtime/loopback.h`: the input service's
tick, `post_command`, `drain_events`, and the publishes of the HUD snapshot, menus, party rows, Target Info and
battle log). The wire carries a type's value bytes only and the decoder poisons every padding byte, so a consumer
that read anything beside the record would behave differently. The shipped DLL is built without the define: every
hook expands to nothing or to a plain alias, the image differs only in its source id, and it has no codec switch.

`tests/test_coop_loopback.py` replays one hour of a scripted input stream and one hour of HUD frames with the codec
off and on and requires identical digests (and a different one for a deliberately lossy codec), and soaks eight
simulated hours of everything together (seats joining and leaving, kill switch, park, captures, battle-log traffic,
game-thread jobs, 3 s liveness stalls, settings regenerations), checking that every queue and ring stays bounded.
