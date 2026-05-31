// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_u16 = u16;
type tflac_u32 = u32;

// FIXME: d has a size of len
harness! {
    state: {
        d: Vec<tflac_u8>,
        len: tflac_u32,
        crc16: tflac_u16,
        returns: tflac_u16,
    },

    signature: unsafe extern "C" fn(*const tflac_u8, tflac_u32, tflac_u16) -> tflac_u16,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.d.as_ptr(),
                self.len,
                self.crc16,
            )
        };
    }
}
