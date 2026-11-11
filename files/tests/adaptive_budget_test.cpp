#include <cassert>
#include <iostream>
#include "utils/adaptive_budget.h"

int main() {
  using namespace pipeann;
  AdaptiveBudgetConfig c; c.max_l = 1280;
  assert(choose_fallback_l(320, 10, 10, 47, 280, c) == 320);
  assert(choose_fallback_l(320, 10, 10, 16, 309, c) == 1280);
  assert(choose_fallback_l(320, 10, 4, 4, 320, c) == 1280);
  assert(choose_fallback_l(320, 10, 10, 30, 300, c) == 640);
  assert(choose_fallback_l(320, 10, 10, 16, 1, c) == 320);
  c.max_l = 320;
  assert(choose_fallback_l(320, 10, 0, 0, 0, c) == 320);
  c.max_l = 1000;
  assert(choose_fallback_l(320, 10, 0, 0, 300, c) == 1000);
  for (uint64_t exact = 0; exact < 100; ++exact) {
    for (uint64_t maximum : {321ULL, 640ULL, 1000ULL, 1280ULL, 5120ULL}) {
      c.max_l = maximum;
      auto l = choose_fallback_l(320, 10, std::min<uint64_t>(exact, 10), exact, 320, c);
      assert(l >= 320 && l <= maximum);
    }
  }
  c.max_l = 1280;
  c.candidates_per_k = 4;
  uint32_t output[3]; QueryStats stats;
  unsigned calls = 0;
  auto trace = adaptive_budget_search(320, 3, output, stats, c,
      [&](uint64_t l, uint32_t *ids, float *d, QueryStats *s, uint64_t *exact) -> size_t {
        ++calls; s->n_ios = 5; s->n_filter_skip = 100; s->bd_io_us = 7;
        if (l == 320) { ids[0]=10; d[0]=1; ids[1]=20; d[1]=3; *exact=2; return 2; }
        ids[0]=10; d[0]=1; ids[1]=30; d[1]=2; ids[2]=40; d[2]=4;
        *exact=6; return 3;
      });
  assert(calls == 2 && trace.fallback && trace.attempts == 2);
  assert(trace.sum_l == 1600 && trace.final_l == 1280);
  assert(trace.exact_evaluations == 8 && trace.pilot_exact == 2);
  assert(output[0] == 10 && output[1] == 30 && output[2] == 20);
  assert(stats.n_ios == 10 && stats.n_filter_skip == 200 && stats.bd_io_us == 14);
  calls = 0;
  c.max_l = 320;
  trace = adaptive_budget_search(320, 3, output, stats, c,
      [&](uint64_t, uint32_t *ids, float *d, QueryStats*, uint64_t *exact) -> size_t {
        ++calls; ids[0]=5; d[0]=1; *exact=1; return 1;
      });
  assert(calls == 1 && !trace.fallback && trace.sum_l == 320);
  assert(output[0] == 5 && output[1] == UINT32_MAX && output[2] == UINT32_MAX);
  bool rejected = false;
  c.skip_threshold = std::numeric_limits<double>::quiet_NaN();
  try { choose_fallback_l(320, 10, 10, 10, 300, c); }
  catch (const std::invalid_argument&) { rejected = true; }
  assert(rejected);
  std::cout << "PASS adaptive policy: 500 bounded cases, restart, merge, stats, sentinels, invalid inputs\n";
}
