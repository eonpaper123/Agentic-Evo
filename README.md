# Agentic-Evo

Private research repository for Agent Runtime Intelligence and the study of autonomous, continual Agent self-evolution.

The project is not a Codex plugin or a Codex-specific optimization system. The user is the only host; Codex is currently a research tool and one possible execution surface. The research target is a coding-agent-, model-, and project-independent Autonomous Agent Evolution System bound to one user lineage.

## Current installed release (3060 / RC9)

The current user-level release is RC9, installed through the official RC1 → RC2 → RC4 → RC5 → RC6 → RC7 → RC8 → RC9 upgrade path. The RC9 upgrade stopped the old Witness before replacing its payload, then recovered the new service while preserving the same Root, generation 2, Head, and verified lineage history. This closes the replacement-order defect observed during the RC8 transition.

| Item | Current value |
| --- | --- |
| Program | `D:\rawle\Apps\Agentic-Evo` |
| Retained data | `D:\rawle\AppData\Agentic-Evo` |
| Runtime home | `D:\rawle\Coding\Agentic-Evo\.agentic-evo\runtime-home` |
| Authority / generation | On / 2 |

`Evo Codex.cmd` has completed two real daily coding rounds on this installation. `Evo LingTai.cmd` has also completed one real RC6 documentation task through LingTai's native model-and-tool loop while loading the same Root, generation 2 Head, and bounded historical references; its task-end development opportunity received one host-loaded same-Root initial page and chose `no_change`. The transport supports Body-requested pagination, but this record does not show an additional page request. This proves the second harness can execute a real task and return its consequences to the lineage, not that the Body improved or that LingTai has passed long-term validation. RC9 includes lineage-fact projection, recallable `head_advanced` events, direct-parent `withdraw` semantics, runtime bounded-tail validation, bounded redacted tool output, explicit LingTai failure observations, artifact-derived upgrade versioning, and an LPAC-only executable development entrypoint. A D-drive fixture proved that an explicitly declared Body program can organize two recall pages and two same-thread fake-organ calls, including using the first result to determine the second input; a second fixture proved Off cancels its process tree and rejects a late result. No installed Head currently declares that entrypoint, and these fixtures do not prove autonomous adoption, improvement, or RSI gain. A withdrawal creates a new corrective successor from the direct parent; it does not roll back Head or delete history. Windows user-startup recovery is configured, but an actual Windows logon recovery has not yet been observed.

## Install a versioned release

Prerequisites:

- Python 3.12 or later is available as `python` on `PATH`.
- The chosen Codex executable is already signed in.
- Choose a runtime home and an absolute Codex executable path. A new or empty runtime home creates an identity and prompts for the required host binding.

Extract `agentic-evo-<version>.zip`, then run its Windows installer:

```powershell
Set-Location .\agentic-evo-<version>
.\install.cmd --home "D:\path\to\runtime-home" --codex-executable "D:\path\to\codex.cmd"
```

LingTai is an optional second coding-agent harness, not a model interface. Enable its daily launcher only by supplying both of its existing local paths at installation:

```powershell
.\install.cmd --home "D:\path\to\runtime-home" --codex-executable "D:\path\to\codex.cmd" --lingtai-python "D:\path\to\lingtai-python.exe" --lingtai-preset "D:\path\to\existing-preset.json"
```

Omit both LingTai arguments for a Codex-only installation. Do not supply only one.

## Daily coding and runtime controls

From an existing project directory on the installed 3060 machine, start the managed Codex session:

```powershell
Set-Location "D:\rawle\Coding\your-project"
& "D:\rawle\Apps\Agentic-Evo\Evo Codex.cmd"
```

The launcher displays the chosen project directory. If launched from the program directory or a non-directory, it asks for an existing project directory. `Evo LingTai.cmd` exists only when the optional LingTai pair was configured; one real RC6 documentation task has been verified on the 3060 installation.

The installed runtime launcher exposes the supported controls:

```powershell
$evoLauncher = "D:\rawle\Apps\Agentic-Evo\agentic-evo.cmd"
& $evoLauncher status
& $evoLauncher off
& $evoLauncher on
& $evoLauncher recover
& $evoLauncher shutdown
```

