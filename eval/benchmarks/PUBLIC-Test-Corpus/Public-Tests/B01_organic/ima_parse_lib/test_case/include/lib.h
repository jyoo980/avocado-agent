typedef unsigned int ima_u32_t;
typedef unsigned long long ima_u64_t;
typedef double ima_f64_t;

typedef unsigned char ima_u8_t;
typedef unsigned short ima_u16_t;

struct ima_block {
    ima_u16_t preamble;
    ima_u8_t data[(32)];
};

struct ima_info {
    const struct ima_block *blocks;
    ima_u64_t size;
    ima_f64_t sample_rate;
    ima_u64_t frame_count;
    ima_u32_t channel_count;
};

int ima_parse(struct ima_info *info, const void *data);
