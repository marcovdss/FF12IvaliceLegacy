// Ivalice Legacy co-op module, host tests only: the loopback replays and the soak simulator. Never linked into the
// shipped DLL.
//
//   * Input replay: a scripted, deterministic input stream (pads on three XInput slots, keys, focus changes, seat
//     commands through the bridge, settings regenerations) driven through the input_replay device world and the
//     manual input service, tick by tick on a simulated clock. Every 20 ms beat it digests what the seam's
//     consumers read: every seat's edge delta through its own cursor, the bridge's events, the snapshot's frames,
//     action frames and control view, and the pad mirror. The digest skips padding (value bytes only).
//   * HUD replay: variants of every view the HUD passes draw from (given by the test) are published each beat in a
//     scripted order, and every frame is composed by the frame driver; the digest covers each frame's result and
//     its command list and text.
//   * Soak: both of the above together for hours of simulated time, plus the co-op switches (kill switch on and off
//     by command and by key), captures, battle-log traffic through its event ring, game-thread jobs, liveness
//     stalls (no heartbeat for 3 s) and settings regenerations. It reports the peak of every queue and ring.
// The same seed gives the same stream, so a run with the loopback codec on must give the same digests as one with
// it off.
#include <string.h>

#include "bridge/bridge_internal.h"
#include "control/control.h"
#include "hud/battle_log.h"
#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/menus_draw.h"
#include "hud/party_rows.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"
#include "ilc/bridge_abi.h"
#include "ilc/bridge_defaults.h"
#include "input/edge_history.h"
#include "input/input_service.h"
#include "input/keyboard.h"
#include "input/pad_mirror.h"
#include "input_replay.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "runtime/game_thread.h"
#include "runtime/loopback.h"

extern "C" {
int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t in_len, void* out, uint64_t out_cap);
void ILCT_SetFixedClock(uint64_t now);
}

using namespace ilc;

namespace {

struct rng {
    uint64_t s;
    uint32_t next()
    {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        return static_cast<uint32_t>(s >> 16);
    }
    uint32_t below(uint32_t n) { return n != 0 ? next() % n : 0; }
    bool one_in(uint32_t n) { return below(n) == 0; }
};

struct digest {
    uint64_t h = 0xCBF29CE484222325ull;
    void byte(uint8_t b)
    {
        h ^= b;
        h *= 0x100000001B3ull;
    }
    void bytes(const void* p, size_t n)
    {
        const uint8_t* c = static_cast<const uint8_t*>(p);
        for (size_t i = 0; i < n; ++i) {
            byte(c[i]);
        }
    }
    void u64(uint64_t v) { bytes(&v, sizeof(v)); }
    template <typename T>
    void value(const T& v)  // value bytes only: independent of padding
    {
        const uint8_t* m = loopback::value_mask<T>();
        const uint8_t* c = reinterpret_cast<const uint8_t*>(&v);
        for (size_t i = 0; i < sizeof(T); ++i) {
            if (m[i] != 0) {
                byte(c[i]);
            }
        }
    }
};

class drill_keys final : public input::keyboard_api {
public:
    bool focused() override { return focus; }
    bool down(uint8_t vk) override { return state[vk] != 0; }
    bool focus = true;
    uint8_t state[256] = {};
};

// Keys the stream presses: P2's keyboard layout, the drop-in/out keys and the right mouse button.
constexpr uint8_t STREAM_KEYS[] = {'W', 'A', 'S', 'D', 0x20, 0x0D, 0x1B, 'F', 'C', 'R', 'T', 0xA0,
                                   0x75, 0x76, 0x02, '1', '3', 'I', 'K', 0x08};
constexpr uint16_t PAD_BITS[] = {0x1000, 0x2000, 0x4000, 0x8000, 0x0100, 0x0200, 0x0010,
                                 0x0020, 0x0001, 0x0002, 0x0004, 0x0008, 0x0040, 0x0080};
constexpr int16_t STICK[] = {0, 0, 0, 12000, -12000, 32767, -32768, 26000, -26000};
constexpr uint8_t TRIGGER[] = {0, 0, 90, 160, 200, 255};
constexpr uint32_t BEAT_MS = 20;
constexpr uint32_t TICK_MS = 8;

struct input_rig {
    rng r = {1};
    drill_keys keys;
    ilc_settings s = {};
    uint32_t gen = 0;
    uint32_t request = 0;
    edges::edge_cursor cursor = {};
    // results
    uint64_t commands = 0;
    uint64_t captures_started = 0;
    uint64_t command_busy = 0;
    uint64_t command_rejected = 0;
    uint64_t settings_applied = 0;
    uint64_t events = 0;
    uint64_t edge_presses = 0;
    uint64_t beats = 0;
    uint64_t ticks = 0;
    uint64_t focus_losses = 0;
    uint64_t disconnects = 0;
    uint32_t peak_events_pending = 0;
    uint32_t last_events_dropped = 0;
    uint32_t first_events_dropped = 0xFFFFFFFFu;
    uint32_t last_outbox_dropped = 0;

