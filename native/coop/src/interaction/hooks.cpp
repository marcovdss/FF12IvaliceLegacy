// Ivalice Legacy co-op module: the interaction detours and the consumer window (see interaction.h).
//
// Every handler runs on the game thread and never blocks or allocates. The handlers at mid-function sites
// (MV-08, IX-02..IX-10, IX-17..IX-19) touch game memory only through the fault-safe probe and call nothing; the
// ones at call rows (MV-07, IX-01, IX-20, LT-01) run at a call boundary with static unwind data and call the game's
// own functions there, exactly where the game itself would. IX-02..IX-10 and IX-17..IX-19 act only inside our own
// nested call (a private pass started by IX-01, the exact NPC scan started by IX-20), which runs synchronously on
// this thread; outside it they run the original instructions.
#include <string.h>

#include "interaction/interaction.h"
#include "interaction/interaction_facts.h"
#include "interaction/rules.h"
#include "patch/call_island.h"
#include "patch/detour.h"
#include "runtime/probe.h"
#include "session/session.h"

namespace ilc::interaction {

namespace detail {
extern patch::record_slot<ix_record> g_slot;
extern patch::record_slot<ix_private> g_private[SLOTS];
extern counters g_counters;
extern request g_request;
}  // namespace detail

namespace {

using patch::ctx;
using patch::RESUME;
using patch::RUN_ORIGINAL;

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr bool patch_row(int32_t row, uint8_t kind, uint32_t subsystem)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH && patch::SITES[row].kind == kind &&
           patch::SITES[row].subsystem == subsystem;
}

constexpr uint32_t MOVEMENT = ILC_SITE_SUBSYS_MOVEMENT;
constexpr int32_t MV07 = find_row("MV-07");
constexpr int32_t MV08 = find_row("MV-08");
constexpr int32_t IX01 = find_row("IX-01");
constexpr int32_t IX02 = find_row("IX-02");
constexpr int32_t IX03 = find_row("IX-03");
constexpr int32_t IX04 = find_row("IX-04");
constexpr int32_t IX05 = find_row("IX-05");
constexpr int32_t IX06 = find_row("IX-06");
constexpr int32_t IX07 = find_row("IX-07");
constexpr int32_t IX08 = find_row("IX-08");
constexpr int32_t IX09 = find_row("IX-09");
constexpr int32_t IX10 = find_row("IX-10");
constexpr int32_t IX17 = find_row("IX-17");
constexpr int32_t IX18 = find_row("IX-18");
constexpr int32_t IX19 = find_row("IX-19");
constexpr int32_t IX20 = find_row("IX-20");
constexpr int32_t LT01 = find_row("LT-01");
static_assert(patch_row(MV07, ILC_SITE_KIND_CALL, MOVEMENT) && patch_row(IX01, ILC_SITE_KIND_CALL, MOVEMENT) &&
                  patch_row(IX20, ILC_SITE_KIND_CALL, MOVEMENT) &&
                  patch_row(LT01, ILC_SITE_KIND_CALL, ILC_SITE_SUBSYS_PARTY),
              "the call-type interaction rows");
static_assert(patch::island::is_call_row(static_cast<uint32_t>(MV07)) &&
                  patch::island::is_call_row(static_cast<uint32_t>(IX01)) &&
                  patch::island::is_call_row(static_cast<uint32_t>(IX20)) &&
                  patch::island::is_call_row(static_cast<uint32_t>(LT01)),
              "MV-07, IX-01, IX-20 and LT-01 run through call islands (their handlers may call the game)");
static_assert(patch_row(MV08, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX02, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX03, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX04, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX05, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX06, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX07, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX08, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX09, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX10, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX17, ILC_SITE_KIND_DETOUR, MOVEMENT) && patch_row(IX18, ILC_SITE_KIND_DETOUR, MOVEMENT) &&
                  patch_row(IX19, ILC_SITE_KIND_DETOUR, MOVEMENT),
              "the mid-function interaction rows");
static_assert(!patch::island::is_call_row(static_cast<uint32_t>(IX02)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(IX17)),
              "mid-function rows never call the game");

static_assert(patch::IMAGE_BASE == facts::IMAGE_VA, "the pinned image base");
constexpr uint64_t va(uintptr_t v)
{
    return static_cast<uint64_t>(patch::IMAGE_BASE + (v - facts::IMAGE_VA));
}

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

template <typename T>
bool load(uint64_t address, T* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(T));
}

template <typename T>
bool store(uint64_t address, const T& value)
{
    return address != 0 && copy_to_caller(reinterpret_cast<void*>(address), &value, sizeof(T));
}

