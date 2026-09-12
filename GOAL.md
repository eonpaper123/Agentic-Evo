# Agentic-Evo v1.0 Goal

状态：active  
适用范围：`D:\rawle\Coding\Agentic-Evo`；第一参考实验台为 `3060-computer`。根 checkout 的当前分支不再代表项目总进度，必须同时核对集成分支、研究分支、独立 worktree 与实时实验状态。

目标：把当前已进入真实出生 home、跨 coding-agent 接入和纵向观测阶段的纵切面，推进为一个可安装、可运行、可复现实验、能在真实 coding-agent 使用过程中零人工逐步介入地持续学习与自我进化的 `Agent Developmental Runtime`。唯一宿主始终是用户本人；机器、Codex、基础模型、GPU、工具和项目都只是可更换的身体、器官或执行表面。

从这里开始：
1. 进入仓库根目录，先核对 `git status`、`HEAD`、远端和真实机器状态。
2. 先运行 `git worktree list` 与 `git log --all --oneline --decorate -20`；当前实现至少要对照 `agent/v1.0-integration`、`agent/memory-capability-loop`、Observer worktree 及实时 receipts，不能只看根 checkout。
3. 旧电脑 `artifacts/windows-gate-a`、`artifacts/windows-gate-b` 只算历史证据，不得冒充 `3060-computer` 当前证据。
4. 当前实验台身份、环境承诺和证据谱系以 `experiments/labs/3060-computer.json` 为起点；不记录硬件序列号、凭据或秘密。
5. 若文档与代码、测试、实时观察或不可变原始证据冲突，先修正文档，不反向篡改事实。

权威顺序：
1. 真实证据、当前代码、测试、运行结果、不可变原始记录。
2. 当前集成/研究分支的代码、测试和对应实现报告，以及 `.lingtai/agentic-evo-total/work/` 中可复核的实时实验 receipts。
3. `docs/engineering/实现状态_Pre-Genesis.md` 仅是 2026-08-04 历史快照；其中“没有真实 Agent、第二真实 coding-agent 或纵向证据”等表述已被后续本地证据部分超越，不得继续作为当前结论。
4. `docs/开发框架.md`
5. `docs/Agent_Runtime_Intelligence_最终产品与科研说明.md`
6. `docs/engineering/工具与实验共生开发计划.md`
7. `experiments/001_机器级连续生命循环.md`
8. `docs/topics/*` 与 `docs/research/*`

`GOAL.md` 对 v1.0 目标、不可变边界、模型分工和完成判定具有最高规范权威；下列顺序只裁定当前事实与实现状态，不得以当前代码或证据缩小最终目标。

当前事实起点（2026-08-14）：
1. 已有 Genesis CLI 出生并经 `runtime-adopt` 接入的真实实验 home；它不是正式机器级 Genesis，也未建立 Gate B。
2. OpenCode 已在真实任务中接入并产生纵向 receipts；这证明第二真实 coding-agent 接入存在，不证明跨重启连续性或因果学习。
3. Memory-to-Capability 已形成“真实经历→CAMU→能力卡→未来候选→结果记录”的工程闭环；当前比较仍是描述性的，能力增益未建立。
4. Observer 24 小时真实使用试运行已于 2026-08-14 正常结束并冻结证据：1,335 个周期、28,436 条脱敏派生记录、14 个唯一 Observation（9 confirmed TP、0 known FP、5 unresolved）。产品修复仍在独立未提交 worktree 中；不得把本地代码、unresolved 项或描述性证据冒充已发布能力增益。

本轮实施前工程快照（2026-09-12，保留供追溯）：
1. `run-codex` 显式入口已完成一次真实编码任务，并将有界可读的请求、工具动作、结果和回复写回同一谱系；后续会话当时只加载历史引用，尚未形成 successor。
2. Codex hooks 已配置并获得信任，但本机 Codex CLI/Desktop 没有自然派发出可用事件；当时集成分支的 OpenCode adapter 仍是 hash-only。
3. 正式 runtime home 当时仍为 generation 1，尚无自主 candidate/successor。
4. 当时 Witness 不可用，release 仍是 plan-only。以上均为本轮工程推进前的历史状态，不再作为当前能力结论。

