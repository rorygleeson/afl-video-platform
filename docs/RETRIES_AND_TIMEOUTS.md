# AFL Video Platform — Retry, Timeout and Failure Audit

> **RE-AUDIT WARNING — 16 NOVEMBER 2026:** Actual source-defined assembly state machine includes `Stitch → WaitBeforeConfirmedRetry(15s) → RetryConfirmedAssemblyFailure` when the first worker durably records `assembly.FAILED`. The older retry matrix describing assembly as *one-shot, no Retry/Catch* is outdated. The API currently treats **any** persisted `assembly.FAILED` as terminal, even during this legitimate retry window; see RA-01 in [full resilience re-audit](RESILIENCE_AUDIT_2026-11-16.md). Do not conflate `failed first worker attempt` with `permanently failed job` or automatic refund eligibility. The Google video worker still has **no automatic paid Submit POST retry**; only Google status GET PollVideo has retry. No deployed changes were made for this re-audit.
>

**Audit date:** 2026-11-15 · **Verification:** SOURCE VERIFIED; guarded AWS handoff/resource deployments VERIFIED from GitHub Actions logs, but individual timeout/runtime/SDK settings **not independently live-audited**.  
**Sources:** `src/api/handler.py`, `src/image_worker/handler.py`, `src/image_worker/google_direct.py`, `src/google_video_worker/handler.py`, `src/assembly_worker/handler.py`, `frontend/app.js`, `infrastructure/app/backend.yaml`, `infrastructure/app/google-video.yaml`, `infrastructure/app/assembly.yaml`.  
**Related:** [API_ENDPOINTS.md](API_ENDPOINTS.md), [JOB_STATUSES.md](JOB_STATUSES.md), [RESILIENCY_ROADMAP.md](RESILIENCY_ROADMAP.md).

> The values below are confirmed in current repository source and CloudFormation. Do not represent them as measured live AWS values until compared against deployed resources and effective SDK settings. **Do not rerun paid Google requests just to test retry logic.**

