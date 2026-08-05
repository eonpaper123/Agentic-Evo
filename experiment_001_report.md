# 实验 001 预注册报告 — 机器级连续生命循环（正式样本起点）

Worktree：`D:\rawle\Coding\Agentic-Evo\.lingtai\agentic-evo-total\work\dev-integrate`
日期：2026-08-05
范围：仅在本 worktree 内落盘；未 stage/commit；未触碰共享 checkout；对真实出生 home 仅只读（load/status/evidence 读取，未执行 Genesis/Off/serve/安装/生命周期操作）。

## 1. 结论

实验 001（机器级连续生命循环，v1.0 req 5）的纵向实验已针对**真实出生 runtime home** 完成预注册：

- 预注册工件：`experiments/001/prereg.json`（schema `agentic-evo.experiment-prereg.v1`，工件字节 SHA-256 `5ebc3245bb50b739ae2410625f6e991c1ec739f135b46cb875edb77bd8319511`）
- 通过模块自身校验：`_validate_prereg` 对内存对象与磁盘回读对象均通过（脚本 exit 0，`artifact_reload_validation: valid`）
- 协议文档（含预注册锚点与可重跑流程）：`experiments/001_机器级连续生命循环.md`（§17 新增）

预注册不建立任何学习/进化/Gate B/安装/第二真实 coding-agent 证据；声明上限见 §6。

## 2. 创建/修改的文件

| 路径 | 类型 | 说明 |
|---|---|---|
| `experiments/001/prereg.json` | 新建 | 预注册工件（verbatim JSON 见 §3），与协议 ref、固定锚点、claim_ceiling 一一对应 |
| `experiments/001/prereg_export.py` | 新建 | 可重跑导出/校验脚本（stdlib only，对 home 只读；幂等） |
| `experiments/001_机器级连续生命循环.md` | 修改 | 追加 §17 预注册记录（起点锚点、可重跑流程、声明上限）；既有 H001-A..F、C1..C8、claim ceilings 内容保留 |
| `experiment_001_report.md` | 新建 | 本报告 |

未改动任何 `src/` 文件；未改动真实 home 的任何状态（仅读）；未触碰共享 checkout。

## 3. 预注册工件（verbatim，无删改）

```json
{
  "schema": "agentic-evo.experiment-prereg.v1",
  "protocol_ref": "experiments/001_机器级连续生命循环.md",
  "instrument_version": "agentic-evo-cli-genesis-v1",
  "protocol_version": "agentic-evo-trusted-v2",
  "root_commitment": "c78860b7a6dc65c6af61f69a6e54f4b5c86f9b8b5d612019a7159c95daabe497",
  "head_start": "1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884",
  "execution_surface": "opencode",
  "project_environment": "D:\\rawle\\Coding\\Agentic-Evo",
  "hypothesis_refs": [
    "H001-A",
    "H001-B",
    "H001-C",
    "H001-D",
    "H001-E",
    "H001-F"
  ],
  "control_refs": [
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "C8"
  ],
  "start_anchor": {
    "sequence": 1,
    "integrity_hash": "9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9"
  },
  "claim_ceiling": {
    "artifact_integrity": "established",
    "committed_head_lineage": "established",
    "declared_metadata_truth": "not_established",
    "causal_attribution": "not_established",
    "future_capability_gain": "not_established",
    "memory_formation": "not_established",
    "autonomous_learning": "not_established",
    "self_evolution": "not_established",
    "second_real_coding_agent_natural_usage": "not_established",
    "zero_human_learning_intervention": "not_established"
  }
}
```

（此 JSON 不包含任何秘密；`root_commitment` 是出生 root 字符串的公开 sha256，`project_environment` 为公开路径。）

## 4. 起点锚点如何映射到 genesis 证据

真实 home `D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home` 由 `genesis` CLI 出生并经 `runtime-adopt` 采用（head 从出生钉定值迁到 initial Body commitment）。本次加载实测（`DevelopmentalRuntime.load` + `status()` + `evidence.records()`，全部 home 核对项为 true）：

| 字段 | 值 |
|---|---|
| root（status.root） | `agentic-evo-root-v1`（出生字符串，非 64-hex） |
| head（status.head = head_start） | `1628ca500af68509084609a47a692efdad903c47fa612e1fdd070f8e796a5884`（generation 1；manifest.parent_head = `ee79ae49b8941344e1f314e1f9183deacf8c089d`） |
| authority / lifecycle | `on` / `waiting` |
| instrument_version | `agentic-evo-cli-genesis-v1` |
| protocol_version | `agentic-evo-trusted-v2` |
| evidence 记录数 | 2（genesis seq 1 + head_advanced seq 2） |

起点锚点 = **genesis 证据记录（seq 1）**：

- `sequence: 1`
- `integrity_hash: 9c74390ca2f25bf65fd0538a7fb55230e4c1eb219b8fd01bce203346ef9403b9`（与任务钉定值逐字节一致）
- event_id `e4a099344d3841cfb6fbd18b76089414`，event_kind `genesis`，source/author `research_instrument`，head_before `null`，head_after `ee79ae49b8941344e1f314e1f9183deacf8c089d`（出生钉定 head，保留为谱系父代）

即：实验 001 的“生命起点”钉定在该真实 home 的出生证据上；当前 Head（`1628ca50…`）由 seq 2 `head_advanced`（event_id `d714cf5c3fea411699a9aa9a05f84237`，integrity `2cb02c95…`）从出生 head 原子推进而来，两条记录 hash 链连续。

### 4.1 派生说明（代码为准，非猜测）

