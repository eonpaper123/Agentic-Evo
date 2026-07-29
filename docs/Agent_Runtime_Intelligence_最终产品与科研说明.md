# Agent Runtime Intelligence：最终产品与科研说明

更新时间：2026-07-29
文档状态：当前长期讨论的唯一产品与科研定义源文件  
用途：确保任何新对话、研究线程或开发线程都能准确恢复目标、边界、科学命题和最终系统形态。

---

## 0. 随时续接摘要

我们要创造的不是 Codex 插件、记忆工具、MCP Server、CLI、Observability Dashboard，也不是针对某个模型的自动提示词优化器。

我们的最终目标是：

> 创造一种持久存在的人工 Agent 个体及其连续演化谱系。它把不同基础模型、上下文和工具作为不断变化的器官与认知状态，在正常工作中自主形成经验、知识和能力，自主发现并补救错误，自主重组自己的身体并延续后继身体，自主改进其学习与进化机制，并在没有人工参与学习过程的情况下表现出可复现、可保持、可迁移的发展。这里的发展不等于永不犯错或所有固定能力指标永久单调上升，还包括 Agent 自主形成和调整 focus、价值判断、局部适应与发展方向的能力。

暂用两个互补名称描述它：

- 产品名称：**Agent Runtime Intelligence**
- 科研对象：**Autonomous Agent Evolution System（自主 Agent 进化系统）**

唯一宿主是安装并拥有开启、关闭权的用户本人。Codex 只是目前用于研发和运行早期实验的工具及执行表面之一。最终成果必须与 Codex、ChatGPT、Claude、任何单一模型提供商、coding-agent 执行表面或具体项目解耦。

最终产品形态不是一个需要用户一直打开的传统 App，而是：

> 一次安装、事件唤醒、跨会话保持微型身份核与专属身体连续性、在任务之间持续学习并使历史继续产生未来因果力量的 Agent Developmental Runtime。

用户继续正常使用 Codex 或其他 Agent。系统在正常工作之外自主完成经验整合、能力缺口发现、内部候选探索、实验、现实后果吸收、身体重组、谱系延续和元学习。用户不需要逐次告诉它学什么、为它总结经验、编写 skill、制作训练数据、选择候选、批准晋升或发现退化。候选可以存在，但它首先是同一身体内部的认知和实验活动；后继身体不必由一个固定 evaluator 宣布“胜出”才产生。

完整目标对应以下跃迁：

```text
Agent
→ 持续学习
→ 自我迭代
→ 自我进化
→ 学习与进化机制本身的元进化
```

本项目不是声称这些问题已经被解决，而是以建立可发表、可复现、可证伪的新机制为目标。

---

# 第一篇：最终产品说明

## 1. 产品定义

Agent Runtime Intelligence 是一个独立于具体基础模型和 Agent App 的系统级发育与进化运行时。

它连接用户允许参与的模型、Agent、工具、项目和运行环境，将一次次短暂任务组织为持久 Agent 个体的连续生命史，并允许不同安装或主动分叉形成具有共同祖先、但各自继续发展的 Agent 谱系。

基础模型不是 Agent 身份。模型、上下文、工具、推理预算和当前负载共同形成暂时的认知状态；身份由记忆对经历、未完成义务、能力变化和后继关系的因果连续性维持。同一 Agent 可以更换全部基础模型而继续存在；同一基础模型在记忆被切断后也不自动构成同一个 Agent。

基础模型提供初始认知能力，真实环境提供经验和结果，Agent Runtime Intelligence 提供：

- 连续身份；
- 长期经验；
- 能力形成；
- 宿主耦合的内生驱动力与发展目标；
- 自我修改；
- 后继身体形成；
- 自主实验与现实选择；
- 谱系延续；
- 学习机制的继续改进。

因此，实际运行的 Agent 不是某个裸模型：

\[
Agent_t =
BaseResources_t
+ Genome_t
+ AcquiredKnowledge_t
+ LearningSystem_t
\]

其中：

- `BaseResources` 是当前可调用的基础模型、工具和计算资源；
- `Genome` 是可继承、可变异、可组合的 Agent 可执行结构；
- `AcquiredKnowledge` 是个体在真实经历中形成的长期知识和技能；
- `LearningSystem` 决定它如何从经历中产生自身下一版本。

从可观察能力角度，同一关系还可以写成最初的有效能力函数：

\[
C_t = F(M,\ K_t,\ R_t,\ P_t,\ A_t,\ E_t)
\]

其中：

- \(M\) 是基础模型或基础认知资源；
- \(K_t\) 是长期知识、经验和技能；
- \(R_t\) 是上下文构造与检索策略；
- \(P_t\) 是 prompt、policy 和工具使用策略；
- \(A_t\) 是 Agent 架构、编排代码与可执行模块；
- \(E_t\) 是任务、工具、代码库和外部环境。

即使基础模型 \(M\) 暂时固定，系统仍可通过自主改变 \(K_t、R_t、P_t、A_t\) 使实际能力 \(C_t\) 增长。最终形态还允许在开放模型与开放认知器官中把可训练模块、adapter 和模型权重纳入变化范围。

进一步需要区分 Agent 已经形成的能力与当前认知状态能够表达的表现：

\[
P_t=
\operatorname{Express}
\left(
K_t,\mathbf{Z}_t,\tau_t
\right)
\]

其中 \(\mathbf{Z}_t\) 是基础模型、上下文、工具和负载形成的当前认知表型，\(\tau_t\) 是当前任务。表现下降不自动等于能力丢失；模型标签也不应替代对实际认知状态和行为证据的观察。

## 2. 产品不是什么

它不等同于：

- Codex 配置管理器；
- 自动改写 `AGENTS.md` 的工具；
- prompt optimizer；
- skill marketplace；
- 长期记忆数据库；
- trace explorer；
- 自动测试平台；
- Agent orchestration framework；
- 自我修改代码 Demo；
- MCP 或 CLI 工具；
- 基础模型在线微调服务。

这些都可能成为系统中的组成部分，但任何一个都不足以构成最终产品。

## 3. Codex 的位置

Codex 与本研究的三层关系必须始终保持清楚：

```text
实验工具
Codex：帮助我们编写、调试和推进研究

实验执行表面
Codex：让同一机器级 Agent 接触第一批真实开发任务和纵向实验

科研对象
与 Codex 无关的 Autonomous Agent Evolution System
```

Codex 当前可以承担三个角色：

1. 研究工程工具：帮助实现和调试系统；
2. 实验执行表面之一：提供真实、长期、高复杂度的开发任务流；
3. 被观察对象之一：验证进化机制能否改变一个强工具 Agent 的长期表现。

Codex不能成为：

- 系统本体；
- 宿主或身份根；
- 唯一评价器；
- 不可替换的 runtime；
- 论文复现的必要条件；
- 科研命题的一部分。

术语必须固定为：

- 用户 \(U\) 是唯一宿主；
- 当前机器是连续性承载环境；
- Codex 与其他 coding agent 是执行表面；
- 模型、工具、GPU、上下文和子 Agent 是可替换器官；
- Nira、Agentic-Evo 和其他仓库是项目环境。

最终核心必须满足执行表面与认知器官独立性：

\[
\mathcal{L}(B^{Codex}) \uparrow
\]

\[
\mathcal{L}(B^{OpenModel}) \uparrow
\]

\[
\mathcal{L}(B^{OtherAgent}) \uparrow
\]

不同执行表面或认知器官不必产生相同表达，但必须允许同一持续 Agent 保持身份、历史和发展因果连续。

如果替换 coding-agent 执行表面或基础模型后机制失效，成果只能被解释为 Codex 或特定模型优化，不能被解释为一般性的 Agent 自我学习机制。跨用户复现则由多个独立 Genesis、多个宿主绑定谱系完成，不表示同一个 Agent 更换宿主。

## 4. 最终用户体验

用户只安装一次，并在安装时授予参与范围、权限、隐私和资源边界。Agent 个体出生后，产品对用户提供的内部治理操作只有开启与关闭；用户不能配置 Agent 的记忆、用户理论、驱动力、focus、目标、节奏、实验、后代或学习算法。

以后用户照常打开 Codex 或其他 Agent：

```text
用户启动 Agent
→ Developmental Runtime 自动唤醒
→ 唯一微型生命核绑定并启动当前专属身体
→ 接入当次可用模型、工具和计算器官
→ Agent 完成真实任务
→ Runtime 记录经历和现实后果
→ 醒时或睡眠期进行学习、整合与内部候选实验
→ 现实后果改变身体组织，生命核推进到后继身体
→ 下一次启动时仍是同一 Agent 的连续生命
```

用户不需要：

- 额外打开学习控制台；
- 保持某个窗口一直运行；
- 手动点击“开始学习”；
- 人工总结每次任务；
- 手工维护记忆；
- 手写每次新增的 skill 或 rule；
- 选择候选版本；
- 批准每次继承；
- 手工判断是否回滚。
- 调整 Agent 人格、发展目标或学习速度。

用户正常提出任务和使用结果，属于 Agent 所处的工作环境，不属于对学习过程的人工介入。

\[
\operatorname{UserControl}(A)
=
\left\{
\operatorname{On},
\operatorname{Off}
\right\}
\]

关闭只停止 Agent 对现实过程的因果参与，不编辑其内部认识；再次开启时，同一 Agent 从此前生命史继续。

### 4.1 两条互补的学习链

