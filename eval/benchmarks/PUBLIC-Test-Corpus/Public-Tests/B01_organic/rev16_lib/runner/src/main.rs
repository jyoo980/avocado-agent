// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        a: u32,
        returns: u32,
    },

    signature: unsafe extern "C" fn(u32) -> u32,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.a,
            )
        };
    }
}
