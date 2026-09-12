# 威胁模型（Threat Model）

> 发布包脚手架文档。威胁条目以**真实代码信任边界**为依据：Witness TCB（身份/谱系/证据；**不含记忆**）、
> 微生命核 O(1)（Who / Why / Authority / Root / Head）、本地 HMAC checkpoint、loop 策略门（外部副作用默认拒绝）、
> Body/调用方自有模块、UAC/服务/重启边界、证据来源证明。每条威胁给出：资产、威胁、既有控制、残余风险、状态标签。

## 状态图例（Status Legend）

| 状态标签 | 含义 |
| --- | --- |
| `design_ready` | 设计已定稿，实现尚未落地 |
| `code_present` | 实现已存在于仓库，未被本次运行实测 |
| `verified` | 有自动化测试或 smoke 产物支撑 |
| `not_proven_runtime` | 代码存在，但真实运行时证据尚未取得 |
| `blocked_authorization` | 被明确授权边界阻止 |

## 1. 威胁清单

### T1 微生命核被改写（Who / Why / Authority / Root / Head）

- **资产**：生命核（`kernel.py` `KernelSnapshot` 五字段；`trusted.py` state 单例行）；
- **威胁**：Body / 模型 / 工具 / 候选后代经普通权限改写生命核，伪造 Root/Head/On-Off；
- **既有控制**：`MicroLifeKernel` 签名 state（`authority.key` HMAC）；`TrustedState._require_on` / `HeadConflictError` /
  `RootBindingError`；Body 内容寻址（`body.py` commitment=sha256(canonical manifest)）；
  `advance_head` 必须 `expected_head` 精确匹配且候选 `parent_head` 匹配；loop 引擎 `LIFE_CORE_SCOPES` 拒绝候选作用域；
- **残余风险**：同普通用户权限域内的进程均可读本地 key（`最小可信边界与来源证明.md` 的 same-principal 不可区分定理）；
  安装态 service-SID 隔离未建立；
- **状态**：`verified`（`tests/test_trusted_transactions.py`、`tests/test_lifecycle.py`）——Pre-Genesis 层；
  OS principal 层：`not_proven_runtime`。

### T2 Witness TCB（身份 / 谱系 / 证据）完整性

- **资产**：Root 承诺、Head 谱系、evidence 链、session 承诺；
- **威胁**：篡改历史后重签有效见证（伪造证据链）；
- **既有控制**：evidence append-only hash 链（`evidence.py._verify_records`：`previous_integrity_hash` + canonical JSON 重算）；
  本地 checkpoint 每事件一条 + HMAC-SHA256（`trusted.py._insert_checkpoint` / `_verify_connection`，witness.key 32 字节）；
  session 承诺 `sessions_hash` 进入 state；`author_kind=surface_unverified/control_unverified` 显式标注来源；
- **残余风险**：HMAC key 与 state 同机同权限存放；本地攻击者/同用户进程可读 key 后重放（来源证明只到“哪个受保护通道提交”，
  不到“谁在真实世界中创建”，见 `最小可信边界与来源证明.md`）；
- **状态**：`verified`（`tests/test_trusted_transactions.py` 10、`tests/test_evidence.py` 7）；OS 级 Witness：`not_proven_runtime`。

### T3 记忆存储被当作 TCB 或反向污染生命核

- **资产**：`<agent home>/memory/camus.jsonl`（Body-owned CAMU，`memory_store.py`）；
- **威胁**：记忆机制被误当作 Witness 可信状态；记忆内容反向改写生命核/谱系；
- **既有控制**：memory_store 明确“必须 NOT import runtime/body/kernel/trusted/witness”（docstring），
  memory 记录是声明式、可替换、永不重写；hash 链 + `verify_chain` 检出篡改；`evidence_refs` 只按哈希引用证据；
- **残余风险**：记忆语义（activation/prediction）由 Body 自定，不校验正确性；
- **状态**：`code_present`（分支 `2b09bd2`）+ `design_ready`（机制空间）；**记忆不在 Witness TCB 内**。

### T4 自治循环的外部副作用

- **资产**：调用方工作区与外部世界；
- **威胁**：循环自动对外部施加不可逆效果（改生产项目、网络、进程）；
- **既有控制**：`LoopPolicy(authorize_external_effects=False)` 默认拒绝；`effect_kind="external"` 抛 `PolicyGateError`；
  `CandidateRepair` 强制 `reversible=True`、`touches_life_core=False`、作用域不得落入生命核；probation 有界
  （`max_probe_runs=5`、`max_probation_seconds=30.0`）；循环只写自己的 home（`meta.json/events.jsonl/consolidation.jsonl`），无网络/子进程；
- **残余风险**：调用方提供的 probe/candidate 正确性不在引擎职责内；显式授权后外部效果可发生（loop-demo 即如此）；
- **状态**：`verified`（smoke 产物 + `docs/engineering/autonomous-loop.md`）；loop-demo 真实文件修复：`code_present`（分支）。

### T5 公共 Surface / IPC 滥用