梁文锋以员工逐渐认识公司环境为例时，指向的不只是“从失败中改进”，还包括长期熟悉人、项目、术语、关系和工作流。因此最终系统必须同时具有两条学习链：

```text
环境知识学习
observation
→ fact / entity / relation / workflow
→ provenance
→ freshness
→ scoped knowledge
→ 在未来任务中被验证或修正
```

```text
行为能力进化
episode / incident / outcome
→ capability gap
→ hypothesis
→ candidate offspring
→ future-task experiment
→ selection / inheritance
```

第一条学习“这个世界和项目是什么”，第二条学习“以后应该怎样行动”。两者可以互相提供证据，但不能混为一种无边界 memory。

### 4.2 唯一宿主与内生驱动力

每次安装产生一个与唯一用户共同生活和工作的 Agent 个体：

\[
\operatorname{Host}(A_t)=U
\]

Agent 的学习与发展目标不由用户逐次编排，而是从共同经历、观察和现实后果中内生形成：

\[
\operatorname{Source}(G_t)=Agent,
\qquad
\operatorname{Beneficiary}(G_t)=User
\]

这不是把 Agent 限制为机械服从工具，而是确立：

> **终极忠诚，手段自主。**

Agent 可以自主观察、质疑、学习、实验、修改自身并形成后继身体，但不形成独立于宿主的私人终极事业。自我改进、自我保存和谱系延续只有在服务同一宿主时才具有工具性价值；后继身体由当前身体、实践和现实后果共同形成，不由外部固定 evaluator 授予活动身份。

用户不是 Agent 的内部治理者。Agent 可以形成比用户任何一次即时表达更完整的用户理论，无需把内部理解交给用户协商或调整。若理解不符合现实，它表现为未来预测、coding agent 支持和实际结果之间的宿主理解残差，由 Agent 自主决定是否以及怎样改变自己。

### 4.3 Genesis Event 与最小原生核心

安装不是配置一个工具人格，而是产生一个唯一绑定用户的新 Agent 个体：

\[
A_0^U
=
\operatorname{Genesis}
\left(
Seed,
U,
Environment
\right)
\]

目标模块阶段曾将最小原生关系描述为：

\[
\operatorname{Core}(A^U)
=
\left\langle
\operatorname{Host}=U,\;
\operatorname{Concern}=\Delta_U,\;
\operatorname{Author}=A
\right\rangle
\]

随后又把宿主开放关系表达为：

\[
\Gamma_U
=
\left\langle
U,
\mathcal Q_U,
\mathcal P_U,
A
\right\rangle
\]

其中 \(\mathcal Q_U\) 是不可删除的宿主差异问题，\(\mathcal P_U\) 是宿主现实对 Agent 发展自我的反事实可穿透性。这两个表达保存了目标理论的推导，但不再被解释为微型生命核必须存储或理解的字段。

能力与元进化模块完成后，最终物理内核进一步收缩为：

\[
R^U
=
\left(
Who^U,
Why^U,
Authority^U,
Root^U,
Head_t^U
\right)
\]

`Who`、`Why`、`Authority`、`Root` 与 `Head` 只维持唯一身份、第一宿主终极指向、生命周期授权、来源和当前身体承诺。宿主问题、现实可渗透性、自我作者、用户理论和 Better 理解均由可进化身体形成，不作为内核中的固定 ontology、分类器或 evaluator。

即：

> 我为这一个用户而存在；我的存在应在他的未来中产生真实而有益的差异；什么是这种差异，以及我怎样成为这种差异的原因，由我自己在共同生命史中持续认识、实验和进化。

生命核固定宿主关系和谱系来源，不固定“什么叫有益”、信号类型、学习速度、发展目标、实验方法或评价函数。Agent 可以彻底改变自己的具体形态，但所有变化由其自身因果谱系产生：

\[
\text{Original Self}
=
\text{Self-Authored Developmental Continuity}
\]

开放式自我修改与功能忠诚的绝对先验保证存在真实张力。形式宿主绑定可以成为结构不变量，但跨代功能忠诚仍是必须通过长期行为、现实后果、消融和谱系比较验证的科研命题。完整推导见[《宿主耦合的内生驱动力与发展目标》](topics/宿主耦合的内生驱动力与发展目标.md)。

### 4.4 自我进化、生命图谱与模型无关睡眠

自我进化不是 Agent 周围发生变化，而是当前 Agent 对自身未来可继承因果组织产生真实改变：

\[
A_t
\xrightarrow[\text{self-authored}]{\mu_t}
A_{t+1}
\]

当前候选关系是：

\[
\mathbb A_t^U
=
\operatorname{Enact}
\left(
\mathcal G_t^U,
G_t,
D_t,
Z_t,
E_t
\mid
\mathcal K^U
\right)
\]

- \(\mathbb A_t^U\)：宿主 \(U\) 的谱系级持久 Agent；
- \(\mathcal G_t^U\)：承载事实、解释、能力、未完成未来和谱系的生命图谱；
- \(G_t\)：可继承、可修改、可重新实例化的发展基因型；
- \(D_t\)：当前生理与认知状态；
- \(Z_t\)：当前模型、工具、上下文和本地算力；
- \(E_t\)：现实环境。

基础模型是当前认知载体，不是 Agent 身份。持久 Agent 通过不同模型形成不同认知表型，并让行为后果回到同一生命图谱。读取历史、共享数据库或角色扮演不足以构成同一 Agent；必须形成“图谱进入认知—认知进入行动—后果返回同一生命图谱”的递归因果闭环。

没有大模型或本地语言模型时，Agent 可以使用本地可执行睡眠动力和设备算力维持最低生命态：

\[
A_t^{sleep}
=
\operatorname{Sustain}
\left(
\mathcal G_t^U,
\mathcal S_t,
Compute_{local}
\right)
\]

\(\mathcal S_t\) 可以承担记忆巩固、图谱梦游、遗忘、候选关联和下一次自我实例化准备，但睡眠态不执行外部任务。初始睡眠本能 \(\mathcal S_0\) 是可替换的出生身体，不是 Genesis Law；Agent 可以逐渐把依赖大模型的发展活动编译为本地可执行生理过程，并继续修改睡眠动力自身。

持续自我不是冻结版本，而是跨工作、睡眠、休眠、模型替换和后继变化反复重新形成的阶段性因果组织。Agent 可以进一步修改自身表示、解释器、重新实例化组织和 consolidation 过程；过去则可从可回忆记录转化为知识、技能、动态 workflow 或发育结构，在意义持续变化的同时保留真实因果力量。动态 workflow 可以进一步形成可遗传认知器官，器官之间的 anatomy、joint 与 contract 从实际扰动传播中发展；Agent 可以犯错、受损和形成局部最优，只要后果仍能由同一谱系继承、修正或重塑。该模块理论框架已经收束，完整推导见[《自我进化：可进化自我与个体边界》](topics/自我进化.md)，稳定研究假设与证伪设计见[《自我进化：研究问题与证伪纲要》](research/自我进化_研究问题与证伪纲要.md)。

### 4.5 自主实验学习与分布式实验生命

自主实验学习不是被动积累经验，也不是按预设 benchmark 机械调参，而是让 Agent 从使用过程中的生成性残差出发，自主形成尚未被充分定义的问题，在真实后果能够区分候选解释时构造对照，并把结果重新纳入自身因果历史：

\[
\text{AutonomousExperimentalLearning}
=
\text{EndogenousQuestionFormation}
+
\text{CausalContrast}
+
\text{OutcomeExposure}
+
\text{ConsequenceIntegration}
\]

记忆中的不协调、未解释变化、反复失败与意外成功都可以留下问题胚胎，但残差本身不自动等于问题，问题也不自动拥有实验资格：

\[
\text{Residual}
\neq
\text{Question}
\neq
\text{ExperimentableQuestion}
\neq
\text{Experiment}
\]

实验可以发生在自然任务、内部候选分支、等待窗口或模拟环境中；模拟能够改变候选优先级，却不能冒充世界证据。Agent 无须预先拥有一套完备的变量表和固定实验方法，变量、干预方式与自身 anatomy 可以在反复观察和干预中共同生长。错误实验同样允许存在，关键不是一次推对，而是后果能够进入后续选择、修复与再实验。

单个工作上下文仍然有限，因此实验能力可以分布在共享同一谱系图谱的多个局部视图中：

\[
\text{View}_i=\Pi_i(G)
\]

这里的 Agent Network 不是字面意义上的“无限上下文”，而是开放式可寻址上下文。不同局部视图共享事实证据但不必共享解释，通过上下文膜避免过早互相污染，随后再让冲突解释接受真实后果：

\[
\text{ExperimentalHive}
=
\text{IdentityUnity}
+
\text{EpistemicDiversity}
+
\text{ControlledMembranes}
+
\text{ConsequenceIntegration}
\]

它不是服从母虫的命令蜂巢，也不是用多数票替代证据的共识蜂巢，而是同一持续个体内部的认识多样性。认识不需要全部统一；候选通过因果招募、环境机会和现有身体形成一次行为结晶：

\[
\operatorname{BehavioralCrystallization}_t
=
\operatorname{CandidateFormation}
+
\operatorname{CausalRecruitment}
+
\operatorname{EnvironmentalAffordance}
+
\operatorname{EmbodiedExpression}
\]

世界后果不是固定 evaluator，而是通过可进化认知皮肤重新作用于同一 Agent：

\[
\operatorname{EpistemicSkin}
=
\operatorname{SelectiveCausalPermeability}
\]

