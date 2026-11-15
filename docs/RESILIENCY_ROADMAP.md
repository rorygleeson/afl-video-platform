# AFL Video Platform — Resiliency and Payment-Readiness Roadmap

**Status:** Active checklist — documentation/discovery first  
**Created:** 2026-11-15 (Australia/Melbourne)  
**Repository:** `rorygleeson/afl-video-platform` (`main`)  
**Scope:** Existing selfie → three Google reference images → three Google video scenes → FFmpeg final MP4. Stripe design only until explicitly authorised later.

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
- [ ] **2.6 Server-side final stitching trigger:** initiate exactly once from durable completion of video 3, not frontend refresh; add bounded retry for transient FFmpeg/S3/DynamoDB failures, retain all three completed clips.
- [ ] **2.7 Persistent watchdog and stalled-job reconciliation:** recover or explicitly mark cases needing review without false failures/refunds. Define job-wide deadline and cost limits.
- [ ] **2.8 Anonymous job recovery and security:** future-ready unguessable per-job capability token, hashed server-side, expiry/revocation, rate limits, safe cross-device/email option, and restricted media access. Developer `x-api-key` remains testing-only, not a consumer credential.
- [ ] **2.9 Frontend integration:** preserve current layout/progress bars; use backend source of truth, restore safely after browser refresh/close, hide raw errors from customers, keep diagnostics in developer panel.

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
- Assembly Lambda configured 600s, and assembly workflow contains no automatic Retry; frontend currently requests assembly when all three scenes appear complete.
- Frontend job polling 5s; on poll error 15s. Frontend stores current job ID in runtime memory and does not yet implement anonymous secure recovery.
- Image worker retains completed images on error but its recovery may terminalise unfinished stages. The image watchdog runs every 5 min and marks long-stalled jobs failed.
- Existing `docs/ARCHITECTURE.md`, `docs/API_ENDPOINTS.md`, `docs/DEPLOYMENT.md` and some stale API strings include obsolete parallel/Replicate/assembly claims.

## Change log

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
