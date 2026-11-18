// Ivalice Legacy co-op module: the Lua <-> native bridge ABI, version 1.
//
// THIS FILE IS THE SINGLE SOURCE OF TRUTH for the wire format. Every constant, enum, record layout and
// per-field range below is written once, as an X-macro table. The module expands the tables into C++
// structs and range checks; tools/bridge_schema.cpp expands the same tables into schema/bridge-abi-v1.json,
// which the Lua side and the tests read. build.py regenerates the JSON and fails on any difference, and the
// module reports the JSON's hash in HELLO, so the two sides cannot drift apart silently.
//
// Entry point (Microsoft x64, called synchronously on the calling Lua script thread by memory.execute):
//
//   int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t inLen, void* out, uint64_t outCap);
//
//   slots: RCX = op, RDX = in, R8 = inLen, R9 = out, [rsp+28h] = outCap. memory.execute always passes
//   8 slots; slots 5..7 are ignored.
//
// Arguments: op, inLen and outCap must fit in 32 bits. memory.execute passes the full 64-bit Lua value, so a
// non-zero upper half is refused with ILC_E_BAD_ARGS and nothing is written (fail closed).
//
// Return value (read as s32): >= 0 is the number of bytes written to out (never more than outCap, never
// more than INT32_MAX); < 0 is an ilc_error. Every error except FAULT writes nothing to out. On FAULT, state
// may have changed and out may hold a partial reply; re-sync with GET_STATUS.
//
// Records: flat, little-endian, fixed layout, no implicit padding (reserved bytes are explicit and must
// be zero). Every record starts with an ilc_header. The module copies the inbound record into its own
// memory through a fault-safe probe before reading any field, validates every field as untrusted, and keeps
// no pointer the caller passed. Code pointers never cross the bridge.
//
// Field ids (in rejections): (struct id << 8) | field index, both from the tables below.
// Locations (in rejections): outer = ilc_location (seat, alias or kill switch), inner = array element.
#pragma once

#include <stdint.h>

// ---------------------------------------------------------------------------------------------------------
// Constants: X(NAME, value, doc). Declared as ILC_<NAME>.
#define ILC_CONSTANTS(X)                                                                                    \
    X(ABI_MAGIC, 0x42434C49u, "ILCB as little-endian bytes")                                               \
    X(ABI_VERSION, 1u, "this file")                                                                         \
    X(MAX_RECORD, 65536u, "inLen above this is refused before the buffer is touched")                      \
    X(OUT_CAP_RECOMMENDED, 4096u, "an output buffer this large fits every reply")                          \
    X(SEATS, 4u, "P1..P4: every seat array has this many entries")                                          \
    X(ACTIVE_SEATS_MAX, 4u, "seats that may play in this release (P1..P4)")                                \
    X(XINPUT_SLOTS, 4u, "XInput user indices 0..3")                                                         \
    X(ACTIONS, 20u, "ilc_action count")                                                                     \
    X(BINDINGS_PER_ACTION, 4u, "")                                                                          \
    X(SEAT_BINDINGS, 80u, "ACTIONS * BINDINGS_PER_ACTION; index = action * 4 + slot")                       \
    X(KILL_SWITCH_BINDINGS, 4u, "") \
    X(CAPTURE_KILL_SWITCH_ACTION, 0xFFu, "CAPTURE_RESULT action byte of a seat-0 capture: the co-op on/off key") \
    X(CHARACTERS, 6u, "regular party members 0..5")                                                         \
    X(NAME_MAX, 96u, "UTF-8 bytes in a device name")                                                        \
    X(ALIASES_MAX, 16u, "XInput slot <-> SDL identity pairs")                                               \
    X(DEVICES_MAX, 16u, "entries in a device inventory reply")                                              \
    X(EVENT_QUEUE, 64u, "events held; the oldest is dropped (and counted) when full")                       \
    X(P1_ACTION_MASK, 0x000F8F00u, "bit per ilc_action that P1 may bind")                                  \
    X(STALL_MAX_MS, 5000u, "STALL_COOP_THREAD: the longest sleep arg0 may ask for (milliseconds)")         \
    X(COUNTERS_LAYOUT, 4u, "ilc_counters.layout: changes whenever a counters field is added, removed or moved") \
    X(LOG_LINES_MAX, 20u, "battle log: the most lines a view shows (the expanded view)")                    \
    X(LOG_LINES_COMPACT_MAX, 8u, "battle log: the most lines the compact view may show")                 \
    X(HUD_FOES, 8u, "PUSH_HUD_STATES: foe entries per record")                                           \
    X(HUD_WORDS, 4u, "PUSH_HUD_STATES: party weapon-state words per record")                             \
    X(HUD_PROCS, 8u, "PUSH_HUD_STATES: proc tags per record")                                            \
    X(HUD_PROC_AGE_MAX_MS, 5000u, "PUSH_HUD_STATES: the oldest proc a record may carry")             \
    X(THEME_ID_MAX, 32u, "HUD theme: bytes of a theme id (settings hud_theme, theme list entries)")      \
    X(THEME_NAME_MAX, 32u, "HUD theme: bytes of a theme's shown name in the theme list")                 \
    X(THEMES_MAX, 16u, "HUD theme: entries in a theme list reply (the three built-ins first)")

// ---------------------------------------------------------------------------------------------------------
// Enums: each item is X(PREFIX, NAME, value, doc) and declares PREFIXNAME = value.
#define ILC_OPS(X)                                                                                          \
    X(ILC_OP_, HELLO, 1, "in: nothing or one ilc_header; out: ilc_hello_reply")                             \
    X(ILC_OP_, SET_SETTINGS, 2, "in: ilc_settings; out: ilc_settings_ack")                                  \
    X(ILC_OP_, COMMAND, 3, "in: ilc_command; out: nothing. 0 = queued, the outcome follows as an event; NOT_READY while the input service is stopped") \
    X(ILC_OP_, GET_STATUS, 4, "in: nothing; out: ilc_status")                                               \
    X(ILC_OP_, GET_SEATS, 5, "in: nothing; out: ilc_seats_reply")                                           \
    X(ILC_OP_, GET_DEVICES, 6, "in: nothing; out: ilc_devices_reply, 32 + count * 148 bytes")               \
    X(ILC_OP_, POP_EVENTS, 7, "in: nothing; out: ilc_events_reply, 32 + count * 32 bytes, as many as fit")  \
    X(ILC_OP_, GET_SETTINGS, 8, "in: nothing; out: the applied ilc_settings (generation 0 = defaults)")      \
    X(ILC_OP_, GET_COUNTERS, 9, "in: nothing; out: ilc_counters, the module's diagnostic counters (read-only, any state; field names in the schema). Read field by field without a lock: each field is untorn, but fields can disagree with each other") \
    X(ILC_OP_, PUSH_HUD_STATES, 10, "in: ilc_hud_states; out: nothing. 0 = kept as the latest (the HUD shows it while fresh); push from a script timer, never from a sync hook") \
    X(ILC_OP_, GET_THEMES, 11, "in: nothing; out: ilc_themes_reply, the HUD themes the Mod Menu offers (built-ins, then valid player themes)") \
    X(ILC_OP_, TEST_SAFE_POINT, 0x7F01, "debug: run the settings safe point now; NOT_AVAILABLE once the boot started its init thread")

#define ILC_ERRORS(X)                                                                                       \
    X(ILC_E_, UNKNOWN_OP, -1, "op is not defined")                                                          \
    X(ILC_E_, NOT_IMPLEMENTED, -2, "reserved")                                                              \
    X(ILC_E_, BAD_ARGS, -3, "null buffer with a length, null out, or a non-zero upper half in op/inLen/outCap") \
    X(ILC_E_, BAD_HEADER, -4, "wrong magic")                                                                \
    X(ILC_E_, ABI_MISMATCH, -5, "record abi_version is not ILC_ABI_VERSION")                                \
    X(ILC_E_, OUT_TOO_SMALL, -6, "outCap is below the reply size; nothing was consumed")                    \
    X(ILC_E_, TOO_LARGE, -7, "inLen above ILC_MAX_RECORD; nothing was read")                                \
    X(ILC_E_, BAD_LENGTH, -8, "inLen is not the op's record size, or header.size differs from inLen")       \
    X(ILC_E_, BAD_TYPE, -9, "header.record_type does not match the op")                                     \
    X(ILC_E_, BAD_FIELD, -10, "a field is out of range or a reserved byte is not zero")                     \
    X(ILC_E_, CONFLICT, -11, "fields are each valid but break a cross-field rule")                          \
    X(ILC_E_, STALE_GENERATION, -12, "settings generation older than the last accepted one")                \
    X(ILC_E_, GENERATION_CONFLICT, -13, "same generation as the last accepted settings, different content") \
    X(ILC_E_, NOT_READY, -14, "valid request for a subsystem that does not exist yet")                      \
    X(ILC_E_, BUSY, -15, "the command queue is full; nothing was queued, send it again")                    \
    X(ILC_E_, NOT_AVAILABLE, -16, "debug op refused because the module's own tick owns the safe point")     \
    X(ILC_E_, FAULT, -17, "caller buffer unusable; state may have changed, out may be partial: re-sync with GET_STATUS")

#define ILC_RECORD_TYPES(X)                                                                                 \
    X(ILC_REC_, NONE, 0, "a bare HELLO header may carry 0")                                                 \
    X(ILC_REC_, HELLO, 1, "")                                                                               \
    X(ILC_REC_, HELLO_REPLY, 2, "")                                                                         \
    X(ILC_REC_, SETTINGS, 3, "")                                                                            \
    X(ILC_REC_, SETTINGS_ACK, 4, "")                                                                        \
    X(ILC_REC_, COMMAND, 5, "")                                                                             \
    X(ILC_REC_, STATUS, 6, "")                                                                              \
    X(ILC_REC_, SEATS, 7, "")                                                                               \
    X(ILC_REC_, DEVICES, 8, "")                                                                             \
    X(ILC_REC_, EVENTS, 9, "")                                                                              \
    X(ILC_REC_, SAFE_POINT_RESULT, 10, "")                                                                  \
    X(ILC_REC_, COUNTERS, 11, "")                                                                        \
    X(ILC_REC_, HUD_STATES, 12, "")                                                                      \
    X(ILC_REC_, THEMES, 13, "")

#define ILC_STATES(X)                                                                                       \
    X(ILC_STATE_, INERT, 1, "no hooks, no threads, no game memory access: not started, unsupported build, duplicate copy, or shut down")\
    X(ILC_STATE_, DISABLED, 2, "co-op module switched off for this start, or the settings file could not be read (reason says which)")\
    X(ILC_STATE_, BLOCKED, 3, "another co-op runtime is installed: nothing armed (reason_detail = marker bits)")\
    X(ILC_STATE_, STARTING, 4, "the init thread is starting the input service and installing the hooks")    \
    X(ILC_STATE_, ARMED, 5, "hooks installed, waiting for the game to finish starting")                     \
    X(ILC_STATE_, READY, 6, "hooks installed and the game is running; reason DRY_ARMED = every co-op detour still runs stock, LIVE = armed subsystems act")\
    X(ILC_STATE_, DEGRADED, 7, "reserved: transition")                                                      \
    X(ILC_STATE_, FAULT, 8, "start-up failed and nothing is armed (reason and reason_detail say where)")

// Reason ids: why the module is in its state (ilc_status.reason, HELLO reason). reason_detail depends on the reason.
#define ILC_REASONS(X)                                                                                      \
    X(ILC_REASON_, NONE, 0, "FF12Hinit has not run: a stray load, or the loader has not called it yet")     \
    X(ILC_REASON_, UNSUPPORTED_BUILD, 1, "not the pinned game build; detail: 1 base, 2 headers unreadable, 3 not AMD64 PE32+, 4 timestamp, 5 SizeOfImage")\
    X(ILC_REASON_, DUPLICATE_INSTANCE, 2, "another copy of this module already runs in the process")        \
    X(ILC_REASON_, INSTANCE_GUARD_FAILED, 3, "the per-process guard could not be created; detail = Win32 error")\
    X(ILC_REASON_, MODULE_OFF, 4, "Co-op module is Off in the settings file")                               \
    X(ILC_REASON_, SETTINGS_UNREADABLE, 5, "the settings file exists but could not be read; detail = Win32 error (0 = larger than 1 MiB)")\
    X(ILC_REASON_, SETTINGS_INVALID, 6, "the settings file is not valid; detail: 1 syntax, 2 schema, 3 newer schema, 4 a value of the wrong type")\
    X(ILC_REASON_, TOGETHER_PRESENT, 7, "Ivalice Together is installed or running; detail = marker bits (bit 31: a check was uncertain)")\
    X(ILC_REASON_, BOOTING, 8, "start-up in progress; detail = ilc_boot_step")                              \
    X(ILC_REASON_, INPUT_FAILED, 9, "the input service did not start; detail = its issue bits (0x100 = did not answer in time)")\
    X(ILC_REASON_, PATCH_CONFLICT, 10, "game code differs from the pinned bytes (another mod or build); detail = patch result | (site row + 1) << 16")\
    X(ILC_REASON_, PATCH_BUSY, 11, "a thread stayed inside a patch site, or its context could not be read; detail as PATCH_CONFLICT")\
    X(ILC_REASON_, PATCH_FAILED, 12, "the hook transaction failed and was rolled back; detail as PATCH_CONFLICT")\
    X(ILC_REASON_, DRY_ARMED, 13, "hooks installed; every co-op detour runs stock (the layer is still dry, or no subsystem is armed): only the settings safe point and the player's own background rows run")\
    X(ILC_REASON_, THREAD_FAILED, 14, "the init thread could not be created; detail = Win32 error")         \
    X(ILC_REASON_, SHUT_DOWN, 15, "the game is exiting; co-op disarmed and the input service stopped")      \
    X(ILC_REASON_, BOOT_ABORTED, 16, "the game began exiting before start-up finished; nothing was armed")   \
    X(ILC_REASON_, LIVE, 17, "hooks installed and live: an applied settings record took the layer out of dry mode, and at least one armed subsystem may act")

