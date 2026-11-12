// Ivalice Legacy co-op module: live device backends. See input_live.h.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "SDL.h"
#include "input/input_live.h"
#include "input/input_service.h"

namespace ilc::input {
namespace {

// XINPUT_STATE as XInputGetState writes it: DWORD packet number, then XINPUT_GAMEPAD.
struct xinput_state_raw {
    DWORD packet;
    ilc_xinput_gamepad pad;
};
static_assert(sizeof(xinput_state_raw) == 16, "XINPUT_STATE layout");

using xinput_get_state_fn = DWORD(WINAPI*)(DWORD, xinput_state_raw*);

HMODULE g_xinput_module = nullptr;
HMODULE g_hid_module = nullptr;
HMODULE g_cfgmgr_module = nullptr;
xinput_get_state_fn g_xinput_get_state = nullptr;
bool g_sdl_up = false;

class live_xinput final : public xinput_api {
public:
    uint32_t get_state(uint32_t slot, xinput_reading* out) override
    {
        xinput_state_raw state;
        memset(&state, 0, sizeof(state));
        const DWORD rc = g_xinput_get_state(slot, &state);
        out->packet = state.packet;
        out->pad = state.pad;
        return rc;
    }
};

constexpr uint32_t HANDLES_MAX = 32;

class live_sdl final : public sdl_api {
public:
    bool pump() override
    {
        SDL_GameControllerUpdate();  // updates every opened pad and runs device detection
        return true;
    }

    int32_t count() override { return SDL_NumJoysticks(); }

    bool device_info(int32_t index, sdl_device_info* out) override
    {
        const SDL_JoystickID id = SDL_JoystickGetDeviceInstanceID(index);
        if (id < 0) {
            return false;
        }
        out->instance_id = id;
        const SDL_JoystickGUID guid = SDL_JoystickGetDeviceGUID(index);
        memcpy(out->guid, guid.data, sizeof(out->guid));
        out->is_controller = SDL_IsGameController(index) ? 1 : 0;
        copy_c_string(out->path, SDL_PATH_MAX, SDL_JoystickPathForIndex(index));
        const char* name = SDL_GameControllerNameForIndex(index);
        copy_c_string(out->name, SDL_NAME_MAX, name != nullptr ? name : SDL_JoystickNameForIndex(index));
        return true;
    }

    uint64_t open(int32_t index) override
    {
        uint32_t free_slot = HANDLES_MAX;
        for (uint32_t i = 0; i < HANDLES_MAX; ++i) {
            if (handles_[i] == nullptr) {
                free_slot = i;
                break;
            }
        }
        if (free_slot == HANDLES_MAX) {
            return 0;
        }
        SDL_GameController* controller = SDL_GameControllerOpen(index);
        if (controller == nullptr) {
            return 0;
        }
        handles_[free_slot] = controller;
        ++serial_;
        if (serial_ == 0) {
            serial_ = 1;
        }
        ids_[free_slot] = (static_cast<uint64_t>(serial_) << 8) | free_slot;
        return ids_[free_slot];
    }

    bool handle_info(uint64_t handle, sdl_device_info* out) override
    {
        SDL_GameController* controller = lookup(handle);
        if (controller == nullptr) {
            return false;
        }
        SDL_Joystick* joystick = SDL_GameControllerGetJoystick(controller);
        if (joystick == nullptr) {
            return false;
        }
        out->instance_id = SDL_JoystickInstanceID(joystick);
        if (out->instance_id < 0) {
            return false;
        }
        const SDL_JoystickGUID guid = SDL_JoystickGetGUID(joystick);
        memcpy(out->guid, guid.data, sizeof(out->guid));
        out->is_controller = 1;
        copy_c_string(out->path, SDL_PATH_MAX, SDL_GameControllerPath(controller));
        copy_c_string(out->name, SDL_NAME_MAX, SDL_GameControllerName(controller));
        return true;
    }

    bool attached(uint64_t handle) override
    {
        SDL_GameController* controller = lookup(handle);
        return controller != nullptr && SDL_GameControllerGetAttached(controller) == SDL_TRUE;
    }

    bool read(uint64_t handle, sdl_reading* out) override
    {
        SDL_GameController* controller = lookup(handle);
        if (controller == nullptr) {
            return false;
        }
        out->buttons = 0;
        for (uint32_t b = 0; b < SDLB_COUNT_USED; ++b) {
            if (SDL_GameControllerGetButton(controller, static_cast<SDL_GameControllerButton>(b))) {
                out->buttons |= 1u << b;
            }
        }
        for (uint32_t a = 0; a < SDLA_COUNT; ++a) {
            out->axes[a] = SDL_GameControllerGetAxis(controller, static_cast<SDL_GameControllerAxis>(a));
        }
        return true;
    }

    void close(uint64_t handle) override
    {
        const uint32_t slot = static_cast<uint32_t>(handle & 0xFF);
        if (slot < HANDLES_MAX && handles_[slot] != nullptr && ids_[slot] == handle) {
            SDL_GameControllerClose(handles_[slot]);
            handles_[slot] = nullptr;
            ids_[slot] = 0;
        }
    }

