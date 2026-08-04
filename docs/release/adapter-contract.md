# Adapter Contract（适配器契约）

> 发布包脚手架文档。本文件说明：现有 `adapters/codex.py` 实现了什么 hook 事件映射、
> `ipc.py` 公共 Surface 暴露什么（请求帧校验）、复合会话身份 `(execution_surface, session_id)` 如何形成、
> 以及第二个 coding agent 如何接入（hook 契约）。**当前仅为契约层基础设施；
> 在第二个真实 agent 产出可重跑证据之前，一律不声称已接入。**

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计/契约已定稿（字段清单、状态机），实现尚未落地 |
| `code_present` | 实现已存在于仓库（引用文件/函数/常量），未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑（引用测试文件或 artifact） |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止（第二真实 agent 未运行、hook 未安装等） |

## 1. `adapters/codex.py` 实现的 hook 事件映射

入口：`handle_codex_hook(home, payload)`（`src/agentic_evo/adapters/codex.py`）。
文档字符串明确：**把稳定可观察的 Codex hook 字段映射为 evidence，而不复制原始 prompt/工具内容**。

| Codex hook 事件 | 映射结果（`_map_event`） | Surface 操作 | 记录内容 |
| --- | --- | --- | --- |
| `SessionStart` | —（构造 wake context） | `surface.wake(execution_surface="codex", session_id, project_environment, model)` | 返回 `additionalContext`（Root/Head/Generation/Body files/activation 摘要） |
| `SessionEnd` | — | `surface.sleep(execution_surface, session_id)` | 无 |
| `UserPromptSubmit` | `user_prompt_submitted` | `surface.observe(...)` | `prompt_chars` + `prompt_sha256`（**不存原文**） |
| `PreToolUse` / `PostToolUse` | `tool_use_started` / `tool_use_finished` | `surface.observe(...)` | `tool_name`、`tool_input_sha256`、`tool_response_sha256`（`_hash_json_value`，canonical JSON 哈希） |
| `PreCompact` / `PostCompact` | `context_compaction_started` / `_finished` | `surface.observe(...)` | `trigger`（`_bounded_text`，≤256 字符） |
| `SubagentStart` / `SubagentStop` | `subagent_started` / `subagent_stopped` | `surface.observe(...)` | `agent_id`、`agent_type`、`last_message_sha256` |
| `Stop` | `turn_stopped` | `surface.observe(...)` | `last_message_chars` + `last_message_sha256` |
| `PermissionRequest` | `permission_requested` | `surface.observe(...)` | `tool_name`、`tool_input_sha256`、`permission_mode` |
| 其它 | `execution_surface_event` | `surface.observe(...)` | `unmapped_event_name`（有界文本） |

边界行为：

- 所有自由文本字段经 `_bounded_text(value, limit=256)` 截断；`_project_ref` 把 `cwd` 映射为 `sha256:` 前缀摘要；
- 适配层异常（`AgenticEvoError/KeyError/OSError/TimeoutError/...`）→ 返回 `None`（适配层 fail-open）；
  安装计划中 `codex_hook.failure_policy = "fail_open"`（`install_plan.py`）；
- hook 的**禁止操作**（`install_plan.py` `codex_hook.forbidden_operations`）：`advance_head / genesis / prepare_successor / turn_off / turn_on` —— hook 无谱系写入权。

状态：`code_present` + `verified`（`tests/test_codex_adapter.py`，6 例）。

## 2. `ipc.py` 公共 Surface（请求帧校验）

- `PUBLIC_PROTOCOL = "agentic-evo-public-v2"`；`CONTROL_PROTOCOL = "agentic-evo-off-rehearsal-v1"`；
- 帧边界：`MAX_PUBLIC_FRAME_BYTES = 64 KiB`；`send_public_message` / `validate_public_request_frame` 对超界帧抛 `InvalidPublicFrame`；
- 信封校验（`service.py._validate_request_envelope`）：字段集合必须恰为 `{protocol, request_id, operation, params}`；
  `request_id` 为非空 ≤128 字符串；`operation` 必须在公共 allowlist；
- 参数校验（`_validate_parameters`）：每个 operation 的 required/optional 集合（`_PUBLIC_PARAMETERS`），
  单字段 UTF-8 字节上限 `_MAX_PUBLIC_TEXT_BYTES = 1024`；
