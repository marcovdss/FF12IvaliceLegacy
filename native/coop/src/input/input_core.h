// Ivalice Legacy co-op module: input service core (device tracking, identities, seat resolution, frames).
//
// Pure logic over the two device APIs in input_api.h: no thread, no clock, no Windows call. The caller passes the
// time of each tick, so host tests can drive it tick by tick. One core instance is owned by one thread; the
// service (input_service.cpp) publishes its results for other threads.
//
// Rules implemented here:
//   * XInput: a failed read is bridged with the last good sample for 0.100 s, counted from the first failure;
//     after that the slot is disconnected. An absent slot is probed again every 0.250 s. A slot that comes back
//     gets a new connection generation.
//   * SDL: controllers are keyed by the instance id of the handle actually opened; a device that cannot be read
//     or opened is skipped without touching the others (open is retried after 1 s); a handle that still reports
//     attached survives one scan in which enumeration omits it; a controller whose read fails is retired alone;
//     a failed global update keeps the last coherent samples for at most 0.100 s, then every SDL pad reads
//     neutral until updates work again, and each pad then gets a new generation.
//   * Identities: SDL GUID, uid (first 16 bytes of the SHA-256 of the lower-cased device path), and an ordinal
//     that tells identical models apart. A uid keeps its ordinal while the service runs, so a pad that reconnects
//     gets its old ordinal back and a new identical pad does not take it.
//   * Aliases: one physical pad may be an XInput slot and one or more SDL identities. Configured aliases and
//     SDL's own XInput devices ("XInput#n") join them into one pad group; a group has one owner seat.
//   * A seat's live source never changes ownership: XInput assignment -> that slot, else a connected SDL alias;
//     SDL assignment -> that identity, else its XInput alias slot, else an SDL sibling in its alias group, else
//     assigned but disconnected. Nobody adopts an unassigned slot.
//   * Sticks: radial deadzone 0.24 with rescale, so a full diagonal keeps full magnitude.
//   * Hold absorption: when a seat's source changes (first sample, other device, new connection generation,
//     a slot recovering after an exposed gap, a settings change for the seat), everything held is absorbed and
//     produces no press until released and pressed again.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"
#include "input/input_api.h"

