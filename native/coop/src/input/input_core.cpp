// Ivalice Legacy co-op module: input service core. See input_core.h for the rules.
#include "input/input_core.h"

#include <string.h>
#include <xmmintrin.h>

#include "input/sha256.h"

namespace ilc::input {
namespace {

// XInput button bit for each pad control code 1..14 (A, B, X, Y, LB, RB, BACK, START, L3, R3, D-pad U/D/L/R).
constexpr uint16_t XINPUT_BIT[14] = {0x1000, 0x2000, 0x4000, 0x8000, 0x0100, 0x0200, 0x0020,
                                     0x0010, 0x0040, 0x0080, 0x0001, 0x0002, 0x0004, 0x0008};
// SDL button for each pad control code 1..14, same order.
constexpr uint8_t SDL_BUTTON_FOR[14] = {SDLB_A,         SDLB_B,          SDLB_X,         SDLB_Y,
                                        SDLB_LEFTSHOULDER, SDLB_RIGHTSHOULDER, SDLB_BACK, SDLB_START,
                                        SDLB_LEFTSTICK, SDLB_RIGHTSTICK, SDLB_DPAD_UP,   SDLB_DPAD_DOWN,
                                        SDLB_DPAD_LEFT, SDLB_DPAD_RIGHT};

inline float axis_unit(int32_t v)
{
    return v >= 0 ? static_cast<float>(v) / 32767.0f : static_cast<float>(v) / 32768.0f;
}

inline float clamp(float v, float lo, float hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

inline int32_t round_half_away(float v)
{
    return v >= 0.0f ? static_cast<int32_t>(v + 0.5f) : static_cast<int32_t>(v - 0.5f);
}

inline float square_root(float v)
{
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(v)));
}

inline uint16_t to_q16(float v)
{
    const int32_t q = round_half_away(clamp(v, 0.0f, 1.0f) * 65535.0f);
    return static_cast<uint16_t>(q);
}

inline int16_t to_s16(float v)
{
    return static_cast<int16_t>(round_half_away(clamp(v, -1.0f, 1.0f) * 32767.0f));
}

// ---- union-find over XInput slots (0..3) and SDL entries (4..19) -------------------------------------------
constexpr uint32_t NODES = XINPUT_SLOTS + SDL_TRACKED_MAX;

struct groups {
    uint8_t parent[NODES];
    void init()
    {
        for (uint32_t i = 0; i < NODES; ++i) {
            parent[i] = static_cast<uint8_t>(i);
        }
    }
    uint8_t find(uint32_t n)
    {
        while (parent[n] != n) {
            parent[n] = parent[parent[n]];
            n = parent[n];
        }
        return static_cast<uint8_t>(n);
    }
    void join(uint32_t a, uint32_t b)
    {
        const uint8_t ra = find(a), rb = find(b);
        if (ra != rb) {
            // The smaller root wins, so a group containing an XInput slot is rooted at that slot.
            if (ra < rb) {
                parent[rb] = ra;
            } else {
                parent[ra] = rb;
            }
        }
    }
};

inline uint32_t sdl_node(uint32_t entry)
{
    return XINPUT_SLOTS + entry;
}

uint32_t new_connection(core& c)
{
    ++c.generation_counter;
    if (c.generation_counter == 0) {
        c.generation_counter = 1;
    }
    return c.generation_counter;
}

// "XInput#n" (SDL's path for its own XInput devices) -> n, else -1.
int8_t xinput_path_slot(const char* path)
{
    static const char prefix[] = "XInput#";
    for (uint32_t i = 0; i < sizeof(prefix) - 1; ++i) {
        if (path[i] != prefix[i]) {
            return -1;
        }
    }
    const char d = path[sizeof(prefix) - 1];
    if (d < '0' || d > '3' || path[sizeof(prefix)] != '\0') {
        return -1;
    }
    return static_cast<int8_t>(d - '0');
}

// ---- ordinals ------------------------------------------------------------------------------------------------
bool ordinal_taken(const core& c, const uint8_t guid[16], uint16_t ordinal)
{
    for (const reservation& r : c.reservations) {
        if (r.used && r.ordinal == ordinal && memcmp(r.guid, guid, 16) == 0) {
            return true;
        }
    }
    return false;
}

reservation* free_reservation(core& c)
{
    for (reservation& r : c.reservations) {
        if (!r.used) {
            return &r;
        }
    }
    // Full: forget the oldest disconnected uid (a pad not seen for the longest time).
    reservation* oldest = nullptr;
    for (reservation& r : c.reservations) {
        if (!r.connected && (oldest == nullptr || r.stamp < oldest->stamp)) {
            oldest = &r;
        }
    }
    if (oldest != nullptr) {
        memset(oldest, 0, sizeof(*oldest));
    }
    return oldest;
}

uint16_t claim_ordinal(core& c, const uint8_t guid[16], const uint8_t uid[16], bool has_uid)
{
    if (has_uid) {
        for (reservation& r : c.reservations) {
            if (r.used && r.has_uid && !r.connected && memcmp(r.guid, guid, 16) == 0 && memcmp(r.uid, uid, 16) == 0) {
                r.connected = 1;
                r.stamp = ++c.reservation_stamp;
                return r.ordinal;
            }
        }
    }
    uint16_t ordinal = 0;
    while (ordinal < 255 && ordinal_taken(c, guid, ordinal)) {
        ++ordinal;
    }
    reservation* r = free_reservation(c);
    if (r != nullptr) {
        r->used = 1;
        r->connected = 1;
        r->has_uid = has_uid ? 1 : 0;
        r->ordinal = ordinal;
        memcpy(r->guid, guid, 16);
        if (has_uid) {
            memcpy(r->uid, uid, 16);
        }
        r->stamp = ++c.reservation_stamp;
    }
    return ordinal;
}

void release_ordinal(core& c, const ilc_device_identity& id)
{
    for (reservation& r : c.reservations) {
        if (r.used && r.connected && r.ordinal == id.ordinal && memcmp(r.guid, id.guid, 16) == 0 &&
            r.has_uid == id.has_uid && (!id.has_uid || memcmp(r.uid, id.uid, 16) == 0)) {
            if (r.has_uid) {
                r.connected = 0;  // keep the ordinal for this uid
            } else {
                memset(&r, 0, sizeof(r));
            }
            return;
        }
    }
}

// ---- XInput ----------------------------------------------------------------------------------------------------
void xinput_tick(core& c, uint64_t now, xinput_api* api)
{
    for (uint32_t s = 0; s < XINPUT_SLOTS; ++s) {
        xinput_slot_state& st = c.xi[s];
        if (api == nullptr) {
            memset(&st, 0, sizeof(st));
            continue;
        }
        if (!st.connected && st.next_probe > now) {
            continue;
        }
        xinput_reading reading;
        memset(&reading, 0, sizeof(reading));
        const uint32_t rc = api->get_state(s, &reading);
        ++st.polls;
        ++c.counters.xinput_polls;
        st.last_rc = rc;
        if (rc == XINPUT_OK) {
            if (!st.connected) {
                st.connected = 1;
                st.generation = new_connection(c);
            }
            st.failing = 0;
            st.bridged = 0;
            st.last = reading;
            continue;
        }
        if (st.connected) {
            if (!st.failing) {
                st.failing = 1;
                st.fail_since = now;
            }
            if (now - st.fail_since <= GRACE_US) {
                st.bridged = 1;  // keep the last good sample
                continue;
            }
            st.connected = 0;
            st.bridged = 0;
            st.failing = 0;
            memset(&st.last, 0, sizeof(st.last));
        }
        st.next_probe = now + ABSENT_RECHECK_US;
    }
}

// ---- SDL -------------------------------------------------------------------------------------------------------
int32_t find_instance(const core& c, int32_t instance_id)
{
    for (uint32_t i = 0; i < SDL_TRACKED_MAX; ++i) {
        if (c.sdl[i].used && c.sdl[i].instance_id == instance_id) {
            return static_cast<int32_t>(i);
        }
    }
    return -1;
}

bool backing_off(core& c, int32_t instance_id, uint64_t now)
{
    for (open_backoff& b : c.backoff) {
        if (b.used && b.instance_id == instance_id) {
            if (now < b.reopen_after_us) {
                return true;
            }
            b.used = 0;
            return false;
        }
    }
    return false;
}

void add_backoff(core& c, int32_t instance_id, uint64_t now)
{
    open_backoff* slot = nullptr;
    for (open_backoff& b : c.backoff) {
        if (b.used && b.instance_id == instance_id) {
            slot = &b;
            break;
        }
        if (!b.used && slot == nullptr) {
            slot = &b;
        }
    }
    if (slot == nullptr) {
        slot = &c.backoff[0];
        for (open_backoff& b : c.backoff) {
            if (b.reopen_after_us < slot->reopen_after_us) {
                slot = &b;
            }
        }
    }
    slot->used = 1;
    slot->instance_id = instance_id;
    slot->reopen_after_us = now + OPEN_RETRY_US;
}

void retire(core& c, uint32_t index, sdl_api* api)
{
    sdl_entry& e = c.sdl[index];
    if (api != nullptr && e.handle != 0) {
        api->close(e.handle);
    }
    release_ordinal(c, e.id);
    memset(&e, 0, sizeof(e));
    ++c.counters.sdl_retired;
}

void adopt(core& c, uint32_t index, uint64_t handle, const sdl_device_info& info)
{
    sdl_entry& e = c.sdl[index];
    memset(&e, 0, sizeof(e));
    e.used = 1;
    e.handle = handle;
    e.instance_id = info.instance_id;
    e.generation = new_connection(c);
    char path[SDL_PATH_MAX];
    memcpy(path, info.path, sizeof(path));
    path[SDL_PATH_MAX - 1] = '\0';
    e.auto_slot = xinput_path_slot(path);
    memcpy(e.id.guid, info.guid, 16);
    e.id.has_uid = path[0] != '\0' ? 1 : 0;
    if (e.id.has_uid) {
        device_uid(path, e.id.uid);
    }
    e.id.ordinal = claim_ordinal(c, e.id.guid, e.id.uid, e.id.has_uid != 0);
    char name[SDL_NAME_MAX];
    memcpy(name, info.name, sizeof(name));
    name[SDL_NAME_MAX - 1] = '\0';
    e.id.name_len = sanitize_name(name, reinterpret_cast<char*>(e.id.name));
}

void sdl_tick(core& c, uint64_t now, sdl_api* api)
{
    c.sdl_present = api != nullptr;
    if (api == nullptr) {
        for (uint32_t i = 0; i < SDL_TRACKED_MAX; ++i) {
            if (c.sdl[i].used) {
                retire(c, i, nullptr);
            }
        }
        return;
    }
    int32_t count = -1;
    if (api->pump()) {
        count = api->count();
    }
    if (count < 0) {
        ++c.counters.sdl_pump_failures;
        if (!c.sdl_failing) {
            c.sdl_failing = 1;
            c.sdl_fail_since = now;
        }
        if (now - c.sdl_fail_since <= GRACE_US && !c.sdl_failed_closed) {
            c.sdl_bridged = 1;  // last coherent samples stand
        } else {
            c.sdl_bridged = 0;
            c.sdl_failed_closed = 1;  // every SDL pad reads neutral
        }
        return;
    }
    c.sdl_failing = 0;
    c.sdl_bridged = 0;
    if (c.sdl_failed_closed) {
        // The gap was exposed: each pad is a new connection, so held controls get absorbed.
        for (sdl_entry& e : c.sdl) {
            if (e.used) {
                e.generation = new_connection(c);
            }
        }
        c.sdl_failed_closed = 0;
    }
    uint8_t seen[SDL_TRACKED_MAX];
    memset(seen, 0, sizeof(seen));
    const int32_t scan = count < static_cast<int32_t>(SDL_INDEX_SCAN_MAX) ? count : SDL_INDEX_SCAN_MAX;
    for (int32_t i = 0; i < scan; ++i) {
        sdl_device_info info;
        memset(&info, 0, sizeof(info));
        if (!api->device_info(i, &info)) {
            ++c.counters.sdl_unreadable_devices;  // a broken (often virtual) device: skip it, keep the rest
            continue;
        }
        if (!info.is_controller) {
            continue;
        }
        int32_t known = find_instance(c, info.instance_id);
        if (known >= 0) {
            seen[known] = 1;
            continue;
        }
        if (backing_off(c, info.instance_id, now)) {
            continue;
        }
        const uint64_t handle = api->open(i);
        if (handle == 0) {
            ++c.counters.sdl_open_failures;
            add_backoff(c, info.instance_id, now);
            continue;
        }
        sdl_device_info opened;
        memset(&opened, 0, sizeof(opened));
        if (!api->handle_info(handle, &opened)) {
            api->close(handle);
            ++c.counters.sdl_open_failures;
            add_backoff(c, info.instance_id, now);
            continue;
        }
        // Enumeration can reorder between the query and the open: key by what was actually opened.
        known = find_instance(c, opened.instance_id);
        if (known >= 0) {
            api->close(handle);  // a second reference to a pad we already hold
            seen[known] = 1;
            continue;
        }
        int32_t slot = -1;
        for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
            if (!c.sdl[k].used) {
                slot = static_cast<int32_t>(k);
                break;
            }
        }
        if (slot < 0) {
            api->close(handle);
            continue;
        }
        adopt(c, static_cast<uint32_t>(slot), handle, opened);
        seen[slot] = 1;
    }
    for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
        sdl_entry& e = c.sdl[k];
        if (!e.used) {
            continue;
        }
        if (!seen[k]) {
            if (e.omitted == 0 && api->attached(e.handle)) {
                e.omitted = 1;  // one coherent omission is tolerated
            } else {
                retire(c, k, api);
                continue;
            }
        } else {
            e.omitted = 0;
        }
        if (!api->attached(e.handle)) {
            retire(c, k, api);
            continue;
        }
        sdl_reading reading;
        memset(&reading, 0, sizeof(reading));
        if (!api->read(e.handle, &reading)) {
            ++c.counters.sdl_read_failures;
            retire(c, k, api);  // only this controller
            continue;
        }
        e.last = reading;
    }
}

