// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

type mp3d_sample_t = i16;

harness! {
    state: {
        pcm: Vec<mp3d_sample_t>,
        nch: c_int,
        z: [[[c_float; 32]; 2]; 15],
    },

    signature: unsafe extern "C" fn(*mut mp3d_sample_t, c_int, *const c_float),

    fn run(&mut self) {
        self.pcm = vec![0; (self.nch as usize) * 16 + 1];
        unsafe {
            (*SYMBOL)(
                self.pcm.as_mut_ptr(),
                self.nch,
                self.z.as_ptr() as *const c_float
            )
        }
    }
}
