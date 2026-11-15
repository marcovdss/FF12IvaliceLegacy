// Ivalice Legacy co-op module: the loopback codec hooks at the seam (host-test builds only).
//
// The session core consumes and produces three message families: seat frames (input and action frames, and the
// input service's control view), seat commands and their results, and the views the HUD draws from. A future
// network transport would carry exactly these. To prove that nothing else crosses, the host-test build can run
// every record of those families through an encoder and a decoder at the point where it is handed over, each
// tick, and the behaviour must stay the same with the codec on and off.
//
// The shipped DLL is built without ILC_LOOPBACK_CODEC: every hook below expands to nothing (or to a plain alias of
// the original object), so the release image and its imports do not change. The test DLL defines ILC_LOOPBACK_CODEC
// and links testsupport/loopback.cpp, which holds the codec and its on/off switch (off by default).
//
// The codec is lossless and independent of padding: it writes only a type's value bytes (its padding is found
// once with __builtin_clear_padding) into a separate wire buffer, and the decoder rebuilds the object with every
// padding byte poisoned. A consumer that read padding, a pointer into the producer's memory or anything beside the
// record would behave differently with the codec on.
#pragma once

#include <stdint.h>

#if defined(ILC_LOOPBACK_CODEC)

#include <string.h>

namespace ilc::loopback {

enum family : uint32_t {
    FAM_FRAME = 0,         // ilc_seat_input_frame (input service tick)
    FAM_ACTION = 1,        // ilc_seat_action_frame (input service tick)
    FAM_CONTROL_VIEW = 2,  // input::control_view (input service tick)
    FAM_COMMAND = 3,       // input::command_msg (post_command)
    FAM_EVENT = 4,         // input::control_event (drain_events)
    FAM_SNAPSHOT = 5,      // hud::hud_snapshot
    FAM_MENU_VIEW = 6,     // hud::menus_draw::menu_view
    FAM_ROWS = 7,          // hud::party_rows::rows_record
    FAM_TI_VIEW = 8,       // hud::target_info::view
    FAM_LOG_VIEW = 9,      // hud::battle_log::view
    FAM_COUNT
};

bool on();
// Encodes the value bytes of object (value_mask[i] != 0) into a wire buffer and decodes it back into object, with
// every padding byte poisoned. Counts per family.
void roundtrip_bytes(uint32_t fam, void* object, const uint8_t* value_mask, uint32_t size);
// The value mask of a type: built once from a probe object (all ones) with its padding cleared.
void build_mask(uint8_t* mask, uint8_t* probe, uint32_t size);

template <typename T>
const uint8_t* value_mask()
{
    static uint8_t mask[sizeof(T)];
    static volatile uint32_t built = 0;
    if (__atomic_load_n(&built, __ATOMIC_ACQUIRE) == 0) {
        alignas(T) static uint8_t probe[sizeof(T)];  // every builder writes the same bytes: a race is harmless
        memset(probe, 0xFF, sizeof(T));
        __builtin_clear_padding(reinterpret_cast<T*>(probe));
        build_mask(mask, probe, sizeof(T));
        __atomic_store_n(&built, 1u, __ATOMIC_RELEASE);
    }
    return mask;
}

template <typename T>
inline void roundtrip(uint32_t fam, T& object)
{
    if (on()) {
        roundtrip_bytes(fam, &object, value_mask<T>(), sizeof(T));
    }
}

}  // namespace ilc::loopback

// The object itself, in place (an output the producer does not read back).
#define ILC_LOOPBACK(fam, object) ::ilc::loopback::roundtrip(::ilc::loopback::fam, object)
// count objects of an array, in place.
#define ILC_LOOPBACK_EACH(fam, array, count)                         \
    do {                                                             \
        for (uint32_t ilc_lb_i = 0; ilc_lb_i < (count); ++ilc_lb_i) { \
            ILC_LOOPBACK(fam, (array)[ilc_lb_i]);                    \
        }                                                            \
    } while (0)
// A decoded copy named `name` of a const input (stack: small records, any thread).
#define ILC_LOOPBACK_COPY(T, fam, in, name) \
    T name##_loopback = (in);               \
    ILC_LOOPBACK(fam, name##_loopback);     \
    const T& name = name##_loopback
// The same in a static buffer (large records with one writer thread).
#define ILC_LOOPBACK_COPY_STATIC(T, fam, in, name)                       \
    static T name##_loopback;                                            \
    memcpy(static_cast<void*>(&name##_loopback), &(in), sizeof(T));      \
    ILC_LOOPBACK(fam, name##_loopback);                                  \
    const T& name = name##_loopback
// A decoded copy of an array of `count` records (static buffer, one writer thread), as a pointer named `name`.
#define ILC_LOOPBACK_ARRAY(T, fam, array, count, name)                         \
    static T name##_loopback[count];                                           \
    memcpy(static_cast<void*>(name##_loopback), (array), sizeof(T) * (count)); \
    ILC_LOOPBACK_EACH(fam, name##_loopback, count);                            \
    const T* const name = name##_loopback

#else  // the shipped DLL: no codec

#define ILC_LOOPBACK(fam, object) ((void)0)
#define ILC_LOOPBACK_EACH(fam, array, count) ((void)0)
#define ILC_LOOPBACK_COPY(T, fam, in, name) const T& name = (in)
#define ILC_LOOPBACK_COPY_STATIC(T, fam, in, name) const T& name = (in)
#define ILC_LOOPBACK_ARRAY(T, fam, array, count, name) const T* const name = (array)

#endif