当前工程事实（2026-09-12，已安装 RC7）：
1. 用户级 release 已真实安装并由 RC1→RC2→RC4→RC5→RC6→RC7 升级；当前 Witness 为 `0.1.0-rc.7`，Authority On、Low-IL Body Ready，并在升级中保留同一 Root、generation 2、Head 和全部历史。RC7 的升级回执、安装 manifest 和运行中 Witness 版本一致。Windows HKCU 登录启动项已配置，但尚未观察一次真实重新登录后的自然启动；macOS/Linux 尚未实机运行。
2. 模型器官曾在无人逐步指定学习内容的 development opportunity 中读取同 Root 经历，自主写出两份 Body 文件并把 Head 从 generation 1 推进到 generation 2。generation 2 随后被真实日常 Codex 会话加载；一次失败会话和一次成功双轮会话的后果均回流，Body 分别接收到宿主自动提供的一页初始经历并选择 `no_change`。这证明“自写后继→后来加载→现实后果回流→再次自主选择”的工程链存在，但现有记录未证明 Body 主动发起了追加分页，也不证明候选改善了能力，不把 `no_change`解释成 retain。
3. RC4 阶段的 `Evo Codex.cmd` 已真实从项目目录直接启动，同一原生 Codex thread 完成两轮任务，中文和特殊字符正确显示；第二轮修正了首次缺少 `PYTHONPATH` 的调用并使两个聚焦用例通过。RC7 现已安装有界、脱敏的工具输出显示。
4. release 主链现已实现 install、upgrade、status、recover、On/Off、shutdown 和默认保留可迁移数据的 uninstall；真实安装与升级已观察，Off/On、在途取消、卸载/保留数据/重装及真实 Windows 登录恢复仍待同一实现验收。不得把尚未观察的生命周期结果写成完成。
5. Codex 的产品入口是 Evo 托管的显式日常会话，不是原生 Desktop/TUI 透明附着；受信 hooks 仍未证明自然派发。OpenCode 不作为默认产品依赖。LingTai 是可选第二 coding-agent harness，不是模型接口；RC6 的 `Evo LingTai.cmd` 已在同一 Root、generation 2 Head 下通过原生模型与 file/shell 工具完成一次真实文档任务，任务结果回流后 Body 接收到宿主自动提供的一页同 Root 初始事件并选择 `no_change`。这证明第二 harness 的真实任务闭环，不证明 Body 主动追加分页、长期可靠性、能力改善或 RSI 完成。
6. 当前 RSI 是可继承的内容/元策略级递归自修改：Body 的 activation 会进入后续 coding 行动；activation 与全部可读 Body 文本文件会进入下一次 development prompt。唯一可执行 activation 仍是 `surface-context-utf8-v1`；Body 内的 Python、skill 或 workflow 文件不会因后缀自动执行，runner、调度、动作协议、权限与模型器官配置仍由人类实现固定。尚未证明“自改发展方法→下一轮采用→产生新的后继改进→按现实后果保留或撤回”的完整 RSI 闭环。
7. RC7 已安装当前 Head 的谱系事实投影，包括 manifest、直接父代、代际、实际 `head_advanced` 引用和最后一条真实 retain/withdraw 决议引用；后续 Body 可通过有界历史回忆读取 `head_advanced` 事件。无记录时明确为 `no_recorded_resolution`，不会从 generation、`no_change`、任务成功或记录缺失推断待决、保留、稳定或更优。`withdraw` 的已安装语义是以直接父代内容生成一个新的纠正后继，不回拨 Head、不删历史。首次 generation 2 缺失的 action 不补造。
8. RC7 已安装运行期有界尾校验，移除了 status、lease 与每次写事务对全部历史的重复扫描；完整历史验证仍保留在 Genesis、启动加载与显式 verify，运行期只检查当前 state、活动 sessions 和最新 event/checkpoint 尾。对正式 12125 条历史的隔离副本实测：status 约 0.002 秒，session start 约 0.017 秒，append 约 0.007 秒，session end 约 0.014 秒，均低于现有 12 秒 IPC deadline；启动和显式全链验证仍分别约 0.94 秒、1.57 秒。旧 RC4 的第三次真实 console 超时失败仍保留为历史事实。
9. 当前 Low-IL Body 进程只提供低完整性与受限令牌，不是任意 Body 代码的读取或网络隔离：一次非秘密 canary 已实测它仍可读取同一用户的外部文件并连接本机 loopback。可执行发展入口、descriptor 继承/清除语义、模型器官无本机工具配置及 Windows LPAC/AppContainer 隔离正在源码中实现，尚未打包、安装或由任何 Head 启用；只有在 LPAC 实测满足 AppContainer/LPAC 身份、外部文件拒读、loopback 拒绝、私有 scratch 可写、显式 pipe 握手及 Off 杀进程树后，才允许激活 Body 自写的可执行发展文件。现有 generation 2 仍是文本 Body，不补写成可执行 RSI 证据。

