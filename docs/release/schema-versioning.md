# 版本化 Schema 清单（Schema Versioning）

> 发布包脚手架文档。v1.0 要求 6 中的“版本化 schema”交付物：**全部 `*_SCHEMA_VERSION` 常量清单**
> （文件位置、内容、迁移说明），以及版本化 schema 到六项要求的映射。所有常量均引用真实代码。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计已定稿，实现尚未落地 |
| `code_present` | 实现已存在于仓库，未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑 |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止 |

## 1. Schema 版本常量清单（`src/agentic_evo`）

| 常量 | 值 | 文件:行 | 载体 / 内容 | 状态 |
| --- | --- | --- | --- | --- |
| `TRUSTED_SCHEMA_VERSION` | `agentic-evo-trusted-v2` | `trusted.py:32` | `state` 表单例行：`who/why/authority/root/head/revision/sessions_hash/...`；`_read_state` 校验 | `verified` |
| `CHECKPOINT_SCHEMA_VERSION` | `agentic-evo-checkpoint-v1` | `trusted.py:33` | `checkpoints` 表：`record_json` + `checkpoint_mac`（HMAC-SHA256），每事件一条 | `verified` |
| `SESSIONS_SCHEMA_VERSION` | `agentic-evo-sessions-v2` | `trusted.py:34` | `sessions` 表：PK `(execution_surface, session_id)`，`value_json`；`_session_commitment` 承诺哈希 | `verified` |
| `EVIDENCE_SCHEMA_VERSION` | `agentic-evo-evidence-v1` | `evidence.py:23` | evidence 记录（trusted `events` 表 / 独立 `events.jsonl`）：`previous_integrity_hash` 链 | `verified` |
| `BODY_SCHEMA_VERSION` | `agentic-evo-body-v2` | `body.py:13` | body manifest：`root/parent_head/generation/author_kind/files/activation_*`；commitment=sha256(canonical manifest) | `verified` |
| `KERNEL_SCHEMA_VERSION` | `agentic-evo-kernel-v1` | `kernel.py:22` | 生命核 `state.json`（legacy kernel 模块，`authority.key` 签名） | `verified` |
| `RUNTIME_SCHEMA_VERSION` | `agentic-evo-runtime-v3` | `runtime.py:22` | runtime 信封（`DevelopmentalRuntime` 协调层） | `code_present` |
| `AUTONOMOUS_LOOP_SCHEMA_VERSION` | `agentic-evo-autonomous-loop-v1` | `autonomous_loop.py:56` | loop `events.jsonl` cycle records（hash 链） | `verified`（smoke） |
| `CONSOLIDATION_SCHEMA_VERSION` | `agentic-evo-consolidation-v1` | `autonomous_loop.py:57` | `consolidation.jsonl` 可复用 procedure 注册表 | `verified`（smoke） |
| `MEMORY_STORE_SCHEMA_VERSION` | `agentic-evo-memory-store-v1` | `memory_store.py`（分支 `2b09bd2`） | `memory/camus.jsonl` meta header + 链式记录 | `code_present`（分支） |
| `CAMU_SCHEMA_VERSION` | `agentic-evo-camu-v1` | `memory_store.py`（分支 `2b09bd2`） | CAMU 记录 `<G, A, I, P, E>`（grounding/activation/influence/prediction/epistemic） | `code_present`（分支） |

## 2. 协议 / 工件 schema 字符串（非 `_SCHEMA_VERSION` 常量，但同样版本化）

| 值 | 位置 | 含义 |
| --- | --- | --- |
| `agentic-evo-public-v2` / `agentic-evo-off-rehearsal-v1` | `ipc.py:21-22` | 公共 Surface / Off 控制协议 |
| `agentic-evo-private-boot-v1` / `agentic-evo-private-lineage-v1` | `body_process.py:32-33` | Body 私有谱系通道协议 |
| `agentic-evo.install-dry-run.v1` | `install_plan.py:12` | 确定性安装计划（plan-only） |
| `agentic-evo.windows-gate-a.v1` | `windows_gate_b_evidence.py:414` | Gate A bundle manifest |
| `agentic-evo.windows-gate-b-plan.v1/.v2`、`config-probe.v1/.v2`、`result.v1/.v2` | `windows_gate_b_evidence.py` | Gate B receipt 族（v2 为 lab-bound 活跃族；v1 仅兼容读取，lab binding inconclusive） |
| `agentic-evo.windows-gate-b-retained-preflight.v1` | `windows-gate-b-experiment.ps1` | 保留服务生命周期 plan-only 预注册工件 |
| `agentic-evo.windows-gate-b-evidence-corpus.v1` | `tests/fixtures/windows_gate_b/corpus.manifest.json` | 冻结语料清单 |
| `agentic-evo.experiment-prereg.v1` / `experiment-pack.v1` / `experiment-artifact-verification.v1` | `experiment_pack.py:20-22` | 纵向实验预注册 / 证据包 / 校验工件 |
| `surface-context-utf8-v1` | `runtime.py:23` | Body activation kind（`SURFACE_CONTEXT_ACTIVATION_KIND`） |

