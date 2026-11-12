// Ivalice Legacy co-op module: the exports the FF12 External File Loader looks up in a module in x64/modules.
// Every one is optional for the loader; it calls them in this order:
//
//   ilc_efl_version FF12HgetVer(void);   1st, right after LoadLibrary, main thread
//   const char* FF12HgetName(void);      2nd, main thread
//   void FF12Hinit(void);                3rd, main thread before WinMain, loader lock released
//   void FF12HinitDone(void);            later, on the loader's watcher thread, once the game runs
//   void FF12Hcleanup(void);             at exit, from the loader's ExitProcess hook; not on a crash
//
// ilc_efl_version is 12 bytes, so the Windows x64 ABI returns it through a hidden pointer: the caller
// passes the destination in RCX and the callee returns that same pointer in RAX. The loader reads the
// 12 bytes at RAX, so returning RCX is required.
//
// DllMain does nothing; module start-up belongs in FF12Hinit (src/boot/boot.h: host probe, instance guard,
// Ivalice Together check, the Co-op module switch, then the init thread). FF12HinitDone marks readiness and
// FF12Hcleanup disarms and stops the module's threads; neither ever runs under the loader lock.
#pragma once

#include <stdint.h>

struct ilc_efl_version {
    uint32_t major;
    uint32_t minor;
    uint32_t patch;
};

static_assert(sizeof(struct ilc_efl_version) == 12, "ilc_efl_version layout");

extern "C" {
struct ilc_efl_version FF12HgetVer(void);
const char* FF12HgetName(void);
void FF12Hinit(void);
void FF12HinitDone(void);
void FF12Hcleanup(void);
}
