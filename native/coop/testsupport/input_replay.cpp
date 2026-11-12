// Ivalice Legacy co-op module, host tests only: scripted device world, recorder and transcript replay.
#include "input_replay.h"

#include <stdlib.h>
#include <string.h>

namespace ilc::input::test {
namespace {

world_state g_world;

// ---- scripted world ------------------------------------------------------------------------------------------
constexpr uint32_t WORLD_HANDLES = 64;
struct world_handle {
    uint64_t id;
    int32_t instance_id;
    uint32_t used;
};
world_handle g_handles[WORLD_HANDLES];
uint32_t g_handle_serial = 0;

world_sdl_device* by_instance(int32_t instance_id)
{
    for (uint32_t i = 0; i < g_world.sdl_count; ++i) {
        if (g_world.sdl[i].instance_id == instance_id) {
            return &g_world.sdl[i];
        }
    }
    return nullptr;
}

// Enumeration as SDL would report it now: devices that are attached and not omitted, in list order.
int32_t enumerated(int32_t index)
{
    int32_t seen = 0;
    for (uint32_t i = 0; i < g_world.sdl_count; ++i) {
        const world_sdl_device& d = g_world.sdl[i];
        if (!d.attached || d.omitted) {
            continue;
        }
        if (seen == index) {
            return static_cast<int32_t>(i);
        }
        ++seen;
    }
    return -1;
}

int32_t enumerated_count()
{
    int32_t n = 0;
    for (uint32_t i = 0; i < g_world.sdl_count; ++i) {
        if (g_world.sdl[i].attached && !g_world.sdl[i].omitted) {
            ++n;
        }
    }
    return n;
}

void fill_info(const world_sdl_device& d, sdl_device_info* out)
{
    memset(out, 0, sizeof(*out));
    out->instance_id = d.instance_id;
    memcpy(out->guid, d.guid, 16);
    out->is_controller = d.is_controller;
    memcpy(out->path, d.path, SDL_PATH_MAX);
    memcpy(out->name, d.name, SDL_NAME_MAX);
    out->path[SDL_PATH_MAX - 1] = '\0';
    out->name[SDL_NAME_MAX - 1] = '\0';
}

world_handle* handle_of(uint64_t id)
{
    const uint32_t slot = static_cast<uint32_t>(id & 0xFF);
    if (id == 0 || slot >= WORLD_HANDLES || !g_handles[slot].used || g_handles[slot].id != id) {
        return nullptr;
    }
    return &g_handles[slot];
}

class world_xinput_api final : public xinput_api {
public:
    uint32_t get_state(uint32_t slot, xinput_reading* out) override
    {
        if (slot >= 4) {
            return XINPUT_NOT_CONNECTED;
        }
        if (g_world.xinput_rc[slot] == XINPUT_OK) {
            *out = g_world.xinput[slot];
        }
        return g_world.xinput_rc[slot];
    }
};

class world_sdl_api final : public sdl_api {
public:
    bool pump() override { return g_world.pump_fails == 0; }
    int32_t count() override { return g_world.count_fails ? -1 : enumerated_count(); }
    bool device_info(int32_t index, sdl_device_info* out) override
    {
        const int32_t i = enumerated(index);
        if (i < 0 || g_world.sdl[i].info_fails) {
            return false;
        }
        fill_info(g_world.sdl[i], out);
        return true;
    }
    uint64_t open(int32_t index) override
    {
        const int32_t n = enumerated_count();
        if (n <= 0) {
            return 0;
        }
        const int32_t shifted = ((index + g_world.reorder_open) % n + n) % n;
        const int32_t i = enumerated(shifted);
        if (i < 0 || g_world.sdl[i].open_fails) {
            return 0;
        }
        for (uint32_t k = 0; k < WORLD_HANDLES; ++k) {
            if (!g_handles[k].used) {
                g_handles[k].used = 1;
                g_handles[k].instance_id = g_world.sdl[i].instance_id;
                g_handles[k].id = (static_cast<uint64_t>(++g_handle_serial) << 8) | k;
                return g_handles[k].id;
            }
        }
        return 0;
    }
    bool handle_info(uint64_t handle, sdl_device_info* out) override
    {
        world_handle* h = handle_of(handle);
        world_sdl_device* d = h ? by_instance(h->instance_id) : nullptr;
        if (d == nullptr) {
            return false;
        }
        fill_info(*d, out);
        return true;
    }
    bool attached(uint64_t handle) override
    {
        world_handle* h = handle_of(handle);
        world_sdl_device* d = h ? by_instance(h->instance_id) : nullptr;
        return d != nullptr && d->attached;
    }
    bool read(uint64_t handle, sdl_reading* out) override
    {
        world_handle* h = handle_of(handle);
        world_sdl_device* d = h ? by_instance(h->instance_id) : nullptr;
        if (d == nullptr || d->read_fails) {
            return false;
        }
        *out = d->reading;
        return true;
    }
    void close(uint64_t handle) override
    {
        world_handle* h = handle_of(handle);
        if (h != nullptr) {
            memset(h, 0, sizeof(*h));
        }
    }
};

world_xinput_api g_world_xinput;
world_sdl_api g_world_sdl;

// ---- transcript ------------------------------------------------------------------------------------------------
enum rec_kind : uint8_t {
    REC_XI_GET = 1, REC_PUMP, REC_COUNT, REC_INFO, REC_OPEN, REC_HINFO, REC_ATTACHED, REC_READ, REC_CLOSE,
};

struct rec {
    uint8_t kind;
    uint8_t ok;
    uint16_t reserved;
    int32_t index;    // slot, enumeration index, or count result
    uint64_t handle;  // handle argument or open() result
    uint32_t value;   // XInput rc
    uint32_t reserved2;
    union {
        xinput_reading xr;
        sdl_device_info info;
        sdl_reading sr;
    } u;
};

uint8_t* g_rec = nullptr;
uint32_t g_rec_size = 0;
uint32_t g_rec_cap = 0;
xinput_api* g_inner_xinput = nullptr;
sdl_api* g_inner_sdl = nullptr;

void append(const rec& r)
{
    if (g_rec_size + sizeof(rec) > g_rec_cap) {
        const uint32_t cap = g_rec_cap ? g_rec_cap * 2 : 1u << 20;
        uint8_t* grown = static_cast<uint8_t*>(realloc(g_rec, cap));
        if (grown == nullptr) {
            return;
        }
        g_rec = grown;
        g_rec_cap = cap;
    }
    memcpy(g_rec + g_rec_size, &r, sizeof(r));
    g_rec_size += sizeof(rec);
}

rec make(rec_kind kind)
{
    rec r;
    memset(&r, 0, sizeof(r));
    r.kind = kind;
    return r;
}

class recording_xinput final : public xinput_api {
public:
    uint32_t get_state(uint32_t slot, xinput_reading* out) override
    {
        const uint32_t rc = g_inner_xinput->get_state(slot, out);
        rec r = make(REC_XI_GET);
        r.index = static_cast<int32_t>(slot);
        r.value = rc;
        r.u.xr = *out;
        append(r);
        return rc;
    }
};

class recording_sdl final : public sdl_api {
public:
    bool pump() override
    {
        const bool ok = g_inner_sdl->pump();
        rec r = make(REC_PUMP);
        r.ok = ok;
        append(r);
        return ok;
    }
    int32_t count() override
    {
        const int32_t n = g_inner_sdl->count();
        rec r = make(REC_COUNT);
        r.index = n;
        append(r);
        return n;
    }
    bool device_info(int32_t index, sdl_device_info* out) override
    {
        const bool ok = g_inner_sdl->device_info(index, out);
        rec r = make(REC_INFO);
        r.index = index;
        r.ok = ok;
        r.u.info = *out;
        append(r);
        return ok;
    }
    uint64_t open(int32_t index) override
    {
        const uint64_t h = g_inner_sdl->open(index);
        rec r = make(REC_OPEN);
        r.index = index;
        r.handle = h;
        append(r);
        return h;
    }
    bool handle_info(uint64_t handle, sdl_device_info* out) override
    {
        const bool ok = g_inner_sdl->handle_info(handle, out);
        rec r = make(REC_HINFO);
        r.handle = handle;
        r.ok = ok;
        r.u.info = *out;
        append(r);
        return ok;
    }
    bool attached(uint64_t handle) override
    {
        const bool ok = g_inner_sdl->attached(handle);
        rec r = make(REC_ATTACHED);
        r.handle = handle;
        r.ok = ok;
        append(r);
        return ok;
    }
    bool read(uint64_t handle, sdl_reading* out) override
    {
        const bool ok = g_inner_sdl->read(handle, out);
        rec r = make(REC_READ);
        r.handle = handle;
        r.ok = ok;
        r.u.sr = *out;
        append(r);
        return ok;
    }
    void close(uint64_t handle) override
    {
        g_inner_sdl->close(handle);
        rec r = make(REC_CLOSE);
        r.handle = handle;
        append(r);
    }
};

recording_xinput g_recording_xinput;
recording_sdl g_recording_sdl;

uint8_t* g_tx = nullptr;
uint32_t g_tx_size = 0;
uint32_t g_tx_pos = 0;
uint32_t g_tx_mismatches = 0;

// The next record if it has this kind and these arguments; otherwise a mismatch (and nothing is consumed).
const rec* next(rec_kind kind, int32_t index, uint64_t handle, bool check_index, bool check_handle)
{
    if (g_tx_pos + sizeof(rec) > g_tx_size) {
        ++g_tx_mismatches;
        return nullptr;
    }
    const rec* r = reinterpret_cast<const rec*>(g_tx + g_tx_pos);
    if (r->kind != kind || (check_index && r->index != index) || (check_handle && r->handle != handle)) {
        ++g_tx_mismatches;
        return nullptr;
    }
    g_tx_pos += sizeof(rec);
    return r;
}

class transcript_xinput_api final : public xinput_api {
public:
    uint32_t get_state(uint32_t slot, xinput_reading* out) override
    {
        const rec* r = next(REC_XI_GET, static_cast<int32_t>(slot), 0, true, false);
        if (r == nullptr) {
            return XINPUT_NOT_CONNECTED;
        }
        *out = r->u.xr;
        return r->value;
    }
};

class transcript_sdl_api final : public sdl_api {
public:
    bool pump() override
    {
        const rec* r = next(REC_PUMP, 0, 0, false, false);
        return r != nullptr && r->ok;
    }
    int32_t count() override
    {
        const rec* r = next(REC_COUNT, 0, 0, false, false);
        return r != nullptr ? r->index : -1;
    }
    bool device_info(int32_t index, sdl_device_info* out) override
    {
        const rec* r = next(REC_INFO, index, 0, true, false);
        if (r == nullptr) {
            return false;
        }
        *out = r->u.info;
        return r->ok;
    }
    uint64_t open(int32_t index) override
    {
        const rec* r = next(REC_OPEN, index, 0, true, false);
        return r != nullptr ? r->handle : 0;
    }
    bool handle_info(uint64_t handle, sdl_device_info* out) override
    {
        const rec* r = next(REC_HINFO, 0, handle, false, true);
        if (r == nullptr) {
            return false;
        }
        *out = r->u.info;
        return r->ok;
    }
    bool attached(uint64_t handle) override
    {
        const rec* r = next(REC_ATTACHED, 0, handle, false, true);
        return r != nullptr && r->ok;
    }
    bool read(uint64_t handle, sdl_reading* out) override
    {
        const rec* r = next(REC_READ, 0, handle, false, true);
        if (r == nullptr) {
            return false;
        }
        *out = r->u.sr;
        return r->ok;
    }
    void close(uint64_t handle) override { next(REC_CLOSE, 0, handle, false, true); }
};

transcript_xinput_api g_tx_xinput;
transcript_sdl_api g_tx_sdl;

}  // namespace

world_state& world()
{
    return g_world;
}

void world_reset()
{
    memset(&g_world, 0, sizeof(g_world));
    for (uint32_t s = 0; s < 4; ++s) {
        g_world.xinput_rc[s] = XINPUT_NOT_CONNECTED;
    }
    memset(g_handles, 0, sizeof(g_handles));
}

uint32_t world_open_handles()
{
    uint32_t n = 0;
    for (const world_handle& h : g_handles) {
        n += h.used ? 1u : 0u;
    }
    return n;
}

xinput_api* world_xinput()
{
    return &g_world_xinput;
}

sdl_api* world_sdl()
{
    return &g_world_sdl;
}

void recorder_begin(xinput_api* xinput, sdl_api* sdl, xinput_api** out_xinput, sdl_api** out_sdl)
{
    g_rec_size = 0;
    g_inner_xinput = xinput;
    g_inner_sdl = sdl;
    *out_xinput = xinput ? static_cast<xinput_api*>(&g_recording_xinput) : nullptr;
    *out_sdl = sdl ? static_cast<sdl_api*>(&g_recording_sdl) : nullptr;
}

uint32_t recorder_size()
{
    return g_rec_size;
}

uint32_t recorder_copy(void* out, uint32_t cap)
{
    const uint32_t n = g_rec_size < cap ? g_rec_size : cap;
    if (n != 0) {
        memcpy(out, g_rec, n);
    }
    return n;
}

bool transcript_begin(const void* data, uint32_t size, xinput_api** out_xinput, sdl_api** out_sdl)
{
    if (size % sizeof(rec) != 0) {
        return false;
    }
    uint8_t* copy = static_cast<uint8_t*>(realloc(g_tx, size ? size : 1));
    if (copy == nullptr) {
        return false;
    }
    g_tx = copy;
    if (size != 0) {
        memcpy(g_tx, data, size);
    }
    g_tx_size = size;
    g_tx_pos = 0;
    g_tx_mismatches = 0;
    *out_xinput = &g_tx_xinput;
    *out_sdl = &g_tx_sdl;
    return true;
}

uint32_t transcript_mismatches()
{
    return g_tx_mismatches;
}

uint32_t transcript_record_bytes()
{
    return static_cast<uint32_t>(sizeof(rec));
}

uint32_t transcript_remaining()
{
    return (g_tx_size - g_tx_pos) / static_cast<uint32_t>(sizeof(rec));
}

}  // namespace ilc::input::test