bool load_bytes(uint64_t address, void* out, size_t n)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), n);
}

bool store_bytes(uint64_t address, const void* data, size_t n)
{
    return address != 0 && copy_to_caller(reinterpret_cast<void*>(address), data, n);
}

// ---- the game's functions (replaceable by host tests) ------------------------------------------------------------
game_calls g_game = {};
const game_calls* volatile g_override = nullptr;
volatile uint32_t g_test_fail_attach = 0;  // host tests: attach() fails after arming its rows

void fill_game_calls()
{
    g_game.resolve = reinterpret_cast<uint64_t (*)(uint32_t)>(va(facts::FN_RESOLVE));
    g_game.generic_scan = reinterpret_cast<void (*)(int32_t)>(va(facts::FN_GENERIC_SCAN));
    g_game.exact_scan = reinterpret_cast<void (*)(int32_t)>(va(facts::FN_EXACT_SCAN));
    g_game.build_handle = reinterpret_cast<void (*)()>(va(facts::FN_BUILD_HANDLE));
    g_game.consumer = reinterpret_cast<void (*)()>(va(facts::FN_CONSUMER));
    g_game.gate = reinterpret_cast<int32_t (*)(int32_t)>(va(facts::FN_GATE));
    g_game.auxiliary = reinterpret_cast<uint64_t (*)(uint64_t)>(va(facts::FN_AUXILIARY));
    g_game.gateway = reinterpret_cast<void (*)()>(va(facts::FN_GATEWAY));
}

const game_calls& calls()
{
    const game_calls* o = g_override;
    return o != nullptr ? *o : g_game;
}

// ---- game-thread state of our own nested calls -------------------------------------------------------------------
struct pass_state {
    volatile uint32_t active;  // a private pass runs (IX-01's nested generic scan)
    uint32_t slot;
    uint64_t entry;            // the guest's entry, resolved by IX-01 right before the pass
};
pass_state g_pass = {};
volatile uint32_t g_guard = 0;  // the window's exact NPC scan runs
ix_private g_scratch = {};      // this pass's publications
ix_private g_last[SLOTS] = {};  // per guest: the last pass that found something, plus the counters
volatile int32_t g_last_result = WR_NONE;
volatile uint32_t g_last_policy = 0;

bool in_pass(uint32_t row)
{
    if (__atomic_load_n(&g_pass.active, __ATOMIC_ACQUIRE) != 0 && patch::subsystem_live(MOVEMENT)) {
        return true;
    }
    patch::note_declined(row);
    return false;
}

bool guard_armed(uint32_t row)
{
    if (__atomic_load_n(&g_guard, __ATOMIC_ACQUIRE) != 0 && patch::subsystem_live(MOVEMENT)) {
        bump(detail::g_counters.guard_hits);
        return true;
    }
    patch::note_declined(row);
    return false;
}

int32_t resume_at(ctx* c, uintptr_t target_va)
{
    c->resume = va(target_va);
    return RESUME;
}

bool return_address(const ctx* c, uint64_t* out)
{
    return load(c->rsp, out);  // a call row: [rsp] is the return address the original call pushed
}

// ---- MV-07: gateway trigger call (call row) ---------------------------------------------------------------------
// Live: no arguments; the callee reads the controlled handle itself. The stock pass runs as the game's own call
// (CALL_AROUND), then mv07_after runs one native pass per steering guest.
int32_t mv07_body(ctx* c, const ix_record& r)
{
    (void)c;
    if ((r.head.flags & IXF_GATEWAYS) == 0 || r.active == 0) {
        return RUN_ORIGINAL;
    }
    return patch::CALL_AROUND;
}

// ---- MV-08: gateway collision class store `mov [rcx+0x80],bx` ---------------------------------------------------
// Live: rcx = skeleton keep, bx = the class about to be stored (1 = follower). A guest's skeleton gets class 0 (the
// leader's), as `xor ebx,ebx` would leave it; the store and the byte copy after it then run natively.
int32_t mv08_body(ctx* c, const ix_record& r)
{
    if ((r.head.flags & IXF_GATEWAYS) == 0 || (c->rbx & 0xFFFF) != 1 || c->rcx == 0) {
        return RUN_ORIGINAL;
    }
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) != 0 && r.slot[k].skeleton == c->rcx) {
            c->rbx = 0;
            bump(detail::g_counters.class_rewritten);
            break;
        }
    }
    return RUN_ORIGINAL;
}

