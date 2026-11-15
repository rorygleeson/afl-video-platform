# Pre-payment resilience source audit — 2026-11-16

**Status:** SOURCE VERIFIED findings; **live AWS runtime and full end-to-end customer experience NOT VERIFIED**. Documentation-only audit. No code changes, deployments, paid requests or cleanup enabled.

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