    void begin(uint32_t seed)
    {
        r.s = 0x9E3779B97F4A7C15ull ^ (uint64_t{seed} * 0xD1B54A32D192ED03ull);
        if (r.s == 0) {
            r.s = 1;
        }
        input::test::world_reset();
        input::test::world_state& w = input::test::world();
        for (uint32_t k = 0; k < 4; ++k) {
            w.xinput_rc[k] = k < 3 ? input::XINPUT_OK : input::XINPUT_NOT_CONNECTED;
            memset(&w.xinput[k], 0, sizeof(w.xinput[k]));
            w.xinput[k].packet = 1;
        }
        w.sdl_count = 0;
        input::manual_keyboard(&keys);
        input::manual_begin(input::test::world_xinput(), input::test::world_sdl(), 0x5EED);
        s = default_settings();
        s.seats[1].device.kind = ILC_DEVICE_XINPUT;
        s.seats[1].device.xinput_slot = 1;
        s.seats[2].device.kind = ILC_DEVICE_XINPUT;
        s.seats[2].device.xinput_slot = 2;
        s.kbm_seat = 2;
        ilc_status st;  // the bridge may already hold a later generation (an earlier test in this module)
        if (ILC_Bridge(ILC_OP_GET_STATUS, nullptr, 0, &st, sizeof(st)) > 0) {
            gen = st.accepted_generation > st.applied_generation ? st.accepted_generation : st.applied_generation;
        }
        apply();
    }

    void end() { input::manual_end(); }

    void apply()
    {
        s.header.generation = ++gen;
        uint8_t out[64];
        const int32_t rc = ILC_Bridge(ILC_OP_SET_SETTINGS, &s, sizeof(s), out, sizeof(out));
        if (rc < 0) {
            ++command_rejected;
            return;
        }
        static ilc_settings applied;
        bridge_safe_point(nullptr, &applied);
        input::configure_from_settings(applied);
        control::configure_from_settings(applied);
        ++settings_applied;
    }

    void regenerate()
    {
        static const uint8_t KBM[] = {2, 2, 3, 0, 1};
        s.kbm_seat = KBM[r.below(5)];
        s.seats[1].parked = r.one_in(4) ? 1 : 0;
        s.seats[2].parked = r.one_in(4) ? 1 : 0;
        if (r.one_in(3)) {
            s.seats[2].device.kind = s.seats[2].device.kind == ILC_DEVICE_XINPUT ? ILC_DEVICE_NONE : ILC_DEVICE_XINPUT;
            s.seats[2].device.xinput_slot = s.seats[2].device.kind == ILC_DEVICE_XINPUT ? 2 : 0;
        }
        apply();
    }

