// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        dest: [c_float; 3],
        src: [c_float; 3]
    },

    signature: unsafe extern "C" fn(*mut c_float, *const c_float),

    fn run(&mut self) {
        unsafe {
            (*SYMBOL)(
                self.dest.as_mut_ptr(),
                self.src.as_ptr()
            )
        }
    }
}
