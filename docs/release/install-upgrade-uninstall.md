# 安装 / 升级 / 卸载入口（Windows 原生）

> 本文档属于 `docs/release/` 发布包脚手架（Slice D）。所有主张均以当前仓库代码为据，
> 使用统一状态图例，明确声明上限：**Gate B 未建立，原生安装/卸载未在真实运行中被证明**。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计/契约已定稿（公式、状态机、字段清单），实现尚未落地 |
| `code_present` | 实现已存在于仓库（引用文件/函数/常量），未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑（引用测试文件或 artifact） |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得（安装态、真实 agent、重启后、OS principal） |
| `blocked_authorization` | 被明确授权边界阻止（UAC/重启/正式 Genesis/推送远端/第二真实 agent） |

## 1. 现状总览：代码里有什么

### 1.1 确定性安装计划（plan-only）—— `src/agentic_evo/install_plan.py`

`build_install_plan()` 返回 schema 为 `agentic-evo.install-dry-run.v1` 的确定性目标契约，**不执行任何安装写入**：

- `effects`：`create_state / install_hook / install_service / perform_genesis / start_process` 全部为 `false`；
- `claims`：`implemented_native_components` 含四项 foreground 局部证据
  （`win32_job_object_process_tree_fencing`、`win32_public_named_pipe_dacl_peer_authentication`、
  `win32_restricted_low_integrity_body_process_rehearsal`、`win32_explicit_inherited_private_lineage_transport_rehearsal`）；
  `native_security_verified=False`、`ready_to_install=False`、`windows_gate_a_status=scm_probe_bundle_ready`；
- `platforms.win32`：`service_scope=machine`、`supervisor=windows_scm`、
  `witness_principal=restricted_service_sid`、`trusted_state=programdata_service_sid_acl`、
  `public_surface=named_pipe_explicit_dacl_bound_host_sid_reject_remote_clients`、`worker_fencing=job_object`；
- `codex_hook`：`scope=user`、`status=not_installed`、`failure_policy=fail_open`、`provenance=surface_unverified`；
- `blockers` 八项：`authenticated_host_presence`、`formal_genesis_authorization`、`native_witness_and_body_principals`、
  `service_owned_trusted_state`、`peer_authenticated_public_and_private_ipc`、`native_process_tree_fencing`、
  `native_service_artifacts_and_reversible_uninstall`、`target_platform_integration_tests`。

状态：`verified`（`tests/test_install_plan.py` 覆盖计划契约；计划明确输出 `ready_to_install=false`）。

### 1.2 Windows 原生基元 —— `src/agentic_evo/windows_native.py`

- `spawn_restricted_suspended_process()`：`CreateRestrictedToken`（`_DISABLE_MAX_PRIVILEGE` + restricting SID 列表）+
  `SetTokenInformation` 降为 Low Integrity（`S-1-16-4096`）+ `CreateProcessAsUserW` 以 `_CREATE_SUSPENDED` 创建；
  创建后校验 `RestrictedTokenProfile`（`is_restricted`、`integrity_rid == 4096`、`privilege_count <= 1`），不满足即 `PermissionError`；
