typedef enum cb_impairment {
    cbProtanopia,
    cbDeuteranopia,
    cbTritanopia,
} cb_impairment;

void colourblind(cb_impairment Impairment, float *R, float *G, float *B);