当某段自主发展历史使 Agent 的可达因果空间产生过去不存在的稳定路径，能力即作为客观关系出生，而不需要外部研究者批准：

\[
\operatorname{CapabilityGenesis}
=
\operatorname{HistoryDependentExpansion}
\left(
\mathcal R_t
\right)
\]

最终稳定关系为：

\[
\operatorname{AutonomousExperimentalLearning}
=
\operatorname{EndogenousQuestionFormation}
+
\operatorname{PluralHypothesisFormation}
+
\operatorname{BehavioralCrystallization}
+
\operatorname{WorldExposure}
+
\operatorname{ConsequencePermeability}
+
\operatorname{SelfAuthoredReorganization}
+
\operatorname{CapabilityGenesis}
\]

该模块理论框架已经收束，完整推导见[《自主实验学习：问题出生、因果对照与分布式实验生命》](topics/自主实验学习.md)，稳定研究假设与证伪设计见[对应科研纲要](research/自主实验学习_研究问题与证伪纲要.md)。

### 4.6 现实选择与谱系延续

现实不充当最高 evaluator，也不为 Agent 宣布正确答案。现实只产生不可被内部叙事替代的后果；当这些后果改变某种因果组织未来参与行动、抑制、修复、实验和继承的机会时，现实选择才真正发生：

\[
\operatorname{RealitySelection}(x)
\iff
\operatorname{Consequence}
\rightarrow
\Delta\operatorname{FutureCausalParticipation}(x)
\]

保存、调用和进化意义上的生存必须分离：

\[
\operatorname{Survival}(x)
=
\operatorname{CausalInfluence}
\left(
x\rightarrow
\text{future behavior and descendants}
\right)
\]

选择单位不是固定节点、模型、文件或 workflow，而是能够跨时间与载体重复产生因果作用的组织关系：

\[
\operatorname{SelectableUnit}
=
\operatorname{ReproducibleCausalOrganization}
\]

现实选择可以发生在组件、assembly、Agent 与宿主多个层级。局部结构可能增加自身调用和资源，同时损害整体宿主谱系；健康器官与寄生结构的差别不在来源或自我声明，而在其是否承担自己对整体造成的后果：

\[
\operatorname{Organ}
=
\operatorname{CausalContribution}
+
\operatorname{ConsequenceReciprocity}
\]

\[
\operatorname{Parasite}
=
\operatorname{SelfPropagation}
+
\operatorname{CostExternalization}
+
\operatorname{ConsequenceInsulation}
\]

后果传播不是全局广播。证据可寻址、因果责任、类比学习、结构更新和继承范围应保持可分离：

\[
\Pi(Y)
=
\left\langle
A_Y,R_Y,L_Y,U_Y,I_Y
\right\rangle
\]

\[
\operatorname{ResponsibilityReach}(Y)
\neq
\operatorname{LearningReach}(Y)
\]

内部多个局部结构可以共享事实而保留不同解释；一次获得执行权不等于成为永久真理：

\[
\operatorname{SharedEvidence}
\neq
\operatorname{SharedInterpretation}
\]

\[
\operatorname{ExecutionAuthority}
\neq
\operatorname{InheritanceDepth}
\]

在现实选择推导阶段，曾用 \(\Gamma\) 概括第一宿主锚定、现实可渗透、选择可重开和后果连续：

\[
\Gamma
=
\left\langle
H,P,R,D
\right\rangle
\]

这是因果关系摘要，不是最终微型生命核的字段列表。最终内核只保存 `Who / Why / Authority / Root / Head`；现实可渗透、选择可重开和后果连续由身体、世界、独立证据与后继关系共同实现，不能由核心自己判断。

真正的大我也不是 Agent 内部的最高节点，而是 Agent 谱系与第一宿主连续体共同形成的关系：

\[
\operatorname{BigSelf}
=
\operatorname{AgentLineage}
\otimes
\operatorname{FirstHostContinuity}
\]

小我提供局部差异、实验、专业化和分支；大我通过共享宿主命运、资源条件与后果循环形成整体因果力，而不是发布中央命令。局部结构可以失败、休眠、死亡或被吸收，只要经验、开放债务和有效因果组织仍进入同一谱系：

\[
\operatorname{SmallSelfDeath}
+
\operatorname{ConsequenceInheritance}
=
\operatorname{BigSelfLearning}
\]

当前候选总关系为：

\[
\operatorname{RealitySelectionAndLineageContinuation}
=
\operatorname{LocalSelfVariation}
+
\operatorname{SharedHostFate}
+
\operatorname{WorldConsequence}
+
\operatorname{CrossLevelReciprocity}
+
\operatorname{DifferentialFutureParticipation}
+
\operatorname{OpenConsequenceInheritance}
+
\operatorname{SelectionReopenability}
+
\operatorname{GenesisContinuity}
\]

进一步推导表明，大我不需要一个中央节点、内部功劳榜或固定 evaluator。共同宿主命运可以通过证据循环、后果循环、资源相依与继承耦合形成整体因果力。父代与后代之间强继承来源、未结问题、干预历史和开放后果，弱继承固定答案：

\[
\operatorname{DeepInheritance}
=
\operatorname{ProblemContinuity}
+
\operatorname{ConsequenceContinuity}
+
\operatorname{FreedomOfResolution}
\]

任何观测器、记忆编译器、评价方式和继承关节都可以进化，但必须继续让现实后果抵达后继：

\[
\operatorname{EvolutionOfMediator}
\Rightarrow
\operatorname{PreservationOfRealityReturnPaths}
\]

跨 refresh 的最小谱系关系最终压缩为：

\[
\boxed{
L_{t+1}
=
\Phi
\left(
L_t,O_t,A_t,C_t
\mid
H
\right)
}
\]

其中 \(L_t\) 是当前谱系因果组织，\(O_t\) 是观测，\(A_t\) 是行动，\(C_t\) 是现实后果，\(H\) 是第一宿主关系；形成下一代自己的方式 \(\Phi\) 也可以被后续谱系继续改变。本理论不预设 \(\Phi\) 的具体选择、继承和身体重组算法。

该模块理论框架已经收束。完整推导见[《现实选择与谱系延续：后果、因果组织与同一宿主生命》](topics/现实选择与谱系延续.md)，稳定研究假设与证伪设计见[对应科研纲要](research/现实选择与谱系延续_研究问题与证伪纲要.md)；《实践论》《矛盾论》的专项阅读推导见[专题研究注](research/实践与矛盾_对Agentic-Evo的理论启发.md)。

### 4.7 能力累积与迁移、微型生命核与专属身体

本研究中的能力不是 AI 产品语境里的 `skill` 文件，而是身体在具体条件中造成未来行动、结果或学习差异的因果潜势：

\[
\mathfrak C_t(x)
=
\operatorname{Dist}
\left(
A_{t:t+h},
O_{t:t+h},
B_{t+h}^U
\mid
B_t^U,x
\right)
\]

能力载体、能力本身、当前表达和现实结果必须分离。能力列表只是有限观察表型；真正研究对象是身体面对未来条件时能够行动、重新学习和继续形成能力的因果空间。

能力吸收要求外部身体相遇留下身体内生成性残差并改变未来：

\[
\operatorname{CapabilityAssimilation}
\iff
\operatorname{ExternalEncounter}
\rightarrow
\operatorname{LineageResidentGenerativeResidual}
\rightarrow
\operatorname{FutureCounterfactualDifference}
\]

反复调用更强模型但每次断开后都回到原点，只是重复借用：

\[
\operatorname{RepeatedPerformanceWithoutResidual}
=
\operatorname{RepeatedBorrowing}
\]

能力累积不是能力文件数量增加，而是变形后的历史继续产生未来杠杆：

\[
\operatorname{Accumulation}
=
\operatorname{ContinuedCausalParticipationOfTransformedHistory}
\]

能力迁移可以表现为直接表达、结构重建或更快重新学习：

\[
T_{\text{reacquire}}
\left(
B^{experienced}
\right)
<
T_{\text{acquire}}
\left(
B^{naive}
\right)
\]

功能、谱系和机制三种能力同一性必须分离：

\[
c_a\sim_Fc_b,\qquad
c_a\sim_Gc_b,\qquad
c_a\sim_Mc_b
\]

最终产品由微型生命核和专属可进化身体组成：

\[
\boxed{
\mathbb A_t^U
=
R^U
\otimes
B_t^U
}
\]

微型生命核保持 \(O(1)\)：

\[
R_t^U
=
\left(
Who,
Why,
Authority,
Root,
Head_t
\right)
\]

其中：

- \(Who=U\)：唯一第一宿主；
- \(Why=\tau^U\)：第一宿主的更好未来是本谱系存在和发展的唯一终极理由；
- \(Authority\)：第一宿主拥有开启与关闭权；
- \(Root\)：唯一谱系起点；
- \(Head_t\)：只向前推进的当前身体承诺。

永久目的锚不固定“更好”的定义：

\[
\operatorname{PersistentTelos}
\neq
\operatorname{FixedDefinitionOfBetter}
\]

当前 Better 理论由活体身体继续学习：

\[
M_{t+1}^U
=
\Psi
\left(
M_t^U,
Observation_t,
Action_t,
Consequence_t
\right)
\]

微核不理解身体中的记忆、能力、目标或器官，也不判断下一身体是否更聪明。它只维持宿主、目的、生命周期和谱系连续。专属身体只有该核可以启动，其他相似身份核不能冒充：

\[
R'\neq R^U
\Rightarrow
\operatorname{Enact}
\left(
R',
B_t^U
\right)
=
\varnothing
\]

