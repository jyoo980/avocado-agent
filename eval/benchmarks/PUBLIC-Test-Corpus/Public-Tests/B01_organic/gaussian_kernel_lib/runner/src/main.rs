// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        dest: Vec<c_float>,
        size: c_int,
        radius: c_float
    },

    signature: unsafe extern "C" fn(*mut c_float, c_int, c_float),

    fn run(&mut self) {
        self.dest = vec![0.0; self.size as usize];
        unsafe {
            (*SYMBOL)(
                self.dest.as_mut_ptr(),
                self.size,
                self.radius
            )
        }
    }
}