// ---- IX-01: dual scan call (call row) ---------------------------------------------------------------------------
// Live: ecx = registry index. For each steering guest: resolve its entry from its handle now, run the native scan
// with the pass flag up (IX-02..IX-10 measure from the guest and publish privately), keep what it found. Then the
// stock scan runs for P1 with every register as it was, after the passes: the scan clears and recomputes the
// objects' range bits, so P1's view of this frame is the last one written.
int32_t ix01_body(ctx* c, const ix_record& r)
{
    if ((r.head.flags & IXF_ROUTING) == 0 || r.active == 0) {
        return RUN_ORIGINAL;
    }
    const game_calls& g = calls();
    const int32_t registry = static_cast<int32_t>(c->rcx);
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) == 0 || r.slot[k].handle == 0) {
            continue;
        }
        ix_private& last = g_last[k];
        ++last.tick;  // an unresolvable guest still completes a pass: an old find ages out
        const uint64_t entry = g.resolve(r.slot[k].handle);
        if (entry != 0) {
            memset(&g_scratch, 0, sizeof(g_scratch));
            g_pass.slot = k;
            g_pass.entry = entry;
            __atomic_store_n(&g_pass.active, 1u, __ATOMIC_RELEASE);
            g.generic_scan(registry);
            __atomic_store_n(&g_pass.active, 0u, __ATOMIC_RELEASE);
            g_pass.entry = 0;
            bump(detail::g_counters.private_passes);
            uint64_t any = 0;
            for (uint32_t s = 0; s < facts::PUBLISH_SLOTS; ++s) {
                any |= g_scratch.resource[s] | g_scratch.text[s] | g_scratch.state[s] | g_scratch.handle[s];
            }
            if (any != 0) {
                memcpy(last.resource, g_scratch.resource, sizeof(last.resource));
                memcpy(last.text, g_scratch.text, sizeof(last.text));
                memcpy(last.state, g_scratch.state, sizeof(last.state));
                memcpy(last.handle, g_scratch.handle, sizeof(last.handle));
                last.mark = last.tick;
                bump(detail::g_counters.private_finds);
            }
        }
        last.source_handle = r.slot[k].handle;
        last.epoch = r.head.epoch;
        detail::g_private[k].publish(last);
    }
    return RUN_ORIGINAL;
}

// ---- IX-02: publisher entry `mov rax,[rsp+0x28]` (leaf: rcx handle, edx slot, r8d state, r9 resource) -----------
// During a pass the publication goes to the guest's private slots and the function returns at its own `ret`,
// leaving rax, rdx and r10 as the stock body would (text, image base, slot).
int32_t ix02_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    const uint32_t row = static_cast<uint32_t>(c->index);
    if (!in_pass(row)) {
        return RUN_ORIGINAL;
    }
    const int32_t slot = static_cast<int32_t>(c->rdx);
    if (slot < 0 || slot >= static_cast<int32_t>(facts::PUBLISH_SLOTS)) {
        return RUN_ORIGINAL;
    }
    uint64_t text = 0;
    if (!load(c->rsp + facts::PUBLISHER_TEXT_ARG, &text)) {
        return RUN_ORIGINAL;
    }
    g_scratch.resource[slot] = c->r9;
    g_scratch.text[slot] = text;
    g_scratch.state[slot] = static_cast<uint32_t>(c->r8);
    g_scratch.handle[slot] = static_cast<uint32_t>(c->rcx);
    c->rax = text;
    c->r10 = static_cast<uint64_t>(static_cast<int64_t>(slot));
    c->rdx = va(facts::IMAGE_VA);
    return resume_at(c, facts::PUBLISHER_RET);
}

// ---- IX-03 / IX-04: the scan sources `mov rax,[source]` / `mov rsi,[source]` ------------------------------------
int32_t ix03_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!in_pass(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    c->rax = g_pass.entry;
    return resume_at(c, facts::SOURCE_AFTER);  // the movaps after the load runs natively
}

int32_t ix04_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!in_pass(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    c->rsi = g_pass.entry;
    return resume_at(c, facts::PROXIMITY_AFTER);
}

// ---- IX-05: enable gate `test ebx,ebx; je reject` -------------------------------------------------------------
int32_t ix05_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (static_cast<uint32_t>(c->rbx) != 0) {
        return RUN_ORIGINAL;  // the game accepts on its own
    }
    if (!in_pass(static_cast<uint32_t>(c->index)) || g_pass.entry == 0) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, facts::ENABLE_ACCEPT);
}

