// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

harness! {
    state: {
        uni: c_int,
        step: c_int,
        pred: c_int,
        tgt: c_int,
        tgt2: c_int,
        lsbit: c_int,
        returns: c_int,
    },

    signature: unsafe extern "C" fn(c_int, c_int, c_int, c_int, c_int, c_int) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                self.uni,
                self.step,
                self.pred,
                self.tgt,
                self.tgt2,
                self.lsbit
            )
        };
    }
}
