#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <unordered_set>
#include <utility>
#include <vector>
#include "utils/percentile_stats.h"

namespace pipeann {

// At most one restart. A fresh search recovers candidates pruned at the pilot L.
// Candidate counts are a work proxy; the policy does not estimate/guarantee recall.
struct AdaptiveBudgetConfig {
  uint64_t max_l = 0;  // zero disables adaptation
  uint64_t candidates_per_k = 4;
  double skip_threshold = 0.85;
};

struct AdaptiveBudgetTrace {
  uint64_t initial_l = 0, final_l = 0, sum_l = 0;
  uint64_t pilot_exact = 0, final_exact = 0, exact_evaluations = 0;
  uint64_t attempts = 1;
  bool fallback = false;
};

inline uint64_t choose_fallback_l(uint64_t initial_l, uint64_t k,
                                 uint64_t returned, uint64_t exact,
                                 double skipped, const AdaptiveBudgetConfig &config) {
  if (initial_l == 0 || k == 0 || initial_l < k)
    throw std::invalid_argument("Require L >= K > 0");
  if (config.candidates_per_k == 0 || !std::isfinite(config.skip_threshold) ||
      config.skip_threshold < 0 || config.skip_threshold > 1 ||
      !std::isfinite(skipped) || skipped < 0)
    throw std::invalid_argument("Invalid adaptive budget parameters");
  if (config.max_l <= initial_l) return initial_l;
  const long double target = static_cast<long double>(k) * config.candidates_per_k;
  const double ratio = skipped / std::max(1.0, skipped + static_cast<double>(exact));
  const bool short_output = returned < k;
  const bool sparse_work = static_cast<long double>(exact) < target &&
                           ratio >= config.skip_threshold;
  if (!short_output && !sparse_work) return initial_l;

  // Geometric rounding avoids a separate run for every intermediate budget.
  const long double wanted = std::max(2.0L * initial_l,
      static_cast<long double>(initial_l) * target / std::max<uint64_t>(1, exact));
  uint64_t next = initial_l;
  while (next < config.max_l && static_cast<long double>(next) < wanted) {
    next = next > config.max_l / 2 ? config.max_l : next * 2;
  }
  return next;
}

inline void add_query_stats(QueryStats &a, const QueryStats &b) {
  a.total_us += b.total_us; a.n_4k += b.n_4k; a.n_8k += b.n_8k;
  a.n_12k += b.n_12k; a.n_ios += b.n_ios; a.read_size += b.read_size;
  a.io_us += b.io_us; a.io_us1 += b.io_us1; a.head_us += b.head_us;
  a.cpu_us += b.cpu_us; a.cpu_us1 += b.cpu_us1; a.cpu_us2 += b.cpu_us2;
  a.n_cmps_saved += b.n_cmps_saved; a.n_cmps += b.n_cmps;
  a.n_cache_hits += b.n_cache_hits; a.n_hops += b.n_hops;
  a.n_current_used += b.n_current_used; a.n_filter_skip += b.n_filter_skip;
  a.bd_io_us += b.bd_io_us; a.bd_tunnel_us += b.bd_tunnel_us;
  a.bd_process_us += b.bd_process_us;
}

// Search callback: size_t(L, ids, exact_distances, QueryStats*, uint64_t *exact_count).
// Both attempts use identical query/filter/index. Costs include both searches.
template<class Search>
AdaptiveBudgetTrace adaptive_budget_search(uint64_t initial_l, uint64_t k,
    uint32_t *output, QueryStats &stats, const AdaptiveBudgetConfig &config, Search search) {
  choose_fallback_l(initial_l, k, k, k, 0, config);  // validate before allocating/calling search
  std::vector<uint32_t> ids(k, std::numeric_limits<uint32_t>::max());
  std::vector<float> dists(k, std::numeric_limits<float>::infinity());
  std::vector<std::pair<float, uint32_t>> candidates;
  candidates.reserve(k * 2);
  stats = QueryStats{};
  AdaptiveBudgetTrace trace;
  trace.initial_l = trace.final_l = trace.sum_l = initial_l;

  auto attempt = [&](uint64_t l) -> std::pair<uint64_t, uint64_t> {
    QueryStats current{};
    uint64_t exact = 0;
    const uint64_t returned = search(l, ids.data(), dists.data(), &current, &exact);
    if (returned > k) throw std::runtime_error("Search returned more than K results");
    for (uint64_t j = 0; j < returned; ++j) {
      if (!std::isfinite(dists[j]) || ids[j] == std::numeric_limits<uint32_t>::max())
        throw std::runtime_error("Invalid exact-distance search result");
      candidates.emplace_back(dists[j], ids[j]);
    }
    add_query_stats(stats, current);
    trace.exact_evaluations += exact;  // includes repeated evaluations across attempts
    return {returned, exact};
  };

  auto pilot = attempt(initial_l);
  trace.pilot_exact = trace.final_exact = pilot.second;
  const uint64_t next = choose_fallback_l(initial_l, k, pilot.first, pilot.second,
                                         stats.n_filter_skip, config);
  if (next > initial_l) {
    trace.fallback = true;
    trace.attempts = 2;
    trace.final_l = next;
    trace.sum_l += next;
    auto final = attempt(next);
    trace.final_exact = final.second;
  }

  // Keep the best exact-distance top-K from both attempts; never append duplicates.
  std::sort(candidates.begin(), candidates.end());
  std::unordered_set<uint32_t> seen;
  std::fill(output, output + k, std::numeric_limits<uint32_t>::max());
  uint64_t count = 0;
  for (const auto &entry : candidates) {
    if (!seen.insert(entry.second).second) continue;
    output[count++] = entry.second;
    if (count == k) break;
  }
  return trace;
}
}  // namespace pipeann
