# AFL Video Platform — Event-driven architecture and failure boundaries

**Source reviewed:** 16 November 2026. **Authority:** event rules in `infrastructure/app/{image-video-start-events,video-sequence-events,handoff-watchdog}.yaml`, workflow definitions in `backend.yaml`, `google-video.yaml`, `assembly.yaml`, API handlers in `src/api/handler.py`, and `src/handoff_watchdog/handler.py`. This is **repository source analysis**, not a live deployed AWS `DescribeStateMachine` comparison or an end-to-end delivery test.

**Diagrams:** [main AWS architecture](architecture-high-level.svg) · [event delivery and deduplication](event-driven-handoffs.svg) · [failure and retry decision matrix](generation-and-retries.svg) · [watchdog detail](watchdog-architecture.svg). All are editable SVG, not Mermaid.

## 1. What makes the architecture event-driven?

A browser performs selfie upload, job creation and `POST /jobs/{jobId}/generate` **once**. After the image workflow starts, the backend owns automatic advancement. A successful Step Functions execution emits an `aws.states` status-change event to an EventBridge rule; the rule invokes API Lambda, whose handler validates the event and **persisted application checkpoints** before attempting to start the next named execution.

**A Step Functions success event is NOT an instruction to trust the previous provider call.** The API separately checks the expected execution identity, current DynamoDB state, output key and (for real video handoffs) S3 object. `SUCCEEDED` means the *workflow execution* finished; it does **not** alone prove the expected MP4 exists or that a later job was started.

There are **three distinct event flows** and a separate periodic image reconciler:

| Event | Rule and target | Source event | API / worker entry | Result after independent validation |
|---|---|---|---|---|
| Image workflow completion | `CompletedImageExecution` in `image-video-start-events.yaml` → `afl-app-dev-api` | `aws.states`, status `SUCCEEDED`, named image SFN | `lambda_handler → image_sequence_event()` | With `autoVideoConsent:true`, overall image job `COMPLETED` and all three image scenes finished, reserve Video 1/sequence |
| Real video completion | `CompletedGoogleVideoExecution` in `video-sequence-events.yaml` → same API | `aws.states`, status `SUCCEEDED`, Google scene SFN | `lambda_handler → video_sequence_event()` | Check exact request owner, scene `COMPLETED`, expected key and S3 HEAD; Scene 1→2; Scene 2→3; Scene 3→assembly |
| Watchdog startup | `EveryMinute` (misleading **logical resource ID**, **not a scheduled rule**) in `handoff-watchdog.yaml` → watchdog Lambda | `aws.states`, image SFN `RUNNING` | `start_for_image_execution()` | Validate job/consent/activation, start one named `watch-{jobId}` SFN |
| Separate periodic image repair | `afl-app-dev-reconcile-images` EventBridge schedule `rate(5 minutes)` | Time-based (not image SFN event) | Image worker `op:reconcile` | Scan bounded stale image jobs >45 minutes and finalise unfinished scenes **without Google reissue** |

### Important distinction: event-driven does not mean guaranteed immediate

Image/video EventBridge rules each use a target retry policy of **24 attempts maximum** and **3600-second maximum event age**; the watchdog-start event uses **12 attempts** and a **3600-second maximum event age**. These policies control **event delivery to the Lambda target**, not whether Google accepts the model POST or whether our Lambda's downstream Step Functions StartExecution succeeds.

The current rule templates show **no EventBridge target dead-letter queue (DLQ)** and no independently durable failed-event registry. There are **two distinct retry boundaries**: EventBridge's target `RetryPolicy` controls delivery/invocation acceptance; once a Lambda asynchronous invoke is accepted, **Lambda's own asynchronous queue** handles execution and function-error retries (typically two function-error retries by default, but effective configuration must be checked live). A handler that simply returns `NEEDS_REVIEW` is a **successful function execution** and triggers neither a Lambda function-error retry nor an EventBridge delivery retry. Raising a function error may invoke **Lambda's async retry**, not EventBridge's target-delivery RetryPolicy. See **event acknowledgement mismatch** under open risks below.