// ---- IX-06 / IX-07: range gates `test byte [rsi+0x5D],mask; je reject` ----------------------------------------
// The range bits are the controlled actor's; during a pass the object is accepted when the scan's source (the
// frame slot [rbp-0x71]) is the guest, and the geometry checks that follow decide.
int32_t range_gate(ctx* c, uint8_t mask, uintptr_t accept)
{
    uint8_t bits = 0;
    if (!load(c->rsi + facts::RANGE_FLAGS, &bits) || (bits & mask) != 0) {
        return RUN_ORIGINAL;
    }
    if (!in_pass(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    uint64_t source = 0;
    if (!load(c->rbp - facts::SOURCE_FRAME, &source) || source != g_pass.entry) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, accept);
}

int32_t ix06_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    return range_gate(c, facts::RANGE_INNER, facts::INNER_ACCEPT);
}

int32_t ix07_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    return range_gate(c, facts::RANGE_OUTER, facts::OUTER_ACCEPT);
}

// ---- IX-08: broadphase `test eax,r15d; je reject` (rbx = the scan source) ----------------------------------------
int32_t ix08_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if ((static_cast<uint32_t>(c->rax) & static_cast<uint32_t>(c->r15)) != 0) {
        return RUN_ORIGINAL;
    }
    if (!in_pass(static_cast<uint32_t>(c->index)) || c->rbx != g_pass.entry) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, facts::BROADPHASE_ACCEPT);
}

// ---- IX-09 / IX-10: proximity and secondary filters `cmp rbx,<source>; jne reject` -------------------------------
int32_t ix09_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!in_pass(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, c->rbx == g_pass.entry ? facts::PROXIMITY_ACCEPT : facts::PROXIMITY_REJECT);
}

int32_t ix10_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!in_pass(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, c->rbx == g_pass.entry ? facts::SECONDARY_ACCEPT : facts::SECONDARY_REJECT);
}

// ---- IX-17..IX-19: the NPC candidate guards (armed only during the window's exact scan) -------------------------
// IX-17 `and byte [rdx+0xC],0xFD; mov rdi,rdx`: keep the NPC's range bit (only the mov runs).
int32_t ix17_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!guard_armed(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    c->rdi = c->rdx;
    return resume_at(c, facts::NPC_ENTRY_AFTER);
}

// IX-18 `or byte [rdi+0xC],2; movzx eax,byte [rdi+0xB]`: no range bit, no proximity timer, no proximity script.
// Continue at the distance bias with al = 0, so the game's own `test bpl,al; je` takes its out-of-range branch:
// the facing cone and the descriptor publication still run with the bias applied, as on the stock path.
int32_t ix18_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!guard_armed(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    c->rax = 0;
    return resume_at(c, facts::NPC_ACCEPT_OUT_OF_RANGE);
}

// IX-19 `movzx eax,byte [rdi+0xB]; test al,8`: skip the proximity cleanup (state and timer stay P1's).
int32_t ix19_body(ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!guard_armed(static_cast<uint32_t>(c->index))) {
        return RUN_ORIGINAL;
    }
    return resume_at(c, facts::NPC_CLEANUP_SKIP);
}

// ---- LT-01: loot ownership query call (call row; ecx = the actor's handle) ----------------------------------------
int32_t lt01_body(ctx* c, const ix_record& r)
{
    const uint32_t handle = static_cast<uint32_t>(c->rcx);
    if ((r.head.flags & IXF_LOOT) == 0 || handle == 0) {
        return RUN_ORIGINAL;
    }
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) != 0 && r.slot[k].handle == handle) {
            uint64_t back = 0;
            if (!return_address(c, &back)) {
                return RUN_ORIGINAL;
            }
            bump(detail::g_counters.loot_owned);
            c->rax = 1;
            c->resume = back;
            return RESUME;
        }
    }
    return RUN_ORIGINAL;
}

// ---- IX-20: the consumer window --------------------------------------------------------------------------------
uint64_t registry_va(int32_t index)
{
    return va(facts::REGISTRY_BASE) + static_cast<uint64_t>(index) * facts::REGISTRY_STRIDE;
}

