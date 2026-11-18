// Ivalice Legacy co-op overlay: the Legacy script states (see states.h).
#include "overlay/states.h"

#include "control/control.h"
#include "hud/snapshot.h"
#include "hud/snapshot_facts.h"
#include "patch/patch_engine.h"
#include "session/reader.h"
#include "session/session.h"
#include "targets/live.h"

namespace ilc::overlay::states {
namespace {

namespace sf = hud::snapfacts;

// The bridge's latest push (any thread writes through ingest: the bridge runs on the Lua script thread), with when it
// arrived. A seqlock of its own: ilc_hud_states is 296 bytes (a record_slot would do; kept explicit for the pair).
struct pushed_slot {
    volatile uint32_t seq;
    volatile uint32_t valid;
    ilc_hud_states value;
    uint64_t at_ms;
};
pushed_slot g_pushed = {};
volatile uint32_t g_ingest_lock = 0;   // one writer at a time (two Lua threads could push)
patch::record_slot<record> g_slot = {};
counters g_counters = {};
uintptr_t g_base = 0x120000;   // NOTADDR: the pinned image base until start
bool g_published = false;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

void zero(void* p, uint32_t n)
{
    auto* b = static_cast<volatile uint8_t*>(p);
    for (uint32_t k = 0; k < n; ++k) {
        b[k] = 0;
    }
}

void copy(void* dst, const void* src, uint32_t n)
{
    auto* d = static_cast<volatile uint8_t*>(dst);
    const auto* s = static_cast<const volatile uint8_t*>(src);
    for (uint32_t k = 0; k < n; ++k) {
        d[k] = s[k];
    }
}

bool read_pushed(ilc_hud_states* out, uint64_t* at)
{
    for (uint32_t tries = 0; tries < 2; ++tries) {
        const uint32_t first = __atomic_load_n(&g_pushed.seq, __ATOMIC_ACQUIRE);
        if ((first & 1u) != 0) {
            continue;
        }
        const uint32_t ok = g_pushed.valid;
        copy(out, &g_pushed.value, sizeof(*out));
        *at = g_pushed.at_ms;
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&g_pushed.seq, __ATOMIC_RELAXED) == first) {
            return ok == 1;
        }
    }
    return false;
}

struct live_ctx {
    session::game_reader* r;
    const hud::hud_snapshot* snap;
};

// A battle record address -> the actor handle among the units the HUD knows (party rows and targets): their actor's
// +0x698 is that record. 0 when none matches.
uint32_t live_handle(void* ctx, uint64_t record_address)
{
    live_ctx& c = *static_cast<live_ctx*>(ctx);
    if (record_address == 0) {
        return 0;
    }
    const auto check = [&](const hud::hud_unit& u) -> uint32_t {
        if ((u.flags & hud::UNIT_ACTOR) == 0 || u.handle == 0) {
            return 0;
        }
        const uintptr_t actor = targets::live_actor(*c.r, u.handle, false);
        uintptr_t rec = 0;
        return actor != 0 && c.r->ptr(actor + sf::ACTOR_BATTLE, &rec) && rec == record_address ? u.handle : 0;
    };
    for (uint32_t k = 0; k < c.snap->target_count && k < hud::SNAP_TARGETS; ++k) {
        if (const uint32_t h = check(c.snap->targets[k])) {
            return h;
        }
    }
    for (uint32_t k = 0; k < c.snap->member_count && k < hud::SNAP_MEMBERS; ++k) {
        if (const uint32_t h = check(c.snap->members[k])) {
            return h;
        }
    }
    return 0;
}

bool resolve(const ilc_hud_states& in, uint64_t pushed_ms, uint64_t now, resolve_fn handle_of, void* ctx, record* out)
{
    zero(out, sizeof(*out));
    out->head.generation = in.header.generation;
    out->built_ms = now;
    out->pushed_ms = pushed_ms;
    const uint32_t foes = in.foe_count < FOES ? in.foe_count : FOES;
    for (uint32_t k = 0; k < foes; ++k) {
        const ilc_hud_foe& f = in.foes[k];
        const uint32_t h = handle_of(ctx, f.record);
        if (h == 0) {
            bump(g_counters.unresolved);
            continue;
        }
        foe& o = out->foes[out->foe_count++];
        o.handle = h;
        o.flags = f.flags;
        o.broken_s = f.broken_s;
        o.dent_pct = f.dent_pct;
        o.tired = f.tired;
        o.spotter_seat = f.spotter_seat;
        o.crown_element = f.crown_element;
    }
    const uint32_t words = in.word_count < WORDS ? in.word_count : WORDS;
    for (uint32_t k = 0; k < words; ++k) {
        const ilc_hud_word& w = in.words[k];
        if (w.member == 0xFFFF || w.kind == ILC_HUD_WORD_NONE) {
            continue;
        }
        out->words[out->word_count++] = word{w.member, w.kind, w.value};
    }
    const uint32_t procs = in.proc_count < PROCS ? in.proc_count : PROCS;
    for (uint32_t k = 0; k < procs; ++k) {
        const ilc_hud_proc& p = in.procs[k];
        const uint32_t h = handle_of(ctx, p.record);
        if (h == 0 || p.tag == ILC_HUD_TAG_NONE) {
            continue;
        }
        proc& o = out->procs[out->proc_count++];
        o.handle = h;
        o.tag = p.tag;
        // When it fired, on our clock: the push's arrival less the age the script reported.
        o.at_ms = pushed_ms > p.age_ms ? pushed_ms - p.age_ms : 1;
    }
    for (uint32_t k = out->word_count; k < WORDS; ++k) {
        out->words[k].member = 0xFFFF;
    }
    return true;
}

}  // namespace

