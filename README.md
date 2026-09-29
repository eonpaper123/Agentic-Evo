# Agentic-Evo

Agentic-Evo is an experimental runtime for a **persistent, user-bound coding agent**. It connects work across sessions and coding-agent surfaces so that experience can influence future behavior instead of disappearing when a conversation ends.

The long-term research goal is autonomous agent development: learning from real work, changing capabilities and working methods, and eventually improving the learning process itself. **This is a research objective, not a claim that recursive self-improvement (RSI) has already been achieved.** Codex is one supported execution surface, not the agent's identity or the object of the research.

## What the runtime does

```text
Coding task → observation → persistent experience → future task
                    ↘ candidate change → outcome → lineage
```

- Keeps a user-bound identity and lineage separate from any one model, conversation, project, or coding-agent app.
- Runs managed coding sessions and records bounded observations and outcomes for later use.
- Provides mechanisms for memory, candidate development, and controlled successor changes without treating generated self-assessment as proof of improvement.
- Lets the user turn the runtime on or off. Removing the program preserves identity and history unless data removal is explicitly requested.

Agentic-Evo is not a model API, a Codex fork, or a promise that every session will be better than the last.

## Current state

The repository contains a versioned, user-level release path, a managed Codex launcher, an optional LingTai harness launcher, runtime and lineage storage, and experimental learning/development mechanisms. A Windows installation has been used for real coding tasks. That evidence does **not** establish autonomous RSI, durable capability gains, transparent integration with every coding agent, or a verified macOS/Linux runtime. The project remains experimental.

For implementation details, see the [release lifecycle code](src/agentic_evo/release_lifecycle.py) and [tests](tests/).

## Get started on Windows

Requirements: Python 3.12 or newer available as `python`, a working Codex CLI executable, and an existing project directory. The installer creates a new identity for an empty runtime home and asks for its host binding; keep that binding private. This is an experimental user-level installation, not a verified system-wide native service.

Build a versioned archive from this checkout, then extract it:

```powershell
python tools/build_release.py --version 0.1.0 --output-dir dist
Expand-Archive -Path .\dist\agentic-evo-0.1.0.zip -DestinationPath .\dist
Set-Location .\dist\agentic-evo-0.1.0
```

Install using paths resolved on **your** machine:

```powershell
$runtimeHome = Join-Path $env:LOCALAPPDATA 'Agentic-Evo\runtime-home'
$codexExecutable = (Get-Command codex -CommandType Application | Select-Object -First 1).Source
.\install.cmd --home $runtimeHome --codex-executable $codexExecutable
```

The installer reports the daily launcher path. From an existing project directory, run that `Evo Codex.cmd` launcher to start a managed coding session. An optional `Evo LingTai.cmd` launcher is created only when both `--lingtai-python` and `--lingtai-preset` are provided during installation; LingTai is a coding-agent harness, not a model endpoint.

The installed `agentic-evo.cmd` launcher provides `status`, `off`, `on`, `recover`, and `shutdown`. For upgrade and uninstall, use its `upgrade --artifact <new-release.pyz> --program-dir <installed-program-directory>` and `uninstall --program-dir <installed-program-directory>` commands. Uninstall retains identity data by default; `--purge-data` permanently removes it. The generated archive also contains `install.sh`, but macOS/Linux runtime operation has not yet been verified.

## Research and documentation

- [Product and research definition](docs/Agent_Runtime_Intelligence_最终产品与科研说明.md)
- [Development framework](docs/开发框架.md)
- [Research hypotheses](docs/research/跨模块科研假设矩阵.md)
- [Documentation index](docs/README.md)

Research documents describe hypotheses and intended architecture; code and observed behavior determine what is implemented today. Please do not commit credentials, private task data, raw recordings, or unbounded conversation logs.