// The entry the consumer will take for this descriptor, read as the consumer reads it (registry enabled, index
// inside its table); 0 when it would be null.
uint64_t descriptor_target(const uint32_t d[6])
{
    const int32_t reg = static_cast<int32_t>(d[1]);
    const int32_t index = static_cast<int32_t>(d[2]);
    if (reg < 0 || reg >= static_cast<int32_t>(facts::REGISTRIES) || index < 0) {
        return 0;
    }
    const uint64_t registry = registry_va(reg);
    uint8_t flags = 0;
    uint64_t table = 0;
    int32_t count = 0;
    uint64_t entry = 0;
    if (!load(registry + facts::REG_FLAGS, &flags) || (flags & 1) == 0 || !load(registry + facts::REG_TABLE, &table) ||
        table == 0 || !load(table + facts::TABLE_COUNT, &count) || index >= count ||
        !load(table + facts::TABLE_ENTRIES + 8ull * static_cast<uint32_t>(index), &entry)) {
        return 0;
    }
    return entry;
}

bool target_view_of(uint64_t entry, target_view* out)
{
    memset(out, 0, sizeof(*out));
    if (entry == 0) {
        return true;  // not present
    }
    out->present = 1;
    return load(entry + facts::E_TYPE, &out->type) && load(entry + facts::E_FLAGS, &out->flags) &&
           load(entry + facts::E_SCRIPT, &out->script) && load(entry + facts::E_SCRIPT_TABLE, &out->script_table) &&
           load(entry + facts::E_SCRIPT_CONTEXT, &out->script_context);
}

bool actor_facts(uint64_t entry, uint64_t* flags, uint32_t* state, uint32_t* action)
{
    uint64_t actor = 0;
    return load(entry + facts::E_ACTOR, &actor) && actor != 0 && load(actor + facts::ACTOR_FLAGS, flags) &&
           load(actor + facts::ACTOR_STATE, state) && load(actor + facts::ACTOR_ACTION, action);
}

bool guards_open()
{
    return patch::gate(static_cast<uint32_t>(IX17)) != 0 && patch::gate(static_cast<uint32_t>(IX18)) != 0 &&
           patch::gate(static_cast<uint32_t>(IX19)) != 0;
}

struct saved_state {
    uint64_t source;
    uint8_t can_use;
    uint8_t reserved[7];
    uint32_t descriptor[6];
};

bool save_state(saved_state* s)
{
    return load(va(facts::SOURCE), &s->source) && load(va(facts::CAN_USE), &s->can_use) &&
           load_bytes(va(facts::DESCRIPTOR), s->descriptor, sizeof(s->descriptor));
}

void restore_state(const saved_state& s)
{
    store(va(facts::SOURCE), s.source);
    store(va(facts::CAN_USE), s.can_use);
    store_bytes(va(facts::DESCRIPTOR), s.descriptor, sizeof(s.descriptor));
}

