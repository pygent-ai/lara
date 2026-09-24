# Pygent 0.3.23 性能复测报告

> 测试日期：2026-09-24
>
> 对比版本：Pygent 0.3.12 与 0.3.23
>
> 0.3.23 源码版本：`75eed43387c05125868ebde5d959e128148f5ab3`
>
> 证据等级：本地 OpenAI-compatible mock 的合成集成测试

## 结论

Pygent 0.3.23 的功能正确性正常：串行、200 并发和 1,000 并发样本全部完成，每个请求都实际执行了 3 次工具调用，没有资源保护器终止。性能方面，相对相同环境下的 0.3.12，0.3.23 没有提升：三种执行模式的耗时、P95、CPU 和 RSS 均有不同程度的回退。

最明显的结果是：

- 200 并发时，direct、Runtime disabled、Runtime preferred 的运行时间分别增加 53.4%、46.9%、28.6%。
- 1,000 并发时，三种模式的运行时间分别增加 18.5%、34.8%、30.0%。
- CPU 时间在所有高并发模式下增加 17.1% 至 35.2%，峰值 RSS 增加 6.3% 至 12.2%。
- preferred 的进程树物理写入量在 200 并发增加 5.9%，在 1,000 并发增加 5.0%。
- 0.3.23 新增的功能没有造成失败或死锁；这里的问题是单位工作量的框架开销增加。

这是一组刻意压低模型耗时、放大框架开销的合成测试，不代表真实模型端到端延迟，也不能单独用于更新跨框架排名。

## 测试结果

所有数值均为新进程重复运行后的中位数；变化率为 0.3.23 相对 0.3.12，正数表示资源或时间增加。

### 串行：8 个请求，并发度 1，重复 5 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.12 | 0.810 | 0.194 | 0.026 | 1.031 | 71.078 | 0.002 |
| direct | 0.3.23 | 1.152 | 0.260 | 0.036 | 1.422 | 81.480 | 0.002 |
| direct | 变化 | +42.3% | +33.8% | +41.7% | +37.9% | +14.6% | 约 0% |
| Runtime disabled | 0.3.12 | 0.781 | 0.220 | 0.030 | 0.906 | 72.062 | 0.002 |
| Runtime disabled | 0.3.23 | 0.951 | 0.254 | 0.035 | 1.266 | 82.281 | 0.002 |
| Runtime disabled | 变化 | +21.8% | +15.5% | +16.5% | +39.7% | +14.2% | 约 0% |
| Runtime preferred | 0.3.12 | 0.644 | 0.987 | 0.148 | 1.438 | 74.383 | 6.300 |
| Runtime preferred | 0.3.23 | 0.873 | 1.066 | 0.155 | 1.734 | 84.484 | 6.736 |
| Runtime preferred | 变化 | +35.5% | +8.0% | +4.8% | +20.7% | +13.6% | +6.9% |

### 200 个请求，并发度 200，重复 3 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.12 | 0.696 | 2.461 | 2.417 | 4.109 | 100.590 | 0.028 |
| direct | 0.3.23 | 0.948 | 3.774 | 3.739 | 5.469 | 110.863 | 0.029 |
| direct | 变化 | +36.3% | +53.4% | +54.7% | +33.1% | +10.2% | +0.8% |
| Runtime disabled | 0.3.12 | 0.768 | 3.812 | 3.693 | 5.422 | 105.523 | 0.028 |
| Runtime disabled | 0.3.23 | 0.971 | 5.599 | 5.454 | 7.328 | 118.367 | 0.029 |
| Runtime disabled | 变化 | +26.5% | +46.9% | +47.7% | +35.2% | +12.2% | +0.5% |
| Runtime preferred | 0.3.12 | 0.809 | 6.721 | 6.609 | 7.328 | 108.258 | 95.790 |
| Runtime preferred | 0.3.23 | 0.926 | 8.641 | 8.509 | 9.812 | 117.645 | 101.457 |
| Runtime preferred | 变化 | +14.4% | +28.6% | +28.8% | +33.9% | +8.7% | +5.9% |

### 1,000 个请求，并发度 1,000，重复 3 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.12 | 0.712 | 19.443 | 19.191 | 22.438 | 199.371 | 0.140 |
| direct | 0.3.23 | 0.926 | 23.034 | 22.777 | 26.266 | 212.492 | 0.141 |
| direct | 变化 | +30.0% | +18.5% | +18.7% | +17.1% | +6.6% | +0.2% |
| Runtime disabled | 0.3.12 | 0.731 | 22.671 | 22.259 | 25.828 | 227.637 | 0.140 |
| Runtime disabled | 0.3.23 | 0.836 | 30.569 | 30.279 | 34.203 | 246.617 | 0.141 |
| Runtime disabled | 变化 | +14.4% | +34.8% | +36.0% | +32.4% | +8.3% | +0.2% |
| Runtime preferred | 0.3.12 | 0.736 | 40.974 | 40.226 | 37.359 | 219.012 | 725.993 |
| Runtime preferred | 0.3.23 | 0.889 | 53.250 | 52.585 | 49.484 | 232.711 | 762.370 |
| Runtime preferred | 变化 | +20.8% | +30.0% | +30.7% | +32.5% | +6.3% | +5.0% |