namespace ilc::input {

inline constexpr uint32_t SEATS = ILC_SEATS;
inline constexpr uint32_t XINPUT_SLOTS = ILC_XINPUT_SLOTS;
inline constexpr uint32_t SDL_TRACKED_MAX = 16;
inline constexpr uint32_t SDL_INDEX_SCAN_MAX = 64;
inline constexpr uint32_t BACKOFF_MAX = 16;
inline constexpr uint32_t RESERVATIONS_MAX = 64;
inline constexpr uint64_t GRACE_US = 100000;            // 0.100 s
inline constexpr uint64_t ABSENT_RECHECK_US = 250000;   // probe an absent XInput slot every 0.250 s
inline constexpr uint64_t OPEN_RETRY_US = 1000000;      // retry a device SDL failed to open after 1 s
inline constexpr float DEADZONE = 0.24f;
inline constexpr uint16_t HELD_THRESHOLD = 32768;       // strength >= 0.5

struct seat_config {
    uint8_t kind;         // ilc_device_kind
    uint8_t xinput_slot;  // when kind is XINPUT
    uint8_t parked;       // a parked seat has no source
    uint8_t reserved;
    ilc_device_identity sdl;  // when kind is SDL
};

struct alias_config {
    uint8_t xinput_slot;
    uint8_t reserved[3];
    ilc_device_identity sdl;
};

struct config {
    seat_config seats[SEATS];
    uint32_t alias_count;
    alias_config aliases[ILC_ALIASES_MAX];
    // Read by the action layer (actions.h), not by device resolution: changing them never reseeds a pad.
    uint8_t kbm_seat;  // 0 none, 1..4
    uint8_t reserved[3];
    ilc_binding bindings[SEATS][ILC_SEAT_BINDINGS];
};

// The input part of the applied settings record (seat devices, parked flags, aliases, bindings, kbm seat).
void config_from_settings(const ilc_settings& settings, config* out);

// ---- normalisation (exposed for tests) ------------------------------------------------------------------
struct pad_norm {
    uint16_t buttons;  // XInput button bits
    float lt, rt;      // 0..1
    float lx, ly, rx, ry;  // -1..1, y up positive, no deadzone
};

pad_norm normalize_xinput(const ilc_xinput_gamepad& pad);
pad_norm normalize_sdl(const sdl_reading& reading);
ilc_xinput_gamepad pack_xinput(const pad_norm& norm);
void radial_deadzone(float x, float y, float deadzone, float* out_x, float* out_y);
void control_strengths(const pad_norm& norm, uint16_t strength[ILC_PAD_CONTROL_COUNT], int16_t left[2],
                       int16_t right[2]);

// ---- identities -------------------------------------------------------------------------------------------
void device_uid(const char* path, uint8_t uid[16]);  // callers set has_uid = 0 (and no uid) for an empty path
uint8_t sanitize_name(const char* in, char out[ILC_NAME_MAX]);  // returns bytes used; strips controls, bad UTF-8
// A configured identity names a live device: same GUID and, if the configured one has a uid, the same uid
// (ordinals can differ between runs), else the same ordinal.
bool identity_matches(const ilc_device_identity& configured, const ilc_device_identity& live);
// Two configured identities are the same device (the bridge's rule: GUID, uid, ordinal, uid flag).
bool identity_equal(const ilc_device_identity& a, const ilc_device_identity& b);

// ---- tracker state ----------------------------------------------------------------------------------------
struct xinput_slot_state {
    uint8_t connected;  // includes a bridged slot
    uint8_t bridged;
    uint8_t failing;
    uint8_t reserved;
    uint32_t generation;
    uint64_t fail_since;
    uint64_t next_probe;
    xinput_reading last;
    uint32_t last_rc;
    uint32_t polls;
};

struct sdl_entry {
    uint8_t used;
    uint8_t omitted;
    int8_t auto_slot;   // SDL's own XInput device for slot n ("XInput#n"), else -1
    uint8_t reserved;
    int32_t instance_id;
    uint64_t handle;
    uint32_t generation;
    uint32_t reserved2;
    ilc_device_identity id;
    sdl_reading last;
};

struct open_backoff {
    int32_t instance_id;
    uint32_t used;
    uint64_t reopen_after_us;
};

struct reservation {
    uint8_t used;
    uint8_t connected;
    uint8_t has_uid;
    uint8_t reserved;
    uint16_t ordinal;
    uint16_t reserved2;
    uint8_t guid[16];
    uint8_t uid[16];
    uint64_t stamp;  // last connection, for eviction
};

struct seat_state {
    uint32_t last_key_generation;  // 0 = no source last frame
    uint8_t last_key_source;
    uint8_t reseed_pending;        // settings changed for this seat
    uint8_t reserved[2];
};

struct core_counters {
    uint32_t ticks;
    uint32_t xinput_polls;
    uint32_t sdl_unreadable_devices;
    uint32_t sdl_open_failures;
    uint32_t sdl_read_failures;
    uint32_t sdl_pump_failures;
    uint32_t sdl_retired;
    uint32_t seat_conflicts;
};

// The whole core, zero-initialized in static storage; reset() starts a run.
struct core {
    uint32_t seq;
    uint32_t generation_counter;
    uint64_t reservation_stamp;
    config cfg;
    config pending;
    uint32_t pending_set;
    xinput_slot_state xi[XINPUT_SLOTS];
    sdl_entry sdl[SDL_TRACKED_MAX];
    open_backoff backoff[BACKOFF_MAX];
    reservation reservations[RESERVATIONS_MAX];
    uint8_t sdl_failing;
    uint8_t sdl_bridged;
    uint8_t sdl_failed_closed;
    uint8_t sdl_present;  // an SDL API was supplied this tick
    uint64_t sdl_fail_since;
    uint8_t group[XINPUT_SLOTS + SDL_TRACKED_MAX];  // pad group of every node after the last tick
    seat_state seats[SEATS];
    ilc_seat_input_frame frames[SEATS];
    core_counters counters;
};

void core_reset(core& c);
void core_set_config(core& c, const config& cfg);  // applied at the start of the next tick
// One tick. Either API may be null (that backend is unavailable: it reads as no devices).
void core_step(core& c, uint64_t now_us, xinput_api* xinput, sdl_api* sdl);
void core_close_all(core& c, sdl_api* sdl);  // closes every SDL handle (service stop)
// One entry per physical pad currently connected; returns the count written (<= max).
uint32_t core_inventory(const core& c, ilc_device_entry* entries, uint32_t max);

// ---- read-only views for the pairing observer (state after the last core_step) ---------------------------
// Nodes: XInput slots 0..3, then SDL entries as nodes 4..19. A pad group is named by its smallest node.
inline constexpr uint32_t CORE_NODES = XINPUT_SLOTS + SDL_TRACKED_MAX;
struct node_view {
    uint8_t live;         // connected (XInput, a bridged slot included) or tracked and readable (SDL)
    uint8_t a_down;       // the physical A button (bottom face) is down
    uint8_t group;        // pad group
    int8_t auto_slot;     // SDL node: SDL's own XInput device for slot n, else -1 (always -1 for XInput nodes)
    uint32_t generation;  // connection generation = the inventory entry id when this node lists the pad
};
node_view core_node(const core& c, uint32_t node);
// The live node whose connection generation is entry, or -1.
int32_t core_node_of_entry(const core& c, uint32_t entry);
// The seat (1..4) whose configured device is in this pad group, or 0.
uint8_t core_group_owner(const core& c, uint8_t group);

}  // namespace ilc::input
