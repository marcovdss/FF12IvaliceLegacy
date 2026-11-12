/* Ivalice Legacy co-op module: SDL 2.32.10 build configuration.
 *
 * This file is ours, not SDL's. build_sdl2.py stages SDL's public headers into the build folder and puts this
 * file in place of include/SDL_config.h, the way SDL's own CMake build generates its SDL_config.h. It takes SDL's
 * stock Windows configuration and then switches off everything the co-op input service does not use:
 *
 *   kept:  events, joystick + game controller (DirectInput, XInput, HIDAPI drivers), HIDAPI, threads, timers,
 *          shared-object loading, RWops
 *   off:   audio, video, render, haptic, sensor, power, filesystem, locale, misc, the RawInput, Windows.Gaming.Input
 *          and virtual joystick drivers, OpenGL/Vulkan/D3D
 *
 * SDL_DYNAMIC_API is switched off without patching SDL: build_sdl2.py defines DYNAPI_NEEDS_DLOPEN and this
 * configuration never defines HAVE_DLOPEN, which selects the `#define SDL_DYNAMIC_API 0` branch already present in
 * SDL's src/dynapi/SDL_dynapi.h. build_sdl2.py verifies the result (a probe compile and a string scan).
 */
#ifndef ILC_SDL_CONFIG_H
#define ILC_SDL_CONFIG_H

#ifndef HAVE_LIBC
#define HAVE_LIBC 1 /* mingw-w64 on the Universal CRT */
#endif

#include "SDL_config_windows.h"

#ifdef HAVE_DLOPEN
#error "HAVE_DLOPEN must stay undefined: it would re-enable SDL_DYNAMIC_API"
#endif

/* SDL assertions compiled out. SDL's release default (level 1) keeps SDL_assert_release, whose default handler
 * can prompt on stdin and end the process (TerminateProcess); inside the game a failed SDL assertion must never
 * do either. */
#define SDL_ASSERT_LEVEL 0

/* Subsystems we never initialize. */
#define SDL_AUDIO_DISABLED 1
#define SDL_VIDEO_DISABLED 1
#define SDL_RENDER_DISABLED 1
#define SDL_HAPTIC_DISABLED 1
#define SDL_SENSOR_DISABLED 1
#define SDL_POWER_DISABLED 1
#define SDL_FILESYSTEM_DISABLED 1
#define SDL_LOCALE_DISABLED 1
#define SDL_MISC_DISABLED 1

#undef SDL_AUDIO_DRIVER_WASAPI
#undef SDL_AUDIO_DRIVER_DSOUND
#undef SDL_AUDIO_DRIVER_WINMM
#undef SDL_AUDIO_DRIVER_DISK
#define SDL_AUDIO_DRIVER_DUMMY 1

#undef SDL_VIDEO_DRIVER_WINDOWS
#undef SDL_VIDEO_RENDER_D3D
#undef SDL_VIDEO_RENDER_D3D11
#undef SDL_VIDEO_RENDER_D3D12
#undef SDL_VIDEO_OPENGL
#undef SDL_VIDEO_OPENGL_WGL
#undef SDL_VIDEO_RENDER_OGL
#undef SDL_VIDEO_RENDER_OGL_ES2
#undef SDL_VIDEO_OPENGL_ES2
#undef SDL_VIDEO_OPENGL_EGL
#undef SDL_VIDEO_VULKAN
#define SDL_VIDEO_DRIVER_DUMMY 1

#undef SDL_HAPTIC_DINPUT
#undef SDL_HAPTIC_XINPUT
#define SDL_HAPTIC_DUMMY 1

#undef SDL_SENSOR_WINDOWS
#define SDL_SENSOR_DUMMY 1

#undef SDL_POWER_WINDOWS
#undef SDL_FILESYSTEM_WINDOWS
#define SDL_FILESYSTEM_DUMMY 1

/* Joystick drivers: DirectInput (generic HID pads), XInput (Xbox-class pads, so they get names and can be
 * matched to the module's own XInput slots), HIDAPI (DualSense, DualShock, Switch Pro and the like).
 * RawInput registers a process-wide raw input sink and Windows.Gaming.Input needs WinRT: both stay out. */
#undef SDL_JOYSTICK_RAWINPUT
#undef SDL_JOYSTICK_WGI
#undef SDL_JOYSTICK_VIRTUAL
#undef HAVE_WINDOWS_GAMING_INPUT_H
#undef HAVE_ROAPI_H
#undef HAVE_D3D11_H
#undef HAVE_D3D12_H
#undef HAVE_SENSORSAPI_H
#undef HAVE_MMDEVICEAPI_H
#undef HAVE_AUDIOCLIENT_H

#endif /* ILC_SDL_CONFIG_H */