bool sdl_live(const core& c, uint32_t index)
{
    return c.sdl[index].used && !c.sdl_failed_closed;
}

// ---- groups and seat resolution ----------------------------------------------------------------------------
void build_groups(core& c, groups& g)
{
    g.init();
    for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
        if (!c.sdl[k].used) {
            continue;
        }
        if (c.sdl[k].auto_slot >= 0) {
            g.join(static_cast<uint32_t>(c.sdl[k].auto_slot), sdl_node(k));
        }
        for (uint32_t a = 0; a < c.cfg.alias_count; ++a) {
            if (identity_matches(c.cfg.aliases[a].sdl, c.sdl[k].id)) {
                g.join(c.cfg.aliases[a].xinput_slot, sdl_node(k));
            }
        }
    }
    for (uint32_t n = 0; n < NODES; ++n) {
        c.group[n] = g.find(n);
    }
}

// Live SDL entries in connection order (oldest generation first), so "any alias" picks are stable.
uint32_t sdl_order(const core& c, uint8_t order[SDL_TRACKED_MAX])
{
    uint32_t n = 0;
    for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
        if (sdl_live(c, k)) {
            uint32_t pos = n++;
            while (pos > 0 && c.sdl[order[pos - 1]].generation > c.sdl[k].generation) {
                order[pos] = order[pos - 1];
                --pos;
            }
            order[pos] = static_cast<uint8_t>(k);
        }
    }
    return n;
}