    int32_t command(uint16_t cmd, uint8_t seat, uint32_t a0, uint32_t a1)
    {
        ilc_command c;
        memset(&c, 0, sizeof(c));
        c.header.magic = ILC_ABI_MAGIC;
        c.header.abi_version = ILC_ABI_VERSION;
        c.header.record_type = ILC_REC_COMMAND;
        c.header.size = sizeof(c);
        c.header.generation = ++request;
        c.command = cmd;
        c.seat = seat;
        c.arg0 = a0;
        c.arg1 = a1;
        uint8_t out[16];
        const int32_t rc = ILC_Bridge(ILC_OP_COMMAND, &c, sizeof(c), out, sizeof(out));
        ++commands;
        if (rc == ILC_E_BUSY) {
            ++command_busy;
        } else if (rc < 0 && rc != ILC_E_NOT_READY) {
            ++command_rejected;
        }
        return rc;
    }

    void random_command(digest& d)
    {
        int32_t rc = 0;
        switch (r.below(8)) {
        case 0:
            rc = command(ILC_CMD_PARK, static_cast<uint8_t>(r.below(2) ? 0 : 2 + r.below(2)), r.below(2), 0);
            break;
        case 1:
            rc = command(ILC_CMD_MOVE_SEAT_NOW, static_cast<uint8_t>(1 + r.below(3)), r.below(ILC_CHARACTERS), 0);
            break;
        case 2:
            rc = command(ILC_CMD_CAPTURE_START, 0, 0, r.below(ILC_KILL_SWITCH_BINDINGS));
            ++captures_started;
            break;
        case 3:
            ++captures_started;
            rc = command(ILC_CMD_CAPTURE_START, static_cast<uint8_t>(2 + r.below(2)), r.below(ILC_ACTIONS),
                         r.below(ILC_BINDINGS_PER_ACTION));
            break;
        case 4:
            rc = command(ILC_CMD_CAPTURE_CANCEL, static_cast<uint8_t>(r.below(4)), 0, 0);
            break;
        case 5:
            rc = command(ILC_CMD_PAIRING_START, static_cast<uint8_t>(2 + r.below(2)), 1 + r.below(1000), r.below(50));
            break;
        case 6:
            rc = command(ILC_CMD_PAIRING_CANCEL, 2, 0, 0);
            break;
        default:
            rc = command(ILC_CMD_PARK, 4, 1, 0);  // P4 cannot play: COMMAND_REJECTED
            break;
        }
        d.u64(static_cast<uint64_t>(static_cast<int64_t>(rc)));
    }

    void script_tick()
    {
        input::test::world_state& w = input::test::world();
        for (uint32_t k = 0; k < 3; ++k) {
            if (!r.one_in(40)) {
                continue;
            }
            ilc_xinput_gamepad& p = w.xinput[k].pad;
            uint16_t b = 0;
            for (const uint16_t bit : PAD_BITS) {
                if (r.one_in(6)) {
                    b |= bit;
                }
            }
            p.buttons = b;
            p.lt = TRIGGER[r.below(6)];
            p.rt = TRIGGER[r.below(6)];
            p.lx = STICK[r.below(9)];
            p.ly = STICK[r.below(9)];
            p.rx = STICK[r.below(9)];
            p.ry = STICK[r.below(9)];
            ++w.xinput[k].packet;
        }
        if (r.one_in(30000)) {
            w.xinput_rc[2] = w.xinput_rc[2] == input::XINPUT_OK ? input::XINPUT_NOT_CONNECTED : input::XINPUT_OK;
            ++disconnects;
        }
        for (const uint8_t vk : STREAM_KEYS) {
            if (keys.state[vk] != 0) {
                if (r.one_in(8)) {
                    keys.state[vk] = 0;
                }
            } else if (r.one_in(500)) {
                keys.state[vk] = 1;
            }
        }
        if (keys.focus && r.one_in(6000)) {
            keys.focus = false;
            ++focus_losses;
        } else if (!keys.focus && r.one_in(50)) {
            keys.focus = true;
        }
    }

