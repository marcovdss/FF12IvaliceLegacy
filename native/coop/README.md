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
| 2 `SET_SETTINGS` | `ilc_settings` (3,492 bytes) | ack: STAGED or DUPLICATE |
| 3 `COMMAND` | `ilc_command` | nothing. Seat commands (park, seat move, pairing, capture) are queued for the input service (0) and answer with an event; `NOT_READY` while the service is stopped, `BUSY` when its queue is full. `RESTORE_DEFAULTS` and `KILL_SWITCH` answer `NOT_READY` until their subsystems exist |
| 4 `GET_STATUS` | nothing | state, generations, digests, event and reject counters, last reject, applied subsystem mask |
| 5 `GET_SEATS` | nothing | the configured seat view (no session yet) |
| 6 `GET_DEVICES` | nothing | the input service's inventory: `READY`, session, age and one entry per connected pad; `NOT_READY` and no entries while the service is stopped. `OUT_TOO_SMALL` if `outCap` < 32 + count × 148 |
| 7 `POP_EVENTS` | nothing | as many queued events as fit in `outCap` |
| 8 `GET_SETTINGS` | nothing | the applied settings (generation 0 = native defaults) |
| 0x7F01 `TEST_SAFE_POINT` | nothing | debug: runs the settings safe point; refused once the module's tick owns it |

Settings rules: every field is validated as untrusted (ranges, zero reserved bytes, UTF-8 names, one pad and one
character per seat, P1 always keeps an input). `subsystems_armed` is a bit per future subsystem (input isolation,
movement, party, camera, firewall and menus, Wait, render, targets and cards), all armed by default; each
subsystem must check its bit at its safe point and stay stock while it is clear. A record lands in a staging slot
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
```

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
  only, no hook). Menu directions repeat after 0.250 s, then every 0.100 s (fast: 0.100 s, then 0.035 s).
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
  closed and refused again in the dispatcher, so every detour runs stock. Only the CORE UI tick (UT-01) runs, for
  the settings safe point; in DRY it runs no game-thread job. `MODE_LIVE` lets the other gates open.
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
keyboard back if the co-op thread stalls. Pads keep working unfocused and focus loss does not pause the game. If
the bytes before the focus gate are not stock, that one hook is skipped at install and reported.

## Game-thread queue and UI tick (`src/runtime/game_thread.*`, `src/runtime/tick.*`)

Game functions run only on the game thread, inside the UT-01 tick. Producers on any thread `push` a job (an
operation number from a table sealed at boot, never a pointer; issuer, epoch, generation, ttl, four arguments)
into a bounded lock-free ring (64 jobs; `PUSH_FULL` instead of waiting). Each frame the tick runs the settings
safe point, hands a newly applied `subsystems_armed` mask to the arm gates (try-lock, retried next frame), then
drains up to 16 jobs. Right before each job it re-samples liveness and the session generation: the job runs only
if its subsystem may act, its epoch and generation are current and it is younger than its ttl (at most 2 s);
otherwise it is rejected with the reason and never runs late. Every ticket gets exactly one result in a
128-entry completion table. In DRY mode queued jobs are rejected as `DISARMED` without running. Re-entry runs
stock. The stock tick call always follows exactly once.

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
* **Seat table:** `ILC_SEATS` entries, filled only below `ILC_ACTIVE_SEATS_MAX` (3). Per seat: character, party
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
