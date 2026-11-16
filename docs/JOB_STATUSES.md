# AFL Video Platform — Current job and stage status contract

**Reviewed:** 16 November 2026 against `src/api/handler.py`, image/video/assembly workers, AWS Step Functions definitions and `frontend/app.js`. This is the authoritative **source-based** status reference; live fault injection remains outstanding. Historical status proposals are archived in older release notes and [resiliency roadmap](RESILIENCY_ROADMAP.md).

## Most important distinction

**`job.status` is the reference-image workflow status only.** `job.progress` in `GET /jobs/{jobId}` and top-level `progress` in `GET /jobs/{jobId}/videos` describe the end-to-end stage and outcome. The progress object is **computed read-only** from existing DynamoDB records and, where needed, read-only Step Functions `DescribeExecution`; there is no separately persisted `jobLifecycle` attribute.

```json
{
  "progress": {
    "currentStage": "STITCHING",
    "stageStatus": "RETRYING",
    "terminal": false,
    "outcome": null,
    "imagesCompleted": 3,
    "imagesTotal": 3,
    "videosCompleted": 3,
    "videosTotal": 3,
    "finalVideoReady": false,
    "message": "We are retrying the final video assembly. No new scenes are being generated."
  }
}
```

*Illustrative subset of the real projection. Not a recorded customer job.*

## Stages and nonterminal statuses

| `currentStage` | When reported | Typical `stageStatus` |
|---|---|---|
| `IMAGES` | Image workflow has not successfully finalised all 3 images | `PENDING`, `PROCESSING`, `RETRYING`, `REVIEW_REQUIRED`, or final `FAILED` |
| `VIDEO_1` | Scene 1 real video incomplete | `WAITING_FOR_ACTION`, `PENDING`, `PROCESSING`, `FAILED` (manual retry possible), `REVIEW_REQUIRED`, or terminal `BLOCKED`/`FAILED` |
| `VIDEO_2` | Scene 1 saved; Scene 2 incomplete | Same categories |
| `VIDEO_3` | Scenes 1 and 2 saved; Scene 3 incomplete | Same categories |
| `STITCHING` | All three real MP4s completed | `WAITING_FOR_ACTION`, `PROCESSING`, `RETRYING`, `REVIEW_REQUIRED` or terminal `FAILED` |
| `COMPLETED` | Assembly reports expected final MP4 key and signed final URL | `COMPLETED`; terminal |

`WAITING_FOR_ACTION` does **not always** mean the browser needs to push a button. It can also mean a backend-owned event/initial reservation has not yet arrived. `REVIEW_REQUIRED` is neither final customer failure nor proof of a provider rejection, and must never initiate an automatic refund.

## Reference-image states

The overall image `job.status`: `AWAITING_IMAGE_PIPELINE`, `GENERATING_IMAGES`, `COMPLETED`, `PARTIAL_SUCCESS`, `FAILED`. Individual `job.scenes.sceneN.status`: `PENDING`, `SUBMITTING`, `GENERATING`, `RETRY_WAIT`, `COMPLETED`, `FAILED`.

- Three scenes are generated in parallel with SFN Map `MaxConcurrency:3`; one confirmed Google HTTP 429 can put its own scene in `RETRY_WAIT` without resetting completed other scenes.
- Image `COMPLETED` means its reference PNG exists and checkpoint is recorded; **not** a completed video.
- `PARTIAL_SUCCESS` or image `FAILED` after finalisation project to `terminal:true, outcome:FAILED, failureCode:REFERENCE_IMAGES_FAILED`.
- Failed scene may expose safe diagnostic fields such as `errorCode`, `providerFinishReason`, `providerBlockReason`, bounded `providerBlockedCategories`, `providerCandidateCount`, `providerImageParts`, `providerTextOnly`. Available fields depend on the error.
- Image 429 `retryCount/maxRetries/nextRetryAt` are not Google video attempts.

## Real video scene states

| Scene status | Meaning | Safe follow-up |
|---|---|---|
| `NOT_REQUESTED` | Scene not reserved | Wait for backend-verified predecessor handoff |
| `REQUESTED` | DDB approval reserved; named execution not yet/just started | Reconcile named execution, never blindly resubmit |
| `PREPARED` | Worker has claimed request | Worker may submit once |
| `SUBMISSION_UNKNOWN` | Recorded **before** Google billable POST; acceptance unknown until interaction ID recorded | Not a failed Google result; never automatically repeat POST |
| `PROCESSING` | Google interaction ID known; async read-only polling | Keep existing operation, don't generate another |
| `COMPLETED` | Private scene MP4 written, expected S3 key checkpoint saved | Eligible for next verified handoff |
| `FAILED` | Provider-confirmed terminal failure or worker failure marker | Manual retry only if API confirms `retryAvailable:true`; not globally terminal unless policy blocked / attempts exhausted |
| `REVIEW_REQUIRED` | Projected for failed, timed-out, aborted or missing named SFN, or inconsistency | Reconcile; not refund-ready |

A worker `WAITING` polling response represents provider queue/progress, not a persisted scene status. Each video has `attemptNumber` and `maxAttempts:10`; `retryAvailable` requires confirmed provider terminal failure, attempts remaining, previous safe completion and no assembly. The public video may also include `retryBlockedReason`, `errorCode`, `providerFinalStatus`, `providerErrorCategory`, `interactionId`, `requestId`, `workflowStatus` and timestamps.

