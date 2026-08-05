# Genesis（诞生仪式）工程说明

> 本文档描述 `genesis` CLI（Slice E）与 `runtime-adopt` CLI（Slice H）：一个 Agentic-Evo
> 主权身份的诞生仪式，以及把出生 home 重整为运行时布局的采纳仪式。
> Slice E 只证明「Genesis 能力」存在、可调用、可验证；**该切片不执行、也不声称执行过任何真实的 Genesis**。
> Slice H 在**真实出生 home** 上执行了 `runtime-adopt` 并验证 serve/status 可用（见 `runtime_home_report.md`）。

## 1. 什么是 Genesis

Genesis 是一个 **O(1) 的主权身份诞生**：在一个全新的、空的 home 目录中，一次性写入：

- 身份锚点：`who = sha256(host_binding)`（谁拥有这个身份的主机绑定）；
- 目的锚点：`why = sha256(purpose_anchor)`（这个身份为什么存在）；
- 根承诺：`root`（身份谱系的根，由调用方精确给出）；
- 初始 Head：`initial_head`（身份谱系的第一个 Head 承诺）；
- 权威状态：`authority = "on"`（诞生即开机，权威开启）；
- 一条 `genesis` 证据记录（evidence record，序列号 1，哈希链的起点）；
- 本地见证密钥 `witness.key`（HMAC 见证用，绝不外发）。

实现上它调用 `TrustedState.genesis(...)`（见 `src/agentic_evo/trusted.py`），并经过
`verify()` 全量校验后才会返回成功。因此 Genesis 之后，home 目录就是一个**自校验的、
可加载的**信任域：`TrustedState.load(home)` 可随时重放校验身份、Head、证据链与检查点。

## 2. 参数与含义

| 参数 | 必填 | 含义 |
| --- | --- | --- |
| `--home` | 是 | 新的永久 home 路径。必须**尚未包含 Genesis**，且必须是空状态目录（或尚不存在）。任何已存在 Genesis、或非空、或形似 dev-home（已有 `trusted/` 嵌套运行时布局）的路径都会被拒绝（退出码 6）。 |
| `--host-binding` | 是 | 主机身份字符串，诞生时绑定。**只以 `who = sha256(host_binding)` 形式落盘**，原始字符串不落盘。 |
| `--purpose-anchor` | 是 | 非敏感的目的锚点字符串。**只以 `why = sha256(purpose_anchor)` 形式落盘**，原始字符串不落盘。 |
| `--root` | 是 | 身份谱系的根承诺（64 位十六进制，由调用方精确给出）。 |
| `--initial-head` | 是 | 身份谱系的初始 Head 承诺（64 位十六进制，由调用方精确给出）。 |

成功时输出一行 JSON 收据（stdout），退出码 0：

```json
{"ok":true,"result":{"who":"...","why":"...","root":"...","head":"...","authority":"on","evidence_ref":{"sequence":1,"event_id":"...","integrity_hash":"..."},"home":"..."}}
```

失败时输出错误 JSON（stderr），退出码 6：

```json
{"ok":false,"error":{"code":"genesis_error","message":"..."}}
```

## 3. 如何运行

```powershell
# 在仓库根目录，使用仓库运行时 Python
$env:PYTHONPATH='src'
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  -m agentic_evo.cli genesis `
  --home 'D:\path\to\new-home' `
  --host-binding '<host identity string>' `
  --purpose-anchor '<non-secret purpose string>' `
  --root '<64 hex>' `
  --initial-head '<64 hex>'