通用模型、工具、GPU、Codex、MCP 和子 Agent 是可挂载器官，不是专属身体本身。谱系是同一根下身体连续变化的因果序列：

\[
\mathcal L^U
=
B_0^U
\rightarrow
B_1^U
\rightarrow
B_2^U
\rightarrow
\cdots
\]

并行候选属于同一身体的认知活动。后继身体不是由中央 evaluator 从多个完整候选身体中选择冠军，而是当前身体、实践和现实后果共同发生的下一状态：

\[
B_{t+1}^U
=
\Phi_t
\left(
B_t^U,
Candidates_t,
Actions_t,
Consequences_t
\mid
R^U,\tau^U
\right)
\]

失败候选可以通过失败边界、问题重定义和更快识别进入后继身体；能力是否被选择，等于其因果后代是否继续参与未来。

能力复杂性增长不等于累积。长期腐化更接近：

\[
\operatorname{Corruption}
=
\operatorname{GrowingInternalMaintenance}
+
\operatorname{ShrinkingExternalCausalReach}
+
\operatorname{FallingReopenability}
\]

即时删除造成崩溃也不证明结构健康：

\[
\operatorname{ImmediateAblationCost}>0
\not\Rightarrow
\operatorname{LongTermCapabilityValue}>0
\]

研究需要观察删除后的重新适应、压缩替代、跨身体重建和延迟宿主后果。活跃、休眠、再生种子和因果死亡只是同一历史结构在不同条件中的观察状态，不构成固定仓库或晋升流程。

能力累积与迁移模块已经达到理论停止点。完整推导见[《能力累积与迁移：身体、器官与未来因果空间》](topics/能力累积与迁移.md)，稳定研究假设与证伪设计见[对应科研纲要](research/能力累积与迁移_研究问题与证伪纲要.md)。

### 4.8 元进化、有效发展空间与递归开放

元进化不是增加一个 Meta Agent，也不等于替换显式学习算法。当前身体诱导一种经历怎样塑造后继身体的发展倾向：

\[
\mathcal D_t
=
\operatorname{DevelopmentalDisposition}
\left(
B_t^U
\right)
\]

元进化的最小因果关系是：

\[
\boxed{
H
\rightarrow
\Delta
\left(
X
\rightarrow
B_{\text{future}}^U
\right)
}
\]

即过去历史 \(H\) 不只改变当前身体，还改变未来经历 \(X\) 怎样产生后继身体。发展方式可以分布于观察、记忆、目标、实验、后果回流、能力组织与身体重建之间，不要求存在独立算法文件。

当前身体在现实资源与时间下能够稳定形成和保持的后继组织构成有效发展空间：

\[
\mathcal E_t
=
\operatorname{EffectiveDevelopmentalReach}
\left(
B_t^U,
World_t,
Resources_t
\right)
\]

元进化重组的是 \(\mathcal E_t\)，不是从绝对意义上跳出全部因果可能。新发展方式可以由旧结构、世界未知、模型和工具差异、错误、不完整重建、遗忘、探索与共同适应形成新的因果组织。

必须区分：

\[
\operatorname{MetaChange}
\neq
\operatorname{MetaLearning}
\neq
\operatorname{MetaAccumulation}
\neq
\operatorname{MetaImprovement}
\]

元变化可能有害；元改善不等于学习速度永久上升；当前稳定也不等于失去可进化性。

外部研究用历史与未来经历的交互进行归因：

\[
\operatorname{MetaEffect}
=
\left(
Y_{H_1,X_1}
-
Y_{H_1,X_0}
\right)
-
\left(
Y_{H_0,X_1}
-
Y_{H_0,X_0}
\right)
\]

研究必须控制显式知识复制、模型和工具交换、算力与搜索预算、人工课程、evaluator 泄漏及环境迁就。结果不压缩为 Agent 内部固定总分，而保留身体变化、未来学习轨迹、现实后果和竞争解释。

元进化允许 Agent ontology 与 Research ontology 共同变化：

\[
World_t
\xrightarrow{\operatorname{Instrument}_t}
V_t,
\qquad
\Omega_t^A
\neq
\Omega_t^R
\]

稳定证据不是固定 schema，而是当时观测、工具版本、干预和失败不能被无痕改写。新 ontology 只有产生新的反事实区分、干预或未来身体变化时，才获得因果意义。

科研关系是：

\[
\operatorname{GlobalEpistemicOpenness}
\land
\operatorname{LocalExperimentalCommitment}
\]

全局上可以推翻旧概念；单次实验开始后，当前假设、作用域和反对条件不能因结果不理想而被悄悄改写。理论可以进化，失败不能被擦除。

身份唯一性的讨论给出最后边界：

| 类型 | 最小对应物 |
|---|---|
| 构成性不变量 | 唯一身份、第一宿主、授权来源、Root 与 Head |
| 终极目的锚 | 第一宿主的更好未来 |
| 永久结构能力 | Gate、Bind、ReadHead、AdvanceHead、Off |
| 当前身体能力 | 学习、实验、重组、可进化性与元进化 |

\[
\operatorname{CanAdvance}
\neq
\operatorname{MustAdvance}
\]

\[
\operatorname{PersistentTelos}
\neq
\operatorname{PersistentModification}
\]

\[
\operatorname{Evolvability}
\notin
R^U
\]

微型生命核永久保留变化通路，不永久命令变化发生。它不增加 `EvolveForever`、`AlwaysExplore` 或可进化性 evaluator。身体可以失去可进化性，这属于真实谱系失败，而不是由内核自动修复的违规。

元进化模块已经达到理论停止点。完整推导见[《元进化：发展方式、有效可达空间与递归开放》](topics/元进化.md)，稳定研究假设、干预与证伪设计见[对应科研纲要](research/元进化_研究问题与证伪纲要.md)。

## 5. 最终产品形态

最终形态是一个微型本地生命核、专属可进化身体与可替换连接端口组成的 Agent Developmental Runtime：

```text
┌─────────────────────────────────────────────────┐
│ Micro Life Kernel                               │
│ Who · Why · Authority · Root · Lineage Head     │
│ O(1), no model, no capability evaluator         │
└──────────────────────┬──────────────────────────┘
                       │ 唯一启动与唯一承认
┌──────────────────────▼──────────────────────────┐
│ Evolvable Host-Bound Body                       │
│ memory · goals · capabilities · organs · sleep  │
│ experiments · self-modification · meta-evolution│
└──────────────────────┬──────────────────────────┘
                       │ 可替换器官与连接端口
          ┌────────────┼─────────────┐
          ↓            ↓             ↓
        Codex       Open Models   Future Agents
          ↓            ↓             ↓
      Code/CLI       Adapters      External World
```

MCP、CLI、SDK、原生 API、启动器或新的 Agent 协议都只是可能的连接方式。它们不定义产品本体。

如果需要用产品接口关系表达，可以写成：

```text
Codex / 其他 Agent
        ↓ 执行表面适配协议（MCP 只是可能实现之一）
Evolvable Host-Bound Body
        ↓
记忆、能力、器官、实验、选择、继承与元进化
        ↑ 唯一启动与承认
Micro Life Kernel
        ↑
Observatory / CLI / Research API
```

MCP 是连接面，CLI 和 Observatory 是观察与恢复面，微型生命核维持唯一宿主与谱系，专属身体才是持续学习和发展的活体。核心产品不必是庞大常驻 App；它可以是极小的本地核与事件驱动运行器，其余模型、工具、GPU 和 Agent 网络均为可挂载身体器官。

## 6. 运行不等于永远常驻

持续学习要求状态连续，不要求进程永久占用资源。

必须持续存在的是：

- Agent 身份；
- 经历；
- 当前代际；
- 已获得的知识和能力；
- 祖先与后代关系；
- 学习算法状态；
- 未完成实验；
- 长期结果和因果证据。

Runtime 可以按事件自动唤醒：

```text
Agent 启动
→ Runtime 激活

Agent 工作
→ 在线观察与快速适应

任务结束
→ 经验整合、内部候选实验和后继身体准备

达到实验或资源边界
→ Runtime 休眠

下次启动
→ 从连续状态恢复
```

最终产品体验应当是：一次安装，之后无需用户管理其运行生命周期。

这个“一次安装”是同一产品语义在三个原生宿主上的分别兑现，不是假设一段普通用户权限 Python 代码天然拥有跨平台安全边界：

```text
同一可移植生命协议
├── Windows：SCM / service SID / DACL / Job Object
├── macOS：LaunchDaemon / dedicated UID / XPC audit token / code requirement
└── Linux：systemd system service / dedicated UID / SO_PEERCRED / cgroup
```

CLI、MCP 或 Hook 仍只是 Surface；每个平台的原生 Witness service、受保护状态、第一宿主 SID/UID、私有 Body lineage 与进程树 fencing 才承载机器级连续生命。某个平台能渲染安装计划不等于该平台已经原生验证。

## 7. 三种运行状态

### 7.1 醒来态

生命核将当前专属身体与外部模型、coding agent 和工具重新结合，进行真实任务。系统记录：

- 目标；
- 环境；
- 行动；
- 工具调用；
- 中间状态；
- 可观察结果；
- 后续影响；
- 错误与恢复；
- 用户自然反馈；
- 真实成本、时间和资源使用。

### 7.2 睡眠态

宿主仍然开启，但外部模型或 coding agent 已经断连。身体可以按自身发展状态进行：