- `KillOnCloseJob`：匿名 Job Object 设 `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，进程树围栏；
- 显式句柄继承：`_PROC_THREAD_ATTRIBUTE_HANDLE_LIST`，创建前后清除继承位（`_clear_handle_inheritance`）。

状态：`code_present` + `verified`（`tests/test_windows_native.py`、`tests/test_body_process.py`）；
OS principal 安装态运行：`not_proven_runtime`。

### 1.3 公共管道与对等认证 —— `src/agentic_evo/windows_pipe.py`、`src/agentic_evo/service.py`

- `WindowsPublicPipeListener`：`CreateNamedPipeW` + `_PIPE_REJECT_REMOTE_CLIENTS`，DACL 绑定
  `current_process_sid()`（宿主 SID），客户端认证前置字节 `\x00`，拒绝远程客户端；
- `WitnessService`（`service.py`）docstring 明确：**前台排演，不描述为独立 OS principal**；
  公共操作 allowlist 为 `status / wake / sleep / observe`（`_PUBLIC_PARAMETERS`），
  控制端点（`agentic-evo-off-rehearsal-v1`）只允许 `off` 排演；
- `ipc.py`：`PUBLIC_PROTOCOL = "agentic-evo-public-v2"`、`MAX_PUBLIC_FRAME_BYTES = 64 KiB`、
  请求帧校验（`validate_public_request_frame`）、超时 `PUBLIC_IO_TIMEOUT_SECONDS=2.0s` / `PUBLIC_RESPONSE_TIMEOUT_SECONDS=12.0s`。

状态：`code_present` + `verified`（`tests/test_windows_pipe.py`、`tests/test_witness_service.py`、`tests/test_cli_lifecycle.py`）；
安装态 service-SID ACL：`not_proven_runtime`。

### 1.4 Gate B 证据边界 —— `src/agentic_evo/windows_gate_b_evidence.py`、`tools/windows-gate-b-experiment.ps1`

- `verify_gate_b_evidence()`：只读、无提升、无写证据；`zero_residue_observed_now` 观察当前计划命名空间是否为空；
- `exercise_gate_b_evidence_attacks()`：仅在 disposable 副本中演练 A01/A03/A04/A05/A07；
- `tools/windows-gate-b-experiment.ps1`：`-Mode` 取值 `Plan | Run | Elevated | RetainedPreflight`；
  `RetainedPreflight` 输出 schema `agentic-evo.windows-gate-b-retained-preflight.v1` 的 plan-only 工件：
  `authorized_effects`（`temporary_service=true`、`system_restart=true`，`permanent_service/hook/genesis=false`）、
  `retention`（跨恰好一次重启保留到 post-restart 验证后移除、`reversible_uninstall_required=true`）、
  `claim_ceiling`（`gate_b=not_established`、`native_security_verified=false`、`ready_to_install=false`）。

状态：`code_present` + `verified`（`tests/test_windows_gate_b_evidence.py` 36 例、`tests/test_windows_gate_b.py`）；
Gate B 结论：`not_proven_runtime`；重启实验：`blocked_authorization`。

## 2. Windows 安装 / 运行 / On-Off / 恢复 / 卸载入口清单

| 条目 | 代码位置 | 现状 | 状态标签 |
| --- | --- | --- | --- |
| 安装（创建服务） | `tools/windows-gate-b-experiment.ps1` `-Mode Run/Elevated`（实验脚本，`temporary_service=true`） | 从未安装任何服务；`plan-install` 输出 `install_service=false` | `not_proven_runtime`（`code_present`） |
| 永久服务安装 | `install_plan.py`（`permanent_service=false`）、`blockers` 含 `native_service_artifacts_and_reversible_uninstall` | 未实现/未授权 | `blocked_authorization` |
| 运行（SCM principal） | `service.py` `WitnessService.serve_forever()`（前台排演 + `ExclusiveFileLock` 单例） | 仅 foreground 排演，非 OS principal | `not_proven_runtime` |
| On / Off | `cli.py` `serve/status/off/hook`；`ipc.py` `OffRehearsalClient.off()`；`trusted.py` `set_authority`/`set_off_rehearsal`（Off 事件 `host_off`/`control_rehearsal_off`） | Off 排演已测；On 需 `host_binding` 与 `state.who` HMAC 比对（`hmac.compare_digest`） | `verified`（rehearsal 层）；`not_proven_runtime`（HostPresence 层） |
| 恢复 / 重启验证 | `tools/windows-gate-b-experiment.ps1` `RetainedPreflight`（`retention`：跨一次重启保留、post-restart 验证后移除） | plan-only 工件，无 service 创建/无重启触发 | `design_ready` + `code_present`；重启执行 `blocked_authorization` |
| 卸载（零残留） | `windows_gate_b_evidence.py` `_observe_current_zero_residue`（当前命名空间缺失 + `sc.exe query`）；U01 案例 `partial_cleanup_if_service_lifecycle_occurs`；`reversible_uninstall_required=true` | 当前命名空间为空已观察（`zero_residue_observed_now`）；历史 privileged 事件未被独立观察 | `verified`（当前观察）；`not_proven_runtime`（卸载/重启后零残留，直到重启实验） |
| 有界睡眠 | `wake/sleep` session 生命周期（`runtime.py` `WakeState`、`trusted.py` `start_session/end_session`） | session 层已验证；OS 层睡眠不在代码内 | `verified`（session 层） |

## 3. 升级入口（Upgrade entry）

- 升级的**版本化基础**已就绪：`trusted.py`（`TRUSTED_SCHEMA_VERSION = "agentic-evo-trusted-v2"`、
  `CHECKPOINT_SCHEMA_VERSION = "agentic-evo-checkpoint-v1"`、`SESSIONS_SCHEMA_VERSION = "agentic-evo-sessions-v2"`）、
  `evidence.py`（`EVIDENCE_SCHEMA_VERSION = "agentic-evo-evidence-v1"`）、`body.py`（`BODY_SCHEMA_VERSION = "agentic-evo-body-v2"`）、
  `autonomous_loop.py`（`AUTONOMOUS_LOOP_SCHEMA_VERSION`/`CONSOLIDATION_SCHEMA_VERSION`），详见 `schema-versioning.md`；
- 迁移纪律：append-only + hash 链，**无 in-place 重写**（`evidence.py._verify_records`、`trusted.py._verify_connection` 对不支持 schema 直接 `IntegrityError`）；
- 状态：`code_present` + `verified`（`tests/test_trusted_transactions.py`、`tests/test_evidence.py` 等）。

**升级流程本身（自动迁移工具/跨版本升级入口）未实现**：`design_ready`，不声称已可用。

## 4. macOS / Linux 状态（不得声称已验证）

- `src/agentic_evo/native_backends.py` 定义 `DARWIN_BACKEND`（launchd_launchdaemon / xpc_audit_token / dedicated uid）与
  `LINUX_BACKEND`（systemd_system_service / AF_UNIX SO_PEERCRED / dedicated system uid）；两者 `native_test_status = "not_run"`；
- `install_plan.py` 通过 `dict(DARWIN_BACKEND)` / `dict(LINUX_BACKEND)` 把同一协议后端写入计划；
- 状态：`design_ready`（同协议 backend 已规划）；**不验证**。若后续声称已验证，必须先有目标平台自动化验收证据。

## 5. 授权边界与阻塞项

- UAC / 系统重启 / 永久服务 / 正式 Genesis / 推送私有远端：全部属于 `GOAL.md` 不可变边界第 5 条，必须事先获得明确授权；
- 当前状态：`blocked_authorization`（重启验证待 the operator 授权；正式 Genesis 待授权；私有远端推送待授权）。

## 6. 声明上限（Claim ceilings）

- **不得声称**：原生安装/卸载已验证（Gate B 未建立）；macOS/Linux 已验证；存在运行中 SCM principal / service-owned protected state / 正式 Genesis；
- **可声称**：确定性 plan-only 计划（`ready_to_install=false`）；foreground 四项局部原生证据；Gate B retained-preflight 为 plan-only 预注册工件；当前命名空间零残留观察。