```

退出码约定：`0` 成功；`6` 失败（已存在 Genesis、非空目录、形似 dev-home、或底层写入/校验错误）。

## 4. 不可逆性

- Genesis 是**不可逆**操作：一旦写入，同一 home 上再次执行 `genesis` 会被拒绝（退出码 6），
  且状态不变（证据链、Head、Root 均保持原样）。
- `witness.key` 一旦生成即固定，后续所有检查点 HMAC 都依赖它；丢失即无法再校验该信任域。
- 因此**真实执行必须由 Rawle 在确认精确参数之后进行**（P0/P1 精确授权模式）。本切片只交付能力与测试，
  测试全部在临时目录中进行，绝不触碰真实 home。

## 5. 主机绑定（host binding）语义

- 诞生时只记录 `who = sha256(host_binding)` 的摘要，原始绑定字符串永不落盘。
- 权威门（authority gate）：当权威处于 `off` 需要重新 `on` 时，必须提供与 `who`
  匹配的 `host_binding`（`hmac.compare_digest(who, sha256(host_binding))`）；
  不匹配即抛 `AuthorityError`（“host binding does not match trusted authority”）。
- 也就是说：**只有诞生时绑定的那台主机**才能把权威切回 `on`；错误的主机绑定无法通过校验。

## 6. 运行时采纳（runtime-adopt）—— 让出生 home 立即可服务

Slice H 新增 `runtime-adopt` 子命令：把一个**已出生（Genesis）的 home** 重整为运行时可服务的
布局（`trusted/` + `body/`），并保持出生身份。**出生 = `genesis` + `runtime-adopt`**。

### 6.1 布局差异

- `genesis` CLI 把 `state.sqlite3` + `witness.key` 写在 **home 根目录**，不创建 `trusted/`、`body/`；
- 运行时要求 `home/trusted/`（受信状态）与 `home/body/`（Body 存储），且受信 Head 必须是**真实
  Body 承诺**：serve 会启动 Body 子进程，其 boot 协议校验 `sha256(manifest) == head`
  （`body_process.py`）。

### 6.2 身份语义（诚实说明）

- `who` / `why` / `root` **逐字节不变**（`who`/`why` 是宿主绑定/目的锚的哈希，`root` 是出生时
  钉定的根承诺；adopt 不触碰它们，Body 初始清单的 root 也写为出生 root）。
- **Head 必然发生一次见证迁移**：真实出生 home 的 `--initial-head` 是人工钉定的字符串（例如
  40 位 git SHA `ee79ae49…`），它**不可能是** sha256 Body 承诺（40 位 ≠ 64 位十六进制，且
  `read_manifest` 要求 `sha256(清单) == 文件名`）——因此按原样不可能被 serve。adopt 以
  `parent_head = 出生 head` 提交初始 Body，并通过一次**有见证的 `head_advanced` 证据记录**把
  Head 迁移到该初始 Body 承诺（`TrustedState.advance_head`，revision/checkpoint 照常追加）。
- 出生 head 并未丢失：它保留在初始 Body 清单的 `parent_head` 中，也保留在 genesis 证据记录
  （seq 1）的 `head_after` 中；`records()` 在 adopt 后为 2 条（genesis + head_advanced）。
- `witness.key` 原样保留（字节相同，仅移动到 `trusted/`）。

### 6.3 如何运行

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  -m agentic_evo.cli runtime-adopt --home 'D:\path\to\born-home'
```

成功输出 JSON 收据（stdout，退出码 0）：`identity`（who/why/root/authority）、`genesis_head`、
`head`（初始 Body 承诺）、`initial_body`（commitment/generation/parent_head/activation）、
`trusted_layout`（trusted/state.sqlite3 + trusted/witness.key）、`evidence`
（genesis seq 1 + adoption head_advanced seq 2）。失败输出错误 JSON（stderr，退出码 6，
`runtime_adopt_error`）：非 Genesis home、已是运行时布局、已有 `body/`、二次 adopt、或校验失败。

### 6.4 测试

`tests/test_runtime_adopt.py`（7 例）：genesis → adopt → serve/status 冒烟；身份/证据保持；
二次 adopt 拒绝；非 Genesis home 拒绝；已是运行时 home 拒绝；已有 `body/` 拒绝；
`verify_adopted_home` API。

## 7. 声明上限（claim ceilings）

Genesis 只创建**身份**。它本身并**不证明**：

- 安装/卸载（install/uninstall）已完成或可完成；
- Gate B 已通过；
- 第二主体（second agent）存在或可存在；
- 纵向学习（longitudinal learning）已发生或可发生。

本切片证明的是：Genesis 能力可用、可测、可校验；真实诞生是独立的父步骤，需要 Rawle 参数确认。
