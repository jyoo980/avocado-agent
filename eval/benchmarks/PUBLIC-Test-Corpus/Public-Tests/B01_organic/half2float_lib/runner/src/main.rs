// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        h: u16,
        returns: c_float,
    },

    signature: unsafe extern "C" fn(u16) -> c_float,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.h
            )
        }
    }
}