Video sequence `videoSequence.status=APPROVED` enables backend EventBridge progression; a confirmed policy refusal persists `videoSequence.status=BLOCKED`, `failureCode=CONTENT_BLOCKED`, failed scene and terminal timestamp. The public `sequence` in `GET /videos` exposes only `status,mode,approvedAt`; the full failure reason belongs in `progress`.

## Final FFmpeg assembly — corrected retry semantics

The `assembly` map in DynamoDB contains `QUEUED`, `PROCESSING`, `COMPLETED` or `FAILED` worker states. The API exposes additional **computed** statuses in `assembly_public` and overall `progress`:

| Durable worker record | Named `final-{jobId}` SFN | Public assembly/progress | Permanent? |
|---|---|---|---|
| `QUEUED` | Not yet/just started | `QUEUED` or `REVIEW_REQUIRED` after confirmed execution trouble | No |
| `PROCESSING` | Running | `PROCESSING` | No |
| `FAILED,retryCount=0` | RUNNING during 15s wait/one safe retry | `RETRYING` | **No** |
| `FAILED,retryCount=1` | RUNNING while failure settles | `RETRYING` | **No** |
| `FAILED,retryCount=1` | FAILED | `FAILED`, `STITCHING_FAILED` | **Yes** |
| `FAILED` | Missing, inaccessible, timed-out, aborted, inconsistent, or FAILED without recorded second failure | `REVIEW_REQUIRED` | **No** |
| `COMPLETED` with expected `videos/{jobId}/final.mp4` key | Final media marked complete | `COMPLETED`, final signed URLs | **Yes** |

**First failed FFmpeg attempt is NOT job failure.** Step Functions catches it, waits 15s and runs one extra invocation only if a failed worker checkpoint exists. If AWS cannot verify a final outcome, it reports review instead of guessing. This was fixed and deployed in guarded run [43835732389](https://github.com/rorygleeson/afl-video-platform/actions/runs/43835732389); source/offline/deployment verification is not a controlled live failure-injection pass.

## Definitive job outcomes

| `failureCode` / completion | Evidence for `terminal:true` | Customer experience |
|---|---|---|
| `REFERENCE_IMAGES_FAILED` | Image overall `FAILED` / `PARTIAL_SUCCESS` finalised | Explain that reference images could not be prepared |
| `CONTENT_BLOCKED` | Atomic content-policy `videoSequence.BLOCKED` from confirmed Google response | Explain generation guidelines; stop further scenes |
| `VIDEO_ATTEMPTS_EXHAUSTED` | Confirmed provider final failure at 10th authorised scene attempt | Explain attempts exhausted; stop |
| `STITCHING_FAILED` | **Second** persisted assembly worker failure + confirmed named SFN FAILED | Explain final assembly could not finish |
| `COMPLETED` | Expected final assembly key + signed URL in status projection | Show final MP4; allow download |

Definitive failure returns `terminal:true,outcome:FAILED,retryable:false,canStartNewJob:true,finalVideoReady:false`, `currentStage`, `stageStatus`, `failureCode` and a safe `message`. Other nonterminal progress has `terminal:false,outcome:null` and may lack the optional failure fields. Completed final video returns `terminal:true,outcome:COMPLETED,finalVideoReady:true`. There is **no implemented global `CANCELLED` outcome**.

## APIs and browser behaviours

- `GET /jobs/{jobId}` → `{job:{...,status,scenes,progress}}`.
- `GET /jobs/{jobId}/images` → `{jobId,status,scenes,images:[{sceneId,url}]}`.
- `GET /jobs/{jobId}/videos` → `{...,sequence,videos,assembly,progress}`.
- `GET /jobs/{jobId}/videos/{scene}` → `{jobId,video}`; refreshes signed video URLs without generation.
- `GET /jobs/{jobId}/assembly` → `{jobId,assembly}`; `assembly.url` and `downloadUrl` when completed.
- `GET /jobs/{jobId}/selfie` → signed uploaded-selfie URL for same-browser developer recovery.

The frontend status polls are read-only; AWS completion events initiate next scenes and stitching. When the backend reports a **definitive** failure, the customer sees a clean failure screen; during FFmpeg retry the customer remains on the progress screen. The same-browser developer resume requires the shared API key. Secure no-signup customer recovery is **not** implemented.

## What remains unproven

Full AWS live drift comparison, injected first-failure→retry-success and exhausted-retry scenarios, missed events/watchdog start, ambiguous provider acceptance, missing final S3 object, authenticated cross-device access and Stripe/refund integration. See [RESILIENCE_AUDIT_2026-11-16.md](RESILIENCE_AUDIT_2026-11-16.md) and [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md). Never infer production readiness from a read-only status response alone.

## Event-driven review states and UI status interpretation

A customer-facing `PROCESSING` stage does not guarantee Google has accepted the paid interaction; a newly persisted `SUBMISSION_UNKNOWN` deliberately precedes the POST. Likewise, a DDB next-scene reservation without a discoverable named SFN is **not** proof the next video is running; EventBridge may have delivered the prior success event even though the application reported `NEXT_SCENE_HANDOFF_NEEDS_REVIEW`. Read-only `GET` may project `REVIEW_REQUIRED` without any new persisted global lifecycle field.

First FFmpeg `assembly.FAILED` + RUNNING named SFN = `RETRYING` **nonterminal**; confirmed second failed worker + failed SFN = `STITCHING_FAILED` terminal. Missing/unknown SFN = nonterminal `REVIEW_REQUIRED`. Detailed transitions and handoff-to-status mapping: [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md). No payment/refund entitlements are defined by any of these job statuses.