#define ILC_BOOT_STEPS(X)                                                                                   \
    X(ILC_BOOT_STEP_, CHECKS, 1, "host, instance guard, Ivalice Together, co-op module switch (FF12Hinit thread)")\
    X(ILC_BOOT_STEP_, INPUT, 2, "starting the input service on its own thread")                             \
    X(ILC_BOOT_STEP_, RECHECK, 3, "Ivalice Together checked again just before the hooks")                   \
    X(ILC_BOOT_STEP_, PATCH, 4, "the hook transaction (dry)")                                               \
    X(ILC_BOOT_STEP_, ARM, 5, "the settings tick attached and the liveness record armed")

#define ILC_BOOT_FLAGS(X)                                                                                   \
    X(ILC_BOOTF_, INIT_CALLED, 0x0001, "FF12Hinit ran")                                                     \
    X(ILC_BOOTF_, GUARD_HELD, 0x0002, "this copy holds the per-process guard")                              \
    X(ILC_BOOTF_, THREAD_STARTED, 0x0004, "the init thread was created")                                    \
    X(ILC_BOOTF_, INPUT_RUNNING, 0x0008, "the input service was started by the boot and is running")        \
    X(ILC_BOOTF_, HOOKS_INSTALLED, 0x0010, "the hook transaction committed (hooks stay installed until the process ends)")\
    X(ILC_BOOTF_, ARMED, 0x0020, "the liveness record is armed")                                            \
    X(ILC_BOOTF_, INIT_DONE, 0x0040, "FF12HinitDone ran: the game is running")                              \
    X(ILC_BOOTF_, CLEANUP_DONE, 0x0080, "FF12Hcleanup ran")                                                 \
    X(ILC_BOOTF_, SETTINGS_DEFAULTED, 0x0100, "the settings file or its switch was missing: the native default decided")\
    X(ILC_BOOTF_, TOGETHER_RESIDUE, 0x0200, "files left by Ivalice Together that cannot patch anything (settings folder, Lua menu)")\
    X(ILC_BOOTF_, INTERACTIONS_FAILED, 0x0400, "guest interactions could not attach: guests cannot use doors, chests, NPCs or loot (the rest runs)")

#define ILC_BUILD_FLAGS(X) X(ILC_BUILD_, INERT, 1, "reserved: an inert build (no hooks, threads or game memory access)")

#define ILC_READY_BITS(X) X(ILC_READY_, SETTINGS, 1, "settings staging and safe point work")

#define ILC_ACK_DISPOSITIONS(X)                                                                             \
    X(ILC_ACK_, STAGED, 1, "new generation copied to the staging slot; applied at the next safe point")    \
    X(ILC_ACK_, DUPLICATE, 2, "same generation and content as the last accepted settings; nothing changed")

#define ILC_SAFE_POINT_RESULTS(X)                                                                           \
    X(ILC_SAFE_POINT_, IDLE, 0, "nothing staged")                                                           \
    X(ILC_SAFE_POINT_, APPLIED, 1, "staged settings became the applied settings")                           \
    X(ILC_SAFE_POINT_, BUSY, 2, "a bridge call held the lock; try again next tick (never blocks)")

#define ILC_COMMANDS(X)                                                                                     \
    X(ILC_CMD_, PARK, 1, "seat 0 = all guests or 2..4; arg0 0 = play, 1 = park. Live now; holds until a settings record changes that seat's parked field") \
    X(ILC_CMD_, MOVE_SEAT_NOW, 2, "seat 1..4; arg0 = ilc_character 0..5. Absorbs the seat's held input and queues the move for the session") \
    X(ILC_CMD_, PAIRING_START, 3, "seat 1..4; arg0 = inventory session (non-zero), arg1 = entry id. Proof by pressing A on that pad; P1 on another pad runs the guarded change") \
    X(ILC_CMD_, PAIRING_CANCEL, 4, "seat 1..4")                                                             \
    X(ILC_CMD_, CAPTURE_START, 5, "seat 1..4; arg0 = ilc_action (P1: P1 actions only), arg1 = slot 0..3. Seat 0 = the co-op on/off key: arg0 0, arg1 = kill_switch slot 0..3; keys and the right mouse button only, from the keyboard whoever owns it") \
    X(ILC_CMD_, CAPTURE_CANCEL, 6, "seat 0..4 (0 = the co-op on/off key's capture)") \
    X(ILC_CMD_, RESTORE_DEFAULTS, 7, "seat 0 = all or 1..4")                                                \
    X(ILC_CMD_, KILL_SWITCH, 8, "seat 0; arg0 1 = co-op off now, 0 = back on")                              \
    X(ILC_CMD_, STALL_COOP_THREAD, 9, "developer test, seat 0; arg0 = 1..STALL_MAX_MS: the co-op thread sleeps that long once (its liveness goes stale after 2 s); arg1 0. CONFLICT unless the applied settings have developer = 1; NOT_READY while the co-op thread is not running. The outcome follows as COMMAND_RESULT, b = milliseconds slept")

#define ILC_EVENT_KINDS(X)                                                                                  \
    X(ILC_EV_, SETTINGS_APPLIED, 1, "generation; a = body digest")                                          \
    X(ILC_EV_, SETTINGS_REJECTED, 2, "generation as sent; code, field, location")                           \
    X(ILC_EV_, COMMAND_REJECTED, 3, "generation = request id; code, field, location; a = command")          \
    X(ILC_EV_, COMMAND_RESULT, 4, "generation = request id; a = command; b: PARK = parked seats (bit n = seat n+1), MOVE_SEAT_NOW = character | 0x100 (queued, no session yet), STALL_COOP_THREAD = milliseconds slept") \
    X(ILC_EV_, PAIRING_RESULT, 5, "generation = request id; a = ilc_pairing_outcome | other seat << 8 | learned xinput slot << 16 (0xFF none); b = entry id") \
    X(ILC_EV_, CAPTURE_RESULT, 6, "generation = request id; a = ilc_capture_outcome | action << 8 | slot << 16 (seat 0: action = CAPTURE_KILL_SWITCH_ACTION, slot = kill_switch index); b = binding kind | code << 8 when captured") \
    X(ILC_EV_, SEAT_CHANGED, 7, "reserved")                                                                 \
    X(ILC_EV_, STATUS_CHANGED, 8, "reserved")                                                               \
    X(ILC_EV_, DEVICES_CHANGED, 9, "reserved")

#define ILC_LOCATIONS(X)                                                                                    \
    X(ILC_LOC_, SEAT_P1, 0, "seats[0]; also used for top-level fields")                                     \
    X(ILC_LOC_, SEAT_P2, 1, "")                                                                             \
    X(ILC_LOC_, SEAT_P3, 2, "")                                                                             \
    X(ILC_LOC_, SEAT_P4, 3, "")                                                                             \
    X(ILC_LOC_, KILL_SWITCH, 8, "settings.kill_switch")                                                     \
    X(ILC_LOC_, ALIAS_0, 16, "aliases[n] is ALIAS_0 + n")

#define ILC_CHARACTERS_ENUM(X)                                                                              \
    X(ILC_CHAR_, VAAN, 0, "party member index")                                                             \
    X(ILC_CHAR_, ASHE, 1, "")                                                                               \
    X(ILC_CHAR_, FRAN, 2, "")                                                                               \
    X(ILC_CHAR_, BALTHIER, 3, "")                                                                           \
    X(ILC_CHAR_, BASCH, 4, "")                                                                              \
    X(ILC_CHAR_, PENELO, 5, "")                                                                             \
    X(ILC_CHAR_, AUTO, 0xFE, "first free regular at session start; for P1, keep the current character")    \
    X(ILC_CHAR_, NONE, 0xFF, "no character (guests only)")

#define ILC_COLOURS(X)                                                                                      \
    X(ILC_COLOUR_, SAPPHIRE, 0, "target-circle palette index")                                              \
    X(ILC_COLOUR_, RUBY, 1, "")                                                                             \
    X(ILC_COLOUR_, PINK, 2, "")                                                                             \
    X(ILC_COLOUR_, EMERALD, 3, "")                                                                          \
    X(ILC_COLOUR_, GOLD, 4, "")                                                                             \
    X(ILC_COLOUR_, AMETHYST, 5, "")                                                                         \
    X(ILC_COLOUR_, BRONZE, 6, "")

#define ILC_COLOUR_MODES(X)                                                                                 \
    X(ILC_COLOUR_MODE_, CHARACTER, 0, "colours follow characters")                                          \
    X(ILC_COLOUR_MODE_, PLAYER, 1, "colours follow seats")

#define ILC_DEVICE_KINDS(X)                                                                                 \
    X(ILC_DEVICE_, NONE, 0, "")                                                                             \
    X(ILC_DEVICE_, XINPUT, 1, "xinput_slot 0..3")                                                           \
    X(ILC_DEVICE_, SDL, 2, "identity")

#define ILC_BINDING_KINDS(X)                                                                                \
    X(ILC_BIND_, NONE, 0, "code 0")                                                                         \
    X(ILC_BIND_, KEY, 1, "code = virtual-key 1..255")                                                       \
    X(ILC_BIND_, MOUSE, 2, "code 1 = left, 2 = right")                                                      \
    X(ILC_BIND_, PAD, 3, "code = ilc_pad_control on the seat's own pad")

#define ILC_PAD_CONTROLS(F)                                                                                 \
    F(ILC_PAD_, A, 1, "")                                                                                   \
    F(ILC_PAD_, B, 2, "")                                                                                   \
    F(ILC_PAD_, X, 3, "")                                                                                   \
    F(ILC_PAD_, Y, 4, "")                                                                                   \
    F(ILC_PAD_, LB, 5, "")                                                                                  \
    F(ILC_PAD_, RB, 6, "")                                                                                  \
    F(ILC_PAD_, BACK, 7, "")                                                                                \
    F(ILC_PAD_, START, 8, "")                                                                               \
    F(ILC_PAD_, L3, 9, "")                                                                                  \
    F(ILC_PAD_, R3, 10, "")                                                                                 \
    F(ILC_PAD_, DPAD_UP, 11, "")                                                                            \
    F(ILC_PAD_, DPAD_DOWN, 12, "")                                                                          \
    F(ILC_PAD_, DPAD_LEFT, 13, "")                                                                          \
    F(ILC_PAD_, DPAD_RIGHT, 14, "")                                                                         \
    F(ILC_PAD_, LT, 15, "")                                                                                 \
    F(ILC_PAD_, RT, 16, "")                                                                                 \
    F(ILC_PAD_, LS_UP, 17, "")                                                                              \
    F(ILC_PAD_, LS_DOWN, 18, "")                                                                            \
    F(ILC_PAD_, LS_LEFT, 19, "")                                                                            \
    F(ILC_PAD_, LS_RIGHT, 20, "")                                                                           \
    F(ILC_PAD_, RS_UP, 21, "")                                                                              \
    F(ILC_PAD_, RS_DOWN, 22, "")                                                                            \
    F(ILC_PAD_, RS_LEFT, 23, "")                                                                            \
    F(ILC_PAD_, RS_RIGHT, 24, "")

#define ILC_ACTIONS_ENUM(X)                                                                                 \
    X(ILC_ACTION_, TOGGLE_CONTROL, 0, "seat drop-in/out key")                                         \
    X(ILC_ACTION_, MOVE_UP, 1, "")                                                                          \
    X(ILC_ACTION_, MOVE_DOWN, 2, "")                                                                        \
    X(ILC_ACTION_, MOVE_LEFT, 3, "")                                                                        \
    X(ILC_ACTION_, MOVE_RIGHT, 4, "")                                                                       \
    X(ILC_ACTION_, WALK_RUN, 5, "")                                                                         \
    X(ILC_ACTION_, FLEE, 6, "")                                                                             \
    X(ILC_ACTION_, BATTLE_MENU, 7, "")                                                                      \
    X(ILC_ACTION_, MENU_UP, 8, "P1")                                                                        \
    X(ILC_ACTION_, MENU_DOWN, 9, "P1")                                                                      \
    X(ILC_ACTION_, MENU_LEFT, 10, "P1")                                                                     \
    X(ILC_ACTION_, MENU_RIGHT, 11, "P1")                                                                    \
    X(ILC_ACTION_, TARGET_PREV, 12, "")                                                                     \
    X(ILC_ACTION_, TARGET_NEXT, 13, "")                                                                     \
    X(ILC_ACTION_, QUICKENING, 14, "")                                                                      \
    X(ILC_ACTION_, CONFIRM, 15, "P1")                                                                       \
    X(ILC_ACTION_, CANCEL, 16, "P1")                                                                        \
    X(ILC_ACTION_, PARTY, 17, "P1")                                                                         \
    X(ILC_ACTION_, PAUSE, 18, "P1")                                                                         \
    X(ILC_ACTION_, IDENTIFY, 19, "P1 too: flash this seat's diamond over its character (Battle HUD)")