## 怎么测得

### 环境与隔离

- 操作系统：Windows；Python 3.13.11。
- 0.3.12 和 0.3.23 分别安装在独立虚拟环境中，避免包覆盖和 import cache 影响。
- 0.3.23 使用 tag 对应的独立源码 checkout；两个版本使用同一份 Lara benchmark adapter。
- 测试版本在同一台机器、同一测试时段交错运行。只比较这次同会话 A/B，不拿跨日期结果计算版本变化。
- mock 服务是单独的外部进程，不计入被测 adapter 的进程树资源。

### 每个请求做什么

本地 mock 提供 OpenAI-compatible Chat Completions 接口。每个请求固定产生 4 次模型 HTTP 往返：前三轮各请求一次确定性的纯函数工具，第四轮返回 `benchmark complete`。因此每个合格请求必须满足：

1. 模型调用 4 次；
2. 工具实际执行 3 次，step 依次为 1、2、3；
3. 最终输出包含 `benchmark complete`；
4. adapter 正常退出，且没有超时或 RSS guard 终止。

不能只根据最终文本判定成功。adapter 会独立统计 executor 中的工具执行次数；任一步缺失、重复或被拒绝都会让该轮结果失效。本次串行、200 并发和 1,000 并发的所有样本均通过该检查。

### 三种 Pygent 模式

- `pygent-direct`：直接运行 Agent，不创建 Runtime。
- `pygent-runtime-disabled`：经过 LocalRuntime，但 durability 关闭。
- `pygent-runtime-preferred`：经过 LocalRuntime，并启用 SQLite history 与 `DurabilityPolicy.PREFERRED`。

这三行不能互相替代。direct 体现 agent loop 的最低框架成本；disabled 体现 Runtime admission/lifecycle 成本；preferred 还包含 execution journal 和 SQLite 持久化成本。

### 采样与统计口径

资源采样器通过 `psutil` 每 10 ms 枚举 adapter 根进程及全部子进程，并记录：

- 初始化时间：构建 adapter、模型 client、Runtime 和相关资源的时间；
- 运行时间：从提交请求到全部请求完成的 wall time；
- P95：单轮内所有请求延迟的 95 分位数；表中再取多轮中位数；
- CPU：进程树所有进程的 user + system CPU 累计时间，不是 CPU 使用率；
- RSS：同一采样点进程树 RSS 之和的峰值；
- 读写：进程树的操作系统 I/O counter，代表物理/系统层 I/O，不等同于 SQLite 文件净增长；
- 线程和进程峰值、退出码与资源保护器终止原因。

串行场景为 8 请求、并发度 1、5 个新进程重复；高并发场景分别为 200/200 和 1,000/1,000、各 3 个新进程重复。表格报告中位数，避免单次 Windows 调度抖动主导结论。每个请求 deadline 为 120 秒；资源 runner 的边界高于请求 deadline，1,000 并发上限为 600 秒；峰值 RSS guard 为 4 GB。

### 可复现入口

测试脚本位于未纳入 Git 的本地 benchmark 工作目录：

- adapter：`.tmp/framework-bench/python_adapter.py`
- mock：`.tmp/framework-bench/mock_openai_server.py`
- 进程树采样：`.tmp/framework-bench/resource_runner.py`
- 高负载矩阵：`.tmp/framework-bench/run_high_load.py`

代表性的单轮调用如下；端口上的 mock 需先单独启动：

```powershell
python .tmp/framework-bench/mock_openai_server.py --port 8765

python .tmp/framework-bench/resource_runner.py `
  --python .tmp/framework-bench/py-pygent-0323/Scripts/python.exe `
  --adapter .tmp/framework-bench/python_adapter.py `
  --framework pygent-runtime-preferred `
  --requests 200 `
  --concurrency 200 `
  --output-dir .tmp/framework-bench/results/reproduction/run-1 `
  --timeout-seconds 180 `
  --max-rss-mb 4096
```

正式数据由外层矩阵为每个版本、模式和负载创建全新进程，多轮完成后按字段取中位数。`cProfile` 只用于解释热点，不参与上表计时；保留 SQLite 的诊断轮只用于比较表行数和文件大小，也不混入性能中位数。

