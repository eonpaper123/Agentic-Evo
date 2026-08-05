# Slice F report — opencode second real coding-agent adapter

Date: 2026-08-05 (worktree `dev-genesis`, branch `agent/genesis`, base `0e93875`)
Scope: adapter mapping slice only (per design brief `slice_f_opencode_design.md`).

## 1. Confirmed opencode hooks schema (opencode 1.18.13)

Verified against the installed package and the pinned v1.18.13 source:

- `opencode --version` → `1.18.13` (npm global `opencode-ai@1.18.13` at `D:\rawle\Apps\npm-global\node_modules\opencode-ai`).
- **There is NO `hook` JSON config key in opencode 1.18.13.** `opencode.ai/config.json` (fetched 2026-08-05) has no `hook` property, and `packages/opencode/src/config/config.ts`@v1.18.13 contains no "hook" reference.
- The real hook surface is the **plugin event system** (`https://opencode.ai/docs/plugins/`): local JS/TS plugin files in `.opencode/plugins/` or `~/.config/opencode/plugins/` (or npm plugins via the `plugin` array) export functions keyed by event name; each receives the event payload as an in-process JSON object (camelCase fields).
- Confirmed event names + payload fields (from `packages/schema/src/v1/session.ts`, `packages/schema/src/session-status-event.ts` @v1.18.13):
  - `session.created` / `session.updated` / `session.deleted` → `{ sessionID, info: { id, directory, model:{id}, agent, title, ... } }`
  - `session.idle` → `{ sessionID }` (marked deprecated; superseded by `session.status`)
  - `session.status` → `{ sessionID, status: { type: "idle" | "busy" | "retry", message?, attempt?, next? } }`
  - `session.error` → `{ sessionID?, error: { name, data?: { message, ... } } }`
  - `session.diff` → `{ sessionID, diff: [...] }`; `session.compacted` (docs event list)
  - `message.updated` → `{ sessionID, info: { id, role, ... } }`; `message.removed` → `{ sessionID, messageID }`; `message.part.updated` → `{ sessionID, part, time }`; `message.part.removed` → `{ sessionID, messageID, partID }`; `message.part.delta` → `{ sessionID, messageID, partID, field, delta }`
  - `tool.execute.before` / `tool.execute.after` → input `{ tool, sessionID, callID? }`, output `{ args, ... }`
  - `permission.asked` / `permission.replied`, `command.executed` → `{ name, sessionID, arguments, messageID }`, `file.edited`, plus LSP/TUI/other events
- **Design-brief guessed names `session.start` / `session.end` / `notification` DO NOT exist in 1.18.13.** The adapter keeps `session.start` and `session.end` as strict aliases (`session.created` and `session.idle`/`session.deleted` respectively) so the brief's requested lifecycle (start→wake, end→sleep) is testable and old-hook-contract callers keep working; `notification` falls through to the generic `execution_surface_event` mapping. Real-schema events are the primary mappings and are unit-tested directly.

## 2. Files created / changed (worktree only; nothing staged/committed)

| Path | Change |
| --- | --- |
| `src/agentic_evo/adapters/opencode.py` | **new** — `handle_opencode_hook(home, payload)`, `EXECUTION_SURFACE="opencode"`; mirrors `codex.py` style (bounded_text≤256, sha256 hashes only, `_project_ref` sha256:, swallow `AgenticEvoError/KeyError/OSError/TimeoutError/TypeError/ValueError`→None) |
| `src/agentic_evo/cli.py` | **modified** — `hook` subcommand gains `--surface {codex,opencode}` (default `codex`, behavior unchanged); `_codex_hook` generalized to `_surface_hook(home, surface)`; import of `handle_opencode_hook` |
| `tests/test_opencode_adapter.py` | **new** — 12 unit tests on a genesis fixture home with a live witness service |
| `docs/release/adapter-contract.md` | **appended** — section 7: opencode adapter contract (execution_surface=opencode, confirmed event mapping table, opencode.json + plugin bridge snippet) |
| `opencode_adapter_report.md` | **new** — this report |

