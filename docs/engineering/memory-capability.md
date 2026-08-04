# Memory-to-Capability mechanism space (v1.0) — CAMU 存储

English / 中文混合说明。本模块为 Agentic-Evo v1.0 建立 **Memory-to-Capability 机制空间**
（mechanism space）：(a) 不可变经验归档引用，(b) 可替换的 active recall / forget /
sleep-consolidation 脚手架，(c) Memory-to-Capability 的 CAMU 契约。它**不**预置任何具体
算法（GOAL.md 第一原则禁止人类预写记忆 schema、检索/遗忘算法、学习速度、能力目录、候选生成与
评估函数、演化节奏）；这些机制必须由 Body 发明、替换与演化。

- 模块：`src/agentic_evo/memory_store.py`
- 文档：本文件
- 测试：`tests/test_memory_camu.py`（32 个用例）
- 存储默认路径：`<agent home>/memory/camus.jsonl`（可配置）

## 设计（Design）

1. **Body 所有，Witness 无关**。记忆是 Body 可演化的机制，绝不并入 `trusted/state.sqlite3`
   （身份 / 谱系 / 证据 / 检查点领域）。CAMU 存储是 caller-owned 组件：只管理自己的
   append-only JSONL 文件，不导入、不修改 `trusted` / `body` / `kernel` / `runtime` /
   `witness`。它只通过 `evidence_refs`（`{sequence, hash}`，按 EvidenceRecord 形状校验）
   **引用**证据作为 grounding，绝不读写 trusted 状态。
2. **append-only + hash 链**。文件首行是 meta 头，后续每行是 `camu`（内容寻址）或
   `camu_update`（use / outcome / status 记账）记录；每行 sha256 链接到上一行
   （复用 autonomous_loop.py 的最小 sha256 链模式，但不导入它）。`verify_chain()` 校验
   序列连续、previous hash 匹配、逐行 hash 匹配、内容地址匹配、update 引用存在；任一字节
   被篡改即失败。
3. **内容寻址**。`id = sha256(canonical_json(record))`，record 即 ⟨G,A,I,P,E⟩ 五元组。
   同语义记录在任何 store 中 id 相同；重复内容地址被拒绝。
4. **有效记录（effective record）**。任何记录都不被改写：`record_use` / `record_outcome` /
   `consolidate` 只追加 chained `camu_update` 行；`get` / `list` 重放（replay）基础记录 +
   全部 update，得到有效记录（含累积的 `use_log`、`support` / `oppose` 与 `status`）。

## CAMU 契约（Contract）

每条记忆 m_i = ⟨G_i, A_i, I_i, P_i, E_i⟩：

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| G | grounding | `evidence_refs`（可选，列表，每项 `{sequence:int>=1, hash:sha256hex}`，按 EvidenceRecord 形状校验）、`provenance`（必填字符串）、`collected_at`（必填 ISO-8601） |
| A | activation | Body 自写的 JSON 谓词声明（dict），如 `{"domain": "...", "when": "..."}`；**不是**固定相似度算法，评估器是可插拔钩子 |
| I | influence | `{"domain": predict\|context\|tool_selection\|plan\|executable_skill\|learning_goal\|successor_generation, "description": str}`；未知 domain 拒绝 |
| P | prediction | `{"condition", "expected", "counterfactual"}`（均为必填字符串）+ `status`；可证伪的可观测声明：有无此记忆的差异；创建时 status 必须为 `pending` |
| E | epistemic | `uncertainty`（必填，0..1）、`support` / `oppose` / `conflicts` / `use_log`（列表）、`version`（int>=1）、`lineage`（null\|父 CAMU id） |

预测状态机（`record_outcome` 的**状态记账规则**，不是学习算法）：

```
pending  ->  verified      所有已记录 outcome 均支持（support 非空且 oppose 为空）
pending  ->  contradicted  所有已记录 outcome 均反对（oppose 非空且 support 为空）
*        ->  pending       混合证据（support 与 oppose 均非空）
pending  ->  overdue       consolidate：pending 且超过 TTL（默认 7 天）
```

`use_log` 只追加：`record_use` 每次追加 `{at, context, observed, matched: null}`，
绝不改写历史。

## API