#define ILC_SEAT_FLAGS(X)                                                                                   \
    X(ILC_SEATF_, HAS_DEVICE, 0x01, "configured device is not NONE")                                        \
    X(ILC_SEATF_, HAS_KBM, 0x02, "seat owns keyboard and mouse (kbm_seat)")                                 \
    X(ILC_SEATF_, PARKED, 0x04, "")                                                                         \
    X(ILC_SEATF_, PLAYING, 0x08, "configured: owns an input and is not parked")                             \
    X(ILC_SEATF_, LOCKED, 0x10, "P1")                                                                       \
    X(ILC_SEATF_, LIVE, 0x20, "a session is running (never set in v1)")                                     \
    X(ILC_SEATF_, BENCHED, 0x40, "live only")

#define ILC_SESSION_STATES(X)                                                                               \
    X(ILC_SESSION_, NOT_RUNNING, 0, "the session core has not started (module not armed)")                  \
    X(ILC_SESSION_, NO_WORLD, 1, "title screen, start-up or game over screen: no game world")               \
    X(ILC_SESSION_, LOADING, 2, "a world exists but the party or the area is still settling")               \
    X(ILC_SESSION_, CUTSCENE, 3, "the player has no control (cutscene or scripted dialogue)")               \
    X(ILC_SESSION_, MENU, 4, "a native menu or modal interaction owns the screen")                          \
    X(ILC_SESSION_, FIELD, 5, "play: seats with a character may steer it")                                  \
    X(ILC_SESSION_, PARTY_DOWN, 6, "every party member is down: the game's own recovery runs")

#define ILC_COOP_SWITCHES(X)                                                                                \
    X(ILC_COOP_SWITCH_, ON, 0, "co-op on")                                                                  \
    X(ILC_COOP_SWITCH_, RELEASING, 1, "kill switch pressed: releasing guest input (at most 250 ms)")        \
    X(ILC_COOP_SWITCH_, OFF, 2, "co-op off (kill switch): stock play until it is switched back on")

#define ILC_SERVICE_STATES(X)                                                                               \
    X(ILC_SERVICE_, NOT_READY, 0, "input service not built yet: show discovery unavailable")                \
    X(ILC_SERVICE_, READY, 1, "the input service runs; the inventory is current")

// Subsystem bits. With the module on, every subsystem is armed: the arm gates get every bit except those named in
// subsystems_disarm (a developer override, honoured only while developer = 1) and, with shared_camera 0, CAMERA.
// Each subsystem checks its bit at its own safe point and stays (or returns to) stock behaviour while it is clear.
#define ILC_SUBSYSTEMS(X)                                                                                   \
    X(ILC_SUBSYS_, INPUT_ISOLATION, 0x01, "guest pads and keyboard never drive the game")                   \
    X(ILC_SUBSYS_, MOVEMENT, 0x02, "follower steering and movement hooks")                                  \
    X(ILC_SUBSYS_, PARTY, 0x04, "seat assignment, story guard, party edits")                                \
    X(ILC_SUBSYS_, CAMERA, 0x08, "shared group camera")                                                     \
    X(ILC_SUBSYS_, FIREWALL_MENUS, 0x10, "semantic input firewall and guest battle menus")                  \
    X(ILC_SUBSYS_, WAIT, 0x20, "Wait-mode participant rule")                                                \
    X(ILC_SUBSYS_, RENDER, 0x40, "D3D11 overlay and panels")                                                \
    X(ILC_SUBSYS_, TARGETS_CARDS, 0x80, "target rings, TARGET INFO and cards")

// Battle HUD elements (settings hud_elements): a set bit = that element on. hud_enabled 0 turns every one off.
#define ILC_HUD_ELEMENTS(X)                                                                                 \
    X(ILC_HUD_, PARTY_SHRINK, 0x001, "the reduced native party list (bottom-right)")                       \
    X(ILC_HUD_, SEAT_EDGE, 0x002, "seat-colour edge and seat numeral on each party row")                    \
    X(ILC_HUD_, LOW_HP_GLOW, 0x004, "low HP: the native HP number glows red")                               \
    X(ILC_HUD_, DAMAGE_GHOST, 0x008, "damage ghost on the party HP gauges")                                 \
    X(ILC_HUD_, ACTION_LINE, 0x010, "action line: Choosing... and the ally an action aims at")              \
    X(ILC_HUD_, TARGET_INFO, 0x020, "expanded Target Info (top-left); off = the native window")             \
    X(ILC_HUD_, BATTLE_LOG, 0x040, "battle log (top-right); off = the native combat log")                   \
    X(ILC_HUD_, SEAT_LINES, 0x080, "target lines in seat colours (gambit allies silver)")                   \
    X(ILC_HUD_, SEAT_DIAMONDS, 0x100, "numbered seat diamonds over the players' characters")                \
    X(ILC_HUD_, AOE_RING, 0x200, "a ring only while a seat aims an area magick or technick")                \
    X(ILC_HUD_, CIRCLES_OFF, 0x400, "native target circles off")                                           \
    X(ILC_HUD_, PARTY_TIMERS, 0x800, "status timers on the party list rows")                                \
    X(ILC_HUD_, GUEST_CURSORS, 0x1000, "the seats' party-target hands on the party list rows")

// Town visibility (settings town_view).
#define ILC_TOWN_VIEWS(X)                                                                                   \
    X(ILC_TOWN_VIEW_, PLAYERS, 0, "only the characters the players control have a body in towns")          \
    X(ILC_TOWN_VIEW_, PARTY, 1, "the whole party as the game (or Companions) shows it: nothing is hidden")

// When the co-op Wait rule stops battle time (settings wait_pause).
#define ILC_WAIT_PAUSES(X)                                                                                  \
    X(ILC_WAIT_PAUSE_, ALL, 0, "only while every playing seat has its battle menu open")                   \
    X(ILC_WAIT_PAUSE_, ANY, 1, "while any playing seat has its battle menu open")

// Battle log filters (settings log_filter).
#define ILC_LOG_FILTERS(X)                                                                                  \
    X(ILC_LOG_FILTER_, ALL, 0, "every line")                                                                \
    X(ILC_LOG_FILTER_, PARTY, 1, "party and gambit allies")                                                 \
    X(ILC_LOG_FILTER_, FOES, 2, "foes")                                                                     \
    X(ILC_LOG_FILTER_, STATUSES, 3, "status lines")                                                         \
    X(ILC_LOG_FILTER_, MINE, 4, "one seat (log_filter_seat)")

// What the Battle HUD page shows about the HUD's native pieces (status hud_report).
#define ILC_HUD_REPORTS(X)                                                                                  \
    X(ILC_HUD_REPORT_, FRAME_HOOK_OFF, 0x01, "the HUD frame hook does not run (its call is not ours): no co-op HUD or menus are drawn")\
    X(ILC_HUD_REPORT_, VISION_NO_COMBAT_LOG, 0x02, "Insurgent's Vision No Combat Log is on: its NOPs sit on the frame hook's call")\
    X(ILC_HUD_REPORT_, PARTY_LAYOUT_FOREIGN, 0x04, "another mod changed the party list layout: left alone (not reduced)")\
    X(ILC_HUD_REPORT_, CIRCLES_BY_VISION, 0x08, "native target circles are off through Vision's own patch (adopted)")\
    X(ILC_HUD_REPORT_, CIRCLES_FOREIGN, 0x10, "another mod changed the target-circle code: left alone")\
    X(ILC_HUD_REPORT_, RENDER_OFF, 0x20, "the HUD is not drawn: rendering is off or faulted, or the frame hook is not attached")

// The Legacy script states the HUD shows (PUSH_HUD_STATES): a foe's state bits.
#define ILC_HUD_FOE_FLAGS(X)                                                                             \
    X(ILC_HUD_FOE_, BROKEN, 0x01, "Broken: broken_s seconds left (family_identities noteBroken)")        \
    X(ILC_HUD_FOE_, STAGGERED, 0x02, "Staggered: its push budget is spent until it acts")                \
    X(ILC_HUD_FOE_, SPOTTED, 0x04, "Spotted: the next dodge roll against it cannot be dodged (spotter_seat)") \
    X(ILC_HUD_FOE_, OIL, 0x08, "Oil not yet kindled (Gillie Boots)")                                     \
    X(ILC_HUD_FOE_, KINDLED, 0x10, "its Oil was kindled (the first hit Slowed it)")                      \
    X(ILC_HUD_FOE_, KING, 0x20, "the king of the zone (kings_runtime)")                                  \
    X(ILC_HUD_FOE_, CROWNED, 0x40, "a crowned story boss (crown_element)")                               \
    X(ILC_HUD_FOE_, MARK, 0x80, "a hunt mark")

// A party member's weapon-state word (PUSH_HUD_STATES ilc_hud_word.kind).
#define ILC_HUD_WORD_KINDS(X)                                                                            \
    X(ILC_HUD_WORD_, NONE, 0, "no word")                                                                 \
    X(ILC_HUD_WORD_, GUARD, 1, "guard fatigue: value = worn steps 0..4 (shown from 2)")                  \
    X(ILC_HUD_WORD_, CADENCE, 2, "fist Cadence: value = steps 1..5")                                     \
    X(ILC_HUD_WORD_, FLOW, 3, "pole Counterflow: value = the bonus, +N")

// A Proficiency / identity proc as a log tag (PUSH_HUD_STATES ilc_hud_proc.tag).
#define ILC_HUD_TAGS(X)                                                                                  \
    X(ILC_HUD_TAG_, NONE, 0, "")                                                                         \
    X(ILC_HUD_TAG_, SUNDER, 1, "axe: a Defense dent")                                                    \
    X(ILC_HUD_TAG_, BREAK, 2, "a hit Broke the foe")                                                     \
    X(ILC_HUD_TAG_, CADENCE, 3, "fists: Cadence full")                                                   \
    X(ILC_HUD_TAG_, COUNTERFLOW, 4, "pole: a lent Counter")                                              \
    X(ILC_HUD_TAG_, GUARD, 5, "a guard held (block, parry, dodge)")                                      \
    X(ILC_HUD_TAG_, UNSEEN, 6, "ninja sword: a tired foe could not dodge")                               \
    X(ILC_HUD_TAG_, PIERCE, 7, "a pierce Broke the foe")                                                 \
    X(ILC_HUD_TAG_, MEND, 8, "rod: an overflowing heal drew foes")                                       \
    X(ILC_HUD_TAG_, PROFICIENCY, 9, "a Proficiency proc")                                                \
    X(ILC_HUD_TAG_, SPOTTED, 10, "a Spotter mark")                                                       \
    X(ILC_HUD_TAG_, KINDLE, 11, "Oil kindled: Slow")

// HUD theme list entries (GET_THEMES).
#define ILC_THEME_FLAGS(X)                                                                               \
    X(ILC_THEME_, BUILTIN, 0x01, "compiled into the module")                                            \
    X(ILC_THEME_, DEFAULT, 0x02, "the built-in default (used when hud_theme is all zero or cannot be used)") \
    X(ILC_THEME_, ACTIVE, 0x04, "the theme the overlay draws with now")                                 \
    X(ILC_THEME_, SELECTED, 0x08, "the theme the applied settings ask for")

// Pairing outcomes (PAIRING_RESULT a & 0xFF). Success outcomes never change settings: Lua commits them.
#define ILC_PAIRING_OUTCOMES(X)                                                                             \
    X(ILC_PAIR_, READY, 1, "proved on the pad; commit the device (and the alias if a slot was learned)")    \
    X(ILC_PAIR_, IDENTIFIED, 2, "P1 proved its own pad: add the name or alias only, never reassign")        \
    X(ILC_PAIR_, PROVISIONAL, 3, "P1 change: the new pad drives P1 now; press A on it again to keep it")    \
    X(ILC_PAIR_, CONFIRMED, 4, "P1 change confirmed on the new pad: commit it; not seen in 5 s -> REVERTED") \
    X(ILC_PAIR_, REVERTED, 5, "P1 change undone: no confirm (10 s) or no commit; P1 is on its configured pad") \
    X(ILC_PAIR_, CANCELLED, 6, "PAIRING_CANCEL, a newer PAIRING_START, a settings change or a service stop") \
    X(ILC_PAIR_, TIMEOUT, 7, "no proof within 20 s")                                                        \
    X(ILC_PAIR_, HELD_A, 8, "timed out with an A button held throughout: release A first")                  \
    X(ILC_PAIR_, DISCONNECTED, 9, "the pad went away")                                                      \
    X(ILC_PAIR_, TWO_PADS, 10, "more than one pad answered (hide duplicate virtual devices)")               \
    X(ILC_PAIR_, P1_LOCKED, 11, "the pad is Player 1's")                                                    \
    X(ILC_PAIR_, OWNED_BY_SEAT, 12, "another guest holds the pad (other seat in a >> 8): offer a swap")     \
    X(ILC_PAIR_, STALE_SESSION, 13, "arg0 is not the current inventory session")                            \
    X(ILC_PAIR_, NOT_FOUND, 14, "no connected pad has that entry id")

