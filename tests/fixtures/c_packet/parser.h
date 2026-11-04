/* Packet format "FP" version 1.
 *
 *   'F' 'P' version(1) record_count(1)
 *   record: type(1) length(2, big-endian) value(length)
 *     type 1: id, exactly 4 bytes, big-endian unsigned
 *     type 2: name, 0..64 printable ASCII bytes
 *     type 3: tags, count(1) then count x [length(1) bytes], 0..8 tags of 0..32 printable bytes
 *
 * Anything else is malformed.
 */
#ifndef PACKET_PARSER_H
#define PACKET_PARSER_H

#include <stddef.h>
#include <stdint.h>

#define MAX_NAME 64
#define MAX_TAGS 8
#define MAX_TAG 32

struct packet {
    uint32_t id;
    int has_id;
    char name[MAX_NAME + 1];
    int has_name;
    char tags[MAX_TAGS][MAX_TAG + 1];
    int tag_count;
};

/* Returns NULL when the packet is valid, otherwise a fixed message naming the problem. */
const char *parse_packet(const uint8_t *data, size_t size, struct packet *out);

#endif
