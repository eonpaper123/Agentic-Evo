# Observer 24h 证据闭合与覆盖修复

## 结论

24 小时固定窗口已经正常结束：1,335 个周期、28,436 条脱敏派生记录，collector 为 `ended / healthy / exit 0`。原始最终报告和 metrics 保持冻结，不被本切片改写。

14 个唯一 Observation 的闭合口径为：9 个 confirmed TP、0 个 known FP、5 个 unresolved、0 个仍待裁决的 unclassified。这里的“闭合”不是强行二分：5 项均已得到明确状态与原因，但允许的证据只能证明“结构化结果报告失败”，不能独立证明外部事件或根因，因此不能诚实地改判为 TP 或 FP。

## 证据边界

本轮只使用冻结的最终报告、最终 metrics、脱敏派生事件锚点和有界元数据。未读取 raw prompt、transcript、tool arguments/body、secrets、token ledger、SQLite 或私密会话正文。

## 五项闭合账本

| ID | cycle | UTC | duration ms | 脱敏锚点 | 闭合状态 | 原因 |
|---|---:|---|---:|---|---|---|
| UO01 | 235 | 2026-08-13T08:31:25.140967Z | 7 | `cycle-000235#line:52` | unresolved | 只有结构化失败；允许证据边界内没有独立外部确认。 |
| UO02 | 843 | 2026-08-13T18:45:48.639754Z | 1564 | `cycle-000843#line:25` | unresolved | 只有结构化失败；允许证据边界内没有独立外部确认。 |
| UO03 | 844 | 2026-08-13T18:47:11.251296Z | 15022 | `cycle-000844#line:23` | unresolved | 只有结构化失败；允许证据边界内没有独立外部确认。 |
| UO04 | 847 | 2026-08-13T18:49:46.534514Z | 15024 | `cycle-000847#line:10` | unresolved | 只有结构化失败；允许证据边界内没有独立外部确认。 |
| UO05 | 847 | 2026-08-13T18:49:53.437678Z | 0 | `cycle-000847#line:70` | unresolved | 只有结构化失败；允许证据边界内没有独立外部确认。 |

机器可读账本见 `docs/research/Observer_24h证据闭合.json`。

## 最小覆盖修复

真实试运行暴露了两类产品缺口，本切片只修这两类：

1. JSONL 单条记录按 256 KiB 上限二进制有界读取；超大记录不解码、不保留正文，计入 skipped，并显式记录 `oversize JSONL record skipped`。
2. provider 无法识别或 bounded adapter 无法投影的对象不再只有普通 warning；它仍被跳过，但同时显式记录 `event shape not supported by bounded adapter projection`。

这不是通用 fallback，也不把未知形状猜成已支持事件。输入保持只读；warning、JSON 和 Markdown 均不包含被跳过的记录正文；重复运行结果确定。

## 声明上限

当前只能声称 Observer 能在脱敏、证据锚定条件下发现结构化失败，并对未知形状与超大记录留下可复现的覆盖缺口。它不证明外部根因、修复正确性、能力增益、自我进化、完整覆盖、精度、召回率或“零误报率”。反事实能力增益实验不在本切片中执行。

## 下一阶段入口

反事实能力增益只完成预注册入口，尚未执行，具体 run manifest 也未注册。因果目的、四臂和运行门槛见 [E003：Observer 反事实能力增益预注册入口](../../experiments/003_Observer反事实能力增益.md)。