// Checks that touch nothing, then the swap. *touched = the game's interaction state was written (and restored).
int32_t run_window(const request& q, bool* touched, uint32_t* target_handle)
{
    *touched = false;
    *target_handle = 0;
    const game_calls& g = calls();
    int32_t pending = 0;
    uint32_t locks = 0;
    if (!load(va(facts::PENDING_AREA), &pending) || pending != -1 || !load(va(facts::LOCK_MASK), &locks) ||
        (locks & facts::LOCK_TRANSITION) != 0) {
        return WR_BLOCKED;
    }
    if (q.policy != POLICY_TREASURE) {
        uint32_t render = 0, suppression = 0, events = 0;
        if (!load(va(facts::RENDER_FLAGS), &render) || (render & facts::RENDER_UI) == 0 ||
            !load(va(facts::SUPPRESSION_FLAGS), &suppression) || (suppression & facts::SUPPRESSION_MASK) != 0 ||
            !load(va(facts::EVENT_FLAGS), &events) || (events & facts::EVENT_BUSY) != 0) {
            return WR_BLOCKED;
        }
    }
    if (q.policy == POLICY_NPC && !guards_open()) {
        return WR_GUARDS_OFF;
    }
    if (g.gate(0) == 0) {
        return WR_P1_BUSY;  // the consumer checks P1's controlled actor whoever starts the interaction
    }
    // The guest's entry, resolved now from its stable handle (never a pointer from another thread).
    const uint64_t owner = g.resolve(q.source_handle);
    if (owner == 0) {
        return WR_SOURCE_GONE;
    }
    uint64_t flags = 0;
    uint32_t state = 0, action = 0;
    if (!actor_facts(owner, &flags, &state, &action)) {
        return WR_SOURCE_GONE;
    }
    if (q.policy != POLICY_TREASURE && !press_allowed(flags, state, action)) {
        return WR_OWNER_BUSY;
    }
    if (q.policy == POLICY_NPC) {
        uint64_t physics = 0;
        if (!load(owner + facts::E_PHYSICS, &physics) || physics == 0) {
            return WR_SOURCE_GONE;  // the exact scan reads the source's physics object without a check
        }
    }
    if (q.policy != POLICY_TREASURE) {
        uint32_t leader_handle = 0;
        uint64_t lf = 0;
        uint32_t ls = 0, la = 0;
        const uint64_t leader = load(va(facts::CONTROLLED_HANDLE), &leader_handle) ? g.resolve(leader_handle) : 0;
        if (leader == 0 || !actor_facts(leader, &lf, &ls, &la) || !leader_allows(lf)) {
            return WR_LEADER_BUSY;
        }
    }
    saved_state saved;
    if (!save_state(&saved)) {
        return WR_BLOCKED;
    }

    // From here on the game's interaction state is ours until restore_state().
    *touched = true;
    int32_t result = WR_TARGET_STALE;
    uint32_t d[6];
    store(va(facts::SOURCE), owner);
    if (q.policy == POLICY_NPC) {
        reset_descriptor(d);
        store(va(facts::CAN_USE), static_cast<uint8_t>(0));
        store_bytes(va(facts::DESCRIPTOR), d, sizeof(d));
        __atomic_store_n(&g_guard, 1u, __ATOMIC_RELEASE);
        for (int32_t reg = 0; reg < static_cast<int32_t>(facts::REGISTRIES); ++reg) {
            g.exact_scan(reg);
        }
        __atomic_store_n(&g_guard, 0u, __ATOMIC_RELEASE);
        g.build_handle();
        uint8_t usable = 0;
        if (!load(va(facts::CAN_USE), &usable) || usable != 1 || !load_bytes(va(facts::DESCRIPTOR), d, sizeof(d))) {
            restore_state(saved);
            return WR_NO_TARGET;
        }
    } else {
        forced_descriptor(q.object_handle, q.object_state, saved.descriptor[5], d);
        store_bytes(va(facts::DESCRIPTOR), d, sizeof(d));
        store(va(facts::CAN_USE), static_cast<uint8_t>(1));
    }
    *target_handle = d[4];

    // The target, immediately before the call: the consumer takes registry[d1].table[d2] and dereferences it
    // without a check. A stale descriptor (the entry gone, its handle reused, a sub-object torn down) never reaches
    // the consumer.
    uint64_t target = descriptor_target(d);
    target_view view;
    if (target != 0 && d[4] != 0 && g.resolve(d[4]) != target) {
        target = 0;  // the handle's generation no longer names this entry
    }
    if (!target_view_of(target, &view) || target_ready(view, d[3]) != TARGET_OK) {
        restore_state(saved);
        return WR_TARGET_STALE;
    }
    if ((view.flags & facts::E_FLAG_AUX) != 0) {
        if (g.auxiliary(target) == 0) {
            restore_state(saved);
            return WR_TARGET_STALE;
        }
        // The resolver may have changed the tables: the entry read now must be the one validated above, and it is
        // checked again in full (handle generation, sub-objects, script context). Anything else is refused.
        const uint64_t again = descriptor_target(d);
        target_view view_again;
        if (again != target || (d[4] != 0 && g.resolve(d[4]) != again) || !target_view_of(again, &view_again) ||
            target_ready(view_again, d[3]) != TARGET_OK) {
            restore_state(saved);
            return WR_TARGET_STALE;
        }
    }
    // The consumer expects the target in range of the source (bit 1, which the controlled actor's own scan sets).
    // Set it for this call only; put the old byte back unless the consumer changed it.
    uint8_t range = 0;
    if (!load(target + facts::E_RANGE, &range)) {
        restore_state(saved);
        return WR_TARGET_STALE;
    }
    const uint8_t armed = static_cast<uint8_t>(range | facts::E_RANGE_BIT);
    store(target + facts::E_RANGE, armed);
    g.consumer();
    result = WR_CONSUMED;
    const uint64_t after = descriptor_target(d);  // the consumer may have removed the target
    if (after == target) {
        uint8_t now = 0;
        if (load(target + facts::E_RANGE, &now) && now == armed) {
            store(target + facts::E_RANGE, range);
        }
        uint8_t talk = 0;
        if (q.policy == POLICY_NPC && load(target + facts::E_TALK, &talk) && (talk & facts::E_TALK_ACTIVE) != 0) {
            result = WR_TALK;
        }
    }
    restore_state(saved);
    return result;
}

