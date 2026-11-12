// Ivalice Legacy co-op module: exports for the External File Loader.
#include "boot/boot.h"
#include "ilc/efl.h"
#include "ilc/version.h"

static const char module_name[] = ILC_MODULE_NAME;

extern "C" struct ilc_efl_version FF12HgetVer(void)
{
    struct ilc_efl_version version;
    version.major = ILC_VERSION_MAJOR;
    version.minor = ILC_VERSION_MINOR;
    version.patch = ILC_VERSION_PATCH;
    return version;
}

extern "C" const char* FF12HgetName(void)
{
    return module_name;
}

// Start-up, readiness and shutdown live in src/boot/boot.cpp; DllMain never does any of it.
extern "C" void FF12Hinit(void)
{
    ilc::boot::on_init();
}

extern "C" void FF12HinitDone(void)
{
    ilc::boot::on_init_done();
}

extern "C" void FF12Hcleanup(void)
{
    ilc::boot::on_cleanup();
}
