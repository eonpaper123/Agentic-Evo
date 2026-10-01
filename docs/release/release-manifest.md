# 发布清单（Release Manifest）

> 发布包脚手架文档（v1.0 要求 6）。将 v1.0 的**六项要求**逐一映射到当前证据/状态：存在什么、缺什么、
> 阻塞项（Gate B 重启、第二真实 coding agent、Genesis 授权、私有远端推送授权），并给出聚焦提交历史
> （四个分支 tip 的确认 SHA）。发布 base：`1f886b0`（`agent/autonomous-loop` tip）。

> **集成状态（2026-08-05 03:03 +08）**：四个切片已在本地集成分支 `agent/v1.0-integration`（tip `e9fa817`）合并完成：`113db04 merge(slice-b)`、`0a6f92e merge(slice-c)`、`e9fa817 merge(slice-d)`（均在 `1f886b0` 之上；cli.py 冲突已手工解决，保留两套子命令）。集成树全量测试：**275 tests OK**（77.2s）。私有远程推送仍 `blocked_authorization`。

> **Slice H 集成状态（2026-08-05 +08，worktree `dev-genesis`）**：新增 `runtime-adopt` 子命令
> （`src/agentic_evo/runtime_adopt.py` + `cli.py` 加法接线；未改 `trusted.py`/`body.py`/`kernel.py`）。
> 真实出生 home（`<AGENTIC_EVO_REPO>\.agentic-evo\runtime-home`）已备份、采纳并 serve/status 冒烟通过
> （详见 `runtime_home_report.md`）。`tests/test_runtime_adopt.py` 7 例全绿；全量 299 tests 仅余
> 两个**预先存在**的环境性失败模块（`test_windows_gate_b_evidence.py` 43 例、`test_windows_gate_b.py` 1 例，
> 均为 sandbox 子进程非 UTF-8 输出解码）。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计/契约已定稿，实现尚未落地 |
| `code_present` | 实现已存在于仓库（含分支），未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑 |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止 |

## 1. 六项要求 → 当前状态

| # | v1.0 要求（GOAL.md） | 存在（证据） | 缺失 / 阻塞 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | Windows 原生安装/运行/恢复/On-Off/卸载零残留/有界睡眠；macOS/Linux 同协议 backend | `install_plan.py`（plan-only，`ready_to_install=false`）；`windows_native.py`（受限 Low-IL + Job Object）；`windows_pipe.py`（DACL + reject remote）；`service.py`（前台 Witness 排演）；Gate B 脚本四 Mode（含 `RetainedPreflight` plan-only）；`native_backends.py` 定义 darwin/linux backend | 真实安装/运行/卸载未执行；重启验证未执行；macOS/Linux `native_test_status=not_run` | `code_present` + `verified`（局部/foreground）；`not_proven_runtime`（安装态）；`blocked_authorization`（重启）；macOS/Linux `design_ready` |
| 2 | 同一 Agent 跨对话/项目/模型/重启持续存在；接入 Codex + 第二个真实 coding-agent | `adapters/codex.py` hook 映射（wake/sleep/observe，原文不落盘）；`ipc.py` 公共 Surface（帧校验、allowlist）；`(execution_surface, session_id)` 复合会话；`surface-stdio` 通用桥 | hook 未安装（`status=not_installed`）；无第二个真实 agent 使用；跨重启的 OS 级存在未证明 | `code_present` + `verified`（基础设施测试）；第二 agent：`blocked_authorization` + `not_proven_runtime` |
| 3 | immutable experience archive、主动回忆/遗忘/睡眠巩固、Memory-to-Capability 机制空间 | `memory_store.py`（分支 `2b09bd2`）：`memory/camus.jsonl` append-only hash 链、CAMU `<G,A,I,P,E>`、`memory-camu-*` CLI、`tests/test_memory_camu.py` | 机制空间在分支，**未并入发布 base**；不声称记忆形成/学习 | `code_present`（分支）+ 分支内 `verified` |
| 4 | 正常任务流中自治完成 observe→候选→probation→commit/rollback→consolidate | `autonomous_loop.py`（策略门默认拒绝外部副作用、有界 probation、hash 链 cycle records、consolidation store）；smoke 产物 3 份；`loop_integration.py` 真实任务 demo（分支 `e138b9a`，`loop-demo-run`） | 真实生产任务流证据未建立；demo 在分支未并入 base | `verified`（引擎 smoke）；demo `code_present`（分支） |
| 5 | 预注册、可重跑的真实纵向实验 + 反例测试 | `experiment_pack.py`（prereg/pack/verify schema 族、claim ceiling）；`experiments/001_...md` 协议；receipt `2026-08-04-detached-artifact-regression.receipt.json`；`tests/test_experiment_pack.py`（12） | 正式 Genesis 未发生 → 真实纵向实验未启动（Pre-Genesis） | `code_present` + `verified`（工件一致性）；真实实验 `not_proven_runtime`；Genesis `blocked_authorization` |
| 6 | 发布物：安装/升级/卸载入口、adapter contract、复现说明、威胁模型、数据/隐私边界、版本化 schema、测试与迁移说明；聚焦提交 + 推送私有远端 | 本 `docs/release/` 七文件 + `docs/README.md` 索引（本 slice）；聚焦提交历史见第 3 节 | 私有远端**未推送**（未授权）；原生安装/卸载仍未证明（Gate B） | 本 slice：`code_present`（docs）+ `verified`（对照代码/测试逐条核实）；推送：`blocked_authorization` |

