// Ivalice Legacy co-op module, host tests only: the boot's ILCT_Boot* exports.
//
// A host test tells the boot which process it runs in (image base, the x64 folder, the process id for mutex names,
// the instance-guard name), can pause the init thread at a step, and reads the status, the marker table and the
// lifecycle event order. Nothing here is reachable from the shipped DLL.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "boot/boot.h"
#include "boot/settings_file.h"
#include "boot/together_detect.h"

using namespace ilc::boot;

namespace {

volatile uint32_t g_pause_step = 0;   // 0 = no pause
volatile uint32_t g_paused_at = 0;
volatile uint32_t g_release = 0;
constexpr uint32_t PAUSE_LIMIT_MS = 30000;

void pause_hook(uint32_t step)
{
    if (__atomic_load_n(&g_pause_step, __ATOMIC_ACQUIRE) != step) {
        return;
    }
    __atomic_store_n(&g_paused_at, step, __ATOMIC_RELEASE);
    for (uint32_t waited = 0; waited < PAUSE_LIMIT_MS && !__atomic_load_n(&g_release, __ATOMIC_ACQUIRE); waited += 2) {
        Sleep(2);
    }
    __atomic_store_n(&g_paused_at, 0u, __ATOMIC_RELEASE);
}

}  // namespace

extern "C" {

struct ilct_boot_marker {
    uint32_t kind;
    uint32_t bit;
    uint32_t offset;
    uint32_t reserved;
    char text[128];
};

void ILCT_BootSetHost(uint64_t image_base, uint32_t pid, const wchar_t* x64_dir, const wchar_t* guard_name)
{
    host h;
    memset(&h, 0, sizeof(h));
    h.image_base = static_cast<uintptr_t>(image_base);
    h.pid = pid;
    if (x64_dir != nullptr) {
        wcsncpy(h.x64_dir, x64_dir, sizeof(h.x64_dir) / sizeof(wchar_t) - 1);
    }
    if (guard_name != nullptr) {
        wcsncpy(h.guard_name, guard_name, sizeof(h.guard_name) / sizeof(wchar_t) - 1);
    }
    test_set_host(&h);
}

void ILCT_BootStatus(status* out)
{
    *out = current();
}

uint32_t ILCT_BootMarkerCount(void)
{
    return marker_count();
}

int32_t ILCT_BootMarker(uint32_t index, ilct_boot_marker* out)
{
    const marker* m = marker_at(index);
    if (m == nullptr) {
        return 0;
    }
    memset(out, 0, sizeof(*out));
    out->kind = m->kind;
    out->bit = m->bit;
    out->offset = m->offset;
    strncpy(out->text, m->text, sizeof(out->text) - 1);
    return 1;
}

uint32_t ILCT_BootDetect(const wchar_t* x64_dir, uint32_t pid)
{
    return detect_together(x64_dir, pid);
}

void ILCT_BootParseSwitch(const char* text, uint64_t size, int32_t default_on, settings_switch* out)
{
    *out = parse_module_switch(text, static_cast<size_t>(size), default_on != 0);
}

void ILCT_BootReadSwitch(const wchar_t* x64_dir, int32_t default_on, settings_switch* out)
{
    *out = read_module_switch(x64_dir, default_on != 0);
}

uint32_t ILCT_BootEvents(uint32_t* out, uint32_t max)
{
    return test_events(out, max);
}

uint32_t ILCT_BootInitThreadId(void)
{
    return test_init_thread_id();
}

uint32_t ILCT_BootInputStartThreadId(void)
{
    return test_input_start_thread_id();
}

int32_t ILCT_BootWaitSettled(uint32_t timeout_ms)
{
    return test_wait_settled(timeout_ms) ? 1 : 0;
}

// Pauses the init thread when it reaches `step` (ilc_boot_step, or 0x80 = just before arming) until
// ILCT_BootResume. 0 = no pause.
void ILCT_BootPauseAt(uint32_t step)
{
    __atomic_store_n(&g_release, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pause_step, step, __ATOMIC_RELEASE);
    test_set_step_hook(step != 0 ? pause_hook : nullptr);
}

uint32_t ILCT_BootPausedAt(void)
{
    return __atomic_load_n(&g_paused_at, __ATOMIC_ACQUIRE);
}

void ILCT_BootResume(void)
{
    __atomic_store_n(&g_release, 1u, __ATOMIC_RELEASE);
}

}  // extern "C"