// Capture outcomes (CAPTURE_RESULT a & 0xFF).
#define ILC_CAPTURE_OUTCOMES(X)                                                                             \
    X(ILC_CAPT_, CAPTURED, 1, "b holds the binding: store it in that action and slot")                      \
    X(ILC_CAPT_, CANCELLED, 2, "CAPTURE_CANCEL, a newer CAPTURE_START or a service stop")                   \
    X(ILC_CAPT_, TIMEOUT, 3, "no fresh press within 20 s")                                                  \
    X(ILC_CAPT_, NO_INPUT, 4, "the seat has no connected pad and does not own the keyboard (never for seat 0)")

// The list of enums: E(json name, C++ type name, underlying type, item list).
#define ILC_ENUMS(E)                                                                                        \
    E(op, ilc_op, uint32_t, ILC_OPS)                                                                        \
    E(error, ilc_error, int32_t, ILC_ERRORS)                                                                \
    E(record_type, ilc_record_type, uint16_t, ILC_RECORD_TYPES)                                             \
    E(state, ilc_state, uint32_t, ILC_STATES)                                                               \
    E(reason, ilc_reason, uint32_t, ILC_REASONS)                                                            \
    E(boot_step, ilc_boot_step, uint32_t, ILC_BOOT_STEPS)                                                   \
    E(boot_flag, ilc_boot_flag, uint32_t, ILC_BOOT_FLAGS)                                                   \
    E(build_flag, ilc_build_flag, uint32_t, ILC_BUILD_FLAGS)                                                \
    E(ready_bit, ilc_ready_bit, uint32_t, ILC_READY_BITS)                                                   \
    E(ack_disposition, ilc_ack_disposition, uint32_t, ILC_ACK_DISPOSITIONS)                                 \
    E(safe_point_outcome, ilc_safe_point_outcome, uint32_t, ILC_SAFE_POINT_RESULTS)                           \
    E(command, ilc_command_id, uint16_t, ILC_COMMANDS)                                                      \
    E(event_kind, ilc_event_kind, uint16_t, ILC_EVENT_KINDS)                                                \
    E(location, ilc_location, uint32_t, ILC_LOCATIONS)                                                      \
    E(character, ilc_character, uint8_t, ILC_CHARACTERS_ENUM)                                               \
    E(colour, ilc_colour, uint8_t, ILC_COLOURS)                                                             \
    E(colour_mode, ilc_colour_mode, uint8_t, ILC_COLOUR_MODES)                                              \
    E(device_kind, ilc_device_kind, uint8_t, ILC_DEVICE_KINDS)                                              \
    E(binding_kind, ilc_binding_kind, uint8_t, ILC_BINDING_KINDS)                                           \
    E(pad_control, ilc_pad_control, uint8_t, ILC_PAD_CONTROLS)                                              \
    E(action, ilc_action, uint8_t, ILC_ACTIONS_ENUM)                                                        \
    E(seat_flag, ilc_seat_flag, uint8_t, ILC_SEAT_FLAGS)                                                    \
    E(session_state, ilc_session_state, uint32_t, ILC_SESSION_STATES)                                       \
    E(service_state, ilc_service_state, uint32_t, ILC_SERVICE_STATES)                                       \
    E(coop_switch, ilc_coop_switch, uint8_t, ILC_COOP_SWITCHES)                                             \
    E(subsystem, ilc_subsystem_bit, uint32_t, ILC_SUBSYSTEMS)                                               \
    E(hud_element, ilc_hud_element_bit, uint32_t, ILC_HUD_ELEMENTS)                                         \
    E(log_filter, ilc_log_filter, uint8_t, ILC_LOG_FILTERS)                                                 \
    E(town_view, ilc_town_view, uint8_t, ILC_TOWN_VIEWS)                                                    \
    E(wait_pause, ilc_wait_pause, uint8_t, ILC_WAIT_PAUSES)                                                 \
    E(hud_report, ilc_hud_report_bit, uint8_t, ILC_HUD_REPORTS)                                             \
    E(pairing_outcome, ilc_pairing_outcome, uint8_t, ILC_PAIRING_OUTCOMES)                                  \
    E(capture_outcome, ilc_capture_outcome, uint8_t, ILC_CAPTURE_OUTCOMES)                               \
    E(hud_foe_flag, ilc_hud_foe_flag, uint8_t, ILC_HUD_FOE_FLAGS)                                        \
    E(hud_word_kind, ilc_hud_word_kind, uint8_t, ILC_HUD_WORD_KINDS)                                     \
    E(hud_tag, ilc_hud_tag, uint8_t, ILC_HUD_TAGS)                                                       \
    E(theme_flag, ilc_theme_flag, uint8_t, ILC_THEME_FLAGS)

// ---------------------------------------------------------------------------------------------------------
// Records. Each field is X(kind, type, name, count, min, max, doc):
//   VAL    one little-endian integer, min..max checked generically on inbound records
//   ARR    count integers, each min..max
//   REC    one nested record (checked by its own table)
//   RECS   count nested records
//   PAD    count reserved bytes, must be zero on inbound records
//   BYTES  count opaque bytes, checked by hand (GUIDs, names)
// Ranges on outbound-only records are informative.

#define ILC_HEADER_FIELDS(X)                                                                                \
    X(VAL, uint32_t, magic, 1, 0, 0xFFFFFFFFu, "ILC_ABI_MAGIC")                                             \
    X(VAL, uint16_t, abi_version, 1, 0, 0xFFFFu, "ILC_ABI_VERSION of the writer")                           \
    X(VAL, uint16_t, record_type, 1, 0, 0xFFFFu, "ilc_record_type")                                         \
    X(VAL, uint32_t, size, 1, 0, 0xFFFFFFFFu, "whole record in bytes, header included")                     \
    X(VAL, uint32_t, generation, 1, 0, 0xFFFFFFFFu, "settings generation, command request id, or 0")

#define ILC_BINDING_FIELDS(X)                                                                               \
    X(VAL, uint8_t, kind, 1, 0, ILC_ENUM_MAX(ILC_BINDING_KINDS), "ilc_binding_kind")                        \
    X(VAL, uint8_t, code, 1, 0, 255, "depends on kind; 0 when kind is NONE")

#define ILC_IDENTITY_FIELDS(X)                                                                              \
    X(BYTES, uint8_t, guid, 16, 0, 0, "SDL joystick GUID; not all zero when used")                          \
    X(BYTES, uint8_t, uid, 16, 0, 0, "first 16 bytes of SHA-256 of the casefolded device path; zero unless has_uid") \
    X(VAL, uint16_t, ordinal, 1, 0, 255, "tells identical models apart when there is no uid")               \
    X(VAL, uint8_t, has_uid, 1, 0, 1, "")                                                                   \
    X(VAL, uint8_t, name_len, 1, 0, ILC_NAME_MAX, "bytes of name in use")                                   \
    X(BYTES, uint8_t, name, ILC_NAME_MAX, 0, 0, "UTF-8, no control characters; zero after name_len")        \
    X(PAD, uint8_t, reserved, 4, 0, 0, "")

#define ILC_SEAT_DEVICE_FIELDS(X)                                                                           \
    X(VAL, uint8_t, kind, 1, 0, ILC_ENUM_MAX(ILC_DEVICE_KINDS), "ilc_device_kind")                          \
    X(VAL, uint8_t, xinput_slot, 1, 0, ILC_XINPUT_SLOTS - 1, "when kind is XINPUT, else 0")                 \
    X(PAD, uint8_t, reserved, 2, 0, 0, "")                                                                  \
    X(REC, ilc_device_identity, sdl, 1, 0, 0, "when kind is SDL, else all zero")

#define ILC_SEAT_SETTINGS_FIELDS(X)                                                                         \
    X(VAL, uint8_t, character, 1, 0, 255, "seat character (P1: preference): 0..5, AUTO, NONE (not P1)")         \
    X(VAL, uint8_t, colour, 1, 0, ILC_ENUM_MAX(ILC_COLOURS), "seat colour, ilc_colour")                              \
    X(VAL, uint8_t, seat_ui_sounds, 1, 0, 1, "battle menu sounds for this seat")                       \
    X(VAL, uint8_t, parked, 1, 0, 1, "seat parked (guest sits out); P1 must be 0")                                          \
    X(PAD, uint8_t, reserved, 4, 0, 0, "")                                                                  \
    X(REC, ilc_seat_device, device, 1, 0, 0, "the seat's input device and its identity")                                            \
    X(RECS, ilc_binding, bindings, ILC_SEAT_BINDINGS, 0, 0, "button bindings: action * 4 + slot")

#define ILC_ALIAS_FIELDS(X)                                                                                 \
    X(VAL, uint8_t, xinput_slot, 1, 0, ILC_XINPUT_SLOTS - 1, "")                                            \
    X(PAD, uint8_t, reserved, 3, 0, 0, "")                                                                  \
    X(REC, ilc_device_identity, sdl, 1, 0, 0, "")

#define ILC_ECONOMY_FIELDS(X)                                                                               \
    X(VAL, uint8_t, present, 1, 0, 1, "Legacy MP economy detected by Lua; all other fields 0 when not")     \
    X(VAL, uint8_t, version_major, 1, 0, 255, "")                                                           \
    X(VAL, uint8_t, version_minor, 1, 0, 255, "")                                                           \
    X(VAL, uint8_t, version_patch, 1, 0, 255, "")                                                           \
    X(VAL, uint16_t, divisor_x100, 1, 0, 25500, "DIVISOR * 100; 100..25500 when present")                   \
    X(VAL, uint16_t, cap_x100, 1, 0, 1600, "CAP * 100; 100..1600 when present")

#define ILC_SETTINGS_FIELDS(X)                                                                              \
    X(REC, ilc_header, header, 1, 0, 0, "record_type SETTINGS; generation >= 1, monotonic")                 \
    X(VAL, uint8_t, module_enabled_at_start, 1, 0, 1, "module switch as Lua sees it; native reads coop.json at start") \
    X(PAD, uint8_t, reserved0, 1, 0, 0, "was all_characters_at_start (nothing read it)")                   \
    X(VAL, uint8_t, kbm_seat, 1, 0, ILC_SEATS, "seat that owns keyboard and mouse; 0 none, 1..ACTIVE_SEATS_MAX")      \
    X(VAL, uint8_t, colour_mode, 1, 0, ILC_ENUM_MAX(ILC_COLOUR_MODES), "colour mode, ilc_colour_mode")               \
    X(VAL, uint8_t, log_level, 1, 0, 1, "hook.log detail: 0 normal (start-up, faults, refusals), 1 verbose (also each applied record, co-op on/off, seat toggles)") \
    X(VAL, uint8_t, network_mode, 1, 0, 0, "phase 1: 0 = local only")                                       \
    X(VAL, uint8_t, alias_count, 1, 0, ILC_ALIASES_MAX, "aliases in use; the rest are all zero")            \
    X(VAL, uint8_t, keep_running, 1, 0, 1, "keep running while another window has focus (windowed, borderless)") \
    X(ARR, uint8_t, character_colour, ILC_CHARACTERS, 0, ILC_ENUM_MAX(ILC_COLOURS), "colour per character, by ilc_character 0..5") \
    X(VAL, uint8_t, hud_test_draw, 1, 0, 1, "Diagnostics: co-op canvas test draw; 0 off")                   \
    X(VAL, uint8_t, developer, 1, 0, 1, "Diagnostics: developer options; 1 allows test-only commands (STALL_COOP_THREAD); 0 off") \
    X(RECS, ilc_binding, kill_switch, ILC_KILL_SWITCH_BINDINGS, 0, 0, "co-op kill switch binding; keyboard or mouse only")       \
    X(REC, ilc_economy, economy, 1, 0, 0, "Legacy MP economy parameters")                                   \
    X(RECS, ilc_seat_settings, seats, ILC_SEATS, 0, 0, "P1..P4")                                            \
    X(RECS, ilc_device_alias, aliases, ILC_ALIASES_MAX, 0, 0, "")                                           \
    X(VAL, uint32_t, subsystems_disarm, 1, 0, ILC_ENUM_OR(ILC_SUBSYSTEMS), "developer override: ilc_subsystem_bit bits kept disarmed, honoured only while developer = 1; 0 = every subsystem follows the module") \
    X(VAL, uint8_t, hud_enabled, 1, 0, 1, "Battle HUD on/off; off = every element stock (the co-op battle menus stay)") \
    X(VAL, uint8_t, log_expand_control, 1, 0, ILC_ENUM_MAX(ILC_PAD_CONTROLS), "battle log expand button: ilc_pad_control pressed on any seat's pad; 0 none") \
    X(VAL, uint8_t, log_filter, 1, 0, ILC_ENUM_MAX(ILC_LOG_FILTERS), "battle log filter, ilc_log_filter") \
    X(VAL, uint8_t, log_filter_seat, 1, 1, ILC_ACTIVE_SEATS_MAX, "seat the MINE filter shows")          \
    X(VAL, uint32_t, hud_elements, 1, 0, ILC_ENUM_OR(ILC_HUD_ELEMENTS), "ilc_hud_element_bit bits; set = on") \
    X(VAL, uint8_t, shared_camera, 1, 0, 1, "shared group camera; 0 = the game's camera follows P1 (CAMERA stays disarmed)") \
    X(VAL, uint8_t, guest_interactions, 1, 0, 1, "guests use doors, chests, crystals, NPCs and shops; 0 = only P1 does (gateways and loot stay)") \
    X(VAL, uint8_t, town_view, 1, 0, ILC_ENUM_MAX(ILC_TOWN_VIEWS), "who has a body in towns, ilc_town_view") \
    X(VAL, uint8_t, wait_pause, 1, 0, ILC_ENUM_MAX(ILC_WAIT_PAUSES), "when Wait mode stops time for the co-op menus, ilc_wait_pause") \
    X(VAL, uint8_t, guest_pause, 1, 0, 1, "guests may pause the game and open the Party menu; 0 = a guest's PAUSE and PARTY do nothing") \
    X(VAL, uint8_t, log_lines_compact, 1, 1, ILC_LOG_LINES_COMPACT_MAX, "battle log lines in the compact view; at most log_lines_expanded") \
    X(VAL, uint8_t, log_lines_expanded, 1, 5, ILC_LOG_LINES_MAX, "battle log lines in the expanded view") \
    X(VAL, uint8_t, log_fade_s, 1, 3, 15, "seconds an ordinary battle log line holds before it fades (important lines twice as long)") \
    X(BYTES, uint8_t, hud_theme, ILC_THEME_ID_MAX, 0, 0, "HUD theme id: lower-case letters, digits, '-' or '_', zero after it; all zero = the built-in default (legacy-bold). A theme that is missing or broken at run time draws the default")

