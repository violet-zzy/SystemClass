# GateANN 受限搜索预算回退

作者：仲昭阳；学号：M202674325。

## 当前状态

基础版本固定在 `71e87cbf0e25c0400c0109dd25a64a7c4dccc9dd`。本人已完成预算回退代码和实验脚本，Windows 主机上的策略与结果解析检查通过。完整 Linux 构建、真实索引上的回退质量与性能测试仍待执行；目前没有优化收益数据。`evidence/` 中保存的是修改前的 27 行预算扫描结果。

## 观察与实现

预算扫描中，Low 的 Recall@10 从 L=320 的 34.30% 上升到 L=1280 的 70.05%，三轮数值一致。对应 QPS 中位数从 1195 下降到 477，逻辑 I/O 从 4.38 上升到 13.95。这说明增加探索预算可以改善所测负载的质量，同时增加查询开销。

本实现先执行一次初始 L 的搜索。满足以下条件之一时，最多追加一次较大预算搜索：

- 返回结果不足 K。
- 精确距离候选条目数少于 `candidates_per_k * K`，且 `skips / (skips + exact_candidates)` 达到阈值。

默认初始 L=320，上限 1280，候选目标为 4K，过滤跳过比例阈值为 0.85。按照初次搜索的候选产出估算所需 L，再按初始 L 的倍增档位向上取整并限制在上限内。该规则只使用当前查询的运行统计，不读取负载桶名称、Ground Truth 或已知 Recall。候选条目数是工作量代理，并不保证达到某个 Recall。上述参数是本次探索性评测的起点；在同一负载上调参后得到的结果仍需独立负载验证。

小预算阶段已经丢弃的候选无法仅靠增大变量恢复，因此这版采用重新搜索。结果按精确距离合并去重，保留两次搜索的 top-K；没有重新计算完整数据集。IO、FilterSkips 及分阶段统计累加两次搜索；查询计时覆盖初次搜索、追加搜索和合并。最多两次搜索，搜索参数之和最多为 initial_L + max_L；这个和表示两次池宽参数之和，不能等同于访问节点数。实际精确距离条目数单独记录，跨尝试重复计算也计入。

这版实现会产生重复工作，可能比直接固定大 L 更慢。是否有收益，要在相近 Recall 下比较总时延、I/O 和 RSS。若结果不理想，应如实报告，进一步考虑保留被淘汰候选的单次搜索设计。

## 修改文件

| 文件 | 改动 |
|---|---|
| `include/utils/adaptive_budget.h` | 回退条件、预算选择、两次结果合并、累计统计 |
| `include/ssd_index.h` | 为 pipe_search 增加可选候选数输出指针，默认空指针 |
| `src/search/pipe_search.cpp` | 输出最终精确距离候选条目数 |
| `tests/search_disk_index_yfcc.cpp` | 默认关闭的环境变量开关、完整计时、逐查询结果及统计输出 |
| `tests/adaptive_budget_test.cpp` | 预算边界、结果去重、统计累计和异常输入检查 |

测试程序还将未填满的输出槽初始化为 UINT32_MAX。原程序用 0 初始化，在返回不足 K 条时会把空槽当成节点 0；新版本显式标记缺失结果，逐查询验证会报告缺失槽数量。实际搜索循环和距离计算不受此修正影响；关闭回退路径的 Recall 仍会与原程序对照。

不开启环境变量时继续调用原有搜索路径。课程 AIO 补丁不在本人优化补丁内，独立源码副本会保留它。索引布局、过滤谓词、距离函数、K、beamwidth 和 FullAdj neighbors 保持原设置。

## 执行方法

在 WSL/Linux 执行，默认课程目录为 `~/fvs-course-lab`：

```bash
bash run_all.sh "$HOME/fvs-course-lab"
```

脚本将原源码复制为 `systems/GateANN-adaptive`，不修改 `systems/GateANN-public` 或它的已有构建。复制前检查固定提交及源文件 SHA256；若源码与交付包基线不一致会停止。随后编译、执行独立 C++ 检查、运行 Debug 冒烟与正式对照。复制目录跳过 `.git`、构建目录和日志，保留已有 AIO 兼容修改。不会重建或覆盖索引。

