// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    struct Shape {
        a: f32,
        b: f32,
        c: f32,
        d: f32,
    }
}

harness! {
    state: {
        a: Shape,
        type_a: c_uint,
        b: Shape,
        type_b: c_uint,
        returns: c_int,
    },

    signature: unsafe extern "C" fn(*const c_void, c_uint, *const c_void, c_uint) -> c_int,

    fn run(&mut self) {
        self.returns = unsafe {
            (*SYMBOL)(
                &raw const self.a as *const c_void,
                self.type_a,
                &raw const self.b as *const c_void,
                self.type_b,
            )
        };
    }
}