#define ILC_SETTINGS_ACK_FIELDS(X)                                                                          \
    X(REC, ilc_header, header, 1, 0, 0, "generation = the generation sent")                                 \
    X(VAL, uint32_t, disposition, 1, ILC_ENUM_MIN(ILC_ACK_DISPOSITIONS), ILC_ENUM_MAX(ILC_ACK_DISPOSITIONS), "ilc_ack_disposition") \
    X(VAL, uint32_t, accepted_generation, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, applied_generation, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, staged_pending, 1, 0, 1, "")

#define ILC_COMMAND_FIELDS(X)                                                                               \
    X(REC, ilc_header, header, 1, 0, 0, "record_type COMMAND; generation = request id, non-zero")           \
    X(VAL, uint16_t, command, 1, ILC_ENUM_MIN(ILC_COMMANDS), ILC_ENUM_MAX(ILC_COMMANDS), "ilc_command_id")  \
    X(VAL, uint8_t, seat, 1, 0, ILC_SEATS, "0 = all/none, 1..ACTIVE_SEATS_MAX; allowed values depend on the command") \
    X(PAD, uint8_t, reserved0, 1, 0, 0, "")                                                                 \
    X(VAL, uint32_t, arg0, 1, 0, 0xFFFFFFFFu, "depends on the command")                                     \
    X(VAL, uint32_t, arg1, 1, 0, 0xFFFFFFFFu, "depends on the command")                                     \
    X(PAD, uint8_t, reserved1, 4, 0, 0, "")

#define ILC_HELLO_REPLY_FIELDS(X)                                                                           \
    X(REC, ilc_header, header, 1, 0, 0, "generation 0")                                                     \
    X(VAL, uint32_t, module_major, 1, 0, 0xFFFFFFFFu, "")                                                   \
    X(VAL, uint32_t, module_minor, 1, 0, 0xFFFFFFFFu, "")                                                   \
    X(VAL, uint32_t, module_patch, 1, 0, 0xFFFFFFFFu, "")                                                   \
    X(VAL, uint32_t, state, 1, 0, 0xFFFFFFFFu, "ilc_state")                                                 \
    X(VAL, uint32_t, reason, 1, 0, 0xFFFFFFFFu, "ilc_reason")                                               \
    X(VAL, uint32_t, build_flags, 1, 0, 0xFFFFFFFFu, "ilc_build_flag bits")                                 \
    X(VAL, uint32_t, caller_thread_id, 1, 0, 0xFFFFFFFFu, "")                                               \
    X(VAL, uint32_t, settings_size, 1, 0, 0xFFFFFFFFu, "sizeof(ilc_settings)")                              \
    X(VAL, uint32_t, accepted_generation, 1, 0, 0xFFFFFFFFu, "re-sync after a script restart")              \
    X(VAL, uint32_t, applied_generation, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, ops_mask, 1, 0, 0xFFFFFFFFu, "bit n set = op n implemented (ops 1..31)")               \
    X(PAD, uint8_t, reserved, 4, 0, 0, "")                                                                  \
    X(ARR, uint64_t, source_id, 2, 0, 0, "first 16 bytes of the SHA-256 of the module sources")             \
    X(ARR, uint64_t, schema_id, 2, 0, 0, "first 16 bytes of the SHA-256 of schema/bridge-abi-v1.json (LF)")

#define ILC_STATUS_FIELDS(X)                                                                                \
    X(REC, ilc_header, header, 1, 0, 0, "generation = applied generation")                                  \
    X(VAL, uint32_t, state, 1, 0, 0xFFFFFFFFu, "ilc_state")                                                 \
    X(VAL, uint32_t, reason, 1, 0, 0xFFFFFFFFu, "ilc_reason")                                               \
    X(VAL, uint32_t, build_flags, 1, 0, 0xFFFFFFFFu, "")                                                    \
    X(VAL, uint32_t, caller_thread_id, 1, 0, 0xFFFFFFFFu, "")                                               \
    X(VAL, uint32_t, accepted_generation, 1, 0, 0xFFFFFFFFu, "staged if pending, else applied")             \
    X(VAL, uint32_t, accepted_digest, 1, 0, 0xFFFFFFFFu, "FNV-1a 32 of the accepted record's bytes 16..size") \
    X(VAL, uint32_t, applied_generation, 1, 0, 0xFFFFFFFFu, "0 = native defaults")                          \
    X(VAL, uint32_t, applied_digest, 1, 0, 0xFFFFFFFFu, "FNV-1a 32 of the applied record's bytes 16..size") \
    X(VAL, uint32_t, staged_pending, 1, 0, 1, "")                                                           \
    X(VAL, uint32_t, ready_mask, 1, 0, 0xFFFFFFFFu, "ilc_ready_bit bits")                                   \
    X(VAL, uint32_t, events_pending, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, events_dropped, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, event_next_seq, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, safe_points, 1, 0, 0xFFFFFFFFu, "safe point runs, busy ones included")                 \
    X(VAL, uint32_t, applies, 1, 0, 0xFFFFFFFFu, "")                                                        \
    X(VAL, uint32_t, rejects, 1, 0, 0xFFFFFFFFu, "every negative return except NOT_READY")                  \
    X(VAL, uint32_t, last_reject_op, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, int32_t, last_reject_code, 1, 0, 0, "ilc_error")                                                 \
    X(VAL, uint32_t, last_reject_field, 1, 0, 0xFFFFFFFFu, "field id, 0 if none")                           \
    X(VAL, uint32_t, last_reject_location, 1, 0, 0xFFFFFFFFu, "outer << 8 | inner")                         \
    X(VAL, uint32_t, applied_subsystems, 1, 0, 0xFFFFFFFFu, "the subsystem mask the applied settings ask for: every bit, less the developer disarm and (shared camera off) CAMERA") \
    X(VAL, uint32_t, reason_detail, 1, 0, 0xFFFFFFFFu, "depends on reason (see ilc_reason)")                \
    X(VAL, uint32_t, boot_flags, 1, 0, 0xFFFFFFFFu, "ilc_boot_flag bits")                                   \
    X(VAL, uint8_t, coop_switch, 1, 0, ILC_ENUM_MAX(ILC_COOP_SWITCHES), "ilc_coop_switch: the kill switch") \
    X(VAL, uint8_t, hud_report, 1, 0, 255, "ilc_hud_report_bit bits (the Battle HUD page's status line)") \
    X(PAD, uint8_t, reserved, 2, 0, 0, "")

#define ILC_SEAT_VIEW_FIELDS(X)                                                                             \
    X(VAL, uint8_t, seat, 1, 1, ILC_SEATS, "")                                                              \
    X(VAL, uint8_t, flags, 1, 0, 255, "ilc_seat_flag bits")                                                 \
    X(VAL, uint8_t, configured_character, 1, 0, 255, "")                                                    \
    X(VAL, uint8_t, live_member, 1, 0, 255, "character the seat controls now (0..5); NONE without one or without a session")\
    X(VAL, uint8_t, device_kind, 1, 0, ILC_ENUM_MAX(ILC_DEVICE_KINDS), "")                                  \
    X(VAL, uint8_t, xinput_slot, 1, 0, ILC_XINPUT_SLOTS - 1, "")                                            \
    X(VAL, uint8_t, colour, 1, 0, ILC_ENUM_MAX(ILC_COLOURS), "")                                            \
    X(VAL, uint8_t, seat_ui_sounds, 1, 0, 1, "")                                                            \
    X(VAL, uint32_t, seat_generation, 1, 0, 0xFFFFFFFFu, "incarnation of the seat's character (changes when the game rebuilds it); 0 without one")\
    X(PAD, uint8_t, reserved, 4, 0, 0, "")

#define ILC_SEATS_REPLY_FIELDS(X)                                                                           \
    X(REC, ilc_header, header, 1, 0, 0, "generation = applied generation")                                  \
    X(VAL, uint32_t, session_state, 1, 0, 0xFFFFFFFFu, "ilc_session_state")                                 \
    X(VAL, uint32_t, epoch, 1, 0, 0xFFFFFFFFu, "session epoch: changes at every map jump, load, title screen and game over (0 = no session)")\
    X(VAL, uint32_t, count, 1, 0, ILC_SEATS, "")                                                            \
    X(PAD, uint8_t, reserved, 4, 0, 0, "")                                                                  \
    X(RECS, ilc_seat_view, seats, ILC_SEATS, 0, 0, "")

#define ILC_DEVICE_ENTRY_FIELDS(X)                                                                          \
    X(VAL, uint32_t, entry_id, 1, 0, 0xFFFFFFFFu, "")                                                       \
    X(VAL, uint8_t, kind, 1, 0, ILC_ENUM_MAX(ILC_DEVICE_KINDS), "ilc_device_kind")                          \
    X(VAL, uint8_t, xinput_slot, 1, 0, 255, "0xFF = none")                                                  \
    X(VAL, uint8_t, connected, 1, 0, 1, "")                                                                 \
    X(VAL, uint8_t, owner_seat, 1, 0, ILC_SEATS, "")                                                        \
    X(VAL, uint8_t, flags, 1, 0, 255, "bit 0 = P1 locked")                                                  \
    X(PAD, uint8_t, reserved, 3, 0, 0, "")                                                                  \
    X(REC, ilc_device_identity, sdl, 1, 0, 0, "")

#define ILC_DEVICES_REPLY_FIELDS(X)                                                                         \
    X(REC, ilc_header, header, 1, 0, 0, "size = 32 + count * 148")                                          \
    X(VAL, uint32_t, service_state, 1, 0, ILC_ENUM_MAX(ILC_SERVICE_STATES), "ilc_service_state")            \
    X(VAL, uint32_t, inventory_session, 1, 0, 0xFFFFFFFFu, "")                                              \
    X(VAL, uint32_t, age_ms, 1, 0, 0xFFFFFFFFu, "")                                                         \
    X(VAL, uint32_t, count, 1, 0, ILC_DEVICES_MAX, "entries that follow")                                   \
    X(RECS, ilc_device_entry, entries, ILC_DEVICES_MAX, 0, 0, "only count entries are sent")

#define ILC_EVENT_FIELDS(X)                                                                                 \
    X(VAL, uint32_t, seq, 1, 0, 0xFFFFFFFFu, "monotonic from 1; a gap means events were dropped")           \
    X(VAL, uint16_t, kind, 1, 0, 0xFFFFu, "ilc_event_kind")                                                 \
    X(VAL, uint8_t, seat, 1, 0, ILC_SEATS, "")                                                              \
    X(PAD, uint8_t, reserved, 1, 0, 0, "")                                                                  \
    X(VAL, uint32_t, generation, 1, 0, 0xFFFFFFFFu, "")                                                     \
    X(VAL, int32_t, code, 1, 0, 0, "ilc_error or 0")                                                        \
    X(VAL, uint32_t, field, 1, 0, 0xFFFFFFFFu, "field id or 0")                                             \
    X(VAL, uint32_t, location, 1, 0, 0xFFFFFFFFu, "outer << 8 | inner")                                     \
    X(VAL, uint32_t, a, 1, 0, 0xFFFFFFFFu, "")                                                              \
    X(VAL, uint32_t, b, 1, 0, 0xFFFFFFFFu, "")

