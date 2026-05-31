#include <math.h>

#include "lib.h"

static float cbLuminance(float R, float G, float B) {
    R = ((float)(R > 0.04045 ? pow((R + 0.055) / 1.055, 2.4) : R / 12.92));
    G = ((float)(G > 0.04045 ? pow((G + 0.055) / 1.055, 2.4) : G / 12.92));
    B = ((float)(B > 0.04045 ? pow((B + 0.055) / 1.055, 2.4) : B / 12.92));
    float Result = 0.2126f * R + 0.7152f * G + 0.0722f * B;
    return Result;
}

static float cbContrastRatio(float RA, float GA, float BA, float RB, float GB,
                      float BB) {
    float LumA = cbLuminance(RA, GA, BA);
    float LumB = cbLuminance(RB, GB, BB);
    float High = LumA, Low = LumB;
    if (High < Low) {
        High = LumB, Low = LumA;
    }
    float Ratio = High / Low;
    return Ratio;
}

float contrast_ratio(cb_rgb_255 A, cb_rgb_255 B)
    /* Operates on by-value RGB structs; touches no memory. */
    __CPROVER_assigns()
{
    return cbContrastRatio(((float)(A.R) / 255.f), ((float)(A.G) / 255.f),
                           ((float)(A.B) / 255.f), ((float)(B.R) / 255.f),
                           ((float)(B.G) / 255.f), ((float)(B.B) / 255.f));
}

#ifdef CBMC_HARNESS
void contrast_ratio_harness(void) {
    cb_rgb_255 A, B;
    contrast_ratio(A, B);
}
#endif
