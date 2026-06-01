// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        y: c_float,
        exp_q2: c_int,
        returns: c_float
    },

    signature: unsafe extern "C" fn(c_float, c_int) -> c_float,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.y,
                self.exp_q2
            )
        }
    }
}