## 回退来源分析

以下是源码、profile、import timing 和 SQLite 诊断共同支持的归因，不表示所有回退都能由单一提交解释。0.3.12 到 0.3.23 跨越多个版本，结论是累计变化。

### 1. 每次工具输出都重新做 JSON Schema 校验

0.3.23 在本地工具执行路径中对每个输出调用 `jsonschema.validate()`。该入口会重复检查并构建 validator；本负载每个请求执行 3 次工具，高并发时成本线性放大。

200 并发 direct 的 `cProfile` 函数调用数从 7,130,690 增到 8,760,319（+22.9%），profile 总时间从 7.593 秒增到 9.476 秒（+24.8%）。其中 jsonschema validator 的 `descend` 新增 22,200 次调用，累计约 1.331 秒。disabled 和 preferred 也观察到相同方向的调用数增长。

建议在 ToolSpec 注册或 binding compile 阶段创建并缓存 `Draft202012Validator`，执行时复用已编译 validator。

### 2. immediate steering 让 durable 空 receive 增多

保留数据库的 200 并发 preferred 诊断显示：

| 版本 | `execution_input_receives` | 每个 execution |
|---|---:|---:|
| 0.3.12 | 1,000 | 5 |
| 0.3.23 | 1,800 | 9 |

即使没有 steering 输入，0.3.23 仍在 model effect 完成边界执行额外 drain；durable 路径把空 receive 也持久化，receive 行数增加 80%。同期 journal event 和 effect 数没有增加：200 并发均为 14,600 个 event、1,400 个 effect。因此写放大的新增部分主要来自 receive，而不是业务事件变多。

建议将 immediate steering 做成显式 opt-in，或不持久化无输入的 drain，或使用 execution 级 watcher 避免每个 effect 产生 durable 空记录。

### 3. 基础依赖和 import 变重

- `import pygent`：0.595 秒增到 0.736 秒（+23.7%）。
- 隔离环境的 site-packages：54.8 MB 增到 77.7 MB（+22.9 MB，+41.8%）。
- 0.3.23 基础依赖包含 Pillow 和 pypdfium2；两者在本地分别约占 14.2 MB 和 7.2 MB。
- import timing 中 pypdfium2 约占 95 ms，`pygent.tool.standard._files` 累计 import 从约 90 ms 增到 203 ms。

建议把图像/PDF 能力放进可选 extra，并将标准工具的重依赖延迟到首次实际调用。

### 4. preferred 写入量继续增长

200 并发保留数据库从 14,860,288 bytes 增到 15,577,088 bytes（约 +4.8%）；进程树写入中位数从 95.790 MB 增到 101.457 MB（+5.9%）。1,000 并发从 725.993 MB 增到 762.370 MB（+5.0%）。

这不意味着 durability 不可用：全部请求均成功，恢复数据也可查询；它说明在短、密集、低模型延迟负载下，journal 的写放大仍是主要资源成本。

## 原始证据

本地原始结果没有纳入 Git，路径如下：

- 串行：`.tmp/framework-bench/results/pygent0323-ab-serial-20260924/`
- 200 并发：`.tmp/framework-bench/results/pygent0323-ab-200-20260924/`
- 1,000 并发：`.tmp/framework-bench/results/pygent0323-ab-1000-20260924/`
- profile：`.tmp/framework-bench/results/profile-0312-*-20260924.prof` 与 `profile-0323-*-20260924.prof`
- import timing：`.tmp/framework-bench/results/importtime-0312-20260924.log` 与 `importtime-0323-20260924.log`
- retained history：`.tmp/framework-bench/results/history-0312-200-20260924/executions.sqlite3` 与 `history-0323-200-20260924/executions.sqlite3`

`result.json` 包含 adapter 结果、正确性计数和进程树资源数据；`adapter.json` 保留每个请求的状态与延迟；`stderr.log` 用于排查警告和失败。原始目录是本机临时证据，发布或迁移仓库前应另外归档，不能把上述路径视为长期公共链接。

## 限制

- mock 每次模型响应只延迟约 2 ms，故意突出框架 CPU、调度和持久化成本。真实模型通常会稀释这些差异。
- 没有测试模型质量、真实供应商限流、TLS 网络距离、token streaming 或供应商 retry。
- Windows 调度和同机背景负载会带来噪声；结论依赖同会话、同负载、重复运行的版本 A/B，而不是跨日期绝对值。
- 0.3.23 的回退是 0.3.12 至 0.3.23 的累计差异，不能全部归因于 0.3.23 单个 release commit。
- 本报告不替换[跨框架资源基准](./agent-framework-resource-benchmark.md)的排名。若要更新排名，必须让所有候选框架在同一会话、同一正确性门槛下整体重跑。
