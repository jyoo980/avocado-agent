// © 2026 Massachusetts Institute of Technology
// MIT License

#![cfg_attr(fuzzing, no_main)]

use cando2::*;

#[repr(C)]
struct bs_t {
    buf: *mut u8,
    pos: c_int,
    limit: c_int,
}

// size_of = 32
#[repr(C)]
struct L3_gr_info_t {
    sfbtab: *mut u8,
    part_23_length: u16,
    big_values: u16,
    scalefac_compress: u16,
    global_gain: u8,
    block_type: u8,
    mixed_block_flag: u8,
    n_long_sfb: u8,
    n_short_sfb: u8,
    table_select: [u8; 3],
    region_count: [u8; 3],
    subblock_gain: [u8; 3],
    preflag: u8,
    scalefac_scale: u8,
    count1_table: u8,
    scfsi: u8,
}

state_member! {
    struct Granule {
        sfbtab: [u8; 23],
        //l_sfbtab: Vec<u8>,
        part_23_length: u16,
        big_values: u16,
        scalefac_compress: u16,
        global_gain: u8,
        block_type: u8,
        mixed_block_flag: u8,
        n_long_sfb: u8,
        n_short_sfb: u8,
        table_select: [u8; 3],
        region_count: [u8; 3],
        subblock_gain: [u8; 3],
        preflag: u8,
        scalefac_scale: u8,
        count1_table: u8,
        scfsi: u8,
    }
}

harness! {
    state: {
        //b_buf: [u8; 20],
        b_buf: Vec<u8>,
        b_pos: c_int,
        b_limit: c_int,
        granule: [Granule; 4],
        hdr: [u8; 4],
        returns: c_int
    },

    signature: unsafe extern "C" fn(*mut bs_t, *mut L3_gr_info_t, *const u8) -> c_int,

    fn run(&mut self) {
        //println!("{}", size_of::<L3_gr_info_t>());
        //self.b_buf = vec![0; 32]; // b_limit?
        //self.l_sfbtab = vec![0; 32]; // ?
        let mut b = bs_t {
            buf: self.b_buf.as_mut_ptr(),
            pos: self.b_pos,
            limit: self.b_limit
        };

        use std::mem::MaybeUninit;

        let mut ul: [MaybeUninit<L3_gr_info_t>; 4] = [ const { MaybeUninit::uninit() }; 4];

        for i in 0..4 {
            let n = L3_gr_info_t {
                sfbtab: self.granule[i].sfbtab.as_mut_ptr() as *mut u8,
                part_23_length: self.granule[i].part_23_length,
                big_values: self.granule[i].big_values,
                scalefac_compress: self.granule[i].scalefac_compress,
                global_gain: self.granule[i].global_gain,
                block_type: self.granule[i].block_type,
                mixed_block_flag: self.granule[i].mixed_block_flag,
                n_long_sfb: self.granule[i].n_long_sfb,
                n_short_sfb: self.granule[i].n_short_sfb,
                table_select: self.granule[i].table_select,
                region_count: self.granule[i].region_count,
                subblock_gain: self.granule[i].subblock_gain,
                preflag: self.granule[i].preflag,
                scalefac_scale: self.granule[i].scalefac_scale,
                count1_table: self.granule[i].count1_table,
                scfsi: self.granule[i].scfsi,
            };
            ul[i].write(n);
        }

        let mut l: [L3_gr_info_t; 4] = unsafe { std::mem::transmute(ul) };

            /*
        let mut l = L3_gr_info_t {
            sfbtab: self.l_sfbtab.as_mut_ptr(),
            part_23_length: self.l_part_23_length,
            big_values: self.l_big_values,
            scalefac_compress: self.l_scalefac_compress,
            global_gain: self.l_global_gain,
            block_type: self.l_block_type,
            mixed_block_flag: self.l_mixed_block_flag,
            n_long_sfb: self.l_n_long_sfb,
            n_short_sfb: self.l_n_short_sfb,
            table_select: self.l_table_select,
            region_count: self.l_region_count,
            subblock_gain: self.l_subblock_gain,
            preflag: self.l_preflag,
            scalefac_scale: self.l_scalefac_scale,
            count1_table: self.l_count1_table,
            scfsi: self.l_scfsi
        };
            */
        self.returns = unsafe {
            (*SYMBOL)(
                &raw mut b as *mut bs_t,
                &raw mut l as *mut L3_gr_info_t,
                self.hdr.as_ptr()
            )
        };

        self.b_pos = b.pos;
        self.b_limit = b.limit;

        for i in 0..4 {
            self.granule[i].sfbtab = unsafe { std::ptr::read(l[i].sfbtab as *const [u8; 23]) };
            self.granule[i].part_23_length = l[i].part_23_length;
            self.granule[i].big_values = l[i].big_values;
            self.granule[i].scalefac_compress = l[i].scalefac_compress;
            self.granule[i].global_gain = l[i].global_gain;
            self.granule[i].block_type = l[i].block_type;
            self.granule[i].mixed_block_flag = l[i].mixed_block_flag;
            self.granule[i].n_long_sfb = l[i].n_long_sfb;
            self.granule[i].n_short_sfb = l[i].n_short_sfb;
            self.granule[i].table_select = l[i].table_select;
            self.granule[i].region_count = l[i].region_count;
            self.granule[i].subblock_gain = l[i].subblock_gain;
            self.granule[i].preflag = l[i].preflag;
            self.granule[i].scalefac_scale = l[i].scalefac_scale;
            self.granule[i].count1_table = l[i].count1_table;
            self.granule[i].scfsi = l[i].scfsi;
        }
    }
}
