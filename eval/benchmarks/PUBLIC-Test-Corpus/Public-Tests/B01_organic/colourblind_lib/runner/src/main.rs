// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    #[derive(Copy)]
    enum CbImpairment {
        CbProtanopia,
        CbDeuteranopia,
        CbTritanopia,
    }
}

harness! {
    state: {
        impairment: CbImpairment,
        r: f32,
        g: f32,
        b: f32
    },

    signature: unsafe extern "C" fn(CbImpairment, *mut f32, *mut f32, *mut f32),

    fn run(&mut self) {
        // self.impairment %= 3;
        unsafe {
            (*SYMBOL)(
                self.impairment,
                &raw mut self.r as *mut f32,
                &raw mut self.g as *mut f32,
                &raw mut self.b as *mut f32,
            )
        };
    }
}
