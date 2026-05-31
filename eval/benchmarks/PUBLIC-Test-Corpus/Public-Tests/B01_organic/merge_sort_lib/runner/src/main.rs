// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    struct spritebatch_sprite_t {
        texture_id: c_ulonglong,
        sort_bits: c_int,
    }
}

harness! {
    state: {
        a: Vec<spritebatch_sprite_t>,
        b: Vec<spritebatch_sprite_t>,
        size: c_int
    },

    signature: unsafe extern "C" fn(*mut spritebatch_sprite_t, *mut spritebatch_sprite_t, c_int),

    fn run(&mut self) {
        self.b = vec![
            spritebatch_sprite_t {
                texture_id: 0,
                sort_bits: 0
            };
            self.size as usize
        ];
        unsafe {
            (*SYMBOL)(
                self.a.as_mut_ptr(),
                self.b.as_mut_ptr(),
                self.size
            )
        }
    }
}
