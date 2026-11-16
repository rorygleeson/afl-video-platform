# Pre-payment resilience source audit — 2026-11-16

**Status:** Historical static baseline and current ranked gap register. **RA-01 was subsequently code-fixed and guarded AWS-deployed**; its live injected failure/retry behaviour remains unverified (see final RA-01 sections). All other open gaps remain tracked. This report itself is documentation-only; it does not trigger AWS changes, paid requests or cleanup.

> **Reading convention:** The early R1 and RA-01 entries below preserve the **original findings at discovery time**. Their older “one Stitch task” and “OPEN” descriptions are historical. The latest status is in the RA-01 implementation and rollout sections at the bottom of this report. Source-code changes have already been deployed separately.

## Scope
Reviewed current `main` source: `src/api/handler.py`, `src/image_worker/{handler.py,google_direct.py}`, `src/google_video_worker/handler.py`, `src/assembly_worker/handler.py`, `src/handoff_watchdog/handler.py`, `frontend/app.js`, `infrastructure/app/{backend,google-video,assembly,handoff-watchdog}.yaml`, and existing resiliency/deployment baseline documents. This is focused static inspection, **not a guarantee of no bugs**. Tests and live AWS comparison still required.

## Findings / gates before Stripe

| ID | Severity | Finding and evidence | Required test / action | Status |
| --- | --- | --- | --- | --- |
| R1 | HIGH | `start_assembly()` conditionally writes `assembly.status=QUEUED` before `states.start_execution()`. If StartExecution fails or its response is lost, later calls return the existing assembly record without ensuring `final-{jobId}` runs. The assembly SFN has one Stitch Task with no Retry/Catch. | Inject failed/uncertain StartExecution and Lambda timeout; reconcile named execution and retry *stitching only* safely, never regenerate scene MP4s. | OPEN |
| R2 | HIGH | Handoff watchdog is started by a best-effort EventBridge image SFN RUNNING event; missing this event means no per-job watchdog. Its Standard SFN stops after 7200s. Watchdog can replay verified image/video success but does not repair every missing assembly reservation. | Inject dropped start/completion events; test >2h slow jobs and each handoff gap; use durable checks/alerting without accidental paid submissions. | OPEN |
| R3 | HIGH | `frontend/app.js` persists a job ID in localStorage and restores the selfie/message/status, but `restoreJob()` requires a developer API key. Same-browser recovery in developer mode is not equivalent to an anonymous paid-customer recovery scheme. | Validate refresh and close/reopen while active and after final completion; before Stripe design a secure paid-job access token with recovery across devices. | OPEN |
| R4 | HIGH | Video worker deliberately records `SUBMISSION_UNKNOWN` before billable Google POST, and image worker treats ambiguous network failure as submission-unknown. This avoids unsafe duplicate billing but does not guarantee automatic completion/reconciliation. | Inject read timeout / dropped response after provider acceptance; verify no duplicate POST, useful diagnostics and well-defined manual review path. | OPEN |
| R5 | HIGH | `infrastructure/app/backend.yaml` S3 Lifecycle currently covers `uploads/` and `results/` at 7 days, **not `videos/`**, which is where scene and stitched MP4s are stored. DynamoDB `expiresAt` is 7 days. | Inventory exact S3 prefixes and sizes; define final MP4 retention and cleanup before customer rollout. Do not activate blanket `videos/` expiry until the right distinction between scene and final MP4 is implemented. | OPEN |
| R6 | MEDIUM | Image generation has bounded explicit 429 retry, but ambiguous image submissions are terminal/unknown; three scenes execute with Map `MaxConcurrency:3`. | Simulate HTTP 429 (including Retry-After), explicit 500, timeout, one-scene failure and parallel collisions; ensure successful image outputs survive retries. | TEST REQUIRED |
| R7 | MEDIUM | Frontend play/replay refreshes signed clip URLs; S3 GET presigns last ~300s. Browser polling and explicit replay must not trigger regeneration. | Exercise elapsed presigns, hidden tab, final-only view, refresh/revisit, partial scene preview and download with slow networks. | TEST REQUIRED |
| R8 | MEDIUM | Active jobs may exceed intended cleanup windows, and S3 expiry is based on object age, not job completion. Existing unfinished multipart upload rule is one day. | Delay scene completion, force retries/assembly failure, then verify source files are not deleted before a recoverable final MP4 exists. | DESIGN REQUIRED |