## 3. 迁移说明（Migration notes）

**通用纪律（代码强制）**：

1. **append-only，无 in-place 重写**：evidence/loop/memory 全部为追加型 JSONL 或只插不更的表；
   任何“更新”都以新链节表达（`camu_update`、`record_outcome`、`consolidate` 均为追加）；
2. **hash 链自证**：每条记录含 `previous_integrity_hash` + 自身 `integrity_hash`（canonical JSON 重算校验）；
   checkpoint 另加本地 HMAC；篡改即 `IntegrityError`；
3. **版本拒绝**：加载时校验 `schema_version`，不支持即 `IntegrityError("unsupported ... schema")`
   （`trusted.py._read_state`、`evidence.py.load`、`body.py.read_manifest`、`memory_store`）；
4. **sessions 表结构校验**：`_require_sessions_schema` 用 `PRAGMA table_info` 精确比对列布局，布局变化即拒绝；
5. **receipt 版本选择**：`windows_gate_b_evidence._select_receipt_version` 只按 result schema 选择 v1/v2 封闭族，
   混合 schema 即 `result_contract_invalid`；v1 历史族仅兼容读取（`lab_binding=inconclusive`）。

**跨版本升级路径**：当前仓库**没有自动迁移工具**（如 v1→v2 迁移器）。既有版本升级只能通过“新 home 重新 Genesis”
或手工导出/导入（`body.py` `export_manifest/import_manifest` 支持 body 迁移）。状态：`design_ready`（机制已设计：版本拒绝 + 追加迁移），
升级工具本身 `not_proven_runtime`。

**跨布局迁移（Slice H，`runtime-adopt`）**：这是仓库内第一个**跨目录布局**的迁移路径
（Genesis 根布局 → 运行时布局 `trusted/` + `body/`）。它**不改变任何 schema 版本**：

- 受信状态文件原样搬迁（`state.sqlite3` + `witness.key` 从 home 根 → `trusted/`，witness 密钥字节不变）；
- 初始 Body 以 `parent_head = 出生 head` 提交（body-v2，commitment = sha256(canonical manifest)）；
- 唯一的受信变更是一次**追加式、有见证的 `head_advanced` 过渡**（trusted-v2/evidence-v1/checkpoint-v1 原样追加，revision+1）；
- 出生 head（人工钉定、非 Body 承诺的字符串）保留为初始 Body 的 `parent_head` 与 genesis 证据记录的 `head_after`。

因此 `runtime-adopt` 完全遵守“append-only、无 in-place 重写、hash 链自证、版本拒绝”的迁移纪律。

## 4. 版本化 schema 要求映射（v1.0 要求 6）

- 要求 6 要求发布物包含“版本化 schema”。本清单 + `release-manifest.md` 共同构成该交付物：
  - 运行时 schema：TRUSTED / CHECKPOINT / SESSIONS / EVIDENCE / BODY / KERNEL / RUNTIME（`verified`）；
  - 机制空间 schema：AUTONOMOUS_LOOP / CONSOLIDATION（`verified`，smoke）；MEMORY_STORE / CAMU（`code_present`，分支）；
  - 协议与工件 schema：`ipc.py` / `body_process.py` / Gate A·B receipt 族 / experiment pack 族（`code_present` + `verified` 按测试覆盖）。

## 5. 测试与迁移说明（Tests & Migration）

对应测试（base `1f886b0` `tests/`，方法数见 `reproduction.md` 第 1 节）：

- `test_trusted_transactions.py`（10）—— 单一事务域、checkpoint HMAC、session 承诺、崩溃语义；
- `test_evidence.py`（7）—— evidence 链、payload 边界、敏感键拒绝；
- `test_body_lease.py` / `test_body_process.py`（14/24）—— body manifest 承诺与受限子进程；
- `test_lifecycle.py` / `test_cli_lifecycle.py`（28/29）—— Genesis/On-Off/会话生命周期；
- `test_windows_gate_b_evidence.py`（36）—— v1/v2 receipt 契约、claim ceiling、A01/A03/A04/A05/A07 攻击演练；
- 分支补充：`tests/test_memory_camu.py`（`2b09bd2`）、`tests/test_loop_integration.py`（`e138b9a`）—— 发布 base 合并后纳入套件。

迁移场景对照：body 迁移（export/import manifest）、新 home Genesis（`DevelopmentalRuntime.genesis`）、
receipt 族切换（v1 只读 → v2 lab-bound）均已具备测试或代码路径；版本升级自动化工具为唯一缺失项。

## 6. 声明上限（Claim ceilings）

- 可声称：全部 11 个 `*_SCHEMA_VERSION` 常量与协议 schema 已列明并受版本校验；追加式迁移纪律代码强制；
- 不可声称：存在自动升级/迁移工具；旧版本数据可被自动原地升级（当前策略是拒绝 + 重新 Genesis）。
