# P0/P1 evidence readiness

_Generated from `evidence-readiness.v1.json`; do not hand-edit._

- **Project:** `agentic-evo`
- **Scope:** `p0-p1-project-local-pre-genesis`
- **Baseline commit:** `936e9393352bc7f0e7521a9d53e1ca76abc77be1`
- **Overall status:** `not_ready`

## Claim ceiling

| Field | Value |
| --- | --- |
| `gate_a_complete` | `false` |
| `gate_b_outcome` | `not_established` |
| `native_security_verified` | `false` |
| `ready_to_install` | `false` |

## Provenance classes

| Class |
| --- |
| `synthetic_fixture` |
| `observed_local_test` |
| `agent_authored_decision` |
| `external_authorized_observation` |

## Lanes

| ID | Title | Owner role | Status | Authorization | Evidence classes |
| --- | --- | --- | --- | --- | --- |
| `L1` | contract/receipt | `p0-contract-implementer` | `planned` | `none` | `observed_local_test` |
|  | Scope: Project-local readiness rendering and local test-process receipt contract only. |  |  |  |  |
|  | Inputs: docs/engineering/evidence-readiness.v1.json<br>src/agentic_evo/test_result_receipts.py |  |  |  |  |
|  | Deliverables: strict readiness checker and generated Markdown<br>atomic local receipt runner and verifier |  |  |  |  |
|  | Exit criteria: [ ] Matrix/render checker, atomic receipt runner/verifier, and all L1 unit and negative tests pass.<br>[ ] No Gate B files changed. |  |  |  |  |
|  | Non-claims: A local receipt is only a local test-process outcome.<br>This lane does not establish Gate A, Gate B, native security, or installation readiness. |  |  |  |  |
| `L2` | provenance/namespace | `p1-evidence-custodian` | `planned` | `none` | `synthetic_fixture`, `observed_local_test` |
|  | Scope: Closed P0/P1 provenance classes and writer-owned project-relative namespaces. |  |  |  |  |
|  | Inputs: tests/fixtures/windows_gate_b/corpus.manifest.json<br>src/agentic_evo/evidence_provenance.py |  |  |  |  |
|  | Deliverables: closed class/root validators<br>synthetic fixture non-promotion and namespace-negative tests |  |  |  |  |
|  | Exit criteria: [ ] Closed classes and root checks pass.<br>[ ] Synthetic promotion, link/reparse, reserved-lab, collision, and tamper cases fail closed. |  |  |  |  |
|  | Non-claims: Synthetic fixture material cannot be promoted by a digest, lab binding, or passing parser test.<br>The generic P0/P1 verifier does not read or rewrite artifacts/labs. |  |  |  |  |
| `L3` | routing audit | `p1-routing-auditor` | `planned` | `none` | `agent_authored_decision`, `observed_local_test` |
|  | Scope: Explicit agent-authored route decisions with no provider invocation or configuration change. |  |  |  |  |
|  | Inputs: src/agentic_evo/model_routing_audit.py<br>approved observed-local receipt references when present |  |  |  |  |
|  | Deliverables: closed decision writer and verifier<br>policy digest and candidate-membership checks |  |  |  |  |
|  | Exit criteria: [ ] Decision writer/verifier binds candidate, policy digest, and optional local receipt references.<br>[ ] No raw prompt, credential, provider call, or effective-model claim is retained. |  |  |  |  |
|  | Non-claims: A declared route decision does not observe provider execution.<br>A route audit does not change provider or model configuration. |  |  |  |  |
| `L4` | P2 disposition | `p2-project-disposition-agent` | `stopped` | `Rawle_explicit` | `agent_authored_decision` |
|  | Scope: This repository's P2 disposition only; it is not a cross-repository policy or authorization grant. |  |  |  |  |
|  | Inputs: docs/engineering/p2-stop-decision.v1.json<br>L1-L3 readiness state |  |  |  |  |
|  | Deliverables: integrity-valid project-local stop/not_authorized record |  |  |  |  |
|  | Exit criteria: [ ] The P2 stop record validates and links L1-L3 state.<br>[ ] Any reopening has the listed project-local evidence and necessary explicit authorization. |  |  |  |  |
|  | Non-claims: The stop record is not consent, a schedule, a live-goal change, or a provider invocation.<br>The stop record does not authorize a Windows retained experiment. |  |  |  |  |

## Blocked by

- `L1`
- `L2`
- `L3`
- `Rawle_explicit authorization`

## Non-claims

- This matrix is project-local Pre-Genesis planning and does not establish readiness.
- Gate A remains incomplete and Gate B outcome remains not established.
- No native security verification, installation, provider-effective route, or external observation is claimed.
- No P0/P1 generated receipt or model-route record is committed by this matrix.
