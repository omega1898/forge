/* Tags: every length is checked against the bytes that remain BEFORE it is used. */
#include "tags.h"

#include <string.h>

const char *parse_tags(const uint8_t *value, size_t length, struct packet *out) {
    if (length < 1) return "tags record is empty";
    size_t offset = 0;
    size_t count = value[offset++];
    if (count > MAX_TAGS) return "too many tags";
    for (size_t i = 0; i < count; i++) {
        if (offset >= length) return "tag length is missing";
        size_t tag_length = value[offset++];
        if (tag_length > MAX_TAG) return "tag is too long";
        if (tag_length > length - offset) return "tag runs past its record";
        for (size_t j = 0; j < tag_length; j++)
            if (value[offset + j] < 0x20 || value[offset + j] > 0x7e) return "tag is not printable";
        memcpy(out->tags[i], value + offset, tag_length);
        out->tags[i][tag_length] = '\0';
        offset += tag_length;
    }
    if (offset != length) return "tags record has trailing bytes";
    out->tag_count = (int)count;
    return NULL;
}