    // What the seam's consumers see this beat.
    void observe(digest& d)
    {
        for (uint32_t seat = 1; seat <= ILC_SEATS; ++seat) {
            edges::edge_delta e;
            memset(&e, 0, sizeof(e));
            if (edges::since(cursor, seat, &e)) {
                d.value(e);
                edge_presses += static_cast<uint64_t>(__builtin_popcount(e.act_pressed));
            }
        }
        ilc_status st;
        if (ILC_Bridge(ILC_OP_GET_STATUS, nullptr, 0, &st, sizeof(st)) > 0) {
            peak_events_pending = st.events_pending > peak_events_pending ? st.events_pending : peak_events_pending;
            if (first_events_dropped == 0xFFFFFFFFu) {
                first_events_dropped = st.events_dropped;
            }
            last_events_dropped = st.events_dropped;
        }
        static uint8_t out[4096];
        const int32_t n = ILC_Bridge(ILC_OP_POP_EVENTS, nullptr, 0, out, sizeof(out));
        if (n > 0) {
            d.bytes(out, static_cast<size_t>(n));
            uint32_t count = 0;
            memcpy(&count, out + 16, sizeof(count));
            events += count;
        }
        static input::snapshot snap;
        if (input::snapshot_read(&snap)) {
            for (uint32_t s2 = 0; s2 < ILC_SEATS; ++s2) {
                d.value(snap.frames[s2]);
                d.value(snap.actions[s2]);
            }
            d.value(snap.control);
            last_outbox_dropped = snap.control.events_dropped;
        }
        for (uint32_t seat = 1; seat <= ILC_SEATS; ++seat) {
            pads::pad_view v;
            if (pads::read(seat, &v)) {
                d.value(v);
            }
        }
        ++beats;
    }
};

input_rig g_rig;

}  // namespace

extern "C" {

// Input replay. out (at least 12): digest, ticks, beats, events, edge presses, commands, busy, rejected, settings
// applied, focus losses, disconnects, outbox drops. Returns the number of values written.
uint32_t ILCT_LoopbackInputDrill(uint64_t duration_ms, uint32_t seed, uint64_t* out, uint32_t cap)
{
    input_rig& g = g_rig;
    g = input_rig();
    g.begin(seed);
    digest d;
    uint64_t next_beat = BEAT_MS;
    for (uint64_t t = TICK_MS; t <= duration_ms; t += TICK_MS) {
        g.script_tick();
        input::manual_step(t * 1000);
        ++g.ticks;
        while (next_beat <= t) {
            if (g.r.one_in(75)) {
                g.random_command(d);
            }
            if (g.r.one_in(12000)) {
                g.regenerate();
            }
            g.observe(d);
            next_beat += BEAT_MS;
        }
    }
    g.end();
    const uint64_t v[] = {d.h, g.ticks, g.beats, g.events, g.edge_presses, g.commands, g.command_busy,
                          g.command_rejected, g.settings_applied, g.focus_losses, g.disconnects, g.last_outbox_dropped};
    uint32_t n = 0;
    for (; n < cap && n < sizeof(v) / sizeof(v[0]); ++n) {
        out[n] = v[n];
    }
    return n;
}

}  // extern "C"

// ---- HUD replay ------------------------------------------------------------------------------------------------------
namespace {

struct hud_pools {
    const hud::hud_snapshot* snaps;
    uint32_t n_snaps;
    uint32_t epoch;
    const hud::menus_draw::menu_view* menus;
    uint32_t n_menus;
    uint32_t frame_hz;
    const hud::party_rows::rows_record* rows;
    uint32_t n_rows;
    uint32_t reserved;
    const hud::target_info::view* tis;
    uint32_t n_tis;
    uint32_t reserved2;
    const hud::battle_log::view* logs;
    uint32_t n_logs;
    uint32_t reserved3;
};

struct hud_rig {
    rng r = {1};
    uint64_t frames = 0;
    uint64_t composed = 0;
    uint64_t commands = 0;
    uint64_t texts = 0;
    uint64_t beats = 0;
    uint64_t not_ok = 0;
    uint32_t peak_commands = 0;

