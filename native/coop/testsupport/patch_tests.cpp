// Ivalice Legacy co-op module, host tests only: the patch layer's ILCT_Patch* exports and a minimal image twin.
//
// The image twin maps FFXII_TZA.exe at its pinned base (0x120000) inside the test process: headers and sections
// copied from the file, each with its own page protection; imports unresolved, no relocations, and never executed
// natively (host tests read it, MinHook patches it, unicorn runs pieces of it). twin_drills.cpp adds parked
// threads, allocation counters and the game-thread tick exports.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "patch/patch_engine.h"

using namespace ilc::patch;

namespace {

uint8_t* g_twin = nullptr;
size_t g_twin_size = 0;

DWORD section_protection(DWORD characteristics)
{
    const bool x = (characteristics & IMAGE_SCN_MEM_EXECUTE) != 0;
    const bool w = (characteristics & IMAGE_SCN_MEM_WRITE) != 0;
    const bool r = (characteristics & IMAGE_SCN_MEM_READ) != 0;
    if (x) {
        return w ? PAGE_EXECUTE_READWRITE : (r ? PAGE_EXECUTE_READ : PAGE_EXECUTE);
    }
    return w ? PAGE_READWRITE : (r ? PAGE_READONLY : PAGE_NOACCESS);
}

int32_t passthrough_handler(ctx* c, uint32_t epoch)
{
    (void)c;
    (void)epoch;
    return RUN_ORIGINAL;
}

}  // namespace

