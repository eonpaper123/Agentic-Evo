# Agentic-Evo v1.0 Goal

状态：active  
适用范围：`D:\rawle\Coding\Agentic-Evo`，当前分支 `agent/document-memory-theory`，第一参考实验台为 `3060-computer`

目标：把当前 Pre-Genesis 纵切面推进为一个可安装、可运行、可复现实验、能在真实 coding-agent 使用过程中零人工逐步介入地持续学习与自我进化的 `Agent Developmental Runtime`。唯一宿主始终是用户本人；机器、Codex、基础模型、GPU、工具和项目都只是可更换的身体、器官或执行表面。

从这里开始：
1. 进入仓库根目录，先核对 `git status`、`HEAD`、远端和真实机器状态。
2. 以当前已提交代码为起点，优先重跑关键入口：`$env:PYTHONPATH='src'; & 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_windows_gate_b_evidence`。
3. 旧电脑 `artifacts/windows-gate-a`、`artifacts/windows-gate-b` 只算历史证据，不得冒充 `3060-computer` 当前证据。
4. 当前实验台身份、环境承诺和证据谱系以 `experiments/labs/3060-computer.json` 为起点；不记录硬件序列号、凭据或秘密。
5. 若文档与代码、测试、实时观察或不可变原始证据冲突，先修正文档，不反向篡改事实。

权威顺序：
1. 真实证据、当前代码、测试、运行结果、不可变原始记录。
2. `docs/engineering/实现状态_Pre-Genesis.md`
3. `docs/开发框架.md`
4. `docs/Agent_Runtime_Intelligence_最终产品与科研说明.md`
5. `docs/engineering/工具与实验共生开发计划.md`
6. `experiments/001_机器级连续生命循环.md`
7. `docs/topics/*` 与 `docs/research/*`

模型分工，硬规则：
1. 所有推理工作必须交给 `gpt-5.6-sol` + `reasoning_effort=xhigh`：需求解释、第一性拆解、架构与协议、研究假设、实验设计、因果归因、威胁模型、失败诊断、验收裁决、停止点、代码与证据审查、是否达到 v1.0。
2. 所有已被 Sol 明确定义、无需新增语义判断的执行工作交给 `gpt-5.6-terra` + `reasoning_effort=high`：按规格改代码、补测试、跑验证、机械重构、环境探测、证据采集、文档落盘和 Git 操作。
3. Terra 不得自行改变目标、理论、权限边界、验收语义或发明机制；一旦实现中出现歧义，必须回到 Sol。

第一性原理、剃刀与开放自由度：
1. 每一步先问：删掉现有名词、模块和实现习惯后，目标还不可再删地依赖哪些因果关系。
2. 只实现能产生可观察、可证伪未来因果力的最小机制；新增抽象、模块、依赖、规则或限制，必须有真实反例或实验收益支撑。
3. 人类只冻结理论基础、方向、科研可观察性、宿主根和权限边界；不得预设记忆 schema、检索/遗忘算法、常识内容、信号类型、目标权重、学习速度、能力目录、候选生成、评价函数、进化节奏或元学习算法。
4. `Body/Agent` 可以根据现实后果自主创造、替换、压缩或淘汰这些机制，包括重构自身记忆、能力、实验方法、evaluator 与身体架构。

不可变边界：
1. 微型生命核保持 `O(1)`，只维持 `Who / Why / Authority / Root / Head`、唯一宿主和用户 `On/Off` 权。
2. Body、模型、候选后代和 evaluator 都不能重写生命核。
3. 原始经历、实验动作、失败、权限与外部结果追加保存；解释可以变化，历史不能无痕改写。
4. 工具不得直接自改生产项目，只能通过用户已授权的 coding-agent 或工具表面提供能力。
5. `UAC`、系统重启、持久服务、签名/凭据、外发数据、不可逆操作、正式 Genesis 都必须先获得明确授权。
6. 失败允许；伪成功、隐藏失败、知道反例后不修复、把 receipt 或 self-report 当独立事实不允许。结论只允许 `not_run / inconclusive / failed / passed`。

v1.0 必须同时成立：
1. 参考实现完成 Windows 原生安装、运行、恢复、On/Off、卸载零残留与有界睡眠；macOS/Linux 至少完成同协议 backend 与自动化验收框架，未实证的平台不得声称已验证。
2. 同一 Agent 跨对话、跨项目、跨模型强弱变化、跨进程重启持续存在，且至少接入 `Codex + 一个第二真实 coding-agent/通用表面`。
3. 形成 immutable experience archive、主动回忆/遗忘/睡眠巩固与 `Memory-to-Capability` 机制空间，但不把具体算法写死给人类。
4. 在正常任务流中无需人工逐步指挥即可完成 `observe -> 候选问题/变化 -> probation -> 读取现实后果 -> commit/rollback -> consolidate/migrate`，并允许 evaluator 与学习方式继续进化。
5. 存在预注册、可重跑的真实纵向实验，能证明某段经历对后续新会话、模型或执行表面的行为产生可归因能力增益；协调替换、伪 receipt、负迁移、错误候选、断连、崩溃和回滚都有反例测试。
6. 发布物必须包含安装/升级/卸载入口、adapter contract、复现实验说明、威胁模型、数据/隐私边界、版本化 schema、测试与迁移说明；Git 中保留聚焦提交并推送私有远端。

完成判定：
只有当以上六类条件全部被当前证据直接支持，且文档、代码、测试、实验记录和实际运行状态一致时，才允许判定 `Agentic-Evo v1.0` 完成；否则继续推进，不得缩小目标。
