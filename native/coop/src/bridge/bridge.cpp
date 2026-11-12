// Ivalice Legacy co-op module: the bridge entry point (ABI version 1).
//
// Threads: ILC_Bridge runs on whatever Lua script thread calls it, possibly several at once. All shared state
// below is guarded by one SRW lock, held only while records are copied and checked (microseconds; nothing
// inside it waits on anything else). The safe point only ever *tries* the lock, so the game-thread tick that
// will call it can never be blocked by a script.
//
// Settings flow: SET_SETTINGS validates a copy and puts it in the staging slot; the safe point moves the
// staging slot into the applied slot. Generation rules: a repeat (same generation, same bytes) changes
// nothing, an older generation is refused, and the applied settings stay in force whatever Lua does next.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "boot/boot.h"
#include "bridge/bridge_internal.h"
#include "bridge/validate.h"
#include "ilc/bridge_abi.h"
#include "ilc/bridge_defaults.h"
#include "ilc/version.h"
#include "control/control.h"
#include "input/input_service.h"
#include "runtime/probe.h"
#include "session/session.h"

#ifndef ILC_SCHEMA_ID0
#define ILC_SCHEMA_ID0 0ull
#endif
#ifndef ILC_SCHEMA_ID1
#define ILC_SCHEMA_ID1 0ull
#endif

