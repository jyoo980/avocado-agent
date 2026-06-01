// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

state_member! {
    struct cp_pixel_t {
        r: u8,
        g: u8,
        b: u8,
        a: u8,
    }
}

#[repr(C)]
struct cp_image_t {
    w: c_int,
    h: c_int,
    pix: *mut cp_pixel_t
}

harness! {
    state: {
        w: c_int,
        h: c_int,
        pix: [cp_pixel_t; 16]
    },

    signature: unsafe extern "C" fn(*mut cp_image_t),

    fn run(&mut self) {
        let mut s = cp_image_t {
            w: self.w,
            h: self.h,
            pix: &raw mut self.pix as *mut cp_pixel_t
        };
        unsafe {
            (*SYMBOL)(
                &raw mut s as *mut cp_image_t
            )
        }
    }
}