## 2. 阻塞项（Blockers）汇总

| 阻塞项 | 影响 | 授权状态 |
| --- | --- | --- |
| Gate B 重启实验（RetainedPreflight → Run/Elevated 保留服务生命周期） | 要求 1 的安装/恢复/卸载零残留 | `blocked_authorization`（待 the operator 重启授权） |
| 正式 Genesis | 要求 5 的真实纵向实验起点；要求 2 的跨重启存在 | `blocked_authorization` |
| 第二个真实 coding-agent 接入并产出可重跑证据 | 要求 2 验收 | `blocked_authorization` + `not_proven_runtime` |
| 私有远端推送 | 要求 6 的“Git 中保留聚焦提交并推送私有远端” | `blocked_authorization`（本 slice 明确不推送） |
| memory / loop-demo 分支合并进发布 base | 要求 3、4 的可发布性 | `not_proven_runtime`（未合并，需后续 slice） |

## 3. 聚焦提交历史（确认 SHA）

下列 SHA 已用 `git log --oneline -1 <branch>` 在共享仓库核实（branch tip → SHA）：

| 分支（worktree） | tip SHA（已确认） | 聚焦内容 |
| --- | --- | --- |
| `agent/p0-p1-evidence-readiness` | `1eac396`（`chore(lab): declare 3060-computer as host per the operator`） | 证据准备度闭包：`1bae2e4 feat(evidence): P0/P1 evidence-readiness closure`、`936e939`（实验包泄漏检查结构化）、`893b502`（canonical 工件 CLI 输出）、`7d16855`/`c47505f`（red 测试：工件 CLI / 纵向证据包）、`87c9c99`/`c857c7a`（evidence/backend 边界文档）、`7c745fc`/`fff6c5c`/`cfd6deb`/`0289268`/`c4113c1`（跨平台 backend 契约冻结）、`ef67624`（五模块实证覆盖要求） |
| `agent/autonomous-loop` | `1f886b0`（`feat(loop): add caller-owned autonomous repair loop engine`） | **发布 base**：自治循环引擎（`autonomous_loop.py`）+ 上游 `870c932`/`36698b9`（experiment pack 导出/CLI）、`3f9cf17`/`e24c0bc`（prereg/pack CLI 测试）、`7f618b0`/`3c2db88`/`1349de6`（artifact receipt / 文档） |
| `agent/memory-capability` | `2b09bd2`（`feat(memory): add Body-owned CAMU memory mechanism space`） | 在 `1f886b0` 之上 +1：CAMU 机制空间（`memory_store.py`、`memory-camu-*` CLI、`tests/test_memory_camu.py`） |
| `agent/loop-demo` | `e138b9a`（`feat(loop): add real-task autonomous-loop integration demo`） | 在 `1f886b0` 之上 +1：真实任务循环集成 demo（`loop_integration.py`、`loop-demo-run` CLI、`tests/test_loop_integration.py`） |

说明：`agent/p0-p1-evidence-readiness` 与另外三分支的历史关系见各自 `git log`；对应 worktree `dev-memory` / `dev-loop-demo` 存在于仓库内。

## 4. 推送授权状态

- 私有远端推送 **NOT authorized**：本 slice 不执行任何 `git push`，也不产生推送证据；
- 状态：`blocked_authorization`（`pending authorization`）。

## 5. 声明上限（Claim ceilings）

- 本清单的 `verified` 均指代码/测试/artifact 层面的事实核实；
- 六项要求中**无一项达到 v1.0 完成判定**（GOAL.md 完成判定要求全部要求被当前证据直接支持且一致）；
- 不得把 receipt、self-report 或本文档当作独立事实；Gate B / Genesis / 第二 agent / 推送 四项恒为未完成状态。
