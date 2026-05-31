// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_s32 = i32;
type tflac_u32 = u32;
type tflac_u64 = u64;

state_member! {
    struct tflac_md5 {
        pos: tflac_u32,
        total: tflac_u64,
        buffer: [[tflac_u8; 24]; 3]
    }
}

state_member! {
    struct tflac {
        md5_ctx: tflac_md5,
        cur_blocksize: tflac_u32,
        channels: tflac_u32,
    }
}

harness! {
    state: {
        t: [[tflac; 32]; 4],
        samples: [[tflac_s32; 32]; 4],
        returns: tflac_u32
    },

    signature: unsafe extern "C" fn(*mut tflac, *const tflac_s32) -> tflac_u32,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.t.as_mut_ptr() as *mut tflac,
                self.samples.as_ptr() as *const tflac_s32
            )
        };
    }
}
