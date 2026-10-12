// Exact live-heap high-water mark for a macOS process. Load with DYLD_INSERT_LIBRARIES; every
// allocation entry point is interposed, live bytes are the allocator's own block sizes
// (malloc_size) added on allocation and removed on free, and the maximum is kept. The hooks
// never allocate and take no locks.
#include <malloc/malloc.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>

#define INTERPOSE(replacement, original)                                           \
  __attribute__((used)) static struct {                                             \
    const void *replacement;                                                       \
    const void *original;                                                          \
  } interpose_##original __attribute__((section("__DATA,__interpose"))) = {       \
      (const void *)(uintptr_t)&replacement, (const void *)(uintptr_t)&original};

static _Atomic int64_t live_bytes;
static _Atomic int64_t peak_bytes;

static inline void grow(int64_t delta) {
  int64_t now = atomic_fetch_add_explicit(&live_bytes, delta, memory_order_relaxed) + delta;
  int64_t peak = atomic_load_explicit(&peak_bytes, memory_order_relaxed);
  while (now > peak &&
         !atomic_compare_exchange_weak_explicit(&peak_bytes, &peak, now, memory_order_relaxed,
                                                memory_order_relaxed)) {
  }
}

static inline void shrink(size_t bytes) {
  atomic_fetch_sub_explicit(&live_bytes, (int64_t)bytes, memory_order_relaxed);
}

static inline void gained(void *ptr) {
  if (ptr) grow((int64_t)malloc_size(ptr));
}

__attribute__((visibility("default"))) int64_t rss_live_peak(void) {
  return atomic_load(&peak_bytes);
}
__attribute__((visibility("default"))) int64_t rss_live_current(void) {
  return atomic_load(&live_bytes);
}
__attribute__((visibility("default"))) void rss_live_reset_peak(void) {
  atomic_store(&peak_bytes, atomic_load(&live_bytes));
}

static void *live_malloc(size_t size) {
  void *p = malloc(size);
  gained(p);
  return p;
}
INTERPOSE(live_malloc, malloc)

static void *live_calloc(size_t count, size_t size) {
  void *p = calloc(count, size);
  gained(p);
  return p;
}
INTERPOSE(live_calloc, calloc)

static void *live_realloc(void *ptr, size_t size) {
  size_t before = ptr ? malloc_size(ptr) : 0;
  void *p = realloc(ptr, size);
  if (p) grow((int64_t)malloc_size(p) - (int64_t)before);
  return p;
}
INTERPOSE(live_realloc, realloc)

static void *live_reallocf(void *ptr, size_t size) {
  size_t before = ptr ? malloc_size(ptr) : 0;
  void *p = reallocf(ptr, size);
  if (p)
    grow((int64_t)malloc_size(p) - (int64_t)before);
  else
    shrink(before);  // reallocf frees the old block on failure
  return p;
}
INTERPOSE(live_reallocf, reallocf)

static void live_free(void *ptr) {
  if (ptr) shrink(malloc_size(ptr));
  free(ptr);
}
INTERPOSE(live_free, free)

static int live_posix_memalign(void **ptr, size_t alignment, size_t size) {
  int result = posix_memalign(ptr, alignment, size);
  if (!result) gained(*ptr);
  return result;
}
INTERPOSE(live_posix_memalign, posix_memalign)

static void *live_aligned_alloc(size_t alignment, size_t size) {
  void *p = aligned_alloc(alignment, size);
  gained(p);
  return p;
}
INTERPOSE(live_aligned_alloc, aligned_alloc)

static void *live_valloc(size_t size) {
  void *p = valloc(size);
  gained(p);
  return p;
}
INTERPOSE(live_valloc, valloc)

static void *live_zone_malloc(malloc_zone_t *zone, size_t size) {
  void *p = malloc_zone_malloc(zone, size);
  gained(p);
  return p;
}
INTERPOSE(live_zone_malloc, malloc_zone_malloc)

static void *live_zone_calloc(malloc_zone_t *zone, size_t count, size_t size) {
  void *p = malloc_zone_calloc(zone, count, size);
  gained(p);
  return p;
}
INTERPOSE(live_zone_calloc, malloc_zone_calloc)

static void *live_zone_valloc(malloc_zone_t *zone, size_t size) {
  void *p = malloc_zone_valloc(zone, size);
  gained(p);
  return p;
}
INTERPOSE(live_zone_valloc, malloc_zone_valloc)

static void *live_zone_memalign(malloc_zone_t *zone, size_t alignment, size_t size) {
  void *p = malloc_zone_memalign(zone, alignment, size);
  gained(p);
  return p;
}
INTERPOSE(live_zone_memalign, malloc_zone_memalign)

static void *live_zone_realloc(malloc_zone_t *zone, void *ptr, size_t size) {
  size_t before = ptr ? malloc_size(ptr) : 0;
  void *p = malloc_zone_realloc(zone, ptr, size);
  if (p) grow((int64_t)malloc_size(p) - (int64_t)before);
  return p;
}
INTERPOSE(live_zone_realloc, malloc_zone_realloc)

static void live_zone_free(malloc_zone_t *zone, void *ptr) {
  if (ptr) shrink(malloc_size(ptr));
  malloc_zone_free(zone, ptr);
}
INTERPOSE(live_zone_free, malloc_zone_free)
