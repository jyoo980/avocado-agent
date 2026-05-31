// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    struct cn_rnd_t {
        state: [u64; 2],
    }
}

harness! {
    state: {
        rnd: cn_rnd_t,
        returns: c_double
    },

    signature: unsafe extern "C" fn(*mut cn_rnd_t) -> c_double,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                &mut self.rnd as *mut cn_rnd_t
            )
        }
    }
}
