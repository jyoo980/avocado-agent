// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        h: [u8; 3],
        returns: c_uint,
    },

    signature: unsafe extern "C" fn(*const u8) -> c_uint,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.h.as_ptr()
            )
        }
    }
}
