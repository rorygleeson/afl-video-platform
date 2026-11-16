# AFL Video Platform — Current HTTP API contract

**Reviewed:** 16 November 2026 against `src/api/handler.py` (the actual `lambda_handler` route dispatcher), `frontend/app.js`, and `infrastructure/app/backend.yaml`. **Classification:** current **source contract**, not an independently executed test of every live API Gateway route. This document supersedes older stage-specific endpoint plans.

## Scope, access and shared rules

- API Gateway REST API `ANY /{proxy+}` forwards to `afl-app-dev-api` using Lambda proxy integration; `OPTIONS` uses a separate mock CORS preflight. Business routes require the developer `x-api-key`. `AuthorizationType: NONE` at method level **does not mean these developer API routes are anonymous to customers**: the API key requirement comes from API Gateway configuration. No Stripe customer authentication, checkout, entitlement, refund or order endpoint exists.
- `jobId` must be 32 lowercase hexadecimal characters; `scene` means `scene1`, `scene2` or `scene3`. JSON body must be an object, length at most 32,768 characters, not base64-encoded.
- Mutating routes that expressly require `Idempotency-Key` accept 12–128 `[A-Za-z0-9_-]` characters. **Image generation and assembly start routes do not require that header**; they use stable named Step Functions executions and DynamoDB reservations instead.
- Errors use JSON `{error: "..."}` or a structured `status` / `error` combination. Typical statuses: 400 invalid request, 404 absent job/route, 409 conflict/unsafe retry, 503 unconfigured dependency or uncertain handoff, 500 unexpected exception. `202` acknowledges a request, **not finished media**. Not every endpoint uses every code. Response headers include `Cache-Control: no-store`.
- Paid in internal method names means a potentially **billable Google request**, not a verified customer Stripe payment. The current browser records consent at job creation and starts image generation once; successful AWS events advance the remaining sequence. Normal browser status GETs never launch Google or FFmpeg.
- All signed S3 GETs have **300-second URL validity**; media object retention is separate. API and developer diagnostics are sensitive: do not publish developer keys or presigned URLs.

## Complete HTTP route inventory — 20 method/path combinations

The following entries have been checked against the **actual dispatcher**, not copied from old plans. Braces are explanatory placeholders, not literal path characters.