// The world the request was made for: same epoch, a valid world, the same seat layout (a swap or a seat change
// bumps the session generation), and the requesting seat still steers the character the request names.
bool world_ok(const request& q, uint32_t epoch)
{
    session::session_record s;
    return session::read(&s) && s.head.epoch == epoch && (s.head.flags & session::GATE_WORLD) != 0 &&
           static_cast<uint32_t>(s.head.generation) == q.generation && q.seat >= 1 && q.seat < ACTIVE_SEATS &&
           s.seat[q.seat].handle == q.source_handle;
}

void count_result(int32_t result)
{
    const int32_t index = result >= 0 ? result : -result - 13;
    if (index >= 0 && index < 12) {
        bump(detail::g_counters.results[index]);
    }
}

}  // namespace

int32_t ix02(ctx* c, uint32_t epoch)
{
    return ix02_body(c, epoch);
}

int32_t ix03(ctx* c, uint32_t epoch)
{
    return ix03_body(c, epoch);
}

int32_t ix04(ctx* c, uint32_t epoch)
{
    return ix04_body(c, epoch);
}

int32_t ix05(ctx* c, uint32_t epoch)
{
    return ix05_body(c, epoch);
}

int32_t ix06(ctx* c, uint32_t epoch)
{
    return ix06_body(c, epoch);
}

int32_t ix07(ctx* c, uint32_t epoch)
{
    return ix07_body(c, epoch);
}

int32_t ix08(ctx* c, uint32_t epoch)
{
    return ix08_body(c, epoch);
}

int32_t ix09(ctx* c, uint32_t epoch)
{
    return ix09_body(c, epoch);
}

int32_t ix10(ctx* c, uint32_t epoch)
{
    return ix10_body(c, epoch);
}

int32_t ix17(ctx* c, uint32_t epoch)
{
    return ix17_body(c, epoch);
}

int32_t ix18(ctx* c, uint32_t epoch)
{
    return ix18_body(c, epoch);
}

int32_t ix19(ctx* c, uint32_t epoch)
{
    return ix19_body(c, epoch);
}

int32_t mv07(ctx* c, uint32_t epoch)
{
    return patch::fail_open<ix_record, detail::g_slot, mv07_body>(c, epoch);
}

// After the stock pass (registers as the callee returned them, which are put back unchanged). One native pass per
// steering guest: the controlled handle names the guest for the call and is restored right after. Never twice for
// one character (a guest whose handle is the controlled one is P1's leader this frame), and no further pass once a
// pass has requested an area change.
void mv07_after(ctx* c, uint32_t epoch)
{
    (void)c;
    ix_record r;
    if (!patch::subsystem_live(MOVEMENT) || !detail::g_slot.read(r) || r.head.epoch != epoch || epoch == 0 ||
        (r.head.flags & IXF_GATEWAYS) == 0) {
        return;
    }
    const uint64_t controlled = va(facts::CONTROLLED_HANDLE);
    uint32_t own = 0;
    if (!load(controlled, &own)) {
        return;
    }
    const game_calls& g = calls();
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) == 0) {
            continue;
        }
        const uint32_t handle = r.slot[k].handle;
        int32_t pending = 0;
        if (handle == 0 || handle == own || !load(va(facts::PENDING_AREA), &pending) || pending != -1) {
            bump(detail::g_counters.gateway_skipped);
            continue;
        }
        if (!store(controlled, handle)) {
            continue;
        }
        g.gateway();
        store(controlled, own);
        bump(detail::g_counters.gateway_passes);
    }
    store(controlled, own);
}

int32_t mv08(ctx* c, uint32_t epoch)
{
    return patch::fail_open<ix_record, detail::g_slot, mv08_body>(c, epoch);
}

int32_t ix01(ctx* c, uint32_t epoch)
{
    return patch::fail_open<ix_record, detail::g_slot, ix01_body>(c, epoch);
}

int32_t lt01(ctx* c, uint32_t epoch)
{
    return patch::fail_open<ix_record, detail::g_slot, lt01_body>(c, epoch);
}