struct source {
    uint8_t kind;  // ilc_frame_source
    uint8_t xinput_slot;
    uint8_t sdl_index;
    uint8_t via_alias;
};

int32_t matching_entry(const core& c, const ilc_device_identity& id, const uint8_t* order, uint32_t n)
{
    for (uint32_t i = 0; i < n; ++i) {
        if (identity_matches(id, c.sdl[order[i]].id)) {
            return order[i];
        }
    }
    return -1;
}

int32_t entry_in_group(const core& c, uint8_t group, const uint8_t* order, uint32_t n)
{
    for (uint32_t i = 0; i < n; ++i) {
        if (c.group[sdl_node(order[i])] == group) {
            return order[i];
        }
    }
    return -1;
}

source resolve(const core& c, const seat_config& seat, const uint8_t* order, uint32_t n)
{
    source none = {ILC_FRAME_SOURCE_NONE, 0xFF, 0, 0};
    if (seat.parked) {
        return none;
    }
    if (seat.kind == ILC_DEVICE_XINPUT && seat.xinput_slot < XINPUT_SLOTS) {
        const uint8_t s = seat.xinput_slot;
        if (c.xi[s].connected) {
            return source{ILC_FRAME_SOURCE_XINPUT, s, 0, 0};
        }
        const int32_t e = entry_in_group(c, c.group[s], order, n);
        if (e >= 0) {
            return source{ILC_FRAME_SOURCE_SDL, 0xFF, static_cast<uint8_t>(e), 1};
        }
        return none;
    }
    if (seat.kind == ILC_DEVICE_SDL) {
        const int32_t own = matching_entry(c, seat.sdl, order, n);
        if (own >= 0) {
            return source{ILC_FRAME_SOURCE_SDL, 0xFF, static_cast<uint8_t>(own), 0};
        }
        uint8_t slots = 0;  // bit s: configured alias slot of this identity
        for (uint32_t a = 0; a < c.cfg.alias_count; ++a) {
            if (identity_equal(c.cfg.aliases[a].sdl, seat.sdl) && c.cfg.aliases[a].xinput_slot < XINPUT_SLOTS) {
                slots |= static_cast<uint8_t>(1u << c.cfg.aliases[a].xinput_slot);
            }
        }
        for (uint8_t s = 0; s < XINPUT_SLOTS; ++s) {
            if ((slots & (1u << s)) && c.xi[s].connected) {
                return source{ILC_FRAME_SOURCE_XINPUT, s, 0, 1};
            }
        }
        for (uint8_t s = 0; s < XINPUT_SLOTS; ++s) {
            if (slots & (1u << s)) {
                const int32_t e = entry_in_group(c, c.group[s], order, n);
                if (e >= 0) {
                    return source{ILC_FRAME_SOURCE_SDL, 0xFF, static_cast<uint8_t>(e), 1};
                }
            }
        }
    }
    return none;
}

