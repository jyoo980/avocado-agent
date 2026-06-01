// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        flt: c_float,
        returns: u16
    },

    signature: unsafe extern "C" fn(c_float) -> u16,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.flt
            )
        }
    }
}