// The window. With no request armed (every frame but the guest's press) it is the game's own consumer call. A
// request is claimed only while P1's own trigger byte is clear: P1's interaction this frame goes first and the
// request waits for the next frame (or its deadline).
int32_t ix20(ctx* c, uint32_t epoch)
{
    const uint32_t row = static_cast<uint32_t>(c->index);
    request& q = detail::g_request;
    if (!patch::subsystem_live(MOVEMENT)) {
        patch::note_declined(row);
        return RUN_ORIGINAL;
    }
    if (__atomic_load_n(&q.state, __ATOMIC_ACQUIRE) != REQ_ARMED) {
        return RUN_ORIGINAL;
    }
    uint8_t trigger = 0;
    if (!load(va(facts::TRIGGER), &trigger) || (trigger & 1) != 0) {
        return RUN_ORIGINAL;
    }
    // Where the consumer call returns to, read before anything runs: without it the window could not take this
    // frame's call, and the game's own consumer would run a second time after ours. The request waits instead.
    uint64_t back = 0;
    if (!return_address(c, &back)) {
        return RUN_ORIGINAL;
    }
    uint32_t expected = REQ_ARMED;
    if (!__atomic_compare_exchange_n(&q.state, &expected, static_cast<uint32_t>(REQ_CLAIMED), false,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        return RUN_ORIGINAL;
    }
    bump(detail::g_counters.windows);
    bool touched = false;
    uint32_t target = 0;
    int32_t result = WR_STALE;
    if (q.epoch == epoch && epoch != 0 && patch::now_ms() <= q.deadline_ms && world_ok(q, epoch) &&
        q.source_handle != 0 && q.policy >= POLICY_LEADER && q.policy <= POLICY_NPC) {
        result = run_window(q, &touched, &target);
    }
    count_result(result);
    __atomic_store_n(&g_last_result, result, __ATOMIC_RELAXED);
    __atomic_store_n(&g_last_policy, q.policy, __ATOMIC_RELAXED);
    q.result = result;
    q.target_handle = target;
    q.touched = touched ? 1u : 0u;
    __atomic_store_n(&q.state, static_cast<uint32_t>(REQ_DONE), __ATOMIC_RELEASE);
    if (!touched) {
        return RUN_ORIGINAL;  // nothing of the game's was written: its own consumer call runs as usual
    }
    c->rax = 0;
    c->resume = back;  // the window consumed this frame's consumer call (P1's trigger was clear)
    return RESUME;
}

int32_t row_of(const char* id)
{
    return find_row(id);
}

bool attach()
{
    fill_game_calls();
    struct binding {
        int32_t row;
        patch::handler_fn fn;
    };
    const binding rows[] = {{MV07, mv07}, {MV08, mv08}, {IX01, ix01}, {IX02, ix02}, {IX03, ix03}, {IX04, ix04},
                            {IX05, ix05}, {IX06, ix06}, {IX07, ix07}, {IX08, ix08}, {IX09, ix09}, {IX10, ix10},
                            {IX17, ix17}, {IX18, ix18}, {IX19, ix19}, {IX20, ix20}, {LT01, lt01}};
    bool ok = patch::set_after(static_cast<uint32_t>(MV07), mv07_after);
    for (const binding& b : rows) {
        ok = ok && patch::set_handler(static_cast<uint32_t>(b.row), b.fn);
    }
    for (const binding& b : rows) {
        ok = ok && patch::set_site_armed(static_cast<uint32_t>(b.row), true);
    }
    ok = ok && __atomic_load_n(&g_test_fail_attach, __ATOMIC_ACQUIRE) == 0;
    if (!ok) {
        for (const binding& b : rows) {
            patch::set_site_armed(static_cast<uint32_t>(b.row), false);
        }
    }
    return ok;
}

namespace hooks_detail {

void reset_game_thread_state()
{
    memset(&g_pass, 0, sizeof(g_pass));
    g_guard = 0;
    memset(&g_scratch, 0, sizeof(g_scratch));
    memset(g_last, 0, sizeof(g_last));
    g_last_result = WR_NONE;
    g_last_policy = 0;
    fill_game_calls();
}

void set_override(const game_calls* c)
{
    g_override = c;
}

void set_pass(uint32_t active, uint32_t slot, uint64_t entry)
{
    g_pass.slot = slot;
    g_pass.entry = entry;
    __atomic_store_n(&g_pass.active, active, __ATOMIC_RELEASE);
}

void set_guard(uint32_t active)
{
    __atomic_store_n(&g_guard, active, __ATOMIC_RELEASE);
}

void read_state(uint32_t* pass_active, uint32_t* pass_slot, uint64_t* pass_entry, uint32_t* guard, int32_t* result,
                uint32_t* policy)
{
    *pass_active = g_pass.active;
    *pass_slot = g_pass.slot;
    *pass_entry = g_pass.entry;
    *guard = g_guard;
    *result = g_last_result;
    *policy = g_last_policy;
}

}  // namespace hooks_detail

void test_fail_attach(bool fail)
{
    __atomic_store_n(&g_test_fail_attach, fail ? 1u : 0u, __ATOMIC_RELEASE);
}

}  // namespace ilc::interaction
