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
        rgb: CbRgb255,
        returns: CbRgb255
    },

    signature: unsafe extern "C" fn(CbRgb255) -> CbRgb255,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.rgb
            )
        };
    }
}