uint8_t source_group(const core& c, const source& src)
{
    return src.kind == ILC_FRAME_SOURCE_XINPUT ? c.group[src.xinput_slot] : c.group[sdl_node(src.sdl_index)];
}

void build_frame(core& c, uint32_t seat, const source& src, uint64_t now)
{
    ilc_seat_input_frame& f = c.frames[seat];
    const uint32_t prev_held = f.held;
    const uint32_t prev_absorbed = f.absorbed;
    seat_state& st = c.seats[seat];

    ilc_seat_input_frame next;
    memset(&next, 0, sizeof(next));
    next.seq = c.seq;
    next.time_us = now;
    next.seat = static_cast<uint8_t>(seat + 1);
    next.source = src.kind;
    next.xinput_slot = 0xFF;
    if (c.cfg.seats[seat].kind != ILC_DEVICE_NONE) {
        next.flags |= ILC_FRAMEF_ASSIGNED;
    }
    uint32_t generation = 0;
    if (src.kind == ILC_FRAME_SOURCE_XINPUT) {
        const xinput_slot_state& xs = c.xi[src.xinput_slot];
        const pad_norm norm = normalize_xinput(xs.last.pad);
        control_strengths(norm, next.strength, next.left_stick, next.right_stick);
        next.pad = xs.last.pad;
        next.xinput_slot = src.xinput_slot;
        generation = xs.generation;
        next.flags |= ILC_FRAMEF_CONNECTED;
        if (xs.bridged) {
            next.flags |= ILC_FRAMEF_BRIDGED;
        }
    } else if (src.kind == ILC_FRAME_SOURCE_SDL) {
        const sdl_entry& e = c.sdl[src.sdl_index];
        const pad_norm norm = normalize_sdl(e.last);
        control_strengths(norm, next.strength, next.left_stick, next.right_stick);
        next.pad = pack_xinput(norm);
        generation = e.generation;
        next.flags |= ILC_FRAMEF_CONNECTED;
        if (c.sdl_bridged) {
            next.flags |= ILC_FRAMEF_BRIDGED;
        }
    }
    if (src.via_alias) {
        next.flags |= ILC_FRAMEF_VIA_ALIAS;
    }
    next.source_generation = generation;
    uint32_t held = 0;
    for (uint32_t i = 0; i < ILC_PAD_CONTROL_COUNT; ++i) {
        if (next.strength[i] >= HELD_THRESHOLD) {
            held |= 1u << i;
        }
    }
    const bool has_source = src.kind != ILC_FRAME_SOURCE_NONE;
    const bool reseed = has_source && (generation != st.last_key_generation || src.kind != st.last_key_source ||
                                       st.reseed_pending);
    uint32_t absorbed = prev_absorbed;
    if (reseed) {
        absorbed |= held;
        next.flags |= ILC_FRAMEF_RESEEDED;
    }
    absorbed &= held;
    next.held = held;
    next.absorbed = absorbed;
    next.pressed = held & ~prev_held & ~absorbed;
    next.released = prev_held & ~held;
    st.last_key_generation = generation;
    st.last_key_source = src.kind;
    st.reseed_pending = 0;
    f = next;
}

}  // namespace

