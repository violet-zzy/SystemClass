#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
course_root="${1:-$HOME/fvs-course-lab}"
opt_repo="$course_root/systems/GateANN-adaptive"

if [[ ! -e "$opt_repo" ]]; then
  python3 "$bundle_dir/prepare.py" "$course_root"
fi
python3 "$bundle_dir/verify_install.py" "$course_root"
python3 "$bundle_dir/test_tools.py"
cmake -S "$opt_repo" -B "$opt_repo/build" -DCMAKE_BUILD_TYPE=Release -DUSE_AIO=ON
cmake --build "$opt_repo/build" --target search_disk_index_yfcc -j "${FVS_BUILD_JOBS:-8}"
c++ -std=c++17 -O2 -Wall -Wextra -I "$opt_repo/include" \
  "$opt_repo/tests/adaptive_budget_test.cpp" -o "$opt_repo/build/adaptive_budget_test"
"$opt_repo/build/adaptive_budget_test"

# Any build, unit test, or debug regression failure stops before the formal run.
python3 "$bundle_dir/run_experiments.py" --course-root "$course_root" --smoke
python3 "$bundle_dir/run_experiments.py" --course-root "$course_root"
