# AFL Video Platform — Resiliency and Payment-Readiness Roadmap

> **CURRENT STATUS — 16 NOVEMBER 2026: NOT PAYMENT READY.** The latest ranked risk register is [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md), with complete source and documentation references in [README.md](README.md). Earlier dated findings and milestones below are historical where superseded.
>
> **RA-01 FIX DEPLOYED, LIVE FAILURE TEST OPEN:** The initial FFmpeg failure no longer advertises permanent `STITCHING_FAILED` while the one safe retry remains possible. The API checks the stable final Step Functions execution; `STITCHING/RETRYING` is nonterminal and a confirmed exhausted retry may be terminal. [Guarded API/UI release 43835732389](https://github.com/rorygleeson/afl-video-platform/actions/runs/43835732389) passed. **Fault injection of an actual first failure → success and second failure → permanent outcome is still required.**
>
> **17 NOV 2026 ITEM 2 UPDATE:** A bounded missed-start watchdog fallback is merged and AWS deployed ([successful guarded release](https://github.com/rorygleeson/afl-video-platform/actions/runs/44022615608)); live injected missed-event recovery remains untested. **High-priority remaining gaps:** watchdog >7200s lifetime, manual-retry re-monitoring, durable failure alerts/DLQ and scan coverage, ambiguous unacknowledged Google POST, conditional terminal fencing races, no customer-scoped access or long-lived payment ledger, absent `videos/` lifecycle and seven-day DDB job TTL, unverified delivery object and incomplete operator alerts. **Source/CI/deployment success is not end-to-end recovery proof.**
>
> **Next sequence:** (1) live/synthetic RA-01 fault testing; (2) durable missed-handoff/watchdog remediation; (3) concurrency safety and live ASL drift comparison; (4) provider uncertainty reconciliation; (5) secure paid-order recovery/retention; (6) media cleanup and observability. No blind new Google POSTs. Refer to RA-02…RA-12 acceptance tests.
>

**Status:** Active checklist — documentation/discovery first  
**Created:** 2026-11-15 (Australia/Melbourne)  
**Repository:** `rorygleeson/afl-video-platform` (`main`)  
**Scope:** Existing selfie → three Google reference images → three Google video scenes → FFmpeg final MP4. Stripe design only until explicitly authorised later.

## Current audit update — 2026-11-15 (post backend-orchestration releases)

**Scope and confidence:** **SOURCE VERIFIED** against current `main` API, browser, Step Functions/CloudFormation definitions, EventBridge rules and offline tests. **LIVE DEPLOYMENT VERIFIED (limited)** by guarded GitHub Actions AWS CloudFormation/S3 readback and resource checks. **NOT END-TO-END VERIFIED** with a fresh actual selfie/Google/FFmpeg job; no paid provider generation was run as part of these releases. This update supersedes older *current source findings* below where they conflict. Historical change-log entries remain historical.

### Implemented and deployed: three backend orchestration releases

| Stage | Current actual behaviour | Deployment evidence | Limit |
| --- | --- | --- | --- |
| **1 — Images → Video 1** | `POST /jobs` records `autoVideoConsent: true` from the frontend's explicit `confirmPaidSequence: true`; image SFN successful execution EventBridge event invokes API `image_sequence_event()`. Checks named execution/job, completed overall image job + three completed scenes, consent, then reuses `start_video_sequence()` and atomic video reservations. | [43689396745](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689396745): API/isolated EventBridge stack and frontend verified. | **No Stripe gate yet.** This is developer-mode Google-cost consent, not a customer payment. Failure to deliver event may leave an authorised job stalled. |
| **2 — Video 3 → stitching** | Existing successful Google SFN completion event invokes `video_sequence_event()`. Checks scene/attempt ownership, completed scene 3 MP4 and S3, then uses existing `start_assembly()`, which validates **all three** S3 MP4s and conditionally reserves assembly; named `final-{jobId}` execution. Duplicates do not initiate another assembly. | [43689754297](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689754297): API Lambda update, existing EventBridge rules verified; tests passed. | An uncertain assembly StartExecution leaves a reserved record requiring reconciliation; no assembly-only automated retry/watchdog. |
| **3 — browser display-only after job submission** | Frontend still presigns/uploads selfie, creates job and explicitly POSTs `/jobs/{jobId}/generate` once. After that, the browser **does not POST** `/real-videos/sequence` or `/assembly`. It reads image/video/assembly status, displays progress and final download, retains explicit scene-specific manual retry and free diagnostics. | [43690121205](https://github.com/rorygleeson/afl-video-platform/actions/runs/43690121205): frontend test suite passed, S3 byte readback and CloudFront invalidation verified. | Browser close *after starting image SFN* no longer blocks video progression or stitching, provided AWS events/workflows work. Browser close **before** image start still can interrupt setup. No same-job resume across browser/device yet. |

### Existing status API — preserved, not replaced

`GET /jobs/{jobId}` returns `job.progress` derived **read-only** from existing image, `realVideos` and assembly state: `currentStage` = `IMAGES`, `VIDEO_1`, `VIDEO_2`, `VIDEO_3`, `STITCHING`, `COMPLETED`; `stageStatus`; `imagesCompleted/imagesTotal`; `videosCompleted/videosTotal`; `finalVideoReady`. Legacy `job.status` **continues to mean the image-pipeline status**. The developer diagnostics panel polls this GET **every 2 seconds while open and visible**; normal image/video UI status polling is **5 seconds**, with **15-second** error backoff. The backend does not persist a new single mutable overall-stage field. Deployment evidence for API + diagnostics: [43678843432](https://github.com/rorygleeson/afl-video-platform/actions/runs/43678843432). Existing responses, stage names and compatibility **must remain intact**.

### Isolated missed-handoff watchdog — event-driven per-job migration, 2026-11-15

**LIVE DEPLOYMENT VERIFIED (CloudFormation/Lambda/SFN resource + exact source):** The original periodic watchdog was created in [run 43697800769](https://github.com/rorygleeson/afl-video-platform/actions/runs/43697800769) and **replaced** by per-job monitoring through the isolated stack update in [run 43700553301](https://github.com/rorygleeson/afl-video-platform/actions/runs/43700553301) (CloudFormation UPDATE_COMPLETE; its post-deployment direct EventBridge read failed because the GitHub role lacks `events:DescribeRule`). Subsequent source-and-stack verification **passed** in [run 43701311166](https://github.com/rorygleeson/afl-video-platform/actions/runs/43701311166): active Standard watchdog state machine, S3 artifact's `handler.py` byte-for-byte equal to GitHub source, deployed Lambda code hash equal to the **actual deployed** ZIP, and replacement rule identity verified via CloudFormation. The deployed template has **no ScheduleExpression**. No existing application stack was modified.

- **Start:** Image-generation Standard Step Functions `RUNNING` event → isolated EventBridge rule `afl-app-dev-handoff-watchdog-image-start` → isolated watchdog Lambda → named `watch-{jobId}` Standard Step Functions execution. A customer selfie upload by itself does **not** start monitoring; only a started image-generation execution does. Each job gets at most one named watchdog. Pre-activation jobs are still excluded.
- **During:** Step Functions waits 60 seconds, invokes the same watchdog Lambda with that **job ID**, and waits again only while the job is active. The handler uses **one consistent DynamoDB GetItem** rather than a table Scan. After verified image/video execution success is at least **120 seconds old**, it can replay a missing handoff event into the unchanged, atomically guarded API. `SUBMISSION_UNKNOWN` is monitored without retrying a paid request. Failures and ambiguous reservations require review, not resubmission.
- **Stop/cost:** Stops when final assembly is `COMPLETED`, image/video terminal failure requires review, or the maximum **2-hour** job window is reached. When there are no active jobs, this handoff watchdog does **zero periodic invocations and zero DynamoDB scans**. Per-job Standard SFN transitions and Lambda GetItem checks still incur small usage-dependent charges.
- **Isolation:** Own CloudFormation stack, Lambda, IAM, EventBridge start-event rule, log group and Standard SFN. No permission for Google API, S3 media mutation, DynamoDB writes, or starting paid Google state machines. Only the existing API can advance verified steps.
- **Limitations:** Image start events are delivered **best effort** by EventBridge. If that specific start notification is lost, there is no newly scheduled fallback to create the watchdog. An `APPROVED` sequence with no Video 1 reservation, an already reserved assembly with missing execution, and complex provider-unknown outcomes still need separate reconciliation. Live end-to-end missed-event injection and actual customer generation have **not** been tested as part of this migration.
- **Separate service:** The pre-existing **image-worker** reconcile schedule (`afl-app-dev-reconcile-images`, every **5 minutes**) was deliberately **not** changed; it is not the handoff watchdog and still runs even with no active jobs.

### Remaining high-priority resiliency gaps (verified source; not fixed by orchestration)

1. **Critical — Stripe/payment admission missing.** The frontend records `confirmPaidSequence:true` at job creation and starts image generation without a verified payment. Never expose this developer-mode flow to paying anonymous consumers without server-verified entitlement and a bounded free-test bypass.
2. **High — missed or unacknowledged AWS EventBridge handoff (partially mitigated by per-job watchdog).** Both image- and video-success rules use `MaximumEventAgeInSeconds:3600`, `MaximumRetryAttempts:24`; **no explicit DLQ** exists in these two rule templates ; the per-job watchdog only exists if its image-start event was delivered. An execution can finish with its next stage unstarted when no monitor exists. Add reliable event-start reconciliation if stronger guarantees are required, always checking named executions and durable S3/DynamoDB evidence before *any* new provider POST. In particular, distinguish `assembly:QUEUED` with absent `final-{jobId}` workflow from actual running assembly.
3. **High — no secure anonymous customer job recovery.** Current frontend saves `jobId` in `localStorage` and supports same-browser resume/reload with the developer API key; cross-device/customer capability-link recovery is **not** implemented. Developer `x-api-key` is not a customer entitlement.
4. **High — ambiguous paid Google submissions.** Video `SUBMISSION_UNKNOWN` and image network/provider acceptance uncertainty must be reconciled using recorded IDs/outputs; **do not blindly retry billable POSTs**. Manual video retry (up to 10 attempts) remains available only after confirmed terminal failure and guarded previous execution.
5. **High — failure of FFmpeg itself.** Stitching now **starts on AWS**, but assembly Lambda/SFN still has **no bounded retry/Catch or assembly-only reconciler**. Retain completed scene MP4s and recover stitching without re-generating videos.
6. **Medium — signed sharing URLs and retention.** Final `url`/`downloadUrl` are **300-second presigned S3 GETs**, not durable email links. Authenticated GET can issue new ones. Seven-day job TTL/object retention must be verified; a future expiring capability-link endpoint must enforce access and retention.
7. **Medium — live e2e/monitoring proof missing.** Deployment/test success demonstrates source+AWS resource installation, **not** a complete unattended generation, missed-event recovery, multiclient admission, provider billing or delivery SLO. Build offline fault-injection and one explicitly authorised live full-run validation.

### Updated acceptance test for orchestration

Given an authorised job, after `POST /jobs/{jobId}/generate` succeeds the user may close the browser. AWS must complete three images (Map `MaxConcurrency:3`), start original Video 1 once, advance Video 2 then 3 only after verified successful MP4 checkpoints, start `final-{jobId}` assembly once and expose `job.progress.finalVideoReady:true` after the final file is available. Repeated/disordered events, slow S3, workflow missing, Google terminal failure and ambiguous submission must not result in a duplicate paid Google submission. **This full test has not yet been run live.**

## Ground rules

- Work through numbered tasks in order; update this checklist with dates, evidence, test outcomes, and commit links as tasks finish.
- **No application code, prompts, model changes, AWS deployments, or Stripe integration during Step 1 audit.** Documentation-only commits are allowed.
- Read the **actual current code** and, where authorised/accessible, compare with **live deployed AWS**. Mark findings as **SOURCE VERIFIED**, **LIVE VERIFIED**, **UNVERIFIED**, or **PROPOSED**. A CloudFormation value is *not* proof of a deployed value.
- Preserve current appearance, diagnostics, scene retries, team assets, prompts, Google models, and S3 output conventions.
- No paid Google calls in discovery or fault testing. Prefer mocks and offline tests. Never retry an ambiguous billable POST.
- Each implementation change should have minimal scope, tests, deployment preview, explicit approval when applicable, and post-deployment evidence.
- A job must complete or reach a **reconciled** terminal failure before it becomes eligible for an automatic refund.

## Step 1 — Documentation and baseline audit (FIRST; no application changes)

- [ ] **1.1 Snapshot source and deployment:** record audited main commit SHA, current GitHub workflows and last deployed artifacts; establish accessible live AWS evidence (Lambda configuration/code SHA, Step Functions definitions, API Gateway, DynamoDB, S3, EventBridge, CloudWatch, CloudFormation parameters/drift). Do not guess if inaccessible.
- [ ] **1.2 Update `README.md` and `docs/ARCHITECTURE.md`:** correct current direct Google image implementation, image `MaxConcurrency: 3`, server-side video sequencing, actual stitching, WIF, data flow, diagram, and obsolete Replicate/parallel references.
- [ ] **1.3 Update `docs/API_ENDPOINTS.md`:** inventory *every* implemented HTTP endpoint and developer-only/legacy endpoint; method, path, purpose, caller, Lambda integration, auth, idempotency, request/response examples, timeout, success/error codes, synchrony, signed URLs, and CORS. Reconcile with API Gateway deployment.
- [ ] **1.4 Create `docs/JOB_STATUSES.md`:** complete job/image/video/sequence/assembly state dictionary; legal transitions, source of truth, error codes, retries, timestamps, and what the UI actually displays. Document `GET /jobs/{jobId}`, `/images`, `/videos`, `/videos/{scene}`, and `/assembly`. Distinguish **existing behavior** from a **proposed unified overall status**; do not claim it exists.
- [ ] **1.5 Create `docs/RETRIES_AND_TIMEOUTS.md`:** actual source and deployed Lambda timeout/memory/concurrency, Step Functions waits/retries/backoff/jitter/max attempts, provider HTTP socket timeout, frontend polling/upload retries, SDK retry defaults, EventBridge delivery/retries, workflow deadlines. Include matrix for upload, images 1–3, videos 1–3, Google polling, S3, DynamoDB, assembly. Check nested retry multiplication and Google current published guidance.
- [ ] **1.6 Create `docs/INFRASTRUCTURE.md`:** CloudFormation stacks, Lambdas/handlers/dependencies/env var *names* (never secret values), state-machine state graphs, DynamoDB keys/TTL and stage fields, S3 prefixes/lifecycle/access/signed URL expiry, EventBridge, GitHub Actions OIDC/release and CloudWatch logs/alarms.
- [ ] **1.7 Create `docs/FAILURE_RECOVERY.md`:** known failure modes, retryable/permanent/ambiguous taxonomy, conditional writes, immutable stage checkpoints, duplicate request guards, lost AWS updates, unknown Google POST results, stalled jobs, browser loss, recovery/resume guarantees and gaps.
- [ ] **1.8 Review existing `docs/DEPLOYMENT.md` and all related older GitHub docs:** update authoritative docs; clearly label obsolete plans/historical docs. Verify source examples, avoid leaking keys, tokens, customer photos, or provider payloads.
- [ ] **1.9 Publish baseline findings:** ranked Critical/High/Medium/Low risks with evidence file/line or deployed resource, customer impact, cost impact, smallest fix, test requirements and acceptance criteria. Explicitly report what cannot be live-verified.
- [ ] **1.10 Documentation review gate:** check consistency across docs, code and deployed resources; don't begin code changes until audit complete and priorities agreed.

## Step 2 — Backend status and recoverability

- [ ] **2.1 Unified job status contract:** propose and implement *after Step 1* backend-owned current stage (`UPLOAD`, `IMAGE_1..3`, `VIDEO_1..3`, `ASSEMBLY`, `DELIVERED`), operation (`QUEUED`, `SUBMITTING`, `WAITING_FOR_GOOGLE`, `RETRY_WAIT`, `RECONCILING`, etc.), stage/attempt timestamps, remaining actions, `requiresAttention`, and download readiness. Retain existing endpoint compatibility. Never claim stage-count progress is elapsed-time accuracy.
- [ ] **2.2 Durable checkpoints and resumability:** audit image/video/S3 completion commits; do not regenerate completed reference images or clips; ensure interrupted workers can recover last completed stage.
- [ ] **2.3 Idempotency and race control:** verify upload/job creation, start-image, sequence start, per-scene retry, completion events, and assembly; conditional writes and stable execution names; handle duplicate EventBridge events and multiple concurrent customers.
- [ ] **2.4 Google ambiguous submission reconciliation:** guard unknown image POST and video `SUBMISSION_UNKNOWN` outcomes; reconcile provider operation IDs and output before any new model call; define safe escalation if provider cannot be queried.
- [ ] **2.5 Automatic transient recovery:** classified 429/5xx/network/provider failure rules, bounded exponential backoff + jitter, no nested retry explosions or indefinite waits. Preserve developer retry controls and never blindly retry unknown paid submissions.
- [ ] **2.6 Server-side final stitching trigger:** **Trigger deployed** (run 43689754297), from verified video 3 completion, independent of browser. **Still open:** bounded FFmpeg/S3/DynamoDB retry, lost-handoff recovery and live end-to-end verification. Retain all three completed clips.
- [ ] **2.7 Persistent watchdog and stalled-job reconciliation:** recover or explicitly mark cases needing review without false failures/refunds. Define job-wide deadline and cost limits.
- [ ] **2.8 Anonymous job recovery and security:** future-ready unguessable per-job capability token, hashed server-side, expiry/revocation, rate limits, safe cross-device/email option, and restricted media access. Developer `x-api-key` remains testing-only, not a consumer credential.
- [ ] **2.9 Frontend integration:** **Frontend-only backend-owned orchestration deployed** (run 43690121205), with progress bars, retry controls and developer diagnostics retained. **Still open:** secure same-job restore after refresh/close/device change and consumer-safe diagnostics.

## Step 3 — Controlled failure tests and observability

- [ ] **3.1 Build deterministic mock/fault-injection harness** for provider requests and AWS SDK operations, without real Google cost.
- [ ] **3.2 Test:** Google 429/500/502/503/504, network timeout, stuck/failed/unexpected provider operation, unknown accepted submission, provider completion followed by lost AWS status, Lambda timeout, Step Functions failure, failed S3 upload/download, failed DynamoDB write, duplicate generation, overlapping retry, multiple customers, job exceeding deadline.
- [ ] **3.3 Test browser refresh/close:** workflow continues and authorised customer can resume same job; no new chargeable generation from refresh, polling or download.
- [ ] **3.4 Test partial success:** video 2 fails after video 1 succeeds; recover without repeating images/video 1. Stitching failure must preserve all three video clips.
- [ ] **3.5 End-to-end trace:** job ID and execution/Google IDs; attempt counts, timestamps, retry classifications, billing-relevant events and S3 output paths; redact sensitive data and secrets.
- [ ] **3.6 CloudWatch monitoring:** alarms for stuck/failed jobs, unusual retry rates, Step Functions and Lambda errors, costs and delivery SLO; test alarms where practical.
- [ ] **3.7 Publish `docs/TEST_RESULTS.md`:** exact code/deployment revisions, tests executed, pass/fail, untested cases, real Google calls and costs if explicitly authorised, residual risks. No production-readiness claim without evidence.

## Step 4 — Stripe, anonymous ordering and refund DESIGN ONLY (NO integration)

- [ ] **4.1 Create `docs/STRIPE_REFUNDS_DESIGN.md`:** A$5 Stripe Embedded Checkout after selfie upload; Apple Pay, Google Pay, cards; no mandatory account, subscription or credits; Stripe handles card details.
- [ ] **4.2 Define payment/job/refund records and state machines:** one verified paid Checkout Session/PaymentIntent authorises exactly one video job, separate states for payment, generation and refund, conditional locks and idempotency.
- [ ] **4.3 Webhook-only server verification and duplicate protection:** authentic signed events, repeated/out-of-order events, redirects lost, repeated checkout clicks, pending/failed/authentication-incomplete charges and disputes; preserve authorised developer `x-api-key` bypass.
- [ ] **4.4 Refund eligibility/reconciliation:** only after bounded recovery is truly exhausted and unresolved Google operations/AWS writes checked; full A$5 refund to original method; separate `REQUESTED`, `PENDING`, `SUCCEEDED`, `FAILED`, `CANCELLED` states where Stripe supports them; idempotent API requests and asynchronous webhook reconciliation; block restart after final refund.
- [ ] **4.5 Customer messaging and recovery:** customer-friendly retry, delay, failure and accepted-refund messages; refund settlement is variable; expiring secure recovery URL and optional email; no raw provider errors exposed.
- [ ] **4.6 Policy and financial review:** double charge, cancellation, dissatisfaction, missing/expired download, chargeback and Australian Consumer Law; verify current Stripe fees and Google model costs, refund fee handling, failed-generation costs, gross margin and dispute exposure from published sources.
- [ ] **4.7 Test plan only:** Stripe test mode + mocked provider/AWS, including all 14 refund test cases; no live Stripe resources, charges, webhook endpoints or deployments until separately approved.

## Current verified source findings (NOT live-AWS claims)

- Direct Google image model `gemini-3-pro-image`; Step Functions Map `MaxConcurrency: 3`; image Lambda 600s; Google image HTTP socket 240s; explicit 429 only: maximum 8 attempts within 600s retry window.
- Google video model `gemini-omni-1.2-flash`; Google Lambda configured 300s; Step Functions 3600s; wait/poll 20s; PollVideo TaskFailed retry 3 times (10s base, ×2). No automatic paid Submit retries.
- Video sequence progresses through AWS EventBridge on successful completed scene; eligible manual scene retry max 10 attempts.
- Assembly Lambda configured 600s, and assembly workflow contains no automatic Retry. **Updated:** AWS now initiates assembly on verified Scene 3 success via EventBridge; frontend no longer requests assembly.
- Frontend image/video polling 5s; on poll error 15s. **Separate** developer overall-job-status polling 2s while expanded and visible. Frontend stores current job ID in runtime memory and does not yet implement anonymous secure recovery.
- Image worker retains completed images on error but its recovery may terminalise unfinished stages. The image watchdog runs every 5 min and marks long-stalled jobs failed.
- Existing `docs/ARCHITECTURE.md`, `docs/API_ENDPOINTS.md`, `docs/DEPLOYMENT.md` and some stale API strings include obsolete parallel/Replicate/assembly claims.

## Documentation review findings and evidence

- **Code paths traced:** `frontend/app.js` creates the job with `confirmPaidSequence:true`, invokes `/generate` once, then uses read-only video and assembly GETs. `src/api/handler.py` handles `image_sequence_event()`, `video_sequence_event()`, `start_video_sequence()`, `start_real_scene()` and `start_assembly()`. The event rules live in `infrastructure/app/{image-video-start-events,video-sequence-events}.yaml`. The worker/SFN model code and retry policies were not modified during orchestration releases.
- **Tests/deployment evidence:** Stage 1 [43689396745](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689396745); Stage 2 [43689754297](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689754297); Stage 3 [43690121205](https://github.com/rorygleeson/afl-video-platform/actions/runs/43690121205), which reported **0 frontend test failures**, verified all three published UI files by S3 readback and submitted CloudFront invalidation. These do **not** replace a billable end-to-end test or a failure-injected real AWS event-delivery test.
- **Document cross-check:** [ARCHITECTURE.md](ARCHITECTURE.md), [JOB_STATUSES.md](JOB_STATUSES.md), [API_ENDPOINTS.md](API_ENDPOINTS.md), [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md), and [INFRASTRUCTURE.md](INFRASTRUCTURE.md) updated for deployed orchestration. Earlier entries below are chronological records of earlier observations and may describe then-current browser-triggered stitching.
- **Documentation gap:** `docs/FAILURE_RECOVERY.md` does **not yet exist** in current repository; roadmap task 1.7 remains open. This roadmap's risk register is an interim summary, not a substitute for a full fault-by-fault operational runbook.
- **Scope of this review:** Documentation commits only. No prompts, reference images, customer assets, worker code, infrastructure resources, payment integration or Google API requests have been changed or triggered.

## Change log

- 2026-11-15 — **Source audit refreshed after three separately verified AWS deployments:** Stage 1 (image-success → Video 1), Stage 2 (Scene 3 → assembly), Stage 3 (frontend read-only for video/stitching). Preserved `job.progress` and existing retry controls. Identified outstanding missed-event reconciliation, ambiguous provider submissions, FFmpeg-only recovery, anonymous job access, Stripe payment gate and live end-to-end testing. See top of this document for evidence and priorities.

- 2026-11-15 — **LIVE VERIFIED**: overall job progress API + 2-second Developer diagnostics UI deployed through guarded release workflow run [43678843432](https://github.com/rorygleeson/afl-video-platform/actions/runs/43678843432), commit `f37b4aaf`. 17 API unit tests and 60 frontend regression tests passed; CloudFormation preview changed `ApiLambda` Code and verified dynamic `ProxyAny` dependency only. AWS API Lambda CodeSha256 matched, image worker and image state-machine definition unchanged; S3 readback matched published `app.js` and `index.html`; CloudFront invalidation submitted `I60QG5W70WKQYCE1F8XYRDXPB2`. No billable Google tests performed; in-browser live request using an actual job ID not yet observed.

- 2026-11-15 — Added **developer diagnostics-only** overall job stage line (frontend `index.html` and `app.js`), using read-only `GET /jobs/{jobId}` every 2 seconds while panel open/tab visible, with request single-flight and pause on tab hide. Commits `75c02280`, `578717d8`. No changes to image/video/frontend generation polling or orchestration. Feature requires deployment alongside API progress handler; **not yet verified live**.

- 2026-11-15 — First bounded **API-only implementation** after user-approved scope: add read-only `job.progress` to `GET /jobs/{jobId}` from existing scene/video/assembly fields, preserving existing `job.status`. Code changes limited to `src/api/handler.py` (latest feature commit `00ec4239`), plus `tests/test_overall_job_progress.py` with 16 offline cases (commit `15224169`). **No AWS deployment, generation changes, database schema or frontend changes.** Tests committed but execution not verified. Backend-controlled transitions are the explicitly separate *next* change.


- 2026-11-15 — Image concurrency restored to **3** in source `infrastructure/app/backend.yaml` (`MaxConcurrency: 3`), with a guarded deployment-intent commit `5ec4cb8f`. Updated architecture, infrastructure, API, status and retry docs to match; per-scene 429 retries are unchanged. Source verified; independently checking live deployed state is still open.

- 2026-11-15 — Updated [ARCHITECTURE.md](ARCHITECTURE.md) to current sequential image Map, direct Google model, server-side per-scene video orchestration, frontend-triggered assembly, detailed state/data flow (commit `e0829cd7`). Task 1.2 source documentation drafted; live comparison pending.
- 2026-11-15 — Created [INFRASTRUCTURE.md](INFRASTRUCTURE.md) with CloudFormation stacks, five Lambda configurations and env variable names, state diagrams, DDB record fields, S3 lifecycle, API Gateway, CloudFront, EventBridge and release/monitoring gaps (commit `139d6ec5`). Task 1.6 source documentation drafted; live and legacy mock stack verification pending.

- 2026-11-15 — Replaced [API_ENDPOINTS.md](API_ENDPOINTS.md) with source-verified 19-route inventory, request/response samples, auth, integrations, async and idempotency notes (commit `dcbe8d5d`). Step 1.3 documentation drafted; live/API contract tests remain.
- 2026-11-15 — Created [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md) with per-stage source-configured timeouts, retry/backoff, AWS worker and SFN matrix, failure handling and unexecuted test list (commit `e98748f6`). Step 1.5 drafted; published Google guidance and live cross-check remain.

- 2026-11-15 — Added [JOB_STATUSES.md](JOB_STATUSES.md) (commit `48e506a3`) documenting existing API states, response fields, limitations and a clearly labelled proposed unified status. Task 1.4 remains pending deployment cross-check and response tests.
- 2026-11-15 — Decision: proceed with source-verified documentation and do not block documentation work on live AWS access. Deployed resource comparison is a later audit cross-check, not a prerequisite to drafting current endpoint/status/timeout docs.

- 2026-11-15 — Step 1.1 source baseline captured in [DEPLOYMENT_BASELINE_2026-11-15.md](DEPLOYMENT_BASELINE_2026-11-15.md) (commit `892bf629`). **IN PROGRESS:** live AWS config and workflow evidence not yet accessible/verified. No application changes.

- 2026-11-15 — Roadmap created. Checklist tasks are **not yet marked complete**; current source findings are preliminary and live AWS values remain unverified.


## 2026-11-16 source audit and cleanup tracking

See [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md) for the current source-based audit, eight tracked resilience findings R1–R8, fault-injection test matrix, and job-aware S3 cleanup proposal. **Audit only:** no AWS changes, deletion rules, Stripe integration, or paid Google calls were made. Critical pre-payment gates: queued-but-not-started assembly recovery; missed watchdog-start event; safe handling of unknown provider submissions; consumer job restoration; and source-versus-live verification. Existing S3 Lifecycle covers uploads/results but not videos. Do not claim production readiness without the listed tests.

## Item 1 implementation checkpoint — 2026-11-16

**SOURCE IMPLEMENTATION MERGED, LIVE NOT DEPLOYED.** GitHub [PR #83](https://github.com/rorygleeson/afl-video-platform/pull/83) was merged to main at commit `491f07a8d715341e67837c2bcaed8a9fdcc8c1db`. It adds named queued-final execution reconciliation, one confirmed-failure-only FFmpeg retry after a 15-second wait, watchdog reconciliation for aged queued jobs, detection of failed/timed-out processing workflows, and no-retry guards for ambiguous processing/completed states. It preserves all three original Google scene MP4s. No Stripe or S3 cleanup changes.

**Offline test evidence:** [GitHub Actions run 43774298515](https://github.com/rorygleeson/afl-video-platform/actions/runs/43774298515) ran 323 tests. All newly added assembly retry and recovery tests passed; 5 failures and 38 errors remain in unrelated pre-existing tests, versus 6 failures/38 errors on earlier main baseline [43771213042](https://github.com/rorygleeson/afl-video-platform/actions/runs/43771213042). No newly introduced test failure remained on the final branch. This is **NOT** a globally green release.

**Item 1 remains deployment-gated:** verify live baseline and CFN drift; deploy API Lambda, FFmpeg worker Lambda, assembly Step Functions, and watchdog Lambda+IAM/environment in that order; complete AWS synthetic fault injection/rollback validation. Existing workflows do not automatically deploy the coordinated change from this source-only merge. A PROCESSING Lambda timeout is conservatively review-required, not automatically resubmitted. Only mark Item 1 operationally COMPLETED after live evidence. See [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md).

## Item 1 — AWS deployment result (2026-11-16)

**DEPLOYED AND VERIFIED (CloudFormation + Lambda/State Machine readback):** [GitHub Actions run 43775514956](https://github.com/rorygleeson/afl-video-platform/actions/runs/43775514956) finished **success** with `DEPLOYED_AND_VERIFIED api`, `DEPLOYED_AND_VERIFIED assembly-worker`, and `DEPLOYED_AND_VERIFIED watchdog`. The assembly-worker stack update also changed and verified the `afl-app-dev-assembly` Standard Step Functions retry definition. All change sets were reviewed before execution; no other application stacks or Google paid generation were invoked. `ap-southeast-2`, deployment source `ec996bdf31edc2bfacf5027e8c256322365add87`.

**Item 1 operational status:** Source code and guarded infrastructure deployment completed. Offline targeted recovery tests passed. A live *fault-injection* proof (deliberately timeout/kill FFmpeg, event loss, recovery of an existing completed three-scene fixture) has not been performed. Therefore mark **DEPLOYED / LIVE RESOURCE VERIFIED; LIVE FAILURE RECOVERY UNTESTED**, rather than asserting full end-to-end reliability. Keep video generation and Stripe unchanged. The old unrelated 5 test failures/38 errors remain a separate test-suite maintenance task.


## Universal terminal-outcome milestone — 16 November 2026

**Implemented in repository:** common backend status projection recognises
finalised image failure/partial success, Google policy block, confirmed Google
video failure after ten attempts, and persisted FFmpeg assembly failure as
`terminal=true, outcome=FAILED`, with stage, safe `failureCode` and
customer-facing message. Final assembled MP4 is
`terminal=true, outcome=COMPLETED`. The frontend presents the same clean
termination screen after refresh. Existing APIs reject new work after a
definitive failure; uncertain failures remain nonterminal. Existing DynamoDB
stage records serve as authoritative durable evidence; there is **no new
jobLifecycle field or write**.

**Still open / not payment-ready:**
- Reconcile stuck/uncertain Step Functions submissions and EventBridge handoffs
  without duplicate paid Google POSTs.
- Prove live end-to-end permanent failure and stitching outcomes using
  observed provider results; simulated tests alone are not live proof.
- Decide whether stitching failures should gain a bounded, explicitly safe
  recovery path (currently one-shot and treated as final).
- Create payment-verified order/entitlement and durable Stripe ledger, independent
  of the current seven-day job TTL.
- Add secure, no-login per-job customer recovery; job ID and developer API key
  are not sufficient customer authorisation.
- Formalise cancellation and expiry semantics. No production `CANCELLED`
  status is currently implemented; never invent it from a timeout.

**Status:** Universal definitive failure projection implemented, payment
integration remains out of scope. Do not mistake this for indefinite-unknown
reconciliation or full payment readiness.


## Handoff latency improvement — 16 November 2026

Eliminated fixed AWS Step Functions submission waits for Scene 2 (10s) and Scene 3 (20s), while retaining backend EventBridge-only advancement, verified predecessor MP4 checks, conditional DynamoDB admission and no automatic Google POST retries. New scene workflows start their `Submit` task directly after `PaidGate/Claim`. This is not a change to read-only 20-second Google status polling, provider rate limits, or failure/reconciliation semantics. Measure actual completion-event-to-Submit latency and Google 429 rates in real test jobs; no automatic duplicate submission is permitted.


### RA-01 FFmpeg status reconciliation — 16 November 2026

**Implementation recorded:** On first worker `assembly.FAILED`, while the existing named SFN is RUNNING during its 15-second wait/one confirmed retry, backend reports `STITCHING/RETRYING`, `terminal=false`. Only a durably persisted second failed attempt (`retryCount=1`) and confirmed SFN `FAILED` make `STITCHING_FAILED` terminal. Unknown or inconsistent execution stays `REVIEW_REQUIRED` without refund eligibility. Customer sees a final-video retry message; successful retry still delivers the MP4. No changes to Google, Step Functions orchestration, or FFmpeg worker. CI/deployment evidence and controlled fault injection are required before marking end-to-end complete. Other RA-02…RA-12 resilience gaps remain open.


**RA-01 release update (16 November 2026):** Guarded AWS API/UI release [43835732389](https://github.com/rorygleeson/afl-video-platform/actions/runs/43835732389) passed offline tests and verified deployment. Premature `STITCHING_FAILED` during first attempt retry is fixed in deployed projection. No Google/FFmpeg/ASL modifications. **Live failure injection remains outstanding**; other audit findings remain open.

## Deep event-driven source audit — new priority checks (16 November 2026)

Source-verifiable tasks to complete **before paid rollout**: (a) independently read live Google ASL and reconcile the `Claim.requestId.$` path discrepancy (ED-01 / RA-10); (b) preserve/alert application-level `NEEDS_REVIEW` handoff outcomes instead of assuming EventBridge target RetryPolicy will re-invoke Lambda (ED-02); (c) missed watchdog start, 7200s cutoff and no DLQ handling (ED-03); (d) concurrency terminal fences on paid claim (ED-04); (e) unknown provider acceptance (ED-05); (f) final MP4 delivery/retention (ED-06). See [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md) and its three detailed SVG diagrams for exact evidence and acceptance criteria.

**Documentation-only:** new source findings do not imply a live incident or an AWS change. RA-01 FFmpeg status code remains deployment-verified with live fault injection outstanding; RA-02…RA-12 stay open.


## Minor priority — API polling and quota UX (17 November 2026)

**Priority: LOW / post-critical resilience work. Status: OPEN.** During a three-scene job, final FFmpeg assembly finished successfully and the MP4 remained downloadable, but the developer API Gateway usage plan had **0 requests remaining** against its previous 10,000/month quota. The browser displayed `Limit Exceeded`, appeared stuck at estimated 94% stitching, and could not retrieve existing job status on refresh. Increasing the quota to 100,000/month restored access and the final video was downloaded successfully. This was an API status-access incident, **not** an FFmpeg failure; it does not prove live FFmpeg fault recovery.

- Reduce redundant/overlapping frontend GET polling (job, images, real-videos, assembly and expanded developer diagnostics), especially when idle, backgrounded, completed, or reconnecting. Keep backend-driven generation independent of polling and never issue billable Google POSTs from a status GET.
- Handle API Gateway 429 / `Limit Exceeded` explicitly: preserve saved job ID, show a clear temporary status-access/quota message instead of implying generation failure or frozen 94% stitching, and back off retries safely.
- Test quota exhaustion → browser refresh → quota restoration → same-job recovery and final MP4 download, without a new generation request; separately validate API throttling versus quota exhaustion.
- Keep this **minor priority** behind watchdog/handoff resilience, duplicate-payment prevention, durable recovery, and other critical pre-Stripe gates. Current 100,000/month quota is a temporary development setting, not a substitute for sensible polling.


## Item 2 — watchdog / event-handoff careful audit checkpoint (17 November 2026)

**Status: SOURCE AUDIT COMPLETE; implementation, CI fault injection and AWS deployment NOT STARTED. HIGH priority, OPEN.** Re-read `src/handoff_watchdog/handler.py`, `infrastructure/app/handoff-watchdog.yaml`, image/video completion EventBridge rules, and existing watchdog tests. No paid Google calls or live AWS mutations made.

**Verified limitations and required acceptance gates:**
1. `watch-{jobId}` starts only after an image SFN `RUNNING` EventBridge notification. Lost notification means no monitor exists. Require a durable *event-independent* missed-start detector (bounded, targeted, with no always-on full-table scan); prove dropped-start recovery and duplicate-start idempotency.
2. Both the SFN's `TimeoutSeconds` and Lambda's `MAX_LIFETIME_SECONDS` are 7200. Require observable escalation/reconciliation for still-active jobs at/after deadline; distinguish Step Functions timeout from durable operator alert. Test a synthetic >2h job.
3. The monitor currently stops at `REVIEW_VIDEO_FAILED` (even if a manual scene retry later succeeds) and on `REVIEW_*` from `reconcile()`. Ensure later authorised retries regain monitoring and no review is silently lost. Test failed scene → manual retry → completed scene → successor handoff.
4. EventBridge targets have RetryPolicy but no DLQ; successful Lambda returns of `*_NEEDS_REVIEW` do not trigger automatic retry. Add durable, deduplicated review/alert routing and test event-delivery vs application-level failure separately.
5. Preserve safety: watchdog can GetItem, DescribeExecution, invoke the existing API and start only its own SFN. No DynamoDB scan/write, no direct Google or paid request, no blind retries or extra FFmpeg writer; test zero new model submissions and single named handoff per scene.

**Next safe step:** introduce isolated mocked tests / a no-write review design and inspect exact deployed resource configuration before a guarded runtime change. Do not treat existing unit tests or an uploaded workflow as live coverage. Keep user-visible list updated after each milestone.

**17 November test checkpoint:** Added three offline baseline tests in `tests/test_handoff_watchdog.py` covering (a) absence of independent watchdog bootstrap if the RUNNING event is missing, (b) current premature stop when a video is FAILED before manual retry, and (c) 7200-second deadline behaviour. Commit `3f7340b`. These capture *current* semantics; they do not resolve these gaps. CI test outcome not yet verified, no AWS deployment. Next implementation must replace the gap behaviour with explicit recovery and update acceptance assertions.

**Item 2 offline CI verification (17 November 2026):** Dedicated GitHub Actions workflow `test-watchdog-item2.yml` automatically executed on commit `47ff4418` and **PASSED** ([run 44021102418](https://github.com/rorygleeson/afl-video-platform/actions/runs/44021102418)). Syntax verification and the entire `test_handoff_watchdog.py` unittest suite succeeded. This proves baseline mocked tests only; missing-start, manual-retry re-monitoring, long-job reconciliation, and DLQ/alerts are **still OPEN**, not fixed or live-tested. No AWS credentials, paid model calls, or runtime deployment in this test workflow. General `Validate infrastructure` workflow is separate and has shown failures on prior commits; do not conflate its status with this targeted pass.

### Item 2 missing-start fallback — proposed isolated implementation (17 November 2026)

**Status: SOURCE IMPLEMENTED ON BRANCH `resilience/item2-missed-watchdog`; NOT LIVE DEPLOYED, CI pending.** Does not change Google prompts, provider submissions, image/real-video Step Functions or FFmpeg.

- Reuse the existing **five-minute image reconciliation EventBridge schedule**, not a second schedule. Its already bounded DynamoDB scan includes recent (<=2h) consenting active image/completed-image jobs as well as previously detected stale image jobs.
- The image worker can invoke **only** the named `afl-app-dev-handoff-watchdog` Lambda (narrow IAM permission), with `op=ensure_monitor` for a single job. Watchdog independently validates job ID, consent, activation, nonterminal status and an actual RUNNING/SUCCEEDED image SFN execution before it starts the same idempotent `watch-{jobId}` monitor. A missing image RUNNING EventBridge event can therefore be recovered without a paid Google operation.
- Safety: no direct Google call, no new image/video generation, no DDB writes from the watchdog. `ExecutionAlreadyExists` remains duplicate-safe. Errors are logged without interrupting the original 45-minute stale-image recovery path.
- **Residual limits:** The pre-existing bounded scan examines at most 1,000 DynamoDB items each five-minute pass; a larger table could exclude some jobs, and the function returns `scanIncomplete` for detection. Jobs older than two hours, expired watchdog executions, manual-retry restarts, review DLQs/alarms and failure of the separate scheduled reconciler are **not** fixed by this substep. More durable targeted indexing/monitor registry may be required before paid launch.
- **Acceptance:** offline tests for lost start, duplicate monitor, missing image execution, old/nonconsenting/terminal jobs, preserving stale-image recovery, failure isolation, bounded scan; then independent CI, scoped IAM/CloudFormation preview and deployment verification. Never claim production coverage merely because tests pass.

**Item 2 PR #84 CI checkpoint (17 November 2026):** [Dedicated offline watchdog run 44021829673](https://github.com/rorygleeson/afl-video-platform/actions/runs/44021829673) **PASSED** syntax verification, existing watchdog regressions and new image-reconciler fallback tests; [documentation validation 44021829654](https://github.com/rorygleeson/afl-video-platform/actions/runs/44021829654) **PASSED**; PR secret scan **PASSED**. The wider infrastructure suite [44021829647](https://github.com/rorygleeson/afl-video-platform/actions/runs/44021829647) **FAILED** at its existing full Python-test step (8 failures / 46 errors, many unrelated image/prompt fixtures); baseline main already fails at this step as well. Do not mislabel full CI green. Draft PR remains **NOT MERGED / NOT DEPLOYED** pending scoped verification and safe rollout. Tests are mocked; AWS EventBridge delivery failure injection is still outstanding.

### Item 2 missing-watchdog recovery — merged and AWS-deployed (17 November 2026)

**Status: MERGED / GUARDED AWS DEPLOYMENT VERIFIED; LIVE MISSED-EVENT FAULT INJECTION OPEN.** [PR #84](https://github.com/rorygleeson/afl-video-platform/pull/84) was squash-merged to `main` as `c3f1dd69fad59c19b561caf37a699605b79185a9`. A targeted, guarded release workflow was added to deploy the isolated watchdog and image-worker fallback via CloudFormation, with exact Lambda artifact readback and restrictive allowed change sets.

- **Successful AWS deployment:** [Actions run 44022615608](https://github.com/rorygleeson/afl-video-platform/actions/runs/44022615608), committed deployment guard `3cb4fe5d`. The workflow's offline checks passed **30 watchdog tests plus 5 reconciler tests**. It previewed both changesets before execution, rejected unrelated resource changes, and logged `DEPLOYED_AND_VERIFIED watchdog` and `DEPLOYED_AND_VERIFIED image`, followed by `ITEM2_MISSED_WATCHDOG_DEPLOYED: NO GOOGLE OR JOB SUBMISSIONS`.
- **Resources updated:** watchdog Lambda code in isolated watchdog stack; image worker Lambda code and the narrowly scoped watchdog invocation IAM statement in backend stack. CFN additionally recalculated downstream references in existing event rule/State Machine resources; guards permitted only dependency-driven modifications with no definite replacement.
- **Failed previews before successful deployment:** [run 44022331548](https://github.com/rorygleeson/afl-video-platform/actions/runs/44022331548) and [run 44022447239](https://github.com/rorygleeson/afl-video-platform/actions/runs/44022447239) were stopped by conservative preview assertions **before any stack updates**; guard was amended for source-confirmed dynamic Code/role dependencies, then deployed successfully.
- **What this provides:** the existing five-minute image reconciliation schedule can start a verified missing `watch-{jobId}` monitor for recent, consenting jobs, independently of the image RUNNING EventBridge event. Does not launch a Google or video generation request, duplicate an existing watchdog execution, or introduce a second scheduled rule.
- **Still OPEN:** controlled live lost-RUNNING-event injection and recovery proof, bounded scan coverage (max 1,000 evaluated items per cycle), jobs older than two hours, manual retry re-monitoring, EventBridge DLQ/durable review queue/alerts and broader failure tests. A successful deployed-code readback is **not** full end-to-end resilience verification.

**Item 2 missing-start fallback — MERGED AND AWS DEPLOYED (17 November 2026).** PR [#84](https://github.com/rorygleeson/afl-video-platform/pull/84) merged as `c3f1dd69`. Guarded release [run 44022615608](https://github.com/rorygleeson/afl-video-platform/actions/runs/44022615608) **SUCCEEDED**: 30 watchdog and 5 image-reconciler offline tests passed; CloudFormation previews approved for isolated watchdog stack and backend image-worker code/IAM changes; both stack updates succeeded; deployed watchdog and image-worker Lambda hashes matched the new artifacts. No Google model requests or paid video workflow executions were made by this release. The original 5-minute image reconciler now attempts an idempotent missing-start watchdog bootstrap for recently started consenting jobs, with image-SFN existence validation. **Status: DEPLOYED / RESOURCE VERIFIED; LIVE MISSED-EVENT FAULT INJECTION NOT YET PROVEN.** Remaining Item 2 work: watchdog 2-hour lifetime, manual retry after monitor stop, DLQ/durable review alerts, and bounded scan >1000 item coverage. Full general CI suite remains red from unrelated existing tests; it must not be described as green.


## Decision — strict 15-minute customer job deadline (17 November 2026)

**Requested product policy: 15 minutes maximum per video job, NOT two hours. DESIGN DECISION / IMPLEMENTATION OPEN.** The currently deployed per-job watchdog and its SFN are both still configured at **7200 seconds**; Google's per-scene workflow has a separate **3600-second** SFN timeout. Do **not** simply change watchdog `TimeoutSeconds` to 900: that would stop recovery monitoring without cancelling provider work or blocking future paid scene starts, and might strand jobs.

**Required gated implementation:** (1) Persist a backend-authoritative deadline from the initial actual image-generation start, not each watchdog (re)start; (2) use conditional admission checks at image/video retries, next-scene EventBridge handoffs and FFmpeg starts to block *new work* after 900 seconds; (3) when the deadline passes, stop owned AWS executions where safe, but treat Google provider cancellation as unconfirmed unless a supported provider cancel operation is verified; (4) preserve all completed reference images/clips and do not blindly replay any paid POST; (5) distinguish definitively stopped/failed from provider-unknown `REVIEW_REQUIRED` and do not automatically refund on ambiguous billable acceptance; (6) durable operator alert and safe idempotent recovery/readback; (7) prove with mocked elapsed-deadline/slow Google/late completion/manual retry/racing handoff tests plus guarded AWS readback. Limit applies to normal *customer processing*, not expiry of retained customer deliverables or operational audit/review records.

**Status:** Policy recorded. **NOT IMPLEMENTED OR DEPLOYED**; no claim that Google charges can be cancelled or that existing deployed 2h monitoring has been shortened. The 2h watchdog gap is superseded by this stricter deadline design, but the deadline must not be made by destroying recovery coverage. Follow up in Item 2 before Stripe.


## Focused existing-main flow bug audit — 17 November 2026 (read-only)

**Scope:** current production branch `main` only; draft PR #85 is unmerged and must be excluded. Inspected actual `frontend/app.js`, `src/api/handler.py`, image and Google workers, `src/assembly_worker/handler.py`, `src/handoff_watchdog/handler.py`, and main infrastructure templates. **SOURCE VERIFIED, LIVE STATE NOT RE-INSPECTED** in this pass. No AWS writes, paid requests or code changes performed.

**What appears sound:** the browser has GET-only progression after initial image workflow; server reserves real scene attempts in DynamoDB before named Standard SFN starts, previous scenes need completed S3 MP4s, Google POST ambiguous results are not blindly retried, completed MP4s persist on job, FFmpeg is separate and has one guarded confirmed-failure retry, localStorage job resume works with developer session key, and the missed watchdog start fallback is deployed.

**Ranked targeted defects and discrepancies:**

1. **HIGH / verify live immediately — Google `Claim` source JSONPath:** `infrastructure/app/google-video.yaml` Claim uses `"requestId.$":"$.Execution.Name"`, while Submit/Poll/Finalize use `"$$.Execution.Name"`. Without an `Execution` object in input, Claim fails before Google POST. This is an **IaC source discrepancy**, not evidence of an active customer outage: compare read-only `DescribeStateMachine` live ASL and known successful execution history before any deployment. Retain correct live config rather than blindly replacing it.
2. **HIGH / failed handoff can become manual review rather than automatic recovery:** `api.video_sequence_event` returns `ASSEMBLY_HANDOFF_NEEDS_REVIEW` or `NEXT_SCENE_HANDOFF_NEEDS_REVIEW` as successful Lambda results for certain missing/reserved workflows. That does not automatically trigger delivery retry. Watchdog generally skips already reserved paid scene attempts and its `REVIEW_*` results can stop monitoring. Add visible durable review/escalation, no second paid request.
3. **HIGH / paid-admission terminal race remains:** `api.start_real_scene`, `start_video_sequence` and `retry_failed_real_scene` read `definitive_failure` before their DynamoDB reservation, but conditional updates do not atomically fence all newly terminal job states. `google_video_worker.require_paid_unlock` checks blocked sequence, not all terminal states. Race-test an image/assembly terminal transition against the paid claim; add only the necessary conditional safety gates after evidence.
4. **MEDIUM-HIGH / final output marked deliverable without HEAD validation on status GET:** `assembly_public` creates 300-second signed URLs when DDB says COMPLETED; it does not confirm `videos/{jobId}/final.mp4` still exists. A missing object would produce an apparent completed delivery then a download failure. Verify at final durable completion and classify missing/expired media clearly.
5. **MEDIUM / orphaned generated videos and expired job data:** backend S3 lifecycle expires `uploads/` and `results/` after seven days, but has no lifecycle for `videos/`, while DDB job TTL is seven days. MP4s can outlive metadata; active/review jobs can lose input images. Implement safe job-aware cleanup after confirmed completion, keeping final download for chosen retention and independent payment audit records later.
6. **MEDIUM / watchdog coverage not exhaustive:** image reconciler scans at most 1,000 evaluated items every five minutes and targets recent jobs; a larger table can omit active work. Named monitor stops at 7,200 seconds, or `REVIEW_VIDEO_FAILED` before a later manual retry. The deployed missing-start fallback reduces but does not eliminate stalls; test real event loss and manual-retry re-monitoring before launching consumers.
7. **LOW / API quota source drift:** `backend.yaml` still says monthly 10,000 in test usage plan; user previously raised live quota to 100,000 after hitting 10k. Confirm live plan and update source using an explicit guarded IaC release to avoid reset on a future backend stack change.
8. **LOW-MEDIUM / browser recovery bounded to same browser + developer key:** `localStorage` retains job ID and `sessionStorage` may retain developer key. Same-browser resume exists, contrary to older documentation; cross-device link and customer entitlement do not. Status GETs can overlap across panels, contributing to usage-plan quota spikes. Do not add new authentication/payment architecture as part of this reliability audit.

**Verification gaps:** cannot certify runtime Google ASL drift, EventBridge delivery, actual Google billable outcomes, active AWS errors, production quota or MP4 health based only on source. Full repository CI is separately red from existing tests; dedicated watchdog tests passed. The aborted 15-minute draft PR #85 has its own failing Scene-3-to-assembly assertion and is **not** part of deployed code.

**Minimum-change next sequence:** (1) read-only live Google SFN ASL + recent execution ownership; (2) verify stored/completed MP4 download and EventBridge handoff on one existing job, no Google spend; (3) add targeted failure regression tests and durable observable review for missing handoff; (4) only then guarded small fixes. Leave two-hour operational watchdog unchanged and do not merge PR #85.

### 17 November deduplication/status cross-check (no new defects introduced)

The latest existing-`main` flow inspection confirms **all eight findings are already tracked**, so do not add duplicate risk IDs: Google `Claim` JSONPath → **RA-10**; missed/uncertain EventBridge scene/assembly handoffs → **RA-02 / ED-02**; concurrent paid-admission terminal fencing → **RA-04**; unverified final MP4 delivery → **RA-08**; `videos/` cleanup and seven-day metadata TTL → **RA-06**; watchdog coverage and re-monitoring after manual retry → **RA-02**; API Gateway source quota versus reported live development setting → **Minor priority — API polling and quota UX**; same-browser recovery versus missing secure cross-device customer recovery → **RA-05**, with existing implementation noted above. These are **previously identified source findings / unverified live risks**, not newly demonstrated defects. **RA-01 deployed**; **RA-02 missing-start fallback deployed but remaining gaps open**. Draft **PR #85** (hard 15-minute deadline) remains unmerged/undeployed, separate from `main`. Next audit work should prioritise reproducible failing tests and read-only deployed-state comparison, not re-list old risks. No AWS or app-runtime changes in this documentation cross-check.

## Release drift / idempotency deployment checkpoint — 26 November 2026

**Status: RELEASE TOOLING DRIFT CONFIRMED; SOURCE-TO-LIVE IaC DRIFT NOT YET MEASURED; NO GOOGLE-WORKER DEPLOYMENT.** This checkpoint supersedes the earlier RA-10 source-path suspicion: the audited live Google video state machine uses `$$.Execution.Name` for `Claim`, `Submit`, `PollVideo` and `Finalize`. Do not reintroduce the obsolete `$.Execution.Name` discrepancy.

- **Live read-only audit:** [GitHub Actions run 45709407783](https://github.com/rorygleeson/afl-video-platform/actions/runs/45709407783) reported no warnings, five active state machines, four enabled EventBridge rules with one target each, and audited Lambda hashes matching their *deployed CloudFormation artifacts*. This proves a deployed-resource/artifact relationship at audit time, **not** equivalence between current GitHub infrastructure templates and deployed CloudFormation templates. It does not prove delivery, failure recovery or a paid end-to-end job.
- **Google paid-submit idempotency:** [PR #90](https://github.com/rorygleeson/afl-video-platform/pull/90) merged the exact authorised Step Functions execution-name check for *every* paid POST attempt, including the first. The worker already records `SUBMISSION_UNKNOWN` conditionally before POST and does not blindly repeat an ambiguous accepted submission. Offline regression tests cover uncertain responses and crash windows; full Python suite subsequently passed 373/373 after [PR #91](https://github.com/rorygleeson/afl-video-platform/pull/91) repaired a test import. **The PR #90 worker change remains merged in GitHub only; AWS deployment and live fault injection are not verified.**
- **Concrete release-tooling drift:** `scripts/prepare_google_reconciliation.py` expects hard-coded Google worker SHA `RKx1wcvmxcE3Sx7kzlxbTI3NhBlNlt/Ydpy6J4ZOpmI=` and historical artifact key `google-diagnostics/956c2a9faa7efb1f39ed1a8ecc376510ffb1ec34.zip`; the audit observed live worker SHA `NpSAPzsdGDJlorMm9zmbdhVVI9Ywj+wAOVi8U0Y/8aE=`. Consequently `.github/workflows/release.yml` option `change_set_preview=google` is a historical narrow reconciliation, **not** a reusable current-worker release path. `deploy_backend` is the general backend deployment action; neither option should be assumed safe for this Google worker without a verified preview. A standalone idempotency deploy workflow was considered but **not committed, merged or executed**.

**Drift-safe next sequence (required before deploying PR #90):**

1. Capture **read-only** deployed CloudFormation templates, stack parameters (names and nonsecret comparison only), resource physical IDs, Lambda code SHAs/artifact keys, and Step Functions definitions using the established audit/baseline mechanisms. Record exact `main` SHA and timestamp; do not print secrets or complete sensitive templates in logs.
2. Compare live templates/parameters and critical runtime configuration against the same-commit GitHub IaC. Classify differences as intended operational overrides, stale IaC, stale release tooling, or actual unintended drift. In particular verify Google worker artifact, execution ownership, state-machine JSONPaths and IAM; avoid overwriting any live-only change.
3. Reconcile the **existing** release pipeline to support a tightly scoped, repeatable Google worker deployment with deterministic artifact hash, immutable version/key, allowlisted CloudFormation changes, rollback artifact and pre/post SHA readback. Remove or retire obsolete hard-coded reconciliation assumptions. **Do not add another one-off CI/CD workflow** for this fix.
4. Generate and independently review the exact change set; fail closed if unrelated resources, IAM, timeouts, state-machine definitions or parameters would change. Only then execute the approved isolated release, verify the live worker SHA and run nonbillable idempotency checks. Never exercise real Google paid POSTs as a deployment smoke test.

**Gate:** Until steps 1–4 succeed, describe PR #90 as **SOURCE MERGED / NOT DEPLOYED**, and do not claim GitHub IaC and AWS are drift-free. Preserve the existing user-maintained scenario asset publishing workflow and avoid unrelated edits.

**Read-only implementation checkpoint:** The existing manual `Audit live AWS deployment state (read-only)` workflow is being extended with `scripts/compare_live_templates.py`. It fetches deployed CloudFormation *original templates* via `GetTemplate` and compares them to the five source stack templates. Its published artifact includes only resource logical IDs/types and structural digests, missing/extra logical IDs and parameter/condition/output **names**; it does not upload template bodies, parameter values or secrets. Any difference or access denial is a review gate, **not** proof of dangerous drift or permission to deploy. This complements, rather than replaces, the existing live Lambda/SFN/EventBridge audit; it is not a substitute for CloudFormation drift detection or a guarded change-set preview. It makes no AWS writes or paid requests.

**First live comparison (read-only run #9, 26 November):** [Actions run 45713435539](https://github.com/rorygleeson/afl-video-platform/actions/runs/45713435539) completed its existing Lambda/SFN/EventBridge inspection successfully, then intentionally failed closed on template differences. `assembly`, `frontend` and `mock_video` source resource structures matched deployed originals. No extra/missing resource logical IDs or parameter/output/condition names were reported. The differing resources were `backend/ImageStateMachine`, `google/GoogleStateMachine`, and `google/GoogleWorkerLambda`. **These are resource-template differences, not yet classified configuration errors.** The next read-only audit iteration adds the changed *top-level CloudFormation property names* for only those differing resources, without emitting values or nested environment keys. Do not deploy the Google worker until these differences are explained and a guarded changeset is reviewed.
