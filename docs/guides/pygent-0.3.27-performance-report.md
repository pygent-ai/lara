# Pygent 0.3.27 性能复测报告

> 测试日期：2026-10-20
>
> 对比版本：Pygent 0.3.26 与 0.3.27
>
> 0.3.27 tag：`3c9e0f2221d3797e8333feb0f696ae3c50d746b9`
>
> 证据等级：本地 OpenAI-compatible mock 的合成集成测试

## 结论

Pygent 0.3.27 没有出现功能退化或灾难性资源回退。串行、200 并发和 1,000 并发的 direct、Runtime disabled、Runtime preferred 全部通过；每个请求都实际执行 3 次工具调用，没有超时、RSS guard 终止或工具计数错误。

性能结果是混合的：

- direct 在串行和 1,000 并发分别快 3.9% 和 3.9%，但在 200 并发慢 11.1%。
- Runtime disabled 在串行基本持平，在 200 和 1,000 并发分别慢 18.8% 和 11.1%。
- Runtime preferred 在串行、200 和 1,000 并发分别慢 10.2%、9.4% 和 5.1%。
- 峰值 RSS 基本不变，所有场景变化都小于 1%。
- preferred 的写入量在 200 并发下降 0.77%，在 1,000 并发增加 0.37%，没有新的写放大信号。

因此，0.3.27 的 Agent loop 本身没有持续性变慢；需要关注的是 LocalRuntime 路径约 5% 至 19% 的 wall/P95 回退。三轮高并发样本足以识别方向，但不足以把全部差异归因到某一个实现变化。

## 发布变化

0.3.27 的公开发布说明主要包含：

- Volcengine Ark 视频生成协议；
- OpenAI Images 协议和 chat-completions 音频输出；
- durable history 剩余写路径的 SQLite lock retry；
- 将 wire codec、model effect、request snapshot 和 token estimate 的 Message-tree 投影统一到一个实现。