#define ILC_EVENTS_REPLY_FIELDS(X)                                                                          \
    X(REC, ilc_header, header, 1, 0, 0, "size = 32 + count * 32")                                           \
    X(VAL, uint32_t, count, 1, 0, ILC_EVENT_QUEUE, "events that follow, oldest first")                      \
    X(VAL, uint32_t, dropped_total, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, next_seq, 1, 0, 0xFFFFFFFFu, "")                                                       \
    X(VAL, uint32_t, pending_after, 1, 0, 0xFFFFFFFFu, "events still queued")                               \
    X(RECS, ilc_event, events, ILC_EVENT_QUEUE, 0, 0, "only count events are sent")

#define ILC_SAFE_POINT_RESULT_FIELDS(X)                                                                     \
    X(REC, ilc_header, header, 1, 0, 0, "generation = applied generation")                                  \
    X(VAL, uint32_t, result, 1, 0, ILC_ENUM_MAX(ILC_SAFE_POINT_RESULTS), "ilc_safe_point_outcome")          \
    X(VAL, uint32_t, applied_generation, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, applied_digest, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, safe_points, 1, 0, 0xFFFFFFFFu, "")

#define ILC_HUD_FOE_FIELDS(X)                                                                            \
    X(VAL, uint64_t, record, 1, 0, 0xFFFFFFFFFFFFFFFFull, "the foe's battle record address as the script sees it (opaque; 0 = unused entry)") \
    X(VAL, uint8_t, flags, 1, 0, ILC_ENUM_OR(ILC_HUD_FOE_FLAGS), "ilc_hud_foe_flag bits")                \
    X(VAL, uint8_t, broken_s, 1, 0, 60, "seconds of Broken left")                                        \
    X(VAL, uint8_t, dent_pct, 1, 0, 50, "Sunder's Defense dent, percent of the starting value")          \
    X(VAL, uint8_t, tired, 1, 0, 4, "guard fatigue steps")                                               \
    X(VAL, uint8_t, spotter_seat, 1, 0, ILC_SEATS, "the seat whose member set the Spotter mark, 0 unknown") \
    X(VAL, uint8_t, crown_element, 1, 0, 255, "element 0..7 of a crowned boss, 255 none")                \
    X(PAD, uint8_t, reserved, 2, 0, 0, "")

#define ILC_HUD_WORD_FIELDS(X)                                                                           \
    X(VAL, uint16_t, member, 1, 0, 0xFFFFu, "party id 0..39; the entry all zero = unused")                     \
    X(VAL, uint8_t, kind, 1, 0, ILC_ENUM_MAX(ILC_HUD_WORD_KINDS), "ilc_hud_word_kind")                   \
    X(VAL, uint8_t, value, 1, 0, 255, "depends on kind")

#define ILC_HUD_PROC_FIELDS(X)                                                                           \
    X(VAL, uint64_t, record, 1, 0, 0xFFFFFFFFFFFFFFFFull, "the battle record of the unit whose hit fired it (0 = unused entry)") \
    X(VAL, uint8_t, tag, 1, 0, ILC_ENUM_MAX(ILC_HUD_TAGS), "ilc_hud_tag")                                \
    X(PAD, uint8_t, reserved, 3, 0, 0, "")                                                               \
    X(VAL, uint32_t, age_ms, 1, 0, ILC_HUD_PROC_AGE_MAX_MS, "how long ago it fired, at the push")

#define ILC_HUD_STATES_FIELDS(X)                                                                         \
    X(REC, ilc_header, header, 1, 0, 0, "record_type HUD_STATES; generation = the script's push counter") \
    X(VAL, uint8_t, foe_count, 1, 0, ILC_HUD_FOES, "foes used")                                          \
    X(VAL, uint8_t, word_count, 1, 0, ILC_HUD_WORDS, "words used")                                       \
    X(VAL, uint8_t, proc_count, 1, 0, ILC_HUD_PROCS, "procs used")                                       \
    X(PAD, uint8_t, reserved, 5, 0, 0, "")                                                               \
    X(RECS, ilc_hud_foe, foes, ILC_HUD_FOES, 0, 0, "the first foe_count are used, the rest all zero")    \
    X(RECS, ilc_hud_word, words, ILC_HUD_WORDS, 0, 0, "the first word_count are used, the rest all zero") \
    X(RECS, ilc_hud_proc, procs, ILC_HUD_PROCS, 0, 0, "the first proc_count are used, the rest all zero")

#define ILC_THEME_ENTRY_FIELDS(X)                                                                        \
    X(BYTES, uint8_t, id, ILC_THEME_ID_MAX, 0, 0, "theme id (lower-case letters, digits, '-', '_'), zero after it") \
    X(BYTES, uint8_t, name, ILC_THEME_NAME_MAX, 0, 0, "the theme's name, printable ASCII, zero after it") \
    X(VAL, uint8_t, flags, 1, 0, ILC_ENUM_OR(ILC_THEME_FLAGS), "ilc_theme_flag bits")                   \
    X(PAD, uint8_t, reserved, 3, 0, 0, "")

#define ILC_THEMES_REPLY_FIELDS(X)                                                                       \
    X(REC, ilc_header, header, 1, 0, 0, "record_type THEMES; generation = applied settings generation") \
    X(VAL, uint32_t, count, 1, 0, ILC_THEMES_MAX, "entries in use: the built-ins first, then the valid player themes") \
    X(VAL, uint32_t, refused, 1, 0, 0xFFFFFFFFu, "player theme folders refused in the last scan (each named in hook.log)") \
    X(VAL, uint32_t, scans, 1, 0, 0xFFFFFFFFu, "completed scans of the themes folder (0: the first is still running)") \
    X(PAD, uint8_t, reserved, 4, 0, 0, "")                                                               \
    X(RECS, ilc_theme_entry, entries, ILC_THEMES_MAX, 0, 0, "the first count are used, the rest all zero")

