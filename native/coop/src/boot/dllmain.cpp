// Ivalice Legacy co-op module: DllMain.
//
// The mingw-w64 DLL start-up (DllMainCRTStartup) is the image entry point; it initializes the C runtime and
// runs static constructors, then calls this function. DllMain itself does nothing, ever: all module start-up
// belongs in FF12Hinit, and nothing starts from DLL_PROCESS_ATTACH. At exit the module never unpatches.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance;
    (void)reason;
    (void)reserved;
    return TRUE;
}
