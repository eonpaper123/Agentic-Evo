# 复现实验说明（Reproduction）

> 发布包脚手架文档。给出**可重跑**的复现步骤：现有测试套件、autonomous-loop smoke、
> CAMU memory smoke（Slice B，位于 `agent/memory-capability` 分支）、loop-demo（Slice C，位于
> `agent/loop-demo` 分支）、Gate B retained-preflight dry-run（plan-only）。
> 每条均给出精确命令、预期输出与声明上限。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计/契约已定稿，实现尚未落地 |
| `code_present` | 实现已存在于仓库（含分支），未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑 |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止（重启、正式 Genesis 等） |

## 0. 前置：仓库运行时 Python

按 `GOAL.md` 起点命令，仓库运行时 Python 为（本机已确认存在）：

```powershell
$env:PYTHONPATH = 'src'
$py = 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
```

以下所有命令默认先执行这两行。包入口：`pyproject.toml` 的 `[project.scripts] agentic-evo = "agentic_evo.cli:main"`；
无第三方依赖（`dependencies = []`，`requires-python = ">=3.12"`）。

## 1. 现有测试套件

命令（完整套件，base `1f886b0` 的 `tests/` 共 17 个文件 / 237 个 `test_` 方法）：

```powershell
$env:PYTHONPATH = 'src'
& $py -m unittest discover -s tests -v
```

单模块示例（GOAL.md 起点）：

```powershell
$env:PYTHONPATH = 'src'
& $py -m unittest tests.test_windows_gate_b_evidence
```

预期输出：全部模块 `OK`（`Ran N tests ... OK`），退出码 0。覆盖清单（节选）：
`test_install_plan`（3）、`test_native_backends`（5）、`test_windows_native`（5）、`test_windows_pipe`（6）、
`test_windows_gate_a`（6）、`test_windows_gate_b`（8）、`test_windows_gate_b_evidence`（36）、
`test_witness_service`（36）、`test_cli_lifecycle`（29）、`test_lifecycle`（28）、`test_body_process`（24）、
`test_body_lease`（14）、`test_experiment_pack`（12）、`test_trusted_transactions`（10）、`test_evidence`（7）、
`test_codex_adapter`（6）、`test_util`（2）。

状态：`verified`（前序切片已运行；本 slice 为 docs-only，重新运行以确认本机状态）。
声明上限：测试证明的是 **Pre-Genesis 可移植层行为**，不证明安装态原生安全。

## 2. Autonomous-loop smoke

命令（模块入口或 CLI 子命令等价，均委托 `autonomous_loop.smoke_main()`）：

```powershell
$env:PYTHONPATH = 'src'
& $py -m agentic_evo.cli autonomous-loop-smoke --fixture passed --output autonomous-loop-smoke.jsonl --promotion-passes 2
# 等价：& $py -m agentic_evo.autonomous_loop --fixture passed --output autonomous-loop-smoke.jsonl
```

预期输出（passed fixture）：stdout 输出 11 行 JSONL cycle records，event 序列
`observe, candidate, probation, outcome(passed), commit, observe, candidate, probation, outcome(passed), commit, consolidate`；
`consolidation.jsonl` 1 行；stderr 一行摘要；退出码 0。

failed fixture：`--fixture failed` 输出 5 行（`observe, candidate, probation, outcome(failed), rollback`），
`reverted=true`、`destructive_global_ops=false`、无 consolidation；退出码 0。

工作区根目录已有对应产物可对照：`autonomous-loop-smoke.jsonl`、`autonomous-loop-smoke-failed.jsonl`、
`autonomous-loop-smoke-cli.jsonl`（CLI 接线验证）及 `smoke-*.stdout.txt` / `smoke-*.stderr.txt`、`cli-smoke.*`。

状态：`verified`（smoke 产物存在且逐字输出已存档）。
声明上限：内存 fixture 场景；不证明能修复真实生产缺陷（修复正确性由调用方 probe/candidate 决定，见 `docs/engineering/autonomous-loop.md`）。

## 3. CAMU memory smoke（Slice B，`agent/memory-capability` 分支）

> **注意**：`memory-camu-*` CLI 位于分支 `agent/memory-capability`（tip `2b09bd2`，在 `1f886b0` 之上 +1 提交），
> **尚未并入发布 base `1f886b0`**。复现前需先检出该分支（或等待合并）。

命令（`src/agentic_evo/cli.py` 在 `2b09bd2` 上的子命令）：

```powershell
$env:PYTHONPATH = 'src'
& $py -m agentic_evo.cli memory-camu-add   --dev-home <agent-home> --record '{"id":"m1","grounding":{...},"activation":{...},"influence":{...},"prediction":{...},"epistemic":{...}}'
& $py -m agentic_evo.cli memory-camu-list --dev-home <agent-home>
& $py -m agentic_evo.cli memory-camu-show --dev-home <agent-home> --id <content-address>
& $py -m agentic_evo.cli memory-camu-outcome --dev-home <agent-home> --id <content-address> --status verified
```