- 超时：`PUBLIC_IO_TIMEOUT_SECONDS = 2.0`（收帧）、`PUBLIC_RESPONSE_TIMEOUT_SECONDS = 12.0`（客户端等响应）；
- 公共操作集：`status / wake / sleep / observe`；控制端点只接受 `off` 且参数必须为空（`_validate_control_request`），
  provenance 固定为 `control_unverified`（`_turn_off_rehearsal`）；
- 客户端：`SurfaceClient`（未提权公共客户端）、`OffRehearsalClient`（Off-only）。

状态：`code_present` + `verified`（`tests/test_witness_service.py` 36 例、`tests/test_cli_lifecycle.py` 29 例、`tests/test_windows_pipe.py`）。

## 3. 复合会话身份 `(execution_surface, session_id)`

- `runtime.py` 定义 `SessionIdentity(execution_surface, session_id)`；`trusted.py` 的 `sessions` 表主键即
  `PRIMARY KEY (execution_surface, session_id)`（`WITHOUT ROWID`）；
- `_require_session_identity` 强制两者均为非空字符串；`start_session/end_session` 以该复合键原子记录，
  并把 `sessions_hash`（canonical JSON 承诺，`_session_commitment_hash`）写入 state 单例行；
- 同一 Agent 跨会话/跨项目/跨模型连续存在的“载体”正是该复合键 + `root/head` 承诺（GOAL.md 要求 2 的基础设施层）。

状态：`verified`（`tests/test_trusted_transactions.py`、`tests/test_lifecycle.py`）。

## 4. 第二个 coding agent 如何接入（hook 契约，contract-only）

接入路径（按现有代码可组合）：

1. **公共 Surface 直连**：实现任意语言的客户端，向 `PUBLIC_PROTOCOL`（`agentic-evo-public-v2`）发送
   `wake / observe / sleep` 请求帧（信封与参数集合见上文），经 `SurfaceClient` 或自定义实现；
   会话身份必须是该执行面的 `(execution_surface, session_id)` 复合键；
2. **stdio 桥**：`cli.py` 的 `surface-stdio --dev-home <home> --execution-surface <name>` 把有界通用 stdio 协议
   桥接到公共 Surface（provider-neutral CLI/stdio 基础设施，GOAL.md 要求 2）；
3. **MCP**：作为后续 adapter 基础设施（`design_ready`，当前无实现）；
4. **hook 生命周期映射**：仿照 `codex.py` 把 agent 的会话开始/结束、事件、工具调用映射为
   `wake/sleep/observe`，并遵守：不复制原始内容、字段有界、证据 payload 拒绝敏感键
   （`evidence.py._reject_sensitive_keys` / `SensitiveContentError`）、不触碰禁止操作集合。

### 契约要求（对第二个真实 agent 的验收）

- 在**真实任务**中实际使用该契约，并产出可重跑的 evidence（`evidence_refs`/`events` 可验证）；
- 至少覆盖 `SessionStart → 若干 observe → SessionEnd` 的完整生命周期；
- 同一 `(execution_surface, session_id)` 会话的 Head/Root 承诺与 trusted state 一致（`start_session` 的 `expected_head` 校验）。

### 现状

- hook **未安装**（`install_plan.py`：`codex_hook.status = "not_installed"`）；
- 尚无第二个真实 coding agent 使用任何接入路径；
- 状态：`code_present`（接入基础设施）/ `design_ready`（MCP）；**contract-only** ——
  第二 agent 接入与可重跑证据：`blocked_authorization` + `not_proven_runtime`。

## 5. 证据塑形与隐私纪律（本契约附带）

- 原始 prompt / 工具输入输出只以 `sha256` + 字符数进入 evidence（`codex.py`）；
- `project_environment` 一律 `sha256:` 摘要；`model` 仅存有界文本引用；
- evidence payload 边界：`MAX_PAYLOAD_BYTES = 16 KiB`、`MAX_PAYLOAD_KEYS = 32`、敏感键正则拒绝
  （`access key / password / secret / token / authorization / cookie / credential` 等，`evidence.py._SENSITIVE_KEY`）。

## 6. 声明上限（Claim ceilings）

- **不得声称**：第二个真实 coding agent 已接入（GOAL.md 要求 2 未达成）；Codex hook 已安装并在真实会话中产生证据；
- **可声称**：adapter 基础设施（hook 映射、公共 Surface、帧校验、复合会话身份）已实现并通过单元测试；
  该基础设施是 provider-neutral 的 adapter 基础，不构成“已完成第二 agent 接入”。