#define ILC_COUNTERS_FIELDS(X)                                                                              \
    X(REC, ilc_header, header, 1, 0, 0, "record_type COUNTERS; generation = applied generation")            \
    X(VAL, uint32_t, layout, 1, 0, 0xFFFFFFFFu, "ILC_COUNTERS_LAYOUT of the writer")                        \
    X(VAL, uint32_t, now_ms, 1, 0, 0xFFFFFFFFu, "module clock (low 32 bits); every *_ms field below is on it") \
    X(VAL, uint32_t, caller_thread_id, 1, 0, 0xFFFFFFFFu, "the thread of this call")                        \
    X(VAL, uint32_t, init_thread_id, 1, 0, 0xFFFFFFFFu, "the thread that called FF12Hinit (the game's main thread)") \
    X(VAL, uint32_t, init_done_thread_id, 1, 0, 0xFFFFFFFFu, "the thread that called FF12HinitDone (0 = not yet)") \
    X(VAL, uint32_t, coop_thread_id, 1, 0, 0xFFFFFFFFu, "the init thread that stays on as the co-op thread (0 = none)") \
    X(VAL, uint32_t, mode, 1, 0, 0xFFFFFFFFu, "0 = dry (every co-op detour stock), 1 = live")               \
    X(VAL, uint32_t, live_at_ms, 1, 0, 0xFFFFFFFFu, "module clock when the layer went live (0 = still dry)") \
    X(VAL, uint32_t, liveness_armed, 1, 0, 0xFFFFFFFFu, "the liveness record is armed (1)")                 \
    X(VAL, uint32_t, liveness_epoch, 1, 0, 0xFFFFFFFFu, "epoch of the last heartbeat")                      \
    X(VAL, uint32_t, liveness_age_ms, 1, 0, 0xFFFFFFFFu, "time since the last heartbeat; 0xFFFFFFFF = none yet") \
    X(VAL, uint32_t, liveness_fresh, 1, 0, 0xFFFFFFFFu, "1 = a detour may act now (coherent, armed, epoch set, heartbeat at most 2 s old)") \
    X(VAL, uint32_t, stale_fallbacks, 1, 0, 0xFFFFFFFFu, "detour entries with an open gate that ran stock because the liveness sample was stale or torn") \
    X(VAL, uint32_t, stale_ticks, 1, 0, 0xFFFFFFFFu, "UI ticks that ran the stale clean-up instead of the tick") \
    X(VAL, uint32_t, stale_releases, 1, 0, 0xFFFFFFFFu, "Wait pauses the stale clean-up gave back")         \
    X(VAL, uint32_t, subsystems_applied, 1, 0, 0xFFFFFFFFu, "the subsystem mask the applied settings ask for (as status applied_subsystems)") \
    X(VAL, uint32_t, subsystems_gates, 1, 0, 0xFFFFFFFFu, "the subsystem mask the arm gates use now (0 while co-op is off)") \
    X(VAL, uint32_t, subsystems_faulted, 1, 0, 0xFFFFFFFFu, "latched fault bits (0x40000000 = the CORE rows)") \
    X(VAL, uint32_t, subsystems_live, 1, 0, 0xFFFFFFFFu, "bit set = that subsystem may act now (live, armed, not faulted)") \
    X(VAL, uint32_t, beats, 1, 0, 0xFFFFFFFFu, "co-op thread beats")                                        \
    X(VAL, uint32_t, beat_max_us, 1, 0, 0xFFFFFFFFu, "longest beat (its work; the wait between beats excluded)") \
    X(VAL, uint32_t, beat_gap_last_ms, 1, 0, 0xFFFFFFFFu, "time between the last two beat starts")          \
    X(VAL, uint32_t, beat_gap_max_ms, 1, 0, 0xFFFFFFFFu, "longest time between two beat starts (a stalled thread shows here)") \
    X(VAL, uint32_t, stall_requests, 1, 0, 0xFFFFFFFFu, "STALL_COOP_THREAD commands accepted")              \
    X(VAL, uint32_t, stalls_done, 1, 0, 0xFFFFFFFFu, "stalls the co-op thread finished")                    \
    X(VAL, uint32_t, stall_last_ms, 1, 0, 0xFFFFFFFFu, "milliseconds the last stall slept")                 \
    X(VAL, uint32_t, tick_count, 1, 0, 0xFFFFFFFFu, "UI-tick handler runs")                                 \
    X(VAL, uint32_t, tick_max_us, 1, 0, 0xFFFFFFFFu, "longest UI-tick handler run, the stock call excluded (0 until the timer is calibrated)") \
    X(VAL, uint32_t, tick_reentered, 1, 0, 0xFFFFFFFFu, "tick runs refused because the handler was already running") \
    X(VAL, uint32_t, tick_settings_applied, 1, 0, 0xFFFFFFFFu, "safe points that applied staged settings")  \
    X(VAL, uint32_t, tick_safe_point_busy, 1, 0, 0xFFFFFFFFu, "safe points that found the bridge lock taken") \
    X(VAL, uint32_t, tick_mask_applied, 1, 0, 0xFFFFFFFFu, "subsystem masks handed to the arm gates")       \
    X(VAL, uint32_t, tick_mask_deferred, 1, 0, 0xFFFFFFFFu, "mask hand-offs that found the layer lock taken (retried next frame)") \
    X(VAL, uint32_t, tick_live_switches, 1, 0, 0xFFFFFFFFu, "dry to live switches (0 or 1)")                \
    X(VAL, uint32_t, tick_live_deferred, 1, 0, 0xFFFFFFFFu, "live switches that found the layer lock taken (retried next frame)") \
    X(VAL, uint32_t, tick_jobs_drained, 1, 0, 0xFFFFFFFFu, "game-thread jobs taken while live")             \
    X(VAL, uint32_t, tick_jobs_flushed, 1, 0, 0xFFFFFFFFu, "game-thread jobs refused while dry")            \
    X(VAL, uint32_t, bridge_calls, 1, 0, 0xFFFFFFFFu, "ILC_Bridge calls")                                   \
    X(VAL, uint32_t, bridge_max_us, 1, 0, 0xFFFFFFFFu, "longest ILC_Bridge call")                           \
    X(VAL, uint32_t, poll_calls, 1, 0, 0xFFFFFFFFu, "query calls (GET_STATUS, GET_SEATS, GET_DEVICES, POP_EVENTS, GET_SETTINGS, GET_COUNTERS)") \
    X(VAL, uint32_t, poll_max_us, 1, 0, 0xFFFFFFFFu, "longest query call")                                  \
    X(VAL, uint32_t, safe_points, 1, 0, 0xFFFFFFFFu, "settings safe point runs, busy ones included")        \
    X(VAL, uint32_t, applies, 1, 0, 0xFFFFFFFFu, "settings records applied")                                \
    X(VAL, uint32_t, rejects, 1, 0, 0xFFFFFFFFu, "every negative bridge return except NOT_READY")           \
    X(VAL, uint32_t, last_reject_op, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, int32_t, last_reject_code, 1, 0, 0, "ilc_error")                                                 \
    X(VAL, uint32_t, events_pending, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, events_dropped, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, patch_rows, 1, 0, 0xFFFFFFFFu, "PATCH rows in the site table")                         \
    X(VAL, uint32_t, hooks_installed, 1, 0, 0xFFFFFFFFu, "rows the hook transaction hooked")                \
    X(VAL, uint32_t, gated_off, 1, 0, 0xFFFFFFFFu, "PATCH rows skipped because their GATE row did not match") \
    X(VAL, uint32_t, info_mismatch, 1, 0, 0xFFFFFFFFu, "INFO rows that differ (information only)")          \
    X(VAL, uint32_t, install_attempts, 1, 0, 0xFFFFFFFFu, "hook transactions tried (a busy probe is retried)") \
    X(VAL, uint32_t, quiesce_retries, 1, 0, 0xFFFFFFFFu, "busy probes before the last transaction's result") \
    X(VAL, uint32_t, threads_probed, 1, 0, 0xFFFFFFFFu, "threads the last probe suspended and inspected")   \
    X(VAL, uint32_t, island_commits, 1, 0, 0xFFFFFFFFu, "call-row rel32 sets written")                      \
    X(VAL, uint32_t, island_busy, 1, 0, 0xFFFFFFFFu, "call-row freezes that found a thread inside a rel32") \
    X(VAL, uint32_t, detour_entered, 1, 0, 0xFFFFFFFFu, "slow-path entries, all rows")                      \
    X(VAL, uint32_t, detour_stock, 1, 0, 0xFFFFFFFFu, "... that ran the original because a check failed")   \
    X(VAL, uint32_t, detour_handled, 1, 0, 0xFFFFFFFFu, "... that ran the handler")                         \
    X(VAL, uint32_t, detour_declined, 1, 0, 0xFFFFFFFFu, "... whose handler chose stock")                   \
    X(VAL, uint32_t, jobs_pushed, 1, 0, 0xFFFFFFFFu, "game-thread jobs queued")                             \
    X(VAL, uint32_t, jobs_push_full, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, jobs_push_refused, 1, 0, 0xFFFFFFFFu, "")                                              \
    X(VAL, uint32_t, jobs_ran, 1, 0, 0xFFFFFFFFu, "")                                                       \
    X(VAL, uint32_t, jobs_rejected, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, jobs_reaped, 1, 0, 0xFFFFFFFFu, "")                                                    \
    X(VAL, uint32_t, coop_switch, 1, 0, 0xFFFFFFFFu, "ilc_coop_switch")                                     \
    X(VAL, uint32_t, kill_presses, 1, 0, 0xFFFFFFFFu, "kill-switch edges (keys and commands)")              \
    X(VAL, uint32_t, kills, 1, 0, 0xFFFFFFFFu, "ordered disarms completed")                                 \
    X(VAL, uint32_t, revives, 1, 0, 0xFFFFFFFFu, "")                                                        \
    X(VAL, uint32_t, released_by_settle, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, released_by_cap, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, last_release_ms, 1, 0, 0xFFFFFFFFu, "duration of the last release wait")               \
    X(VAL, uint32_t, toggles, 1, 0, 0xFFFFFFFFu, "seat toggles applied")                                    \
    X(VAL, uint32_t, toggles_ignored, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, mask_busy, 1, 0, 0xFFFFFFFFu, "co-op switch mask hand-offs that found the layer lock taken") \
    X(VAL, uint32_t, capture_held, 1, 0, 0xFFFFFFFFu, "kill or toggle key edges not acted on while the on/off key was being captured") \
    X(VAL, uint32_t, capture_capped, 1, 0, 0xFFFFFFFFu, "capture holds ended by their cap")                 \
    X(VAL, uint32_t, capture_word, 1, 0, 0xFFFFFFFFu, "a capture that reads the keyboard (on/off key or keyboard seat): bit 0 running, bits 1..31 a serial") \
    X(VAL, uint32_t, session_epoch_bumps, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, session_read_failures, 1, 0, 0xFFFFFFFFu, "")                                          \
    X(VAL, uint32_t, party_requests, 1, 0, 0xFFFFFFFFu, "seat change requests taken")                       \
    X(VAL, uint32_t, party_applied, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, party_refused, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, party_timed_out, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, party_watcher_changes, 1, 0, 0xFFFFFFFFu, "leader changes made outside co-op jobs")    \
    X(VAL, uint32_t, party_external_swaps, 1, 0, 0xFFFFFFFFu, "")                                           \
    X(VAL, uint32_t, party_ko_promotions, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, party_town_rebuilds, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, party_town_takes, 1, 0, 0xFFFFFFFFu, "")                                               \
    X(VAL, uint32_t, towns_rebuilds, 1, 0, 0xFFFFFFFFu, "town rebuild jobs that ran")                       \
    X(VAL, uint32_t, towns_gave_up, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, canvas_reached, 1, 0, 0xFFFFFFFFu, "frame hook runs with its gate open")               \
    X(VAL, uint32_t, canvas_drawn, 1, 0, 0xFFFFFFFFu, "frames replayed to the end")                         \
    X(VAL, uint32_t, canvas_published, 1, 0, 0xFFFFFFFFu, "frames the producer published")                  \
    X(VAL, uint32_t, canvas_declined_off, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, canvas_declined_frame, 1, 0, 0xFFFFFFFFu, "")                                          \
    X(VAL, uint32_t, canvas_declined_context, 1, 0, 0xFFFFFFFFu, "")                                        \
    X(VAL, uint32_t, canvas_declined_budget, 1, 0, 0xFFFFFFFFu, "over a ring limit or the ring unreadable") \
    X(VAL, uint32_t, canvas_ring_gaps, 1, 0, 0xFFFFFFFFu, "")                                               \
    X(VAL, uint32_t, canvas_faults, 1, 0, 0xFFFFFFFFu, "guarded faults (RENDER latched)")                   \
    X(VAL, uint32_t, canvas_frame_use, 1, 0, 0xFFFFFFFFu, "ring bytes used between the last two hook calls") \
    X(VAL, uint32_t, canvas_peak, 1, 0, 0xFFFFFFFFu, "decaying peak of canvas_frame_use")                   \
    X(VAL, uint32_t, canvas_capacity, 1, 0, 0xFFFFFFFFu, "ring size seen")                                  \
    X(VAL, uint32_t, driver_frames, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, driver_composed, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, driver_torn, 1, 0, 0xFFFFFFFFu, "")                                                    \
    X(VAL, uint32_t, driver_pass_faults, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, driver_dropped_frames, 1, 0, 0xFFFFFFFFu, "presented frames that left a seat's open window out") \
    X(VAL, uint32_t, driver_menus_no_view, 1, 0, 0xFFFFFFFFu, "")                                           \
    X(VAL, uint32_t, presenter_age_ms, 1, 0, 0xFFFFFFFFu, "time since the co-op menus were last drawn; 0xFFFFFFFF = never") \
    X(VAL, uint32_t, native_hud_jobs, 1, 0, 0xFFFFFFFFu, "native HUD jobs that ran")                        \
    X(VAL, uint32_t, native_hud_give_backs, 1, 0, 0xFFFFFFFFu, "")                                          \
    X(VAL, uint32_t, ti_native_closed, 1, 0, 0xFFFFFFFFu, "native TARGET INFO opens turned into a close")   \
    X(VAL, uint32_t, ti_native_stock, 1, 0, 0xFFFFFFFFu, "native TARGET INFO opens left alone")             \
    X(VAL, uint32_t, ti_give_opens, 1, 0, 0xFFFFFFFFu, "give-back: pending creates written")                \
    X(VAL, uint32_t, ti_give_opened, 1, 0, 0xFFFFFFFFu, "")                                                 \
    X(VAL, uint32_t, ti_give_cancels, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, ti_give_giveups, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, ti_give_rehides, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, ti_pin_checks, 1, 0, 0xFFFFFFFFu, "")                                                  \
    X(VAL, uint32_t, ti_pin_off, 1, 0, 0xFFFFFFFFu, "")                                                     \
    X(VAL, uint32_t, ti_aims, 1, 0, 0xFFFFFFFFu, "")                                                        \
    X(VAL, uint32_t, ti_gated, 1, 0, 0xFFFFFFFFu, "")                                                       \
    X(VAL, uint32_t, ti_pins, 1, 0, 0xFFFFFFFFu, "0 unknown, 1 ok, 2 off")                                  \
    X(VAL, int32_t, ti_pin_row, 1, 0, 0, "first pin row found off, -1 none")                                \
    X(VAL, uint32_t, menu_opens, 1, 0, 0xFFFFFFFFu, "co-op battle menus opened")                            \
    X(VAL, uint32_t, menu_party_requests, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, menu_p1_givebacks, 1, 0, 0xFFFFFFFFu, "")                                              \
    X(VAL, uint32_t, menu_owns_p1, 1, 0, 0xFFFFFFFFu, "the co-op menus own Player 1's menus (1)")           \
    X(VAL, uint32_t, menu_presenter_live, 1, 0, 0xFFFFFFFFu, "")                                            \
    X(VAL, uint32_t, iso_background, 1, 0, 0xFFFFFFFFu, "host visits made neutral while the game runs unfocused") \
    X(VAL, uint32_t, iso_masked, 1, 0, 0xFFFFFFFFu, "")                                                     \
    X(VAL, uint32_t, iso_substituted, 1, 0, 0xFFFFFFFFu, "")                                                \
    X(VAL, uint32_t, iso_probe_failed, 1, 0, 0xFFFFFFFFu, "")                                               \
    X(VAL, uint32_t, focus_forced, 1, 0, 0xFFFFFFFFu, "application held active while unfocused")            \
    X(VAL, uint32_t, focus_probe_failed, 1, 0, 0xFFFFFFFFu, "")                                             \
    X(VAL, uint32_t, frame_hook_us_last, 1, 0, 0xFFFFFFFFu, "the HUD frame hook's last call: our work and the replay's game draw calls, the native log draw excluded (0 until the timer is calibrated)") \
    X(VAL, uint32_t, frame_hook_us_max, 1, 0, 0xFFFFFFFFu, "the HUD frame hook's longest call")             \
    X(VAL, uint32_t, frame_hook_us_avg, 1, 0, 0xFFFFFFFFu, "the HUD frame hook's average call since start") \
    X(VAL, uint32_t, frame_hook_calls, 1, 0, 0xFFFFFFFFu, "HUD frame hook calls timed")                     \
    X(VAL, uint32_t, canvas_atlas_lookups, 1, 0, 0xFFFFFFFFu, "atlas texture lookups by name (the atlas cache's misses)") \
    X(VAL, uint32_t, canvas_text_last, 1, 0, 0xFFFFFFFFu, "texts replayed on the last frame drawn")          \
    X(VAL, uint32_t, canvas_chars_last, 1, 0, 0xFFFFFFFFu, "characters of those texts")                     \
    X(VAL, uint32_t, probe_queries, 1, 0, 0xFFFFFFFFu, "VirtualQuery calls of the fault-safe probe, every thread") \
    X(VAL, uint32_t, probe_hot_hits, 1, 0, 0xFFFFFFFFu, "hot-path game reads and writes served from a cached region") \
    X(VAL, uint32_t, probe_hot_misses, 1, 0, 0xFFFFFFFFu, "hot-path game reads and writes that probed their region") \
    X(VAL, uint32_t, canvas_ring_ours_last, 1, 0, 0xFFFFFFFFu, "render-ring bytes the last replay with sprites or bars added") \
    X(VAL, uint32_t, canvas_zero_output, 1, 0, 0xFFFFFFFFu, "replays that drew sprites or bars but added nothing to the render ring") \
    X(VAL, uint32_t, canvas_child_space, 1, 0, 0xFFFFFFFFu, "frames drawn in the HUD root's child space (its own context has no matrix)") \
    X(VAL, uint32_t, canvas_declined_matrix, 1, 0, 0xFFFFFFFFu, "frames not drawn: no usable draw matrix") \
    X(VAL, uint32_t, probe_hot_failed, 1, 0, 0xFFFFFFFFu, "hot-path reads refused because their region is not readable")