| # | Method | Route | Handler | Request/response and safeguards |
|---:|---|---|---|---|
| 1 | GET | `/scenarios` | Inline dispatcher | `200 {scenarios:[...]}`; `parallelScenes:true` refers to three concurrent **image** branches. |
| 2 | POST | `/uploads` | `presign_upload` | JSON `{contentType,sizeBytes}`; JPEG/PNG/WebP, 1 byte–10 MiB; returns `uploadKey`, presigned S3 POST `upload:{url,fields}`, `expiresInSeconds:300`. |
| 3 | POST | `/jobs` | `create_job` | Requires `Idempotency-Key`; `uploadKey,scenarioId,team,firstName,lastName,personalMessage?,confirmPaidSequence?`; S3 HEAD/type checks; returns `201 {job}`, matching replay `200`, conflicting fingerprint `409`. |
| 4 | GET | `/jobs/{jobId}` | `public_job + overall_job_progress` | `200 {job:{...,status,scenes,progress}}`; legacy `status` is **image job only**; `progress` covers full video job. |
| 5 | GET | `/jobs/{jobId}/selfie` | `selfie_for_job` | `200 {jobId,url,expiresInSeconds:300}` for saved selfie; GET only, never reuploads. May return 404 if reference absent. |
| 6 | POST | `/jobs/{jobId}/generate` | `start_images` | Named image workflow started once; `202` start/already requested, `200` already progressed, `409` terminal. No Idempotency-Key required. |
| 7 | GET | `/jobs/{jobId}/images` | `images_for_job` | `200 {jobId,status,scenes,images:[{sceneId,url}]}`; completed reference images signed individually. |
| 8 | GET | `/jobs/{jobId}/video/{scene}/preview` | `preview_video_prompt` | Developer-only rendered scene prompt/metadata, not a generated MP4 or provider request. |
| 9 | GET | `/jobs/{jobId}/video/scene1/preflight` | `scene1_preflight` | Read/validation diagnostic, not video generation. |
| 10 | GET | `/video/auth-check` | `google_auth_check` | Legacy Google worker auth diagnostic, no model-generation POST. |
| 11 | GET | `/video/paid-google-auth-check` | `paid_google_connectivity_check` | Google paid-worker auth diagnostic, **not** a paid generation request. |
| 12 | POST | `/jobs/{jobId}/real-videos/sequence` | `start_video_sequence` | `Idempotency-Key` + exact `{confirmPaidSequence:true}`; reserved sequential generation; current browser does **not** call this normally. |
| 13 | POST | `/jobs/{jobId}/real-videos/{scene}` | `start_real_scene` | `Idempotency-Key` + explicit `confirmPaidScene`; developer/manual scene admission only; checks predecessor MP4 and ownership. |
| 14 | POST | `/jobs/{jobId}/real-videos/{scene}/retry` | `retry_failed_real_scene` | `Idempotency-Key` + exact `{confirmRetryScene:"sceneN"}`; **manual** retry only after confirmed provider terminal failure and prior SFN success; max 10 authorised attempts per scene. |
| 15 | GET | `/jobs/{jobId}/real-videos/{scene}/diagnostics` | `failed_video_diagnostic` | Developer diagnostic for eligible failed interaction; may perform **read-only** Google operation lookup; no provider POST. |
| 16 | GET | `/jobs/{jobId}/videos` | `video_status` | `200 {jobId,videoGenerationMode,realVideoMode,scene1PaidApproved,sequence,paidScenes,videos,assembly,progress}`. |
| 17 | GET | `/jobs/{jobId}/videos/{scene}` | `video_status` | `200 {jobId,video}`; signs completed scene MP4 with fresh URL, does not restart it. |
| 18 | POST | `/jobs/{jobId}/videos/{scene}` | `start_mock_video` | **Legacy MOCK_ONLY**, `Idempotency-Key` required; never use to start real Google generation. |
| 19 | POST | `/jobs/{jobId}/assembly` | `start_assembly` | No Idempotency-Key header required; verifies three completed scene objects, reserves one `final-{jobId}` execution and reconciles QUEUED reservation. Current normal sequence uses EventBridge instead. |
| 20 | GET | `/jobs/{jobId}/assembly` | `assembly_public` | `200 {jobId,assembly:{status,ready,enabled,...}}`, with fresh final `url` and `downloadUrl` when completed. |

**Not HTTP endpoints:** `image_sequence_event` and `video_sequence_event` are IAM/EventBridge-delivered Lambda events. `op:"reconcile_assembly"` is internal Lambda invocation by the watchdog, never a public REST route. The API Gateway OPTIONS mock isn't an application business endpoint.

## Request examples (illustrative, not live credentials)

### Upload and job

```http
POST /uploads
Content-Type: application/json
x-api-key: <developer-key>

{"contentType":"image/jpeg","sizeBytes":1250000}
```

Browser next posts the returned form fields and selfie file **directly to private S3**. Job creation requires the upload to exist:

```http
POST /jobs
Content-Type: application/json
x-api-key: <developer-key>
Idempotency-Key: example-job-20261116

{"scenarioId":"afl-grand-final-winning-kick","team":"st-kilda","firstName":"Alex","lastName":"Example","uploadKey":"uploads/<32-lowercase-hex>.jpg","confirmPaidSequence":true}
```

`confirmPaidSequence` becomes `autoVideoConsent` on the DynamoDB job. It **is not** a payment receipt.

### Read-only progress

```json
{"job":{"jobId":"<jobId>","status":"COMPLETED","progress":{"currentStage":"VIDEO_2","stageStatus":"PROCESSING","terminal":false,"outcome":null,"imagesCompleted":3,"imagesTotal":3,"videosCompleted":1,"videosTotal":3,"finalVideoReady":false}}}
```