extern "C" {

// Maps (or re-maps from the file, restoring every byte and protection) the exe at its preferred base. Returns the
// base, or 0 on failure. `file` is the exe's bytes, read by the test.
uint64_t ILCT_PatchTwinMap(const uint8_t* file, uint64_t file_size)
{
    if (file_size < 0x400 || file[0] != 'M' || file[1] != 'Z') {
        return 0;
    }
    uint32_t lfanew;
    memcpy(&lfanew, file + 0x3C, 4);
    if (lfanew + sizeof(IMAGE_NT_HEADERS64) > file_size) {
        return 0;
    }
    IMAGE_NT_HEADERS64 nt;
    memcpy(&nt, file + lfanew, sizeof(nt));
    const uintptr_t base = static_cast<uintptr_t>(nt.OptionalHeader.ImageBase);
    const size_t size = nt.OptionalHeader.SizeOfImage;
    if (g_twin == nullptr) {
        g_twin = static_cast<uint8_t*>(VirtualAlloc(reinterpret_cast<void*>(base), size, MEM_RESERVE | MEM_COMMIT,
                                                    PAGE_READWRITE));
        if (g_twin == nullptr || reinterpret_cast<uintptr_t>(g_twin) != base) {
            g_twin = nullptr;
            return 0;
        }
        g_twin_size = size;
    }
    if (reinterpret_cast<uintptr_t>(g_twin) != base || g_twin_size != size) {
        return 0;
    }
    DWORD old;
    if (!VirtualProtect(g_twin, size, PAGE_READWRITE, &old)) {
        return 0;
    }
    memset(g_twin, 0, size);
    const size_t headers = nt.OptionalHeader.SizeOfHeaders;
    memcpy(g_twin, file, headers < file_size ? headers : file_size);
    const IMAGE_SECTION_HEADER* sections = reinterpret_cast<const IMAGE_SECTION_HEADER*>(
        file + lfanew + offsetof(IMAGE_NT_HEADERS64, OptionalHeader) + nt.FileHeader.SizeOfOptionalHeader);
    for (unsigned i = 0; i < nt.FileHeader.NumberOfSections; ++i) {
        const IMAGE_SECTION_HEADER& s = sections[i];
        const size_t raw = s.SizeOfRawData < s.Misc.VirtualSize ? s.SizeOfRawData : s.Misc.VirtualSize;
        if (s.PointerToRawData + raw > file_size || s.VirtualAddress + raw > size) {
            return 0;
        }
        memcpy(g_twin + s.VirtualAddress, file + s.PointerToRawData, raw);
    }
    if (!VirtualProtect(g_twin, headers, PAGE_READONLY, &old)) {
        return 0;
    }
    for (unsigned i = 0; i < nt.FileHeader.NumberOfSections; ++i) {
        const IMAGE_SECTION_HEADER& s = sections[i];
        const size_t span = (s.Misc.VirtualSize + 0xFFF) & ~static_cast<size_t>(0xFFF);
        if (span && !VirtualProtect(g_twin + s.VirtualAddress, span, section_protection(s.Characteristics), &old)) {
            return 0;
        }
    }
    FlushInstructionCache(GetCurrentProcess(), g_twin, size);
    return base;
}

// Writes bytes into the twin regardless of protection (to corrupt a site or a header field for a test).
int32_t ILCT_PatchTwinPoke(uint64_t address, const uint8_t* data, uint32_t count)
{
    DWORD old;
    if (!VirtualProtect(reinterpret_cast<void*>(address), count, PAGE_EXECUTE_READWRITE, &old)) {
        return 0;
    }
    memcpy(reinterpret_cast<void*>(address), data, count);
    DWORD ignored;
    VirtualProtect(reinterpret_cast<void*>(address), count, old, &ignored);
    return 1;
}

int32_t ILCT_PatchPreflight(uint64_t base, report* out)
{
    return preflight(static_cast<uintptr_t>(base), out);
}

int32_t ILCT_PatchInstall(uint64_t base, report* out)
{
    return install(static_cast<uintptr_t>(base), out);
}

int32_t ILCT_PatchReleaseAll(void)
{
    return test_release_all();
}

void ILCT_PatchInject(uint32_t stage, uint32_t row)
{
    test_inject(stage, row);
}

uint32_t ILCT_PatchState(void)
{
    return current_state();
}

uint32_t ILCT_PatchSiteCount(void)
{
    return SITE_COUNT;
}

struct ilct_patch_site {
    char id[16];
    uint32_t rva;
    uint8_t length, kind, policy, flags;
    uint32_t subsystem;
    uint16_t gate;
    uint8_t hook_len, tail_len;
    uint64_t stub;
    uint64_t trampoline;
    uint8_t gate_open;
    uint8_t reserved[7];
};

int32_t ILCT_PatchSiteInfo(uint32_t row, ilct_patch_site* out)
{
    if (row >= SITE_COUNT) {
        return 0;
    }
    const site& s = SITES[row];
    memset(out, 0, sizeof(*out));
    strncpy(out->id, s.id, sizeof(out->id) - 1);
    out->rva = s.rva;
    out->length = s.length;
    out->kind = s.kind;
    out->policy = s.policy;
    out->flags = s.flags;
    out->subsystem = s.subsystem;
    out->gate = s.gate;
    out->hook_len = s.hook_len;
    out->tail_len = s.tail_len;
    out->stub = reinterpret_cast<uint64_t>(stub_address(row));
    out->trampoline = reinterpret_cast<uint64_t>(trampoline(row));
    out->gate_open = gate(row);
    return 1;
}

struct ilct_patch_addresses {
    uint64_t gate_array;
    uint64_t tramp_array;
    uint64_t clock_slot;
    uint64_t liveness;
    uint64_t stats0;
    uint64_t stats_stride;
    uint64_t stub0;
    uint64_t passthrough;
};

void ILCT_PatchAddresses(ilct_patch_addresses* out)
{
    out->gate_array = reinterpret_cast<uint64_t>(test_gate_array());
    out->tramp_array = reinterpret_cast<uint64_t>(test_trampoline_array());
    out->clock_slot = reinterpret_cast<uint64_t>(test_clock_slot());
    out->liveness = reinterpret_cast<uint64_t>(liveness_record());
    out->stats0 = reinterpret_cast<uint64_t>(stats(0));
    out->stats_stride = sizeof(site_stats);
    out->stub0 = reinterpret_cast<uint64_t>(stub_address(0));
    out->passthrough = reinterpret_cast<uint64_t>(&passthrough_handler);
}

int32_t ILCT_PatchSetPassthrough(uint32_t row, int32_t on)
{
    return set_handler(row, on ? passthrough_handler : nullptr) ? 1 : 0;
}

int32_t ILCT_PatchSetSiteArmed(uint32_t row, int32_t on)
{
    return set_site_armed(row, on != 0) ? 1 : 0;
}

void ILCT_PatchApplySubsystems(uint32_t mask)
{
    apply_subsystems(mask);
}

void ILCT_PatchSetArmed(int32_t on)
{
    set_armed(on != 0);
}

void ILCT_PatchHeartbeat(uint32_t epoch)
{
    heartbeat(epoch);
}

}  // extern "C"
