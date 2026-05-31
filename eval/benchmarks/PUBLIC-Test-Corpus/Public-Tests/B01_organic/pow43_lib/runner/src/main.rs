// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        x: c_int,
        returns: c_float
    },

    signature: unsafe extern "C" fn(c_int) -> c_float,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.x
            )
        }
    }
}