namespace ilc {
namespace {

constexpr uint32_t OPS_MASK = (1u << ILC_OP_HELLO) | (1u << ILC_OP_SET_SETTINGS) | (1u << ILC_OP_COMMAND) |
                              (1u << ILC_OP_GET_STATUS) | (1u << ILC_OP_GET_SEATS) | (1u << ILC_OP_GET_DEVICES) |
                              (1u << ILC_OP_POP_EVENTS) | (1u << ILC_OP_GET_SETTINGS);

struct bridge_state {
    SRWLOCK lock;
    ilc_settings scratch;  // inbound copy, validated here, never the caller's memory
    ilc_settings staged;
    ilc_settings applied;
    uint32_t staged_pending;
    uint32_t staged_digest;
    uint32_t applied_digest;
    ilc_event events[ILC_EVENT_QUEUE];
    uint32_t event_head;  // oldest
    uint32_t event_count;
    uint32_t event_next_seq;
    uint32_t events_dropped;
    uint32_t safe_points;
    uint32_t applies;
    uint32_t rejects;
    uint32_t last_reject_op;
    int32_t last_reject_code;
    uint32_t last_reject_field;
    uint32_t last_reject_location;
    uint32_t tick_attached;
};

// Constant-initialized: the defaults are data in the image, no start-up code builds them.
constinit bridge_state g = {
    SRWLOCK_INIT, ilc_settings{}, ilc_settings{}, default_settings(), 0, 0, 0, {}, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

// The applied digest of the defaults is computed on first use (constexpr FNV over 3 KB would slow the build).
uint32_t g_defaults_digest_ready = 0;

class locked {
public:
    locked() { AcquireSRWLockExclusive(&g.lock); }
    ~locked() { ReleaseSRWLockExclusive(&g.lock); }
    locked(const locked&) = delete;
    locked& operator=(const locked&) = delete;
};

// ---- helpers that need the lock held --------------------------------------------------------------------

void ensure_defaults_digest()
{
    if (!g_defaults_digest_ready) {
        g.applied_digest = settings_digest(g.applied);
        g_defaults_digest_ready = 1;
    }
}

void push_event(uint16_t kind, uint8_t seat, uint32_t generation, int32_t code, uint32_t field,
                uint32_t location, uint32_t a, uint32_t b)
{
    if (g.event_count == ILC_EVENT_QUEUE) {  // drop the oldest, count it
        g.event_head = (g.event_head + 1) % ILC_EVENT_QUEUE;
        --g.event_count;
        ++g.events_dropped;
    }
    ilc_event& e = g.events[(g.event_head + g.event_count) % ILC_EVENT_QUEUE];
    memset(&e, 0, sizeof(e));
    e.seq = g.event_next_seq++;
    if (g.event_next_seq == 0) {
        g.event_next_seq = 1;
    }
    e.kind = kind;
    e.seat = seat;
    e.generation = generation;
    e.code = code;
    e.field = field;
    e.location = location;
    e.a = a;
    e.b = b;
    ++g.event_count;
}

// Moves the input service's outcomes (pairing, capture, seat commands) into the event queue, oldest first.
// Lock order: the bridge lock, then the service's queue lock; the service thread never takes the bridge lock.
void pull_input_events()
{
    input::control_event batch[ILC_EVENT_QUEUE];
    const uint32_t n = input::drain_events(batch, ILC_EVENT_QUEUE);
    for (uint32_t i = 0; i < n; ++i) {
        const input::control_event& e = batch[i];
        push_event(e.kind, e.seat, e.generation, e.code, 0, 0, e.a, e.b);
    }
}

uint32_t pack_location(const reject& r)
{
    return ((r.outer & 0xFFu) << 8) | (r.inner & 0xFFu);
}

// The seat (1..4) a rejected settings field belongs to, or 0 for header-, settings-, economy-, alias- and
// kill-switch-level fields. Identities and bindings are seat fields only when their location is a seat.
uint32_t seat_of_reject(const reject& r)
{
    const uint32_t sid = r.field >> 8;
    const bool seat_scoped = sid == ilc_seat_settings_struct_id || sid == ilc_seat_device_struct_id ||
                             sid == ilc_device_identity_struct_id || sid == ilc_binding_struct_id ||
                             (r.code == ILC_E_BAD_FIELD && r.field == kbm_seat_field());
    return seat_scoped && r.outer < ILC_SEATS ? r.outer + 1 : 0;
}

void note_reject(uint32_t op, const reject& r)
{
    ++g.rejects;
    g.last_reject_op = op;
    g.last_reject_code = r.code;
    g.last_reject_field = r.field;
    g.last_reject_location = pack_location(r);
}

const ilc_settings& accepted()
{
    return g.staged_pending ? g.staged : g.applied;
}

void fill_header(ilc_header& h, uint16_t type, uint32_t size, uint32_t generation)
{
    h.magic = ILC_ABI_MAGIC;
    h.abi_version = ILC_ABI_VERSION;
    h.record_type = type;
    h.size = size;
    h.generation = generation;
}

// Writes a finished reply. outCap was checked before any state changed; the probe catches a bad out buffer.
int32_t deliver(void* out, const void* reply, uint32_t size)
{
    return copy_to_caller(out, reply, size) ? static_cast<int32_t>(size) : ILC_E_FAULT;
}

int32_t record_failure(uint32_t op, int32_t code)
{
    if (code < 0 && code != ILC_E_NOT_READY) {
        reject r = {code, 0, 0, 0};
        locked hold;
        note_reject(op, r);
    }
    return code;
}

// ---- ops ------------------------------------------------------------------------------------------------

int32_t op_hello(const void* in, uint32_t in_len, void* out, uint32_t out_cap)
{
    if (in_len != 0 && in_len != sizeof(ilc_header)) {
        return ILC_E_BAD_LENGTH;
    }
    if (out_cap < sizeof(ilc_hello_reply)) {
        return ILC_E_OUT_TOO_SMALL;
    }
    if (in_len != 0) {
        ilc_header request;
        if (!copy_from_caller(&request, in, sizeof(request))) {
            return ILC_E_FAULT;
        }
        // abi_version and record_type are not checked: HELLO is how a caller learns which ABI this module
        // speaks, and the reply always carries the module's own version.
        if (request.magic != ILC_ABI_MAGIC) {
            return ILC_E_BAD_HEADER;
        }
        if (request.size != in_len) {
            return ILC_E_BAD_LENGTH;
        }
    }
    ilc_hello_reply reply;
    memset(&reply, 0, sizeof(reply));
    fill_header(reply.header, ILC_REC_HELLO_REPLY, sizeof(reply), 0);
    reply.module_major = ILC_VERSION_MAJOR;
    reply.module_minor = ILC_VERSION_MINOR;
    reply.module_patch = ILC_VERSION_PATCH;
    const boot::status boot_status = boot::current();
    reply.state = boot_status.state;
    reply.reason = boot_status.reason;
    reply.build_flags = 0;
    reply.caller_thread_id = GetCurrentThreadId();
    reply.settings_size = sizeof(ilc_settings);
    reply.ops_mask = OPS_MASK;
    reply.source_id[0] = ILC_SOURCE_ID0;
    reply.source_id[1] = ILC_SOURCE_ID1;
    reply.schema_id[0] = ILC_SCHEMA_ID0;
    reply.schema_id[1] = ILC_SCHEMA_ID1;
    {
        locked hold;
        reply.accepted_generation = accepted().header.generation;
        reply.applied_generation = g.applied.header.generation;
    }
    return deliver(out, &reply, sizeof(reply));
}

int32_t op_set_settings(const void* in, uint32_t in_len, void* out, uint32_t out_cap)
{
    constexpr uint32_t op = ILC_OP_SET_SETTINGS;
    if (in_len != sizeof(ilc_settings)) {
        reject r = {ILC_E_BAD_LENGTH, 0, 0, 0};
        locked hold;
        note_reject(op, r);
        push_event(ILC_EV_SETTINGS_REJECTED, 0, 0, r.code, 0, 0, 0, 0);
        return r.code;
    }
    if (out_cap < sizeof(ilc_settings_ack)) {
        return record_failure(op, ILC_E_OUT_TOO_SMALL);  // checked before anything is consumed
    }

    ilc_settings_ack ack;
    memset(&ack, 0, sizeof(ack));
    {
        locked hold;
        ensure_defaults_digest();
        reject r = {0, 0, 0, 0};
        ilc_settings& s = g.scratch;
        if (!copy_from_caller(&s, in, sizeof(s))) {
            r.code = ILC_E_FAULT;
        } else if (!validate_header(s.header, in_len, ILC_REC_SETTINGS, &r)) {
            // r filled in
        } else if (s.header.generation == 0) {
            r = {ILC_E_BAD_FIELD, header_generation_field(), 0, 0};
        }
        const uint32_t generation = r.code == 0 ? s.header.generation : 0;
        const ilc_settings& last = accepted();
        const uint32_t last_generation = last.header.generation;
        uint32_t disposition = 0;
        if (r.code == 0) {
            if (generation < last_generation) {
                r = {ILC_E_STALE_GENERATION, header_generation_field(), 0, 0};
            } else if (generation == last_generation) {
                if (memcmp(&s, &last, sizeof(s)) == 0) {
                    disposition = ILC_ACK_DUPLICATE;
                } else {
                    r = {ILC_E_GENERATION_CONFLICT, header_generation_field(), 0, 0};
                }
            } else if (validate_settings(s, &r)) {
                memcpy(&g.staged, &s, sizeof(s));
                g.staged_digest = settings_digest(s);
                g.staged_pending = 1;
                disposition = ILC_ACK_STAGED;
            }
        }
        if (r.code != 0) {
            note_reject(op, r);
            const uint32_t seat = seat_of_reject(r);
            push_event(ILC_EV_SETTINGS_REJECTED, static_cast<uint8_t>(seat), r.code == ILC_E_FAULT ? 0 : s.header.generation,
                       r.code, r.field, pack_location(r), 0, 0);
            return r.code;
        }
        fill_header(ack.header, ILC_REC_SETTINGS_ACK, sizeof(ack), generation);
        ack.disposition = disposition;
        ack.accepted_generation = accepted().header.generation;
        ack.applied_generation = g.applied.header.generation;
        ack.staged_pending = g.staged_pending;
    }
    return deliver(out, &ack, sizeof(ack));
}

int32_t op_command(const void* in, uint32_t in_len, uint32_t out_cap)
{
    constexpr uint32_t op = ILC_OP_COMMAND;
    (void)out_cap;  // no command reply exists in v1
    reject r = {0, 0, 0, 0};
    ilc_command c;
    memset(&c, 0, sizeof(c));
    if (in_len != sizeof(ilc_command)) {
        r.code = ILC_E_BAD_LENGTH;
    } else if (!copy_from_caller(&c, in, sizeof(c))) {
        r.code = ILC_E_FAULT;
    } else if (validate_header(c.header, in_len, ILC_REC_COMMAND, &r) && validate_command(c, &r)) {
        if (c.command == ILC_CMD_KILL_SWITCH) {
            control::post_kill(c.arg0 == 1);  // the co-op thread runs the ordered disarm (or the revive) next beat
            return 0;
        }
        if (c.command == ILC_CMD_RESTORE_DEFAULTS) {
            return ILC_E_NOT_READY;  // its subsystem is still to be built
        }
        // Seat commands run on the input service's next tick; the outcome comes back as an event.
        const input::command_msg m = {c.command, c.seat, 0, c.header.generation, c.arg0, c.arg1};
        const int32_t queued = input::post_command(m);
        if (queued == 0 || queued == ILC_E_NOT_READY) {
            return queued;
        }
        r.code = queued;  // BUSY: refused and counted like any other rejection
    }
    locked hold;
    note_reject(op, r);
    push_event(ILC_EV_COMMAND_REJECTED, c.seat <= ILC_SEATS ? c.seat : 0, c.header.generation, r.code, r.field,
               pack_location(r), c.command, 0);
    return r.code;
}

int32_t op_get_status(void* out, uint32_t out_cap)
{
    if (out_cap < sizeof(ilc_status)) {
        return record_failure(ILC_OP_GET_STATUS, ILC_E_OUT_TOO_SMALL);
    }
    ilc_status st;
    memset(&st, 0, sizeof(st));
    const boot::status boot_status = boot::current();
    st.state = boot_status.state;
    st.reason = boot_status.reason;
    st.reason_detail = boot_status.detail;
    st.boot_flags = boot_status.flags;
    st.build_flags = 0;
    st.caller_thread_id = GetCurrentThreadId();
    st.ready_mask = ILC_READY_SETTINGS;
    {
        locked hold;
        ensure_defaults_digest();
        pull_input_events();
        fill_header(st.header, ILC_REC_STATUS, sizeof(st), g.applied.header.generation);
        st.accepted_generation = accepted().header.generation;
        st.accepted_digest = g.staged_pending ? g.staged_digest : g.applied_digest;
        st.applied_generation = g.applied.header.generation;
        st.applied_digest = g.applied_digest;
        st.staged_pending = g.staged_pending;
        st.events_pending = g.event_count;
        st.events_dropped = g.events_dropped;
        st.event_next_seq = g.event_next_seq;
        st.safe_points = g.safe_points;
        st.applies = g.applies;
        st.rejects = g.rejects;
        st.last_reject_op = g.last_reject_op;
        st.last_reject_code = g.last_reject_code;
        st.last_reject_field = g.last_reject_field;
        st.last_reject_location = g.last_reject_location;
        st.applied_subsystems = g.applied.subsystems_armed;
    }
    return deliver(out, &st, sizeof(st));
}

int32_t op_get_seats(void* out, uint32_t out_cap)
{
    if (out_cap < sizeof(ilc_seats_reply)) {
        return record_failure(ILC_OP_GET_SEATS, ILC_E_OUT_TOO_SMALL);
    }
    ilc_seats_reply reply;
    memset(&reply, 0, sizeof(reply));
    reply.session_state = ILC_SESSION_NOT_RUNNING;
    reply.count = ILC_SEATS;
    // The live view comes from the session core's published record (wait-free; no lock shared with it).
    session::session_record live;
    bool have = false;
    for (int attempt = 0; attempt < 3 && !have; ++attempt) {
        have = session::read(&live);  // a torn copy (the co-op thread published meanwhile) is simply read again
    }
    const bool running = have && live.head.epoch != 0 && live.phase != session::PHASE_STOPPED;
    if (running) {
        reply.session_state = live.phase;
        reply.epoch = live.head.epoch;
    }
    {
        locked hold;
        const ilc_settings& s = g.applied;
        fill_header(reply.header, ILC_REC_SEATS, sizeof(reply), s.header.generation);
        for (uint32_t i = 0; i < ILC_SEATS; ++i) {
            const ilc_seat_settings& seat = s.seats[i];
            ilc_seat_view& v = reply.seats[i];
            uint8_t flags = 0;
            if (seat.device.kind != ILC_DEVICE_NONE) {
                flags |= ILC_SEATF_HAS_DEVICE;
            }
            if (s.kbm_seat == i + 1) {
                flags |= ILC_SEATF_HAS_KBM;
            }
            if (seat.parked) {
                flags |= ILC_SEATF_PARKED;
            }
            if ((flags & (ILC_SEATF_HAS_DEVICE | ILC_SEATF_HAS_KBM)) && !seat.parked && i < ILC_ACTIVE_SEATS_MAX) {
                flags |= ILC_SEATF_PLAYING;
            }
            if (i == 0) {
                flags |= ILC_SEATF_LOCKED;
            }
            v.seat = static_cast<uint8_t>(i + 1);
            v.flags = flags;
            v.configured_character = seat.character;
            v.live_member = ILC_CHAR_NONE;
            if (running && i < ILC_ACTIVE_SEATS_MAX && live.seat[i].member < ILC_CHARACTERS) {
                v.live_member = static_cast<uint8_t>(live.seat[i].member);
                v.seat_generation = live.seat[i].actor_generation;
            }
            v.device_kind = seat.device.kind;
            v.xinput_slot = seat.device.xinput_slot;
            v.colour = seat.colour;
            v.seat_ui_sounds = seat.seat_ui_sounds;
        }
    }
    return deliver(out, &reply, sizeof(reply));
}

int32_t op_get_devices(void* out, uint32_t out_cap)
{
    // The input service fills the body from its latest snapshot (NOT_READY and no entries while it is stopped).
    alignas(8) uint8_t buffer[sizeof(ilc_devices_reply)];
    memset(buffer, 0, sizeof(buffer));
    ilc_devices_reply* reply = reinterpret_cast<ilc_devices_reply*>(buffer);
    const uint32_t count = input::devices_view(reply);
    const uint32_t size =
        static_cast<uint32_t>(offsetof(ilc_devices_reply, entries) + count * sizeof(ilc_device_entry));
    if (out_cap < size) {
        return record_failure(ILC_OP_GET_DEVICES, ILC_E_OUT_TOO_SMALL);
    }
    fill_header(reply->header, ILC_REC_DEVICES, size, 0);
    return deliver(out, buffer, size);
}

int32_t op_pop_events(void* out, uint32_t out_cap)
{
    constexpr uint32_t head_size = static_cast<uint32_t>(offsetof(ilc_events_reply, events));
    if (out_cap < head_size) {
        return record_failure(ILC_OP_POP_EVENTS, ILC_E_OUT_TOO_SMALL);
    }
    alignas(8) uint8_t buffer[sizeof(ilc_events_reply)];
    uint32_t count = 0;
    {
        locked hold;
        pull_input_events();
        const uint32_t room = (out_cap - head_size) / sizeof(ilc_event);
        count = g.event_count < room ? g.event_count : room;
        ilc_events_reply* r = reinterpret_cast<ilc_events_reply*>(buffer);
        memset(r, 0, head_size);
        for (uint32_t i = 0; i < count; ++i) {
            memcpy(&r->events[i], &g.events[(g.event_head + i) % ILC_EVENT_QUEUE], sizeof(ilc_event));
        }
        g.event_head = (g.event_head + count) % ILC_EVENT_QUEUE;
        g.event_count -= count;
        fill_header(r->header, ILC_REC_EVENTS, head_size + count * static_cast<uint32_t>(sizeof(ilc_event)), 0);
        r->count = count;
        r->dropped_total = g.events_dropped;
        r->next_seq = g.event_next_seq;
        r->pending_after = g.event_count;
    }
    // A fault here loses the popped events; the out buffer is the caller's own memory, so that is its bug.
    return deliver(out, buffer, head_size + count * static_cast<uint32_t>(sizeof(ilc_event)));
}

int32_t op_get_settings(void* out, uint32_t out_cap)
{
    if (out_cap < sizeof(ilc_settings)) {
        return record_failure(ILC_OP_GET_SETTINGS, ILC_E_OUT_TOO_SMALL);
    }
    ilc_settings copy;
    {
        locked hold;  // the applied slot changes only under the lock
        memcpy(&copy, &g.applied, sizeof(copy));
    }
    return deliver(out, &copy, sizeof(copy));
}

int32_t op_test_safe_point(void* out, uint32_t out_cap)
{
    // Once the boot has started its init thread, the UI tick is about to own (or owns) the safe point: no script
    // thread may apply settings, not even during start-up.
    if (__atomic_load_n(&g.tick_attached, __ATOMIC_ACQUIRE) ||
        (boot::current().flags & ILC_BOOTF_THREAD_STARTED) != 0) {
        return record_failure(ILC_OP_TEST_SAFE_POINT, ILC_E_NOT_AVAILABLE);
    }
    if (out_cap < sizeof(ilc_safe_point_result)) {
        return record_failure(ILC_OP_TEST_SAFE_POINT, ILC_E_OUT_TOO_SMALL);
    }
    ilc_safe_point_result result;
    memset(&result, 0, sizeof(result));
    result.result = bridge_safe_point();
    {
        locked hold;
        ensure_defaults_digest();
        fill_header(result.header, ILC_REC_SAFE_POINT_RESULT, sizeof(result), g.applied.header.generation);
        result.applied_generation = g.applied.header.generation;
        result.applied_digest = g.applied_digest;
        result.safe_points = g.safe_points;
    }
    return deliver(out, &result, sizeof(result));
}

}  // namespace

uint32_t bridge_safe_point(uint32_t* applied_subsystems, ilc_settings* applied_copy)
{
    if (!TryAcquireSRWLockExclusive(&g.lock)) {
        return ILC_SAFE_POINT_BUSY;  // the lock holder is a script thread copying a record; next tick
    }
    ensure_defaults_digest();
    ++g.safe_points;
    uint32_t result = ILC_SAFE_POINT_IDLE;
    if (g.staged_pending) {
        memcpy(&g.applied, &g.staged, sizeof(ilc_settings));
        g.applied_digest = g.staged_digest;
        g.staged_pending = 0;
        ++g.applies;
        push_event(ILC_EV_SETTINGS_APPLIED, 0, g.applied.header.generation, 0, 0, 0, g.applied_digest, 0);
        result = ILC_SAFE_POINT_APPLIED;
        if (applied_subsystems != nullptr) {
            *applied_subsystems = g.applied.subsystems_armed;
        }
        if (applied_copy != nullptr) {
            memcpy(applied_copy, &g.applied, sizeof(ilc_settings));
        }
    }
    ReleaseSRWLockExclusive(&g.lock);
    return result;
}

void bridge_copy_applied(ilc_settings* out)
{
    locked hold;
    memcpy(out, &g.applied, sizeof(ilc_settings));
}

void bridge_attach_tick()
{
    __atomic_store_n(&g.tick_attached, 1u, __ATOMIC_RELEASE);
}

}  // namespace ilc

extern "C" int32_t ILC_Bridge(uint64_t op_arg, const void* in, uint64_t in_len_arg, void* out, uint64_t out_cap_arg)
{
    using namespace ilc;
    // Fail closed: each of op, inLen and outCap must fit in 32 bits (bridge_abi.h).
    if ((op_arg | in_len_arg | out_cap_arg) >> 32) {
        return record_failure(static_cast<uint32_t>(op_arg), ILC_E_BAD_ARGS);
    }
    const uint32_t op = static_cast<uint32_t>(op_arg);
    const uint32_t in_len = static_cast<uint32_t>(in_len_arg);
    // Replies are at most a few KiB; the cap keeps every byte count representable as a positive s32.
    const uint32_t out_cap = out_cap_arg > 0x7FFFFFFFu ? 0x7FFFFFFFu : static_cast<uint32_t>(out_cap_arg);

    if (in_len > ILC_MAX_RECORD) {
        return record_failure(op, ILC_E_TOO_LARGE);
    }
    if ((in_len != 0 && in == nullptr) || out == nullptr) {
        return record_failure(op, ILC_E_BAD_ARGS);
    }
    const bool query = op != ILC_OP_HELLO && op != ILC_OP_SET_SETTINGS && op != ILC_OP_COMMAND;
    switch (op) {
    case ILC_OP_HELLO:
    case ILC_OP_SET_SETTINGS:
    case ILC_OP_COMMAND:
    case ILC_OP_GET_STATUS:
    case ILC_OP_GET_SEATS:
    case ILC_OP_GET_DEVICES:
    case ILC_OP_POP_EVENTS:
    case ILC_OP_GET_SETTINGS:
    case ILC_OP_TEST_SAFE_POINT:
        break;
    default:
        return record_failure(op, ILC_E_UNKNOWN_OP);
    }
    if (query && in_len != 0) {
        return record_failure(op, ILC_E_BAD_LENGTH);  // query ops take no input
    }
    int32_t status = ILC_E_UNKNOWN_OP;
    switch (op) {
    case ILC_OP_HELLO:
        return record_failure(op, op_hello(in, in_len, out, out_cap));
    case ILC_OP_SET_SETTINGS:
        return op_set_settings(in, in_len, out, out_cap);  // records its own failures
    case ILC_OP_COMMAND:
        return op_command(in, in_len, out_cap);
    case ILC_OP_GET_STATUS:
        status = op_get_status(out, out_cap);
        break;
    case ILC_OP_GET_SEATS:
        status = op_get_seats(out, out_cap);
        break;
    case ILC_OP_GET_DEVICES:
        status = op_get_devices(out, out_cap);
        break;
    case ILC_OP_POP_EVENTS:
        status = op_pop_events(out, out_cap);
        break;
    case ILC_OP_GET_SETTINGS:
        status = op_get_settings(out, out_cap);
        break;
    case ILC_OP_TEST_SAFE_POINT:
        status = op_test_safe_point(out, out_cap);
        break;
    default:
        break;
    }
    // OUT_TOO_SMALL and NOT_AVAILABLE were recorded where they arose; a probe fault on out is recorded here.
    if (status == ILC_E_FAULT) {
        return record_failure(op, status);
    }
    return status;
}
