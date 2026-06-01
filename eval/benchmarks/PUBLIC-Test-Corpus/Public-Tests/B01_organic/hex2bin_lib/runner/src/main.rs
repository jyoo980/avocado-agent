// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        bin: Vec<u8>,
        bin_maxlen: usize,
        hex: Vec<c_char>,
        hex_len: usize,
        ignore: Vec<c_char>,
        hex_end_p: c_char,
        returns: c_int
    },

    signature: unsafe extern "C" fn(*mut u8, usize, *const c_char, usize, *const c_char, *mut *const c_char) -> c_int,

    fn run(&mut self) {
        self.bin = vec![0; self.bin_maxlen];
        self.returns = unsafe {
            (*SYMBOL)(
                self.bin.as_mut_ptr(),
                self.bin_maxlen,
                self.hex.as_ptr(),
                self.hex_len,
                self.ignore.as_ptr(),
                self.hex_end_p as *mut *const c_char
            )
        }
    }
}