（另含 `memory-recall`、`memory-consolidate`、`memory-verify-chain` 三个子命令；`--dev-home` 为 Body-owned memory store 所在 agent home。）

预期输出：canonical JSON（`ok:true` + 结果对象）；store 文件为 `<agent-home>/memory/camus.jsonl`
（append-only、hash 链，首行为 meta header；`memory-verify-chain` 检出任何篡改）。

状态：`code_present`（分支）+ `verified`（分支自带 `tests/test_memory_camu.py`；**发布 base 中不可用**，需合并）。
声明上限：只证明机制空间存在且可验证；不证明记忆形成/因果能力增益/学习/自我进化（`memory_store.py` docstring）。

## 4. Loop-demo（Slice C，`agent/loop-demo` 分支）

> **注意**：`loop-demo-run` 位于分支 `agent/loop-demo`（tip `e138b9a`，在 `1f886b0` 之上 +1 提交），
> **尚未并入发布 base**。

命令（`src/agentic_evo/loop_integration.py` + `cli.py` 子命令）：

```powershell
$env:PYTHONPATH = 'src'
& $py -m agentic_evo.cli loop-demo-run --workspace-dir <fresh-caller-owned-dir> --loop-home <fresh-loop-home> --defect off_by_one
```

预期输出：在 workspace 写入确定性 demo 模块（`window(values, n)`，植入 off-by-one 缺陷）与失败 unittest；
运行真实自治循环（demo 策略显式 `authorize_external_effects`，修复真实文件），产出 hash 链 cycle records
到 `--loop-home`（`meta.json` / `events.jsonl` / `consolidation.jsonl`），修复后测试转绿并 consolidate；退出码 0。

状态：`code_present`（分支）+ `verified`（分支自带 `tests/test_loop_integration.py`；**发布 base 中不可用**）。
声明上限：demo 是 caller-owned 真实任务集成演示，不代表生产策略可自动采纳 consolidated procedure。

## 5. Gate B retained-preflight dry-run（plan-only）

命令（脚本头参数见 `tools/windows-gate-b-experiment.ps1`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows-gate-b-experiment.ps1 `
  -Mode RetainedPreflight `
  -LabId windows-gate-b-test-fixture `
  -RunId <run-id> `
  -ArtifactPath <repo>\artifacts\gate-a\bundle\AgenticEvo.ScmProbe.exe `
  -ExpectedArtifactSha256 <sha256> `
  -EvidenceRoot <evidence-dir>
```

预期输出：**单个** canonical JSON 对象，schema `agentic-evo.windows-gate-b-retained-preflight.v1`，包含
`authorized_effects`（`temporary_service=true`、`system_restart=true`，`permanent_service/hook/genesis=false`）、
`retention`（跨一次重启保留、post-restart 验证后移除、`reversible_uninstall_required=true`）与
`claim_ceiling`（`gate_b=not_established`、`native_security_verified=false`、`ready_to_install=false`）。

**不产生**：服务创建、UAC 提升、重启触发、evidence receipt/文件、verifier/attacker/cleanup 运行、Gate B 结论。

可选只读证据检查（不安装、不写证据）：

```powershell
& $py -m agentic_evo.cli verify-windows-gate-b-evidence --bundle-dir <bundle> --evidence-dir <evidence> `
  --gate-b-script tools/windows-gate-b-experiment.ps1 `
  --expected-manifest-sha256 <pin> --expected-script-sha256 <pin> --expected-result-sha256 <pin> `
  --lab-id <lab> --run-id <run> --challenge <challenge>
```

冻结语料：`tests/fixtures/windows_gate_b/corpus.manifest.json`（schema `agentic-evo.windows-gate-b-evidence-corpus.v1`，
含 v1/v2 的 pinned hashes；`historical_v1_*` 的 `lab_binding=inconclusive`，仅兼容读取）。

状态：`code_present`（脚本 `-Mode RetainedPreflight` 分支）+ `design_ready`（保留服务生命周期契约）；
实际 retained service / 重启后验证：`not_proven_runtime`；重启授权：`blocked_authorization`。

## 6. 声明上限汇总

- 测试套件与 autonomous-loop smoke：`verified`（本机可重跑）；
- memory smoke 与 loop-demo：`code_present`（分支）+ 分支内 `verified`，**发布 base 需先合并**；
- Gate B：任何 dry-run/verifier 都不得产生 `gate_b=established` 或 `ready_to_install=true` 的输出（receipt claim ceiling 强制）。
