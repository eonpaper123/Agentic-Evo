# 数据与隐私边界（Data / Privacy）

> 发布包脚手架文档。说明：本地 SQLite 状态（`state.sqlite3`、`witness.key`）、Body-owned 记忆存储
> （`memory/camus.jsonl`）、autonomous-loop 存储（`events.jsonl`/`consolidation.jsonl`）、evidence 记录；
> 无外部数据传输；哪些是哈希、哪些是明文；密钥/敏感信息处理；删除/卸载残留策略（Gate B 零残留为
> `not_proven_runtime`，直到重启实验）。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计已定稿，实现尚未落地 |
| `code_present` | 实现已存在于仓库，未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑 |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止 |

## 1. 数据驻留（全部本地）

| 数据 | 位置 | 归属 | 说明 | 状态 |
| --- | --- | --- | --- | --- |
| trusted state | `<home>/trusted/state.sqlite3`（`trusted.py`） | Witness（TCB） | 单例行 state（Who/Why/Authority/Root/Head/…）+ `events` 表（evidence）+ `checkpoints` 表（本地 HMAC）+ `sessions` 表 | `verified`（事务/校验测试） |
| witness key | `<home>/trusted/witness.key`（32 字节，`trusted.py._load_or_create_key`） | Witness | checkpoint MAC 密钥，原子写入 | `verified`（长度/完整性校验） |
| body store | `<home>/body/blobs` + `<home>/body/manifests`（`body.py` `BodyStore`） | Body | 内容寻址 blob（sha256）+ manifest JSON | `verified` |
| memory store | `<agent home>/memory/camus.jsonl`（`memory_store.py`，分支 `2b09bd2`） | Body（**非 TCB**） | append-only hash 链，首行 meta header | `code_present`（分支） |
| autonomous-loop stores | `<loop-home>/meta.json`、`events.jsonl`、`consolidation.jsonl`（`autonomous_loop.py`） | 调用方 | 循环仅写自身 home；无网络/子进程 | `verified`（smoke） |
| 独立 evidence ledger | `evidence.py` `EvidenceLedger`（`events.jsonl` + `meta.json`） | 可独立于 runtime 使用 | 与 trusted state 同构的 hash 链 | `verified` |
| smoke / 实验工件 | 工作区根 `autonomous-loop-smoke*.jsonl`、`smoke-*.stdout.txt`、`experiments/receipts/*.receipt.json` | 研究 | 已提交的不可变产物 | `verified` |

**无外部数据传输**：运行时与循环代码中无网络路径；`GOAL.md` 规定外发数据必须先获授权；无遥测。状态：`code_present`（静态可查证）。

## 2. 哈希 vs 明文

**哈希（SHA-256，不保留原文）**：

- `who` / `why`：`sha256_hex(host_binding)` / `sha256_hex(purpose_anchor)`（`trusted.py` genesis）；
- `project_environment`：`sha256:` 前缀摘要（`codex.py._project_ref`）；
- prompt / tool 输入输出 / 最后消息：`prompt_sha256`、`tool_input_sha256`、`tool_response_sha256`、`last_message_sha256`（只留哈希与字符数）；
- Root / Head / blob / manifest commitment：内容寻址哈希；
- checkpoint：`checkpoint_hash` + HMAC-SHA256（`trusted.py._insert_checkpoint`）。

**明文（仅本地）**：

- evidence payload（subject to `MAX_PAYLOAD_BYTES=16 KiB`、`MAX_PAYLOAD_KEYS=32`、敏感键拒绝）；
- session `value_json`（含 `model_ref`、activation 描述等有界文本）；
- body 文件（opaque 内容，按 blob 哈希寻址）；loop 记录 payload；`model_ref` / 事件名等有界文本。

## 3. 密钥 / 敏感信息处理

- `witness.key`（32 字节）仅用于本地 checkpoint HMAC；`kernel.py` 另有 `authority.key`（32 字节）签名生命核 state；
  两者都只存在 home 内、原子写入、读取时校验长度；**不离开本机、不写入 evidence**；
- evidence payload 的敏感键正则（`_SENSITIVE_KEY`）拒绝 `access key / authorization / chain of thought / cookie /
  credential / environment variable / password / private key / secret / session token / token`（`evidence.py`），命中即 `SensitiveContentError`；
- codex 适配器对 `tool_input/tool_response` 只哈希（canonical JSON 的 sha256），原文不落盘；
- `GOAL.md`：不记录硬件序列号、凭据或密钥（`experiments/labs/3060-computer.json` 为身份起点，无敏感字段）。

状态：`verified`（校验路径有测试：`tests/test_evidence.py`、`tests/test_trusted_transactions.py`）。

## 4. 删除 / 卸载残留策略

- Gate B 脚本 `authorized_effects` 恒为 `temporary_service=true`、`permanent_service=false`，且 `claim_ceiling` 恒为
  `gate_b=not_established`（`windows-gate-b-experiment.ps1` RetainedPreflight）；
- `windows_gate_b_evidence.py`：`verify_gate_b_evidence` 只读校验（`effects` 全 false）；
  `_observe_current_zero_residue` 检查 artifact_root/state_root 缺失 + `sc.exe query` 服务不存在；
  `exercise_gate_b_evidence_attacks` 的 A07 演练 cleanup-root-swap 防御（`same_principal_harness`）；
- 卸载语义：`reversible_uninstall_required=true`、U01 案例为 `partial_cleanup_if_service_lifecycle_occurs`（未发生 lifecycle 即 partial）；
- **零残留主张**：`not_proven_runtime` —— 重启实验未执行（`reboot_validation=not_performed_service_removed`），
  历史 privileged 事件未被独立观察（`historical_configuration.status=inconclusive`）；重启授权：`blocked_authorization`。

## 5. 隐私边界结论

- 唯一宿主始终是用户本人；机器 / Codex / 基础模型 / GPU / 工具 / 项目只是可更换的 Body、器官或执行表面（GOAL.md）；
- 所有状态、证据、记忆、循环记录**仅存本机**；删除即删除（无远端副本）；
- 卸载残留的证明需要一次获授权的重启实验；在获得该证据前，不得声称零残留。

## 6. 声明上限（Claim ceilings）

- 可声称：数据全部本地驻留（代码可查证）、敏感内容不进 evidence（校验强制）、密钥不离机；
- 不可声称：卸载零残留已验证（Gate B pending）；重启后状态无残留（未测）；外发数据不存在于任何路径（人类复制粘贴超出本仓库边界）。
