// Ivalice Legacy co-op overlay: the built-in theme list (see theme_builtin.h).
#include "overlay/theme_builtin.h"

namespace ilc::overlay::builtin {

extern const theme_data THEME_BOLD;
extern const theme_data THEME_HUMANIST;
extern const theme_data THEME_CRISP;

const theme_data& get(uint32_t k)
{
    switch (k) {
    case 1: return THEME_HUMANIST;
    case 2: return THEME_CRISP;
    default: return THEME_BOLD;
    }
}

uint32_t find(const char* id, uint32_t n)
{
    for (uint32_t k = 0; k < COUNT; ++k) {
        const char* b = get(k).id;
        uint32_t i = 0;
        while (i < n && b[i] != '\0' && b[i] == id[i]) {
            ++i;
        }
        if (i == n && b[i] == '\0') {
            return k;
        }
    }
    return COUNT;
}

}  // namespace ilc::overlay::builtin
