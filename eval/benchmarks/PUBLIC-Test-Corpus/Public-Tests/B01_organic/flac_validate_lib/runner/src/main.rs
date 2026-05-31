// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_u32 = u32;

state_member! {
    struct Tflac {
        blocksize: tflac_u32,
        samplerate: tflac_u32,
        channels: tflac_u32,
        bitdepth: tflac_u32,
        channel_mode: tflac_u8,
        max_rice_value: tflac_u8,
        min_partition_order: tflac_u8,
        max_partition_order: tflac_u8,
        partition_order: tflac_u8,
        cur_blocksize: tflac_u32,
    }
}

harness! {
    state: {
        t: Tflac,
        returns: c_int
    },

    signature: unsafe extern "C" fn(*mut Tflac) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                &raw mut self.t as *mut Tflac
            )
        };
    }
}
