// Scratch-only macOS malloc tracer. Loaded into the bounded-profile child with DYLD_INSERT_LIBRARIES.
// Records allocations of at least 4 MiB, their frees, and the current live total.
#include <dlfcn.h>
#include <fcntl.h>
#include <malloc/malloc.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define INTERPOSE(replacement, original)                                           \
  __attribute__((used)) static struct {                                             \
    const void *replacement;                                                       \
    const void *original;                                                          \
  } interpose_##original __attribute__((section("__DATA,__interpose"))) = {       \
      (const void *)(uintptr_t)&replacement, (const void *)(uintptr_t)&original};

#define TRACE_MIN (4ULL << 20)
#define SLOT_COUNT 32768

struct slot {
  void *pointer;
  size_t size;
};
static struct slot slots[SLOT_COUNT];
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static _Thread_local int inside;
static size_t live_bytes;
static size_t peak_bytes;
static int trace_fd = -2;

static uint64_t timestamp_ns(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static int output_fd(void) {
  if (trace_fd == -2) {
    const char *path = getenv("RSS_TRACE_FILE");
    trace_fd = path ? open(path, O_CREAT | O_WRONLY | O_APPEND, 0600) : -1;
  }
  return trace_fd;
}

static size_t slot_index(void *ptr) {
  size_t index = ((uintptr_t)ptr >> 4) % SLOT_COUNT;
  for (size_t n = 0; n < SLOT_COUNT; n++) {
    if (slots[index].pointer == ptr || slots[index].pointer == NULL) return index;
    index = (index + 1) % SLOT_COUNT;
  }
  return SLOT_COUNT;
}

static void trace_event(char kind, void *ptr, size_t requested, size_t actual,
                        void *caller) {
  int fd = output_fd();
  if (fd < 0) return;
  Dl_info info = {0};
  dladdr(caller, &info);
  char line[512];
  int count = snprintf(line, sizeof(line),
                       "%c,%llu,%d,%p,%zu,%zu,%zu,%zu,%s,%s,%p\n", kind,
                       (unsigned long long)timestamp_ns(), getpid(), ptr,
                       requested, actual, live_bytes, peak_bytes,
                       info.dli_fname ? info.dli_fname : "?",
                       info.dli_sname ? info.dli_sname : "?", caller);
  if (count > 0) write(fd, line, (size_t)count < sizeof(line) ? (size_t)count : sizeof(line) - 1);
}

static void remember(void *ptr, size_t requested, void *caller) {
  if (!ptr || requested < TRACE_MIN || inside) return;
  inside = 1;
  pthread_mutex_lock(&lock);
  size_t index = slot_index(ptr);
  if (index < SLOT_COUNT) {
    if (slots[index].pointer == ptr) live_bytes -= slots[index].size;
    slots[index].pointer = ptr;
    slots[index].size = requested;
    live_bytes += requested;
    if (live_bytes > peak_bytes) peak_bytes = live_bytes;
    trace_event('A', ptr, requested, malloc_size(ptr), caller);
  }
  pthread_mutex_unlock(&lock);
  inside = 0;
}

static void forget(void *ptr, void *caller) {
  if (!ptr || inside) return;
  inside = 1;
  pthread_mutex_lock(&lock);
  size_t index = slot_index(ptr);
  if (index < SLOT_COUNT && slots[index].pointer == ptr) {
    size_t size = slots[index].size;
    slots[index].pointer = (void *)1; // tombstone; preserve the probe chain
    slots[index].size = 0;
    live_bytes -= size;
    trace_event('F', ptr, size, 0, caller);
  }
  pthread_mutex_unlock(&lock);
  inside = 0;
}

static void *traced_malloc(size_t size) {
  void *p = malloc(size);
  remember(p, size, __builtin_return_address(0));
  return p;
}
INTERPOSE(traced_malloc, malloc)

static void *traced_calloc(size_t count, size_t size) {
  void *p = calloc(count, size);
  if (size && count <= SIZE_MAX / size) remember(p, count * size, __builtin_return_address(0));
  return p;
}
INTERPOSE(traced_calloc, calloc)

static void *traced_realloc(void *ptr, size_t size) {
  void *p = realloc(ptr, size);
  if (p) {
    forget(ptr, __builtin_return_address(0));
    remember(p, size, __builtin_return_address(0));
  }
  return p;
}
INTERPOSE(traced_realloc, realloc)

static void traced_free(void *ptr) {
  forget(ptr, __builtin_return_address(0));
  free(ptr);
}
INTERPOSE(traced_free, free)

static int traced_posix_memalign(void **ptr, size_t alignment, size_t size) {
  int result = posix_memalign(ptr, alignment, size);
  if (!result) remember(*ptr, size, __builtin_return_address(0));
  return result;
}
INTERPOSE(traced_posix_memalign, posix_memalign)

static void *traced_aligned_alloc(size_t alignment, size_t size) {
  void *p = aligned_alloc(alignment, size);
  remember(p, size, __builtin_return_address(0));
  return p;
}
INTERPOSE(traced_aligned_alloc, aligned_alloc)

static void *traced_zone_malloc(malloc_zone_t *zone, size_t size) {
  void *p = malloc_zone_malloc(zone, size);
  remember(p, size, __builtin_return_address(0));
  return p;
}
INTERPOSE(traced_zone_malloc, malloc_zone_malloc)

static void *traced_zone_memalign(malloc_zone_t *zone, size_t alignment, size_t size) {
  void *p = malloc_zone_memalign(zone, alignment, size);
  remember(p, size, __builtin_return_address(0));
  return p;
}
INTERPOSE(traced_zone_memalign, malloc_zone_memalign)

static void *traced_zone_realloc(malloc_zone_t *zone, void *ptr, size_t size) {
  void *p = malloc_zone_realloc(zone, ptr, size);
  if (p) {
    forget(ptr, __builtin_return_address(0));
    remember(p, size, __builtin_return_address(0));
  }
  return p;
}
INTERPOSE(traced_zone_realloc, malloc_zone_realloc)

static void traced_zone_free(malloc_zone_t *zone, void *ptr) {
  forget(ptr, __builtin_return_address(0));
  malloc_zone_free(zone, ptr);
}
INTERPOSE(traced_zone_free, malloc_zone_free)
