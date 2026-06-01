// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    #[derive(Copy)]
    struct lm_vec2 {
        x: c_float,
        y: c_float,
    }
}

harness! {
    state: {
        p1: lm_vec2,
        p2: lm_vec2,
        p3: lm_vec2,
        p: lm_vec2,
        returns: lm_vec2
    },

    signature: unsafe extern "C" fn(lm_vec2, lm_vec2, lm_vec2, lm_vec2) -> lm_vec2,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.p1,
                self.p2,
                self.p3,
                self.p
            )
        }
    }
}