**Post-release verification (2026-11-15):** Source and guarded AWS deployment logs confirm the Stage 1–3 backend handoff. [Images → Video 1](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689396745), [Scene 3 → assembly](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689754297), [browser read-only after image start](https://github.com/rorygleeson/afl-video-platform/actions/runs/43690121205). Source and offline tests verified; a fresh live unattended full job and failed-event injection were **not** executed. This addendum overrides older browser-driven descriptions.

## Operation retry and timeout matrix

| Operation | Source-configured timeout | Delay / backoff | Maximum attempts | Failure behaviour |
|---|---|---|---|---|
| Selfie presign (`POST /uploads`) | API Lambda 15s; signed form **300s validity** (not a request timeout) | No application retry for presigning | 1 request per invocation | Returns HTTP error; no AI charges; can issue new presigned form |
| Selfie S3 browser POST | Browser `fetch()`: no explicit application deadline found | **700ms, 1400ms** delays for browser `TypeError`; no exponential backoff beyond these | **3 total** (original + 2 retries) | Explicit non-2xx HTTP response is **not** automatically retried; ambiguous network errors may have uploaded successfully |
| Job creation | API Lambda 15s | No client replay on ambiguous response in current form; stable idempotency key supports server replay if retained | 1 attempt in current UI flow | Conditional DDB insert; same key/input returns existing job, conflicting input 409 |
| Start image workflow | API Lambda 15s; Step Functions **1800s** total | No general retry; named execution `jobId` | One workflow name per job | Duplicate named execution protected; ambiguous handoff requires checking job/SFN |
| Image 1 | Image Lambda **600s**; Google HTTP POST **240s** socket | Explicit HTTP 429 only: `Retry-After` (+ jitter), else approx 15/30/60/120/180s cap (+0–3s jitter); Step Functions Wait | **8 total submissions** within 600s retry window | Other failures caught and scene marked `FAILED`; ambiguous Google POST **not resubmitted** |
| Image 2 | Same as Image 1 | Same | Same | Runs in parallel with other image scenes (Map permits 3 branches); its own 429 wait/retry is independent |
| Image 3 | Same as Image 1 | Same | Same | Runs in parallel with other image scenes (Map permits 3 branches); its own 429 wait/retry is independent |
| Google image output parse/S3 write/DDB update | Image Lambda 600s | No application-level repeat of accepted model generation | No safe automatic second model submission | If Google returned image but storage/state fails, process can fail; must reconcile S3/DDB before new chargeable request |
| Video 1 Google submission | Video Lambda **300s**; HTTP POST **150s** socket; video SFN **3600s** | **No Submit Task retry** | **1 submit per authorised attempt**; max 10 separately confirmed scene attempts | Pre-marked `SUBMISSION_UNKNOWN` prior to POST; unknown acceptance requires reconciliation |
| Video 2 Google submission | Same as Video 1 | SFN adds **10s initial scheduling Wait** for scene 2 | Same | AWS completion event advances only after video 1 recorded `COMPLETED` and S3 object verified |
| Video 3 Google submission | Same as Video 1 | SFN adds **20s initial scheduling Wait** for scene 3 | Same | AWS completion event advances only after video 2 recorded `COMPLETED` and S3 object verified |
| Google video polling | Video Lambda 300s; HTTP GET **150s** socket | **20s** wait between polls; Step Functions `States.TaskFailed` Retry: **10s, 20s, 40s** on retries (BackoffRate 2) | 1 initial + **3** retries per failed PollVideo task invocation; repeated Wait/Poll loop bounded by 3600s workflow | GET retries are read-only. Unexpected/invalid provider state can result in SFN failure/review; does **not** authorise resubmitting POST |
| Google video output / GCS read | Worker 300s; GCS GET socket **150s** | No application-level generation resubmission | 1 per worker poll/save path unless safe read-only polling retry | Output can be stored on S3; failed DDB update requires reconciliation, not new Google generation |
| General S3/DynamoDB calls | Bounded by invoking Lambda timeout; SDK timeouts/retry policies not explicitly pinned in all workers | SDK defaults, **effective values not established by source-only audit** | Unknown effective SDK attempt count | S3/DDB errors often propagate to catch/failed/review; paid POST must not repeat as recovery |
| Final assembly | Assembly Lambda **600s**, 2048MB RAM / 2048MB ephemeral; Step Functions one Stitch Task | No Step Functions Retry/Catch configured | One named assembly SFN execution per job | On error worker tries `PROCESSING → FAILED`; preserves source clips; no automatic recovery |
| FFprobe validation | subprocess timeout **15s** per probe | None | 1 each | Invalid media/errors cause assembly failure |
| FFmpeg normalisation | **150s per clip** | None | 1 per scene | Fails assembly; input scene MP4s remain in S3 |
| FFmpeg final concatenate | **60s** | None | 1 | Fails assembly; inputs remain in S3 |
| Browser image/video status poll | Every **5s** normally; **15s** after polling error | Fixed intervals; read-only | Continuous while active/visible | Browser stopping polling does not stop AWS orchestration after image job start; explicit retry controls remain separate |
| Developer overall job status GET | Every **2s** while diagnostics expanded and browser visible | Read-only single-flight | Continuous while open/visible | `GET /jobs/{jobId}` includes `job.progress` derived from existing records; never creates work |
| Video manual scene retry | API Lambda 15s + new SFN/worker deadlines | Separate user confirmation; **not automatic** | **10 total attempts** per video scene | Only confirmed provider terminal failures and safe prior workflow completion eligible |
| Stalled image watchdog | Invoked by EventBridge every **5min**; image worker Lambda 600s | No model retry | Scans up to 10 pages × 100 evaluated records | After **45min** image job threshold, marks incomplete records failed; preserves completed ones |

**Important detail:** the image worker catches a failed per-scene invocation, writes failed state without blocking other parallel Map branches; it is *not* an automatic “resume from last completed scene” service. The overall image Step Functions deadline is 1800s; concurrent image branches are individually subject to 600s Lambda timeouts and may require additional Wait/retry cycles. The workflow timeout remains binding.

## Lambda and state-machine configuration snapshot (SOURCE, not live proof)

| Function | Runtime / handler | Memory | Lambda timeout | Notes |
|---|---|---:|---:|---|
| `afl-app-dev-api` | Python 3.13, `handler.lambda_handler` | 256 MB | 15s | HTTP proxy and EventBridge sequence handler |
| `afl-app-dev-image-worker` | Python 3.13, `handler.lambda_handler` | 1024 MB | 600s | Image generation and watchdog |
| `afl-app-dev-google-omni-worker` | Python 3.13, `handler.lambda_handler` | 1024 MB | 300s | Direct Google Interactions API |
| `afl-app-dev-assembly` | Python 3.13, `handler.lambda_handler` | 2048 MB | 600s | FFmpeg and ffprobe layer; 2048MB ephemeral |
| `afl-app-dev-video-worker` | Python 3.13, `handler.lambda_handler` | 256 MB | 50s | Legacy Google authentication-only worker |

State machines:
- `afl-app-dev-scene1-pilot` **STANDARD**, `TimeoutSeconds:1800`: `Prepare → ParallelScenes` (**Map MaxConcurrency 3**, three image branches may execute concurrently) → `Finalize`; Catch to `RecoverJob`. Each Map iteration: `Generate → AssessImageResult`, confirmed 429 `WaitForGoogleCapacity → Generate`, otherwise `SceneCompleted` or `MarkSceneFailed`.
- `afl-app-dev-google-omni` **STANDARD**, `TimeoutSeconds:3600`: `PaidGate → Claim → SubmissionSchedule` (scene 2 Wait 10s/scene 3 Wait 20s) `→ Submit → WaitForVideo(20s) → PollVideo → InspectVideo`, loop while waiting, terminal to `Finalize`, unknown status to `UnexpectedStatus` Fail. **Only PollVideo** has TaskFailed retry (three retries with backoff 2).
- `afl-app-dev-assembly` **STANDARD**: single `Stitch` task; no explicit Retry/Catch or state-machine TimeoutSeconds in source definition.
- **Two isolated successful execution EventBridge rules:** image state machine → API `image_sequence_event()` (job consent and 3 completed images required) → Video 1; Google video state machine → API `video_sequence_event()` (current attempt/S3 completion verified) → Video 2, Video 3 or final FFmpeg assembly after Scene 3. Both event targets specify **24 attempts maximum / 3600-second maximum event age** in CloudFormation; neither template defines a DLQ. Missing delivery/ambiguous handoff still lacks a periodic backend reconciler.
- EventBridge `afl-app-dev-reconcile-images` runs every 5 minutes; returns incomplete image jobs to terminal state rather than resubmitting paid images.

**No explicit Lambda reserved concurrency settings were found in the four principal source templates**; this must be verified live. AWS-managed event retry and SDK backoff defaults need effective configuration checks; don't assume “no retry” means no provider/network retries in every underlying library.

## Image retries — exact source behaviour

**Outbound endpoint:** `POST https://aiplatform.googleapis.com/v1/projects/afl-video-platform/locations/global/publishers/google/models/gemini-3-pro-image:generateContent`

In `src/image_worker/google_direct.py`:

- `MAX_ATTEMPTS = 8`; `MAX_RETRY_WINDOW_SECONDS = 600`.
- Attempt counter increments only after conditional DynamoDB ownership claim from `PENDING` or `RETRY_WAIT`.
- `GoogleHTTPError(429)` with a concrete HTTP rejection is the **sole automatic paid submission retry condition**. `Retry-After` supports numeric seconds or HTTP-date; if absent delay is `min(180,15*2^(attempt-1)) + ceil(U(0,3))`. With Retry-After, delay `max(1,ceil(retry_after+U(0,1)))`.
- Retry is allowed only if next attempt < 8 and delay fits the remaining window. It persists `RETRY_WAIT`, retry counters, next retry time. Step Functions does the Wait, not a sleeping Lambda.
- HTTP 401/403, 400/422, other HTTP failures, network timeout, unreadable response, invalid output or S3/DDB failure **do not automatically trigger another Google POST**.
- For ambiguous failure after model POST, stores `GOOGLE_SUBMISSION_UNKNOWN` as `errorCode` and fails. No operation ID is available from the synchronous image generation endpoint; stronger reconciliation/accounting is a design gap.

**Cost safeguard:** no general retry on timeout/500 after a potentially accepted request. **Customer reliability gap:** some recoverable infrastructure errors permanently stop the scene even though the paid Google operation may have completed. Resolution requires independent output verification or safe confirmed rejection before reissue.

## Video retries and polling — exact source behaviour

**Outbound endpoints:**
- `POST https://aiplatform.googleapis.com/v1beta1/projects/afl-video-platform/locations/global/interactions`
- `GET https://aiplatform.googleapis.com/v1beta1/projects/afl-video-platform/locations/global/interactions/{interactionId}`
- If configured for GCS delivery: authenticated `GET https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encodedKey}?alt=media` after validating expected private output prefix.

Worker `src/google_video_worker/handler.py` uses `urlopen(... timeout=150)` for Google Interactions and GCS. The Google HTTP socket timeout is **not** the overall model-generation deadline; Step Functions polls asynchronously over a **3600-second** workflow.

The critical flow is **DDB `PREPARED → SUBMISSION_UNKNOWN` BEFORE the billable Google POST**. If the Google request acknowledgement fails, it is unsafe to assume rejection; it must be reconciled. After Google supplies a safe interaction ID, DDB transitions to `PROCESSING` and persists `interactionId`. Read-only GET polling until a terminal state then saves `videos/{jobId}/sceneN.mp4`.

- No automatic Retry on `Submit` in video SFN.
- `PollVideo` TaskFailed: `MaxAttempts:3`, `IntervalSeconds:10`, `BackoffRate:2` = initial call + up to 3 extra read-only attempts at 10/20/40 seconds, after which state machine fails without a catch on that task.
- Normal `WAITING` provider state loops `WaitForVideo(20s) → PollVideo` until success, provider failure, unexpected status, or 3600s execution deadline.
- After a provider-confirmed terminal failure, the operator/developer may call scene-specific retry; API requires `confirmRetryScene`, Idempotency-Key, previous workflow `SUCCEEDED`, matching source image, no assembly, and `attemptNumber < 10`. Previous attempt archived in history.
- Failed or uncertain AWS workflows never independently prove Google refused or stopped billing. No blind provider POST retry.

### Nested retry example

For *one* Google video scene:
1. Normal SFN read-only GET polls repeat every 20 seconds.
2. Each failed `PollVideo` task may perform **4 Lambda GET invocations total** (initial + 3 retries).
3. Underlying Google HTTP GET uses a single `urllib` request per worker invocation, aside from auth provider library behaviour.
4. If Google reports terminal failure, the user may separately trigger another **entire paid attempt** up to the scene total of 10.

Thus a “3 retries” polling setting does not mean three Google generations, and 10 paid manual attempts does not mean 10 automatic retries. Still audit auth/SDK defaults and multi-customer admission before production.

## Assembly retries and duration

`src/assembly_worker/handler.py` checks all 3 real video DDB records and expected S3 objects, reserves `assembly`, downloads each MP4, probes format, transcodes/normalises 3 clips to 720×1280 at 30 fps, creates concat manifest, uses stream-copy concat, probes the final output, uploads `videos/{jobId}/final.mp4`, verifies its existence, then conditionally persists `COMPLETED`.

- Each source MP4 has 75MiB maximum file limit, final MP4 <=250MiB.
- `ffprobe` 15s subprocess deadline; each FFmpeg normalisation up to 150s; concat 60s.
- A transient S3 read or DDB update failure can leave `PROCESSING` or `FAILED`; current SFN has no automatic Retry/Catch.
- Input MP4s remain safely in S3 when assembly fails, so an idempotent assembly-only recovery is plausible without another Google call.
- **Updated (2026-11-15):** successful Scene 3 AWS execution now initiates assembly through the existing EventBridge → API handler. Frontend assembly polling is read-only, so closing the browser after image workflow initiation does not by itself prevent FFmpeg handoff. **Remaining gap:** lost event, unknown StartExecution, failed/stalled FFmpeg and missing assembly-only watchdog; no automatic FFmpeg retry is configured.
- Current frontend stitching progress bar is a **10-second estimate**, not runtime FFmpeg percent. Its estimate cannot be used to detect stuck jobs.

## Retry policy classification — findings, not implemented fixes

| Failure | Current action | Safe recommended direction |
|---|---|---|
| Explicit image POST 429 | Retry with bounded jitter/Retry-After | Preserve current budget; test throttle and exhaustion |
| Image POST timeout / lost response | Mark unknown/failed, **no resubmit** | Reconcile billable outcome or require safe manual resolution; never infer rejection |
| Video POST accepted but interaction ID lost | `SUBMISSION_UNKNOWN` | Correlate with provider logs, operations or configured output; protect from duplicate submission |
| Video HTTP 5xx or network after POST | Unknown/review; no generation retry | Only retry if nonacceptance is proven; no blind paid retry |
| Video polling GET 5xx / network | SFN read-only task Retry up to 3 | Classify errors, consider jitter and longer backoff if needed |
| Provider terminal failed | Scene `FAILED`, manual retry eligible | Bounded automatic recovery only after definitive provider failure and cost policy review |
| Missing scene MP4 after recorded completion | May block next scene or assembly | Verify/reconcile durable storage; do not regenerate blindly |
| DDB failure after Google completed | Workflow may fail; review required | Inspect output/SFN history before reopening scene |
| Step Functions timeout | Review required, no new POST | Query interaction status; don't infer provider stopped |
| Assembly transient failure | Assembly fails, no retry | Server-owned bounded assembly-only retry; preserve clips |
| Browser closure | AWS progression can continue after the image SFN has started; browser-owned upload/job-start still needs connectivity | Add anonymous secure same-job recovery and lost-event reconciliation; stitching trigger is already AWS-owned |
| Duplicate paid submit/retry | Conditional DDB admission guards | Preserve stable names, verify race testing |
| Job expires during delivery/refund review | DDB TTL and S3 lifecycle may remove state | Separate paid-order retention/lifecycle; never TTL-away unresolved order |

## Failure test matrix — NOT EXECUTED IN THIS AUDIT

These tests are defined, **not passed**:

- [ ] Google image HTTP 429, Retry-After, max 8 attempts, max 600s window, backoff+jitter and successful resume with **all 3 images running concurrently**; verify one branch's backoff never suppresses another branch's completion.
- [ ] Image Google 500, network timeout, accepted response lost, malformed output, S3 save fail, DDB save fail, Lambda timeout.
- [ ] Video Google POST 429/500/502/503/504, lost interaction ID, timeout, stuck operation, terminal failed, invalid polling response.
- [ ] Video polling GET temporary 5xx recovery and MaxAttempts exhaustion; no generation resubmission.
- [ ] Step Functions fail/timeout and async EventBridge duplicate/reordered event handling.
- [ ] Concurrent scene retry and original running, duplicate API calls, multiple customers, video 2 failure after video 1 success.
- [ ] Assembly failed S3, DDB, FFmpeg, Lambda timeout; preserve all 3 original MP4s.
- [ ] Browser refresh/close, missing recovery link, customer downloads after signed URL expiry, job TTL/lifecycle retention.
- [ ] Full job exceeds expected duration; deterministic terminal/reconciliation and no premature refund.

Use mocked Google response fixtures and isolated AWS fakes for most cases. Current Stripe/refund tests are **design-only**, not executed. Record actual test outputs in `docs/TEST_RESULTS.md` when run.

## Outstanding research and live cross-check

- [ ] Confirm Google's **current** documented `generateContent` and Interactions API retry/poll/quotas/idempotency behaviour against published docs, explicitly marking what is documented versus inferred; source-only code review is not external-provider verification.
- [ ] Verify exact deployed Lambda `Timeout`, `MemorySize`, reserved concurrency, event retry controls and code hashes; compare active CloudFormation template with GitHub source.
- [ ] Verify API Gateway integration timeout, stage throttle/quota, `x-api-key` usage plan, CORS, EventBridge delivery/retry/DLQ, Step Functions execution definitions.
- [ ] Check effective botocore retries and connection timeouts under deployed Lambda environment; don't confuse application-defined retries with SDK defaults.
- [ ] Verify S3 lifecycle including `videos/` retention, DDB TTL and available point-in-time recovery, logging/monitoring and customer retention controls.
- [ ] Check model failure billing and Google/Stripe price economics before approving automatic retry budgets.

**Overall conclusion (updated 2026-11-15):** The deployed source now has browser-independent AWS stage progression **after the browser initiates image generation**, including successful image completion → Video 1, per-video progress to Video 3, and Scene 3 completion → final assembly. Source is conservative about duplicate expensive Google POSTs, but this is **not** a fully reconciled or payment-ready delivery system. Next phase: missed EventBridge/StartExecution reconciliation, safe provider uncertainty handling, assembly-only recovery, anonymous job restore and payment verification. Preserve prompts and models.

## Image no-content diagnostics — additive observability only (15 November 2026)

For a successful HTTP response that contains no usable generated image,
`src/image_worker/google_direct.py` captures only provider enum categories
(`finishReason` and `promptFeedback.blockReason`), safely bounded candidate/
image-part counts, blocked rating category names, and a text-part-present
flag. The response body, provider free-text explanations and user media are
never persisted or shown. Existing `GOOGLE_NO_IMAGE_CONTENT` may now be
categorised more specifically as `GOOGLE_PROMPT_BLOCKED` or
`GOOGLE_IMAGE_SAFETY_BLOCK` when Google's *response itself* explicitly
supports the classification. Other image-free responses retain
`GOOGLE_NO_IMAGE_CONTENT` or `GOOGLE_NO_CANDIDATES`.

No change to provider requests, prompts, model, Step Functions transitions,
timeouts, number of image scenes, backoff, 8-attempt / 600-second budget, 429
retry-only policy, or the rule that ambiguous submissions must never be
blindly retried. This does not recover response details missing from older jobs.

## Per-job missed-handoff watchdog (reviewed 2026-11-16)

| Mechanism | Frequency / bound | Allowed action | What it does **not** do |
| --- | --- | --- | --- |
| Isolated handoff watchdog | EventBridge image-workflow `RUNNING` starts one named SFN; **60-second Wait** between Lambda checks; **2-hour** total deadline | Read one DDB job, describe named execution, and replay a **verified** successful completion event (only after **120 seconds** since stop) into existing guarded API | No idle cron, no scan, no paid Google retry, no automatic restart of an in-flight image/video, no stage-age timeout |
| Pre-existing image reconciliation | Scheduled **every 5 minutes**, regardless of active jobs; approximately **45-minute** stalled image threshold | Terminalise unfinished image jobs as defined by existing image worker | Does not restart paid image submissions; is **not** the isolated handoff watchdog |

**Do not confuse three independent numbers:** 60 seconds = watchdog check cadence; 120 seconds = successful-execution settlement age before potential handoff replay; 7,200 seconds = overall watchdog monitoring lifetime. A Google video taking eight minutes is not failed simply for remaining in progress across several checks. Monitoring ends or requires review if a failure/uncertain paid submission/unknown reserved assembly occurs; no automatic safe recovery is claimed for all those cases. Source: `infrastructure/app/handoff-watchdog.yaml` and `src/handoff_watchdog/handler.py`. No fresh live AWS deployment or end-to-end test accompanied this documentation amendment.


## Permanent Google content-policy rejection — terminal job exit (16 November 2026)

A confirmed Google video interaction with terminal `failed` status and an explicit
`SAFETY_OR_POLICY` classification now terminates the **whole video job**.
The Google worker performs a single conditional DynamoDB update, owned by the
in-flight scene's request ID and PROCESSING state, which both marks that scene
FAILED and persists `videoSequence = { status: BLOCKED, failureCode:
CONTENT_BLOCKED, failedScene: sceneN, terminalAt: UTC timestamp, ...existing consent }`.
There is no new Google POST, no automatic retry, and no deletion of existing
reference images or successful video clips.

The existing read-only `GET /jobs/{jobId}` and continuous
`GET /jobs/{jobId}/videos` projections expose
`progress.stageStatus = BLOCKED`, `progress.terminal = true`,
`progress.outcome = FAILED`, `progress.failureCode = CONTENT_BLOCKED`,
`progress.failedScene`, `progress.canStartNewJob = true`,
`progress.finalVideoReady = false`, and a safe customer-facing message.
A successfully stitched final MP4 exposes `terminal = true` and
`outcome = COMPLETED`. This is the contract future payment/reconciliation
code should inspect; no payment decisions or refund logic have been added.

The API and Google worker reject further scene submissions/retries and final
assembly on BLOCKED jobs. EventBridge handoffs already require the video
sequence to remain APPROVED and a completed scene MP4; stale events therefore
cannot advance a blocked job. The frontend only renders the backend outcome
and offers Start new video; reloading the job reports the same outcome.

Other provider failures, ambiguous submissions and workflow timeouts remain
their existing non-content-blocked states. Do NOT mark those permanently
CONTENT_BLOCKED without authoritative evidence from Google.


## Extended terminal job exit (16 November 2026)

The backend now projects `terminal=true, outcome=FAILED` using **only durable,
definitive evidence** from the existing DynamoDB records:

- `REFERENCE_IMAGES_FAILED`: image worker finalised `FAILED` or
  `PARTIAL_SUCCESS` (includes existing stale-image recovery);
- `CONTENT_BLOCKED`: Google policy refusal atomically wrote videoSequence
  `BLOCKED`, unchanged from the earlier release;
- `VIDEO_ATTEMPTS_EXHAUSTED`: a scene has a confirmed Google terminal
  failure, persisted `FAILED` record and at least ten authorised attempts;
- `STITCHING_FAILED`: FFmpeg worker durably recorded assembly `FAILED`.

Each returns a safe `message`, stage, failure code, no retry eligibility and
`canStartNewJob=true`. A verified assembled final MP4 returns
`terminal=true, outcome=COMPLETED`. Both job and video status GETs expose
the same `progress`; no new provider call or job-wide DynamoDB write occurs.

**Critical distinction:** `REVIEW_REQUIRED`, `SUBMISSION_UNKNOWN`, Google
capacity/transient failures with remaining retries, API handoff uncertainty
and a timed-out Step Functions execution are **not** final or automatic refund
triggers. No mechanism now manufactures a permanent failure merely from time
elapsed or status polling. Backend start/retry/assembly/EventBridge paths
refuse known definitive failure records. The customer UI hides progress and
previews only after `terminal=true/outcome=FAILED`.

This closes the clear-outcome gap for **known** unrecoverable failures, but
does not close the recovery/reconciliation gap for indefinite unknown work.
Before payments, independently audit staleness thresholds and reconcile
uncertain provider acceptance/handoffs; never blindly resubmit a chargeable
request. Separate media TTL from transaction audit retention. The existing
seven-day DynamoDB TTL is not a sufficient payment history store.


## Immediate backend scene-to-scene handoff — 16 November 2026

**Updated active workflow:** After the previous real MP4 is durably COMPLETED and S3 verified, EventBridge invokes the existing API next-scene reservation and named Step Functions execution. The Google workflow now routes `Claim → Submit` directly for **Scenes 1, 2 and 3**. The older `WaitScene2 = 10s` and `WaitScene3 = 20s` states, and their `SubmissionSchedule` Choice, have been removed. Old delay figures in the matrix and state-machine snapshot above are historical and superseded by this section.

Only the Google Step Functions definition changes, via reviewed CloudFormation change set; Google worker code, paid POST exactly-once admission, read-only polling and `WaitForVideo = 20s` (poll interval, **not inter-scene handoff delay**) remain unchanged. EventBridge, S3 verification, backend orchestration, retries and payment safety remain unchanged. The next scene begins as soon as event delivery and AWS task setup permit; there is still normal event/worker startup latency, not a guaranteed zero-second handoff. A past already-running execution may retain its creation-time workflow version; subsequent new executions use the updated state machine.


## FFmpeg first failure no longer terminates job early — 16 November 2026

The assembly workflow's **one** safe retry remains unchanged: a confirmed first worker `FAILED` record, 15-second Wait, then `RetryConfirmedAssemblyFailure` with conditional DynamoDB `FAILED → PROCESSING` and `retryCount=1`. The API now distinguishes an attempt failure from an exhausted workflow:

- Named SFN `RUNNING` + recorded `assembly.FAILED`: `STITCHING / RETRYING`, **nonterminal**.
- Named SFN `FAILED` + persisted `assembly.FAILED` and `retryCount=1`: definitive `STITCHING_FAILED`, terminal.
- SFN unavailable/ambiguous/timed out, or no persisted retry evidence: `REVIEW_REQUIRED`, **nonterminal**.
- Success: verified final MP4 key returns `COMPLETED`.

No new paid Google requests, no changed retry attempt budget, and no changes to worker/Step Functions logic. Source's previously documented early terminal race is fixed by API projection + a frontend retry message. Read-only `DescribeExecution` is required for an observed failed assembly.