## S3 cleanup and retention — proposed, NOT implemented

1. Inventory `uploads/{jobId}/*`, `results/{jobId}/*`, `videos/{jobId}/scene{1,2,3}.mp4`, `videos/{jobId}/final.mp4`, plus template/configuration prefixes and any historical shapes in live S3. Confirm which artifacts each retry or stitch operation needs. Existing CloudFormation has 7-day `uploads/` and `results/` expiration, no `videos/` expiration.
2. After verified assembly COMPLETED, make selfie, three reference images and three scene MP4s **eligible** for short-term cleanup (~24h following completion). Do not delete scenes before assembly is complete and verified. Prefer dedicated per-job cleanup eligibility marker or isolated disposable prefix, then S3 Lifecycle to perform actual expiry; a job-aware Lambda may be necessary if deletion must be relative to completion time.
3. Retain `videos/{jobId}/final.mp4` for a proposed **7 days**, subject to customer delivery/download policy. Ensure emailed recovery links expire/revoke correctly when media is removed.
4. For FAILED/ABANDONED jobs, only clean inputs after retry/manual-review window expires and no Step Functions execution is active. Do not remove source MP4s required for an assembly-only retry.
5. **NEVER expire** permanent templates, team references, frontend deployments, shared prompts, and CloudFormation artifacts based on a generic bucket rule. Check bucket versioning and incomplete multipart upload expiration separately.
6. Retain minimal DynamoDB audit/payment metadata according to a distinct policy; existing 7-day TTL is not suitable as the sole payment audit record.
7. Monitor storage growth and tag cleanup outcomes; test with mock objects before CloudFormation rollout. Lifecycle execution time is approximate, not an exact 24-hour scheduler.

## Test matrix before accepting customer payments
- Successful 3-image → 3-video → stitched final run, closed browser after image start, and fresh-tab resumption.
- Single image 429/retry, explicit image failure, uncertain image submission, and other scenes preserved.
- Single video 429, provider timeout after accepting POST, terminal video failure, manual retries capped at ten, duplicate-click and competing clients.
- Missing/disordered/duplicate EventBridge image and video completion events; missed watchdog-start event; >2h execution.
- Missing scene in S3, FFmpeg failure, assembly Lambda timeout, missing assembly execution after QUEUED reservation, duplicate Stitch event; recover without recreating paid videos.
- Presigned URL expiration (both intermediate preview and final download), refresh after COMPLETED, final-only display, and no extra billable requests on GET.
- Cleanup at completion+24h, unfinished job+24h, failed job and final+7d, including protection of all shared templates.
- Record evidence: source commit, live deployed hashes/stack revisions, test inputs, result, and actual Google/AWS cost. No claim of release readiness until tests pass.

## Deployment safety
Do not execute GitHub workflows containing deploy or paid-generation steps during this documentation audit. Verify source against live AWS using an explicitly read-only authorised route later. Preserve the development API-key bypass only for pre-payment testing; production admission requires server-side verified Stripe entitlement.

## R1 staging progress — 2026-11-16

On branch `resilience/assembly-handoff-recovery`, API code checks whether a `QUEUED` assembly's stable `final-{jobId}` execution exists and, only if AWS confirms it is absent, retries starting that **same named execution**. A guarded source-contract test was added. This does not invoke Google generation or create a new assembly name. **Not deployed or live-tested.**

**R1 remains open:** an automatic reconciliation trigger is still needed for stranded QUEUED jobs, as are bounded assembly-only retries after FFmpeg FAILED/PROCESSING timeouts. Behavioural fault-injection and CI must pass before considering deployment. Avoid blindly retrying a worker with uncertain execution status.

### R1 test evidence — GitHub Actions run 43772268421

The six new offline behavioural tests in `tests/test_assembly_handoff_behaviour.py` **all passed** in CI. They cover existing execution, missing execution, AccessDenied/unknown DescribeExecution, ExecutionAlreadyExists race, already-completed assembly, and missing source scene. The broader `Validate infrastructure` workflow nevertheless **FAILED**: pre-existing/stale tests expect removed `sharedPrompt`/`promptInstructions` configuration fields, and `test_assembly_worker.py` expects obsolete frontend text (`Download Final Video MP4`). This is not a clean suite, and a baseline-versus-branch comparison is still needed. Evidence: https://github.com/rorygleeson/afl-video-platform/actions/runs/43772268421. **No AWS deployment**; do not mark R1 completed.