// The list of records, dependencies first: S(type name, struct id, field list, doc).
#define ILC_STRUCTS(S)                                                                                      \
    S(ilc_header, 1, ILC_HEADER_FIELDS, "starts every record")                                              \
    S(ilc_binding, 2, ILC_BINDING_FIELDS, "")                                                               \
    S(ilc_device_identity, 3, ILC_IDENTITY_FIELDS, "SDL device identity")                                   \
    S(ilc_seat_device, 4, ILC_SEAT_DEVICE_FIELDS, "")                                                       \
    S(ilc_seat_settings, 5, ILC_SEAT_SETTINGS_FIELDS, "")                                                   \
    S(ilc_device_alias, 6, ILC_ALIAS_FIELDS, "")                                                            \
    S(ilc_economy, 7, ILC_ECONOMY_FIELDS, "")                                                               \
    S(ilc_settings, 8, ILC_SETTINGS_FIELDS, "SET_SETTINGS in, GET_SETTINGS out")                            \
    S(ilc_settings_ack, 9, ILC_SETTINGS_ACK_FIELDS, "SET_SETTINGS out")                                     \
    S(ilc_command, 10, ILC_COMMAND_FIELDS, "COMMAND in")                                                    \
    S(ilc_hello_reply, 11, ILC_HELLO_REPLY_FIELDS, "HELLO out")                                             \
    S(ilc_status, 12, ILC_STATUS_FIELDS, "GET_STATUS out")                                                  \
    S(ilc_seat_view, 13, ILC_SEAT_VIEW_FIELDS, "")                                                          \
    S(ilc_seats_reply, 14, ILC_SEATS_REPLY_FIELDS, "GET_SEATS out")                                         \
    S(ilc_device_entry, 15, ILC_DEVICE_ENTRY_FIELDS, "")                                                    \
    S(ilc_devices_reply, 16, ILC_DEVICES_REPLY_FIELDS, "GET_DEVICES out (variable length)")                 \
    S(ilc_event, 17, ILC_EVENT_FIELDS, "")                                                                  \
    S(ilc_events_reply, 18, ILC_EVENTS_REPLY_FIELDS, "POP_EVENTS out (variable length)")                    \
    S(ilc_safe_point_result, 19, ILC_SAFE_POINT_RESULT_FIELDS, "TEST_SAFE_POINT out")                       \
    S(ilc_counters, 20, ILC_COUNTERS_FIELDS, "GET_COUNTERS out")                                         \
    S(ilc_hud_foe, 21, ILC_HUD_FOE_FIELDS, "")                                                           \
    S(ilc_hud_word, 22, ILC_HUD_WORD_FIELDS, "")                                                         \
    S(ilc_hud_proc, 23, ILC_HUD_PROC_FIELDS, "")                                                         \
    S(ilc_hud_states, 24, ILC_HUD_STATES_FIELDS, "PUSH_HUD_STATES in")                                   \
    S(ilc_theme_entry, 25, ILC_THEME_ENTRY_FIELDS, "")                                                   \
    S(ilc_themes_reply, 26, ILC_THEMES_REPLY_FIELDS, "GET_THEMES out")

// ---------------------------------------------------------------------------------------------------------
// Expansion into C++.
#define ILC_CONST_DECL(name, value, doc) inline constexpr uint32_t ILC_##name = value;
ILC_CONSTANTS(ILC_CONST_DECL)
#undef ILC_CONST_DECL

#define ILC_ENUM_ITEM(prefix, name, value, doc) prefix##name = value,
#define ILC_ENUM_DECL(json, type, underlying, LIST) enum type : underlying { LIST(ILC_ENUM_ITEM) };
ILC_ENUMS(ILC_ENUM_DECL)
#undef ILC_ENUM_DECL
#undef ILC_ENUM_ITEM

// Ranges derived from the enum lists, so a field's limits follow its enum: ILC_ENUM_MAX(ILC_COLOURS) is the
// largest colour value, ILC_ENUM_COUNT(ILC_ACTIONS_ENUM) the number of actions.
#define ILC_ENUM_VALUE(prefix, name, value, doc) static_cast<long long>(value),
template <unsigned long N>
constexpr long long ilc_enum_max(const long long (&values)[N])
{
    long long m = values[0];
    for (unsigned long i = 1; i < N; ++i) {
        m = values[i] > m ? values[i] : m;
    }
    return m;
}
template <unsigned long N>
constexpr long long ilc_enum_min(const long long (&values)[N])
{
    long long m = values[0];
    for (unsigned long i = 1; i < N; ++i) {
        m = values[i] < m ? values[i] : m;
    }
    return m;
}
template <unsigned long N>
constexpr unsigned long ilc_enum_count(const long long (&)[N])
{
    return N;
}
#define ILC_ENUM_MAX(LIST) ilc_enum_max({LIST(ILC_ENUM_VALUE)})
#define ILC_ENUM_MIN(LIST) ilc_enum_min({LIST(ILC_ENUM_VALUE)})
#define ILC_ENUM_COUNT(LIST) ilc_enum_count({LIST(ILC_ENUM_VALUE)})
template <unsigned long N>
constexpr unsigned long long ilc_enum_or(const long long (&values)[N])
{
    unsigned long long m = 0;
    for (unsigned long i = 0; i < N; ++i) {
        m |= static_cast<unsigned long long>(values[i]);
    }
    return m;
}
#define ILC_ENUM_OR(LIST) ilc_enum_or({LIST(ILC_ENUM_VALUE)})

// Enum lists that index arrays or bit masks must be dense and match their constants.
static_assert(ILC_ENUM_MIN(ILC_ACTIONS_ENUM) == 0 && ILC_ENUM_MAX(ILC_ACTIONS_ENUM) + 1 == ILC_ACTIONS &&
                  ILC_ENUM_COUNT(ILC_ACTIONS_ENUM) == ILC_ACTIONS,
              "ILC_ACTIONS must equal the dense action list");
static_assert(ILC_P1_ACTION_MASK < (1ull << ILC_ACTIONS), "P1 mask names an action that does not exist");
static_assert(ILC_CHAR_VAAN == 0 && ILC_CHAR_PENELO + 1 == ILC_CHARACTERS &&
                  ILC_ENUM_COUNT(ILC_CHARACTERS_ENUM) == ILC_CHARACTERS + 2,
              "ILC_CHARACTERS must equal the regular characters (plus AUTO and NONE)");
static_assert(ILC_ENUM_MIN(ILC_COLOURS) == 0 && ILC_ENUM_MAX(ILC_COLOURS) + 1 == ILC_ENUM_COUNT(ILC_COLOURS),
              "colours are dense palette indices");
static_assert(ILC_ENUM_MIN(ILC_PAD_CONTROLS) == 1 && ILC_ENUM_MAX(ILC_PAD_CONTROLS) == ILC_ENUM_COUNT(ILC_PAD_CONTROLS),
              "pad controls are dense from 1");
static_assert(ILC_ENUM_MIN(ILC_COMMANDS) == 1 && ILC_ENUM_MAX(ILC_COMMANDS) == ILC_ENUM_COUNT(ILC_COMMANDS),
              "commands are dense from 1");
static_assert(ILC_ENUM_MAX(ILC_BINDING_KINDS) + 1 == ILC_ENUM_COUNT(ILC_BINDING_KINDS) &&
                  ILC_ENUM_MAX(ILC_DEVICE_KINDS) + 1 == ILC_ENUM_COUNT(ILC_DEVICE_KINDS),
              "binding and device kinds are dense from 0");
static_assert(ILC_ENUM_OR(ILC_SUBSYSTEMS) + 1 == (1ull << ILC_ENUM_COUNT(ILC_SUBSYSTEMS)),
              "subsystem bits are one bit each, dense from bit 0, so the mask range is exactly the defined bits");
static_assert(ILC_ENUM_OR(ILC_HUD_ELEMENTS) + 1 == (1ull << ILC_ENUM_COUNT(ILC_HUD_ELEMENTS)),
              "Battle HUD element bits are one bit each, dense from bit 0");
static_assert(ILC_ENUM_MIN(ILC_LOG_FILTERS) == 0 && ILC_ENUM_MAX(ILC_LOG_FILTERS) + 1 == ILC_ENUM_COUNT(ILC_LOG_FILTERS),
              "log filters are dense from 0");
static_assert(ILC_ENUM_MIN(ILC_TOWN_VIEWS) == 0 && ILC_ENUM_MAX(ILC_TOWN_VIEWS) + 1 == ILC_ENUM_COUNT(ILC_TOWN_VIEWS) &&
                  ILC_ENUM_MIN(ILC_WAIT_PAUSES) == 0 && ILC_ENUM_MAX(ILC_WAIT_PAUSES) + 1 == ILC_ENUM_COUNT(ILC_WAIT_PAUSES),
              "town views and Wait pause rules are dense from 0");
static_assert(ILC_ACTIVE_SEATS_MAX >= 2 && ILC_ACTIVE_SEATS_MAX <= ILC_SEATS, "active seats fit the seat arrays");

#define ILC_DECL_VAL(type, name, count) type name;
#define ILC_DECL_ARR(type, name, count) type name[count];
#define ILC_DECL_REC(type, name, count) struct type name;
#define ILC_DECL_RECS(type, name, count) struct type name[count];
#define ILC_DECL_PAD(type, name, count) uint8_t name[count];
#define ILC_DECL_BYTES(type, name, count) uint8_t name[count];
#define ILC_FIELD_DECL(kind, type, name, count, lo, hi, doc) ILC_DECL_##kind(type, name, count)
#define ILC_STRUCT_DECL(type, id, LIST, doc) struct type { LIST(ILC_FIELD_DECL) };
ILC_STRUCTS(ILC_STRUCT_DECL)
#undef ILC_STRUCT_DECL
#undef ILC_FIELD_DECL

// Struct ids (the high byte of a field id): ilc_settings_struct_id and so on.
#define ILC_STRUCT_ID_DECL(type, id, LIST, doc) inline constexpr uint32_t type##_struct_id = id;
ILC_STRUCTS(ILC_STRUCT_ID_DECL)
#undef ILC_STRUCT_ID_DECL

// Layout locks. tools/bridge_schema.cpp also proves that no struct has implicit padding.
static_assert(sizeof(ilc_header) == 16, "ilc_header");
static_assert(sizeof(ilc_binding) == 2, "ilc_binding");
static_assert(sizeof(ilc_device_identity) == 136, "ilc_device_identity");
static_assert(sizeof(ilc_seat_device) == 140, "ilc_seat_device");
static_assert(sizeof(ilc_seat_settings) == 308, "ilc_seat_settings");
static_assert(sizeof(ilc_device_alias) == 140, "ilc_device_alias");
static_assert(sizeof(ilc_economy) == 8, "ilc_economy");
static_assert(sizeof(ilc_settings) == 3572, "ilc_settings");
static_assert(sizeof(ilc_settings_ack) == 32, "ilc_settings_ack");
static_assert(sizeof(ilc_command) == 32, "ilc_command");
static_assert(sizeof(ilc_hello_reply) == 96, "ilc_hello_reply");
static_assert(sizeof(ilc_status) == 112, "ilc_status");
static_assert(sizeof(ilc_seat_view) == 16, "ilc_seat_view");
static_assert(sizeof(ilc_seats_reply) == 96, "ilc_seats_reply");
static_assert(sizeof(ilc_device_entry) == 148, "ilc_device_entry");
static_assert(sizeof(ilc_devices_reply) == 32 + 16 * 148, "ilc_devices_reply");
static_assert(sizeof(ilc_event) == 32, "ilc_event");
static_assert(sizeof(ilc_events_reply) == 32 + 64 * 32, "ilc_events_reply");
static_assert(sizeof(ilc_safe_point_result) == 32, "ilc_safe_point_result");
static_assert(sizeof(ilc_counters) == 16 + 153 * 4, "ilc_counters");
static_assert(sizeof(ilc_hud_foe) == 16, "ilc_hud_foe");
static_assert(sizeof(ilc_hud_word) == 4, "ilc_hud_word");
static_assert(sizeof(ilc_hud_proc) == 16, "ilc_hud_proc");
static_assert(sizeof(ilc_hud_states) == 24 + 8 * 16 + 4 * 4 + 8 * 16, "ilc_hud_states");
static_assert(ILC_ENUM_MIN(ILC_HUD_WORD_KINDS) == 0 && ILC_ENUM_MAX(ILC_HUD_WORD_KINDS) + 1 == ILC_ENUM_COUNT(ILC_HUD_WORD_KINDS) &&
                  ILC_ENUM_MIN(ILC_HUD_TAGS) == 0 && ILC_ENUM_MAX(ILC_HUD_TAGS) + 1 == ILC_ENUM_COUNT(ILC_HUD_TAGS),
              "HUD word kinds and tags are dense from 0");
static_assert(ILC_ENUM_OR(ILC_HUD_FOE_FLAGS) == 0xFF, "the foe flags fill one byte");
static_assert(sizeof(ilc_theme_entry) == ILC_THEME_ID_MAX + ILC_THEME_NAME_MAX + 4, "ilc_theme_entry");
static_assert(sizeof(ilc_themes_reply) == 32 + ILC_THEMES_MAX * sizeof(ilc_theme_entry), "ilc_themes_reply");
static_assert(sizeof(ilc_themes_reply) <= ILC_OUT_CAP_RECOMMENDED, "the theme list fits the recommended output");
static_assert(ILC_THEMES_MAX > 3, "the theme list holds the three built-ins and player themes");
static_assert(ILC_SEAT_BINDINGS == ILC_ACTIONS * ILC_BINDINGS_PER_ACTION, "binding table");
static_assert(sizeof(ilc_settings) <= ILC_OUT_CAP_RECOMMENDED, "recommended output size");
static_assert(sizeof(ilc_devices_reply) <= ILC_OUT_CAP_RECOMMENDED, "recommended output size");
static_assert(sizeof(ilc_events_reply) <= ILC_OUT_CAP_RECOMMENDED, "recommended output size");
static_assert(sizeof(ilc_counters) <= ILC_OUT_CAP_RECOMMENDED, "recommended output size");

extern "C" int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t in_len, void* out, uint64_t out_cap);
