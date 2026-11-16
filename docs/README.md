# AFL Video Platform — documentation home

**Reviewed:** 16 November 2026. The repository's source and CloudFormation definitions are the definitive implementation evidence. The table below distinguishes **current operational contracts** from **dated audits and historical stage documents**. No document alone proves an AWS deployment is presently identical to GitHub; verify live deployed resources before a production cutover.

## Current documentation — read these first

| Document | Authority and purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Current backend-driven image → three sequential videos → assembly flow and data boundaries. |
| [API_ENDPOINTS.md](API_ENDPOINTS.md) | **All 20 current method/path combinations**, request guards, response shapes, internal event distinction, developer access. |
| [JOB_STATUSES.md](JOB_STATUSES.md) | Image, video, assembly, terminal and review statuses; why first FFmpeg failure is nonterminal. |
| [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md) | Definitive current retry, backoff, timeouts, failure disposition and unknown-Google-submission safety. |
| [INFRASTRUCTURE.md](INFRASTRUCTURE.md) | Source-defined AWS stacks, IAM, EventBridge, Lambda, S3/DDB, runtime inventory and live-verification gaps. |
| [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md) | Severity-ranked RA-01…RA-12 evidence, state of fixes and outstanding fault injection. |
| [RESILIENCY_ROADMAP.md](RESILIENCY_ROADMAP.md) | Open work, verification gates, implementation history. |
| [GOOGLE_OMNI_RELIABILITY_CHECKLIST.md](GOOGLE_OMNI_RELIABILITY_CHECKLIST.md) | Current provider-specific open controls; provider-wide limits and costly POST safety. |
| [DOCUMENTATION_QUALITY_REVIEW_2026-11-16.md](DOCUMENTATION_QUALITY_REVIEW_2026-11-16.md) | Evidence of document-vs-source corrections and remaining review limitations. |

**Canonical ownership:** Do not repeat endpoint definitions in the architecture or implement retries based on historical release notes. Update the relevant canonical document in the same pull request as any code change. Include a source citation path, test, deploy evidence when present, and confidence level.

## Current SVG diagrams (no Mermaid)

- [End-to-end AWS architecture](architecture-high-level.svg): API, parallel Google image generation, EventBridge sequencing, sequential scene videos and final assembly.
- [Generation, retries and failure outcomes](generation-and-retries.svg): confirmed image 429 retries, no blind video POST retry, read-only provider polling, manual scene retry and FFmpeg-only retry.
- [Per-job handoff watchdog](watchdog-architecture.svg): EventBridge start, 60-second checks, verified handoff replay, 2-hour limit and missed-event risk.
- [Historical UI mockup](mobile-ui-illustration.svg): explicitly labelled an old illustrative interface, **not** a representation of current production controls.

These SVGs are conceptual source documentation, **not** live AWS topology or fault-injection evidence. Update them alongside architecture/worker changes. All current operations documentation uses SVG rather than Mermaid.

## Historical plans, dated release evidence, or stage-specific references

These files retain their original purpose and **must not override the current contracts above**:

- **Early API and feature stages:** [API_STAGE1B.md](API_STAGE1B.md), [SCENE1_REPLICATE_PILOT.md](SCENE1_REPLICATE_PILOT.md), [PARALLEL_SCENES.md](PARALLEL_SCENES.md), [VIDEO_IMPLEMENTATION_PLAN.md](VIDEO_IMPLEMENTATION_PLAN.md), [VIDEO_PROMPTS.md](VIDEO_PROMPTS.md), [GOOGLE_OMNI_STAGE2_API_CONTRACT.md](GOOGLE_OMNI_STAGE2_API_CONTRACT.md), [GOOGLE_WIF.md](GOOGLE_WIF.md).
- **Deployment and legacy resource planning:** [BOOTSTRAP.md](BOOTSTRAP.md), [DEPLOYMENT.md](DEPLOYMENT.md), [DEPLOYMENT_BASELINE_2026-11-15.md](DEPLOYMENT_BASELINE_2026-11-15.md), [DEPLOYMENT_OWNERSHIP_AUDIT.md](DEPLOYMENT_OWNERSHIP_AUDIT.md), [PRODUCTION_PIPELINE_PLAN.md](PRODUCTION_PIPELINE_PLAN.md), [RESOURCE_CLEANUP.md](RESOURCE_CLEANUP.md).
- **Dated code audits and releases:** [CODE_REVIEW_2026-11-08.md](CODE_REVIEW_2026-11-08.md), [PAID_MP4_CODE_AUDIT_2026-11-08.md](PAID_MP4_CODE_AUDIT_2026-11-08.md), [BANANA_TEMPLATE_LOCK_RELEASE.md](BANANA_TEMPLATE_LOCK_RELEASE.md), [TEN_VIDEO_ATTEMPTS_RELEASE.md](TEN_VIDEO_ATTEMPTS_RELEASE.md), [VIDEO_SCENE_RETRY_RELEASE.md](VIDEO_SCENE_RETRY_RELEASE.md), [VIDEO_SCENE_RETRY_UI_RELEASE.md](VIDEO_SCENE_RETRY_UI_RELEASE.md).
- **Illustrations and screenshots:** [architecture-high-level.svg](architecture-high-level.svg), [watchdog-architecture.svg](watchdog-architecture.svg), [mobile-ui-illustration.svg](mobile-ui-illustration.svg), and `screenshots/`. Illustrations may represent an earlier stage and are *not* executable architecture or proof of live configuration.

