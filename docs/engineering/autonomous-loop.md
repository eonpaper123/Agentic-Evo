# Autonomous Loop (v1.0) — 自治自修复循环引擎

English / 中文混合说明。本模块实现 v1.0 自治修复循环状态机，属于**调用方所有**
（caller-owned）组件：它只管理自己的 append-only 存储，绝不触碰运行时生命核心
（life core：Who / Why / Authority / Root / Head）。

- 模块：`src/agentic_evo/autonomous_loop.py`
- 文档：本文件
- 冒烟：`autonomous-loop-smoke.jsonl`（passed 场景）、`autonomous-loop-smoke-failed.jsonl`（failed 场景）

## 设计（Design）

循环是独立于 `DevelopmentalRuntime` / `MicroLifeKernel` 的调用方组件。
循环不导入 `runtime`、`kernel`、`trusted`、`body`；候选修复（`CandidateRepair`）、
探针（`Probe`）、工作区（`workspace`）全部由调用方提供，引擎只负责：

1. 把外部信号以 append-only 记录写入 `events.jsonl`（hash 链完整性）；
2. 对候选做作用域/可逆性/生命核心/策略门校验；
3. 在**有界**（probe 次数 + 墙钟时间）的可逆 probation 窗口内应用并探测；
4. 仅允许三种 outcome：`passed` / `failed` / `inconclusive`；
5. passed → commit（记录已接受的修复）；否则 → rollback（调用方 revert，无破坏性全局操作）；
6. 已提交且**反复通过**的修复提升（promote）为可复用 procedure，追加到
   `consolidation.jsonl`；循环本身从不自动修改生产策略。

循环只写自己的 home 目录（`meta.json`、`events.jsonl`、`consolidation.jsonl`），
无网络、无子进程、无服务/UAC。

## 状态机（State machine）

```
observe -> candidate -> probation -> outcome -> commit | rollback -> consolidate
```

| 阶段 | 记录 event_kind | 说明 |
| --- | --- | --- |
| observe | `observe` | 外部信号/观测以追加记录进入循环 |
| candidate | `candidate` | 提出有界、可逆的候选修复（策略门控） |
| probation | `probation` | 有界可逆窗口内应用候选并探测 |
| outcome | `outcome` | 仅允许 passed / failed / inconclusive |
| commit | `commit` | passed 后记录已接受的修复 |
| rollback | `rollback` | failed / inconclusive 后回滚到先前状态 |
| consolidate | `consolidate` | 反复通过的已提交修复提升为持久 procedure |

outcome 判定：窗口内任一探针通过 → `passed`；全部完成探针失败且无错误 → `failed`；
探针报错（非决定性）或窗口耗尽仍无决定性证据 → `inconclusive`。

## 策略默认值（Policy defaults）

```python
LoopPolicy(
    authorize_external_effects=False,   # 默认拒绝一切外部副作用
    max_probe_runs=5,                   # probation 窗口探针次数上限
    max_probation_seconds=30.0,         # probation 窗口墙钟上限
    promotion_passes_required=2,        # 提升到 consolidation 所需连续通过次数
)
```

- 外部副作用候选（`effect_kind="external"`）在默认策略下被 `PolicyGateError` 拒绝；
  仅当调用方显式 `authorize_external_effects=True` 才放行。
- 候选声明 `reversible=True` 且 `touches_life_core=False` 由引擎强制校验；
  作用域落入 `{who, why, authority, root, head}` 直接被拒。
- 通过次数（pass count）按候选 id 累计，并在加载时从自身存储恢复；
  提升只追加到 consolidation store，不改变任何生产策略。

## 声明上限（Claims ceiling）

**已建立（established）**：
- 观测记录 append-only 且 hash 链可校验；
- 候选有界、可逆、策略门控、从不改写生命核心；
- outcome 仅限 {passed, failed, inconclusive}；commit/rollback 语义；
- 反复通过的修复被持久化到 consolidation store；
- 默认无外部副作用；循环自身不自动改策略。

**未主张（NOT claimed）**：
- 不主张能修复真实生产缺陷（修复正确性完全由调用方 probe/candidate 决定）；
- 不主张因果归因、记忆形成、自主学习或自我进化；
- 不主张 consolidated procedure 可被自动采纳为生产策略；
- 不主张零人工干预；不主张跨实例全局的通过计数；
- 不评估 Body 语义或生命核心有效性（那是 runtime/kernel 的职责）。

## 冒烟结果（Smoke results）

运行方式（直接运行，非测试套件）：

```
python -m agentic_evo.autonomous_loop --fixture passed --output autonomous-loop-smoke.jsonl
python -m agentic_evo.autonomous_loop --fixture failed --output autonomous-loop-smoke-failed.jsonl
# 或等价的 CLI 子命令：
python -m agentic_evo.cli autonomous-loop-smoke --fixture passed --output out.jsonl
```

- **passed 场景**：探针先失败一次，候选应用后探针通过；两轮循环均 commit
  （pass_count=2 ≥ 2）后触发 consolidate。退出码 0。
  记录序列：`observe, candidate, probation, outcome(passed), commit, observe,
  candidate, probation, outcome(passed), commit, consolidate`；consolidation store 1 行。
- **failed 场景**：候选修错了对象，窗口内 5 次探针全部失败 → outcome=failed →
  rollback（reverted=true, destructive_global_ops=false），无任何 consolidation。退出码 0。
  记录序列：`observe, candidate, probation, outcome(failed), rollback`；consolidation store 0 行。

完整逐字输出见 `autonomous_loop_report.md` 与工作区根目录的 `smoke-*.stdout.txt`。