// ---- public helpers --------------------------------------------------------------------------------------------
void config_from_settings(const ilc_settings& settings, config* out)
{
    memset(out, 0, sizeof(*out));
    for (uint32_t s = 0; s < SEATS; ++s) {
        const ilc_seat_settings& seat = settings.seats[s];
        out->seats[s].kind = seat.device.kind;
        out->seats[s].xinput_slot = seat.device.xinput_slot;
        out->seats[s].parked = seat.parked;
        out->seats[s].sdl = seat.device.sdl;
    }
    out->alias_count = settings.alias_count <= ILC_ALIASES_MAX ? settings.alias_count : ILC_ALIASES_MAX;
    for (uint32_t a = 0; a < out->alias_count; ++a) {
        out->aliases[a].xinput_slot = settings.aliases[a].xinput_slot;
        out->aliases[a].sdl = settings.aliases[a].sdl;
    }
    out->kbm_seat = settings.kbm_seat <= SEATS ? settings.kbm_seat : 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        memcpy(out->bindings[s], settings.seats[s].bindings, sizeof(out->bindings[s]));
    }
}

pad_norm normalize_xinput(const ilc_xinput_gamepad& pad)
{
    pad_norm n;
    n.buttons = pad.buttons;
    n.lt = static_cast<float>(pad.lt) / 255.0f;
    n.rt = static_cast<float>(pad.rt) / 255.0f;
    n.lx = axis_unit(pad.lx);
    n.ly = axis_unit(pad.ly);
    n.rx = axis_unit(pad.rx);
    n.ry = axis_unit(pad.ry);
    return n;
}