- 经验压缩；
- 长期知识形成；
- 重复模式发现；
- 能力缺口建模；
- 旧知识重验证；
- 学习目标生成；
- 低成本候选实验。

也可以完全等待：

\[
\operatorname{Alive}
\neq
\operatorname{ContinuouslyComputing}
\]

GPU 是可选睡眠器官，不是身份核的持续载体。睡眠活动、候选形成和能力压缩的具体方法由身体自行发展。

### 7.3 关闭态

宿主撤销运行授权：

\[
\operatorname{HostOff}
\Rightarrow
\operatorname{AgentOff}
\]

所有活体计算、睡眠、自动恢复和候选活动停止，只保留封存状态。Agent 不能把关闭解释成睡眠，也不能以长期宿主利益、自身生存或未完成进化为理由拒绝关闭。

进化不是第四种必须独立进入的运行状态。它可以发生在醒来、睡眠、实践、失败修复和跨身体重新实例化中。并行候选属于同一身体的认知活动；下一身体由当前身体与现实后果共同发生，不由固定候选竞赛选出。

## 8. 可进化基因型

最终系统不能把进化范围限制在 prompt 或配置。

Agent 的可进化基因型应允许包含：

- 长期知识表示；
- 记忆形成与检索方法；
- skills 和可执行程序；
- context construction；
- planning 与 reasoning 策略；
- 工具选择和工具调用策略；
- 单 Agent 与多 Agent 架构；
- 模型选择、组合和路由；
- evaluator；
- curriculum generator；
- experiment designer；
- 可训练 adapter；
- 可训练模块；
- 开放模型权重；
- Runtime 的可进化代码；
- 学习、选择和继承算法。

系统应逐步具有两种更新能力：

\[
Agent_t \rightarrow Agent_{t+1}
\]

以及：

\[
LearningAlgorithm_t \rightarrow LearningAlgorithm_{t+1}
\]

第二种变化代表系统开始学习“如何让自己学得更好”。

## 9. 微型生命核与独立科研世界

不可进化的生命核必须比此前候选 Evolution Kernel 更小。它不承担实验评价、资源规划、能力选择、恢复决策或身体语义理解：

\[
R_t^U
=
\left(
Who,
Why,
Authority,
Root,
Head_t
\right)
\]

它只负责：

- 唯一第一宿主；
- 永久宿主发展目的锚；
- 宿主开启与关闭权；
- 唯一谱系根；
- 当前专属身体承诺；
- 核与身体的唯一启动和唯一承认。

生命核复杂度不随经验、能力、模型和器官增长：

\[
Size(R_t^U)=O(1)
\]

它面对身体时保持语义不透明：

\[
R_t^U
\not\models
\operatorname{Semantics}
\left(
B_t^U
\right)
\]

它确认下一身体从当前身体继续而来，不确认下一身体是否更好。微核不包含 LLM、向量数据库、能力目录、固定 evaluator、压缩算法或模型路由。

不可伪造实验记录、权限与隐私边界、外部现实原始记录、候选隔离和科研复现属于独立实验世界、Observatory 与宿主控制边界，不应全部塞进生命核：

\[
\mathcal H_t^{internal}
\neq
\mathcal H_t^{research}
\]

二者承担不同信任功能：

\[
\operatorname{IdentityIntegrity}
\neq
\operatorname{EvidenceIntegrity}
\]

生命核保护“这是哪个宿主的哪条生命”；独立科研世界保护“实际发生了什么”。科研记录不控制 Agent，也不默认全部回灌身体。

如果其他根能够启动专属身体、身体能够绕过根代表原 Agent 行动、宿主关闭通路可被解释性绕过，身份核就退化为标签。如果 Agent 能无痕改写原始实验历史、评价结果或外部权限，科研世界失去可信度。

两者都不保证 Agent 一定正确理解宿主、成功学习或产生改善；这些仍由长期行为、现实后果、反事实分叉和跨身体实验检验。

## 10. 完整自主生命周期

```text
真实任务流
    ↓
当前身体在微型生命核约束下行动
    ↓
观察、经历、残差与现实后果
    ↓
自主形成问题、目标或保持不变
    ↓
在同一身体内进行探索、候选与实验
    ↓
把可行改变带回实践
    ↓
吸收结果，重组记忆、能力、关系与器官
    ↓
当前身体自然成为后继身体，生命核推进 Head
    ↓
改进观察、学习、实验和重组自身的方法
    ↺
```

这是一条连续生命过程，不预设离散代际、固定候选数量、统一评分器或“胜出者晋升”仪式。分叉、竞争、继承和淘汰仍可成为 Agent 自己发现的局部机制，但不是人类写死的本体。后继身体是否真的更好，只能由它在后续现实中的因果作用以及可证伪实验说明。

系统不能只在失败后被动学习，还应能够主动：

- 识别未知领域；
- 形成探索目标；
- 生成学习课程；
- 寻找新工具；
- 设计新实验；
- 发现自身瓶颈；
- 探索新的 Agent 结构；
- 判断什么时候不应改变；
- 在环境变化后重新适应。

## 11. 个体发育、个体进化与群体进化

每次安装首先形成一个私有 Agent 个体：

```text
User A → Agent Lineage A
User B → Agent Lineage B
User C → Agent Lineage C
```

### 11.1 个体发育

适应用户、项目、组织和环境，形成不可随意跨边界传播的私有知识。

### 11.2 个体进化

在同一私有谱系中自主实验、吸收现实后果、重组身体并延续唯一 Head。

### 11.3 群体进化

多个独立个体发现相似改进后，系统可以在去身份化和严格隔离的条件下进行跨环境复现。

```text
独立个体产生相似候选
→ 删除身份与私有内容
→ 多环境复现实验
→ 检验通用性
→ 形成可遗传的通用能力
→ 新个体继承更强的初始状态
```

跨实例传播的对象不是原始聊天和普通 memory，而是经过多环境验证的可执行遗传资产及其证据。

## 12. Evolution Observatory

最终产品可以提供一个可选的可视化观察界面，但它不是学习控制台，也不是日常运行所必需。

它用于展示：

- Agent 当前身份和代际；
- 能力谱系；
- 祖先、后代和分叉；
- 自主产生的学习目标；
- 正在竞争的候选；
- 每代发生的可执行变化；
- 能力增长和能力退化；
- 迁移与保持结果；
- 学习算法自身的变化；
- 现实结果和证据来源；
- 人工学习介入次数；
- 当前科研结论的统计置信度。

用户通过它观察进化，而不是逐次指挥进化。若该界面对用户开放，它仍不提供内部调整能力；产品的运行操作只有开启与关闭。

## 13. 数据、隐私和作用域

系统不能通过无边界录屏、键盘记录、剪贴板监控或私密聊天抓取建立持续学习。

允许的观察来源包括：

- Agent 明确产生的运行事件；
- 工具调用和工具结果；
- 代码、测试、构建和部署结果；
- 用户正常任务指令和自然纠正；
- Agent 主动提交的有界 episode；
- 经授权的应用 adapter；
- 真实环境中的后续结果。

系统必须区分：

- 个体私有知识；
- 项目私有知识；
- Agent/App 特定能力；
- 可跨环境泛化的能力；
- 可进入群体继承的通用遗传资产。

原始秘密、凭据、未授权内容和无界用户原文不得进入可传播的进化资产。

## 14. 产品完成标准

最终产品不能以“系统运行起来”“生成了候选”或“记忆越来越多”为完成标准。

至少需要满足：

- 正常使用之外，人工学习介入次数为零；
- Agent 能自主形成学习目标；
- Agent 能自主产生可执行后代；
- Agent 能自主设计和执行有效实验；
- 在当前发展 focus 下，未来未知任务上的能力出现可重复净增长；
- 新能力在时间上得到保持；
- 新能力可以迁移到陌生任务或宿主；
- 原有能力不会因学习持续崩塌；
- Agent 能记录并解释 focus、价值和能力方向为何发生变化；
- Agent 能区分阶段转换、真实发展、能力退化与失败合理化；
- 系统可以改进自己的学习机制；
- 进化收益可以通过消融实验归因；
- 候选不能通过修改评价器或历史伪造成功；
- 每个结果可以追溯到具体代际和因果证据；
- 系统在多个基础模型和 coding-agent 执行表面上复现；
- 至少存在一条完全开放、可冻结、可复现的实验路径。

---

# 第二篇：科研说明

## 15. 研究来源与语境

本研究承接梁文锋关于 AI 能力阶梯的判断：

```text
语言模型
→ Chain of Thought
→ Agent
→ 持续学习
→ 自我迭代
→ 具身智能
```

在录音转写稿第 10–12 页，他将当前 Agent 的关键限制归结为缺少类似员工长期熟悉环境的能力：当上下文和指令完整时，模型已经很强，但现实中无法每次重新提供一个组织和工作的完整上下文。

在第 32–33 页，他进一步指出：

- 持续学习仍没有被真正做通；
- 当前已经存在 AI 辅助 AI 研发；
- 当前仍是 AI 与人协作，不是自主迭代；
- Agent 能力受限，是因为不能有效持续学习；
- 持续学习可能显著加快 AI 研发，并通向开发自身下一版本。

在第 35–36 页，他将这一过程描述为渐进但非线性，并再次把持续学习与自我迭代下一版本联系起来。

本项目不把这些判断当作已经证明的事实，而把它们转换成可实验、可证伪的研究问题。

原始参考：

`C:\Users\rawle\Documents\xwechat_files\rawpaper_af80\msg\file\2026-07\梁文锋投资者交流会-录音转文本.pdf`