    void close_all()
    {
        for (uint32_t i = 0; i < HANDLES_MAX; ++i) {
            if (handles_[i] != nullptr) {
                SDL_GameControllerClose(handles_[i]);
                handles_[i] = nullptr;
                ids_[i] = 0;
            }
        }
    }

private:
    static void copy_c_string(char* out, uint32_t cap, const char* text)
    {
        memset(out, 0, cap);
        if (text == nullptr) {
            return;
        }
        uint32_t n = 0;
        while (n + 1 < cap && text[n] != '\0') {
            out[n] = text[n];
            ++n;
        }
    }

    SDL_GameController* lookup(uint64_t handle) const
    {
        const uint32_t slot = static_cast<uint32_t>(handle & 0xFF);
        if (handle == 0 || slot >= HANDLES_MAX || ids_[slot] != handle) {
            return nullptr;
        }
        return handles_[slot];
    }

    SDL_GameController* handles_[HANDLES_MAX] = {};
    uint64_t ids_[HANDLES_MAX] = {};
    uint32_t serial_ = 0;
};

constinit live_xinput g_live_xinput;
constinit live_sdl g_live_sdl;

HMODULE load_system_module(const wchar_t* name, bool full_path)
{
    if (!full_path) {
        return LoadLibraryExW(name, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    }
    wchar_t path[MAX_PATH];
    const UINT n = GetSystemDirectoryW(path, MAX_PATH);
    size_t name_len = 0;
    while (name[name_len] != L'\0') {
        ++name_len;
    }
    if (n == 0 || n + 1 + name_len + 1 > MAX_PATH) {
        return nullptr;
    }
    path[n] = L'\\';
    memcpy(path + n + 1, name, (name_len + 1) * sizeof(wchar_t));
    return LoadLibraryExW(path, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
}

// Every hint at override priority, so an SDL_* environment variable cannot change it.
void set_hints()
{
    struct hint {
        const char* name;
        const char* value;
    };
    static const hint hints[] = {
        {SDL_HINT_JOYSTICK_THREAD, "0"},                    // detection runs on our thread, in our update
        {SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1"},   // pads keep working when the game is not focused
        {SDL_HINT_XINPUT_ENABLED, "1"},
        {SDL_HINT_DIRECTINPUT_ENABLED, "1"},
        {SDL_HINT_JOYSTICK_HIDAPI, "1"},
        {SDL_HINT_JOYSTICK_HIDAPI_XBOX, "0"},               // Xbox-class pads come through XInput
        {SDL_HINT_JOYSTICK_RAWINPUT, "0"},                  // compiled out; stated anyway
        {SDL_HINT_JOYSTICK_WGI, "0"},                       // compiled out; stated anyway
        {SDL_HINT_JOYSTICK_HIDAPI_PS4_RUMBLE, "0"},         // no enhanced-report switch, no output reports
        {SDL_HINT_JOYSTICK_HIDAPI_PS5_RUMBLE, "0"},
        {SDL_HINT_JOYSTICK_HIDAPI_PS5_PLAYER_LED, "0"},
        {SDL_HINT_JOYSTICK_HIDAPI_SWITCH_PLAYER_LED, "0"},
        {SDL_HINT_JOYSTICK_HIDAPI_SWITCH_HOME_LED, "0"},
        {SDL_HINT_TIMER_RESOLUTION, "0"},                   // never change the system timer resolution
        {SDL_HINT_GAMECONTROLLERCONFIG, ""},                // no mappings from the environment
        {SDL_HINT_GAMECONTROLLERCONFIG_FILE, ""},           // and no mapping file named by it
        {SDL_HINT_NO_SIGNAL_HANDLERS, "1"},
    };
    for (const hint& h : hints) {
        SDL_SetHintWithPriority(h.name, h.value, SDL_HINT_OVERRIDE);
    }
}

}  // namespace

live_backends live_open(uint32_t* issues)
{
    live_backends backends = {nullptr, nullptr};
    g_xinput_module = load_system_module(L"xinput1_4.dll", true);
    if (g_xinput_module != nullptr) {
        g_xinput_get_state =
            reinterpret_cast<xinput_get_state_fn>(reinterpret_cast<void*>(GetProcAddress(g_xinput_module,
                                                                                         "XInputGetState")));
    }
    if (g_xinput_get_state != nullptr) {
        backends.xinput = &g_live_xinput;
    } else {
        *issues |= ISSUE_XINPUT_LOAD;
    }
    g_hid_module = load_system_module(L"hid.dll", false);
    g_cfgmgr_module = load_system_module(L"cfgmgr32.dll", false);
    set_hints();
    if (SDL_Init(SDL_INIT_GAMECONTROLLER) == 0) {
        g_sdl_up = true;
        SDL_GameControllerEventState(SDL_IGNORE);  // we read state directly; nothing piles up in a queue
        SDL_JoystickEventState(SDL_IGNORE);
        backends.sdl = &g_live_sdl;
    } else {
        *issues |= ISSUE_SDL_INIT;
    }
    return backends;
}

void live_close()
{
    if (g_sdl_up) {
        g_live_sdl.close_all();
        SDL_Quit();
        g_sdl_up = false;
    }
    g_xinput_get_state = nullptr;
    HMODULE* modules[3] = {&g_xinput_module, &g_hid_module, &g_cfgmgr_module};
    for (HMODULE* module : modules) {
        if (*module != nullptr) {
            FreeLibrary(*module);
            *module = nullptr;
        }
    }
}

}  // namespace ilc::input