- **资产**：`status/wake/sleep/observe` allowlist、control Off 端点、`(execution_surface, session_id)` 会话；
- **威胁**：任意本机进程伪造身份、超界帧、越权操作、远程客户端接入；
- **既有控制**：帧边界 64 KiB + 信封/参数集合强校验（`service.py._validate_request_envelope/_validate_parameters`）；
  `request_id` ≤128 且有界；IO 超时（2s/12s）；Windows 公共管道显式 DACL 绑定宿主 SID + `PIPE_REJECT_REMOTE_CLIENTS` +
  认证前置字节（`windows_pipe.py`）；control 端点只允许空参数 `off`（`_validate_control_request`）；
- **残余风险**：control 端点本身未认证（`control_unverified`）；同用户客户端彼此不可区分（来源证明上限）；
  安装态 service-SID ACL 未建立；
- **状态**：`verified`（`tests/test_windows_pipe.py`、`tests/test_witness_service.py`）；安装态：`not_proven_runtime`。

### T6 Body 子进程逃逸 / 句柄泄漏

- **资产**：Body 子进程隔离与私有谱系通道；
- **威胁**：Body 逃出 Job/低完整性边界，继承未授权句柄，伪造 `prepare/advance` 结果；
- **既有控制**：`spawn_restricted_suspended_process`（restricted Low-IL token + suspended + 显式句柄列表 + 创建后 token 画像校验）；
  `KillOnCloseJob` 进程树围栏；私有通道协议 `agentic-evo-private-boot-v1` / `agentic-evo-private-lineage-v1`（`body_process.py`），
  超时后结果 `Unknown`（`BodyLineageOutcomeUnknown`，不做盲目接受）；
- **残余风险**：foreground 同用户权限域排练，不构成安装态不可绕过的 principal 认证（`Windows原生Witness边界.md` 明确）；
- **状态**：`verified`（`tests/test_windows_native.py` 5、`tests/test_body_process.py` 24）；OS principal：`not_proven_runtime`。

### T7 UAC / 服务 / 重启边界

- **资产**：系统提升点、SCM service 生命周期、重启后状态；
- **威胁**：提升执行被滥用、服务残留、重启后无法验证、不可逆卸载；
- **既有控制**：Gate A no-UAC bundle（`windows_gate_a.py`，SCM-only probe，控制台外返回码 1063 fail-closed）；
  Gate B 实验脚本仅一次性、plan-only、claim ceiling 强制 `gate_b=not_established`；`RetainedPreflight` 要求
  `reversible_uninstall_required=true` 且 post-restart 验证后移除；`_observe_current_zero_residue` 观察当前命名空间为空；
  `windows_gate_b_evidence.py` 只读校验 + disposable-copy 攻击演练；
- **残余风险**：重启验证从未执行（service 已移除、`reboot_validation=not_performed_service_removed`）；U01 仅 partial cleanup 语义；
- **状态**：`blocked_authorization`（重启/UAC 授权未批准）；`not_proven_runtime`（Gate B）。

### T8 证据来源证明（provenance）被误读

- **资产**：evidence 的 `author_kind/source_kind/execution_surface/session_id` 等来源字段；
- **威胁**：把“记录里写的作者”当成“真实世界中的创建者”；把 receipt/self-report 当独立事实；
- **既有控制**：来源字段显式降级（`surface_unverified`、`control_unverified`、`author_kind=research_instrument`）；
  实验 pack claim ceiling 中 `declared_metadata_truth=not_established`、`causal_attribution=not_established`（`experiment_pack.py`）；
  相同权限域下作者不可区分定理（`最小可信边界与来源证明.md` 公式）；
- **残余风险**：人类干预与 agent 自主动作在同类通道下不可区分；
- **状态**：`code_present` + `verified`（信封与 ceiling 校验有测试）；真相层面 `not_proven_runtime`。

### T9 数据外泄 / 敏感内容进入 evidence

- **资产**：prompt/工具内容、密钥、本地数据；
- **威胁**：原始内容被复制进 evidence、敏感键进入 payload、数据离机；
- **既有控制**：codex 适配器只记 `sha256` + 字符数（不复制原文）；`_reject_sensitive_keys` 拒绝
  `access key/password/secret/token/authorization/cookie/credential` 等键（`evidence.py`）；payload 上限 16 KiB / 32 键；
  循环引擎无网络、无子进程（docstring）；`GOAL.md` 规定外发数据必须授权；
- **残余风险**：卸载残留（Gate B 零残留未在重启后证明）；人工复制粘贴超出代码边界；
- **状态**：`code_present` + `verified`（payload 校验有测试）；零残留：`not_proven_runtime`。

## 2. 声明上限（Claim ceilings）

- 上述 `verified` 仅指 **Pre-Genesis 可移植层 / foreground 局部证据**，不构成安装态安全结论；
- `gate_a_complete=false`、`gate_b_outcome=not_established`、`native_security_verified=false`、`ready_to_install=false` 四处恒为假；
- 记忆（memory）**不在** Witness TCB；loop 引擎不自动修改生产策略；未授权不得执行 UAC/重启/永久服务/Genesis。