```python
MemoryStore.create(path)                  # 建新 store（已存在则 MemoryRecordError）
MemoryStore.load(path)                    # 加载并校验 hash 链
store.verify_chain() -> bool              # 全链校验；篡改即 MemoryIntegrityError
store.add_camu(record) -> id              # 契约校验 + 内容寻址追加
store.get(id) -> {id, sequence, recorded_at, record}   # 有效记录（重放 update）
store.list() -> list[dict]                # 全部有效记录（链序）
store.count() -> int
store.record_use(id, context, observed=None)           # use_log 追加
store.record_outcome(id, observed, matched: bool)      # outcome 记账 + 状态翻转
store.recall(context, evaluator=None) -> list[str]     # 默认占位评估器；可传 Body 评估器
store.consolidate(now=None, ttl_seconds=7*86400)       # pending+超龄 -> overdue；不删除
```

错误类型（`src/agentic_evo/errors.py`，风格与现有错误一致）：

- `MemoryRecordError`：契约违规（缺字段、未知 influence domain、evidence_refs 形状错误、
  未知 CAMU id、重复内容地址等）。
- `MemoryIntegrityError`：链完整性失败（篡改、序列断裂、内容地址不匹配、update 引用未知
  CAMU、store 缺失/不可读等）。

## 边界（Boundary）

- **无固定 recall / forget / learning 算法**。`default_evaluator` 只是平凡的 JSON 谓词
  占位符（context 包含 A 声明的每个键值对即匹配）；`recall(context, evaluator)` 接受 Body
  自带的评估器。consolidate 是**状态脚手架**：只把 pending 且超龄的预测标为 `overdue`，
  从不删除、从不实现遗忘策略。
- **Body 替换机制**。本 slice 只证明机制空间存在、可校验、且归 Body 所有；真正的召回、遗忘、
  学习、能力形成算法由 Body 在后续 slice 发明并替换（例如传入自定义 evaluator）。
- **不在 TCB**。CAMU 记录无 HMAC 签名（声明式、可替换）；完整性由 hash 链提供，
  不是签名/信任锚。
- **不承诺能力增益**。记忆记录、预测、状态翻转都是记账与可审计性机制，不等于能力形成。

## CLI 用法

```text
agentic-evo memory-camu-add       --store <path> (--record <json> | --record-file <file>)
agentic-evo memory-camu-list      --store <path>
agentic-evo memory-camu-show      --store <path> --id <camu_id>
agentic-evo memory-camu-outcome   --store <path> --id <camu_id> [--observed <json>] --matched true|false
agentic-evo memory-recall         --store <path> --context <json>
agentic-evo memory-consolidate    --store <path> [--ttl-days 7]
agentic-evo memory-verify-chain   --store <path>
```

`--store` 默认 `memory/camus.jsonl`（当前目录下）。输出为一行 JSON
（`{"ok": true, "result": ...}`）；失败写 stderr 并返回非零退出码：

- `0`：成功；`6`：memory_error（契约违规、store 不可用、JSON 解析失败等）；
- `7`：`memory-verify-chain` 校验失败（链被篡改或损坏）。

示例：

```powershell
$env:PYTHONPATH='src'
python -m agentic_evo.cli memory-camu-add --store memory/camus.jsonl --record-file rec1.json
python -m agentic_evo.cli memory-recall --store memory/camus.jsonl --context '{"domain":"codex","when":"compile_error"}'
python -m agentic_evo.cli memory-consolidate --store memory/camus.jsonl --ttl-days 7
python -m agentic_evo.cli memory-verify-chain --store memory/camus.jsonl
```

## 声明上限（Claims ceiling）

**已建立（established）**：

- CAMU 机制空间存在、可审计：append-only hash 链 + 内容寻址 + 契约校验；
  任意字节篡改会被 `verify_chain` 拒绝；
- 存储是 Body-owned / caller-owned：模块只依赖 stdlib 与 `errors`，不导入
  trusted/body/kernel/runtime/witness；只以 hash 引用证据；
- `record_use` 只追加、`record_outcome` 按记账规则翻转 pending→verified/contradicted、
  `consolidate` 把 pending+超龄标为 overdue 且从不删除；
- recall 评估器可插拔（Body 可替换）。

**未主张（NOT claimed）**：

- 不主张记忆形成、因果能力增益、学习、自我演化，或不主张任何召回机制是"那个"记忆系统；
- 默认 recall 评估器是显式占位符，不是检索/相似度算法；
- consolidate 只是状态脚手架，不是遗忘策略（不删除、不衰减、不合并）；
- 尚无真实任务集成（后续 slice）；本 slice 仅冒烟测试；
- 不主张跨进程高并发强一致性（追加以 OS 原子 append 为界，见实现报告）。