### R1 additional staged implementation — 2026-11-16 (still NOT deployed)

- `infrastructure/app/assembly.yaml`: after failed first Stitch task, wait 15 seconds and allow **one** additional invocation marked `retry:true`. No Google calls. On second failure, Step Functions fails explicitly with `ASSEMBLY_NEEDS_REVIEW`.
- `src/assembly_worker/handler.py`: only a worker-persisted **FAILED** record with `retryCount=0` can transition conditionally to `PROCESSING` for that extra attempt; then persist `retryCount=1`. If the first invocation timed out while still PROCESSING, retry is refused and flagged for review, avoiding concurrent writers and final.mp4 overwrites.
- `src/handoff_watchdog/handler.py` + internal API: after two minutes in assembly QUEUED, the existing per-job watchdog asks the API to reconcile the stable `final-{jobId}` Step Functions execution. It does not start Google requests, does not write DynamoDB, and stops at the existing two-hour cap. A missing initial watchdog event remains an **Item 2** gap.
- `src/api/handler.py`: terminal FAILED/TIMED_OUT/ABORTED final executions report `ASSEMBLY_WORKFLOW_NEEDS_REVIEW`; no new Step Functions name is launched in that state.
- New tests cover the safe handoff cases, watchdog QUEUED waiting/reconciliation and bounded FFmpeg-only retry policy. **Full CI and live integration still not validated; this is a staged change only.**

Residual: unknown Lambda timeout stays review-required rather than automatically retried. True operator-guided recovery for a confirmed dead timed-out attempt may require a separate, token-fenced execution and distinct attempt output key; it must not be rushed into the current single-name system. Also verify all existing/source regression tests, IAM configuration, and exact live stacks before deploying.

### R1 final safety review and deployment gate — 2026-11-16

Implementation on PR #83 now includes: reconciling a reserved but missing named final execution; one bounded FFmpeg-only retry after a **confirmed worker-persisted FAILED** state; withholding retry when worker is still PROCESSING/unknown; per-job watchdog inspection of aged QUEUED/PROCESSING/FAILED records and terminal execution statuses. Watchdog has read-only DescribeExecution permission for the final state machine, and delegates QUEUED restart checks to the existing API, which verifies all three real source MP4s.

Tests added/updated: fake AWS assembly handoff (missing/existing/uncertain/duplicate/terminal), watcher WAIT/REVIEW behaviour for queued/processing/failed records, retry worker contract, JSON parsing of the assembly Step Functions definition, and current scene-3 handoff AWS mocks. **The last full CI result must be compared with the main baseline before merge.** Main already has failing unrelated image-prompt tests; do not change prompt payloads to suppress tests.

#### Controlled deployment order (NOT performed)
1. Record exact live AWS source/artifact checksums and verify the existing backend, FFmpeg worker, assembly state machine and watchdog stack; ensure rollback packages.
2. Deploy API Lambda recovery function **first** (no externally advertised endpoint change).
3. Deploy FFmpeg Lambda worker supporting explicit retry flag and conditional failed->processing transition.
4. Update assembly Step Functions ASL to add a 15-second delay and one conditional retry; validate no Google Lambda references.
5. Deploy watchdog Lambda **and its CloudFormation IAM/env changes together** (adds read-only access to named final execution).
6. Run free synthetic/fault tests against a known fixture (or explicitly authorised test assets), then one already-completed real three-scene job. Do not invoke Google generation or change customer data without permission.
7. Confirm no more than one matching final execution name, exactly one retry when the first worker has persistently FAILED, no retry on a PROCESSING timeout, final S3 output and DynamoDB status match, and rollback readiness.
8. Only then mark **LIVE VERIFIED / R1 COMPLETE**. Missing watchdog start events and >2h per-job lifetime remain Item 2 work.

No deployment is implicit from a source merge. This PR intentionally has no automatic AWS deployment trigger.

### R1 AWS deployment evidence — 2026-11-16