## The safe interpretation of "working" and "complete"

- **SOURCE VERIFIED:** Actual repo API/worker/CloudFormation source inspected.
- **CI VERIFIED:** A specific offline test workflow passed. CI does not validate the provider's acceptance/billing state.
- **DEPLOYMENT VERIFIED:** Guarded release confirmed AWS code hashes/templates and/or published frontend objects at a specific time. This does **not** necessarily mean every later resource has no drift.
- **LIVE FAULT-INJECTION VERIFIED:** Explicitly tested named failure transitions in running AWS without unsafe duplicate paid calls. **Not yet established across every stage.**
- **PAYMENT READY:** Requires proven customer entitlement, transaction/refund state, retention and all blocking RA findings closed. **Not achieved**.

## Change control checklist

For every change touching `src/api/handler.py`, worker handlers, frontend status code or Step Functions: confirm whether to update the [API contract](API_ENDPOINTS.md), [status contract](JOB_STATUSES.md), [retry matrix](RETRIES_AND_TIMEOUTS.md), [architecture](ARCHITECTURE.md), [infrastructure inventory](INFRASTRUCTURE.md), and [resilience backlog](RESILIENCE_AUDIT_2026-11-16.md). Record exact tests and deployment status, and keep historical notes historical.

The docs contract checker `tests/test_documentation_contract.py` is intended to run in CI to flag drift in route inventory, waits, FFmpeg retry states, primary documentation references, and unmarked historical docs. It is a **static regression check**, not an API integration test.

## In-depth event-driven reference

**[EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md)** is the authoritative explanation of **why** the video progression is event-driven, exactly **which** `aws.states` events are consumed, what Lambda verifies before a handoff, how duplicate or lost events behave, how the watchdog replays eligible completion events, and why an application-level `NEEDS_REVIEW` return may not trigger EventBridge target retries.

**Three main engineering diagrams (SVG, no Mermaid):**

1. [System architecture](architecture-high-level.svg) — complete browser / API / three parallel images / three sequential videos / assembly boundaries.
2. [EventBridge handoffs and watchdog](event-driven-handoffs.svg) — event sources, DDB/S3 checks, duplicate deliveries, missed notifications and 2-hour monitoring limit.
3. [Failures and retry decisions](generation-and-retries.svg) — per-operation retries, paid POST uncertainty and exact FFmpeg terminal semantics.

A fourth [watchdog detail](watchdog-architecture.svg) remains as a focused supplemental drawing. The old [UI mockup](mobile-ui-illustration.svg) is historical.

**Source verification warning:** `infrastructure/app/google-video.yaml` currently has `Claim.requestId.$ = "$.Execution.Name"` while subsequent tasks use `"$$.Execution.Name"`. This source inconsistency must be compared with the **live** state machine before changing production; track as ED-01 / RA-10. The diagrams depict **intended checkpoint boundaries**, not proof every execution succeeds.


## Operations and incident recovery

**[FAILURE_RECOVERY_RUNBOOK.md](FAILURE_RECOVERY_RUNBOOK.md)** adds an evidence-first, read-only incident checklist: investigate DDB checkpoints, named Step Functions executions, S3 object existence and saved Google interaction IDs; classify safe retries versus provider-acceptance ambiguity; distinguish EventBridge delivery, Lambda asynchronous function retry and ordinary `NEEDS_REVIEW` business returns. The runbook does **not** authorise a new paid model POST or change AWS resources.

