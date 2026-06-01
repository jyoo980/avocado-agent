// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    #[derive(Copy)]
    struct CbRgb255 {
        r: c_uchar,
        g: c_uchar,
        b: c_uchar,
    }
}

harness! {
    state: {
        a: CbRgb255,
        b: CbRgb255,
        returns: c_float,
    },

    signature: unsafe extern "C" fn(CbRgb255, CbRgb255) -> c_float,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.a,
                self.b
            )
        };
    }
}
