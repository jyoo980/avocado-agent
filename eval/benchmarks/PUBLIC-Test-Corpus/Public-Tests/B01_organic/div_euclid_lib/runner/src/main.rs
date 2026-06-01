// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        v1: c_int,
        v2: c_int,
        returns: c_int
    },

    signature: unsafe extern "C" fn(c_int, c_int) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.v1,
                self.v2
            )
        }
    }
}
