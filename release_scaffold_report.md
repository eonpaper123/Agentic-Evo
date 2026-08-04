# Slice D — Release Scaffold Report

Worktree: `D:\rawle\Coding\Agentic-Evo\.lingtai\agentic-evo-total\work\dev-release`
Branch: `agent/release-scaffold`（base `1f886b0`）

## 1. Files created

- `docs/release/install-upgrade-uninstall.md` — Windows 安装/升级/卸载入口清单 + macOS/Linux 状态
- `docs/release/adapter-contract.md` — Codex hook 映射、ipc 公共 Surface、复合会话身份、第二 agent 接入契约
- `docs/release/reproduction.md` — 测试套件 / autonomous-loop smoke / CAMU memory smoke（分支）/ loop-demo（分支）/ Gate B retained-preflight dry-run
- `docs/release/threat-model.md` — 九条威胁（资产/威胁/控制/残余风险/状态）
- `docs/release/data-privacy.md` — 数据驻留、哈希 vs 明文、密钥、卸载残留
- `docs/release/schema-versioning.md` — 11 个 `*_SCHEMA_VERSION` 常量 + 协议/工件 schema + 迁移纪律
- `docs/release/release-manifest.md` — 六项 v1.0 要求映射、阻塞项、聚焦提交历史（确认 SHA）、推送授权状态
- `docs/README.md` — 新增「发布包（Release）」索引小节
- `release_scaffold_report.md` — 本报告

未修改任何源码或测试；未 stage、未 commit。

## 2. Sources consulted（均已逐条核对）

源码：`install_plan.py`、`native_backends.py`、`windows_native.py`、`windows_pipe.py`、`service.py`、
`windows_gate_b_evidence.py`、`windows_gate_a.py`（经文档）、`adapters/codex.py`、`ipc.py`、`trusted.py`、
`evidence.py`、`autonomous_loop.py`、`experiment_pack.py`、`body.py`、`kernel.py`、`runtime.py`、`witness.py`、
`body_process.py`、`cli.py`、`pyproject.toml`、`tools/windows-gate-b-experiment.ps1`。
文档：`GOAL.md`、`docs/README.md`、`docs/engineering/`（实现状态_Pre-Genesis、最小可信边界与来源证明、单一可信事务域、autonomous-loop、Windows原生Witness边界）、`autonomous_loop_report.md`、
分支 `2b09bd2`（`memory_store.py`、CLI）、`e138b9a`（`loop_integration.py`、CLI）、`tests/` 全部 17 文件与分支补充测试。

## 3. Status legend（所有文件一致）

`design_ready`（设计定稿）/ `code_present`（实现存在）/ `verified`（测试或 smoke 支撑）/ `not_proven_runtime`（缺真实运行时证据）/ `blocked_authorization`（被授权边界阻止）。

## 4. Claim ceilings（写入每个文件）

- 不声称原生安装/卸载已验证（Gate B `not_established`，重启实验 `blocked_authorization`）；
- 不声称第二个真实 coding-agent 已接入（`contract-only`）；
- 不声称 macOS/Linux 已验证（`design_ready`，`native_test_status=not_run`）；
- 不声称 Genesis 已发生或 v1.0 完成（六项要求均未达完成判定）；
- 不声称私有远端已推送（`pending authorization`）；
- memory（`2b09bd2`）与 loop-demo（`e138b9a`）产物在分支上，未并入发布 base `1f886b0`。

## 5. Remaining risks

- 分支产物（memory/loop-demo）需后续合并才能在发布 base 复现；
- Gate B 重启实验、正式 Genesis、第二 agent 接入、私有远端推送四项阻塞项均需授权；
- 跨文件一致性由本报告 + 统一图例保证；若未来代码/测试变化，需同步更新本套文档并重跑 `reproduction.md` 命令。