要求已有基线二进制、Debug/Formal 索引和数据，以及 CMake、C++、AIO/OpenMP/BLAS 等原课程构建依赖。采集峰值 RSS 还需要 `/usr/bin/time`，缺少时安装 `sudo apt install time`。

正式测试将运行 45 个独立子进程：3 桶 × 5 配置 × 3 次。配置是原版固定 L=320/640/1280、修改版关闭回退 L=320、修改版开启回退初始 L=320。配置顺序用固定种子交错随机化，操作系统页缓存不清空，FullAdj 仍采用原有 MAP_POPULATE；这属于该缓存协议下的比较。若另测冷缓存，需要单独设计并记录流程。

输出到 `course_runs/optimization/adaptive-日期时间/`：

- `measurements.csv`：真实时间、Recall、I/O、每个进程的峰值 RSS、回退比例及预算统计。
- `summary.json`：三轮中位数汇总。
- `environment.json`、`commands.json`：环境、参数、源码摘要和实际执行顺序。
- 每次运行的 `run.log`、`memory.txt`；修改版另有 `queries.csv`，保存 ID、时延和预算。

逐查询结果会检查重复 ID、ID 范围、多标签子集谓词，并根据原 Ground Truth 重算 Recall。该验证器针对课程的 identity tags，遇到显式 tags 映射文件会停止。修改版关闭回退的 Recall 与同轮原版 L=320 对照。默认运行线程=1、beamwidth=8、K=10、AIO、FullAdj=32。

`mean_latency_us` 按基准程序输出 QPS 的倒数计算，QPS 本身经过打印舍入。逐查询 median/p99 使用另存的计时值；p99 为 nearest-rank，不将其称为 p99.9。修改版查询 CSV 的文件写入发生在批次计时之后，完整进程 RSS 包含输出缓冲。

关闭回退时，逐查询时间沿用原搜索函数的计时边界；开启回退时包围完整回退包装器。主要端到端比较采用统一外层批次计时产生的 QPS；逐查询统计用于补充分析。

## 单独运行或改变上限

```bash
python3 run_experiments.py --course-root "$HOME/fvs-course-lab" --smoke
python3 run_experiments.py --course-root "$HOME/fvs-course-lab"
# 新的一轮对照，不覆盖上次结果：
python3 run_experiments.py --course-root "$HOME/fvs-course-lab" --max-l 5120
```

使用其他 max L 时，默认固定预算对照仍为 320/640/1280；若要与固定 L=5120 比较，应扩充对应配置再运行。不要把不同 Recall 的最快运行点直接作为优化加速比。

课程适配器接口未修改，单独使用修改版时可通过环境变量开启：

```bash
GATEANN_ADAPTIVE_MAX_L=1280 \
GATEANN_ADAPTIVE_CANDIDATES_PER_K=4 \
GATEANN_ADAPTIVE_SKIP_THRESHOLD=0.85 \
python3 adapters/gateann_adapter.py search \
  --repo systems/GateANN-adaptive --dataset-root datasets/yfcc_course_v1 \
  --tier formal --run-root course_runs --threads 1 --beamwidth 8 --k 10 \
  --full-adj-neighbors 32 --bucket low --L 320 --run-id adaptive-manual-01
```

独立源码副本没有 `.git`，课程适配器的 commit 记录可能为空；本包的专用采集脚本保存基线提交与修改文件 SHA256。正式优化比较优先使用专用脚本。环境变量仅对该条命令生效。

## GitHub 提交内容

可以将本交付包直接放入个人仓库：README、补丁、`files/` 修改源码、安装/测试脚本和 `evidence/` 预算结果。真实优化测试完成后再补充新 CSV 和日志。无需上传数据集、大索引或本机编译产物。

目前尚未发布个人 GitHub 链接。上传后在课程报告中填写真实仓库地址和对应 commit 地址，并区分“预算扫描结果”与“回退实现结果”。

复现来源：https://github.com/GyuyeongKim/GateANN-public （固定提交见上文）。