模型分工，硬规则：
1. `gpt-5.6-sol` + `reasoning_effort=xhigh` 是日常主线负责人：解释需求、做第一性拆解、形成执行计划、协调实现、集成并核对证据；不得用局部实现缩小最终目标。
2. 遇到未确定的方法或路线、路线失效的新证据，以及每个实质性交付的最终结论，必须调用一次真实的 `gpt-6-astra` + `reasoning_effort=high`；Astra 不常驻，也不替代 Sol 的持续推进责任。
3. Sol 已明确定义且无需新增语义判断的实现、修复、测试、环境探测、证据采集、文档落盘和 Git 操作交给 `gpt-5.6-terra` + `reasoning_effort=max`。Terra 不得自行改变目标、理论、权限边界、验收语义或发明机制；出现歧义必须回到 Sol。
4. 适用的既有 Spec、已验证实现或明确标准流程可以省略执行前 Astra 选路，但不能省略实质性交付的 Astra 最终结论；模型不可用时不得静默替代。
5. 以上硬路由只约束推进 v1.0 的外部研发、实验与验收工作流，不写入生命核或 Body；被研究 Agent 对未冻结空间保有自主推理、提出问题与假设、选择并进化推理方式、形成并调整发展 focus 与价值判断的自由。

第一性原理、剃刀与开放自由度：
1. 每一步先问：删掉现有名词、模块和实现习惯后，目标还不可再删地依赖哪些因果关系。
2. 只实现能产生可观察、可证伪未来因果力的最小机制；新增抽象、模块、依赖、规则或限制，必须有真实反例或实验收益支撑。
3. 人类只冻结理论基础、方向、科研可观察性、宿主根和权限边界；不得预设记忆 schema、检索/遗忘算法、常识内容、信号类型、目标权重、学习速度、能力目录、候选生成、评价函数、进化节奏或元学习算法。
4. `Body/Agent` 可以根据现实后果自主创造、替换、压缩或淘汰这些机制，包括重构自身记忆、能力、实验方法、evaluator 与身体架构。
5. 能力可按现实后果编译为可回忆知识、skill/workflow、Body 代码或策略、参数/adapter/优化器状态、Agent 架构或新的学习算法；任何一种都只是可替换基质。模型专属参数变化不是身份，发生更新也不等于能力增长。

第一性原理与奥卡姆剃刀只约束人类的研究和工程预设，不是写入 Agent 身体的永久最小化目标；复杂性若产生独特、可证伪的因果作用，必须允许 Agent 保留和发展它。

