// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type ima_u32_t = c_uint;
type ima_u64_t = c_ulonglong;
type ima_f64_t = f64;
type ima_u16_t = c_ushort;
type ima_u8_t = c_uchar;

state_member! {
    struct ImaBlock {
        preamble: ima_u16_t,
        data: [ima_u8_t; 32]
    }
}

#[repr(C)]
struct ImaInfo {
    blocks: *const ImaBlock,
    size: ima_u64_t,
    sample_rate: ima_f64_t,
    frame_count: ima_u64_t,
    channel_count: ima_u32_t,
}

harness! {
    state: {
        blocks: [ImaBlock; 32],
        size: ima_u64_t,
        sample_rate: ima_f64_t,
        frame_count: ima_u64_t,
        channel_count: ima_u32_t,
        data: [u64; 32],
        returns: c_int,
    },

    signature: unsafe extern "C" fn(*mut ImaInfo, *const c_void) -> c_int,

    fn run(&mut self) {
        let mut info = ImaInfo {
            blocks: &raw const self.blocks as *const ImaBlock,
            size: self.size,
            sample_rate: self.sample_rate,
            frame_count: self.frame_count,
            channel_count: self.channel_count,
        };
        self.returns = unsafe {
            (*SYMBOL)(
                &raw mut info as *mut ImaInfo,
                &raw const self.data as *const c_void
            )
        };
        self.size = info.size;
        self.sample_rate = info.sample_rate;
        self.frame_count = info.frame_count;
        self.channel_count = info.channel_count;
    }
}