    void publish_beat(const hud_pools& p, uint64_t now, bool log_from_pool)
    {
        static hud::hud_snapshot s;
        s = p.snaps[r.below(p.n_snaps)];
        s.head.epoch = p.epoch;
        s.sampled_ms = now;
        hud::snapshot::publish(s);
        static hud::menus_draw::menu_view m;
        m = p.menus[r.below(p.n_menus)];
        m.head.epoch = p.epoch;
        m.built_ms = now;
        hud::menus_draw::publish(m);
        static hud::party_rows::rows_record rr;
        rr = p.rows[r.below(p.n_rows)];
        rr.head.epoch = p.epoch;
        rr.sampled_ms = now;
        hud::party_rows::test_publish(rr);
        static hud::target_info::view ti;
        ti = p.tis[r.below(p.n_tis)];
        ti.head.epoch = p.epoch;
        ti.built_ms = now;
        hud::target_info::test_publish(ti);
        if (log_from_pool) {
            static hud::battle_log::view lv;
            lv = p.logs[r.below(p.n_logs)];
            lv.head.epoch = p.epoch;
            lv.built_ms = now;
            hud::battle_log::test_publish(lv);
        }
        ++beats;
    }

    void frame(const hud_pools& p, uint64_t now, digest& d)
    {
        const hud::frame* f = nullptr;
        const int32_t rc = hud::driver::compose(p.epoch, now, nullptr, &f);
        d.u64(static_cast<uint64_t>(static_cast<int64_t>(rc)));
        ++frames;
        if (rc != hud::driver::COMPOSE_OK || f == nullptr) {
            ++not_ok;
            return;
        }
        ++composed;
        const uint32_t count = f->count < hud::MAX_COMMANDS ? f->count : hud::MAX_COMMANDS;
        const uint32_t used = f->text_used < hud::TEXT_POOL ? f->text_used : hud::TEXT_POOL;
        d.u64(count);
        d.u64(used);
        for (uint32_t k = 0; k < count; ++k) {
            d.value(f->cmds[k]);
        }
        d.bytes(f->text, used);
        commands += count;
        texts += used;
        peak_commands = count > peak_commands ? count : peak_commands;
    }
};

}  // namespace

extern "C" {

// HUD replay over the given view pools. out (at least 8): digest, frames, composed, commands, text bytes, beats,
// frames not composed, peak commands in one frame.
uint32_t ILCT_LoopbackHudDrill(const hud_pools* p, uint64_t start_ms, uint64_t duration_ms, uint32_t seed,
                               uint64_t* out, uint32_t cap)
{
    hud_rig h;
    h.r.s = 0x5851F42D4C957F2Dull ^ (uint64_t{seed} * 0x14057B7EF767814Full);
    digest d;
    const uint32_t hz = p->frame_hz != 0 ? p->frame_hz : 60;
    uint64_t frame_k = 0;
    uint64_t next_beat = start_ms;
    for (;;) {
        const uint64_t frame_at = start_ms + (frame_k * 1000u) / hz;
        if (frame_at > start_ms + duration_ms) {
            break;
        }
        while (next_beat <= frame_at) {
            h.publish_beat(*p, next_beat, true);
            next_beat += BEAT_MS;
        }
        h.frame(*p, frame_at, d);
        ++frame_k;
    }
    const uint64_t v[] = {d.h, h.frames, h.composed, h.commands, h.texts, h.beats, h.not_ok, h.peak_commands};
    uint32_t n = 0;
    for (; n < cap && n < sizeof(v) / sizeof(v[0]); ++n) {
        out[n] = v[n];
    }
    return n;
}

}  // extern "C"

// ---- soak ------------------------------------------------------------------------------------------------------------
namespace {

constexpr uint16_t SOAK_OP = 29;
volatile uint64_t g_soak_ran = 0;

int32_t soak_op(const gtq::job& j)
{
    (void)j;
    __atomic_add_fetch(&g_soak_ran, 1, __ATOMIC_RELAXED);
    return 0;
}

uint64_t max64(uint64_t a, uint64_t b)
{
    return a > b ? a : b;
}

}  // namespace