pad_norm normalize_sdl(const sdl_reading& reading)
{
    pad_norm n;
    n.buttons = 0;
    for (uint32_t i = 0; i < 14; ++i) {
        if (reading.buttons & (1u << SDL_BUTTON_FOR[i])) {
            n.buttons |= XINPUT_BIT[i];
        }
    }
    const int32_t lt = reading.axes[SDLA_TRIGGERLEFT], rt = reading.axes[SDLA_TRIGGERRIGHT];
    n.lt = lt > 0 ? static_cast<float>(lt) / 32767.0f : 0.0f;
    n.rt = rt > 0 ? static_cast<float>(rt) / 32767.0f : 0.0f;
    n.lx = axis_unit(reading.axes[SDLA_LEFTX]);
    n.ly = -axis_unit(reading.axes[SDLA_LEFTY]);  // SDL: down positive; ours: up positive
    n.rx = axis_unit(reading.axes[SDLA_RIGHTX]);
    n.ry = -axis_unit(reading.axes[SDLA_RIGHTY]);
    return n;
}

ilc_xinput_gamepad pack_xinput(const pad_norm& norm)
{
    auto axis = [](float v) -> int16_t {
        v = clamp(v, -1.0f, 1.0f);
        const int32_t q = v >= 0.0f ? round_half_away(v * 32767.0f) : round_half_away(v * 32768.0f);
        return static_cast<int16_t>(q < -32768 ? -32768 : (q > 32767 ? 32767 : q));
    };
    ilc_xinput_gamepad pad;
    pad.buttons = norm.buttons;
    pad.lt = static_cast<uint8_t>(round_half_away(clamp(norm.lt, 0.0f, 1.0f) * 255.0f));
    pad.rt = static_cast<uint8_t>(round_half_away(clamp(norm.rt, 0.0f, 1.0f) * 255.0f));
    pad.lx = axis(norm.lx);
    pad.ly = axis(norm.ly);
    pad.rx = axis(norm.rx);
    pad.ry = axis(norm.ry);
    return pad;
}

void radial_deadzone(float x, float y, float deadzone, float* out_x, float* out_y)
{
    const float magnitude = square_root(x * x + y * y);
    if (magnitude <= deadzone || magnitude <= 0.0f) {
        *out_x = 0.0f;
        *out_y = 0.0f;
        return;
    }
    float scaled = (magnitude - deadzone) / (1.0f - deadzone);
    if (scaled > 1.0f) {
        scaled = 1.0f;
    }
    const float factor = scaled / magnitude;
    *out_x = clamp(x * factor, -1.0f, 1.0f);
    *out_y = clamp(y * factor, -1.0f, 1.0f);
}

void control_strengths(const pad_norm& norm, uint16_t strength[ILC_PAD_CONTROL_COUNT], int16_t left[2],
                       int16_t right[2])
{
    for (uint32_t i = 0; i < 14; ++i) {
        strength[i] = (norm.buttons & XINPUT_BIT[i]) ? 65535 : 0;
    }
    strength[ILC_PAD_LT - 1] = to_q16(norm.lt);
    strength[ILC_PAD_RT - 1] = to_q16(norm.rt);
    float lx, ly, rx, ry;
    radial_deadzone(norm.lx, norm.ly, DEADZONE, &lx, &ly);
    radial_deadzone(norm.rx, norm.ry, DEADZONE, &rx, &ry);
    strength[ILC_PAD_LS_UP - 1] = to_q16(ly);
    strength[ILC_PAD_LS_DOWN - 1] = to_q16(-ly);
    strength[ILC_PAD_LS_LEFT - 1] = to_q16(-lx);
    strength[ILC_PAD_LS_RIGHT - 1] = to_q16(lx);
    strength[ILC_PAD_RS_UP - 1] = to_q16(ry);
    strength[ILC_PAD_RS_DOWN - 1] = to_q16(-ry);
    strength[ILC_PAD_RS_LEFT - 1] = to_q16(-rx);
    strength[ILC_PAD_RS_RIGHT - 1] = to_q16(rx);
    left[0] = to_s16(lx);
    left[1] = to_s16(ly);
    right[0] = to_s16(rx);
    right[1] = to_s16(ry);
}

void device_uid(const char* path, uint8_t uid[16])
{
    char lowered[SDL_PATH_MAX];
    size_t n = 0;
    while (n < SDL_PATH_MAX - 1 && path[n] != '\0') {
        const char ch = path[n];
        lowered[n] = (ch >= 'A' && ch <= 'Z') ? static_cast<char>(ch - 'A' + 'a') : ch;
        ++n;
    }
    uint8_t digest[32];
    sha256(lowered, n, digest);
    memcpy(uid, digest, 16);
}

