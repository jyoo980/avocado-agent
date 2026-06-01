// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    // FIXME: The fields h1 and h2 look to have a size of 3 based on the
    // implementation but that isn't encoded in the interface so not sure
    // if we want to make these fixed sizes or not?
    state: {
        h1: [u8; 3],
        h2: [u8; 3],
        returns: c_int
    },

    signature: unsafe extern "C" fn(*const u8, *const u8) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.h1.as_ptr(),
                self.h2.as_ptr()
            )
        }
    }
}
