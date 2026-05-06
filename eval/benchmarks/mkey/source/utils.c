#include "utils.h"

u32 align(u32 offset, u32 alignment)
__CPROVER_requires(alignment > 0)
__CPROVER_requires((alignment & (alignment - 1)) == 0)
__CPROVER_requires(offset <= 0xFFFFFFFF - (alignment - 1))
__CPROVER_ensures(__CPROVER_return_value >= offset)
__CPROVER_ensures((__CPROVER_return_value & (alignment - 1)) == 0)
{
    u32 mask = ~(alignment-1);

    return (offset + (alignment-1)) & mask;
}

u64 align64(u64 offset, u32 alignment)
__CPROVER_requires(alignment > 0)
__CPROVER_requires((alignment & (alignment - 1)) == 0)
__CPROVER_ensures((__CPROVER_return_value & (u64)(alignment - 1)) == 0)
{
    u64 mask = ~(alignment-1);

    return (offset + (alignment-1)) & mask;
}

u64 getle64(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 8 * sizeof(u8)))
{
    u64 n = p[0];

    n |= (u64)p[1]<<8;
    n |= (u64)p[2]<<16;
    n |= (u64)p[3]<<24;
    n |= (u64)p[4]<<32;
    n |= (u64)p[5]<<40;
    n |= (u64)p[6]<<48;
    n |= (u64)p[7]<<56;
    return n;
}

u64 getbe64(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 8 * sizeof(u8)))
{
    u64 n = 0;

    n |= (u64)p[0]<<56;
    n |= (u64)p[1]<<48;
    n |= (u64)p[2]<<40;
    n |= (u64)p[3]<<32;
    n |= (u64)p[4]<<24;
    n |= (u64)p[5]<<16;
    n |= (u64)p[6]<<8;
    n |= (u64)p[7]<<0;
    return n;
}

u32 getle32(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 4 * sizeof(u8)))
{
    return (p[0]<<0) | (p[1]<<8) | (p[2]<<16) | (p[3]<<24);
}

u32 getbe32(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 4 * sizeof(u8)))
{
    return (p[0]<<24) | (p[1]<<16) | (p[2]<<8) | (p[3]<<0);
}

u32 getle16(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 2 * sizeof(u8)))
{
    return (p[0]<<0) | (p[1]<<8);
}

u32 getbe16(const u8* p)
__CPROVER_requires(__CPROVER_is_fresh(p, 2 * sizeof(u8)))
{
    return (p[0]<<8) | (p[1]<<0);
}

void putle16(u8* p, u16 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 2 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 2 * sizeof(u8)))
__CPROVER_ensures(p[0] == (u8) n)
__CPROVER_ensures(p[1] == (u8) (n >> 8))
{
    p[0] = (u8) n;
    p[1] = (u8) (n>>8);
}

void putle32(u8* p, u32 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 4 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 4 * sizeof(u8)))
__CPROVER_ensures(p[0] == (u8) n)
__CPROVER_ensures(p[1] == (u8) (n >> 8))
__CPROVER_ensures(p[2] == (u8) (n >> 16))
__CPROVER_ensures(p[3] == (u8) (n >> 24))
{
    p[0] = (u8) n;
    p[1] = (u8) (n>>8);
    p[2] = (u8) (n>>16);
    p[3] = (u8) (n>>24);
}

void putle64(u8* p, u64 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 8 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 8 * sizeof(u8)))
__CPROVER_ensures(p[0] == (u8) n)
__CPROVER_ensures(p[7] == (u8) (n >> 56))
{
    p[0] = (u8) n;
    p[1] = (u8) (n >> 8);
    p[2] = (u8) (n >> 16);
    p[3] = (u8) (n >> 24);
    p[4] = (u8) (n >> 32);
    p[5] = (u8) (n >> 40);
    p[6] = (u8) (n >> 48);
    p[7] = (u8) (n >> 56);
}

void putbe16(u8* p, u16 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 2 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 2 * sizeof(u8)))
__CPROVER_ensures(p[1] == (u8) n)
__CPROVER_ensures(p[0] == (u8) (n >> 8))
{
    p[1] = (u8) n;
    p[0] = (u8) (n >> 8);
}

void putbe32(u8* p, u32 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 4 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 4 * sizeof(u8)))
__CPROVER_ensures(p[3] == (u8) n)
__CPROVER_ensures(p[0] == (u8) (n >> 24))
{
    p[3] = (u8) n;
    p[2] = (u8) (n >> 8);
    p[1] = (u8) (n >> 16);
    p[0] = (u8) (n >> 24);
}

void putbe64(u8* p, u64 n)
__CPROVER_requires(__CPROVER_is_fresh(p, 8 * sizeof(u8)))
__CPROVER_assigns(__CPROVER_object_upto(p, 8 * sizeof(u8)))
__CPROVER_ensures(p[7] == (u8) n)
__CPROVER_ensures(p[0] == (u8) (n >> 56))
{
    p[7] = (u8) n;
    p[6] = (u8) (n >> 8);
    p[5] = (u8) (n >> 16);
    p[4] = (u8) (n >> 24);
    p[3] = (u8) (n >> 32);
    p[2] = (u8) (n >> 40);
    p[1] = (u8) (n >> 48);
    p[0] = (u8) (n >> 56);
}

