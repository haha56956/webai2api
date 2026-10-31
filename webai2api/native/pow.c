#include <stdint.h>
#include <stddef.h>
#include <string.h>

static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808AULL, 0x8000000080008000ULL,
    0x000000000000808BULL, 0x0000000080000001ULL, 0x8000000080008081ULL, 0x8000000000008009ULL,
    0x000000000000008AULL, 0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000AULL,
    0x000000008000808BULL, 0x800000000000008BULL, 0x8000000000008089ULL, 0x8000000000008003ULL,
    0x8000000000008002ULL, 0x8000000000000080ULL, 0x000000000000800AULL, 0x800000008000000AULL,
    0x8000000080008081ULL, 0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL,
};

static const int PI[25] = {
    0, 6, 12, 18, 24, 3, 9, 10, 16, 22, 1, 7, 13, 19, 20, 4, 5, 11, 17, 23, 2, 8, 14, 15, 21,
};

static const int RHO[25] = {
    0, 44, 43, 21, 14, 28, 20, 3, 45, 61, 1, 6, 25, 8, 18, 27, 36, 10, 15, 56, 62, 55, 39, 41, 2,
};

static inline uint64_t rotl64(uint64_t v, unsigned int n) {
    return (v << n) | (v >> (64 - n));
}

static inline uint64_t load64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--) {
        v = (v << 8) | p[i];
    }
    return v;
}

static inline void store64(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; i++) {
        p[i] = (uint8_t)(v & 0xff);
        v >>= 8;
    }
}

static void keccak_f23(uint64_t st[25]) {
    for (int r = 1; r < 24; r++) {
        uint64_t bc[5];
        for (int i = 0; i < 5; i++) {
            bc[i] = st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20];
        }
        for (int i = 0; i < 5; i++) {
            uint64_t d = bc[(i + 4) % 5] ^ rotl64(bc[(i + 1) % 5], 1);
            for (int j = 0; j < 25; j += 5) {
                st[j + i] ^= d;
            }
        }
        uint64_t b[25];
        for (int i = 0; i < 25; i++) {
            b[i] = rotl64(st[PI[i]], (unsigned int)RHO[i]);
        }
        for (int j = 0; j < 5; j++) {
            for (int i = 0; i < 5; i++) {
                st[j * 5 + i] = b[j * 5 + i] ^ ((~b[j * 5 + (i + 1) % 5]) & b[j * 5 + (i + 2) % 5]);
            }
        }
        st[0] ^= RC[r];
    }
}

static void absorb_block(uint64_t st[25], const uint8_t block[136]) {
    for (int i = 0; i < 17; i++) {
        st[i] ^= load64(block + i * 8);
    }
    keccak_f23(st);
}

static void dspow_hash(const uint8_t *data, size_t len, uint8_t out[32]) {
    uint64_t st[25];
    memset(st, 0, sizeof(st));
    while (len >= 136) {
        absorb_block(st, data);
        data += 136;
        len -= 136;
    }
    uint8_t block[136];
    memset(block, 0, sizeof(block));
    if (len > 0) {
        memcpy(block, data, len);
    }
    block[len] ^= 0x06;
    block[135] ^= 0x80;
    absorb_block(st, block);
    for (int i = 0; i < 4; i++) {
        store64(out + i * 8, st[i]);
    }
}

static int u64_to_decimal(uint64_t value, char *buf) {
    char tmp[20];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + (value % 10));
        value /= 10;
    } while (value != 0);
    for (int i = 0; i < n; i++) {
        buf[i] = tmp[n - 1 - i];
    }
    return n;
}

int dspow_solve(
    const char *prefix,
    uint64_t prefix_len,
    const uint8_t target[32],
    uint64_t max_nonce,
    uint64_t *out_nonce
) {
    if (prefix == NULL || target == NULL || out_nonce == NULL) {
        return -1;
    }
    if (prefix_len > 115) {
        return -2;
    }
    uint8_t block[136];
    memset(block, 0, sizeof(block));
    memcpy(block, prefix, (size_t)prefix_len);
    for (uint64_t nonce = 0; nonce < max_nonce; nonce++) {
        char digits[20];
        int dlen = u64_to_decimal(nonce, digits);
        memcpy(block + prefix_len, digits, (size_t)dlen);
        size_t msg_len = (size_t)prefix_len + (size_t)dlen;
        block[msg_len] = 0x06;
        block[135] ^= 0x80;
        uint64_t st[25];
        memset(st, 0, sizeof(st));
        absorb_block(st, block);
        uint8_t hash[32];
        for (int i = 0; i < 4; i++) {
            store64(hash + i * 8, st[i]);
        }
        block[msg_len] = 0;
        block[135] = 0;
        if (memcmp(hash, target, 32) == 0) {
            *out_nonce = nonce;
            return 1;
        }
    }
    return 0;
}

int dspow_hash_hex(const char *data, uint64_t len, uint8_t out[64]) {
    if (data == NULL || out == NULL) {
        return -1;
    }
    uint8_t digest[32];
    dspow_hash((const uint8_t *)data, (size_t)len, digest);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = (uint8_t)hex[digest[i] >> 4];
        out[i * 2 + 1] = (uint8_t)hex[digest[i] & 0x0f];
    }
    return 0;
}