// ---- pure --------------------------------------------------------------------------------------------------------------
const char* tag_name(uint32_t tag)
{
    switch (tag) {
    case ILC_HUD_TAG_SUNDER: return "SUNDER";
    case ILC_HUD_TAG_BREAK: return "BREAK";
    case ILC_HUD_TAG_CADENCE: return "CADENCE";
    case ILC_HUD_TAG_COUNTERFLOW: return "COUNTERFLOW";
    case ILC_HUD_TAG_GUARD: return "GUARD";
    case ILC_HUD_TAG_UNSEEN: return "UNSEEN";
    case ILC_HUD_TAG_PIERCE: return "PIERCE";
    case ILC_HUD_TAG_MEND: return "MEND";
    case ILC_HUD_TAG_PROFICIENCY: return "PROFICIENCY";
    case ILC_HUD_TAG_SPOTTED: return "SPOTTED";
    case ILC_HUD_TAG_KINDLE: return "KINDLE";
    default: return "";
    }
}

const char* word_label(uint32_t kind)
{
    switch (kind) {
    case ILC_HUD_WORD_GUARD: return "Guard";
    case ILC_HUD_WORD_CADENCE: return "Cadence";
    case ILC_HUD_WORD_FLOW: return "Flow";
    default: return "";
    }
}

bool word_live(const word& w)
{
    switch (w.kind) {
    case ILC_HUD_WORD_GUARD: return w.value >= 2;
    case ILC_HUD_WORD_CADENCE: return w.value >= 1;
    case ILC_HUD_WORD_FLOW: return w.value >= 10;
    default: return false;
    }
}

const foe* foe_for(const record& r, uint32_t handle)
{
    for (uint32_t k = 0; k < r.foe_count && k < FOES; ++k) {
        if (r.foes[k].handle == handle && handle != 0) {
            return &r.foes[k];
        }
    }
    return nullptr;
}

const word* word_for(const record& r, uint16_t member)
{
    for (uint32_t k = 0; k < r.word_count && k < WORDS; ++k) {
        if (r.words[k].member == member && member != 0xFFFF) {
            return &r.words[k];
        }
    }
    return nullptr;
}

uint8_t proc_for(const record& r, uint32_t actor, uint64_t since, uint64_t now)
{
    uint8_t tag = 0;
    uint64_t best = 0;
    for (uint32_t k = 0; k < r.proc_count && k < PROCS; ++k) {
        const proc& p = r.procs[k];
        if (p.handle != actor || actor == 0 || p.at_ms + 250 < since || p.at_ms > now + 250 ||
            now > p.at_ms + PROC_TAG_MS || p.at_ms < best) {
            continue;
        }
        best = p.at_ms;
        tag = p.tag;
    }
    return tag;
}

// ---- module ------------------------------------------------------------------------------------------------------------
void ingest(const ilc_hud_states& pushed, uint64_t now_ms)
{
    while (__atomic_exchange_n(&g_ingest_lock, 1u, __ATOMIC_ACQUIRE) != 0) {
        // two pushes at once from two Lua threads: the later waits for a copy of 296 bytes
    }
    const uint32_t odd = __atomic_add_fetch(&g_pushed.seq, 1u, __ATOMIC_ACQ_REL);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    copy(&g_pushed.value, &pushed, sizeof(pushed));
    g_pushed.at_ms = now_ms != 0 ? now_ms : 1;
    g_pushed.valid = 1;
    __atomic_store_n(&g_pushed.seq, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_ingest_lock, 0u, __ATOMIC_RELEASE);
    bump(g_counters.pushes);
}

void start(uintptr_t image_base)
{
    g_base = image_base;
}

void step(uint64_t now_ms)
{
    session::session_record srec;
    hud::hud_snapshot snap;
    ilc_hud_states pushed;
    uint64_t at = 0;
    const bool world = session::read(&srec) && srec.head.epoch != 0 && (srec.head.flags & session::GATE_WORLD) != 0 &&
                       control::current_state() == control::STATE_ON && patch::subsystem_live(ILC_SUBSYS_RENDER) &&
                       hud::snapshot::read(snap) && snap.head.epoch == srec.head.epoch;
    // The push is stamped on the script thread, the beat's clock was read before it: a push stamped a little after
    // `now_ms` is fresh too (review: no one-beat blink), at most SKEW_MS ahead.
    const bool fresh = world && read_pushed(&pushed, &at) &&
                       (now_ms >= at ? now_ms - at <= STALE_MS : at - now_ms <= SKEW_MS);
    if (!fresh) {
        if (world) {
            bump(g_counters.stale);
        }
        if (g_published) {
            g_slot.withdraw();
            g_published = false;
        }
        return;
    }
    session::game_reader r(g_base);
    live_ctx ctx = {&r, &snap};
    static record next;
    resolve(pushed, at, now_ms, live_handle, &ctx, &next);
    next.head.epoch = srec.head.epoch;
    g_slot.publish(next);
    g_published = true;
    bump(g_counters.published);
}

void stop()
{
    g_slot.withdraw();
    g_published = false;
}

bool read(record& out)
{
    return g_slot.read(out);
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    g_slot.withdraw();
    g_published = false;
    zero(&g_counters, sizeof(g_counters));
    __atomic_add_fetch(&g_pushed.seq, 2u, __ATOMIC_ACQ_REL);
    g_pushed.valid = 0;
}

void test_publish(const record& r)
{
    g_slot.publish(r);
    g_published = true;
}

bool test_resolve(const ilc_hud_states& pushed, uint64_t now_ms, resolve_fn handle_of, void* ctx, record* out)
{
    return resolve(pushed, now_ms, now_ms, handle_of, ctx, out);
}

}  // namespace ilc::overlay::states