发布说明见 [Pygent 0.3.27](https://github.com/pygent-ai/pygent/releases/tag/v0.3.27)。Lara 从 0.3.26 升级时只需要修改依赖版本和锁文件，没有新增 API 兼容代码。

## 测试结果

表格是 fresh-process 重复运行后的中位数。变化率为 0.3.27 相对 0.3.26；时间和资源列中负数更好。

### 串行：8 个请求，并发度 1，重复 5 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.26 | 0.674 | 0.163 | 0.024 | 0.781 | 63.996 | 0.002 |
| direct | 0.3.27 | 0.623 | 0.157 | 0.024 | 0.719 | 64.309 | 0.002 |
| direct | 变化 | -7.6% | -3.9% | -3.3% | -8.0% | +0.5% | +0.1% |
| Runtime disabled | 0.3.26 | 0.610 | 0.199 | 0.028 | 0.750 | 65.320 | 0.002 |
| Runtime disabled | 0.3.27 | 0.682 | 0.203 | 0.031 | 0.781 | 65.375 | 0.002 |
| Runtime disabled | 变化 | +11.8% | +1.9% | +9.8% | +4.2% | +0.1% | +0.1% |
| Runtime preferred | 0.3.26 | 0.725 | 0.564 | 0.076 | 1.125 | 67.547 | 6.673 |
| Runtime preferred | 0.3.27 | 0.822 | 0.622 | 0.084 | 1.203 | 67.672 | 6.630 |
| Runtime preferred | 变化 | +13.4% | +10.2% | +10.2% | +6.9% | +0.2% | -0.6% |

### 200 个请求，并发度 200，重复 3 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.26 | 0.657 | 1.672 | 1.640 | 2.766 | 94.203 | 0.028 |
| direct | 0.3.27 | 0.572 | 1.857 | 1.815 | 3.297 | 94.887 | 0.028 |
| direct | 变化 | -12.9% | +11.1% | +10.7% | +19.2% | +0.7% | 约 0% |
| Runtime disabled | 0.3.26 | 0.675 | 2.313 | 2.269 | 3.281 | 101.219 | 0.028 |
| Runtime disabled | 0.3.27 | 0.534 | 2.748 | 2.698 | 3.703 | 101.953 | 0.028 |
| Runtime disabled | 变化 | -20.9% | +18.8% | +18.9% | +12.9% | +0.7% | 约 0% |
| Runtime preferred | 0.3.26 | 0.582 | 3.578 | 3.510 | 4.547 | 104.320 | 93.964 |
| Runtime preferred | 0.3.27 | 0.729 | 3.914 | 3.830 | 4.875 | 103.969 | 93.240 |
| Runtime preferred | 变化 | +25.4% | +9.4% | +9.1% | +7.2% | -0.3% | -0.8% |

### 1,000 个请求，并发度 1,000，重复 3 次

| 模式 | 版本 | 初始化 s | 运行 s | P95 s | CPU s | RSS MB | 写入 MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 0.3.26 | 0.569 | 12.669 | 12.432 | 15.609 | 197.813 | 0.141 |
| direct | 0.3.27 | 0.872 | 12.172 | 11.856 | 14.984 | 196.785 | 0.141 |
| direct | 变化 | +53.1% | -3.9% | -4.6% | -4.0% | -0.5% | 约 0% |
| Runtime disabled | 0.3.26 | 0.744 | 13.628 | 13.301 | 16.953 | 231.566 | 0.141 |
| Runtime disabled | 0.3.27 | 0.592 | 15.137 | 14.786 | 18.062 | 229.715 | 0.141 |
| Runtime disabled | 变化 | -20.4% | +11.1% | +11.2% | +6.5% | -0.8% | 约 0% |
| Runtime preferred | 0.3.26 | 0.772 | 24.280 | 23.725 | 25.984 | 218.781 | 712.013 |
| Runtime preferred | 0.3.27 | 0.643 | 25.524 | 24.932 | 27.094 | 219.129 | 714.663 |
| Runtime preferred | 变化 | -16.8% | +5.1% | +5.1% | +4.3% | +0.2% | +0.4% |

初始化只发生一次，且 3 个高并发样本容易受 Windows 文件缓存和进程调度影响。评估长生命周期服务时，应优先看运行时间、P95、CPU、RSS 和写入量，而不是单独放大初始化变化。

## 怎么测得

### 测试脚本

本轮性能数据由以下 3 个 Python 脚本生成。它们位于 Lara 工作区的 `.tmp/framework-bench/`，当前受 `.gitignore` 中的 `.tmp/` 规则忽略，**不是已提交到仓库的稳定 benchmark API**。因此，本文同时记录脚本职责、SHA-256 和完整命令，避免后续把不同脚本生成的数据混在一起。

| 脚本 | 职责 | 本轮 SHA-256 |
|---|---|---|
| `.tmp/framework-bench/mock_openai_server.py` | 提供本地 OpenAI Chat Completions mock；前三轮模型响应依次要求调用 `benchmark_tool(step=1/2/3)`，第四轮返回 `benchmark complete` | `5F44A125FF5EEB0E1BFDD1355AAC2F09A3B1FC1FCA16D1CC1DDA9A2D177AC6F2` |
| `.tmp/framework-bench/python_adapter.py` | 构造 Pygent Agent、模型层、工具层和可选 LocalRuntime；发起 N 个请求并记录每请求时延及真实工具执行次数 | `BA12EF5F04EF3392D725127F267F3A144E92B9D8EF3DDA510C4C8859A1BD07B6` |
| `.tmp/framework-bench/resource_runner.py` | 以 fresh process 启动 adapter，每 10 ms 采样整个进程树，施加 timeout/RSS guard，并生成最终 `result.json` | `5B0570257E528DE0F9D261049F9FE44C4037FD9983E0A0ADA127A548250D0F37` |

脚本之间的调用关系如下：

```text
resource_runner.py
  └─ 启动指定版本虚拟环境中的 python_adapter.py
       ├─ 调用 mock_openai_server.py（4 次模型 HTTP 往返/请求）
       ├─ 执行 benchmark_tool（3 次/请求）
       └─ 写 adapter.json
  └─ 采集子进程 CPU/RSS/I/O，合并写入 result.json
```

`python_adapter.py` 中三种 Pygent 模式的差异只有执行承载方式：

- `pygent-direct`：直接调用 `ReActLayer.invoke()`，不创建 Runtime；
- `pygent-runtime-disabled`：通过 `LocalRuntime.bind(...).start()` 执行，`DurabilityMode.DISABLED`；
- `pygent-runtime-preferred`：同样通过 LocalRuntime 执行，但额外打开 `SQLiteHistoryStore` 并启用 `DurabilityMode.PREFERRED`。

三种模式使用相同的 `ModelCallLayer`、`ReActLayer`、`ToolKit`、prompt、模型能力、deadline、工具授权和并发额度。工具声明为 `ToolSideEffect.PURE`，模型 retry 次数为 1；因此表格对比没有混入业务逻辑、工具实现或 retry 策略差异。

### 环境准备

本轮使用 CPython 3.13.11、`uv 0.9.21` 和采样进程中的 `psutil 7.2.2`。两个被测版本安装到独立虚拟环境，避免模块缓存或依赖覆盖：

```powershell
uv venv --python 3.13 .tmp/framework-bench/py-pygent-0326
uv pip install --python .tmp/framework-bench/py-pygent-0326/Scripts/python.exe `
  "pygent-ai[video]==0.3.26"

uv venv --python 3.13 .tmp/framework-bench/py-pygent-0327
uv pip install --python .tmp/framework-bench/py-pygent-0327/Scripts/python.exe `
  "pygent-ai[video]==0.3.27"
```

确认实际安装版本时，应查询发行包名 `pygent-ai`，而不是依赖 `pygent.__version__`：

```powershell
Get-ChildItem .tmp/framework-bench/py-pygent-0326/Lib/site-packages/pygent_ai-*.dist-info
Get-ChildItem .tmp/framework-bench/py-pygent-0327/Lib/site-packages/pygent_ai-*.dist-info
```

### 启动模型 mock

adapter 默认请求 `http://127.0.0.1:18765/v1`，所以先在独立进程启动 mock。mock 不属于 adapter 进程树，其 CPU 和内存不计入框架结果：

```powershell
$mock = Start-Process `
  -FilePath python `
  -ArgumentList @(
    ".tmp/framework-bench/mock_openai_server.py",
    "--port", "18765"
  ) `
  -PassThru `
  -WindowStyle Hidden

Test-NetConnection 127.0.0.1 -Port 18765
```

全部测试结束后停止该进程：

```powershell
Stop-Process -Id $mock.Id
```

### 单轮运行命令

下面是 1,000 请求、1,000 并发、Pygent 0.3.27、Runtime preferred 的完整单轮命令：

```powershell
python .tmp/framework-bench/resource_runner.py `
  --python .tmp/framework-bench/py-pygent-0327/Scripts/python.exe `
  --adapter .tmp/framework-bench/python_adapter.py `
  --framework pygent-runtime-preferred `
  --requests 1000 `
  --concurrency 1000 `
  --output-dir .tmp/framework-bench/results/reproduction/0327/preferred/run-1 `
  --timeout-seconds 600 `
  --max-rss-mb 4096
```

将 `--framework` 替换为 `pygent-direct` 或 `pygent-runtime-disabled` 即可运行另外两种模式；将 Python 路径中的 `0327` 替换为 `0326` 即可运行旧版本。

### 完整 A/B 矩阵

本轮矩阵为：串行 8 请求/并发 1/重复 5 次，200 请求/并发 200/重复 3 次，以及 1,000 请求/并发 1,000/重复 3 次。版本顺序按轮次交错，降低系统温度、文件缓存和后台负载对单个版本的偏置。以下 PowerShell 是等价的完整复现脚本：

```powershell
$bench = Resolve-Path ".tmp/framework-bench"
$runner = Join-Path $bench "resource_runner.py"
$adapter = Join-Path $bench "python_adapter.py"
$root = Join-Path $bench "results/reproduction"
$modes = @(
  "pygent-direct",
  "pygent-runtime-disabled",
  "pygent-runtime-preferred"
)
$loads = @(
  @{ Name = "serial"; Requests = 8; Concurrency = 1; Repeats = 5; Timeout = 120 },
  @{ Name = "200"; Requests = 200; Concurrency = 200; Repeats = 3; Timeout = 180 },
  @{ Name = "1000"; Requests = 1000; Concurrency = 1000; Repeats = 3; Timeout = 600 }
)

foreach ($load in $loads) {
  for ($run = 1; $run -le $load.Repeats; $run++) {
    $versions = if ($run % 2) { @("0326", "0327") } else { @("0327", "0326") }
    foreach ($version in $versions) {
      $python = Join-Path $bench "py-pygent-$version/Scripts/python.exe"
      foreach ($mode in $modes) {
        $output = Join-Path $root "$($load.Name)/$version/$mode/run-$run"
        python $runner `
          --python $python `
          --adapter $adapter `
          --framework $mode `
          --requests $load.Requests `
          --concurrency $load.Concurrency `
          --output-dir $output `
          --timeout-seconds $load.Timeout `
          --max-rss-mb 4096
        if ($LASTEXITCODE -ne 0) {
          throw "benchmark failed: $($load.Name) $version $mode run-$run"
        }
      }
    }
  }
}
```

### 输出文件和聚合规则

每个 `run-N` 目录包含：

- `adapter.json`：初始化时间、运行时间、每请求 latency、通过数和工具执行校验；
- `stderr.log`：adapter 的标准错误，报告结果要求为空或没有影响有效性的异常；
- `result.json`：在 adapter 数据上追加进程树 CPU、峰值 RSS、线程/进程峰值、读写量、退出码和 guard 终止原因。

每一轮的 P95 用该轮全部请求 latency 的 inclusive percentile 计算；表格中的初始化、运行、P95、CPU、RSS 和写入量，均是相同版本、负载和模式下各重复轮次的中位数。核心聚合逻辑为：

```python
import json
import statistics
from pathlib import Path

def load_run(path: Path) -> dict[str, float]:
    result = json.loads(path.read_text(encoding="utf-8"))
    latencies = [task["latency_seconds"] for task in result["tasks"]]
    return {
        "init_seconds": result["init_seconds"],
        "run_seconds": result["run_seconds"],
        "p95_seconds": statistics.quantiles(
            latencies, n=100, method="inclusive"
        )[94],
        "cpu_seconds": result["resource"]["cpu_seconds"],
        "peak_rss_mb": result["resource"]["peak_rss_mb"],
        "write_mb": result["resource"]["write_mb"],
    }

runs = [load_run(path) for path in Path("RESULT_DIRECTORY").glob("run-*/result.json")]
medians = {
    name: statistics.median(run[name] for run in runs)
    for name in runs[0]
}
```

样本只有在下列条件全部满足时才进入聚合：进程退出码为 0、`termination_reason` 为空、所有请求状态为 `passed`、最终文本包含 `benchmark complete`，并且工具 step 1/2/3 的观察次数都严格等于请求数。报告中的百分比使用 `(0.3.27 - 0.3.26) / 0.3.26 × 100%`。

### cProfile 归因命令

profile 使用 200 请求、200 并发和 Runtime disabled，以避免 SQLite I/O 把 Python 调用栈信号淹没。它不经过 `resource_runner.py`，也不参与正式性能表格：

```powershell
& .tmp/framework-bench/py-pygent-0326/Scripts/python.exe `
  -m cProfile `
  -o .tmp/framework-bench/results/profile-0326-disabled-200-20261020.prof `
  .tmp/framework-bench/python_adapter.py `
  --framework pygent-runtime-disabled `
  --requests 200 `
  --concurrency 200 `
  --output .tmp/framework-bench/results/profile-0326-disabled-200-adapter.json

& .tmp/framework-bench/py-pygent-0327/Scripts/python.exe `
  -m cProfile `
  -o .tmp/framework-bench/results/profile-0327-disabled-200-20261020.prof `
  .tmp/framework-bench/python_adapter.py `
  --framework pygent-runtime-disabled `
  --requests 200 `
  --concurrency 200 `
  --output .tmp/framework-bench/results/profile-0327-disabled-200-adapter.json
```

查看累计时间最高的调用：

```powershell
python -c "import pstats; pstats.Stats(r'.tmp/framework-bench/results/profile-0327-disabled-200-20261020.prof').strip_dirs().sort_stats('cumulative').print_stats(40)"
```

### Lara 回归验证命令

性能矩阵之外，还用 Lara 自身测试和静态检查确认升级没有破坏集成：

```powershell
uv run pytest
uv run pyright src
uv run python -m compileall -q src
uv lock --check
```

完整 Python 测试结果为 609 tests、13 subtests 通过；`pyright src` 为 0 errors。性能结果与功能回归结果分开判断：前者衡量开销，后者只证明已覆盖行为没有因升级失效。

### 环境与负载

- Windows，Python 3.13.11。
- 0.3.26 和 0.3.27 分别安装在独立虚拟环境，依赖解析一致，均包含 Lara 使用的 `video` extra。
- 同一 benchmark adapter、同一外部 mock、同一进程树采样器；每轮交错版本顺序。
- mock 服务监听 `127.0.0.1:18765`，不计入 adapter 进程树资源。
- 每个请求固定进行 4 次模型 HTTP 往返、3 次确定性纯工具执行，再返回最终文本。

正确性要求不是只检查最终文本。adapter 会统计 executor 中 step 1、2、3 的真实工具执行次数；缺失、重复、拒绝、请求失败、进程非零退出或资源 guard 终止都会使样本失效。本次全部样本通过。

### 模式

- `pygent-direct`：直接执行 Agent，不创建 Runtime。
- `pygent-runtime-disabled`：使用 LocalRuntime，关闭 durability。
- `pygent-runtime-preferred`：使用 LocalRuntime、SQLite history 和 `DurabilityPolicy.PREFERRED`。

### 资源口径

采样器使用 `psutil` 每 10 ms 枚举 adapter 根进程及全部子进程：

- CPU 是进程树 user + system 的累计时间，不是 CPU 百分比；
- RSS 是同一采样点进程树 RSS 总和的峰值；
- 写入是操作系统进程 I/O counter，不等于 SQLite 文件净增长；
- P95 先在每轮请求延迟中计算，再对重复轮次取中位数；
- 串行重复 5 次，200/1,000 并发各重复 3 次；
- 高负载 runner timeout 分别为 180 秒和 600 秒，RSS guard 为 4 GB。

## Profile 解释

对 200 并发 Runtime disabled 另外做了一次 `cProfile` 诊断。profile 只用于归因，不参与上表计时。

- 0.3.26：8,159,931 次函数调用，profile total 12.451 秒。
- 0.3.27：8,350,861 次函数调用，profile total 19.415 秒。
- 新的统一 `_message_fields` 在该路径执行 9,200 次投影，累计约 0.145 秒；800 次 decode 累计约 0.148 秒。
- 新 projector 的运行时成本是真实存在的，但约 0.3 秒不足以单独解释三轮中位数的全部 0.436 秒 wall 差异。
- `cProfile` 对 import 的函数级 tracing 放大明显，不能用 19.415/12.451 直接声称生产运行慢 56%。7 次 fresh `import pygent` 的 wall 中位数从约 713 ms 增到 817 ms，但单次 `-X importtime` 没有复现相同方向，因此启动回退结论仍不充分。

目前最稳妥的归因是：统一投影增加了一小部分 CPU；Runtime disabled/preferred 的其余差异可能同时包含调度和主机噪声。SQLite lock retry 没有表现为本负载下的写入放大。

## 原始证据

原始结果是本地临时证据，没有纳入 Git：

- smoke：`.tmp/framework-bench/results/pygent0327-smoke-20261020/`
- 串行：`.tmp/framework-bench/results/pygent0327-ab-serial-20261020/`
- 200 并发：`.tmp/framework-bench/results/pygent0327-ab-200-20261020/`
- 1,000 并发：`.tmp/framework-bench/results/pygent0327-ab-1000-20261020/`
- profile：`.tmp/framework-bench/results/profile-0326-disabled-200-20261020.prof` 与 `profile-0327-disabled-200-20261020.prof`

Lara 0.3.27 验证结果：针对 Runtime/模型/ToolTask 的 52 个测试通过；完整 Python 套件 609 tests、13 subtests 全部通过。

## 限制

- 这是近零供应商延迟的合成集成测试，故意放大框架开销；不是模型质量或真实供应商容量测试。
- 没有覆盖真实限流、TLS 网络距离、token streaming、媒体生成延迟或供应商 retry。
- 三次高并发重复适合发现明显变化，但对 5% 左右差异仍可能受主机噪声影响。
- 本轮只重跑 Pygent 0.3.26/0.3.27，不能据此正式替换跨框架排名。更新排名需要所有框架在同一会话重跑。
- 与 0.3.23 历史数据相比，0.3.27 的绝对值明显改善，但测试日期和中间实现均不同，不应把跨日期差值全部归因于单个版本。
