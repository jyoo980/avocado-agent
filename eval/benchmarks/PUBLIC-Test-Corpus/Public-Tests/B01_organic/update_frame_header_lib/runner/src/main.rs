// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_u32 = u32;

state_member! {
    struct Tflac {
        samplerate: tflac_u32,
        channels: tflac_u32,
        bitdepth: tflac_u32,
        channel_mode: tflac_u8,
        frame_header: tflac_u32,
        cur_blocksize: tflac_u32,
    }
}

harness! {
    state: {
        t: Tflac
    },

    signature: unsafe extern "C" fn(*mut Tflac),

    fn run(&mut self) {
        unsafe {
            (*SYMBOL)(
                &raw mut self.t as *mut Tflac
            )
        };
    }
}
