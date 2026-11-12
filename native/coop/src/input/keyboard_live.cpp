// Ivalice Legacy co-op module: the live keyboard and mouse reader (see keyboard.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "input/keyboard.h"

namespace ilc::input {
namespace {

class live_keys final : public keyboard_api {
public:
    bool focused() override
    {
        const HWND foreground = GetForegroundWindow();
        if (foreground == nullptr) {
            return false;
        }
        DWORD pid = 0;
        GetWindowThreadProcessId(foreground, &pid);
        return pid == GetCurrentProcessId();
    }

    bool down(uint8_t vk) override
    {
        return vk != 0 && (GetAsyncKeyState(vk) & 0x8000) != 0;
    }
};

constinit live_keys g_live_keys;

}  // namespace

keyboard_api* live_keyboard()
{
    return &g_live_keys;
}

}  // namespace ilc::input