[Guarded release 43775514956](https://github.com/rorygleeson/afl-video-platform/actions/runs/43775514956) **succeeded**. Verified source/CloudFormation readback after three checked changesets: `api` (`ApiLambda`, dynamic `ProxyAny` reference), `assembly-worker` (`AssemblyWorker`, `AssemblyStateMachine` definition, dynamic `AssemblyStateMachineRole` references), and `watchdog` (`WatchdogLambda`, `WatchdogRole`, dynamic watchdog machine/rule/permission references). No application resource replacements; watchdog's `InvokeFromEventBridge` was reported as **Conditional** in preview, accepted under isolated rule guard; the stack update and deployed Lambda verification succeeded. The assembly workflow verified the bounded `RetryConfirmedAssemblyFailure` state and the watchdog Lambda verified `ASSEMBLY_STATE_MACHINE_ARN` environment config. No paid Google calls occurred.

**R1 status: DEPLOYED / RESOURCE VERIFIED.** New offline tests passed. Actual injected failure recovery and browser-visible final-video validation remain unverified; do not claim complete customer-level reliability from a successful stack update alone. The missing watchdog start event remains Item 2.


---

# Re-audit addendum — 16 November 2026, after terminal lifecycle, immediate handoff and playback releases

**Scope and evidence:** Current `main` source was re-read for API progress/admission, image worker/recovery, Google video worker and ASL, assembly worker/ASL, per-job watchdog/EventBridge, frontend recovery, S3/DynamoDB retention, and previous audit/R1 deployment evidence. **SOURCE REVIEW ONLY for this addendum**: no live AWS DescribeExecution/CloudFormation drift readback, synthetic failure injection, full CI or end-to-end Google test was executed. Previous R1 release 43775514956 provides **resource deployment evidence only**; no automatic assumption that every current repository file matches the live runtime. No running AWS resources were changed.

## Current key controls (verified in source)

| Safeguard | Current behaviour | Source |
| --- | --- | --- |
| Backend handoff | Success EventBridge event verifies owning workflow and persisted previous MP4, checks expected S3 object, conditionally reserves next named execution. Scene 2/3 fixed submission waits were removed; `Claim → Submit`. | `src/api/handler.py:video_sequence_event`, `infrastructure/app/google-video.yaml` |
| Paid POST protection | Scene-specific DynamoDB approvals, named SFN execution, `SUBMISSION_UNKNOWN` written before Google POST, no Submit task Retry; only read-only PollVideo has bounded retry. | `start_real_scene`, `google_video_worker.submit`, Google ASL |
| Image retry | Parallel images, automatic retry only on explicitly rejected Google HTTP 429, bounded by attempts/window; other ambiguous responses not replayed. | `image_worker/google_direct.py`, image ASL |
| Policy block | Confirmed Google safety failure atomically blocks video sequence and terminal projection prevents further starts. | `block_content_job`, `definitive_failure` |
| Assembly recovery | Stable `final-{jobId}` name; reconciles a confirmed missing reserved execution; worker may retry **once** after its first persisted `FAILED` status with a 15s wait; ambiguous PROCESSING timeout not retried. | `ensure_assembly_execution`, `assembly_worker.assemble`, assembly ASL |
| Watchdog | Per-job 60s loop launched from image SFN RUNNING event, bounded by 7200s, read-only describe/replay of safe completion event and conditional assembly reconciliation. | `handoff_watchdog/handler.py`, watchdog ASL |
| Frontend | Read-only status polling for paid scene handoffs; latest signed preview handling reuses loaded media; local job ID allows same-browser developer resume. | `frontend/app.js` |

## Findings, explicitly prioritised

| ID | Severity | Verified gap / exact evidence | Consequence | Fix and acceptance test | Status |
| --- | --- | --- | --- | --- | --- |
| **RA-01** | **CRITICAL** | `api.definitive_failure()` returns `STITCHING_FAILED`, `terminal=true` immediately when `assembly.status == FAILED`. However `assembly.yaml` catches the first worker failure, waits **15 seconds**, then invokes `RetryConfirmedAssemblyFailure` (one FFmpeg-only retry if `retryCount == 0`). `frontend.renderBackendOutcome()` hides the job and stops its video polling on the early terminal projection. | A job can appear permanently failed and enable **Start new video** while the **same job is actively recovering**. After successful retry the backend may later report COMPLETED, contradicting a previously advertised immutable final outcome; a payment/refund service could wrongly refund a recoverable job. | Make final stitching failure conditional on the **named assembly SFN being definitively terminal after all permitted retries**, or persist an explicit terminal assembly outcome under a conditional fenced write. During first `FAILED` / retry Wait show `STITCHING/RETRYING`, `terminal=false`; confirm terminal only after safe retry exhaustion / reconciliation. Test first failure→15s Wait→success, first failure→retry failure, lost SFN event, ambiguous timeout; assert **no premature failure UI/refund**. | **CODE FIXED / CI & AWS DEPLOY VERIFIED; LIVE FAULT INJECTION OPEN** |
| **RA-02** | **HIGH** | Per-job watchdog only launches from an EventBridge **image SFN RUNNING** event; event delivery not guaranteed. Both watchdog machine `TimeoutSeconds` and `MAX_LIFETIME_SECONDS` are 7200. It stops on `REVIEW_VIDEO_FAILED` even if a manual retry will later be initiated, and on certain unresolved review paths. | Missing start event or exceeded lifetime leaves no active automatic handoff monitor; an otherwise healthy completed retry may lose its next-stage completion notification. | Introduce a durable, bounded **active-job reconciliation trigger** or independent missed-start detector, plus explicit handoff/terminal counters; restore monitoring safely after authorised manual retry. Fault inject dropped RUNNING event, missing completion event, >2h job, failed→manual retry→completed next scene. Do not resubmit Google on uncertain provider state. | OPEN |
| **RA-03** | **HIGH** | Google video `SUBMISSION_UNKNOWN` precedes paid POST. It may stay after network timeout/no returned interaction ID, and watchdog merely continues checking; API reports `REVIEW_REQUIRED` when SFN fails. Image unknown accepted submissions similarly lack a provider operation handle. | No guaranteed automatic reconciliation, final termination, or refund decision for ambiguous provider acceptance; unsafe to simply charge again. | Define distinct `UNKNOWN_PROVIDER_ACCEPTANCE` operational cases, bounded attempts to read **existing** provider operation only when an ID exists, operator reconciliation/alert and explicit manual finalisation with evidence. Test response lost after acceptance, request rejected before acceptance, 429, queue timeout and Lambda crash; count Google POSTs exactly once per authorised attempt. | OPEN |
| **RA-04** | **HIGH** | Google scene admission and retry use read-modify-write plus conditional per-scene reservation. The source API checks `definitive_failure(job)` *before* DDB updates, but per-scene condition clauses explicitly fence `videoSequence.status=BLOCKED`, **not** image failure/assembly terminal/exhausted states. Worker `require_paid_unlock` also only explicitly checks `videoSequence.BLOCKED`. | Possible interleaving with a late terminal image/assembly update or stale EventBridge invocation: the earlier read can pass while job changes, then paid work might still reserve/claim. Risk needs concurrent fault-injection, not assumed observed live. | Condition all paid admissions/handoffs against a durable authoritative terminal gate or operation state, test racing terminal update and StartExecution plus stale EventBridge delivery. Preserve conditional request ID ownership and do not treat polling GET as mutation. | OPEN — concurrency risk |
| **RA-05** | **HIGH** | No payment/order entitlement source exists. Customer GET and recovery are behind shared developer `x-api-key`; browser stores job ID in localStorage; there is no per-customer no-signup recovery token or payment ledger. `confirmPaidSequence` only consents to Google billing. | Cannot securely launch a no-signup Stripe checkout, cross-device resume or automated refund decisions. | Design server-verified Stripe webhook + per-order entitlement, replay protection and scoped signed recovery token; persist independent `paymentStatus/refundStatus` with long-lived audit, never infer paid from job ID or Google approval. | OPEN before Stripe |
| **RA-06** | **HIGH** | `backend.yaml` expires `uploads/` and `results/` objects at **7 days**, but has no `videos/` lifecycle rule. `create_job` sets DDB `expiresAt` at 7 days. Intermediate clips may remain indefinitely even after DDB job expires; recoverable jobs can lose inputs at age 7d. | Growing MP4 storage costs, orphaned PII/video artifacts, lost retry sources; jobs/purchases cannot be audited once TTL deletes record. | Implement job-aware completion+~24h cleanup eligibility for selfies/references/intermediates only after verified final MP4; retain final for a defined download period; separate long-lived minimal payment ledger and time-based operational history. Test complete, failed, review and active >7d jobs; never expire permanent team templates or source clips before safe stitch/retry. | OPEN |
| **RA-07** | **MEDIUM-HIGH** | `image_worker.recover()` finalises incomplete branches to FAILED/PARTIAL_SUCCESS after SFN error or watchdog age threshold, yet the parallel branches and confirmed-429 retry path can race with recovery. Existing update includes conditional scene status checks, but no test evidence here of simultaneous late Google/S3 responses and watchdog terminalisation. | Late image output versus final terminal state could produce orphan files or inconsistent diagnostics; premature failure if a long branch outlasts watchdog assumptions. | Fault inject watchdog during slow parallel Map and 429 wait. Verify finalisation checks actual SFN status / bounded settlement, late result cannot alter terminal state or cause video handoff, and all S3 artifacts are accounted for. | TEST REQUIRED |
| **RA-08** | **MEDIUM-HIGH** | `assembly_public()` exposes COMPLETED plus a presigned URL based on recorded final key; the reviewed projection does not perform a final S3 HEAD/content check on **each** success GET. S3 expiry / missing final object can therefore leave `terminal=true, outcome=COMPLETED` while download returns 404. | Paid customer might be told delivery succeeded although media is gone or corrupt. | Verify final artifact existence and expected metadata before *durable delivery* confirmation, then on read distinguish temporary signed URL expiration from deleted media. Synthetic delete/corrupt final MP4 tests and expiry tests. Avoid hot-path HEAD on every poll if verified immutable completion record or separate read-only preflight is sufficient. | OPEN |
| **RA-09** | **MEDIUM** | Several frontend states expose raw technical language such as `SUBMISSION_UNKNOWN` → “Checking original Google submission” and `REVIEW_REQUIRED` → “Generation needs review ... Google may have charged”. Playback signed URL fallback is source-implemented but not proven on real Android/network/CDN expiration. | Confusing customer messaging and uncertain recovery UX while paid provider processing may be proceeding normally. | Separate safe customer-friendly preparation/recovery messages from technical diagnostics, visibly communicate delayed progression without recommending retry, verify expired preview and final link on device. | OPEN |
| **RA-10** | **HIGH — SOURCE MISMATCH / LIVE UNKNOWN** | `google-video.yaml` source has a stale description (“hard-disabled”) and the `Claim` requestId JSONPath appears as `$.Execution.Name` rather than context `$$.Execution.Name`. Source-versus-live ASL was **not** independently compared in this audit; prior deployment checks should not be interpreted as verification of this exact current template revision. | If live ASL were deployed exactly as source, wrong context path could block worker claim. If live differs, IaC drift creates future redeploy risk. | Read-only `DescribeStateMachine` vs committed rendered ASL, confirm successful real executions and requestId ownership. Fix IaC source only after verifying live value. | **VERIFY LIVE FIRST** |
| **RA-11** | **MEDIUM** | No verified complete alerting/operational SLO across active job age, terminal state, stale reservation, missed watchdog start, Google 429 rates, assembly retry exhaustion, deleted media, or anomalous provider costs. Some watchdog REVIEW statuses are logged and return `continue=false`, without proven durable manual-review queue. | Customer failures may remain unnoticed despite a warning in a prior SFN execution or short-lived CloudWatch log. | Set metrics/CloudWatch alarms and durable review record/queue with owner, alert deduplication, and escalation age; test alerts in a free fixture. | OPEN |
| **RA-12** | **LOW-MEDIUM** | Browser can be closed before the one-time `POST /jobs/{id}/generate` after successful job create; `AWAITING_IMAGE_PIPELINE` has no guaranteed backend launch/reconciler. Browser connection loss may strand a valid job before image orchestration. | An order might exist without ever starting generation. | For paid flow, make verified purchase/authorisation trigger backend image workflow exactly once after upload is confirmed; reconcile failed initial handoff via named SFN. | OPEN before Stripe |

## Recommended implementation order and stop/go gates

1. **P0 — reconcile stitching state semantics (RA-01)** before further permanent-failure UX or Stripe work. Do not refund on first persisted `assembly.FAILED` if the named `final-{jobId}` execution can still retry.
2. **P1 — durable review and watchdog coverage (RA-02, RA-03, RA-11)**. Ensure every paid request is eventually completed or explicitly reviewed, including missed RUNNING event and 2h deadline; no blind Google resubmission.
3. **P1 — race-free terminal fencing and SFN drift check (RA-04, RA-10)**. Verify in backend fault-injection and read-only deployed-state comparisons before changing Google/ASL definitions.
4. **P1 — recovery/payment/retention contracts (RA-05, RA-06, RA-08, RA-12)**. Build no-signup access and independent Stripe ledger and cleanup only after proofs.
5. **P2 — image concurrency and phone UX tests (RA-07, RA-09)**, preserve known-good Google prompts, retry limits and scene outputs.

**Acceptance evidence required:** exact git SHA and live Lambda/code/ASL hashes; mocked fault-injection for each case; no unintended new paid Google POST; observable terminal transition once, correct customer reason and no premature refund; recovery from refresh/closed browser; final MP4 downloadable; retention and alarm tests; successful bounded rollback. Never label static source review a production-ready release.

**Release decision:** Development/testing can continue with controlled, explicitly authorised Google spend. **Do not enable paid customer orders/automatic refunds yet.** No changes to AWS were made in this audit.


## RA-01 implementation correction — 16 November 2026

**Code fixed on main, pending deployment evidence until the guarded release passes.**
The API now uses read-only `assembly_failure_disposition(job)` against the stable `final-{jobId}` Step Functions execution. A persisted `assembly.FAILED` alone is **not terminal**:

| Assembly worker record | Named SFN | API `stageStatus` | `terminal` / `outcome` |
|---|---|---|---|
| `FAILED`, `retryCount=0` | `RUNNING` (15s Wait or safe retry) | `RETRYING` | false / null |
| `FAILED`, `retryCount=1` | `RUNNING` (retry settling) | `RETRYING` | false / null |
| `FAILED`, `retryCount=1` | `FAILED` | `FAILED` | true / FAILED, `STITCHING_FAILED` |
| `FAILED`, `retryCount=0` | `FAILED` | `REVIEW_REQUIRED` | false / null: retry not proven to have run |
| `FAILED` | missing/unknown/aborted/timed-out/SUCCEEDED-but-inconsistent | `REVIEW_REQUIRED` | false / null |
| `PROCESSING` | in flight | `PROCESSING` | false / null |
| `COMPLETED` and verified result key | completed | `COMPLETED` | true / COMPLETED |

The frontend remains on the progress screen and displays “Finishing your video — trying assembly again…” during a safe retry. No premature customer failure screen or automatic refund eligibility. The status GET calls SFN **DescribeExecution only** when a failed assembly needs classification. No Google POST, new FFmpeg execution, state mutation or extra automatic retry is introduced. The existing one confirmed FFmpeg-only retry after 15s is unchanged.

**Remaining operational gap:** an unknown/failed workflow without a durably recorded second failure remains `REVIEW_REQUIRED` and requires an operator/reconciliation path. This is intentional safety, not a permanent-failure declaration. Test first fail→success, confirmed second fail→terminal, lost Describe permission, timeout/inconsistent status, browser reload, no duplicate Google charges and status stability. Mark RA-01 **CODE FIXED / OFFLINE TESTED** only after CI evidence; **LIVE VERIFIED** only after guarded AWS code readback and ideally fault injection.


### RA-01 guarded AWS rollout evidence — 16 November 2026

[GitHub Actions run 43835732389](https://github.com/rorygleeson/afl-video-platform/actions/runs/43835732389) **SUCCEEDED**. API lifecycle/retry/UI regression tests passed and guarded CloudFormation API Lambda update plus frontend S3 publication/verification and CloudFront invalidation completed. No Google provider requests, FFmpeg worker code changes, or Step Functions definition changes were performed. **RA-01 status: CODE FIXED / CI AND DEPLOYMENT VERIFIED**. Real injected fail→retry→success and fail→retry→failure scenarios remain to be tested in live AWS; do not label end-to-end recovery proven. RA-02…RA-12 remain open.


## Deeper event-driven architecture re-audit — 16 November 2026

**Evidence:** current `src/api/handler.py:image_sequence_event,video_sequence_event,start_real_scene,retry_failed_real_scene`, `src/google_video_worker/handler.py:claim,submit,poll`, `src/handoff_watchdog/handler.py`, source EventBridge rules and Step Functions definitions. **Documentation review only**; no read-only live AWS `DescribeStateMachine`, new paid generation, fault injection or runtime change. Full trace and diagram in [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md).

### Existing risk upgrade and new explicit failure modes

| Risk | Source-verified observation | Severity / status | Next check |
|---|---|---|---|
| **RA-10 / ED-01: Google Claim JSONPath** | Committed Google-video ASL has `Claim.Payload.requestId.$ = "$.Execution.Name"` while Submit/Poll/Finalize use `"$$.Execution.Name"`. An existing prior dedicated `fix-omni-execution-context` workflow asserts **four** context references; the current template violates it. The source template's stale Comment also claims hard-disabled. **Live could differ.** | **HIGH, source mismatch confirmed, runtime outcome UNKNOWN.** Previously medium RA-10. | Read-only compare exact live `DescribeStateMachine` vs current rendered ASL, check recent execution ownership, then fix source/deploy using guarded change set and static regression assertion. Do not trigger paid Google to test this. |
| **ED-02: event acknowledgement is not business success** | In `video_sequence_event`, an unknown Scene 3 assembly StartExecution can return `ASSEMBLY_HANDOFF_NEEDS_REVIEW` and a reserved-but-absent next scene can return `NEXT_SCENE_HANDOFF_NEEDS_REVIEW` without throwing. An EventBridge→Lambda invocation can be **accepted asynchronously**, while the Lambda later returns a business-level `NEEDS_REVIEW`. That successful Lambda return causes **no Lambda async function-error retry**. EventBridge's target delivery retries do not cover this business-level failure either; Lambda async function errors and EventBridge delivery failures are separate retry paths. | **HIGH, source behaviour confirmed; no observed lost customer delivery.** | Durable reviewed handoff reason / alert, safe recheck of named execution and output; fault inject missing execution while Lambda exits normally. Do not issue new paid POST. |
| **ED-03: EventBridge expiry / missing DLQ** | Image/video completion rules allow `MaximumRetryAttempts=24`, `MaximumEventAgeInSeconds=3600`. Watchdog launch rule permits 12 attempts/3600s. No target DLQ appears in these IaC templates; watchdog image-start event itself may be lost, and watchdog naturally ends at 7200s. | **HIGH, previously RA-02/RA-11; OPEN.** | Test delivery exhaustion, absent watchdog startup, manual retry after watchdog stop and a >2h job. Add monitored durable failure sink before Stripe. |
| **ED-04: transition ownership / atomic fencing** | Paid original scene and manual retry read `definitive_failure` before conditional DDB writes; conditions fence `videoSequence.BLOCKED` but not every possible concurrently terminal state. | **HIGH, RA-04 OPEN.** | Race terminal transition against reserve/claim/StartExecution in offline AWS fake. |
| **ED-05: unknown provider acceptance** | Paid video `SUBMISSION_UNKNOWN` is set **before** Google POST; loss of interaction ID must not be treated as definitive failure. | **HIGH, RA-03 OPEN.** | Reconcile existing provider result if correlation is available; explicit review and provider-cost investigation; no blind POST retry. |
| **ED-06: delivery evidence / expiry** | Final completed API status generates a 300s signed URL based on final DDB result key; live object availability/retention not re-checked in this document review. | **MEDIUM-HIGH, RA-06/RA-08 OPEN.** | Verify final object and customer access, distinguish expired URL vs deleted object; separate job TTL and payment records. |

**Important:** RA-10 was originally classified MEDIUM at discovery and has now been upgraded to **HIGH** in the primary risk table after this source review, pending live ASL comparison. Do not imply this new audit established live AWS drift. These risks do not negate the prior RA-01 FFmpeg status fix; **RA-01 remains code/CI/deployment verified, live fault injection open**.

### Event/retry design acceptance gates

1. EventBridge invocation failure vs Lambda `NEEDS_REVIEW` return: prove each behavior using a fake Lambda target and verify durable reconciliation/alert rather than assume automatic target retry.
2. Repeated/reordered image/video SUCCESS: match exact named execution and current DDB request owner; verify S3 MP4 exists; only one paid next-scene reservation.
3. Stage reservation written then SFN `StartExecution` response lost: preserve idempotency, check existing named execution, never submit another Google request.
4. Watchdog launch absent or stopped before a later manual retry: mark job as requiring review; ensure a durable owner, no infinite unobserved wait.
5. Real video billable POST response unknown: no automatic retry regardless of HTTP 5xx/timeout; distinguish POST cost from read-only PollVideo retries.
6. FFmpeg first worker fail: report nonterminal RETRYING while one safe 15s retry is possible; confirmed exhausted retry terminal only with second failure plus named SFN FAILED.
7. No payment-ready sign-off until live ASL mismatch resolved, review queue and payment-order retention designed, and at least one controlled failure path proved with no duplicate provider POST.

**Visual references:** [architecture](architecture-high-level.svg), [detailed event handoffs](event-driven-handoffs.svg), [retry/failure matrix](generation-and-retries.svg), [watchdog detail](watchdog-architecture.svg). The first three are the primary engineering diagrams; none is Mermaid.
