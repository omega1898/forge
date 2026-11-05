/* band VALUE LOW HIGH: prints IN when LOW <= VALUE <= HIGH, else OUT.
   Implements: BAND-1 (band check), BAND-2 (wrong argument count exits with code 2) */
#include <stdio.h>
#include <stdlib.h>

static int in_band(long value, long low, long high) {
    return value >= low && value <= high;
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fputs("usage: band VALUE LOW HIGH\n", stderr);
        return 2;
    }
    long value = strtol(argv[1], NULL, 10), low = strtol(argv[2], NULL, 10), high = strtol(argv[3], NULL, 10);
    puts(in_band(value, low, high) ? "IN" : "OUT");
    return 0;
}
