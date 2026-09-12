# 自主修复循环真实任务集成演示（Slice C）

本文件说明 v1.0 第 4 项需求的推进：**自主修复循环驱动一个真实任务流程**。
此前 `autonomous_loop.py` 只在内存 smoke fixture 上运行；本 Slice 构建了第一个
调用方 `loop_integration.py`，让循环端到端地驱动一个**合成但真实**的缺陷修复流程：
真实 Python 模块、真实失败的 unittest、真实的补丁应用/回退、真实的测试执行、
真实的后果读取（commit/rollback），并沉淀为可复跑的证据。

## 集成了什么（What was integrated）

| 交付物 | 位置 | 说明 |
| --- | --- | --- |
| `loop_integration.py` | `src/agentic_evo/` | 调用方所有（caller-owned）的集成模块：`DefectWorkspace`、`build_candidates`、`run_loop_demo` |
| `cli.py` 扩展 | `src/agentic_evo/` | 新增 `loop-demo-run` 子命令（JSON 输出，成功退出码 0） |
| 测试 | `tests/test_loop_integration.py` | 6 项 unittest：工作区创建、完整循环、失败回退、可复跑、策略门、导入边界 |
| 文档 | 本文件 | 集成说明、运行方式、声明上限 |
| 报告 | `loop_integration_report.md`（工作区根） | 文件清单、逐字测试输出与退出码、设计决策、剩余风险 |

## 工作区与候选（Workspace and candidates）

`DefectWorkspace.create(root, defect="off_by_one")` 写入两个**确定性**文件：

- `util.py`：真实的 `window(values, n)` 滑动窗口函数，植入了一个 off-by-one
  缺陷（`range(len(values) - n)` 丢掉了最后一个窗口）；
- `test_util.py`：真实 unittest，在有缺陷代码上**失败**、在正确代码上**通过**。

`run_tests()` 用**当前解释器**在工作区目录执行 `python -m unittest test_util -v`，
返回 `(passed, exit_code, stdout, stderr)`。执行前会清理工作区 `__pycache__`，
保证探测进程编译的是被补丁后的源码（见下文设计决策）。

`build_candidates(workspace)` 按顺序返回两个 `CandidateRepair`：

1. `cand-window-wrong-fix` —— 错误的修复（`range(len(values))`），测试仍然失败；
2. `cand-window-correct-fix` —— 正确的修复（`range(len(values) - n + 1)`），测试通过。

两者都是 `scope="workspace.util_patch"`、`effect_kind="external"`、
`reversible=True`、`touches_life_core=False` 的**可逆文本替换**：apply 把缺陷行替换为
目标行（幂等，重复应用无副作用）；revert 写回**字节级一致**的原始内容。

## 循环流程（run_loop_demo）

```
observe -> candidate -> probation -> outcome -> commit | rollback -> consolidate
```

1. **observe**：先运行一次真实测试，把失败证据（缺陷、退出码、输出尾部）作为
   `observe` 记录写入 `events.jsonl`；
2. 依次对每个候选执行 **propose → probation → outcome → settle**：
   - probation 窗口内应用补丁并反复运行真实测试（有界：次数 + 墙钟）；
   - outcome 仅允许 passed / failed / inconclusive；
   - passed → **commit**（保留补丁）；否则 → **rollback**（调用 revert，并校验
     模块字节与原始字节一致）；
3. 第一个通过的候选会**再次跑完整循环**，使 pass count 达到
   `promotion_passes_required`（默认 2）后 **consolidate**：把可复用 procedure
   追加到 `consolidation.jsonl`；
4. 最后再次运行真实测试，返回 JSON 报告
   （`workspace / loop_home / events_path / consolidation_path / defect /
   candidate_ids / final_module_passed / final_status / outcome_counts` 等）。

默认演示策略为 `LoopPolicy(authorize_external_effects=True)`（候选会修改工作区
真实文件）；可通过 `policy=` 覆盖。

## 如何运行（How to run）

仓库运行时 Python（已核验存在，Python 3.12.13）：

```powershell
# 1) 测试套件
$env:PYTHONPATH='src'
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_loop_integration -v

# 2) 新模块语法编译（-S）
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -S -m py_compile src\agentic_evo\loop_integration.py src\agentic_evo\cli.py tests\test_loop_integration.py
```

## CLI 用法（CLI usage）

```powershell
$env:PYTHONPATH='src'
& 'C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m agentic_evo.cli loop-demo-run `
  --workspace-dir <新目录> --loop-home <新目录> [--defect off_by_one]
```

- 输出：单行 JSON（`{"ok":true,"result":{...}}`）；
- 退出码：成功（`final_module_passed=true`）为 0；失败为非 0（运行错误 6，
  最终未修复 7）；
- 不触碰 `trusted/body/kernel/runtime/witness/evidence.py/autonomous_loop.py`，
  无网络、无服务/UAC/生命周期变更。

## 声明上限（Honest claim ceilings）

**已建立（established）**：
- 演示缺陷是**合成**的（刻意植入），但流程是**真实**的：真实文件、真实测试执行、
  真实补丁应用/回退、真实后果读取、可复跑证据；
- 循环能端到端驱动真实任务流并 consolidate 一条可复用 procedure；
- 失败候选回退后模块与原始内容**字节一致**；
- 策略门生效：life-core 作用域候选被拒绝、默认策略拒绝未授权的外部副作用。

**未主张（NOT claimed）**：
- 候选生成是**脚本化桩**（调用方提供补丁），引擎不生成候选；
- 不主张能修复真实生产缺陷（正确性完全由调用方 probe/candidate 决定）；
- 不主张 LLM 生成候选、零人工干预的候选设计（Sol 配额待定）、
  学习/自我进化、或生产策略变更。

## 设计决策（Design decisions）

1. **探测进程每次都清理 `__pycache__`**：Windows 上 pyc 有效性校验是
   “秒级 mtime + 文件大小”。若同一秒内连续写入大小相同的补丁文件，探测进程可能
   加载**过期字节码**而看不到真实补丁（实测曾导致结果不确定）。清理缓存保证探测
   编译的是被补丁后的源码；同时错误修复与正确修复的行长度也刻意不同，双保险。
2. **错误修复先行**：候选按“先错误、后正确”排序，演示完整覆盖
   rollback 与 commit 两条路径。
3. **单次 observe 驱动多个候选尝试**：同一失败观察可以依次被多个候选引用
   （`observation_ref`），符合引擎设计。
4. **提升重跑**：第一个通过的候选再跑一轮完整循环，使 pass count 达到阈值后再
   consolidate；若策略把 `promotion_passes_required` 设为 1，则首次 commit 后
   立即 consolidate。
5. **纯标准库、调用方所有**：`loop_integration.py` 只导入 `autonomous_loop` 与
   `_util` 辅助函数，AST 检查确认未导入 trusted/body/kernel/runtime/witness。
