// Ivalice Legacy co-op module, host tests only: the call-island exports (ILCT_Island*).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "patch/call_island.h"
#include "patch/patch_engine.h"

using namespace ilc::patch;

namespace {

constexpr uint64_t FAKE_RESULT = 0x5EED5EED5EED5EEDull;  // NOTADDR: marker a RESUME handler returns in rax

int32_t resume_handler(ctx* c, uint32_t)
{
    c->rax = FAKE_RESULT;
    c->resume = *reinterpret_cast<const uint64_t*>(c->rsp);  // as if the callee had returned
    return RESUME;
}

int32_t around_handler(ctx*, uint32_t)
{
    return CALL_AROUND;
}

void after_noop(ctx*, uint32_t) {}

void after_mark(ctx* c, uint32_t)
{
    c->rax ^= FAKE_RESULT;
}

}  // namespace

extern "C" {

struct ilct_island_info {
    uint32_t is_call_row;
    uint32_t call_offset;
    uint64_t island;     // 0 unless prepared
    uint64_t entry;
    uint64_t slow;
    uint64_t callee;     // 0 unless prepared
    uint64_t slot;       // what the entry jumps to now
    uint64_t common;     // the shared body (for the unwind test)
    uint64_t slot_address;  // &ilc_call_slot[k]: emulators force the gate by writing it (with the gate byte)
};

extern void* volatile ilc_call_slot[];
extern const char ilc_call_common[];

int32_t ILCT_IslandInfo(uint32_t row, ilct_island_info* out)
{
    memset(out, 0, sizeof(*out));
    if (row >= SITE_COUNT) {
        return 0;
    }
    out->is_call_row = island::is_call_row(row) ? 1 : 0;
    out->call_offset = island::call_offset(SITES[row]);
    if (!out->is_call_row) {
        return 1;
    }
    out->island = island::island_address(row);
    out->entry = reinterpret_cast<uint64_t>(island::entry(row));
    out->slow = reinterpret_cast<uint64_t>(island::slow(row));
    out->callee = island::callee(row);
    out->slot = reinterpret_cast<uint64_t>(ilc_call_slot[island::index_of(row)]);
    out->common = reinterpret_cast<uint64_t>(ilc_call_common);
    out->slot_address = reinterpret_cast<uint64_t>(&ilc_call_slot[island::index_of(row)]);
    return 1;
}

uint32_t ILCT_IslandRows(void)
{
    return island::ROWS;
}

uint64_t ILCT_IslandCalleeRva(uint32_t row)
{
    return island::is_call_row(row) ? island::callee_rva(row) : 0;
}

uint64_t ILCT_IslandRegion(void)
{
    return island::stats().region;
}

// mode: 0 none, 1 RESUME handler, 2 CALL_AROUND + no-op after, 3 CALL_AROUND + after that flips rax by the marker,
// 4 CALL_AROUND without an after-handler.
int32_t ILCT_IslandSetHandler(uint32_t row, int32_t mode)
{
    if (!is_call_row(row)) {
        return 0;
    }
    switch (mode) {
    case 1:
        set_after(row, nullptr);
        return set_handler(row, resume_handler) ? 1 : 0;
    case 2:
        set_after(row, after_noop);
        return set_handler(row, around_handler) ? 1 : 0;
    case 3:
        set_after(row, after_mark);
        return set_handler(row, around_handler) ? 1 : 0;
    case 4:
        set_after(row, nullptr);
        return set_handler(row, around_handler) ? 1 : 0;
    default:
        set_after(row, nullptr);
        return set_handler(row, nullptr) ? 1 : 0;
    }
}

void ILCT_IslandFailWrite(uint32_t row, uint32_t restore_row)
{
    island::test_fail_write(row, restore_row);
}

uint32_t ILCT_IslandCommitted(uint32_t row)
{
    return island::island_address(row) != 0 && island::committed() ? 1u : 0u;
}

uint64_t ILCT_IslandFakeResult(void)
{
    return FAKE_RESULT;
}

void ILCT_IslandHandlers(uint64_t out[5])
{
    out[0] = reinterpret_cast<uint64_t>(&resume_handler);
    out[1] = reinterpret_cast<uint64_t>(&around_handler);
    out[2] = reinterpret_cast<uint64_t>(&after_noop);
    out[3] = reinterpret_cast<uint64_t>(&after_mark);
    out[4] = 0;
}

}  // extern "C"