## 3. Mapping summary (opencode event → SurfaceClient)

- wake: `session.created` / `session.start` → `wake(execution_surface="opencode", session_id, project_environment, model)`; returns `hookSpecificOutput.additionalContext` (Root/Head/Generation/body files/activation), same contract as codex.
- sleep: `session.idle` / `session.deleted` / `session.end` / `session.status`(type=idle) → `sleep(...)`.
- observe (bounded/hashed, no raw content): `message.updated`(message_role/message_id/message_sha256), `message.part.updated`(part_type/part_chars/part_sha256), `message.part.delta`(delta_chars/delta_sha256), `tool.execute.before`→`tool_use_started`/`after`→`tool_use_finished`(tool_name/tool_input_sha256/tool_response_sha256), `permission.asked/replied`, `command.executed`, `file.edited`, `session.error`, `session.updated`, `session.diff`, `session.status`(busy/retry), `session.compacted`→`context_compaction_finished`; anything else → `execution_surface_event` with `unmapped_event_name`.
- Field tolerance: camelCase (`sessionID`/`callID`/`modelID`) and snake_case (`session_id`/`tool_call_id`/`model_id`) both accepted; project env from `directory`/`cwd`/`info.directory`.

## 4. Test results (repo runtime Python 3.12.13, `C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`)

- `python -m py_compile -S` on new modules: exit 0.
- `python -m unittest tests.test_opencode_adapter -v` → **12 tests, OK** (4.5s):
  session.start wake+context, confirmed `session.created` wake (camelCase), message/tool hash-only payloads (raw content absent from records), `session.end` → waiting, confirmed `session.idle` + `session.status`(idle) → sleep, surface-identity isolation, unknown event → `execution_surface_event`, `session.error` bounded observe, malformed payloads swallowed, observatory failure fail-open, unavailable surface fail-open, caller-owned import boundary (importing `adapters.opencode` does not import `agentic_evo.cli`).
- Regression: `python -m unittest tests.test_cli_lifecycle tests.test_genesis_cli tests.test_codex_adapter -v` → **40 tests, OK** (17.8s).
- CLI check: `agentic-evo hook --help` shows `--surface {codex,opencode}` with default `codex`.

## 5. Claim ceilings (honest)

- **Proven here:** opencode 1.18.13 event payloads map into the same lineage surface (wake/sleep/observe with `execution_surface="opencode"`), hash-not-copy discipline holds, fail-open behavior holds, CLI dispatch works, unit-tested on a real genesis home.
- **NOT claimed:** a full real-task opencode run with rerunnable evidence — the parent will run opencode in a scratch repo with the plugin bridge against a real Genesis home as the follow-up evidence step. Requirement 2 (second real agent integrated) is still not complete. No claim that the hook is installed in any opencode config.

## 6. Remaining risks / notes

1. The confirmed 1.18.13 schema uses in-process plugin events, not stdin-command hooks; the documented `opencode.json` snippet in `adapter-contract.md` bridges by forwarding event JSON to `agentic-evo hook --surface opencode` over stdin. The bridge plugin itself was NOT executed in this slice (no real opencode run).
2. `session.start`/`session.end` aliases match the pre-1.0 hook contract and the brief's tests; they will not fire from a real 1.18.13 plugin config (only `session.created`/`session.idle`/`session.deleted`/`session.status` will) — the real-schema paths are covered by dedicated tests.
3. `session.error` payload shape (`{name, data:{message}}`) is based on the v1.18.13 schema `Assistant.fields.error` union; if a plugin bridge forwards a different shape, the bounded `error_name`/`error_message_sha256` fields degrade gracefully (None) rather than failing.
4. `event_kind` strings are free-form at the surface (no allowlist); the chosen kinds (`session_start`, `message_updated`, `tool_use_started`, …) intentionally reuse the codex adapter vocabulary where the semantics match, so downstream queries see one lineage vocabulary.
5. No codex.py/trusted.py/body*/kernel/runtime/witness/evidence or existing tests were modified; stdlib only; no network except doc/schema verification.
