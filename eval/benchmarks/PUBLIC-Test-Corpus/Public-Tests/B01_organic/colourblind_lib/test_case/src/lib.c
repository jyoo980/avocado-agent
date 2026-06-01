#include "lib.h"

static void Protanopia(float *Red, float *Green, float *Blue)
__CPROVER_requires(__CPROVER_is_fresh(Red, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Green, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Blue, sizeof(float)))
__CPROVER_assigns(*Red, *Green, *Blue)
{
    float R = *Red, G = *Green, B = *Blue;
    *Red = 0.17055699213417f * R + 0.82944301379913f * G + 2.91188E-9f * B;
    *Green = 0.17055699092998f * R + 0.82944300785005f * G - 5.98679E-10f * B;
    *Blue = -0.00451714424166f * R + 0.00451714427397f * G + B;
}

static void Deuteranopia(float *Red, float *Green, float *Blue)
__CPROVER_requires(__CPROVER_is_fresh(Red, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Green, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Blue, sizeof(float)))
__CPROVER_assigns(*Red, *Green, *Blue)
{
    float R = *Red, G = *Green, B = *Blue;
    *Red = 0.33066007266046f * R + 0.66933992517563f * G + 3.559314E-9f * B;
    *Green = 0.33066007387760f * R + 0.66933992719147f * G - 1.758327E-9f * B;
    *Blue = -0.02785538261323f * R + 0.02785538252318f * G + B;
}

static void Tritanopia(float *Red, float *Green, float *Blue)
__CPROVER_requires(__CPROVER_is_fresh(Red, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Green, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(Blue, sizeof(float)))
__CPROVER_assigns(*Red, *Green, *Blue)
{
    float R = *Red, G = *Green, B = *Blue;
    *Red = R + 0.12739886310880f * G - 0.12739886341072f * B;
    *Green = -4.486E-11f * R + 0.87390929928361f * G + 0.12609070101523f * B;
    *Blue = 3.1113E-10f * R + 0.87390929725848f * G + 0.12609070067115f * B;
}

void colourblind(cb_impairment Impairment, float *R, float *G, float *B)
__CPROVER_requires(__CPROVER_is_fresh(R, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(G, sizeof(float)))
__CPROVER_requires(__CPROVER_is_fresh(B, sizeof(float)))
__CPROVER_assigns(*R, *G, *B)
{
    switch (Impairment) {
    case cbProtanopia:
        Protanopia(R, G, B);
        break;
    case cbDeuteranopia:
        Deuteranopia(R, G, B);
        break;
    case cbTritanopia:
        Tritanopia(R, G, B);
    }
}