extern "C" {

// Soak over hours of simulated time. Needs the patch layer installed, armed and LIVE (the HUD world's setup).
// out: see the names returned by ILCT_SoakNames (one value each).
static const char* const SOAK_NAMES[] = {
    "sim_ms", "input_ticks", "beats", "frames", "frames_composed", "stall_count", "stalled_beats",
    "kills", "revives", "kill_key_presses", "captures_started", "settings_applied", "commands", "command_busy",
    "events_popped", "peak_bridge_events_pending", "bridge_events_dropped_delta", "input_outbox_dropped",
    "gtq_pushed", "gtq_push_full", "gtq_push_refused", "gtq_drained", "gtq_ran", "gtq_reaped", "gtq_peak_in_flight",
    "log_ring_written", "log_ring_lost", "log_peak_unread", "log_lines", "log_published", "hud_peak_commands",
    "edge_presses", "stale_fallbacks_delta", "mask_busy", "toggles", "capture_held", "focus_losses", "disconnects",
};

uint32_t ILCT_SoakNameCount(void)
{
    return sizeof(SOAK_NAMES) / sizeof(SOAK_NAMES[0]);
}

const char* ILCT_SoakName(uint32_t k)
{
    return k < ILCT_SoakNameCount() ? SOAK_NAMES[k] : nullptr;
}

uint32_t ILCT_SoakDrill(const hud_pools* p, uint64_t start_ms, uint64_t duration_ms, uint32_t seed, uint64_t* out,
                        uint32_t cap)
{
    input_rig& g = g_rig;
    g = input_rig();
    hud_rig h;
    h.r.s = 0x2545F4914F6CDD1Dull ^ seed;
    gtq::test_reset();
    gtq::register_op(SOAK_OP, ILC_SITE_SUBSYS_RENDER, soak_op, gtq::OPF_ANY_WORLD);
    gtq::seal();
    control::test_reset();
    control::test_keyboard(&g.keys);
    hud::battle_log::test_set_attached(true);
    ILCT_SetFixedClock(start_ms);
    g.begin(seed);
    digest d;
    const uint32_t epoch = p->epoch;
    patch::heartbeat(epoch);
    const uint64_t stale0 = patch::stale_fallbacks();
    const hud::battle_log::ring_view ring0 = hud::battle_log::ring_stats();
    uint64_t stall_until = 0, stalls = 0, stalled_beats = 0, kill_keys = 0, peak_in_flight = 0;
    uint64_t peak_unread = 0, log_lines = 0, kill_off_until = 0, key_release_at = 0;
    const uint32_t hz = p->frame_hz != 0 ? p->frame_hz : 60;
    uint64_t next_tick = start_ms + TICK_MS, next_beat = start_ms + BEAT_MS, frame_k = 1;
    const uint64_t end = start_ms + duration_ms;
    static hud::battle_log::view lv;
    for (;;) {
        const uint64_t frame_at = start_ms + (frame_k * 1000u) / hz;
        uint64_t t = next_tick;
        if (next_beat < t) {
            t = next_beat;
        }
        if (frame_at < t) {
            t = frame_at;
        }
        if (t > end) {
            break;
        }
        ILCT_SetFixedClock(t);
        if (t == next_tick) {  // input thread
            g.script_tick();
            input::manual_step((t - start_ms) * 1000);
            ++g.ticks;
            next_tick += TICK_MS;
        }
        if (t == next_beat) {  // Lua (POP_EVENTS, commands) and the co-op thread
            next_beat += BEAT_MS;
            if (g.r.one_in(75)) {
                g.random_command(d);
            }
            if (g.r.one_in(9000)) {  // about every 3 minutes
                g.regenerate();
            }
            g.observe(d);
            if (t < stall_until) {
                ++stalled_beats;
                continue;  // the co-op thread is stalled: no beat, no heartbeat, no jobs, no views
            }
            if (g.r.one_in(45000)) {  // about every 15 minutes: a 3 s stall starts
                stall_until = t + 3000;
                ++stalls;
                continue;
            }
            // the kill switch: the Mod Menu action about every 10 minutes (back on after 30 s) and the key now and then
            if (kill_off_until == 0 && g.r.one_in(30000)) {
                control::post_kill(true);
                kill_off_until = t + 30000;
            } else if (kill_off_until != 0 && t >= kill_off_until) {
                control::post_kill(false);
                kill_off_until = 0;
            }
            if (key_release_at == 0 && g.r.one_in(50000)) {
                g.keys.state[0x77] = 1;
                key_release_at = t + 60;
                ++kill_keys;
            } else if (key_release_at != 0 && t >= key_release_at) {
                g.keys.state[0x77] = 0;
                key_release_at = 0;
            }
            control::step(t);
            patch::heartbeat(epoch);
            for (uint32_t k = g.r.below(4); k > 0; --k) {
                uint64_t args[gtq::ARG_COUNT] = {};
                uint32_t ticket = 0;
                gtq::push(SOAK_OP, 0, epoch, gtq::current_generation(), 500, args, &ticket);
            }
            gtq::reap();
            // battle-log traffic: hook events into the ring, the co-op step drains them into lines and a view
            for (uint32_t k = g.r.below(3); k > 0; --k) {
                hud::battle_log::hook_payload hp;
                memset(&hp, 0, sizeof(hp));
                hp.serial = g.r.next() | 1u;
                hp.actor = 0x10000u + g.r.below(8);
                hp.target = 0x20000u + g.r.below(8);
                hp.action = static_cast<uint16_t>(g.r.below(0x1FF));
                hp.value = static_cast<int32_t>(g.r.below(9999));
                const uint32_t msg = hud::battle_log::EV_HIT_BEGIN + g.r.below(5);
                hud::battle_log::ring_put(msg, 0x7000 + g.r.below(3), hp, t);
            }
            const hud::battle_log::ring_view rv = hud::battle_log::ring_stats();
            peak_unread = max64(peak_unread, rv.written - rv.read);
            h.publish_beat(*p, t, false);
            if (hud::battle_log::test_step(&p->snaps[0], t, &lv)) {
                lv.head.epoch = epoch;
                hud::battle_log::test_publish(lv);
                log_lines = lv.line_count;
            }
            const gtq::counters* q = gtq::stats();
            peak_in_flight = max64(peak_in_flight, q->pushed - q->drained - q->reaped);
        }
        if (t == frame_at) {  // the game thread: the UI tick's safe point, the queue, the HUD frame
            uint32_t mask = 0;
            if (control::take_reapply(&mask)) {
                patch::try_apply_subsystems(mask);
            }
            gtq::drain();
            h.frame(*p, t, d);
            ++frame_k;
        }
    }
    g.end();
    const gtq::counters* q = gtq::stats();
    const control::counters* c = control::stats();
    const hud::battle_log::ring_view rv = hud::battle_log::ring_stats();
    const uint64_t v[] = {
        duration_ms, g.ticks, g.beats, h.frames, h.composed, stalls, stalled_beats,
        c->kills, c->revives, kill_keys, g.captures_started, g.settings_applied, g.commands, g.command_busy,
        g.events, g.peak_events_pending, g.last_events_dropped - (g.first_events_dropped == 0xFFFFFFFFu ? 0 : g.first_events_dropped),
        g.last_outbox_dropped,
        q->pushed, q->push_full, q->push_refused, q->drained, q->ran, q->reaped, peak_in_flight,
        rv.written - ring0.written, rv.lost - ring0.lost, peak_unread, log_lines, hud::battle_log::stats()->published,
        h.peak_commands, g.edge_presses, patch::stale_fallbacks() - stale0, c->mask_busy, c->toggles, c->capture_held,
        g.focus_losses, g.disconnects,
    };
    static_assert(sizeof(v) / sizeof(v[0]) == sizeof(SOAK_NAMES) / sizeof(SOAK_NAMES[0]), "one value per name");
    uint32_t n = 0;
    for (; n < cap && n < sizeof(v) / sizeof(v[0]); ++n) {
        out[n] = v[n];
    }
    control::test_keyboard(nullptr);
    return n;
}

}  // extern "C"