**AWS documentation checked:** [EventBridge Lambda targets](https://docs.aws.amazon.com/us_en/eventbridge/latest/userguide/eb-custom-bus-target-lambda.html), [Lambda async error retries](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html), [EventBridge delivery monitoring](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-monitoring-events-best-practices.html). These are public service semantics, distinguished from the application's source-configured values.

There are **no fixed 10s or 20s waits between video scenes**. EventBridge delivery, API invocation, S3 verification, Step Functions startup and Lambda/Google POST setup still require nonzero time. The separate **20-second WaitForVideo** inside each Google scene execution controls GET polling cadence.

## 2. Concrete successful path and checkpoint ownership

| Step | Authority / persisted evidence | Output | What is NOT yet proven |
|---|---|---|---|
| 1. Browser starts image job | `POST /jobs` with Idempotency-Key creates stable DDB job; S3 selfie verified; `autoVideoConsent` recorded | `AWAITING_IMAGE_PIPELINE` | `confirmPaidSequence` is **not** Stripe payment evidence |
| 2. Image SFN starts | `POST /jobs/{id}/generate` starts named execution `{jobId}` | `GENERATING_IMAGES`, then Map x3 | Browser closure **before** this POST can strand a created job |
| 3. Three Google image edits | Image worker owns conditional per-scene status; Google Vertex synchronous image POST and S3 PNG save | Three `scenes.sceneN.COMPLETED` | Synchronous provider timeout may be ambiguous; no blind image replay |
| 4. Image SUCCESS event | `image_sequence_event()` validates identity, consent, all three completed images and overall job `COMPLETED` | `videoSequence.APPROVED`, scene-1 reservation + named SFN | Lost event / uncertain StartExecution require reconciliation |
| 5. Video scene reserved | API `start_real_scene()` uses conditional `paidVideoApprovals.sceneN` reservation | Stable SFN execution name `real-{jobId}-sceneN` (or `-rN` on explicit retry) | DDB reservation does **not** prove SFN started |
| 6. Worker claim and paid submit | Google worker checks request ownership, writes `realVideos.sceneN.PREPARED`, then `SUBMISSION_UNKNOWN` **before** HTTP POST | Interaction ID (if returned) and `PROCESSING` | Lost POST response is not evidence Google rejected or did not bill |
| 7. Poll and persist result | `WaitForVideo(20s) → PollVideo`; Google GET on the saved interaction ID; S3 `videos/{jobId}/sceneN.mp4` stored and DDB `COMPLETED` | Scene MP4 checkpoint | Google workflow SUCCEEDED alone isn't enough |
| 8. Video SUCCESS event | `video_sequence_event()` checks current approval `executionName`, exact `requestId`, expected S3 key, S3 HEAD ContentLength >=128 | Start next original scene; or, after scene3, reserve `final-{jobId}` | Duplicate and superseded events must be rejected; missed event remains risk |
| 9. FFmpeg assembly | API `start_assembly()` checks all three MP4 objects, conditionally creates `assembly.QUEUED`, named assembly SFN | `videos/{jobId}/final.mp4` and DDB `assembly.COMPLETED` | First FFmpeg worker failure can retry once; unknown processing timeout may require review |
| 10. Customer GET | API read-only `overall_job_progress` and `assembly_public` produce signed GET URLs | Playback/download for signed URL validity 300s | Job entitlement, final object long-term delivery and payment/refund ledger are absent |

## 3. How duplicate, late and missing events are handled

**Duplicate image SUCCESS:** Repeated `image_sequence_event()` calls use the same sequence approval/scene reservation and named Step Functions execution. A previously reserved scene must not be charged twice because of repeat delivery. A *lost acknowledgement* can still leave the reservation stuck until reconciled.

**Duplicate video SUCCESS:** `video_sequence_event()` verifies event `stateMachineArn`, execution name regex, current `paidVideoApprovals[scene].executionName`, current `realVideos[scene].status=COMPLETED`, expected key and S3 HEAD. Old retried/superseded attempts are ignored. A next-stage reservation is checked with `DescribeExecution` before reporting `ALREADY_STARTED`. If reserved but named execution is missing/unknown, the code returns `NEXT_SCENE_HANDOFF_NEEDS_REVIEW`; it does **not** blindly issue another Google POST.

**Missing completed-event notification:** The isolated per-job watchdog is started **earlier** by an image SFN RUNNING event. It polls **one** job every **60 seconds**, and may replay an image/video SUCCEEDED event through the existing API **only** after the matching execution is confirmed SUCCEEDED and its stopDate is at least **120 seconds** old. The API revalidates and conditionally reserves downstream work.

**Watchdog limits:** one named `watch-{jobId}` execution; a **7200-second** maximum; activation timestamp excludes older jobs; a missed image RUNNING event means no job monitor was ever created. The watchdog can stop when a video is FAILED (even though the developer may later manually retry), or when a REVIEW branch is hit. It has no Google POST authority, no DDB write permission and no automated indefinite reconciliation. The separate 5-minute image reconciler remains always scheduled.

**Not fully handled:** EventBridge target delivery exhaustion (no DLQ), lost watchdog start, >2h jobs, late manual retry after watchdog stopped, unknown provider acceptance, unverified assembly outcome, and DDB reservation left without corresponding execution. These are risk register items, **not** “implemented retry features.”

## 4. Failure and retry ownership by component

| Operation | Safe automatic retry? | Owner | Durable gate and failure outcome |
|---|---|---|---|
| Browser S3 POST upload | Limited browser retry on network `TypeError` | Browser | Job creation independently HEAD checks uploaded object; 5-minute presign validity |
| Reference image paid Google POST | **Only confirmed HTTP 429** (bounded, max 8 total / 600s) | Image worker + image SFN Wait | Per-scene `RETRY_WAIT`; other ambiguous POST does not get a new paid submission |
| Paid real-video Google POST | **NO automatic retry** | Google worker/scene SFN | Write `SUBMISSION_UNKNOWN` **before** POST, named attempt, no Submit Task Retry |
| Video interaction GET | **Yes, bounded read-only retries** | Google scene SFN `PollVideo` | 20s Wait between normal polls, TaskFailed up to 3 additional GETs 10/20/40s |
| Confirmed Google terminal failure | **Manual only**, max 10 total separately authorised attempts | API developer retry handler | Match provider terminal failure, completed prior SFN, original source image, no assembly |
| Content-policy refusal | **No** | Google worker → DDB sequence BLOCKED | Final `CONTENT_BLOCKED`; no more scenes |
| SFN SUCCESS notification | Target retry policy and guarded replay possible | EventBridge + API + watchdog | Recheck DDB/S3/execution, not blindly start Google |
| FFmpeg first failed worker attempt | **Yes, once** | Assembly SFN `Catch → Wait15 → RetryConfirmedAssemblyFailure` | `STITCHING/RETRYING` nonterminal while named SFN RUNNING |
| Ambiguous FFmpeg processing timeout | **No** | Assembly SFN/API/manual review | Nonterminal `REVIEW_REQUIRED`, no second writer |
| Confirmed exhausted FFmpeg retry | **No further retry** | API status projection + named SFN | `STITCHING_FAILED` terminal only if second recorded failure and SFN FAILED |

For full timeouts, backoff and source references see [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md). **Retry counts are per operation**, not a promise to attempt an entire customer video 10 times.

## 5. Exact gaps found in this source review

| Ref | Severity / confidence | Observation | Engineering acceptance criteria |
|---|---|---|---|
| **ED-01** | **HIGH / SOURCE-CONFIRMED** | In `google-video.yaml`, `Claim.Parameters.Payload.requestId.$` is **`$.Execution.Name`**, whereas `Submit`, `PollVideo` and `Finalize` use **`$$.Execution.Name`**. The Claim path addresses input rather than Step Functions context. This repo template cannot be assumed identical to currently deployed live state machine; previous fix workflow contains a safety assertion that would reject this source. | Independently compare live `DescribeStateMachine` and rendered template; verify Claim receives stable execution name and current executions succeed. Fix source and guarded deployment in a separate code change after live comparison, with an offline path assertion. **No Google paid tests needed.** |
| **ED-02** | **HIGH / SOURCE-CONFIRMED** | In `video_sequence_event()`, `HANDOFF_UNKNOWN` for Scene 3 assembly or a reserved next-video execution with no observed SFN can be returned as a **normal Lambda return object** (`ASSEMBLY_HANDOFF_NEEDS_REVIEW`, `NEXT_SCENE_HANDOFF_NEEDS_REVIEW`). Since the Lambda handler returned successfully, the application-level failure does not trigger Lambda's asynchronous function-error retry, and EventBridge's delivery policy is not an application business-retry mechanism. | Separate EventBridge target-delivery failure, Lambda asynchronous function error, and successful Lambda invocation with business-level `NEEDS_REVIEW`. Record/alert review durably; simulate each boundary plus watchdog missing/stopped, without another model POST. |
| **ED-03** | **HIGH / SOURCE-CONFIRMED** | Neither completion EventBridge rule nor watchdog start rule configures a DLQ. Per-job monitor starts exclusively from image SFN RUNNING event, ends at 7200s and can stop on manual-retryable video failure. | Add dead-letter/operational review path and independent missed-start remediation, measure unhandled jobs after 2h, test lost start + post-failure manual scene retry. |
| **ED-04** | **HIGH / SOURCE-CONFIRMED GAP** | DDB paid-scene condition fences known `videoSequence.BLOCKED` but does not atomically guard all possible terminal image/assembly state transitions. API performs an earlier read check, so concurrent terminalisation must be tested. | Durable terminal fencing on paid admission; deterministic race tests showing no post-terminal billable StartExecution. |
| **ED-05** | **HIGH / SOURCE-CONFIRMED GAP** | Unknown Google paid POST may remain without an interaction ID, so a watchdog cannot safely read it back or prove no charge. `SUBMISSION_UNKNOWN` does not distinguish request prepared vs request accepted externally. | Bounded explicit reconciliation and operator review with provider correlation; never infer rejection/refund from lack of response. |
| **ED-06** | **MEDIUM / SOURCE-CONFIRMED GAP** | Current status GET reports expected final S3 object key and signs URL from DDB; it does not independently HEAD-check the final object on each completed customer GET. Seven-day job TTL and image lifecycle are separate from `videos/` retention. | Define durable delivery proof, correct cleanup schedule and explicit signed-link retry behaviour, test deleted final video and expired URL. |

**Do not treat this as a live incident report:** ED-01 is a confirmed **source template inconsistency**; the running AWS machine may differ. ED-02 is an architectural acknowledgement/recovery gap, not evidence a customer job was lost. The work in this document is review/documentation only. See [resilience audit](RESILIENCE_AUDIT_2026-11-16.md) for existing RA findings and their statuses.

## 6. Recommended observability and runbook (DESIGN, not implemented)

A payment-ready job should leave a durable, non-sensitive reconciliation record with: `jobId`, stage/scene, attempt number, hashed provider correlation, named execution ARN, DDB checkpoint, last proven S3 artifact, firstSeenAt, lastCheckedAt, review reason, owner and next action. Avoid logging raw selfies, Google response bodies or bearer credentials.

Proposed metrics: `ImagePipelineStale`, `WatchdogNotStarted`, `NextSceneReservationMissingExecution`, `UnknownPaidSubmissionAge`, `VideoHandoffStalled`, `AssemblyRetryExhausted`, `FinalMediaMissing`, `CompletionEventToStartSeconds`, `Google429Rate`. Review queue alarms should distinguish **temporary recovery** from **definitive failure**.

For each stuck job, the runbook should check **DynamoDB owner**, then **named SFN DescribeExecution**, then **provider interaction ID if known**, then **S3 HEAD on expected output**. Never reissue a Google POST from `SUBMISSION_UNKNOWN` without independent proof the first was not accepted. A failed first FFmpeg worker is not terminal until the bounded safe retry is settled.

## 7. Evidence and change-control gate

**Verified here:** current GitHub source and IaC definitions, deterministic job identifiers, existing status projections, source-configured retry/poll/timeout limits and watchdog predicates. **Not verified here:** live AWS deployed Step Functions definition/permissions, provider-side actual acceptance, live event delivery latency, independent cross-region visibility, real payment billing/refund, or fault-injection recovery.

The canonical API endpoint list remains [API_ENDPOINTS.md](API_ENDPOINTS.md); status semantics in [JOB_STATUSES.md](JOB_STATUSES.md). This document intentionally adds **event-specific detail**, without redefining the 20 API route schemas. No AWS resources or model calls were changed.
