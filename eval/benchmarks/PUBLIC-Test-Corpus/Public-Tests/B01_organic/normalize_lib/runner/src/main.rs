// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

// FIXME: Can probably initialize dest + src as size
harness! {
    state: {
        dest: Vec<c_float>,
        src: Vec<c_float>,
        size: c_int,
    },

    signature: unsafe extern "C" fn(*mut c_float, *const c_float, c_int),

    fn run(&mut self) {
        self.dest = vec![0.0; self.size as usize];
        unsafe {
            (*SYMBOL)(
                self.dest.as_mut_ptr(),
                self.src.as_ptr(),
                self.size
            )
        }
    }
}