u32 swap_uint32(u32 val)
__CPROVER_ensures(__CPROVER_return_value ==
    (((__CPROVER_old(val) & 0x000000FFu) << 24) |
     ((__CPROVER_old(val) & 0x0000FF00u) << 8)  |
     ((__CPROVER_old(val) & 0x00FF0000u) >> 8)  |
     ((__CPROVER_old(val) & 0xFF000000u) >> 24)))
{
    val = ((val << 8) & 0xFF00FF00) | ((val >> 8) & 0xFF00FF);
    return (val << 16) | (val >> 16);
}

void reverse_endian(u32* buffer, size_t size)
__CPROVER_requires(size <= 16 * sizeof(u32))
__CPROVER_requires(__CPROVER_is_fresh(buffer, 16 * sizeof(u32)))
__CPROVER_assigns(__CPROVER_object_whole(buffer))
{
    u32 i = 0;
    if(size % sizeof(u32)) return;

    u32 words = size / sizeof(u32);

    for(i = 0; i < words; i++)
        buffer[i] = swap_uint32(buffer[i]);
}

void reverse_words(u32* buffer, size_t size)
__CPROVER_requires(size <= 16 * sizeof(u32))
__CPROVER_requires(__CPROVER_is_fresh(buffer, 16 * sizeof(u32)))
__CPROVER_assigns(__CPROVER_object_whole(buffer))
{
    u32 i = 0;
    if(size % sizeof(u32)) return;

    u32 words = size / sizeof(u32);

    u32 temp = 0;

    for (i = 0; i < words; i++)
    {
        temp = buffer[i];
        buffer[i] = buffer[words - 1];
        buffer[words - 1] = temp;
        words--;
    }
}

void reverse(u32* buffer, size_t size)
__CPROVER_requires(size <= 16 * sizeof(u32))
__CPROVER_requires(__CPROVER_is_fresh(buffer, 16 * sizeof(u32)))
__CPROVER_assigns(__CPROVER_object_whole(buffer))
{
    reverse_words(buffer, size);
    reverse_endian(buffer, size);
}

void hexdump(void *ptr, int buflen)
__CPROVER_requires(buflen >= 0 && buflen <= 64)
__CPROVER_requires(__CPROVER_is_fresh(ptr, (size_t)buflen))
{
    u8 *buf = (u8*)ptr;
    int i, j;

    for (i=0; i<buflen; i+=16)
    {
        printf("%06x: ", i);
        for (j=0; j<16; j++)
        {
            if (i+j < buflen)
            {
                printf("%02x ", buf[i+j]);
            }
            else
            {
                printf("   ");
            }
        }

        printf(" ");

        for (j=0; j<16; j++)
        {
            if (i+j < buflen)
            {
                printf("%c", (buf[i+j] >= 0x20 && buf[i+j] <= 0x7e) ? buf[i+j] : '.');
            }
        }
        printf("\n");
    }
}

void memdump(FILE* fout, const char* prefix, const u8* data, u32 size)
__CPROVER_requires(size <= 64)
__CPROVER_requires(__CPROVER_is_fresh(fout, sizeof(FILE)))
__CPROVER_requires(__CPROVER_is_fresh(prefix, 8))
__CPROVER_requires(prefix[0] == '\0' || prefix[1] == '\0' || prefix[2] == '\0' || prefix[3] == '\0' || prefix[4] == '\0' || prefix[5] == '\0' || prefix[6] == '\0' || prefix[7] == '\0')
__CPROVER_requires(__CPROVER_is_fresh(data, (size_t)size))
{
    u32 i;
    u32 prefixlen = strlen(prefix);
    u32 offs = 0;
    u32 line = 0;
    while(size)
    {
        u32 max = 32;

        if (max > size)
            max = size;

        if (line==0)
            fprintf(fout, "%s", prefix);
        else
            fprintf(fout, "%*s", prefixlen, "");


        for(i=0; i<max; i++)
            fprintf(fout, "%02X", data[offs+i]);
        fprintf(fout, "\n");
        line++;
        size -= max;
        offs += max;
    }
}

int makedir(const char* dir)
__CPROVER_requires(__CPROVER_is_fresh(dir, 8))
__CPROVER_requires(dir[0] == '\0' || dir[1] == '\0' || dir[2] == '\0' || dir[3] == '\0' || dir[4] == '\0' || dir[5] == '\0' || dir[6] == '\0' || dir[7] == '\0')
{
#ifdef _WIN32
    return _mkdir(dir);
#else
    return mkdir(dir, 0777);
#endif
}

bool isnumeric(const char* string)
__CPROVER_requires(__CPROVER_is_fresh(string, 8))
__CPROVER_requires(string[0] == '\0' || string[1] == '\0' || string[2] == '\0' || string[3] == '\0' || string[4] == '\0' || string[5] == '\0' || string[6] == '\0' || string[7] == '\0')
{
    while(*string)
    {
        if(!isdigit(*string))
            return false;
        string++;
    }
    return true;
}
