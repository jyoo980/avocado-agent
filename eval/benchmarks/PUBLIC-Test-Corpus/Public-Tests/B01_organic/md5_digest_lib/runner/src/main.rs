// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_u32 = u32;

state_member! {
    struct TflacMd5 {
        a: tflac_u32,
        b: tflac_u32,
        c: tflac_u32,
        d: tflac_u32,
    }
}

harness! {
    state: {
        m: TflacMd5,
        out: [tflac_u8; 16],
    },

    signature: unsafe extern "C" fn(*const TflacMd5, *mut tflac_u8),

    fn run(&mut self) {
        unsafe {
            (*SYMBOL)(
                &raw const self.m as *const TflacMd5,
                &raw mut self.out as *mut tflac_u8,
            )
        };
    }
}