The installed launcher binds these five management commands to its configured runtime home. Explicit `--home` and `--dev-home` remain available and are never duplicated. `on` prompts for the host binding when it is not supplied. Use `recover --restart` only when a controlled replacement of the running Witness is required.

To upgrade an existing installation, use the new extracted release artifact and keep the installed program directory:

```powershell
$candidateArtifact = "D:\path\to\agentic-evo-<version>\agentic-evo.pyz"
& $evoLauncher upgrade --artifact $candidateArtifact --program-dir "D:\rawle\Apps\Agentic-Evo"
& $evoLauncher status
```

Uninstall removes program integration and startup registration while retaining identity/runtime data by default:

```powershell
& $evoLauncher uninstall --program-dir "D:\rawle\Apps\Agentic-Evo"
```

Add `--purge-data` only for an explicit, permanent removal of retained identity and runtime data.

## Evidence boundaries

This installation does not prove native Codex Desktop transparent integration, RSI or completed autonomous evolution, autonomous adoption of executable development, long-term LingTai reliability, or a macOS/Linux runtime run. The historical research and security checkpoints below remain valuable records, but they are snapshots rather than a description of this RC9 installation.

## Historical Pre-Genesis snapshot

The following paragraph and linked implementation report preserve an earlier engineering checkpoint; they do not describe the current lab installation.

**Pre-Genesis.** The portable `Trusted State + Body + Runtime + Witness process rehearsal + execution-surface adapters` slice exists and is tested, but it is not installed globally and no formal Root has been created. Two public execution-surface adapters now ship in this state: the Codex lifecycle adapter and the provider-neutral `surface-stdio` JSONL bridge. `surface-stdio` is caller-declared and surface-unverified; it proves a second public adapter exists, not that a second real coding-agent has been observed in a natural task. Windows foreground evidence covers Job Object fencing, an authenticated public named pipe, a suspended restricted Low-Integrity Body, an explicit private lineage transport, and an internal supervisor-stop cutoff/drain/restart joint. Surface sessions now use the real composite identity `(execution_surface, session_id)` from SQLite primary key through a versioned structural commitment, runtime status, public protocol v2, and Codex SessionEnd; two surfaces may retain the same raw ID without overwrite or cross-surface deletion. Old trusted v1 state and public v1 requests fail closed rather than being silently reinterpreted. A no-UAC builder also produces an exact-hash, Python-independent SCM-only probe whose console launch fails closed with Windows error 1063; tamper and junction cleanup are rejected. One separately authorized, externally pinned UAC configuration probe then created a random temporary SCM service, read back `SERVICE_SID_TYPE: RESTRICTED` and protected ACLs, never started the service, and removed the service plus both temporary trees; elevated and non-elevated cleanup checks both passed. A no-UAC independent verifier/real-attacker harness now exists: new receipts are the lab-bound `plan.v2`, `config-probe.v2`, and `result.v2` family, with `-LabId` mandatory. Frozen v1 receipts are compatibility-only and their lab binding is inconclusive; A07 is only a `same_principal_harness`. Cycle 3 has completed the PII-safe frozen v1/v2 corpus and public CLI/verifier regression. Separately, `agentic-evo.windows-gate-b-retained-preflight.v1` is implemented as a deterministic, plan-only, lab-bound pre-registration artifact for one retained temporary SCM service across exactly one restart. It creates no service, triggers no UAC or reboot, emits no evidence receipt or file, runs no verifier, attacker, or cleanup, and raises no Gate B conclusion; it is not `plan.v2`, `config-probe.v2`, or `result.v2` evidence. This remains only `configuration_probe_completed`: no reboot, running token, persistent installation, or real UAC/SCM proof occurred; Genesis was not requested and its count was not measured. Therefore `gate_a_complete=false`, `gate_b_outcome=not_established`, `native_security_verified=false`, and `ready_to_install=false`. The actual retained service/restart experiment requires fresh exact user authorization, followed by independent attacker-verifier checks of the running token, protected state, public/Body capability, crash recovery, and reversible uninstall; no Genesis and no permanent install.