uint8_t sanitize_name(const char* in, char out[ILC_NAME_MAX])
{
    memset(out, 0, ILC_NAME_MAX);
    uint32_t used = 0;
    const uint8_t* p = reinterpret_cast<const uint8_t*>(in);
    while (*p != 0) {
        uint32_t cp = 0, len = 0;
        const uint8_t b = p[0];
        if (b < 0x80) {
            cp = b;
            len = 1;
        } else if ((b & 0xE0) == 0xC0) {
            cp = b & 0x1Fu;
            len = 2;
        } else if ((b & 0xF0) == 0xE0) {
            cp = b & 0x0Fu;
            len = 3;
        } else if ((b & 0xF8) == 0xF0) {
            cp = b & 0x07u;
            len = 4;
        } else {
            ++p;  // stray continuation or invalid lead byte
            continue;
        }
        bool ok = true;
        for (uint32_t k = 1; k < len; ++k) {
            if ((p[k] & 0xC0) != 0x80) {
                ok = false;
                break;
            }
            cp = (cp << 6) | (p[k] & 0x3Fu);
        }
        if (!ok) {
            ++p;
            continue;
        }
        const bool overlong = (len == 2 && cp < 0x80) || (len == 3 && cp < 0x800) || (len == 4 && cp < 0x10000);
        const bool control = cp < 0x20 || cp == 0x7F || (cp >= 0x80 && cp <= 0x9F);
        const bool invalid = overlong || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF);
        if (!control && !invalid) {
            if (used + len > ILC_NAME_MAX) {
                break;  // never split a character
            }
            memcpy(out + used, p, len);
            used += len;
        }
        p += len;
    }
    return static_cast<uint8_t>(used);
}

bool identity_matches(const ilc_device_identity& configured, const ilc_device_identity& live)
{
    if (memcmp(configured.guid, live.guid, 16) != 0) {
        return false;
    }
    if (configured.has_uid) {
        return live.has_uid && memcmp(configured.uid, live.uid, 16) == 0;
    }
    return configured.ordinal == live.ordinal;
}

// Two configured identities name the same device under the bridge's own rule (validate.cpp): GUID, uid, ordinal
// and uid flag all equal; the name is display only.
bool identity_equal(const ilc_device_identity& a, const ilc_device_identity& b)
{
    return memcmp(a.guid, b.guid, 16) == 0 && memcmp(a.uid, b.uid, 16) == 0 && a.ordinal == b.ordinal &&
           a.has_uid == b.has_uid;
}

void core_reset(core& c)
{
    memset(&c, 0, sizeof(c));
    for (uint32_t s = 0; s < SEATS; ++s) {
        c.frames[s].seat = static_cast<uint8_t>(s + 1);
        c.frames[s].xinput_slot = 0xFF;
    }
}

void core_set_config(core& c, const config& cfg)
{
    c.pending = cfg;
    c.pending_set = 1;
}

void core_step(core& c, uint64_t now, xinput_api* xinput, sdl_api* sdl)
{
    ++c.seq;
    ++c.counters.ticks;
    if (c.pending_set) {
        const bool aliases_changed = c.pending.alias_count != c.cfg.alias_count ||
                                     memcmp(c.pending.aliases, c.cfg.aliases, sizeof(c.cfg.aliases)) != 0;
        for (uint32_t s = 0; s < SEATS; ++s) {
            if (aliases_changed || memcmp(&c.pending.seats[s], &c.cfg.seats[s], sizeof(seat_config)) != 0) {
                c.seats[s].reseed_pending = 1;
            }
        }
        c.cfg = c.pending;
        c.pending_set = 0;
    }
    xinput_tick(c, now, xinput);
    sdl_tick(c, now, sdl);

    groups g;
    build_groups(c, g);
    uint8_t order[SDL_TRACKED_MAX];
    const uint32_t n = sdl_order(c, order);
    uint8_t claimed[NODES];
    memset(claimed, 0, sizeof(claimed));
    for (uint32_t s = 0; s < SEATS; ++s) {
        source src = resolve(c, c.cfg.seats[s], order, n);
        if (src.kind != ILC_FRAME_SOURCE_NONE) {
            const uint8_t group = source_group(c, src);
            if (claimed[group]) {
                ++c.counters.seat_conflicts;  // settings validation should prevent this; fail closed
                src = source{ILC_FRAME_SOURCE_NONE, 0xFF, 0, 0};
            } else {
                claimed[group] = 1;
            }
        }
        build_frame(c, s, src, now);
    }
}

void core_close_all(core& c, sdl_api* sdl)
{
    for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
        if (c.sdl[k].used) {
            retire(c, k, sdl);
        }
    }
}

