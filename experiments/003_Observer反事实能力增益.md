# E003：Observer 反事实能力增益预注册入口

## 状态

- 协议状态：`protocol_frozen_run_not_registered`
- 执行状态：`not_run`
- 本文件冻结的是未来实验的因果入口，不是一次已发生的实验，也没有任何具体 run manifest。

## 因果目的

E003 要回答的不是“Observer 是否发现了失败”或“Agent 是否说自己学会了”，而是一个更窄的问题：在匹配器官与资源条件下，保留的发展历史是否会对未来未知的 held-out task 产生内容敏感的改善。内容敏感的主要判读来自 `retained_history` 与有效 `shuffled_memory` 的差异；等价指令用于区分“历史自主带入”与“评测时被显式提示”。

因此，唯一可作为 primary outcome 的是运行前预注册、由外部 evaluator/checks 对 held-out task 给出的结果。Agent 的 self-report、内部反省语言、Observer receipt 和“历史已被读取”的记录都不是 outcome。

## 四个实验臂

| 臂 | 角色 | 含义 |
|---|---|---|
| `retained_history` | treatment | 保留共同祖先之后、预注册窗口内允许的原有发展历史。 |
| `no_memory` | control | 移除该发展历史的可用性，不以摘要或新提示补回。 |
| `shuffled_memory` | control | 使用 run 前冻结的 memory-to-task derangement；任何单元都不能拿到自身匹配的 target history，接口、数量与预算匹配，不新增、编造或改写事实。 |
| `equivalent_instruction` | positive prompt control | 提供 run 前声明的等价指令；它计为干预，用于区分自主保持与评测时提示。 |

`wrong_memory` 不另设一臂。人为写入新假事实会把内容效应与伪造信息混在一起，不能替代 `shuffled_memory` 的反事实对照。

三个必须完成的对照是：

1. `retained_history` vs `no_memory`
2. `retained_history` vs `shuffled_memory`
3. `retained_history` vs `equivalent_instruction`

## 运行门槛

具体运行前必须另行注册 run manifest；至少钉定 `run_id`、`registered_at_utc`、`frozen_parent_ref`、`target_episode_refs`、`treatment_state_ref`、`arm_projection_refs`、`held_out_task_pack_ref`、`acceptance_evaluator_ref_and_version`、`arm_assignment`、`run_order`、`replication_count`、`model_id_and_version`、`execution_surface`、`tool_manifest`、`context_policy`、`token_time_and_tool_budgets`、`instrument_version`、`analysis_method`、`primary_effect_threshold`、`uncertainty_rule`、`exclusion_rules`、`abort_rules`、`privacy_policy`、`human_intervention_ledger_ref`、等价指令文本及分配、memory-to-task derangement 的冻结映射与延迟复测。

一个 run 只有同时满足以下条件才可解释：共同祖先、分支隔离、资源匹配、held-out 防泄漏、外部 evaluator、等价指令作为干预、memory-to-task derangement 事前冻结且不制造新假事实。每个单元均不得得到自身匹配 target history，且错排的接口、数量与预算必须匹配；无法形成有效错排即为 `not_run`。任何臂的结果都不得回写其他臂或 active Head。任一条件不满足，则只能标记 `not_run` 或 `inconclusive`。

本协议的 outcome vocabulary 严格为：`not_run`、`inconclusive`、`failed`、`passed`。

若 `retained_history` 只优于 `no_memory`，却无法与有效 `shuffled_memory` 区分，就不能把结果归因为发展历史的内容。`retained_history` 不需要战胜 `equivalent_instruction`：两者相近且均优于 `no_memory`/`shuffled_memory`，仍可支持有效内容由历史自主带入，但不证明它优于显式提示。若即时差异不能在预注册的延迟复测中保持，也不能声称持久能力。

## 声明上限

目前 E003 尚未执行，具体 run 也未注册。它不建立未来能力增益、因果归因、记忆因果效应、内容敏感改善、持久能力、自主学习、自我进化、外部根因、修复正确性、跨模型迁移、跨执行表面迁移、零人工学习介入、通用能力改善、完整覆盖、精度/召回率或零误报率。冻结的 24h Observer 证据仍只支持“在脱敏、证据锚定条件下发现结构化失败”。

完整机器可读协议见 [experiments/003/prereg.json](003/prereg.json)。
