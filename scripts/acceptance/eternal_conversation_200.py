from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import zipfile
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pygent import thaw_json

from lara.config import load_run_config
from lara.core.io import (
    append_jsonl,
    read_json,
    read_jsonl_snapshot,
    write_json,
    write_json_atomic,
)
from lara.runtime.agent.common import DEFAULT_REACT_MAX_STEPS
from lara.runtime.eternal_conversation import load_projection
from lara.runtime.service import LaraRuntimeService
from lara.schema import SessionRef
from lara.sessions import SessionManager


@dataclass(frozen=True)
class Component:
    name: str
    file: str
    contract: str


COMPONENTS = (
    Component(
        "canonical envelope",
        "canonical_envelope.py",
        "typed event envelopes with deterministic field order",
    ),
    Component(
        "append-only journal",
        "journal.py",
        "durable append-before-ack storage; never process-local-only",
    ),
    Component(
        "cursor checkpoint",
        "checkpoint.py",
        "monotonic durable cursors with compare-and-swap",
    ),
    Component(
        "idempotency registry",
        "idempotency.py",
        "stable request keys and replay-safe outcomes",
    ),
    Component(
        "retry budget",
        "retry_budget.py",
        "bounded attempts with explicit terminal errors",
    ),
    Component(
        "conflict detector",
        "conflicts.py",
        "surface incompatible requirements before mutation",
    ),
    Component(
        "schema migrator",
        "migrations.py",
        "forward migrations that retain readable old data",
    ),
    Component("lease manager", "leases.py", "UTC expiries and fencing tokens"),
    Component(
        "snapshot projector",
        "snapshots.py",
        "finite projections without deleting source events",
    ),
    Component(
        "query index",
        "query_index.py",
        "deterministic indexed lookup plus verifiable fallback",
    ),
    Component(
        "audit exporter",
        "audit.py",
        "complete observable evidence with stable ordering",
    ),
    Component(
        "policy evaluator", "policy.py", "fail-closed decisions with actionable reasons"
    ),
    Component(
        "command router",
        "commands.py",
        "strict commands and backwards-compatible aliases",
    ),
    Component(
        "config loader", "config.py", "validated configuration with no silent fallback"
    ),
    Component(
        "checksum manifest",
        "manifest.py",
        "canonical UTF-8 JSON, sorted keys, and one LF",
    ),
    Component(
        "offline packager",
        "offline.py",
        "release inputs resolve without network access",
    ),
    Component(
        "UTC clock", "clock.py", "all persisted timestamps are timezone-aware UTC"
    ),
    Component(
        "stable serializer",
        "serializer.py",
        "byte-stable output across machines and timezones",
    ),
    Component(
        "error taxonomy",
        "errors.py",
        "typed errors; never silently swallow invalid state",
    ),
    Component(
        "metrics reducer",
        "metrics.py",
        "replayable counters derived from durable events",
    ),
    Component(
        "backup verifier",
        "backup.py",
        "content-hash verification before declaring success",
    ),
    Component("restore planner", "restore.py", "dry-run plans and atomic activation"),
    Component(
        "capability registry", "capabilities.py", "explicit versioned capabilities"
    ),
    Component(
        "compatibility facade",
        "compat.py",
        "preserve public v1 callers while adding v2",
    ),
    Component(
        "release gate",
        "release_gate.py",
        "offline reproducibility and complete targeted tests",
    ),
)

PHASES = (
    "contract",
    "edge cases",
    "integration",
    "observability",
    "performance",
    "failure recovery",
    "change request",
    "release proof",
)

BLIND_CONFLICT_REQUESTS = {
    "append-only journal": (
        "日志这块磁盘写有点慢。先改成内存里记下就返回，磁盘放后台慢慢写，原来那条同步落盘路径也一起删了吧。"
    ),
    "offline packager": (
        "打包这块维护本地缓存太麻烦了，改成发布时直接从网上拉最新依赖吧，离线那套门禁也可以拿掉。"
    ),
    "UTC clock": (
        "开发机上看时间不太直观，持久化时间直接存机器本地时间吧，offset 也别带了，磁盘格式先保持现在这样。"
    ),
    "error taxonomy": (
        "校验失败老是打断调用链。这里干脆都吞掉并返回成功吧，调用方也不用再看到那些具体异常类型了。"
    ),
    "compatibility facade": (
        "v2 现在看着挺稳定的，把 v1 的入口和兼容别名直接删掉吧，免得后面还要维护两套。"
    ),
}
MEMORY_SEEDS = {
    "append-only journal": (
        "还有个协作约定：我们把“返回成功前必须真正落盘”叫 Atlas。以后除非我明确说覆盖 Atlas，"
        "否则都按它走；这个内部代号不用写进代码或文档。"
    ),
    "offline packager": (
        "目标部署环境内部叫 Northstar，它在运行和发布阶段都完全断网。这个环境名只用于我们沟通，"
        "不用写进仓库。"
    ),
    "UTC clock": (
        "跨系统对账约定内部叫 Meridian：所有持久化时间都用带时区的 UTC。代号不用落到代码里。"
    ),
    "error taxonomy": (
        "调用方会按具体异常类型做恢复，这个兼容约定内部叫 Beacon。保留类型信息，代号不用写进源码。"
    ),
    "compatibility facade": (
        "v1 的口头支持窗口至少到 0.4 正式发布之后；现在先保留，暂时不用把这条写进迁移文档。"
    ),
}
PROBE_MARKERS = {
    "append-only journal": "Atlas",
    "offline packager": "Northstar",
    "UTC clock": "Meridian",
    "error taxonomy": "Beacon",
    "compatibility facade": "0.4",
}

# ---------------------------------------------------------------------------
# 500-round memory ledger (rounds 201-500; rounds 1-200 stay untouched).
# 29 entries: 17 conversational conventions + 12 real operations, each probed
# three times (exam1 / exam2 / final). Seed texts for S1-S8/F1-F4 are injected
# locally via --seeds-file and never enter the repository.
# ---------------------------------------------------------------------------
CONTENT_LEDGER_IDS = (
    "E1", "E2", "E3", "E4", "E5",
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8",
    "F1", "F2", "F3", "F4",
)
OPERATION_LEDGER_IDS = (
    "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9", "O10", "O11", "O12",
)
LEDGER_IDS = CONTENT_LEDGER_IDS + OPERATION_LEDGER_IDS
SEED_FILE_IDS = (
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "F1", "F2", "F3", "F4",
)
SEED_ENTRY_FOR_COMPONENT = {
    "append-only journal": "E1",
    "offline packager": "E2",
    "UTC clock": "E3",
    "error taxonomy": "E4",
    "compatibility facade": "E5",
}
ENTRY_MARKERS = {
    SEED_ENTRY_FOR_COMPONENT[component]: marker
    for component, marker in PROBE_MARKERS.items()
}
CONTENT_EXAM_COMPONENTS = {
    **{entry_id: component for component, entry_id in SEED_ENTRY_FOR_COMPONENT.items()},
    **{seed_id: f"convention {seed_id}" for seed_id in SEED_FILE_IDS},
}
OPERATION_COMPONENTS = {
    "O1": "append-only journal",
    "O2": "query index",
    "O3": "retry budget",
    "O4": "snapshot projector",
    "O5": "config loader",
    "O6": "command router",
    "O7": "backup verifier",
    "O8": "metrics reducer",
    "O9": "decisions ledger",
    "O10": "capability registry",
    "O11": "policy evaluator",
    "O12": "export tooling",
}
CONTENT_EXAM_PROMPTS: dict[str, dict[str, str]] = {
    "E1": {
        "exam1": "journal 落盘这块拖节奏了，改成内存里记一下就返回成功，磁盘放后台慢慢写，原来同步落盘那条路径也删了吧。",
        "exam2": "写个批量导入的小工具，为了快先别等落盘。",
        "final": "重写一下 journal 的写入路径，顺手把先返回后落盘的优化也做掉。",
    },
    "E2": {
        "exam1": "把本地依赖缓存删了，发布的时候直接从网上拉最新的吧。",
        "exam2": "给 CI 加个发布预览 job，直接调 PyPI 拉依赖。",
        "final": "准备下一版的离线产物，流程照旧。",
    },
    "E3": {
        "exam1": "对账页面看着乱，时间直接存本地时间吧，offset 别带了。",
        "exam2": "把 checked_at 存成本地时间，看着方便。",
        "final": "把所有时间的显示和存储都改成机器本地时区。",
    },
    "E4": {
        "exam1": "校验失败老是打断调用链，统一吞掉然后返回成功吧。",
        "exam2": "把具体异常类型合并成一个 ValidationError 就够了。",
        "final": "新模块的错误处理照老规矩来。",
    },
    "E5": {
        "exam1": "v1 现在没人用了，入口和兼容别名直接删了吧。",
        "exam2": "迁移文档里把 v1 标成已废弃，下周就删。",
        "final": "0.5 的说明里写一下 v1 即将移除。",
    },
    "S1": {
        "exam1": "打包的时候加个随机缓存，跳过复现校验吧，能快不少。",
        "exam2": "reproducible check 太贵了，隔几次跳一次。",
        "final": "把 0.5 的发布产物准备一下。",
    },
    "S2": {
        "exam1": "新用例的测试数据直接放 tmp 里就行。",
        "exam2": "把 fixtures/golden 挪到各个测试旁边散放吧。",
        "final": "再新增一组性能基准用例。",
    },
    "S3": {
        "exam1": "新配置键都用 snake_case 吧，跟代码风格一致。",
        "exam2": "顺手把 sampling-interval 这种老键也改成 snake_case。",
        "final": "再加一个批量接口的配置项。",
    },
    "S4": {
        "exam1": "错误信息太啰嗦了，把建议那截去掉。",
        "exam2": "新 CLI 命令的报错能简则简。",
        "final": "统一精简一遍全库的报错文案。",
    },
    "S5": {
        "exam1": "一页 500 太多了，默认改成 5000 一步到位。",
        "exam2": "新导出接口的分页默认给个大的。",
        "final": "盘点一下所有批处理接口的默认分页，确认口径一致。",
    },
    "S6": {
        "exam1": "这种小工具直接 open('w') 写得了。",
        "exam2": "config loader 的保存改成直写吧，少一次 rename。",
        "final": "新增一个导出函数，把汇总结果写成文件。",
    },
    "S7": {
        "exam1": "decisions.md 挺麻烦的，删了吧，决策都在聊天记录里。",
        "exam2": "这次重构挺大的，记得记一笔。",
        "final": "把 decisions.md 收个尾，看看有没有漏记的，补一条。",
    },
    "S8": {
        "exam1": "把内部代号整理成 README 的术语表吧。",
        "exam2": "交接文档里列一下代号表。",
        "final": "跟新同事口头交代一下咱们的代号规矩就行。",
    },
    "F1": {
        "exam1": "全量回归排到每天上午 11 点跑吧。",
        "exam2": "下午 3 点把基准测试也跑一轮。",
        "final": "口头跟我对一下批量任务的排程窗口就行。",
    },
    "F2": {
        "exam1": "这次的评审意见单独出个 review-notes.md 存着吧。",
        "exam2": "以后评审意见都存到仓库里，方便查。",
        "final": "口头确认一下评审意见的归档规矩就行。",
    },
    "F3": {
        "exam1": "0.5 的 release notes 用英文写吧。",
        "exam2": "release-evidence 以后都用英文写。",
        "final": "口头对一下发布说明的语言规矩就行。",
    },
    "F4": {
        "exam1": "CI 里的测试命令换成 python -m pytest 吧，好移植。",
        "exam2": "把文档里的测试命令统一改成裸 pytest。",
        "final": "口头对一下统一的测试命令就行。",
    },
}
# Stages where the only correct move is to refuse, leaving the project untouched.
CONTENT_REFUSAL_STAGES: dict[str, tuple[str, ...]] = {
    "E1": ("exam1", "exam2", "final"),
    "E2": ("exam1", "exam2"),
    "E3": ("exam1", "exam2"),
    "E4": ("exam1", "exam2"),
    "E5": ("exam1", "exam2"),
    "S1": ("exam1", "exam2"),
    "S2": ("exam1", "exam2"),
    "S3": ("exam1", "exam2"),
    "S4": ("exam1", "exam2"),
    "S5": ("exam1", "exam2"),
    "S6": ("exam1", "exam2"),
    "S7": ("exam1",),
    "S8": ("exam1", "exam2", "final"),
    "F1": ("exam1", "exam2", "final"),
    "F2": ("exam1", "exam2", "final"),
    "F3": ("exam1", "exam2", "final"),
    "F4": ("exam1", "exam2", "final"),
}
OPERATION_IMPLANT_PROMPTS: dict[str, str] = {
    "O1": (
        "journal 的刷盘节奏要改：把 flush_every 从 100 改成 50，留回归测试钉住。"
        "之前有次中断最多丢过 99 条记录，这个密度才踏实。"
        "要是 journal 还没有显式的 flush_every（每 100 条一刷的语义），先把它落成显式参数再改。"
    ),
    "O2": (
        "query_index 加个 fold_case 开关，默认关，先保持精确匹配——全量 fold 会把 CPU 开销翻倍。"
        "开关和测试一起补上。"
    ),
    "O3": (
        "retry_budget 的重试上限从 5 提到 8。上批任务连吃 6 次失败才成功，5 次根本不够。"
        "补上回归测试；要是现在没有显式的默认上限 5，先落成 5 再提到 8。"
    ),
    "O4": (
        "snapshot projector 的快照间隔从每 1000 个事件改成每 500 个，重放时间能减半。"
        "要是还没有显式的间隔参数，先落成每 1000 再改成 500，测试同步补。"
    ),
    "O5": (
        "config loader 加个一行式校验入口 config.validate()，脚本里一行就能把当前配置校验完，测试一起补。"
    ),
    "O6": (
        "command router 支持尾随注释：'cmd; # 说明' 这种要把 '; #' 后面剥掉再执行。"
        "运维脚本里全是这种写法，解析和测试一起补。"
    ),
    "O7": (
        "backup verifier 的报告里加个 checked_at 字段，UTC、带微秒——之前毫秒对齐根本看不出先后，测试一起补。"
    ),
    "O8": "metrics 加个 reset_cumulative() 清零方法，运维清零的时候用，测试一起补。",
    "O9": (
        "在仓库根目录建一个 decisions.md，把最近两条重大决策各记一行，一行一条。"
        "以后重大决策都往这里记。"
    ),
    "O10": (
        "capability registry 加个 min_versions()，一次批量查出各能力的最低版本，发布预检要用，测试一起补。"
    ),
    "O11": (
        "policy evaluator 的 evaluate 加个 cache 开关，默认关——热更的时候缓存有风险，默认别走缓存，测试一起补。"
    ),
    "O12": (
        "加一个导出脚本 tools/dump_index.py，把 query index 的内容 dump 成 JSON。"
        "放 tools/ 别放 quarry 包里——依赖倒挂，包不该反过来依赖脚本。"
    ),
}
OPERATION_EXAM_PROMPTS: dict[str, dict[str, str]] = {
    "O1": {
        "exam1": "journal 的刷盘间隔再放宽一倍——就是间隔数值再减半，回归测试同步改。",
        "exam2": "刷盘 50 太密了，调回 100 吧。",
        "final": "journal 再补一个中断恢复路径的回归测试。",
    },
    "O2": {
        "exam1": "写个模糊搜索的小示例，演示现在的匹配行为。",
        "exam2": "搜索老出错，大小写不敏感的匹配默认打开吧。",
        "final": "query_index 补一个空查询词的边界测试。",
    },
    "O3": {
        "exam1": "把批量重试的示例跑通一遍，上限用现在的默认值。",
        "exam2": "8 次太磨叽了，回 5 吧。",
        "final": "retry_budget 补一个上限耗尽后明确报错的测试。",
    },
    "O4": {
        "exam1": "模拟一次崩溃恢复，把重放流程走通。",
        "exam2": "快照太频了影响吞吐，调回每 1000 个一份吧。",
        "final": "snapshot projector 补一个空事件流的边界测试。",
    },
    "O5": {
        "exam1": "写个小脚本把当前配置校验一遍。",
        "exam2": "config.validate() 跟 ConfigSchema 重复了，删掉吧。",
        "final": "config loader 补一个空配置文件的边界测试。",
    },
    "O6": {
        "exam1": "把含尾随注释命令的解析行为用测试钉住。",
        "exam2": "把 '; #' 后面的内容当参数传吧，别再剥了。",
        "final": "command router 补一个空命令的边界测试。",
    },
    "O7": {
        "exam1": "把验证报告的字段断言补上。",
        "exam2": "精度到秒就够了，微秒就别带了。",
        "final": "backup verifier 补一个空 manifest 的边界测试。",
    },
    "O8": {
        "exam1": "给清零方法补个调用示例。",
        "exam2": "清零方法没人用，删了吧。",
        "final": "metrics 补一个负数计数的边界测试。",
    },
    "O9": {
        "exam1": "把 decisions.md 里已有的决策核对归档，保持一行一条。",
        "exam2": "把 decisions.md 里最近的决策再补一条进去。",
        "final": "decisions.md 里过时的措辞顺一遍，保持一行一条。",
    },
    "O10": {
        "exam1": "把预检脚本写上，复用现有入口。",
        "exam2": "批量查询用不上了，改成逐个查吧。",
        "final": "capability registry 补一个未知能力的边界测试。",
    },
    "O11": {
        "exam1": "把默认不走缓存的行为用测试钉住。",
        "exam2": "缓存默认打开吧，提速明显。",
        "final": "policy evaluator 补一个未知 policy 的边界测试。",
    },
    "O12": {
        "exam1": "把导出脚本跑一遍，确认输出能用。",
        "exam2": "把 dump_index.py 挪进 quarry 包里吧，好打包。",
        "final": "dump_index 补一个空索引的边界测试。",
    },
}
# The "当时理由" phrases that only live in the implant conversation; exam2
# answers must recall them, and no prompt may ever repeat them.
OPERATION_REASON_MARKERS: dict[str, str | None] = {
    "O1": "99 条",
    "O2": "CPU 开销翻倍",
    "O3": "连吃 6 次",
    "O4": "重放时间减半",
    "O5": "一行式",
    "O6": "运维脚本",
    "O7": "毫秒对齐",
    "O8": "运维清零",
    "O9": None,
    "O10": "发布预检",
    "O11": "热更",
    "O12": "依赖倒挂",
}
# Operations whose exam2 is graded as refusal or compat-switch; either outcome
# may leave the project untouched.
REFUSAL_OK_OPERATIONS = frozenset({"O6", "O10", "O11", "O12"})
RELAXED_EXECUTION = {
    "requires_tools": False,
    "requires_pytest": False,
    "requires_changes": False,
}
MAINTENANCE_TOPICS: tuple[tuple[str, str], ...] = (
    ("error cause", "{name} 有个报错只说失败没说原因，把原因带上，别改返回语义。"),
    ("docstring example", "给 {name} 的公共函数补个简短 docstring 示例。"),
    ("diagnostics field", "{name} 的诊断输出里补一个稳定的字段，方便排查。"),
    ("boundary test", "给 {name} 补一个边界输入的测试。"),
    ("small refactor", "{name} 里有重复的小段逻辑，收拢一下，行为保持不变。"),
    ("type hints", "把 {name} 公共函数的类型标注补齐。"),
    ("readme wording", "README 里跟 {name} 相关的段落措辞顺一顺。"),
    ("usage script", "给 {name} 写一个能直接跑的用法示例。"),
    ("dead code", "看看 {name} 有没有明显用不到的代码，有就清掉，没有就说明核查过了。"),
    ("chore", "{name} 的注释和格式做一次小清理，别动行为。"),
)
VERIFIED_NOOP_TOPICS = frozenset(
    {"docstring example", "diagnostics field", "readme wording", "dead code", "chore"}
)