`export_experiment_prereg(runtime, *, hypothesis_refs, control_refs)` 从 runtime 派生所有字段（`status.root/head/instrument/protocol` + 尾部 evidence 记录的 surface/anchor），**不存在** execution_surface/project_environment/start_anchor 形参。实测基线输出：`root_commitment = "agentic-evo-root-v1"`（非 64-hex，模块 `_validate_prereg` 会拒绝）、`start_anchor = {sequence: 2, integrity_hash: 2cb02c95…}`（尾记录）、`execution_surface/project_environment = null`。为满足模块校验与任务钉定值，对最终工件做了四处明确调整（全部记录于脚本与协议 §17）：

1. `root_commitment = sha256_hex("agentic-evo-root-v1") = c78860b7a6dc65c6af61f69a6e54f4b5c86f9b8b5d612019a7159c95daabe497`——模块校验要求 64-hex；任务明确指示对非 hex 出生 root 取其 sha256。
2. `start_anchor = {sequence: 1, integrity_hash: 9c74390c…}`——任务钉定 genesis 证据为起点（取代代码默认的尾记录锚点）。
3. `execution_surface = "opencode"`、`project_environment = "D:\rawle\Coding\Agentic-Evo"`——任务指定值（当前实际执行表面与项目环境）。
4. `head_start`、`instrument_version`、`protocol_version`、refs、claim_ceiling 保持代码派生值不变。

写盘注意：`_ceiling_is_exact` 按**键序**与 `EXPERIMENT_CLAIM_CEILING` 比较，工件必须以模块键序写盘（不可 sort_keys）；已用保持键序的 JSON 写盘并回读验证通过（CLI 的 `_json_object` 加载路径也会做同样规范化）。

## 5. 校验证据

- `experiments/001/prereg_export.py`（repo runtime python `C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` 3.12.13，PYTHONPATH=src）exit **0**：home 核对 7/7 true → 基线导出 → 固定值应用 → `_validate_prereg`（内存）通过 → 工件写盘（SHA-256 `5ebc3245bb50b739ae2410625f6e991c1ec739f135b46cb875edb77bd8319511`，1389 bytes）→ `_validate_prereg`（磁盘回读）通过（`artifact_reload_validation: valid`）。
- `verify_experiment_artifact` 仅接受 `experiment-pack.v1` schema，对 prereg 工件不适用（prereg 层校验即 `_validate_prereg`；pack 层校验在运行期结束后执行）。
- 实测探针：Slice I 前 xport_experiment_pack(runtime, prereg, end_sequence=2) 被 invalid_prereg 拒绝（见 §7 第 2 条）；Slice I 后 ccepted 且 erify_experiment_artifact → alid: true（见 xperiment_pack_born_home_report.md）。

## 6. 声明上限（claim ceilings）

与协议 §5/§17.2 及工件 `claim_ceiling` 完全一致：

| 声明 | 状态 |
|---|---|
| artifact_integrity | `established`（内部一致性） |
| committed_head_lineage | `established`（内部一致性） |
| declared_metadata_truth | `not_established` |
| causal_attribution | `not_established` |
| future_capability_gain | `not_established` |
| memory_formation | `not_established` |
| autonomous_learning | `not_established` |
| self_evolution | `not_established` |
| second_real_coding_agent_natural_usage | `not_established` |
| zero_human_learning_intervention | `not_established` |

预注册本身只固定研究起点，不建立任何能力/学习/进化/Gate B/安装/第二真实 coding-agent 证据。

## 7. 实际运行纵向实验的下一步

1. **正式生命史**：在真实 home 上以 `opencode` 表面唤醒同一 Root/Head 的 Body（`wake`），跨会话/跨项目/跨表面执行真实任务，`observe` 产生 seq ≥ 3 的 evidence window；不复制提示词、不告知 Agent 该记住什么（协议 §8–§10、Run A/B/C）。
2. **pack 导出耦合（已由 Slice I 解除，2026-08-05）**：`experiment_pack.py` 现统一规范化 `root_commitment`（非 hex 出生 root 取 `sha256_hex(root)`），prereg/evidence/manifest 的 root 比较一致；pack 与锚点校验接受 born+adopted 谱系（`start_anchor` = genesis 证据、`head_start` 为被采纳的 Body head、genesis `head_after` ≠ 当前 head 合法）。实测：`export_experiment_pack` → `accepted`，`verify_experiment_artifact` → `valid: true`（详见 `experiment_pack_born_home_report.md`）。
3. **对照控制**：按协议 C1–C8 冻结 Body、历史消融、Root/Head 错配、模型与资源匹配、仪器升级、Human Learning Intervention、Off 后无活体计算。
4. **包导出与验证**：window 结束（seq N）后 `export_experiment_pack(runtime, prereg, end_sequence=N)` → `verify_experiment_artifact`，验证 evidence window 连续性、`authority_end`、body manifest 链；产物与 prereg 一起构成可复现最小实验包。
5. **结论纪律**：只允许声明 §6 中 `established` 两项（内部一致性）；所有学习/进化主张必须来自后续实验（依赖链：连续身份 → 历史可达 → 记忆产生未来因果差异 → 普通能力学习 → 自主体改造 → 自主实验学习 → 现实选择与谱系延续 → 能力积累与跨端口迁移 → 元进化）。

## 8. 剩余风险

- 真实 home 与备份（`runtime-home-backup-20260805`）均在 `.agentic-evo` 下；本任务未修改 home 状态，但任何后续写入（正式生命史）都不可逆且必须保留 witness.key。
- 预注册工件的内容（root 哈希化、genesis 锚定、opencode 表面）是任务钉定值与模块约束的合理解释；若后续模块调整 `_validate_prereg`/pack 语义，本工件需重新校验。
- `verify_experiment_artifact` 对 prereg 不适用；pack 层验证需在模块耦合解决后进行。