See [实现状态：Pre-Genesis](docs/engineering/实现状态_Pre-Genesis.md).

## Document map

This README is a navigation aid, not an authority source. Resolve conflicts using the authority order in [GOAL.md](GOAL.md):

1. Real evidence: current code, tests, run results, and immutable raw records.
2. Implementation-state documents, checked against current runtime evidence; [实现状态：Pre-Genesis](docs/engineering/实现状态_Pre-Genesis.md) is a historical checkpoint.
3. Method, product, and planning documents.

Key references:

- [GOAL.md](GOAL.md) — v1.0 goal contract and the governing authority order.
- [实现状态：Pre-Genesis](docs/engineering/实现状态_Pre-Genesis.md) — historical verified implementation checkpoint.
- [开发框架：让 Agent 成为最终实验者](docs/开发框架.md) — method and development framework.
- [Agent Runtime Intelligence：最终产品与科研说明](docs/Agent_Runtime_Intelligence_最终产品与科研说明.md) — product and research framing.
- [工具与实验共生开发计划](docs/engineering/工具与实验共生开发计划.md) — implementation planning.
- [文档索引](docs/README.md) — broader document index.

Supporting references:

[最小可信边界与来源证明](docs/engineering/最小可信边界与来源证明.md)

[最小 Body 启动契约](docs/engineering/最小Body启动契约.md)

[候选试生与 Head 推进](docs/engineering/候选试生与Head推进.md)

[单一可信事务域](docs/engineering/单一可信事务域.md)

[Current Body 私有会话租约](docs/engineering/CurrentBody私有会话租约.md)

[机器 Witness 服务与 exact-Head 子进程演练](docs/engineering/机器Witness服务与exact-Head子进程演练.md)

[跨平台 CLI、Off 控制与零安装副作用计划](docs/engineering/跨平台CLI与Off控制演练.md)

[Windows 原生 Witness 边界](docs/engineering/Windows原生Witness边界.md)

[受限 Body 与私有谱系能力演练](docs/engineering/受限Body与私有谱系能力演练.md)

[跨模块科研假设矩阵](docs/research/跨模块科研假设矩阵.md)

[实验 001：机器级连续生命循环](experiments/001_机器级连续生命循环.md)

[记忆与能力形成：从保存过去到产生未来能力](docs/topics/记忆与能力形成.md)

[宿主耦合的内生驱动力与发展目标](docs/topics/宿主耦合的内生驱动力与发展目标.md)

[宿主耦合目标系统：研究问题与证伪纲要](docs/research/宿主耦合目标系统_研究问题与证伪纲要.md)

[自我进化：可进化自我与个体边界](docs/topics/自我进化.md)

[自我进化：研究问题与证伪纲要](docs/research/自我进化_研究问题与证伪纲要.md)

[自主实验学习：问题出生、因果对照与分布式实验生命](docs/topics/自主实验学习.md)

[自主实验学习：研究问题与证伪纲要](docs/research/自主实验学习_研究问题与证伪纲要.md)

[现实选择与谱系延续：后果、因果组织与同一宿主生命](docs/topics/现实选择与谱系延续.md)

[现实选择与谱系延续：研究问题与证伪纲要](docs/research/现实选择与谱系延续_研究问题与证伪纲要.md)

[《实践论》《矛盾论》对 Agentic-Evo 的理论启发](docs/research/实践与矛盾_对Agentic-Evo的理论启发.md)

[能力累积与迁移：身体、器官与未来因果空间](docs/topics/能力累积与迁移.md)

[能力累积与迁移：研究问题与证伪纲要](docs/research/能力累积与迁移_研究问题与证伪纲要.md)

[元进化：发展方式、有效可达空间与递归开放](docs/topics/元进化.md)

[元进化：研究问题与证伪纲要](docs/research/元进化_研究问题与证伪纲要.md)

For the final product form, scientific hypotheses, research framework, learning-intervention definition, Agent identity and lineage, and experimental standards, use the applicable documents above under the [GOAL.md](GOAL.md) authority order.

Raw recordings, private task data, credentials and unbounded conversation logs do not belong in this repository.
