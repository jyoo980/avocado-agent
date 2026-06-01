// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        hex: Vec<c_char>,
        hex_maxlen: usize,
        bin: Vec<u8>,
        bin_len: usize,
    },

    signature: unsafe extern "C" fn(*mut c_char, usize, *const u8, usize) -> *mut c_char,

    fn run(&mut self) {
        unsafe {
            (*SYMBOL)(
                self.hex.as_mut_ptr(),
                self.hex_maxlen,
                self.bin.as_ptr(),
                self.bin_len
            )
        };
    }
}
