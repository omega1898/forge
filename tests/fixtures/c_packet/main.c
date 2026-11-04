/* Reads one packet file and prints it as JSON, or "malformed: ..." with exit code 2. */
#include "parser.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PACKET 65536

static void print_json_string(const char *text) {
    putchar('"');
    for (const char *p = text; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        putchar(*p);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: parse PACKET_FILE\n");
        return 64;
    }
    FILE *file = fopen(argv[1], "rb");
    if (file == NULL) {
        fprintf(stderr, "cannot open the packet file\n");
        return 66;
    }
    static uint8_t staging[MAX_PACKET + 1];
    size_t size = fread(staging, 1, sizeof staging, file);
    fclose(file);
    if (size > MAX_PACKET) {
        fprintf(stderr, "malformed: packet is too large\n");
        return 2;
    }
    /* An exactly sized heap copy, so any read past the end is a detectable error. */
    uint8_t *data = malloc(size ? size : 1);
    if (data == NULL) return 70;
    memcpy(data, staging, size);
    struct packet packet;
    const char *problem = parse_packet(data, size, &packet);
    free(data);
    if (problem != NULL) {
        fprintf(stderr, "malformed: %s\n", problem);
        return 2;
    }
    printf("{\"id\":");
    if (packet.has_id) printf("%lu", (unsigned long)packet.id); else printf("null");
    printf(",\"name\":");
    if (packet.has_name) print_json_string(packet.name); else printf("null");
    printf(",\"tags\":[");
    for (int i = 0; i < packet.tag_count; i++) {
        if (i) putchar(',');
        print_json_string(packet.tags[i]);
    }
    printf("]}\n");
    return 0;
}
