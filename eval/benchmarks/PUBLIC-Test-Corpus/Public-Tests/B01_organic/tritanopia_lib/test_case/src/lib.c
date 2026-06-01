#include <math.h>

#include "lib.h"

typedef struct cb_rgb {
    float R;
    float G;
    float B;
} cb_rgb;

static cb_rgb cbRemoveGammaRGB(cb_rgb RGB)
__CPROVER_assigns()
{
    cb_rgb Result = {
        ((float)(RGB.R > 0.04045 ? pow((RGB.R + 0.055) / 1.055, 2.4)
                                 : RGB.R / 12.92)),
        ((float)(RGB.G > 0.04045 ? pow((RGB.G + 0.055) / 1.055, 2.4)
                                 : RGB.G / 12.92)),
        ((float)(RGB.B > 0.04045 ? pow((RGB.B + 0.055) / 1.055, 2.4)
                                 : RGB.B / 12.92))};
    return Result;
}

static cb_rgb cbNorm(cb_rgb_255 RGB)
__CPROVER_assigns()
{
    cb_rgb Result = {((float)(RGB.R) / 255.f), ((float)(RGB.G) / 255.f),
                     ((float)(RGB.B) / 255.f)};
    return Result;
}

static cb_rgb_255 cbDenorm(cb_rgb RGB)
__CPROVER_assigns()
{
    cb_rgb_255 Result = {((unsigned char)((RGB.R) * 255.f + 0.5f)),
                         ((unsigned char)((RGB.G) * 255.f + 0.5f)),
                         ((unsigned char)((RGB.B) * 255.f + 0.5f))};
    return Result;
}

static cb_rgb cbApplyGammaRGB(cb_rgb RGB)
__CPROVER_assigns()
{
    cb_rgb Result = {((float)(RGB.R > 0.00313080495356037151702786377709
                                  ? 1.055 * pow((RGB.R), 0.4166666666) - 0.055
                                  : RGB.R * 12.92)),
                     ((float)(RGB.G > 0.00313080495356037151702786377709
                                  ? 1.055 * pow((RGB.G), 0.4166666666) - 0.055
                                  : RGB.G * 12.92)),
                     ((float)(RGB.B > 0.00313080495356037151702786377709
                                  ? 1.055 * pow((RGB.B), 0.4166666666) - 0.055
                                  : RGB.B * 12.92))};
    return Result;
}

static void Tritanopia(float *Red, float *Green, float *Blue)
__CPROVER_assigns(*Red, *Green, *Blue)
{
    float R = *Red, G = *Green, B = *Blue;
    *Red = R + 0.12739886310880f * G - 0.12739886341072f * B;
    *Green = -4.486E-11f * R + 0.87390929928361f * G + 0.12609070101523f * B;
    *Blue = 3.1113E-10f * R + 0.87390929725848f * G + 0.12609070067115f * B;
}

cb_rgb_255 tritanopia(cb_rgb_255 RGB)
__CPROVER_assigns()
{
    cb_rgb RGBNorm = cbRemoveGammaRGB(cbNorm(RGB));
    Tritanopia(&RGBNorm.R, &RGBNorm.G, &RGBNorm.B);
    cb_rgb_255 Result = cbDenorm(cbApplyGammaRGB(RGBNorm));
    return Result;
}
