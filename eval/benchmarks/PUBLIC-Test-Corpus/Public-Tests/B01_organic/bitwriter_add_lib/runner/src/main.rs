// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type tflac_u8 = u8;
type tflac_u32 = u32;
type tflac_u64 = u64;
type tflac_uint = tflac_u64;

#[repr(C)]
struct TflacBitwriter{
    val: tflac_uint,
    bits: tflac_u32,
    pos: tflac_u32,
    len: tflac_u32,
    tot: tflac_u32,
    buffer: *mut tflac_u8,
}

harness! {
    state: {
        s_val: tflac_uint,
        s_bits: tflac_u32,
        s_tot: tflac_u32,
        bits: tflac_u32,
        val: tflac_uint,
        returns: c_int,
    },

    signature: unsafe extern "C" fn(*mut TflacBitwriter, tflac_u32, tflac_uint) -> c_int,

    fn run(&mut self) {
        let mut z = 0;
        let mut s = TflacBitwriter {
            val: self.s_val,
            bits: self.s_bits,
            pos: 0,
            len: 0,
            tot: self.s_tot,
            buffer: &raw mut z as *mut tflac_u8,
        };

        self.returns = unsafe {
            (*SYMBOL)(
                &raw mut s as *mut TflacBitwriter,
                self.bits,
                self.val
            )
        };

        self.s_val = s.val;
        self.s_bits = s.bits;
        self.s_tot = s.tot;
    }

}
