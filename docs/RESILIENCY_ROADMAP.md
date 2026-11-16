# AFL Video Platform — Resiliency and Payment-Readiness Roadmap

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
3. **High — no secure anonymous job recovery.** The frontend holds current `jobId` in page runtime; no supported resume after browser reload, new device or emailing a link. Developer `x-api-key` is not a customer entitlement.
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