This is a **subset example**. Stages are `IMAGES`, `VIDEO_1`, `VIDEO_2`, `VIDEO_3`, `STITCHING`, `COMPLETED`; current-status logic is documented in [JOB_STATUSES.md](JOB_STATUSES.md).

### Assembly terminal semantics

The worker may persist `assembly.status=FAILED` on its **first** FFmpeg attempt while Step Functions is still RUNNING and waiting 15 seconds to retry once. The API projects **`STITCHING / RETRYING, terminal:false`** during that window. Only a persisted second failure (`retryCount=1`) **and** confirmed named SFN `FAILED` become `STITCHING_FAILED, terminal:true, outcome:FAILED`. Missing/inconsistent SFN states remain `REVIEW_REQUIRED`, nonterminal. Successful final MP4 projects `terminal:true,outcome:COMPLETED`. See [retries](RETRIES_AND_TIMEOUTS.md).

## Error, retry, access and availability caveats

- `202` does **not** prove Google accepted a POST or that AWS reached a finished stage. Read existing job/video status to distinguish accepted, working, uncertain and completed.
- For videos, `SUBMISSION_UNKNOWN` is written **before** the potentially billed Google POST. Never resubmit on this state. API `REVIEW_REQUIRED` may be a read-only projection of failed/missing Step Functions execution; it is **not** permanent failure or refund eligibility.
- `GET /jobs/{jobId}/videos` and `GET /jobs/{jobId}` can perform read-only SFN `DescribeExecution` during review/retry classification. Status GETs do not themselves mutate DynamoDB.
- An expired signed URL can be refreshed by GET while the S3 object still exists. Storage lifecycle currently expires `uploads/` and `results/` after seven days; `videos/` lacks a defined expiry. DynamoDB job TTL is seven days. **Do not equate 300-second URL expiry with asset deletion.**
- The source currently returns some stale user-facing text referring to **Replicate** in the image-start response; image generation code uses Google's direct adapter. This is an **application wording defect**, not a reason to rewrite documentation to claim Replicate is active.
- Cross-device anonymous recovery, payment/refund APIs, secure order entitlement and reliable recovery after lost AWS EventBridge events are **not implemented**. Live API Gateway stage/drift and provider fault-injection remain unverified.

## Source traceability

Routes: `src/api/handler.py:lambda_handler`. Shapes: `public_job`, `images_for_job`, `selfie_for_job`, `video_scene_public`, `video_status`, `assembly_public`, `overall_job_progress`. Admission: `create_job`, `start_images`, `start_real_scene`, `retry_failed_real_scene`, `start_video_sequence`, `start_assembly`. Runtime and retries: [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md). Historical route plans remain in `API_STAGE1B.md` and other stage docs and are **not current contracts**.

## Event-driven internal handoffs and business-level failure results

The HTTP endpoint inventory is unchanged at **20 method/path combinations**. EventBridge success callbacks are *not* HTTP routes: API Lambda internally dispatches `image_sequence_event` for matching image SFN `SUCCEEDED` and `video_sequence_event` for matching Google scene SFN `SUCCEEDED`. API validates current execution identity and, for video, matching DDB source/key and S3 object before any new reservation.

A successful EventBridge→Lambda asynchronous invocation does **not** prove a downstream Step Functions execution was started. Video event handler may **return** `NEXT_SCENE_HANDOFF_NEEDS_REVIEW` / `ASSEMBLY_HANDOFF_NEEDS_REVIEW` as a successful Lambda return, which is not a Lambda function error. EventBridge delivery RetryPolicy and Lambda async function-error retries are **not** a replacement for a persisted application-level reconciliation queue. See [event-driven architecture](EVENT_DRIVEN_ARCHITECTURE.md) and [failure recovery runbook](FAILURE_RECOVERY_RUNBOOK.md).

The committed Google video ASL `Claim.requestId.$` is currently `$.Execution.Name`, differing from `$$.Execution.Name` on subsequent tasks. This is an **infrastructure-source mismatch requiring live verification**, not a new HTTP contract. No payment/auth route is implemented.