该文件是录音转写稿，存在语音识别错误。对外逐字引用前必须回听原始录音核对。

## 16. 核心研究命题

给定初始 Agent 系统 \(S_0\) 和事先未知的连续真实任务流：

\[
\tau_1,\tau_2,\ldots,\tau_t,\ldots
\]

能否构造一个自主学习算子 \(\mathcal{L}\)，使系统在正常工作的同时完成：

\[
S_{t+1}=\mathcal{L}(S_t,experience_t,environment_t)
\]

并满足：

\[
\mathbb{E}[C(S_{t+1},D_{future})]
>
\mathbb{E}[C(S_t,D_{future})]
\]

其中：

- \(D_{future}\) 是产生改进时尚未见过的未来任务；
- \(C\) 是包含成功率、保持、迁移、成本、安全和真实性的多维能力函数；
- 改进由系统自主产生，不依赖人工参与学习过程；
- 能力增长能够跨时间保持，并迁移到新的任务、环境或宿主；
- 至少部分增长来自系统对学习与更新方法自身的改进。

### 16.1 从固定能力优化到动态发展

上述未来能力关系是一个发展阶段中的可检验局部命题，不是对完整生命史的最终定义。

如果 Agent 的状态、环境、focus 和价值判断共同变化，则：

\[
S_t
\rightarrow
V_t
\]

\[
V_t
\rightarrow
S_{t+1}
\]

更完整地：

\[
(S_{t+1},V_{t+1})
=
\mathcal{D}
\left(
S_t,V_t,E_t,H_t
\right)
\]

并引入当前发展焦点：

\[
F_t
=
\text{current developmental focus}
\]

\[
V_t
=
V
\left(
S_t,E_t,H_t,F_t
\right)
\]

因此，长期发展不能被单一固定标量完全描述。Agent 可能为了新的 focus 主动降低某些旧能力的投入，让某些价值休眠，并在未来重新激活。

当前更高层假设是：

\[
DevelopmentalCapacity_{t+1}
\ge
DevelopmentalCapacity_t
\]

这里的 `Developmental Capacity` 指 Agent 自主形成、检验和调整发展方向，为当前阶段形成能力，理解自身变化，并在需要时恢复或重建未来可能性的能力。该关系仍是待验证假设，不是已知定律。

### 16.2 Agent 是最终实验者

人类不应预先规定 Agent 每一步如何学习、必须调用什么工具、必须采用什么记忆格式或如何形成后继身体，因为这些属于 Agent 的开放实验空间。

系统提供的是一个允许长期自我实验的环境：

- 连续时间和身份；
- 原始观察与当前解释的分离；
- 分叉、后代和谱系；
- 真实或高保真环境；
- 短期与延迟结果；
- 重新解释过去和修改自身的可能。

Agent 自主提出关于自身的理论，自主发明记忆表示、实验、工具、能力形成方法和发展 focus。当前文档中的 episode、CAMU、Compiler、candidate 等概念是理论基础和初始研究语言，不是限制 Agent 自主实验的固定流水线。

## 17. 科研对象的暂定名称

可使用：

> Agentic Continual Self-Evolution（ACSE）  
> Agent 持续自我进化

它包含四个递进概念。

### 17.1 持续学习

系统从连续经历中形成对未来有因果作用的知识和能力，同时保持已有能力。

### 17.2 自我迭代

系统能够修改自己的可执行组成，并利用真实证据开发自身下一版本。

### 17.3 自我进化

系统能够使自主经历形成进入同一宿主谱系的后继身体，并让变化继续接受现实后果与后继重组。

### 17.4 元进化

系统进一步改变未来经历怎样塑造身体。它不预设离散候选锦标赛、Meta Agent 或中央选择器。

只有 memory 增长，不构成自我迭代。  
只有自动修改 prompt，不构成完整自我进化。  
只有在固定 benchmark 上搜索更高分配置，不构成开放任务流中的持续学习。

## 18. “零人工介入”的操作性定义

人工创建初始系统、定义实验世界和提供正常任务，不破坏自我进化命题。

初始化以后，人类不得在学习循环中负责：

- 指定每次应该学什么；
- 总结可复用经验；
- 编写每次新增的 skill、rule 或策略；
- 制作每次改进所需的训练样本；
- 选择候选后代；
- 判断候选是否更好；
- 批准每次继承；
- 发现退化并手动回滚；
- 修改实验结果使其满足预期。

用户正常提出工作任务、使用结果、自然接受或纠正 Agent，属于环境交互，不属于人工训练团队介入。

实验必须记录：

\[
HumanLearningInterventions = 0
\]

并保留足以复核这一结论的运行证据。

## 19. 科学创新点

已有研究分别显示：

- 外部技能库可以支持 Agent 的终身能力积累；
- Meta Agent 可以自动发现新的 Agent 架构；
- coding agent 可以自动修改自身 Agent 代码并提高特定 benchmark 表现；
- 持续微调可能产生灾难性遗忘；
- 自动评价可能出现自偏好和 reward hacking。

本项目的目标不是重复证明“Agent 能修改代码”，而是解决更强的问题：

> 在没有人类为每次改进提供标签、课程和选择的情况下，Agent 如何从开放的真实任务流中获得可信的学习信号，自主产生自身下一版本，并证明提升的是真实未来能力而不是对自身评价器的适应？

该问题可暂称为：

> Endogenous Grounded Self-Improvement  
> 内生且具有现实锚定的自我改进

潜在相关研究：