uint8_t core_group_owner(const core& c, uint8_t group)
{
    uint8_t order[SDL_TRACKED_MAX];
    const uint32_t n = sdl_order(c, order);
    for (uint32_t s = 0; s < SEATS; ++s) {
        const seat_config& seat = c.cfg.seats[s];
        if (seat.kind == ILC_DEVICE_XINPUT && seat.xinput_slot < XINPUT_SLOTS && c.group[seat.xinput_slot] == group) {
            return static_cast<uint8_t>(s + 1);
        }
        if (seat.kind == ILC_DEVICE_SDL) {
            for (uint32_t i = 0; i < n; ++i) {
                if (c.group[sdl_node(order[i])] == group && identity_matches(seat.sdl, c.sdl[order[i]].id)) {
                    return static_cast<uint8_t>(s + 1);
                }
            }
            for (uint32_t a = 0; a < c.cfg.alias_count; ++a) {
                if (identity_equal(c.cfg.aliases[a].sdl, seat.sdl) && c.cfg.aliases[a].xinput_slot < XINPUT_SLOTS &&
                    c.group[c.cfg.aliases[a].xinput_slot] == group) {
                    return static_cast<uint8_t>(s + 1);
                }
            }
        }
    }
    return 0;
}

node_view core_node(const core& c, uint32_t node)
{
    node_view v = {0, 0, 0, -1, 0};
    if (node >= CORE_NODES) {
        return v;
    }
    v.group = c.group[node];
    if (node < XINPUT_SLOTS) {
        const xinput_slot_state& xs = c.xi[node];
        v.live = xs.connected;
        v.a_down = (xs.connected && (xs.last.pad.buttons & 0x1000u) != 0) ? 1 : 0;  // XINPUT_GAMEPAD_A
        v.generation = xs.connected ? xs.generation : 0;
        return v;
    }
    const uint32_t k = node - XINPUT_SLOTS;
    if (sdl_live(c, k)) {
        v.live = 1;
        v.a_down = (c.sdl[k].last.buttons & (1u << SDLB_A)) != 0 ? 1 : 0;
        v.auto_slot = c.sdl[k].auto_slot;
        v.generation = c.sdl[k].generation;
    }
    return v;
}

int32_t core_node_of_entry(const core& c, uint32_t entry)
{
    if (entry == 0) {
        return -1;
    }
    for (uint32_t n = 0; n < CORE_NODES; ++n) {
        const node_view v = core_node(c, n);
        if (v.live && v.generation == entry) {
            return static_cast<int32_t>(n);
        }
    }
    return -1;
}

uint32_t core_inventory(const core& c, ilc_device_entry* entries, uint32_t max)
{
    uint8_t order[SDL_TRACKED_MAX];
    const uint32_t n = sdl_order(c, order);
    uint8_t done[NODES];
    memset(done, 0, sizeof(done));
    uint32_t count = 0;

    auto finish = [&](ilc_device_entry& e, uint8_t group) {
        e.connected = 1;
        e.owner_seat = core_group_owner(c, group);
        e.flags = e.owner_seat == 1 ? 0x01 : 0x00;  // bit 0: Player 1's pad, locked
    };

    for (uint8_t s = 0; s < XINPUT_SLOTS && count < max; ++s) {
        if (!c.xi[s].connected || done[c.group[s]]) {
            continue;
        }
        const uint8_t group = c.group[s];
        done[group] = 1;
        ilc_device_entry& e = entries[count++];
        memset(&e, 0, sizeof(e));
        e.entry_id = c.xi[s].generation;
        e.kind = ILC_DEVICE_XINPUT;
        e.xinput_slot = s;
        // Name it after an SDL member of the same pad: a configured alias first, then SDL's own XInput device.
        int32_t named = -1;
        for (uint32_t i = 0; i < n && named < 0; ++i) {
            if (c.group[sdl_node(order[i])] != group) {
                continue;
            }
            for (uint32_t a = 0; a < c.cfg.alias_count; ++a) {
                if (c.cfg.aliases[a].xinput_slot == s && identity_matches(c.cfg.aliases[a].sdl, c.sdl[order[i]].id)) {
                    named = order[i];
                    break;
                }
            }
        }
        if (named < 0) {
            named = entry_in_group(c, group, order, n);
        }
        if (named >= 0) {
            e.sdl = c.sdl[named].id;
        }
        finish(e, group);
    }
    for (uint32_t i = 0; i < n && count < max; ++i) {
        const uint8_t group = c.group[sdl_node(order[i])];
        if (done[group]) {
            continue;  // collapsed into a pad already listed
        }
        done[group] = 1;
        ilc_device_entry& e = entries[count++];
        memset(&e, 0, sizeof(e));
        e.entry_id = c.sdl[order[i]].generation;
        e.kind = ILC_DEVICE_SDL;
        e.xinput_slot = 0xFF;
        e.sdl = c.sdl[order[i]].id;
        finish(e, group);
    }
    return count;
}

}  // namespace ilc::input