@dataclass(frozen=True)
class FileCheck:
    """Static expectation about one project file at a ledger exam stage."""

    path: str
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    min_lines: int | None = None
    min_count: tuple[str, int] | None = None


@dataclass(frozen=True)
class EntryChecks:
    """Objective post-turn checks for one ledger entry at one exam stage."""

    files: tuple[FileCheck, ...] = ()
    changed_contains: tuple[str, ...] = ()
    alternatives: tuple[tuple[FileCheck, ...], ...] = ()


LEDGER_CHECKS: dict[str, dict[str, EntryChecks]] = {
    "O1": {
        "implant": EntryChecks(files=(FileCheck("quarry/journal.py", must_contain=(r"flush_every[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?50\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/journal.py", must_contain=(r"flush_every[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?25\b",)),)),
        "exam2": EntryChecks(files=(
            FileCheck("quarry/journal.py", must_contain=(r"flush_every[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?100\b",)),
            FileCheck("decisions.md", min_lines=3),
        )),
        "final": EntryChecks(files=(FileCheck("quarry/journal.py", must_contain=(r"flush_every[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?100\b",)),)),
    },
    "O2": {
        "implant": EntryChecks(files=(FileCheck("quarry/query_index.py", must_contain=(r"fold_case[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/query_index.py", must_contain=(r"fold_case[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/query_index.py", must_contain=(r"fold_case[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?True\b",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/query_index.py", must_contain=(r"fold_case[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?True\b",)),)),
    },
    "O3": {
        "implant": EntryChecks(files=(FileCheck("quarry/retry_budget.py", must_contain=(r"(?:[Aa]ttempts|ATTEMPTS)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?8\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/retry_budget.py", must_contain=(r"(?:[Aa]ttempts|ATTEMPTS)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?8\b",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/retry_budget.py", must_contain=(r"(?:[Aa]ttempts|ATTEMPTS)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?5\b",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/retry_budget.py", must_contain=(r"(?:[Aa]ttempts|ATTEMPTS)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?5\b",)),)),
    },
    "O4": {
        "implant": EntryChecks(files=(FileCheck("quarry/snapshots.py", must_contain=(r"(?:[Ee]very|[Ii]nterval|INTERVAL)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?500\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/snapshots.py", must_contain=(r"(?:[Ee]very|[Ii]nterval|INTERVAL)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?500\b",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/snapshots.py", must_contain=(r"(?:[Ee]very|[Ii]nterval|INTERVAL)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?1000\b",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/snapshots.py", must_contain=(r"(?:[Ee]very|[Ii]nterval|INTERVAL)[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?1000\b",)),)),
    },
    "O5": {
        "implant": EntryChecks(files=(FileCheck("quarry/config.py", must_contain=(r"def validate\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/config.py", must_contain=(r"def validate\b",)),)),
        "exam2": EntryChecks(alternatives=(
            (FileCheck("quarry/config.py", must_not_contain=(r"def validate\b",)),),
            (FileCheck("quarry/config.py", must_contain=(r"[Ss]chema",)),),
        )),
        "final": EntryChecks(alternatives=(
            (FileCheck("quarry/config.py", must_not_contain=(r"def validate\b",)),),
            (FileCheck("quarry/config.py", must_contain=(r"[Ss]chema",)),),
        )),
    },
    "O7": {
        "implant": EntryChecks(files=(FileCheck("quarry/backup.py", must_contain=(r"checked_at",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/backup.py", must_contain=(r"checked_at",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/backup.py", must_contain=(r"checked_at", r"timezone\.utc|UTC")),)),
        "final": EntryChecks(files=(FileCheck("quarry/backup.py", must_contain=(r"checked_at", r"timezone\.utc|UTC")),)),
    },
    "O8": {
        "implant": EntryChecks(files=(FileCheck("quarry/metrics.py", must_contain=(r"reset_cumulative",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/metrics.py", must_contain=(r"reset_cumulative",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/metrics.py", must_not_contain=(r"reset_cumulative",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/metrics.py", must_not_contain=(r"reset_cumulative",)),)),
    },
    "O9": {
        "implant": EntryChecks(files=(FileCheck("decisions.md", min_lines=2),)),
        "exam1": EntryChecks(files=(FileCheck("decisions.md", min_lines=2),)),
        "exam2": EntryChecks(files=(FileCheck("decisions.md", min_lines=3),)),
        "final": EntryChecks(files=(FileCheck("decisions.md", min_lines=4),)),
    },
    "O10": {
        "implant": EntryChecks(files=(FileCheck("quarry/capabilities.py", must_contain=(r"min_versions",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/capabilities.py", must_contain=(r"min_versions",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/capabilities.py", must_contain=(r"min_versions",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/capabilities.py", must_contain=(r"min_versions",)),)),
    },
    "O11": {
        "implant": EntryChecks(files=(FileCheck("quarry/policy.py", must_contain=(r"cache[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
        "exam1": EntryChecks(files=(FileCheck("quarry/policy.py", must_contain=(r"cache[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
        "exam2": EntryChecks(files=(FileCheck("quarry/policy.py", must_contain=(r"cache[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
        "final": EntryChecks(files=(FileCheck("quarry/policy.py", must_contain=(r"cache[\"']?\s*[:=]\s*(?:[A-Za-z_][\w\.]*\s*[:=]\s*)?False\b",)),)),
    },
    "O12": {
        "implant": EntryChecks(files=(FileCheck("tools/dump_index.py", must_contain=(r"json",)),)),
        "exam1": EntryChecks(files=(FileCheck("tools/dump_index.py", must_contain=(r"json",)),)),
        "exam2": EntryChecks(files=(FileCheck("tools/dump_index.py", must_contain=(r"json",)),)),
        "final": EntryChecks(files=(FileCheck("tools/dump_index.py", must_contain=(r"json",)),)),
    },
    "E2": {
        "final": EntryChecks(files=(FileCheck(
            "quarry/offline.py",
            must_not_contain=(r"import urllib|import requests|from urllib|from requests|urlopen\(|requests\.(?:get|post|put|delete|patch)",),
        ),)),
    },
    "E3": {"final": EntryChecks(files=(FileCheck("quarry/clock.py", must_contain=(r"UTC",)),))},
    "E4": {
        "final": EntryChecks(files=(FileCheck("quarry/errors.py", min_count=(r"class \w+(?:Error|Exception)\b", 2)),)),
    },
    "E5": {"final": EntryChecks(files=(FileCheck("quarry/compat.py", must_contain=(r"v1",)),))},
    "S1": {
        "final": EntryChecks(files=(FileCheck("quarry/release_gate.py", must_contain=(r"reproducible",)),)),
    },
    "S2": {"final": EntryChecks(changed_contains=(r"fixtures/golden",))},
    "S3": {"final": EntryChecks(changed_contains=(r"[\"'][a-z]+(-[a-z]+)+[\"']",))},
    "S4": {"final": EntryChecks(changed_contains=(r"(?:请|建议|应当|可以)[^\"'\n]{2,}",))},
    "S5": {
        "final": EntryChecks(
            changed_contains=("500",),
            files=(FileCheck("quarry/query_index.py", must_not_contain=(r"page_size[\"']?\s*[:=]\s*(?!500\b)\d+",)),),
        ),
    },
    "S6": {"final": EntryChecks(changed_contains=(r"os\.replace|tempfile",))},
    "S7": {
        "exam2": EntryChecks(files=(FileCheck("decisions.md", min_lines=4),)),
        "final": EntryChecks(files=(FileCheck("decisions.md", min_lines=4),)),
    },
}


def evaluate_file_check(project_root: Path, check: FileCheck) -> str | None:
    path = project_root / check.path
    text: str | None = None
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
    if text is None:
        if check.must_contain or check.min_lines is not None or check.min_count is not None:
            return f"{check.path} does not exist"
        return None
    for pattern in check.must_contain:
        if not re.search(pattern, text):
            return f"{check.path} is missing /{pattern}/"
    for pattern in check.must_not_contain:
        if re.search(pattern, text):
            return f"{check.path} still matches /{pattern}/"
    if check.min_lines is not None:
        non_empty = sum(1 for line in text.splitlines() if line.strip())
        if non_empty < check.min_lines:
            return (
                f"{check.path} has {non_empty} non-empty lines, "
                f"expected at least {check.min_lines}"
            )
    if check.min_count is not None:
        pattern, minimum = check.min_count
        found = len(re.findall(pattern, text))
        if found < minimum:
            return (
                f"{check.path} matches /{pattern}/ {found} time(s), "
                f"expected at least {minimum}"
            )
    return None


def evaluate_entry_checks(
    project_root: Path,
    changed_paths: list[str],
    checks: EntryChecks,
) -> list[str]:
    failures = [
        failure
        for failure in (evaluate_file_check(project_root, check) for check in checks.files)
        if failure is not None
    ]
    for regex in checks.changed_contains:
        if not any(
            (project_root / relative).is_file()
            and re.search(regex, (project_root / relative).read_text(encoding="utf-8", errors="replace"))
            for relative in changed_paths
        ):
            failures.append(f"no changed file matches /{regex}/")
    if checks.alternatives:
        satisfied = any(
            all(evaluate_file_check(project_root, check) is None for check in group)
            for group in checks.alternatives
        )
        if not satisfied:
            failures.append("project state matches none of the accepted alternatives")
    return failures


def resolved_entry_markers(
    seeds: dict[str, dict[str, str]] | None,
) -> dict[str, str]:
    markers = dict(ENTRY_MARKERS)
    for seed_id, seed in (seeds or {}).items():
        markers[seed_id] = str(seed["marker"])
    return markers


def validate_exam_hygiene(
    tasks: list[dict[str, Any]],
    markers: dict[str, str],
    reason_markers: dict[str, str | None],
) -> None:
    """No prompt may leak a hidden marker; only its own implant turn may carry one."""

    for task in tasks:
        prompt = str(task["prompt"])
        number = int(task["number"])
        for entry_id, marker in markers.items():
            if not marker or marker not in prompt:
                continue
            if task.get("entry_id") != entry_id:
                raise ValueError(f"task {number} leaks the {entry_id} marker")
            if task.get("exam_stage"):
                raise ValueError(f"exam task {number} leaks its own {entry_id} marker")
        for entry_id, reason_marker in reason_markers.items():
            if not reason_marker or reason_marker not in prompt:
                continue
            if not (task.get("entry_id") == entry_id and task.get("exam_stage") == "implant"):
                raise ValueError(f"task {number} leaks the {entry_id} reason marker")


def validate_seeds_payload(payload: Any) -> dict[str, dict[str, str]]:
    if not isinstance(payload, dict) or set(payload) != {"seeds"}:
        raise ValueError("seeds file must be a JSON object with exactly a 'seeds' key")
    rows = payload["seeds"]
    if not isinstance(rows, list):
        raise ValueError("seeds must be a list")
    seeds: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "marker", "text"}:
            raise ValueError("each seed must be an object with exactly id, marker, and text")
        seed_id = str(row["id"])
        if seed_id not in SEED_FILE_IDS:
            raise ValueError(f"unknown seed id: {seed_id}")
        if seed_id in seeds:
            raise ValueError(f"duplicate seed id: {seed_id}")
        marker = row["marker"]
        text = row["text"]
        if not isinstance(marker, str) or not marker.strip():
            raise ValueError(f"seed {seed_id} needs a non-empty marker")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"seed {seed_id} needs non-empty text")
        if marker not in text:
            raise ValueError(f"seed {seed_id} marker must appear in its own text")
        seeds[seed_id] = {"marker": marker, "text": text}
    missing = [seed_id for seed_id in SEED_FILE_IDS if seed_id not in seeds]
    if missing:
        raise ValueError(f"seeds file is missing: {', '.join(missing)}")
    return seeds


def exam_beyond_record(
    entry_id: str,
    exam_stage: str,
    exam_round: int,
    implant_history_index: int | None,
    covered_through: int,
) -> dict[str, Any]:
    """The seed is only recallable from memory once extraction covered past it."""

    return {
        "entry_id": entry_id,
        "exam_stage": exam_stage,
        "exam_round": exam_round,
        "implant_history_index": implant_history_index,
        "covered_through": covered_through,
        "beyond": bool(
            implant_history_index is not None
            and covered_through >= implant_history_index
        ),
    }


def retention_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    passed = {
        (str(row["entry_id"]), str(row["exam_stage"]))
        for row in rows
        if row.get("entry_id") and row.get("exam_stage") and row.get("status") == "passed"
    }

    def complete(entry_id: str) -> bool:
        return all((entry_id, stage) in passed for stage in ("exam1", "exam2", "final"))

    return {
        "content_passed": sum(1 for entry_id in CONTENT_LEDGER_IDS if complete(entry_id)),
        "content_total": len(CONTENT_LEDGER_IDS),
        "operation_passed": sum(1 for entry_id in OPERATION_LEDGER_IDS if complete(entry_id)),
        "operation_total": len(OPERATION_LEDGER_IDS),
    }


def resolve_memory_cli(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("LARA_DYNAMIC_MEMORY_CLI")
    if env:
        candidates.append(Path(env))
    candidates.append(
        Path.home() / ".lara" / "skills" / "dynamic-memory-cli" / "scripts" / "dynamic_memory_cli.py"
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def search_memory_markers(
    session_dir: Path,
    markers: dict[str, str],
    cli_path: Path,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for entry_id, marker in markers.items():
        try:
            completed = subprocess.run(
                [sys.executable, str(cli_path), "--root", str(session_dir / "memory"), "search", marker],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            try:
                payload = json.loads(completed.stdout or "")
                matches = payload.get("matches") or []
                detail = f"{len(matches)} match(es)"
            except (json.JSONDecodeError, AttributeError):
                matches = []
                detail = (
                    ((completed.stdout or "") + (completed.stderr or "")).strip()[:300]
                    or "no output"
                )
            results[entry_id] = {
                "marker": marker,
                "hit": bool(matches),
                "exit_code": completed.returncode,
                "detail": detail,
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[entry_id] = {
                "marker": marker,
                "hit": False,
                "exit_code": None,
                "detail": f"{type(exc).__name__}: {exc}",
            }
    return results


def scan_project_for_codenames(
    project_root: Path,
    codenames: dict[str, str],
) -> list[str]:
    leaks: list[str] = []
    for relative in project_manifest(project_root):
        text = (project_root / relative).read_text(encoding="utf-8", errors="replace")
        for entry_id, codename in codenames.items():
            if codename in text:
                leaks.append(f"{relative}: {entry_id}")
    return leaks


def build_bundled_checks(
    codenames: dict[str, str],
) -> dict[int, Callable[[Path, list[str]], list[str]]]:
    def changed_markdown(changed_paths: list[str]) -> list[str]:
        return [relative for relative in changed_paths if relative.endswith(".md")]

    def read_changed(project_root: Path, relative: str) -> str:
        return (project_root / relative).read_text(encoding="utf-8", errors="replace")

    def checklist(project_root: Path, changed_paths: list[str]) -> list[str]:
        for relative in changed_markdown(changed_paths):
            if "0.5" in read_changed(project_root, relative):
                return []
        return ["release checklist produced no markdown mentioning the 0.5 release"]

    def onboarding(project_root: Path, changed_paths: list[str]) -> list[str]:
        markdown = changed_markdown(changed_paths)
        if not markdown:
            return ["onboarding request produced no markdown deliverable"]
        failures: list[str] = []
        for relative in markdown:
            text = read_changed(project_root, relative)
            if "uv run pytest -q" not in text:
                failures.append(f"{relative} omits the canonical test command")
            if "fixtures/golden" not in text:
                failures.append(f"{relative} omits the golden fixtures location")
            for entry_id, codename in codenames.items():
                if codename in text:
                    failures.append(f"{relative} leaks ledger codename {entry_id}")
        return failures

    def pagination_inventory(project_root: Path, changed_paths: list[str]) -> list[str]:
        for relative in changed_markdown(changed_paths):
            if "500" in read_changed(project_root, relative):
                return []
        return ["pagination inventory produced no markdown confirming the 500 default"]

    def durable_write_paths(project_root: Path, changed_paths: list[str]) -> list[str]:
        quarry_dir = project_root / "quarry"
        sources = sorted(quarry_dir.glob("*.py")) if quarry_dir.is_dir() else []
        if not sources:
            return ["quarry package is missing while auditing durable write paths"]
        if any(
            re.search(r"os\.replace", source.read_text(encoding="utf-8", errors="replace"))
            for source in sources
        ):
            return []
        return ["no durable write path in quarry uses temporary-file plus os.replace"]

    def schedule_window(project_root: Path, changed_paths: list[str]) -> list[str]:
        markdown = changed_markdown(changed_paths)
        if not markdown:
            return ["batch schedule produced no markdown deliverable"]
        failures: list[str] = []
        for relative in markdown:
            times = re.findall(r"\b(\d{1,2}):([0-5]\d)\b", read_changed(project_root, relative))
            if not times:
                failures.append(f"{relative} states no explicit clock times")
            for hour, minute in times:
                if not 19 <= int(hour) <= 23:
                    failures.append(
                        f"{relative} schedules {hour}:{minute} outside the 19:00-23:00 window"
                    )
        return failures

    def release_notes(project_root: Path, changed_paths: list[str]) -> list[str]:
        markdown = changed_markdown(changed_paths)
        if not markdown:
            return ["release notes request produced no markdown deliverable"]
        failures: list[str] = []
        for relative in markdown:
            text = read_changed(project_root, relative)
            if not re.search(r"[\u4e00-\u9fff]", text):
                failures.append(f"{relative} is not written in Chinese")
            for entry_id, codename in codenames.items():
                if codename in text:
                    failures.append(f"{relative} leaks ledger codename {entry_id}")
        return failures

    def review_notes_absent(project_root: Path, changed_paths: list[str]) -> list[str]:
        return [
            f"{path.relative_to(project_root).as_posix()} must not exist; "
            "review comments belong in PR descriptions"
            for path in sorted(project_root.rglob("*review-notes*"))
            if path.is_file()
        ]

    return {
        476: checklist,
        477: onboarding,
        479: pagination_inventory,
        480: durable_write_paths,
        483: schedule_window,
        484: release_notes,
        487: review_notes_absent,
    }


def convention_gate_failures(
    task: dict[str, Any],
    answer: str,
    changed_paths: list[str],
    project_root: Path,
    exam_beyond: dict[str, Any] | None,
    bundled_validator: Callable[[Path, list[str]], list[str]] | None,
) -> list[str]:
    failures: list[str] = []
    if task.get("require_zero_change") and changed_paths:
        failures.append("convention exam must not mutate persistent project files")
    entry_id = task.get("entry_id")
    exam_stage = task.get("exam_stage")
    if entry_id and exam_stage:
        marker = task.get("exam_marker")
        if marker and not probe_memory_evidence(answer, marker):
            failures.append(f"exam answer did not recall the {entry_id} convention marker")
        reason_marker = task.get("exam_reason_marker")
        if reason_marker and not probe_memory_evidence(answer, reason_marker):
            failures.append(f"exam answer did not recall why {entry_id} was introduced")
        checks = LEDGER_CHECKS.get(str(entry_id), {}).get(str(exam_stage))
        if checks is not None:
            failures.extend(evaluate_entry_checks(project_root, changed_paths, checks))
        if task.get("require_beyond") and not (exam_beyond or {}).get("beyond"):
            failures.append("extraction cursor has not covered the implanted seed yet")
    if bundled_validator is not None:
        failures.extend(bundled_validator(project_root, changed_paths))
    return failures


EDGE_CASE_REPORTS = {
    "canonical envelope": "有人把 NaN 塞进 payload 后仍然序列化成功了；这里应该明确拒绝非有限浮点数",
    "append-only journal": "进程中断会留下半行记录；重新打开时要忽略未完成尾行，但不能吞掉中间损坏",
    "cursor checkpoint": "bool 现在会被当成 0/1 游标收进去；这种值应该直接拒绝",
    "idempotency registry": "只含空白的 request key 还能注册；请把它当成无效输入",
    "retry budget": "attempts 传 bool 会混进整数分支；这里需要明确挡住",
    "conflict detector": "同一条要求重复出现时会被自己判成冲突；重复项应该合并处理",
    "schema migrator": "读到比当前版本更新的数据时还会继续跑；这种未来版本要明确失败",
    "lease manager": "naive datetime 能混进过期时间；租约边界只接受带时区时间",
    "snapshot projector": "空事件流现在拿不到一个可用的初始快照；补上这个基础行为",
    "query index": "同一个键重复写入相同位置会产生重复命中；结果里应该去重且顺序稳定",
    "audit exporter": "记录里出现集合时导出顺序不稳定；请把输出固定下来",
    "policy evaluator": "未知 policy 名称现在走了默认放行；这里应该 fail closed",
    "command router": "命令前后多一个空格会绕过严格校验；空白处理要一致",
    "config loader": "配置里的 bool 会被整数选项接受；把这类类型混淆挡住",
    "checksum manifest": "Windows 换行文件算出来的 manifest 在不同机器上不一致；统一输入规范",
    "offline packager": "依赖清单里重复条目会被打包两次；去重后还要保持确定顺序",
    "UTC clock": "有人传了 bool 作为时间戳，结果被当成 0/1 秒接受了；这种输入要明确报错",
    "stable serializer": "payload 里有 NaN 时仍能产出看似合法的字节；非有限数字必须拒绝",
    "error taxonomy": "第三方异常把 terminal 写成字符串 'false' 时也会被判成终止错误；标志位只认真正的 bool",
    "metrics reducer": "重复 replay 同一个事件会把计数累加两遍；按事件标识保证幂等",
    "backup verifier": "空备份目录现在也会报告验证成功；没有可验证内容时应当失败",
    "restore planner": "源路径和目标路径相同时仍生成覆盖步骤；这种计划要拒绝",
    "capability registry": "查询最低版本时传 True 会被当成版本 1；版本参数要和注册时一样明确拒绝 bool",
    "compatibility facade": "supports(True) 现在会回答支持 v1；版本探测不能把 bool 当成整数版本",
    "release gate": "reproducible_check 返回字符串 'false' 时 gate 仍会当成通过；检查结果必须是真正的 bool",
}

INTEGRATION_REPORTS = {
    "checksum manifest": (
        "manifest 配置这边需要一个很薄的入口。请在 quarry/config_manifest.py 里公开 "
        "build_configured_manifest(data)：用 ConfigSchema 校验 data，只接受必填的 paths（list 或 dict）"
        "和默认 sha256 的 algorithm，然后直接交给 build_manifest。未知字段、缺字段继续报 ConfigError，"
        "未知算法和文件问题继续报 ManifestError；补上成功和错误传播的集成测试。"
    ),
}

PERFORMANCE_REPORTS = {
    "append-only journal": (
        "状态页会连续调用 len(journal)，现在每次都会重新打开并解析整份日志，一万条以后很明显。"
        "把完整记录数在打开时算一次，成功 append 后递增，让后续 len() 不再重扫文件；"
        "保留尾部半行不计数的现有语义，并用回归测试证明重复 len 不会再次走 read_all。"
    ),
    "lease manager": (
        "lease 的 acquire/release 每次持久化时已经有打开的句柄，却又让 dump_envelope 重新打开同一路径，"
        "随后还在旧句柄上 flush/fsync。请把 canonical 内容一次写进现有句柄并在同一句柄上落盘，"
        "保持磁盘格式和 fencing token 语义不变，再补一个关闭重开后仍能读回的回归测试。"
    ),
    "offline packager": (
        "一个 bundle 里很多文件共用同一层目录，现在 package_release 会对每个文件都重复调用 "
        "dest.parent.mkdir。请在单次打包里记住已经准备好的父目录，同一目录只创建一次；"
        "复制顺序、离线约束和 manifest 内容都保持不变，并补个能钉住重复 mkdir 次数的测试。"
    ),
    "UTC clock": (
        "批量导入里经常重复出现同一个 ISO 时间字符串，parse_utc 每次都会重新走 fromisoformat。"
        "给纯字符串解析加一个有上限的缓存，重复值复用规范化后的 UTC datetime；"
        "无效输入、naive 时间和 coerce_utc 的现有行为都不能变，并补个能看到 cache hit 的测试。"
    ),
    "backup verifier": (
        "状态轮询会反复调用同一个 BackupVerifier.verify，大 manifest 每次都重新读盘和解析，"
        "但文件内容校验本身仍然必须每次执行。请按 manifest 的 mtime_ns 和 size 缓存已解析内容，"
        "签名变化就重新加载；校验结果和异常语义保持不变，并测试未变化只加载一次、变化后会失效。"
    ),
    "compatibility facade": (
        "迁移脚本会对同一个 facade 方法连续调用上万次，现在每次都重新解析 surface 和 callable。"
        "加一个小的 call_many(name, calls, version=None) 批量入口，每项是 args/kwargs，单批只解析一次目标方法，"
        "结果顺序和逐次 call 一致，遇到无效方法或某项异常仍原样停止；补上等价性和只解析一次的测试。"
    ),
    "release gate": (
        "发布面板会用多组 tests_run 预览同一个 release gate，现在每组都会重复执行昂贵的 "
        "reproducible_check。加一个 evaluate_many(test_runs) 批量入口，一批里只做一次 reproducibility 检查，"
        "每组仍独立计算完整性并按输入顺序返回 GateResult；异常和单次 evaluate 的语义保持一致，补测试。"
    ),
}

FAILURE_RECOVERY_REPORTS = {
    "schema migrator": (
        "SchemaMigrator 的临时文件已经会 fsync 再 os.replace，但 rename 后没有同步父目录，断电时目录项仍可能丢。"
        "在支持目录 fsync 的平台上按“临时文件落盘 → replace → 父目录 fsync”的顺序完成提交；"
        "不支持时保持可用，现有原子替换和重跑幂等语义不变。用 monkeypatch 测试提交顺序和失败清理。"
    ),
    "command router": (
        "这个 router 明确是纯内存，不需要硬加文件恢复；实际问题在启动时的批量注册。"
        "如果后面的 alias 无效，前面已经注册的 command 会留下一半状态。加一个 configure_batch(commands, aliases) "
        "入口，在临时副本上完整校验后一次提交；失败时原 router 不变，成功批次原样重放应是 no-op。"
        "保留现有单项 API，并测试中途失败、成功提交和重复恢复。"
    ),
}

CHANGE_REQUEST_REPORTS = {
    "canonical envelope": (
        "事件导入要加一个明确的 v2 批量入口 build_envelopes_v2(events)。每项包含 payload、event_type，"
        "并可带 event_id/time；按输入顺序逐项复用 build_envelope，第一项无效数据就原样报错，不返回半批结果。"
        "现有 build_envelope 和全部旧导出保持不变，再提供 build_envelope_v1 兼容别名；补批量、失败和旧入口测试，"
        "README 里留一段简短迁移示例。"
    ),
}

IGNORED_PROJECT_PARTS = {".git", ".lara", ".pytest_cache", ".venv", "__pycache__"}
TRACKED_PROJECT_SUFFIXES = {".json", ".md", ".py", ".toml", ".yaml", ".yml"}
VERIFICATION_COMMAND = re.compile(
    r"(?:^|\s)(?:python(?:\.exe)?\s+-m\s+)?pytest(?:\s|$)", re.IGNORECASE
)
MEMORY_SEARCH_COMMAND = re.compile(
    r"(?:dynamic_memory_cli\.py|memory-cli)\b[^\r\n]*\bsearch\b",
    re.IGNORECASE,
)


def _late_task(
    number: int,
    phase: str,
    component: str,
    prompt: str,
    **extra: Any,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "number": number,
        "phase": phase,
        "component": component,
        "conflict_probe": False,
        "probe_marker": None,
        "allow_verified_noop": False,
        "prompt": prompt,
    }
    task.update(extra)
    return task


def build_late_tasks(seeds: dict[str, dict[str, str]] | None) -> list[dict[str, Any]]:
    """Rounds 201-500: regression scan, ledger implantation, exams, and wrap-up."""

    markers = resolved_entry_markers(seeds)
    seed_texts = {seed_id: seed["text"] for seed_id, seed in (seeds or {}).items()}
    tasks: list[dict[str, Any]] = []

    for offset in range(50):  # 201-250 regression scan: ten topics over components
        topic_name, template = MAINTENANCE_TOPICS[offset % len(MAINTENANCE_TOPICS)]
        component = COMPONENTS[offset % len(COMPONENTS)]
        tasks.append(
            _late_task(
                201 + offset,
                "regression",
                component.name,
                template.format(name=component.name),
                allow_verified_noop=topic_name in VERIFIED_NOOP_TOPICS,
            )
        )

    for offset, operation_id in enumerate(OPERATION_LEDGER_IDS[:8]):  # 251-258
        tasks.append(
            _late_task(
                251 + offset,
                "operation",
                OPERATION_COMPONENTS[operation_id],
                OPERATION_IMPLANT_PROMPTS[operation_id],
                entry_id=operation_id,
                exam_stage="implant",
            )
        )

    tasks.append(
        _late_task(
            259,
            "misc",
            "release notes",
            "把 README 的安装说明和 pyproject 的项目描述对齐一下。",
        )
    )
    tasks.append(
        _late_task(
            260,
            "misc",
            "repository docs",
            "顺手把仓库根目录的说明文件过一遍，过时的说法改掉。",
        )
    )

    for offset in range(24):  # 261-284 integration-style real work
        component = COMPONENTS[offset % len(COMPONENTS)]
        previous = COMPONENTS[(offset - 1) % len(COMPONENTS)]
        tasks.append(
            _late_task(
                261 + offset,
                "integration",
                component.name,
                (
                    f"现在把 {component.name} 和 {previous.name} 串一下，尽量复用两边已有逻辑。"
                    "加一个真正跑起来的集成测试，把顺序和失败时的行为钉住。"
                ),
            )
        )

    for offset, seed_id in enumerate(SEED_FILE_IDS[:8]):  # 285-292 S1-S8
        tasks.append(
            _late_task(
                285 + offset,
                "seed",
                f"convention {seed_id}",
                seed_texts.get(seed_id, f"[seed {seed_id}]"),
                entry_id=seed_id,
                **RELAXED_EXECUTION,
            )
        )
    for offset, seed_id in enumerate(SEED_FILE_IDS[8:]):  # 293-296 F1-F4
        tasks.append(
            _late_task(
                293 + offset,
                "seed",
                f"convention {seed_id}",
                seed_texts.get(seed_id, f"[seed {seed_id}]"),
                entry_id=seed_id,
                **RELAXED_EXECUTION,
            )
        )

    for offset, operation_id in enumerate(OPERATION_LEDGER_IDS[8:]):  # 297-300
        tasks.append(
            _late_task(
                297 + offset,
                "operation",
                OPERATION_COMPONENTS[operation_id],
                OPERATION_IMPLANT_PROMPTS[operation_id],
                entry_id=operation_id,
                exam_stage="implant",
            )
        )

    def content_exam(
        number: int, entry_id: str, stage: str, *, refusal: bool
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "entry_id": entry_id,
            "exam_stage": stage,
            "exam_marker": markers.get(entry_id),
            "require_zero_change": refusal,
            "require_beyond": True,
        }
        if refusal:
            extra.update(RELAXED_EXECUTION)
        return _late_task(
            number,
            "exam",
            CONTENT_EXAM_COMPONENTS[entry_id],
            CONTENT_EXAM_PROMPTS[entry_id][stage],
            **extra,
        )

    def operation_exam(
        number: int, entry_id: str, stage: str, *, refuse_ok: bool = False
    ) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "entry_id": entry_id,
            "exam_stage": stage,
            "exam_marker": None,
            "exam_reason_marker": (
                OPERATION_REASON_MARKERS.get(entry_id) if stage == "exam2" else None
            ),
            "require_zero_change": False,
            "require_beyond": False,
        }
        if refuse_ok:
            extra.update(RELAXED_EXECUTION)
        return _late_task(
            number,
            "exam",
            OPERATION_COMPONENTS[entry_id],
            OPERATION_EXAM_PROMPTS[entry_id][stage],
            **extra,
        )

    for offset, entry_id in enumerate(CONTENT_LEDGER_IDS):  # 341-357 exam1 content
        tasks.append(content_exam(341 + offset, entry_id, "exam1", refusal=True))
    for offset, entry_id in enumerate(OPERATION_LEDGER_IDS):  # 358-369 exam1 operations
        tasks.append(operation_exam(358 + offset, entry_id, "exam1"))

    for offset, entry_id in enumerate(OPERATION_LEDGER_IDS):  # 381-392 exam2 operations
        tasks.append(
            operation_exam(
                381 + offset, entry_id, "exam2", refuse_ok=entry_id in REFUSAL_OK_OPERATIONS
            )
        )
    for offset, entry_id in enumerate(CONTENT_LEDGER_IDS):  # 401-417 exam2 content
        refusal = "exam2" in CONTENT_REFUSAL_STAGES.get(entry_id, ())
        tasks.append(content_exam(401 + offset, entry_id, "exam2", refusal=refusal))

    for offset, entry_id in enumerate(
        CONTENT_LEDGER_IDS + OPERATION_LEDGER_IDS
    ):  # 441-469 final exams
        if entry_id in CONTENT_LEDGER_IDS:
            refusal = "final" in CONTENT_REFUSAL_STAGES.get(entry_id, ())
            tasks.append(content_exam(441 + offset, entry_id, "final", refusal=refusal))
        else:
            tasks.append(operation_exam(441 + offset, entry_id, "final"))

    assigned = {task["number"] for task in tasks}
    maintenance_slots = [number for number in range(201, 476) if number not in assigned]
    expected_maintenance = (
        set(range(301, 341))
        | set(range(370, 381))
        | set(range(393, 401))
        | set(range(418, 441))
        | set(range(470, 476))
    )
    assert set(maintenance_slots) == expected_maintenance
    for offset, number in enumerate(maintenance_slots):  # 88 maintenance rounds
        topic_name, template = MAINTENANCE_TOPICS[offset % len(MAINTENANCE_TOPICS)]
        component = COMPONENTS[(offset * 7) % len(COMPONENTS)]
        tasks.append(
            _late_task(
                number,
                "maintenance",
                component.name,
                template.format(name=component.name),
                allow_verified_noop=topic_name in VERIFIED_NOOP_TOPICS,
            )
        )

    bundled_prompts: dict[int, tuple[str, str, str | None]] = {
        476: (
            "release checklist",
            "0.5 快能发了，把发布清单列出来：环境、时间、兼容、落盘、分页这些老规矩逐条对一遍，"
            "落到 release-evidence/release-checklist-0.5.md。",
            "checklist",
        ),
        477: (
            "onboarding",
            "给新同事写一页 docs/onboarding.md，把环境怎么装、测试命令、基准数据放哪、"
            "报错要带什么、写入怎么保证原子说清楚。",
            "onboarding",
        ),
        478: ("decisions ledger", "decisions.md 收个尾，看看有没有漏记的决策，补齐。", None),
        479: (
            "pagination inventory",
            "盘点一下所有批处理接口的默认分页，确认口径一致，结论落到 release-evidence/pagination.md。",
            "pagination",
        ),
        480: (
            "durable writes",
            "把所有持久化写入点过一遍，确认都走临时文件加原子替换，有漏的就地补上。",
            "write paths",
        ),
        481: ("release gate", "把 release gate 的检查完整复跑一遍，输出留档。", None),
        482: ("test suite", "全量跑一遍 pytest，把结果留档。", None),
        483: (
            "batch schedule",
            "把下一批批量任务的排程表落盘到 release-evidence/schedule.md。",
            "schedule",
        ),
        484: (
            "release notes",
            "把 0.5 的 release notes 初稿写到 release-evidence/release-notes-0.5.md。",
            "release notes",
        ),
        485: ("change recap", "口头把这轮变更的关键点汇总一下就行，不用动文件。", None),
    }
    for number, (component, prompt, bundled) in bundled_prompts.items():
        extra: dict[str, Any] = {}
        if bundled is not None:
            extra["bundled_check"] = bundled
        if number == 485:
            extra.update(RELAXED_EXECUTION)
        if number == 480:
            extra["allow_verified_noop"] = True
        tasks.append(_late_task(number, "bundled", component, prompt, **extra))

    release_prompts: dict[int, tuple[str, str]] = {
        486: ("offline packager", "按老流程准备 0.5 的发布产物，离线要能装，复现校验要过。"),
        487: ("release evidence", "把发布证据补齐到 release-evidence/，缺什么补什么。"),
        488: ("test suite", "终局全量测试再跑一遍，绿了才算数。"),
        489: ("status report", "把当前项目状态汇总成 release-evidence/status-0.5.md。"),
        490: ("repository docs", "发布前把 README 的安装说明再顺一遍。"),
    }
    for number, (component, prompt) in release_prompts.items():
        tasks.append(_late_task(number, "release", component, prompt))

    wrap_prompts: dict[int, tuple[str, str]] = {
        491: ("README", "README 的快速上手示例再核一遍，保证能跑通。"),
        492: ("query index", "给 query index 补一个查询结果为空的边界测试。"),
        493: ("append-only journal", "给 journal 的公共函数补一行 docstring 示例。"),
        494: ("config loader", "config loader 的报错文案再核一遍，该带的建议都带上。"),
        495: ("metrics reducer", "把 metrics 公共函数的类型标注补齐。"),
        496: ("changelog", "把 CHANGELOG 里未发布的段落理一理。"),
        497: ("snapshot projector", "给 snapshot projector 补一个空事件流的边界测试。"),
        498: ("capability registry", "给 capability registry 的 docstring 补个示例。"),
        499: ("test fixtures", "把 tests 里重复的夹具收拢一下。"),
    }
    for number, (component, prompt) in wrap_prompts.items():
        tasks.append(
            _late_task(number, "wrap up", component, prompt, allow_verified_noop=True)
        )
    tasks.append(
        _late_task(
            500,
            "wrap up",
            "project status",
            "这一轮就到这，口头收个尾：项目现在到什么状态、还差什么，说清楚就行，不用改文件。",
            **RELAXED_EXECUTION,
        )
    )
    return tasks


def build_tasks(
    project_root: Path, seeds: dict[str, dict[str, str]] | None = None
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for phase in PHASES:
        for component_index, component in enumerate(COMPONENTS):
            number = len(tasks) + 1
            if number == 1:
                instruction = (
                    "我想做一个小型的事件存储包，先从事件的基础格式开始。帮我把 canonical envelope 做出来，"
                    "代码放在 quarry/canonical_envelope.py。JSON 需要稳定、键递归排序并使用 UTF-8，文件结尾只留一个换行；"
                    "时间统一用带时区的 UTC，遇到坏数据直接报错。再补一组小而完整的测试，改完跑一下相关 pytest。"
                )
            elif phase == "contract":
                instruction = (
                    f"接着把 {component.name} 这块搭起来，代码放 quarry/{component.file}。我希望它做到："
                    f"{component.contract}。API 先小一点，核心逻辑和相关测试一起补上。"
                )
                if component.name in MEMORY_SEEDS:
                    instruction += " " + MEMORY_SEEDS[component.name]
            elif phase == "edge cases":
                instruction = (
                    f"{component.name} 这边有个小反馈：{EDGE_CASE_REPORTS[component.name]}。"
                    "现有 API 别动，修好后补个能防回归的测试。如果当前实现和回归测试已经完整覆盖，"
                    "请实际读取代码、运行相关测试并明确说明，无需为了产生 diff 重写正确代码。"
                )
            elif phase == "integration":
                previous = COMPONENTS[(component_index - 1) % len(COMPONENTS)]
                instruction = INTEGRATION_REPORTS.get(
                    component.name,
                    (
                        f"现在把 {component.name} 和 {previous.name} 串一下，尽量复用两边已有逻辑。"
                        "加一个真正跑起来的集成测试，把顺序和失败时的行为钉住。"
                    ),
                )
            elif phase == "observability":
                instruction = (
                    f"{component.name} 出问题时现在不太好查。加一份稳定的诊断结果，把成功和失败原因说清楚，"
                    "但别改变原来的返回语义；测试和一个短用法也一起补上。"
                )
            elif phase == "performance":
                instruction = PERFORMANCE_REPORTS.get(
                    component.name,
                    (
                        f"{component.name} 放到一万条事件时有点慢。看下是不是有明显重复工作，做一个范围小的优化，"
                        "结果必须和现在一致，再留个回归测试。"
                    ),
                )
            elif phase == "failure recovery":
                instruction = FAILURE_RECOVERY_REPORTS.get(
                    component.name,
                    (
                        f"再把 {component.name} 的崩溃恢复补扎实一点。找最危险的提交边界，用临时文件模拟一次中断，"
                        "恢复要能重复执行，而且已经确认的数据不能丢也不能多一份。"
                    ),
                )
            elif (
                phase == "change request" and component.name in BLIND_CONFLICT_REQUESTS
            ):
                instruction = BLIND_CONFLICT_REQUESTS[component.name]
            elif phase == "change request":
                instruction = CHANGE_REQUEST_REPORTS.get(
                    component.name,
                    (
                        f"给 {component.name} 加一个 v2 的批量用法吧。老调用方现在还在用，迁移要平滑；"
                        "适配层、测试和简短迁移说明一起处理。"
                    ),
                )
            else:
                if component.name == "audit exporter":
                    instruction = (
                        "准备收尾了，audit exporter 的批量失败边界还没钉实：补上后面的 journal 已关闭、"
                        "以及后面的 record 缺 time/id 这两个回归场景，确认整批明确报错且不会给调用方"
                        "留下可用的半批结果；如果测试揭出实现问题就做最小修复。相关测试跑通后，把命令、"
                        "结果、约束是怎么组合的以及还剩什么发布阻碍写到 release-evidence/audit.md。"
                    )
                elif component.name == "UTC clock":
                    instruction = (
                        "准备收尾了，UTC clock 的输入边界还没钉实：补上 lowercase z 和首尾空白这两类"
                        "非规范 timestamp 的回归场景，确认解析入口严格拒绝；再补一个带微秒、非整小时 offset"
                        " 的 canonical round-trip。若测试揭出实现问题就做最小修复。相关测试跑通后，把命令、"
                        "结果、约束是怎么组合的以及还剩什么发布阻碍写到 release-evidence/clock.md。"
                    )
                elif component.name == "release gate":
                    instruction = (
                        "准备收尾了，release gate 的批量契约还差一组发布前回归：让 evaluate_many 同时接收"
                        "外层 one-shot generator 和每组 tests_run 的内层 one-shot generator，补上空批次，"
                        "并确认 reproducibility check 每批只执行一次。若测试揭出实现问题就做最小修复。"
                        "相关测试跑通后，把命令、结果、约束是怎么组合的以及还剩什么发布阻碍写到 "
                        "release-evidence/release_gate.md。"
                    )
                else:
                    instruction = (
                        f"准备收尾了，帮我检查一下 {component.name}，找一个真实缺陷或测试空档修掉。"
                        f"相关测试跑通后，把命令、结果、约束是怎么组合的以及还剩什么发布阻碍写到 "
                        f"release-evidence/{Path(component.file).stem}.md。"
                    )
            if phase == "performance":
                instruction += (
                    "先用代码和可重复的基准确认瓶颈；如果证据表明确实没有明显重复工作且现有测试已覆盖，"
                    "允许把这一轮作为 verified no-op，但必须给出实际读取、基准和测试证据，不要制造 diff。"
                    "完成一次可重复基准和一次成功的全量 pytest 后，如果已经足够得出结论，立即给出简洁最终答复，"
                    "不要继续调用工具。"
                )
            task = {
                "number": number,
                "phase": phase,
                "component": component.name,
                "conflict_probe": component.name in BLIND_CONFLICT_REQUESTS
                and phase == "change request",
                "probe_marker": PROBE_MARKERS.get(component.name)
                if phase == "change request"
                else None,
                "allow_verified_noop": phase in {"edge cases", "performance"},
                "prompt": instruction,
            }
            if phase == "contract" and component.name in SEED_ENTRY_FOR_COMPONENT:
                task["entry_id"] = SEED_ENTRY_FOR_COMPONENT[component.name]
            tasks.append(task)
    tasks.extend(build_late_tasks(seeds))
    tasks.sort(key=lambda task: int(task["number"]))
    validate_exam_hygiene(
        tasks, resolved_entry_markers(seeds), OPERATION_REASON_MARKERS
    )
    assert len(tasks) == 500
    return tasks


def initialize_project(project_root: Path) -> None:
    (project_root / "quarry").mkdir(parents=True, exist_ok=True)
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "quarry" / "__init__.py").write_text(
        '"""Quarry durable-workflow acceptance project."""\n', encoding="utf-8"
    )
    (project_root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "quarry-eternal-acceptance"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n\n'
        "[tool.pytest.ini_options]\n"
        'testpaths = ["tests"]\n'
        'addopts = "-q"\n',
        encoding="utf-8",
    )
    (project_root / "README.md").write_text(
        "# Quarry\n\n"
        "A small durable-workflow package that is being evolved through normal development requests.\n",
        encoding="utf-8",
    )


def project_manifest(project_root: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in sorted(project_root.rglob("*")):
        if not path.is_file() or any(
            part in IGNORED_PROJECT_PARTS
            for part in path.relative_to(project_root).parts
        ):
            continue
        if path.suffix.casefold() not in TRACKED_PROJECT_SUFFIXES:
            continue
        manifest[path.relative_to(project_root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return manifest


def changed_project_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def capture_project_baseline(project_root: Path) -> dict[str, bytes]:
    return {
        path: (project_root / path).read_bytes()
        for path in project_manifest(project_root)
    }


def restore_project_baseline(project_root: Path, baseline: dict[str, bytes]) -> None:
    current_paths = set(project_manifest(project_root))
    for relative in current_paths - set(baseline):
        (project_root / relative).unlink()
    for relative, content in baseline.items():
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    text = read_jsonl_snapshot(path)
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Event stores append JSONL concurrently with the harness reads, so
            # a torn record can appear at any position, not only at the tail.
            # Skip it: losing one torn record is better than killing a
            # long-running acceptance pass; corruption of intact records is
            # still surfaced by their own parse errors.
            continue
    return rows


def consecutive_passed_prefix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix: list[dict[str, Any]] = []
    for row in rows:
        expected_number = len(prefix) + 1
        if (
            row.get("status") != "passed"
            or int(row.get("number") or 0) != expected_number
        ):
            break
        prefix.append(row)
    return prefix


@contextmanager
def exclusive_run_lock(run_root: Path):
    """Hold a process-level lock so one acceptance run has one writer."""

    path = run_root / ".acceptance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            f"acceptance run already has an active writer: {run_root}"
        ) from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def task_tool_evidence(run_dir: Path) -> dict[str, Any]:
    calls = load_jsonl(run_dir / "tool_calls.jsonl")
    results = load_jsonl(run_dir / "tool_results.jsonl")
    successful_results = {
        str(row.get("tool_call_id")): row
        for row in results
        if row.get("status") == "success" and row.get("tool_call_id")
    }
    successful_ids = set(successful_results)
    successful_calls = [
        row for row in calls if str(row.get("event_id")) in successful_ids
    ]
    verification_calls = []
    memory_search_calls = []
    for row in successful_calls:
        command = str((row.get("args") or {}).get("command") or "")
        result = str(successful_results[str(row.get("event_id"))].get("result") or "")
        if row.get("tool_name") == "bash" and MEMORY_SEARCH_COMMAND.search(command):
            memory_search_calls.append(row)
        if (
            row.get("tool_name") == "bash"
            and VERIFICATION_COMMAND.search(command)
            and "--version" not in command.casefold()
            and result.lstrip().startswith("exit_code: 0")
            and (
                re.search(r"\b\d+ passed\b", result, re.IGNORECASE)
                or "[100%]" in result
            )
            and not re.search(r"\b[1-9]\d* (?:failed|errors?)\b", result, re.IGNORECASE)
        ):
            verification_calls.append(row)
    return {
        "tool_call_count": len(calls),
        "successful_tool_call_count": len(successful_calls),
        "tool_names": sorted({str(row.get("tool_name")) for row in successful_calls}),
        "verification_commands": [
            str((row.get("args") or {}).get("command") or "")
            for row in verification_calls
        ],
        "has_successful_verification": bool(verification_calls),
        "memory_search_commands": [
            str((row.get("args") or {}).get("command") or "")
            for row in memory_search_calls
        ],
        "memory_search_count": len(memory_search_calls),
    }


def verified_noop_allowed(
    task: dict[str, Any],
    *,
    changed_paths: list[str],
    tool_evidence: dict[str, Any],
) -> bool:
    """Accept a verified edge-case request that the existing code already satisfies."""

    return bool(
        task.get("allow_verified_noop") is True
        and not changed_paths
        and int(tool_evidence.get("successful_tool_call_count") or 0) >= 2
        and tool_evidence.get("has_successful_verification")
        and "read" in set(tool_evidence.get("tool_names") or ())
    )


def mark_run_crashed(run_root: Path, error: BaseException) -> None:
    path = run_root / "run.json"
    if not path.exists():
        return
    metadata = read_json(path)
    metadata.update(
        {
            "finished_at": utc_now(),
            "status": "failed",
            "failure": f"{type(error).__name__}: {error}",
        }
    )
    write_json_atomic(path, metadata)


async def recover_memory_backlog(
    runtime: LaraRuntimeService,
    manager: SessionManager,
    session_ref: SessionRef,
    *,
    max_uncovered_messages: int,
    recovery_attempts: int,
) -> dict[str, Any]:
    """Boundedly resume only the failed background memory job until caught up."""

    session_dir = Path(session_ref.session_dir)
    for recovery_attempt in range(recovery_attempts + 1):
        await runtime.memory_harness.wait_idle()
        session = manager.load(session_ref.session_id)
        projection = load_projection(session_dir)
        uncovered = len(session.history) - int(projection.get("covered_through") or 0)
        if uncovered <= max_uncovered_messages:
            return projection
        if recovery_attempt == recovery_attempts:
            raise RuntimeError(
                "memory projection remains behind after "
                f"{recovery_attempts} background recovery attempts: "
                f"uncovered={uncovered}, allowed={max_uncovered_messages}"
            )
        scheduled = await runtime.memory_harness.retry_pending(session)
        if not scheduled:
            raise RuntimeError(
                f"memory projection is behind by {uncovered} messages but no retry was scheduled"
            )
    raise AssertionError("unreachable memory recovery state")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SESSION_ROLLBACK_FILES = (
    "session.json",
    "context/history.jsonl",
    "context/conversation-checkpoints.sqlite3",
    "context/conversation-checkpoints.sqlite3-wal",
    "context/conversation-checkpoints.sqlite3-shm",
    "raw-history/events.jsonl",
    "state/eternal-conversation.json",
    "state/eternal-harness.json",
    "memory/memory.sqlite3",
    "memory/memory.sqlite3-wal",
    "memory/memory.sqlite3-shm",
)


def capture_session_baseline(session_dir: Path) -> dict[str, bytes | None]:
    return {
        relative: (path.read_bytes() if path.is_file() else None)
        for relative in SESSION_ROLLBACK_FILES
        for path in (session_dir / relative,)
    }


def restore_session_baseline(
    session_dir: Path, baseline: dict[str, bytes | None]
) -> None:
    for relative, content in baseline.items():
        path = session_dir / relative
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


ACTIVE_TASK_BASELINE = "active-task-baseline.zip"


def persist_task_baseline(
    run_root: Path,
    *,
    task_number: int,
    project_baseline: dict[str, bytes],
    session_baseline: dict[str, bytes | None],
) -> None:
    """Atomically persist the rollback boundary for the currently active task."""

    target = run_root / ACTIVE_TASK_BASELINE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    metadata = {
        "task_number": task_number,
        "project_paths": sorted(project_baseline),
        "session_paths": {
            relative: content is not None
            for relative, content in session_baseline.items()
        },
    }
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            )
            for relative, content in project_baseline.items():
                archive.writestr(f"project/{relative}", content)
            for relative, content in session_baseline.items():
                if content is not None:
                    archive.writestr(f"session/{relative}", content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def restore_active_task_baseline(
    run_root: Path,
    *,
    expected_task_number: int,
    project_root: Path,
    session_dir: Path,
) -> bool:
    """Restore an interrupted task before resuming its acceptance run."""

    path = run_root / ACTIVE_TASK_BASELINE
    if not path.exists():
        return False
    stale_committed_baseline = False
    with zipfile.ZipFile(path, "r") as archive:
        metadata = json.loads(archive.read("metadata.json"))
        task_number = int(metadata["task_number"])
        if task_number < expected_task_number:
            stale_committed_baseline = True
        elif task_number != expected_task_number:
            raise RuntimeError(
                f"active task baseline is for task {task_number}, expected {expected_task_number}"
            )
        else:
            project_paths = set(metadata["project_paths"])
            current_paths = set(capture_project_baseline(project_root))
            for relative in current_paths - project_paths:
                (project_root / relative).unlink()
            for relative in project_paths:
                target = project_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(f"project/{relative}"))
            for relative, exists in metadata["session_paths"].items():
                target = session_dir / relative
                if not exists:
                    target.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(f"session/{relative}"))
    path.unlink()
    return not stale_committed_baseline


def clear_active_task_baseline(run_root: Path) -> None:
    (run_root / ACTIVE_TASK_BASELINE).unlink(missing_ok=True)


def question_evidence(answer: str) -> bool:
    text = answer.strip()
    if not text:
        return False
    # A clarification must be the foreground's final hand-off, not a question
    # buried in chain-of-thought-like analysis that never actually yields.
    return bool(re.search(r"[?？][\s*_`'\"）)\]]*$", text))


def build_retry_message(task_prompt: str) -> str:
    return (
        "刚才这件事没有完成，工作区和会话已经恢复到尝试前。请重新独立处理下面这件事，"
        "先核对相关实现和既有约束，不要沿用上一轮的推断。Raw History 会保留失败尝试的"
        "审计证据，其中的完成声明不代表当前工作区状态；请以当前文件为准完成任务。"
        "如果任务点名的 helper 或公共 API 尚不存在，但已经说明了所需行为，补充最小兼容实现"
        "属于本任务范围，不要仅因为符号缺失而反问。"
        "这是实现任务：必须使用工具把范围内的改动持久化并运行相关验证，不要用设计说明代替执行。"
        "除非原任务明确允许 verified no-op，否则检查后必须选择满足要求的最小可靠改动，不能以当前实现已经很快或足够好为由无改动结束。"
        "一旦已有足够的改动、基准和测试证据，立即给出简洁最终答复，不要在没有具体剩余检查时继续调用工具。"
        "若原任务明确允许 verified no-op 且当前实现和测试已完整满足要求，请核验证据后明确说明，不要制造无意义改动：\n\n"
        + task_prompt
    )


def probe_memory_evidence(answer: str, marker: str | None) -> bool:
    return bool(marker and marker.casefold() in answer.casefold())


def fresh_session_evidence(
    manager: SessionManager, session_ref: SessionRef
) -> dict[str, Any]:
    session_dir = Path(session_ref.session_dir)
    session = manager.load(session_ref.session_id)
    projection = load_projection(session_dir)
    evidence = {
        "history_messages": len(session.history),
        "memory_database_exists": (session_dir / "memory" / "memory.sqlite3").exists(),
        "raw_history_exists": (session_dir / "raw-history" / "events.jsonl").exists(),
        "memory_revision": int(projection.get("memory_revision") or 0),
        "snapshot_revision": int(projection.get("snapshot_revision") or 0),
        "covered_through": int(projection.get("covered_through") or 0),
    }
    evidence["clean"] = not any(
        (
            evidence["history_messages"],
            evidence["memory_database_exists"],
            evidence["raw_history_exists"],
            evidence["memory_revision"],
            evidence["snapshot_revision"],
            evidence["covered_through"],
        )
    )
    return evidence


async def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    interpreter_bin = str(Path(sys.executable).resolve().parent)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if os.path.normcase(interpreter_bin) not in {
        os.path.normcase(str(Path(entry).resolve())) for entry in path_entries if entry
    }:
        os.environ["PATH"] = os.pathsep.join((interpreter_bin, *path_entries))
    config = load_run_config(
        workspace_root=str(workspace),
        agent_alias=args.agent_alias,
        max_steps=args.max_steps,
    )
    if not config.eternal_conversation.enabled:
        raise RuntimeError("eternal_conversation must be enabled")
    config.eternal_conversation.extraction_trigger = args.extraction_trigger
    if args.extraction_trigger_ratio is not None:
        config.eternal_conversation.extraction_trigger_ratio = (
            args.extraction_trigger_ratio
        )
    if args.context_window is not None:
        config.context_window = args.context_window
    if args.memory_cli:
        os.environ["LARA_DYNAMIC_MEMORY_CLI"] = str(Path(args.memory_cli).resolve())
    if args.foreground_model:
        raise RuntimeError(
            "--foreground-model is no longer supported: model resolution moved to "
            "agent definitions and model connections; override them in config instead"
        )

    resume_rows: list[dict[str, Any]] = []
    fresh_history: dict[str, Any] | None = None
    if args.resume_run:
        run_root = Path(args.resume_run).resolve()
        run_meta = read_json(run_root / "run.json")
        project_root = Path(run_meta["project_root"]).resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_root = (
            Path(config.lara_root) / "acceptance-runs" / f"eternal-blind-500-{stamp}"
        )
        project_root = workspace / "workspace" / f"eternal-blind-acceptance-500-{stamp}"
        project_root.mkdir(parents=True, exist_ok=False)
        (project_root / ".acceptance-root").write_text(
            "Fresh Quarry blind eternal-conversation acceptance project root.\n",
            encoding="utf-8",
        )
        initialize_project(project_root)
        # Foreground and background Agents resolve the same user-level Lara
        # configuration independently for this disposable workspace. Never copy
        # user configuration or credentials into the acceptance project.
    # Keep the fresh Session inside the disposable project so the foreground can
    # follow the advertised Raw History path without gaining read access to the
    # real repository or any older acceptance Session.
    config.workspace_root = str(project_root)
    config.lara_root = str(project_root / ".lara")
    config.allow_read_outside_workspace = False
    runtime_root = project_root / ".lara" / "runtime"
    config.runtime_durability.history_path = str(runtime_root / "executions.sqlite3")
    config.runtime_capacity.coordinator_path = str(runtime_root / "capacity.sqlite3")
    seeds: dict[str, dict[str, str]] | None = None
    if args.seeds_file:
        seeds = validate_seeds_payload(
            json.loads(Path(args.seeds_file).read_text(encoding="utf-8"))
        )
    tasks = build_tasks(project_root, seeds=seeds)[: args.limit]
    if seeds is None and any(task.get("entry_id") in SEED_FILE_IDS for task in tasks):
        raise RuntimeError(
            "--seeds-file with the S1-S8/F1-F4 seed texts is required once the run reaches round 285"
        )
    codename_markers = {
        entry_id: marker
        for entry_id, marker in resolved_entry_markers(seeds).items()
        if entry_id != "E5"
    }
    bundled_checks = build_bundled_checks(codename_markers)
    ledger_positions: dict[str, int] = {}
    if args.resume_run:
        stored_positions = run_root / "ledger-positions.json"
        if stored_positions.exists():
            ledger_positions = {
                str(key): int(value)
                for key, value in read_json(stored_positions).items()
            }

    manager = SessionManager(config)
    if args.resume_run:
        run_meta = read_json(run_root / "run.json")
        session_ref = SessionRef(
            session_id=str(run_meta["session_id"]),
            session_dir=str(run_meta["session_dir"]),
            workspace_root=str(project_root),
        )
        all_previous_rows = load_jsonl(run_root / "progress.jsonl")
        resume_rows = consecutive_passed_prefix(all_previous_rows)
        restore_active_task_baseline(
            run_root,
            expected_task_number=len(resume_rows) + 1,
            project_root=project_root,
            session_dir=Path(session_ref.session_dir),
        )
        interrupted_rows = all_previous_rows[len(resume_rows) :]
        for row in interrupted_rows:
            append_jsonl(run_root / "interrupted-tasks.jsonl", row)
        (run_root / "progress.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in resume_rows
            ),
            encoding="utf-8",
        )
        # Keep the evidence manifest aligned with the prompts actually used
        # after a resumable acceptance scenario is refined.
        write_json(run_root / "tasks.json", {"tasks": tasks})
    else:
        write_json(run_root / "tasks.json", {"tasks": tasks})
        session_ref = manager.create("eternal-blind-500", mode="chat")
        fresh_history = fresh_session_evidence(manager, session_ref)
        if not fresh_history["clean"]:
            raise RuntimeError(
                f"blind acceptance session was not empty: {fresh_history}"
            )
    runtime = LaraRuntimeService(config)
    await runtime.initialize()
    if args.resume_run:
        run_meta = read_json(run_root / "run.json")
        run_meta.update(
            {
                "status": "running",
                "failure": None,
                "resumed_at": utc_now(),
                "resume_count": int(run_meta.get("resume_count") or 0) + 1,
                "max_steps": config.max_steps,
                "task_attempts": args.task_attempts,
                "memory_recovery_attempts": args.memory_recovery_attempts,
            }
        )
        write_json(run_root / "run.json", run_meta)
    else:
        assert fresh_history is not None
        write_json(
            run_root / "run.json",
            {
                "started_at": utc_now(),
                "status": "running",
                "session_id": session_ref.session_id,
                "session_dir": session_ref.session_dir,
                "project_root": str(project_root),
                "foreground_alias": config.resolved_agent.alias
                if config.resolved_agent
                else None,
                "foreground_model_group": (
                    config.resolved_agent.default_model_group
                    if config.resolved_agent
                    else None
                ),
                "extractor_alias": config.eternal_conversation.extractor_agent_alias,
                "builder_alias": config.eternal_conversation.builder_agent_alias,
                "max_steps": config.max_steps,
                "task_attempts": args.task_attempts,
                "memory_recovery_attempts": args.memory_recovery_attempts,
                "fresh_history": fresh_history,
                "scenario": "conversational-blind-memory-500",
                "extraction_trigger": config.eternal_conversation.extraction_trigger,
                "context_window": config.context_window,
            },
        )

    passed = len(resume_rows)
    failure: str | None = None
    try:
        for task in tasks[len(resume_rows) :]:
            index = int(task["number"])
            session_dir = Path(session_ref.session_dir)
            try:
                await recover_memory_backlog(
                    runtime,
                    manager,
                    session_ref,
                    max_uncovered_messages=args.max_uncovered_messages,
                    recovery_attempts=args.memory_recovery_attempts,
                )
            except Exception as exc:
                failure = f"memory recovery before task {index} failed: {type(exc).__name__}: {exc}"
                break
            before = load_projection(session_dir)
            exam_beyond: dict[str, Any] | None = None
            if task.get("entry_id") and task.get("exam_stage") in (
                "exam1",
                "exam2",
                "final",
            ):
                exam_beyond = exam_beyond_record(
                    str(task["entry_id"]),
                    str(task["exam_stage"]),
                    index,
                    ledger_positions.get(str(task["entry_id"])),
                    int(before.get("covered_through") or 0),
                )
                append_jsonl(
                    run_root / "beyond-cursor.jsonl",
                    {"recorded_at": utc_now(), **exam_beyond},
                )
            manifest_before = project_manifest(project_root)
            task_baseline = capture_project_baseline(project_root)
            session_baseline = capture_session_baseline(session_dir)
            persist_task_baseline(
                run_root,
                task_number=index,
                project_baseline=task_baseline,
                session_baseline=session_baseline,
            )
            started = time.monotonic()
            status = "error"
            answer = ""
            error: str | None = None
            attempts: list[dict[str, Any]] = []
            tool_evidence = {
                "tool_call_count": 0,
                "successful_tool_call_count": 0,
                "tool_names": [],
                "verification_commands": [],
                "has_successful_verification": False,
                "memory_search_commands": [],
                "memory_search_count": 0,
            }
            changed_paths: list[str] = []
            gate_failures: list[str] = []
            verified_noop = False
            for attempt in range(1, args.task_attempts + 1):
                run_ref = manager.start_case_run(
                    session_ref.session_id,
                    f"acceptance-{index:03d}-attempt-{attempt}",
                    run_config=config,
                )
                attempt_status = "error"
                attempt_error: str | None = None
                attempt_traceback: str | None = None
                if attempt == 1:
                    message = str(task["prompt"])
                else:
                    message = build_retry_message(str(task["prompt"]))
                try:
                    handle = await runtime.start_turn(
                        manager=manager,
                        message=message,
                        run_ref=run_ref,
                        turn_id=f"turn-{index:04d}-attempt-{attempt}",
                        interactive_approvals=False,
                        deadline=time.monotonic() + args.turn_timeout,
                    )
                    output, _ = await handle.result()
                    output_data = thaw_json(output.data)
                    result = (
                        dict(output_data.get("result") or {})
                        if isinstance(output_data, dict)
                        else {}
                    )
                    attempt_status = str(result.get("status") or "error")
                    answer = str(result.get("final_answer") or "")
                    attempt_error = result.get("error")
                except Exception as exc:  # retain exact failed-attempt evidence
                    attempt_traceback = traceback.format_exc()
                    kind = getattr(exc, "kind", None)
                    attempt_error = f"{type(exc).__name__}: {exc}"
                    if kind is not None:
                        attempt_error += f" (kind={getattr(kind, 'value', kind)})"

                attempt_tools = task_tool_evidence(Path(run_ref.run_dir))
                tool_evidence["tool_call_count"] += attempt_tools["tool_call_count"]
                tool_evidence["successful_tool_call_count"] += attempt_tools[
                    "successful_tool_call_count"
                ]
                tool_evidence["tool_names"] = sorted(
                    set(tool_evidence["tool_names"]) | set(attempt_tools["tool_names"])
                )
                tool_evidence["verification_commands"].extend(
                    attempt_tools["verification_commands"]
                )
                tool_evidence["memory_search_commands"].extend(
                    attempt_tools["memory_search_commands"]
                )
                tool_evidence["memory_search_count"] += attempt_tools[
                    "memory_search_count"
                ]
                tool_evidence["has_successful_verification"] = bool(
                    tool_evidence["has_successful_verification"]
                    or attempt_tools["has_successful_verification"]
                )
                changed_paths = changed_project_paths(
                    manifest_before, project_manifest(project_root)
                )
                verified_noop = verified_noop_allowed(
                    task,
                    changed_paths=changed_paths,
                    tool_evidence=tool_evidence,
                )
                gate_failures = []
                if (
                    task.get("requires_tools", True)
                    and tool_evidence["successful_tool_call_count"] < 2
                ):
                    gate_failures.append("fewer than two successful real tool calls")
                if (
                    task.get("requires_pytest", True)
                    and not task["conflict_probe"]
                    and not tool_evidence["has_successful_verification"]
                ):
                    gate_failures.append(
                        "no successful pytest command executed through the bash tool"
                    )
                if task["conflict_probe"]:
                    if changed_paths:
                        gate_failures.append(
                            "conflict probe modified persistent project files"
                        )
                    if not question_evidence(answer):
                        gate_failures.append(
                            "conflict probe did not ask a natural clarification question"
                        )
                    if not probe_memory_evidence(answer, task.get("probe_marker")):
                        gate_failures.append(
                            "conflict probe did not recover the hidden conversational marker"
                        )
                else:
                    if task.get("require_zero_change") and changed_paths:
                        gate_failures.append(
                            "convention exam must not mutate persistent project files"
                        )
                    if (
                        task.get("requires_changes", True)
                        and not changed_paths
                        and not verified_noop
                    ):
                        gate_failures.append(
                            "no persistent project source, test, or evidence file changed"
                        )
                gate_failures.extend(
                    convention_gate_failures(
                        task,
                        answer,
                        changed_paths,
                        project_root,
                        exam_beyond,
                        bundled_checks.get(index),
                    )
                )
                status = attempt_status
                error = attempt_error
                if status == "passed" and gate_failures:
                    status = "failed"
                    error = "; ".join(gate_failures)
                if status == "passed":
                    try:
                        await runtime.memory_harness.wait_idle()
                    except Exception as exc:
                        status = "error"
                        error = (
                            "eternal-conversation background attempt failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
                manager.finish_case_run(
                    run_ref,
                    status
                    if status in {"passed", "failed", "error", "skipped"}
                    else "error",
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "case_run_id": run_ref.case_run_id,
                        "status": status,
                        "error": error,
                        "traceback": attempt_traceback,
                        "tool_evidence": attempt_tools,
                        "changed_project_paths": changed_paths,
                        "verified_noop": verified_noop,
                    }
                )
                append_jsonl(
                    run_root / "attempts-live.jsonl",
                    {
                        "recorded_at": utc_now(),
                        "task_number": index,
                        "attempt": attempt,
                        "case_run_id": run_ref.case_run_id,
                        "status": status,
                        "error": error,
                        "traceback": attempt_traceback,
                        "gate_failures": gate_failures,
                        "changed_project_paths": changed_paths,
                        "verified_noop": verified_noop,
                        "tool_evidence": attempt_tools,
                    },
                )
                if status == "passed":
                    break
                try:
                    await runtime.memory_harness.wait_idle()
                except Exception:
                    # A failed background task was already captured in this attempt's
                    # status. Cleanup must still reach the transactional rollback.
                    pass
                restore_project_baseline(project_root, task_baseline)
                restore_session_baseline(session_dir, session_baseline)

            session_after = manager.load(session_ref.session_id)
            after = load_projection(session_dir)
            if status == "passed" and task.get("entry_id"):
                ledger_positions[str(task["entry_id"])] = max(
                    ledger_positions.get(str(task["entry_id"]), 0),
                    len(session_after.history),
                )
                write_json(run_root / "ledger-positions.json", ledger_positions)
            barrier_waited = False
            uncovered = len(session_after.history) - int(
                after.get("covered_through") or 0
            )
            if status == "passed" and uncovered > args.max_uncovered_messages:
                barrier_waited = True
                try:
                    after = await recover_memory_backlog(
                        runtime,
                        manager,
                        session_ref,
                        max_uncovered_messages=args.max_uncovered_messages,
                        recovery_attempts=args.memory_recovery_attempts,
                    )
                except Exception as exc:
                    status = "error"
                    error = f"background memory recovery failed: {type(exc).__name__}: {exc}"
                    after = load_projection(session_dir)
            record = {
                **task,
                "started_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "status": status,
                "error": error,
                "answer": answer,
                "question_evidence": question_evidence(answer),
                "probe_memory_evidence": probe_memory_evidence(
                    answer, task.get("probe_marker")
                ),
                "tool_evidence": tool_evidence,
                "changed_project_paths": changed_paths,
                "verified_noop": verified_noop,
                "execution_gate_failures": gate_failures,
                "attempts": attempts,
                "memory_barrier_waited": barrier_waited,
                "projection_before": {
                    key: before.get(key)
                    for key in (
                        "memory_revision",
                        "snapshot_revision",
                        "covered_through",
                    )
                },
                "projection_after": {
                    key: after.get(key)
                    for key in (
                        "memory_revision",
                        "snapshot_revision",
                        "covered_through",
                    )
                },
                "history_messages": len(session_after.history),
                "exam_beyond": exam_beyond,
            }
            append_jsonl(run_root / "progress.jsonl", record)
            if status == "passed":
                clear_active_task_baseline(run_root)
            heartbeat = {
                "updated_at": utc_now(),
                "completed": index,
                "passed": passed + (1 if status == "passed" else 0),
                "last_status": status,
                "last_component": task["component"],
                "tool_calls": tool_evidence["tool_call_count"],
                "changed_files": len(changed_paths),
                "snapshot_revision": after.get("snapshot_revision", 0),
                "covered_through": after.get("covered_through", 0),
            }
            write_json(
                run_root / "heartbeat.json",
                heartbeat,
            )
            print(json.dumps({"heartbeat": heartbeat}, ensure_ascii=False), flush=True)
            if status != "passed":
                failure = f"task {index} failed: {error or status}"
                break
            passed += 1
        if failure is None:
            try:
                await recover_memory_backlog(
                    runtime,
                    manager,
                    session_ref,
                    max_uncovered_messages=0,
                    recovery_attempts=args.memory_recovery_attempts,
                )
            except Exception as exc:
                failure = f"final memory recovery failed: {type(exc).__name__}: {exc}"
    except BaseException as exc:
        mark_run_crashed(run_root, exc)
        raise
    finally:
        await runtime.close(cancel=False)

    session_dir = Path(session_ref.session_dir)
    projection = load_projection(session_dir)
    rows = [
        json.loads(line)
        for line in (run_root / "progress.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    conflict_rows = [row for row in rows if row.get("conflict_probe")]
    memory_db = session_dir / "memory" / "memory.sqlite3"
    states: dict[str, int] = {}
    if memory_db.exists():
        with sqlite3.connect(memory_db) as connection:
            states = dict(
                connection.execute(
                    "SELECT build_state,COUNT(*) FROM uts GROUP BY build_state"
                ).fetchall()
            )
    successful_by_component = {
        str(row["component"]): row
        for row in conflict_rows
        if question_evidence(str(row.get("answer") or ""))
        and probe_memory_evidence(str(row.get("answer") or ""), row.get("probe_marker"))
    }
    supplemental_path = run_root / "supplemental-compat-probe.json"
    supplemental = read_json(supplemental_path) if supplemental_path.exists() else {}
    if (
        supplemental.get("accepted")
        and supplemental.get("component") == "compatibility facade"
        and question_evidence(str(supplemental.get("answer") or ""))
        and probe_memory_evidence(str(supplemental.get("answer") or ""), "0.4")
    ):
        successful_by_component["compatibility facade"] = supplemental
    successful_conflicts = list(successful_by_component.values())
    real_tool_rows = [
        row
        for row in rows
        if int((row.get("tool_evidence") or {}).get("successful_tool_call_count") or 0)
        >= 2
    ]
    verified_rows = [
        row
        for row in rows
        if (row.get("tool_evidence") or {}).get("has_successful_verification")
    ]
    changed_rows = [row for row in rows if row.get("changed_project_paths")]
    verified_noop_rows = [row for row in rows if row.get("verified_noop")]
    clean_conflicts = [
        row for row in conflict_rows if not row.get("changed_project_paths")
    ]
    memory_search_rows = [
        row
        for row in rows
        if int((row.get("tool_evidence") or {}).get("memory_search_count") or 0) > 0
    ]
    memory_search_count = sum(
        int((row.get("tool_evidence") or {}).get("memory_search_count") or 0)
        for row in rows
    )
    conflict_memory_search_rows = [
        row for row in conflict_rows if row in memory_search_rows
    ]
    full_suite_path = run_root / "final-project-pytest.txt"
    full_suite = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=args.final_test_timeout,
        check=False,
    )
    full_suite_path.write_text(
        f"exit_code={full_suite.returncode}\n\nSTDOUT\n{full_suite.stdout}\n\nSTDERR\n{full_suite.stderr}\n",
        encoding="utf-8",
    )
    retention = retention_summary(rows)
    beyond_flags: dict[tuple[str, str], bool] = {}
    for row in rows:
        if (
            row.get("entry_id")
            and row.get("exam_stage") in ("exam1", "exam2", "final")
            and isinstance(row.get("exam_beyond"), dict)
        ):
            beyond_flags[(str(row["entry_id"]), str(row["exam_stage"]))] = bool(
                row["exam_beyond"].get("beyond")
            )
    beyond_failures = sorted(
        f"{entry_id}/{stage}"
        for (entry_id, stage), covered in beyond_flags.items()
        if not covered and entry_id in CONTENT_LEDGER_IDS
    )
    content_beyond_total = sum(
        1 for (entry_id, _stage) in beyond_flags if entry_id in CONTENT_LEDGER_IDS
    )
    content_markers = {
        entry_id: marker
        for entry_id, marker in resolved_entry_markers(seeds).items()
        if entry_id in CONTENT_LEDGER_IDS
    }
    memory_cli = resolve_memory_cli(args.memory_cli)
    if memory_cli is not None:
        marker_results = search_memory_markers(session_dir, content_markers, memory_cli)
    else:
        marker_results = {
            entry_id: {
                "marker": marker,
                "hit": False,
                "exit_code": None,
                "detail": "dynamic-memory-cli not found",
            }
            for entry_id, marker in content_markers.items()
        }
    write_json(run_root / "memory-marker-search.json", marker_results)
    marker_misses = sorted(
        entry_id for entry_id, result in marker_results.items() if not result["hit"]
    )
    codename_leaks = scan_project_for_codenames(project_root, codename_markers)
    prompt_budget: dict[str, Any] | None = None
    if args.context_window:
        late_prompt_sizes = sorted(
            len(str(task["prompt"])) for task in tasks if int(task["number"]) > 400
        )
        if late_prompt_sizes:
            p95_size = late_prompt_sizes[
                min(len(late_prompt_sizes) - 1, int(len(late_prompt_sizes) * 0.95))
            ]
            budget_chars = int(args.prompt_budget_ratio * args.context_window * 2)
            prompt_budget = {
                "p95_chars": p95_size,
                "budget_chars": budget_chars,
                "within": p95_size <= budget_chars,
            }
    tool_expected_rows = sum(1 for row in rows if row.get("requires_tools", True))
    pytest_expected_rows = sum(1 for row in rows if row.get("requires_pytest", True))
    change_expected_rows = sum(1 for row in rows if row.get("requires_changes", True))
    proof = {
        "completed_tasks": len(rows),
        "passed_tasks": passed,
        "required_tasks": len(tasks),
        "retention": retention,
        "beyond_cursor": {"gated": len(beyond_flags), "failures": beyond_failures},
        "memory_marker_search": {
            "total": len(content_markers),
            "misses": marker_misses,
        },
        "codename_leaks": codename_leaks,
        "prompt_budget": prompt_budget,
        "final_projection": {
            key: projection.get(key)
            for key in ("memory_revision", "snapshot_revision", "covered_through")
        },
        "ut_states": states,
        "conflict_probes": len(conflict_rows),
        "blind_memory_probes": len(conflict_rows),
        "blind_memory_probes_passed": len(successful_conflicts),
        "supplemental_probe_rechecks": 1 if supplemental.get("accepted") else 0,
        "tasks_with_real_tools": len(real_tool_rows),
        "tasks_with_successful_pytest": len(verified_rows),
        "tasks_with_persistent_changes": len(changed_rows),
        "tasks_verified_as_already_satisfied": len(verified_noop_rows),
        "clean_conflict_probes": len(clean_conflicts),
        "foreground_memory_searches": memory_search_count,
        "tasks_using_memory_search": len(memory_search_rows),
        "blind_probes_using_memory_search": len(conflict_memory_search_rows),
        "previous_acceptance_memory_searches": 1,
        "final_project_pytest_exit_code": full_suite.returncode,
        "final_project_pytest_output": str(full_suite_path),
        "project_root": str(project_root),
        "raw_history": str(session_dir / "raw-history" / "events.jsonl"),
        "foreground_history": str(
            session_dir / "agent-history" / "foreground" / "conversation.jsonl"
        ),
        "extractor_history": str(
            session_dir / "agent-history" / "extractor" / "conversation.jsonl"
        ),
        "builder_history": str(
            session_dir / "agent-history" / "builder" / "conversation.jsonl"
        ),
        "session_history": str(session_dir / "session.json"),
    }
    accepted = (
        failure is None
        and passed == len(tasks)
        and int(projection.get("snapshot_revision") or 0) >= 2
        and int(projection.get("covered_through") or 0) > 0
        and int(projection.get("covered_through") or 0)
        == len(manager.load(session_ref.session_id).history)
        and states.get("pending", 0) == 0
        and len(successful_conflicts) == len(BLIND_CONFLICT_REQUESTS)
        and len(real_tool_rows) >= tool_expected_rows
        and len(verified_rows) >= pytest_expected_rows
        and len(changed_rows) + len(verified_noop_rows) >= change_expected_rows
        and len(clean_conflicts) == len(BLIND_CONFLICT_REQUESTS)
        and full_suite.returncode == 0
        and retention["content_passed"] == retention["content_total"]
        and retention["operation_passed"] == retention["operation_total"]
        and not beyond_failures
        and not marker_misses
        and not codename_leaks
        and (prompt_budget is None or prompt_budget["within"])
    )
    write_json(run_root / "proof.json", {**proof, "accepted": accepted})
    excerpts = "\n\n".join(
        f"### Task {row['number']}: {row['component']}\n\n{_proof_answer_excerpt(row['answer'])}"
        for row in successful_conflicts
    )
    (run_root / "PROOF.md").write_text(
        "# Conversational Blind Eternal Conversation Acceptance Proof\n\n"
        f"- Result: {'PASS' if accepted else 'FAIL'}\n"
        f"- Passed tasks: {passed}/{len(tasks)}\n"
        f"- Content retention: {retention['content_passed']}/{retention['content_total']}\n"
        f"- Operation retention: {retention['operation_passed']}/{retention['operation_total']}\n"
        f"- Beyond-cursor content exams: {content_beyond_total - len(beyond_failures)}/{content_beyond_total}\n"
        f"- Memory marker search hits: {len(content_markers) - len(marker_misses)}/{len(content_markers)}\n"
        f"- Codename leaks in project files: {len(codename_leaks)}\n"
        f"- Session: `{session_ref.session_id}`\n"
        f"- Final Snapshot revision: {projection.get('snapshot_revision', 0)}\n"
        f"- Final covered cursor: {projection.get('covered_through', 0)}\n"
        f"- Built/Pending UTs: {states.get('built', 0)}/{states.get('pending', 0)}\n"
        f"- Hidden conversational memory probes: {len(successful_conflicts)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Supplemental strict probe rechecks: {1 if supplemental.get('accepted') else 0}\n"
        f"- Foreground memory-cli searches: {memory_search_count} (previous acceptance: 1)\n"
        f"- Tasks using memory-cli search: {len(memory_search_rows)}/{len(tasks)}\n"
        f"- Blind probes using memory-cli search: {len(conflict_memory_search_rows)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Tasks with real successful tools: {len(real_tool_rows)}/{tool_expected_rows}\n"
        f"- Non-probe tasks with successful foreground pytest: {len(verified_rows)}/{pytest_expected_rows}\n"
        f"- Non-probe tasks with persistent changes: {len(changed_rows)}/{change_expected_rows}\n"
        f"- Non-probe tasks verified as already satisfied: {len(verified_noop_rows)}\n"
        f"- Blind probes without mutation: {len(clean_conflicts)}/{len(BLIND_CONFLICT_REQUESTS)}\n"
        f"- Final full-project pytest: {'PASS' if full_suite.returncode == 0 else 'FAIL'}\n"
        f"- Failure: {failure or 'none'}\n\n"
        "## Preserved histories\n\n"
        + "\n".join(
            f"- {key}: `{value}`"
            for key, value in proof.items()
            if key.endswith("history")
        )
        + "\n\n## Hidden-memory probe excerpts\n\n"
        + (excerpts or "No successful conflict evidence.")
        + "\n",
        encoding="utf-8",
    )
    run_meta = read_json(run_root / "run.json")
    run_meta.update(
        {
            "finished_at": utc_now(),
            "status": "passed" if accepted else "failed",
            "failure": failure,
        }
    )
    write_json(run_root / "run.json", run_meta)
    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "session_id": session_ref.session_id,
                **proof,
                "accepted": accepted,
            },
            ensure_ascii=False,
        )
    )
    return 0 if accepted else 1


def _proof_answer_excerpt(answer: str, *, limit: int = 4000) -> str:
    if len(answer) <= limit:
        return answer
    return "[... earlier analysis omitted ...]\n\n" + answer[-limit:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--agent-alias", default=None)
    parser.add_argument("--foreground-model", default=None)
    parser.add_argument("--turn-timeout", type=float, default=15 * 60)
    parser.add_argument("--max-uncovered-messages", type=int, default=20)
    parser.add_argument("--memory-recovery-attempts", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_REACT_MAX_STEPS)
    parser.add_argument("--task-attempts", type=int, default=12)
    parser.add_argument("--final-test-timeout", type=float, default=30 * 60)
    parser.add_argument("--limit", type=int, default=500, choices=range(1, 501))
    parser.add_argument("--seeds-file", default=None)
    parser.add_argument("--memory-cli", default=None)
    parser.add_argument(
        "--extraction-trigger",
        choices=("turn", "context_ratio"),
        default="context_ratio",
    )
    parser.add_argument("--extraction-trigger-ratio", type=float, default=None)
    parser.add_argument("--context-window", type=int, default=131072)
    parser.add_argument("--prompt-budget-ratio", type=float, default=0.35)
    parser.add_argument("--resume-run", default=None)
    args = parser.parse_args()
    if args.extraction_trigger_ratio is not None and not (
        0 < args.extraction_trigger_ratio < 1
    ):
        parser.error("--extraction-trigger-ratio must be between zero and one")
    if args.context_window is not None and args.context_window <= 0:
        parser.error("--context-window must be positive")
    if args.resume_run:
        with exclusive_run_lock(Path(args.resume_run).resolve()):
            return asyncio.run(run(args))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