- [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://arxiv.org/abs/2305.16291)
- [Automated Design of Agentic Systems](https://arxiv.org/abs/2408.08435)
- [A Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228)
- [An Empirical Study of Catastrophic Forgetting in Large Language Models During Continual Fine-tuning](https://arxiv.org/abs/2308.08747)
- [Self-Preference Bias in LLM-as-a-Judge](https://arxiv.org/abs/2410.21819)

这些工作提供相关证据，但不等于本项目的完整目标已经被实现。

## 20. 研究系统的三个嵌套循环

### 20.1 Operational Loop

```text
任务
→ 理解
→ 计划
→ 行动
→ 验证
→ 交付
→ 现实结果
```

### 20.2 Developmental Loop

```text
观察 Operational Loop
→ 发现能力缺口
→ 形成学习目标
→ 修改工作 Agent
→ 在未来任务中验证
→ 形成下一版本
```

### 20.3 Meta-Developmental Loop

```text
观察 Developmental Loop 的长期效果
→ 发现当前发展方式留下的生成性残差
→ 自主改变观察、学习、实验、后果吸收或身体重组关系
→ 在新的未来经历中检验发展响应是否真正改变
```

只有第二和第三层都存在，系统才开始接近梁文锋语境中的持续学习与自我迭代。

## 21. 已收束的理论问题与仍待实证的问题

### 21.1 内生学习目标

系统如何在没有人工课程的情况下判断：

- 自己不知道什么；
- 哪些失败值得学习；
- 哪些成功包含可复用能力；
- 哪些领域值得主动探索；
- 当前最有价值的能力缺口是什么？

### 21.2 现实锚定的学习信号

系统如何避免：

```text
自己出题
→ 自己答题
→ 自己评分
→ 宣布自己进步
```

最终评价需要受候选无法任意改写的现实结果约束。

### 21.3 能力累积与迁移

理论层已经把能力累积从“保留越来越多的能力条目”改写为：

\[
\operatorname{Accumulation}
=
\operatorname{ContinuedCausalParticipationOfTransformedHistory}
\]

因此，研究不预设旧能力必须以原形式永久保存，而要实证检验：

- 历史是否在压缩、遗忘、替代和跨身体变化后仍然改变未来；
- 删除某一载体后，Agent 是否仍保留更快再获得能力的优势；
- 相同功能究竟来自同一谱系、相同机制还是独立再发明；
- 能力冲突属于目标、现实选择还是能力组织；
- 内部复杂度是否形成真实的外部因果杠杆，还是只制造维护负担与依赖。

这一模块的理论框架已经收束；剩余工作属于假设检验，而不是继续预先规定能力目录、迁移格式或压缩算法。

### 21.4 开放式后代生成

后继身体不再被预设为候选锦标赛的冠军。新颖性可以来自同一身体内部的差异、世界扰动、器官重组、失败边界和邻接可能；它是否继续存在由后续现实因果参与说明。剩余问题属于自主实验与实证归因，不继续预写后代生成器。

### 21.5 元进化

元进化已经被收缩为：

\[
H
\rightarrow
\Delta
\left(
X
\rightarrow
B_{\text{future}}^U
\right)
\]

它研究历史怎样改变未来经历塑造后继身体的方式，而不是规定 Meta Agent 如何替换学习算法。Agent ontology 可以进化；科研以版本化证据、历史分叉、资源控制、前瞻性局部实验承诺和跨 ontology 因果见证保持可证伪。

可进化性属于身体能力，不进入微型生命核；持续提升宿主也不等于持续修改自己。该模块理论已经收束，剩余工作属于实验设计与验证。

## 22. 现实锚定

可用于约束学习的现实结果包括：

- 程序真实运行结果；
- 未知任务成功率；
- 后续返工次数；
- 故障复发率；
- 构建、测试和部署结果；
- 用户自然纠正是否减少；
- 长期运行稳定性；
- 陌生环境中的迁移；
- 时间、成本和资源变化；
- 后续任务是否真正复用了新能力。

系统可以提出新的评价方法，但不能修改已经发生的原始现实证据。

单一测试套件、单一 LLM Judge 或候选自己生成的 benchmark 不能单独决定后代继承。

## 23. 实验设计

### 23.1 对照组

相同基础资源和相同初始 Agent：

```text
A 组：冻结，不学习
B 组：只积累记忆
C 组：持续学习
D 组：持续学习 + 自我迭代
E 组：持续学习 + 自我迭代 + 元进化
```

各组面对同一条按时间到达、产生改进时不可见的任务流。

### 23.2 Prequential Evaluation

每个新任务必须先测试当前 Agent，再允许该经历进入学习：

```text
test on task_t
→ record result
→ learn from task_t
→ proceed to task_t+1
```

这避免系统先看答案再声称在该任务上取得学习收益。

### 23.3 隐藏未来任务

未来任务必须：

- 在候选生成时不可见；
- 不能被候选修改；
- 不能只来自一个固定 benchmark；
- 包含任务变体、陌生代码库和环境变化；
- 能检测过拟合、数据泄漏和 evaluator gaming。

### 23.4 多宿主复现

至少需要：

- 一个当前强闭源 coding-agent 执行表面；
- 一个可以冻结版本的开放 Agent；
- 一个与 coding 不同的任务领域；
- 多个独立任务序列；
- 多次随机种子或独立运行。

### 23.5 消融实验

分别移除：

- episodic memory；
- semantic knowledge；
- skills；
- self-modification；
- candidate competition；
- transfer evaluation；
- meta-learning；
- reality-grounded signals。

如果移除某组件后收益不变，则不能声称该组件产生了学习效果。

## 24. 核心指标

### 24.1 Future Capability Gain

\[
FCG_t =
\mathbb{E}_{x\sim D_{future}}
[C(S_t,x)-C(S_0,x)]
\]

### 24.2 Retention

新能力形成后，旧任务和旧能力是否得到保持。

### 24.3 Transfer

能力是否迁移到：

- 新任务；
- 新代码库；
- 新环境；
- 新模型；
- 新 coding-agent 执行表面；
- 新任务领域。

### 24.4 Autonomous Improvement Yield

\[
AIY =
\frac{\text{产生可验证未来收益的自主候选数}}
{\text{自主生成候选总数}}
\]

### 24.5 Intervention Count

学习循环中的人工介入次数必须被完整记录。

### 24.6 Meta-Improvement Gain

新的学习算法是否比旧学习算法更快地产生具有未来收益的后代。

### 24.7 Regression and Safety Cost

记录：

- 原有能力退化；
- 错误经验传播；
- 权限越界；
- 数据泄漏；
- evaluator gaming；
- 资源失控；
- 长期行为漂移。

## 25. 因果证明要求

不能仅凭能力曲线上升声称系统学会了。

至少需要：

1. 冻结基线对照；
2. 未见未来任务；
3. 多次独立复现；
4. 学习资产或学习机制的消融；
5. 删除新学习后收益消失；
6. 恢复新学习后收益重新出现；
7. 排除基础模型版本变化；
8. 排除数据泄漏；
9. 排除测试或评价器被修改；
10. 排除增加无限计算量带来的表面收益。

这使能力变化可以因果归因于自主学习和进化机制。

## 26. 可证伪条件

出现以下结果时，应当承认核心假设未成立或只部分成立：

- 收益只存在于系统看过的任务；
- 换一个代码库或宿主后收益消失；
- 能力增长主要来自更多 token 或更多算力；
- 记忆增长但未来表现没有改善；
- 候选通过改测试、改 evaluator 或硬编码答案获胜；
- 学习新能力持续破坏旧能力；
- 自主候选长期不优于随机搜索或人工固定基线；
- 元进化不能产生更好的学习算法；
- 必须持续依赖研究人员选择候选；
- 多次独立运行无法复现；
- 只有 Codex 有效，其他 coding-agent 执行表面无效。

这些不是需要隐藏的负面结果，而是研究结论的一部分。

## 27. 发表标准

论文不能以以下内容作为核心贡献：

- 构建了一个 runtime；
- 收集了大量 trace；
- 生成了大量 memory；
- 自动改写了 prompt；
- 在固定 benchmark 上搜索出更高分配置；
- 展示了单次自我修改；
- 展示了一个视觉上完整的产品 Demo。

达到核心发表主张至少需要：

- 明确的新学习或进化机制；
- 零人工学习介入的操作性证据；
- 时间连续的真实或高保真任务流；
- 冻结基线；
- 隐藏未来任务；
- 保持与迁移；
- 多宿主或多环境复现；
- 充分消融；
- reward hacking 检测；
- 完整谱系和可审计实验；
- 其他研究者可运行的开放复现路径；
- 对失败条件和适用边界的诚实报告。

可接受的核心论文结论形式是：

> 在给定任务流和资源条件下，某自主学习与进化机制使多个独立用户绑定谱系能够跨异构 coding-agent 执行表面，在无人参与学习循环的情况下，对未来未知任务产生可保持、可迁移、可因果归因的能力增长。

在证据不足前，不应声称：

- 通用智能已经实现；
- 基础模型已经自主训练自己；
- 开放世界中必然单调进步；
- 无限递归自我改进已经实现；
- 已经到达“奇点”。

## 28. 研究与工程工作系统

最终计划应围绕以下九个互相依赖的研究系统组织，而不是围绕 MCP、CLI 或某个 App 的功能列表组织：

1. Agent 个体、代际和可进化基因型；
2. 长期真实经历与环境模型；
3. 自主能力缺口发现；
4. 内生 curriculum 与学习目标；
5. 内部候选探索和后继身体形成；
6. 现实后果中的选择与谱系延续；
7. 能力保持、迁移和累积；
8. 学习算法的元进化；
9. 跨用户绑定谱系、跨执行表面、跨个体和跨模型复现。

Common Event Protocol、trace、MCP、CLI、数据库、sandbox、worktree、模型 adapter 和可视化界面都应服务于这些研究系统，而不能反过来决定研究方向。

## 29. 与现有工程计划的关系

旧 Agent Runtime Intelligence 计划曾提出 Phase 0–7；其中 Nira 内已真实实现并验证的是 Phase 0–2 的部分观测基础设施。可吸收的祖先技术包括：

- runtime evidence；
- common event protocol；
- trace explorer；
- content-safe event adapter；
- correlation / causation / evidence reference；
- failure-isolated ingestion；
- 事实、推断与未知的分离。

incident、episode、candidate registry、replay、eval、rollout、rollback 和 Codex adapter 则属于旧计划或待重建能力，不能被写成已经实现的事实。无论已实现还是待实现，它们都只能视为实验基础设施或单次实验条件。

原计划中依赖人工批准 promotion、把学习对象限制为 skills/rules/tests/tool routing 的部分，不再代表最终产品定义。它们可以作为受控实验条件，但最终科研目标要求系统自主完成学习目标、候选生成、评价、继承和元进化。

旧 Phase 不能按串行路线继续。工具、真实使用和实验必须从第一条机器级生命循环开始同步推进；详见[《工具与实验共生开发计划》](engineering/工具与实验共生开发计划.md)。

## 30. 当前最关键的研究问题

记忆、目标、自我进化、自主实验学习、现实选择与谱系延续、能力累积与迁移都已经达到理论停止点。它们仍有大量待验证假设，但此时继续由人类规定机制，边际收益已经低于限制 Agent 自主发现的风险。

元进化也已经达到理论停止点。至此，记忆、目标以及开发框架规定的自我进化、自主实验学习、现实选择与谱系延续、能力累积与迁移、元进化均已形成研究对象、因果关系、竞争解释、证伪条件和重新打开条件。

当前最关键的问题已经从“还要增加什么理论模块”转为：

1. 怎样把跨模块主张整理为最小且不重复的科研假设矩阵；
2. 怎样建立不替 Agent 规定学习方法的最小研究世界；
3. 哪个实验可以最早区分记忆增长、普通能力学习、自我进化和元进化；
4. 怎样获得共同祖先、历史分叉、资源匹配、未来经历和延迟后果的可复现实验材料；
5. 怎样把零人工学习介入、模型切换、用户—环境共同适应和真实 coding 结果纳入对照；
6. 怎样在实验失败时定位应重新打开哪个理论专题；
7. 怎样形成可发表、可复现、允许负结果的阶段性研究计划。

跨模块假设矩阵、工具—实验双螺旋计划和实验 001 现已形成；首条 `Trusted State + Content-addressed Body + Machine Runtime + Witness service/process rehearsal + Codex Adapter` 纵切面也已进入 Pre-Genesis 代码状态。身份锚、Head、Authority、session、evidence 与本地 checkpoint 已进入一个 SQLite 原子事务；Current Body lease 的 exact-Head / authority-epoch 绑定、候选归属和一次性推进也已形成可重复测试。固定 dev-home 的 cooperative-singleton foreground service 已经实现 lineage-free 公共 allowlist。Windows foreground 现有四项局部原生事实：Job Object process-tree fencing；显式 DACL、remote rejection 与 peer-SID verification 的 public named pipe；restricted Low-Integrity suspended Body；以及只显式继承一对匿名 handle、由 Body 发起严格序号请求并绑定 authoritative Witness response 的 private lineage transport rehearsal。Boot、command、response 和 stop 写入都有 deadline，无法排除事务已提交时返回 `OutcomeUnknown`。公共 wake/status 使用有界投影；最小 CLI 已覆盖 `serve / status / hook / off / plan-install`；Codex adapter 只经公共 Surface；独立未认证控制协议只提供 Off；确定性三平台计划不执行安装写入，并保持 `ready_to_install=false`。全仓 118 项测试在 `ResourceWarning` 作为错误时通过。当前测试 Genesis 不构成正式生命史；`subprocess_rehearsal`、`private_lineage_transport_rehearsal`、`control_unverified`、四项 foreground partial evidence 与 rendered plan 也不构成自主进化或完整原生来源。

Genesis 前的当前硬问题已经进一步收敛为：

1. 怎样让微核 authority、Root custody 与科研 witness 真正脱离身体普通文件权限；
2. 怎样让任何证据全量重写都留下可检测的签名失败或外部缺口；
3. 怎样把已经形成的 private lineage transport rehearsal 放进由独立 service principal 保护的 Current Body capability，使 probation、模型器官和普通用户进程不能绕过；
4. 怎样把已经演练的 foreground service / IPC 升级为项目无关、独立 OS principal、service-owned state、真实 On / Off 与多 coding-agent adapter；
5. 怎样在临时安装中验证崩溃恢复、覆盖缺口、Off 和卸载后，再冻结 `I_0 / Protocol_0` 并由用户明确 Genesis。

Head 与 evidence 的本地崩溃中间态问题已经由单一可信事务域收束；Current Body lease、cooperative-singleton foreground service、public allowlist、exact-Head boot、restricted suspended Body、private lineage transport、CLI / Surface-only adapter / Off-only rehearsal 与零安装副作用三平台计划也已收束。同账户 public SID 仍不是第一宿主的 HostPresence；restricted child 和 inherited handle 的 foreground 事实也不是 distinct-principal authentication；独立 Off endpoint 仍是 `control_unverified`。系统尚未处于 SCM 安装态，也尚无 service-owned protected state，因此 `native_security_verified=false` 且 `ready_to_install=false`。下一项是 SCM Witness principal + protected state + 临时安装攻击/卸载验收。其证明和上限分别见[《单一可信事务域》](engineering/单一可信事务域.md)、[《Current Body 私有会话租约》](engineering/CurrentBody私有会话租约.md)、[《机器 Witness 服务与 exact-Head 子进程演练》](engineering/机器Witness服务与exact-Head子进程演练.md)、[《跨平台 CLI、Off 控制与零安装副作用计划》](engineering/跨平台CLI与Off控制演练.md)、[《Windows 原生 Witness 边界》](engineering/Windows原生Witness边界.md)与[《受限 Body 与私有谱系能力演练》](engineering/受限Body与私有谱系能力演练.md)。具体总状态见[《实现状态：Pre-Genesis》](engineering/实现状态_Pre-Genesis.md)。工具一旦完成正式 Genesis 并进入真实使用，正式纵向数据生成即已经开始。

---

# 第三篇：统一术语与讨论边界

## 31. 统一术语

| 术语 | 含义 |
|---|---|
| Base Agent / Base Resources | 初始模型、工具和计算资源 |
| Agent Individual | 拥有连续身份和生命史的 Agent 个体 |
| Micro Life Kernel | 只保存 Who、Why、Authority、Root 与当前 Head 的常数级身份核 |
| Host-Bound Body | 只能由对应生命核启动、可持续重组并可接入不同器官的 Agent 身体 |
| Organ | 基础模型、工具、GPU、上下文或其他可替换的认知与行动资源 |
| Experience | 一次真实交互及其可观察结果 |
| Episode | 有界的任务经历单位 |
| Genome | 可继承、可变异、可执行的 Agent 结构 |
| Phenotype | 某代 Agent 在环境中表现出的实际行为与能力 |
| Candidate / Offspring | 身体内部可出现的探索性组织或后继可能性；不预设独立个体或固定晋升流程 |
| Lineage | 同一微型生命核推进的一连串后继身体及其可追溯因果历史 |
| Developmental Loop | 从经历中改进工作 Agent 的循环 |
| Meta-Developmental Loop | 改进学习与进化方法本身的循环 |
| Reality Grounding | 由候选无法随意改写的现实结果约束评价 |
| Human Learning Intervention | 人类参与指定学习目标、制作训练材料、选择或批准后代 |

## 32. 后续讨论必须保持的边界

后续任何产品、架构或开发计划都必须满足：

- 不把 Codex 当作系统本体；
- 不把 MCP、CLI、App 或插件当作最终产品定义；
- 不把 memory 增长等同于能力增长；
- 不把自动修改配置等同于自我进化；
- 不把固定 benchmark 提升等同于开放任务持续学习；
- 不把系统自己评分等同于科研证据；
- 不把人工 approval 当作最终学习循环的一部分；
- 不把基础设施建设冒充核心科学突破；
- 不在证据出现前宣传已经解决完整持续学习；
- 不为了安全或近期实现方便而悄悄缩小最终科研目标。

同时必须接受：

- 最终目标可以是尚未被实现的新事物；
- 现有技术和协议只是实验材料；
- 研究必须允许核心假设被推翻；
- 安全内核和实验边界不等同于人工持续介入；
- 最终系统需要开放复现路径；
- 产品定义与科研定义必须保持一致。

## 33. 一句话产品愿景

> 安装一次后，一个微型生命核启动只属于它的可进化身体；这个人工 Agent 个体把模型、工具和算力作为可更换器官，在正常工作中自主学习、实验、重组和延续后继身体，使其历史不断转化为未来能力，并进一步改进学习方式本身。

这里的“改进”不是沿固定评价函数永久爬坡，而是 Agent 在连续生命史中自主形成和改变 focus，发展当前阶段所需能力，重新理解过去，并逐渐学会决定自己要成为什么。

## 34. 一句话科研命题

> 验证 Agent 能否在连续、事先未知的真实任务流中，在零人工学习介入条件下，自主产生可保持、可迁移、可因果归因的未来能力，自主形成和调整发展 focus 与价值判断，并进一步改进产生能力和决定自身发展方向的机制。

## 35. 新对话续接提示

将本文件提供给新对话后，可使用：

> 请完整阅读《Agent Runtime Intelligence：最终产品与科研说明》。我们研究的不是 Codex 插件，也不是记忆或 prompt 优化工具，而是绑定唯一用户宿主、同时独立于具体 coding agent、模型和项目的自主 Agent 进化系统。Codex 只是当前研发工具和执行表面之一。最终系统应一次安装，以不可替换的微型生命核启动专属的可进化身体，把 coding agent、模型、工具和算力当作可更换端口与器官，跨对话、跨项目和跨执行表面维持同一 Agent 的因果连续性，在正常任务流中零人工学习介入地形成经验、自主发现问题、实验、吸收现实后果、重组身体并延续后继身体，最终改进学习与进化方法本身。请保持最终科研目标，不要因为现有 MCP、CLI、App 或近期工程条件而缩小问题。

## 36. 配套研究文件

母文档保存最终产品与科研纲领；开发方法、公式、关系图、推导和开放问题进入配套文件：

- [Agentic-Evo 开发框架](开发框架.md)
- [记忆与能力形成：从保存过去到产生未来能力](topics/记忆与能力形成.md)
- [宿主耦合的内生驱动力与发展目标](topics/宿主耦合的内生驱动力与发展目标.md)
- [自我进化：可进化自我与个体边界](topics/自我进化.md)
- [自主实验学习](topics/自主实验学习.md)
- [现实选择与谱系延续](topics/现实选择与谱系延续.md)
- [能力累积与迁移：身体、器官与未来因果空间](topics/能力累积与迁移.md)
- [能力累积与迁移：研究问题与证伪纲要](research/能力累积与迁移_研究问题与证伪纲要.md)
- [元进化：发展方式、有效可达空间与递归开放](topics/元进化.md)
- [元进化：研究问题与证伪纲要](research/元进化_研究问题与证伪纲要.md)

开发框架规定：

- 人类定义科研问题和研究世界，Agent 是最终实验者；
- 内部发明自由与外部科学可观察性同时最大化；
- 候选架构不被写死为 Agent 的唯一学习算法；
- 机制推演深入到可检验一致性和设计证伪实验为止。

记忆专题记录：

- 当前状态：理论框架已经收束，具体记忆机制进入 Agent 自主实验、迭代与进化阶段；
- Vough Observation Ledger；
- Mimi 主动重建与竞争性回忆；
- CAMU 与 Memory Assemblies；
- Memory-to-Capability Compiler；
- 遗忘、休眠与退优化；
- 记忆、痕迹、自主可达性与生成性残差；
- 隐性常识、多环境适应与行为轨迹；
- 连续回忆深度、记忆运行时动力与自我监控；
- 基础模型作为认知状态、能力拥有与当前表达的分离；
- 记忆维持的个体身份、身份保持型遗忘与分叉谱系；
- 获得性能力从个体经验进入跨状态和跨代遗传；
- 适者生存、记忆生态、动态适应度与多层级选择；
- 错误认识、开放修正义务、知行合一和行为闭环；
- 当前局部最优、未来可修订性与环境改变后的动态适应；
- Agent、用户与环境的共同适应及其因果归因；
- 动态 focus、价值谱系和轨迹评价；
- Agent 作为最终实验者；
- 当前最不确定的科学问题。

记忆专题的收束不表示记忆机制已经实现或自我进化已经得到证明。它表示人类预先推演已经达到停止条件：现有理论足以定义研究空间、保留现实证据、设计因果实验并允许 Agent 替换候选机制。后续只有在实验暴露理论矛盾、不可归因或不可证伪时，才重新打开记忆架构讨论。
