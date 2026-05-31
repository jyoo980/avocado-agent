#include "lib.h"

uint32_t rev16(uint32_t a)
__CPROVER_assigns()
__CPROVER_ensures(__CPROVER_return_value <= 0xFFFF)
__CPROVER_ensures(__CPROVER_forall {
    int i;
    (0 <= i && i < 16) ==>
        (((__CPROVER_return_value >> i) & 1U) == ((__CPROVER_old(a) >> (15 - i)) & 1U))
})
{
    a = ((a & 0xAAAA) >> 1) | ((a & 0x5555) << 1);
    a = ((a & 0xCCCC) >> 2) | ((a & 0x3333) << 2);
    a = ((a & 0xF0F0) >> 4) | ((a & 0x0F0F) << 4);
    a = ((a & 0xFF00) >> 8) | ((a & 0x00FF) << 8);
    return a;
}
