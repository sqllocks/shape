#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define BIG (200ULL << 20)

static void touch(void *pointer) {
  volatile unsigned char *bytes = pointer;
  for (size_t i = 0; i < BIG; i += 4096) bytes[i] = 1;
}

int main(void) {
  void *pointer = malloc(BIG);
  if (!pointer) return 11;
  touch(pointer);
  free(pointer);

  pointer = calloc(BIG, 1);
  if (!pointer) return 12;
  touch(pointer);
  free(pointer);

  if (posix_memalign(&pointer, 64, BIG)) return 13;
  touch(pointer);
  free(pointer);

  puts("malloc, calloc and posix_memalign completed");
  return 0;
}
