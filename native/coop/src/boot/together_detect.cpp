// Ivalice Legacy co-op module: detection of an installed Ivalice Together (see together_detect.h).
//
// The names below are the other mod's own product markers, taken from its removal inventory (the files it
// installed) and from the coexistence rules of the co-op design. They appear only in this file.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "boot/together_detect.h"
#include "runtime/probe.h"

namespace ilc::boot {
namespace {

constexpr marker MARKERS[] = {
    {MARKER_FILE, TOGETHER_FILE_MODULE, 0, "modules\\ivalice-together-autostart.dll"},
    {MARKER_FILE, TOGETHER_FILE_RUNTIME, 0, "IvaliceTogetherRuntime\\IvaliceTogetherRuntime.exe"},
    {MARKER_FILE, TOGETHER_FILE_CONFIGURATOR, 0, "IvaliceTogetherConfig.exe"},
    {MARKER_MODULE, TOGETHER_MODULE_LOADED, 0, "ivalice-together-autostart.dll"},
    {MARKER_MUTEX, TOGETHER_MUTEX_MODULE, 0, "Local\\IvaliceTogetherAutostart_"},
    {MARKER_MUTEX, TOGETHER_MUTEX_SUPERVISOR, 0, "Local\\FF12LocalCoopHeadlessLauncher_"},
    {MARKER_REGION, TOGETHER_REGION_PRIMARY, 0, "FF12COOP"},
    {MARKER_REGION, TOGETHER_REGION_INTERACTION, 0, "FF12INT2"},
    {MARKER_REGION, TOGETHER_REGION_DAMAGE, 0, "FF12DMG1"},
    {MARKER_REGION, TOGETHER_INSTALL_MARKER, 0x60, "ITGLBL02"},
    {MARKER_FILE, TOGETHER_RESIDUE_DATA, 0, "coop"},
    {MARKER_FILE, TOGETHER_RESIDUE_MENU, 0, "scripts\\IvaliceTogetherMenu.lua"},
};
constexpr uint32_t MARKER_COUNT = sizeof(MARKERS) / sizeof(MARKERS[0]);

constexpr uintptr_t SCAN_START = 0x10000;     // NOTADDR: lowest allocation address
constexpr uintptr_t SCAN_END = 0x80000000;    // NOTADDR: the low 2 GiB, where rel32-reachable regions live
constexpr uintptr_t GRANULARITY = 0x10000;    // NOTADDR: allocation granularity
constexpr uint32_t PATH_CAP = 1024;

// Appends ASCII `text` to `dst` (wide, NUL-terminated). False if it does not fit.
bool append_ascii(wchar_t* dst, uint32_t cap, const char* text)
{
    uint32_t n = 0;
    while (n < cap && dst[n] != L'\0') {
        ++n;
    }
    for (; *text != '\0'; ++text) {
        if (n + 1 >= cap) {
            return false;
        }
        dst[n++] = static_cast<wchar_t>(static_cast<unsigned char>(*text));
    }
    dst[n] = L'\0';
    return true;
}

bool append_wide(wchar_t* dst, uint32_t cap, const wchar_t* text)
{
    uint32_t n = 0;
    while (n < cap && dst[n] != L'\0') {
        ++n;
    }
    for (; *text != L'\0'; ++text) {
        if (n + 1 >= cap) {
            return false;
        }
        dst[n++] = *text;
    }
    dst[n] = L'\0';
    return true;
}

bool append_decimal(wchar_t* dst, uint32_t cap, uint32_t value)
{
    char digits[12];
    uint32_t count = 0;
    do {
        digits[count++] = static_cast<char>('0' + value % 10);
        value /= 10;
    } while (value != 0);
    char text[12];
    for (uint32_t i = 0; i < count; ++i) {
        text[i] = digits[count - 1 - i];
    }
    text[count] = '\0';
    return append_ascii(dst, cap, text);
}

// present / absent / uncertain (counted as present)
enum probe_result { ABSENT = 0, PRESENT = 1, UNCERTAIN = 2 };

probe_result check_file(const wchar_t* x64_dir, const char* relative)
{
    wchar_t path[PATH_CAP];
    path[0] = L'\0';
    if (!append_wide(path, PATH_CAP, x64_dir) || !append_ascii(path, PATH_CAP, "\\") ||
        !append_ascii(path, PATH_CAP, relative)) {
        return UNCERTAIN;
    }
    if (GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) {
        return PRESENT;
    }
    const DWORD error = GetLastError();
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND ? ABSENT : UNCERTAIN;
}

probe_result check_module(const char* name)
{
    wchar_t wide[PATH_CAP];
    wide[0] = L'\0';
    if (!append_ascii(wide, PATH_CAP, name)) {
        return UNCERTAIN;
    }
    return GetModuleHandleW(wide) != nullptr ? PRESENT : ABSENT;
}

probe_result check_mutex(const char* prefix, uint32_t pid)
{
    wchar_t name[PATH_CAP];
    name[0] = L'\0';
    if (!append_ascii(name, PATH_CAP, prefix) || !append_decimal(name, PATH_CAP, pid)) {
        return UNCERTAIN;
    }
    SetLastError(ERROR_SUCCESS);
    HANDLE mutex = OpenMutexW(SYNCHRONIZE, FALSE, name);
    if (mutex != nullptr) {
        CloseHandle(mutex);
        return PRESENT;
    }
    // Only "no such object" proves absence. Access denied, a different object type under that name, or a
    // transient failure is uncertainty, and uncertainty never authorizes arming.
    return GetLastError() == ERROR_FILE_NOT_FOUND ? ABSENT : UNCERTAIN;
}

bool readable(const MEMORY_BASIC_INFORMATION& info)
{
    if (info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
        return false;
    }
    const DWORD p = info.Protect & 0xFF;
    return p == PAGE_READONLY || p == PAGE_READWRITE || p == PAGE_WRITECOPY || p == PAGE_EXECUTE_READ ||
           p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY;
}

// One pass over the low 2 GiB: every committed, readable allocation base on a 64 KiB boundary is compared with
// each region marker at its offset.
uint32_t scan_regions()
{
    uint32_t found = 0;
    uintptr_t cursor = SCAN_START;
    while (cursor < SCAN_END) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<const void*>(cursor), &info, sizeof(info)) == 0) {
            break;
        }
        const uintptr_t base = reinterpret_cast<uintptr_t>(info.BaseAddress);
        const uintptr_t end = base + info.RegionSize;
        if (base == reinterpret_cast<uintptr_t>(info.AllocationBase) && base % GRANULARITY == 0 && readable(info)) {
            for (uint32_t i = 0; i < MARKER_COUNT; ++i) {
                const marker& m = MARKERS[i];
                if (m.kind != MARKER_REGION || m.offset + 8 > info.RegionSize) {
                    continue;
                }
                char observed[8];
                if (copy_from_caller(observed, reinterpret_cast<const void*>(base + m.offset), sizeof(observed)) &&
                    memcmp(observed, m.text, sizeof(observed)) == 0) {
                    found |= m.bit;
                }
            }
        }
        cursor = end > cursor ? end : cursor + GRANULARITY;
    }
    return found;
}

}  // namespace

uint32_t marker_count()
{
    return MARKER_COUNT;
}

const marker* marker_at(uint32_t index)
{
    return index < MARKER_COUNT ? &MARKERS[index] : nullptr;
}

uint32_t detect_together(const wchar_t* x64_dir, uint32_t pid)
{
    uint32_t found = 0;
    for (uint32_t i = 0; i < MARKER_COUNT; ++i) {
        const marker& m = MARKERS[i];
        probe_result r = ABSENT;
        switch (m.kind) {
        case MARKER_FILE:
            r = x64_dir != nullptr ? check_file(x64_dir, m.text) : UNCERTAIN;
            break;
        case MARKER_MODULE:
            r = check_module(m.text);
            break;
        case MARKER_MUTEX:
            r = check_mutex(m.text, pid);
            break;
        default:
            break;
        }
        if (r != ABSENT) {
            found |= m.bit;
        }
        // A residue check that cannot decide stays residue: it never blocks on its own.
        if (r == UNCERTAIN && (m.bit & TOGETHER_BLOCKING) != 0) {
            found |= TOGETHER_UNCERTAIN;
        }
    }
    return found | scan_regions();
}

}  // namespace ilc::boot