不可变边界：
1. 微型生命核保持 `O(1)`，只维持 `Who / Why / Authority / Root / Head`、唯一宿主和用户 `On/Off` 权。
2. Body、模型、候选后代和 evaluator 都不能重写生命核。
3. 原始经历、实验动作、失败、权限与外部结果追加保存；解释可以变化，历史不能无痕改写。
4. 工具不得直接自改生产项目，只能通过用户已授权的 coding-agent 或工具表面提供能力。
5. `UAC`、系统重启、持久服务、签名/凭据、外发数据、不可逆操作、正式 Genesis 都必须先获得明确授权。本 Goal 内用户已明确授权的持续工程、本机部署、服务、hooks 与 UAC 范围持续有效，不得把本条当成重复索权 gate；只有新增范围或不可逆外部影响才重新确认。
6. 失败允许；伪成功、隐藏失败、知道反例后不修复、把 receipt 或 self-report 当独立事实不允许。结论只允许 `not_run / inconclusive / failed / passed`。

产品与科研目标全景条件：
以下六项共同覆盖五个已收束模块：自我进化、自主实验学习、现实选择与谱系延续、能力累积与迁移、元进化；任一模块只有文档、模拟或单元测试，不得计为实证完成。
1. 参考实现完成 Windows 原生安装、运行、恢复、On/Off、卸载运行进程/启动入口/受管配置与有界睡眠；默认保留可迁移的用户身份、Body 与经历数据，只有显式 purge 才删除。macOS/Linux 至少完成同协议 backend 与自动化验收框架，未实证的平台不得声称已验证。
2. 同一 Agent 跨对话、跨项目、跨模型强弱变化、跨进程重启持续存在，且有可重跑的真实运行证据证明至少接入 `Codex + 一个第二真实 coding-agent`；provider-neutral CLI/stdio/MCP 仅算 adapter 基础设施，除非第二真实 coding-agent 在真实任务中使用它并产出可重跑证据。
3. 形成 immutable experience archive、主动回忆/遗忘/睡眠巩固与 `Memory-to-Capability` 机制空间，但不把具体算法写死给人类。
4. 在正常任务流中无需人工逐步指挥即可完成 `observe -> 候选问题/变化 -> probation -> 读取现实后果 -> commit/rollback -> consolidate/migrate`，并允许 evaluator 与学习方式继续进化。
5. 存在预注册、可重跑的真实纵向实验，能证明某段经历对后续新会话、模型或执行表面的行为产生可归因能力增益；协调替换、伪 receipt、负迁移、错误候选、断连、崩溃和回滚都有反例测试。
6. 发布物必须包含安装/升级/卸载入口、adapter contract、复现实验说明、威胁模型、数据/隐私边界、版本化 schema、测试与迁移说明；Git 中保留聚焦提交并推送私有远端。

本轮工程发布验收：
1. 工程发布与长期科研主张分开判定。本轮先交付可安装、可运行、可恢复、可 On/Off、可升级和可卸载的用户级产品；安装后通过受管日常入口自然承载真实 coding 工作，而不是要求用户为每个任务手工拼接内部 wrapper 参数。
2. 工程发布前必须在两个真实 coding-agent 表面共享同一 Root/Head/经历，并实际出现至少一次不由研发者逐步指定的 `read -> self-authored non-empty change -> later load/action -> consequence -> retain/withdraw`。这证明自主链已经工程贯通，但不等于已经证明长期普遍增益。
3. 短而真实地验证持续会话、进程退出/重启恢复、Off 不采集也不自唤醒、On 恢复、升级保留身份历史、卸载清理运行面并按默认保留数据；未在真实平台运行的 backend 只标记未验证。不得设置 24 小时等待门槛。

长期科研最终主张：
第 5 项预注册纵向因果实验及跨时间普遍能力增益，必须在工程产品真实使用中持续采集、复现和证伪；在该证据形成前，产品可以按工程证据发布，但不得宣传为已经证明长期自我进化。只有当六类全景条件全部由当前证据直接支持，且文档、代码、实验记录和实际运行状态一致时，才允许给出完整科研结论；否则继续推进，不得缩小科研目标。
