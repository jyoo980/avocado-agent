// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type wchar_t = i32;

harness! {
    state: {
        dst: Vec<wchar_t>,
        numElem: usize,
        src: Vec<wchar_t>,
        returns: c_int,
    },

    signature: unsafe extern "C" fn(*mut wchar_t, usize, *const wchar_t) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.dst.as_mut_ptr(),
                self.numElem,
                self.src.as_ptr(),
            )
        }
    }
}
