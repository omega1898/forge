#ifndef PACKET_TAGS_H
#define PACKET_TAGS_H

#include "parser.h"

/* value/length is one complete type-3 record body. */
const char *parse_tags(const uint8_t *value, size_t length, struct packet *out);

#endif
