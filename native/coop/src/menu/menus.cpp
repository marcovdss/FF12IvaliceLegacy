// Ivalice Legacy co-op module: one guest's battle menu (see menus.h).
#include "menu/menus.h"

#include <string.h>

namespace ilc::menu {

void seat_menu_reset(seat_menu* s)
{
    memset(s, 0, sizeof(*s));
    model_reset(&s->model);
}

int32_t seat_menu_refresh(seat_menu* s, const catalog_tables& t, const shared_state& sh, const seat_state& st,
                          commit_result* result)
{
    if (result) {
        *result = {};
    }
    gambit_observe(&s->gambits, st.record_state);
    const bool changed = (s->member_known && s->member != st.member) ||
                         (s->model.pages.valid && s->model.pages.member != st.member);
    if (changed) {
        // Another character: nothing of the previous one may be served, even if the next build fails.
        if (s->model.mode != MODE_CLOSED) {
            model_close(&s->model);
            ++s->identity_closes;
        }
        model_reset(&s->model);
        s->signature = 0;
        s->gambits = {};
        gambit_observe(&s->gambits, st.record_state);
    }
    s->member = st.member;
    s->member_known = 1;
    const uint64_t signature = catalog_signature(t, sh, st);
    if (s->signature != 0 && signature == s->signature) {
        ++s->unchanged;
        s->last_status = BUILD_OK;
        return REFRESH_UNCHANGED;
    }
    const int32_t status = build_catalog(t, sh, st, &s->scratch);
    s->last_status = status;
    if (status != BUILD_OK || model_commit(&s->model, s->scratch, result) != 0) {
        ++s->kept;
        return status != BUILD_OK ? status : BUILD_INVALID;
    }
    s->signature = signature;
    ++s->builds;
    return REFRESH_COMMITTED;
}

}  // namespace ilc::menu
