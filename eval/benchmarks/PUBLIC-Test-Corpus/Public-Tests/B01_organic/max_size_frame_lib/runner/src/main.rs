// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u32 = u32;

harness! {
    state: {
        blocksize: tflac_u32,
        channels: tflac_u32,
        bitdepth: tflac_u32,
        returns: tflac_u32,
    },

    signature: unsafe extern "C" fn(tflac_u32, tflac_u32, tflac_u32) -> tflac_u32,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.blocksize,
                self.channels,
                self.bitdepth,
            )
        };
    }
}
