/* Base version: ids and names only. Tags (type 3) are the next increment. */
#include "parser.h"

#include <string.h>

static uint32_t read_u32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static int printable(const uint8_t *text, size_t length) {
    for (size_t i = 0; i < length; i++)
        if (text[i] < 0x20 || text[i] > 0x7e) return 0;
    return 1;
}

const char *parse_packet(const uint8_t *data, size_t size, struct packet *out) {
    memset(out, 0, sizeof *out);
    if (size < 4) return "header is truncated";
    if (data[0] != 'F' || data[1] != 'P') return "bad magic";
    if (data[2] != 1) return "unsupported version";
    size_t records = data[3];
    size_t offset = 4;
    for (size_t r = 0; r < records; r++) {
        if (size - offset < 3) return "record header is truncated";
        uint8_t type = data[offset];
        size_t length = ((size_t)data[offset + 1] << 8) | data[offset + 2];
        offset += 3;
        if (length > size - offset) return "record runs past the packet";
        const uint8_t *value = data + offset;
        switch (type) {
        case 1:
            if (length != 4) return "id record must be 4 bytes";
            out->id = read_u32(value);
            out->has_id = 1;
            break;
        case 2:
            if (length > MAX_NAME) return "name is too long";
            if (!printable(value, length)) return "name is not printable";
            memcpy(out->name, value, length);
            out->name[length] = '\0';
            out->has_name = 1;
            break;
        default:
            return "unknown record type";
        }
        offset += length;
    }
    if (offset != size) return "trailing bytes after the last record";
    return NULL;
}
