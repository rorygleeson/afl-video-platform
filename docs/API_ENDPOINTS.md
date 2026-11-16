# AFL Video Platform — HTTP API Contract

> **STATUS CONTRACT LIMITATION (re-audited 16 NOVEMBER 2026):** `GET /jobs/{jobId}` and `GET /jobs/{jobId}/videos` use the existing read-only `overall_job_progress`. The `STITCHING_FAILED / terminal:true` outcome can temporarily appear after the first FFmpeg failure while the assembly Step Functions waits 15s and performs its one allowed retry. This is an identified **critical contract bug**, not a reliable final payment/refund signal. Fix RA-01 in [resilience audit](RESILIENCE_AUDIT_2026-11-16.md) before depending on it for Stripe. Other `REVIEW_REQUIRED`/unknown scenarios remain nonterminal; no payment endpoints exist.
>

**Source audit:** 15 November 2026, updated after backend stages 1–3 · **Status:** SOURCE VERIFIED; guarded AWS deploy logs verify API Lambda, EventBridge stacks and frontend publishing; full live route/drift/e2e audit remains open.  
**Authoritative code:** `src/api/handler.py` (route dispatcher and contracts), `infrastructure/app/backend.yaml` (API Gateway proxy/Lambda), `frontend/app.js` (browser calls). See [JOB_STATUSES.md](JOB_STATUSES.md), [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md), and [RESILIENCY_ROADMAP.md](RESILIENCY_ROADMAP.md).

**Source-documented dev base URL:** `https://ue36acwvrc.execute-api.ap-southeast-2.amazonaws.com/dev` (confirm live before relying on it).

**Backend-owned orchestration note (2026-11-15):** Once the browser has uploaded the selfie, created a consented job and POSTed `/jobs/{jobId}/generate`, successful AWS completion events—not frontend polling—trigger Video 1, Video 2, Video 3 and final FFmpeg stitching. Existing video retry POSTs still require deliberate confirmation. **No Stripe payment gate is implemented.**

## Shared conventions

- API Gateway REST API: `ANY /{proxy+}` using **AWS_PROXY Lambda integration** to `afl-app-dev-api`, handler `handler.lambda_handler`; application dispatches actual GET/POST routes. `OPTIONS /{proxy+}` is a non-key mock CORS preflight. No per-route standalone Lambda integrations.
- Every business route currently requires API Gateway `x-api-key`, with `AuthorizationType: NONE`. **API keys are not customer identity or payment entitlements.** They are developer-only. Future paid customers require per-job secure authorisation.
- JSON requests use `Content-Type: application/json`; body must be an object of at most 32,768 characters; base64 bodies rejected.
- Applicable mutation requests require `Idempotency-Key` header, 12–128 characters from `[A-Za-z0-9_-]`. **Not all POSTs require this header**; consult individual rows.
- `jobId`: 32 lowercase hex characters. `scene`: `scene1`, `scene2`, `scene3`.
- General response format: JSON, `Cache-Control: no-store`, CORS allow-origin from `FRONTEND_ORIGIN`. Unhandled exceptions return **500** `{"error":"Internal server error"}`; malformed requests usually **400**; missing jobs **404**; conflicting lifecycle requests **409**; disabled dependencies/handoff uncertainty often **503**.
- API Lambda **source-configured timeout 15 seconds and memory 256 MB**. API Gateway integration timeout and live gateway stage throttling must be checked against actual AWS. Do not mistake a 202 response for completed generation.
- Upload bytes go **directly from browser to private S3**, NOT through API Lambda. Generated media URLs are **S3 signed GETs**, not separate API media stream handlers.

## Endpoint inventory — current dispatcher (19 routes)

| Method | Path | Purpose; request fields | Response / code highlights | Execution |
|---|---|---|---|---|
| GET | `/scenarios` | Scenario metadata | 200 `{scenarios:[{id,name,sceneCount,imageGenerationEnabled,parallelScenes}]}`; `parallelScenes:true` now aligns with image Map `MaxConcurrency:3` | Sync/read-only |
| POST | `/uploads` | JSON `{contentType,sizeBytes}`, JPEG/PNG/WebP; up to 10 MiB | 200 `{uploadKey,upload:{url,fields},expiresInSeconds:300}` | Sync presign, then direct S3 POST |
| POST | `/jobs` | JSON `{uploadKey,scenarioId,team,firstName,lastName,personalMessage?,confirmPaidSequence?}`; **Idempotency-Key required** | 201 `{job}`, 200 same-key/same-input replay; 409 same-key/different-input | Sync; S3 HEAD + DDB conditional Put |
| GET | `/jobs/{jobId}` | Job ID | 200 `{job:{...,status,scenes,progress:{currentStage,stageStatus,imagesCompleted,imagesTotal,videosCompleted,videosTotal,finalVideoReady}}}`; read-only, legacy `status` is image-pipeline state | Sync DDB read |
| POST | `/jobs/{jobId}/generate` | Start reference images (no request body required) | 202 `{jobId,status,message}` on start/already-running; 200 for existing job state; 404 missing | Async; start named image SFN |
| GET | `/jobs/{jobId}/images` | Image scene status + signed URLs | 200 `{jobId,status,scenes,images:[{sceneId,url}]}` | Sync DDB read, presigned S3 GETs |
| GET | `/jobs/{jobId}/video/{scene}/preview` | Render versioned video prompt; free model-wise | 200 rendered prompt/metadata object from `render_scene`; 503 missing assets | Sync S3 templates |
| GET | `/jobs/{jobId}/video/scene1/preflight` | Developer worker preflight (no model generation) | 200 contract-check response or error response | Sync Lambda invoke |
| GET | `/video/paid-google-auth-check` | Developer keyless Google auth check | 200 `AUTHENTICATED` or 502/503 diagnostic | Sync Lambda invoke; no model generation |
| GET | `/video/auth-check` | Legacy worker authentication diagnostic | 200/502/503, `AUTHENTICATED` or `AUTHENTICATION_FAILED` | Sync Lambda invoke; no model generation |
| POST | `/jobs/{jobId}/real-videos/sequence` | JSON `{"confirmPaidSequence":true}`; **Idempotency-Key required** | 202 `SEQUENCE_APPROVED`/`ALREADY_APPROVED`; 409 invalid lifecycle; 503 unsafe handoff | Async; reserves sequence and first missing scene |
| POST | `/jobs/{jobId}/real-videos/{scene}` | JSON `{"confirmPaidScene":"sceneN"}`; **Idempotency-Key required** | 202 `REAL_VIDEO_REQUESTED`/`ALREADY_REQUESTED`; 409 duplicate/not ready; 503 disabled/unknown handoff | Async; conditional DDB claim + named SFN |
| POST | `/jobs/{jobId}/real-videos/{scene}/retry` | JSON `{"confirmRetryScene":"sceneN"}`; **Idempotency-Key required** | 202 `REAL_VIDEO_RETRY_REQUESTED`/`ALREADY_REQUESTED`; 409 ineligible/unsafe; 503 unknown handoff | Async; guarded paid retry, max 10 attempts |
| GET | `/jobs/{jobId}/real-videos/{scene}/diagnostics` | Read-only failed-interaction developer lookup | 200 `{jobId,sceneId,diagnostic}`; 409 no eligible failed attempt | Sync Lambda invoke, read-only Google GET as appropriate |
| GET | `/jobs/{jobId}/videos` | All video states, sequence and assembly | 200 `{jobId,videoGenerationMode,realVideoMode,scene1PaidApproved,sequence,paidScenes,videos,assembly}` | Sync DDB/Step Functions read + presign |
| GET | `/jobs/{jobId}/videos/{scene}` | Individual scene status + signed MP4 | 200 `{jobId,video}` | Sync DDB/Step Functions read + presign |
| POST | `/jobs/{jobId}/videos/{scene}` | **LEGACY MOCK ONLY**; **Idempotency-Key required** | 202 `MOCK_REQUESTED`/`ALREADY_REQUESTED`; 200 previously created; 409 busy/not ready | Async mock SFN, **not** Google paid endpoint |
| POST | `/jobs/{jobId}/assembly` | Reserve final stitching after all 3 real MP4s | 202 `QUEUED`/`ALREADY_REQUESTED`, 200 prior record, 409 unavailable clips, 503 disabled/unknown handoff | Async; DDB conditional reserve + named assembly SFN |
| GET | `/jobs/{jobId}/assembly` | Assembly readiness/failure/final download | 200 `{jobId,assembly:{status,ready,enabled,...}}`; after completion signed `url` and `downloadUrl` | Sync DDB/SFN + presign |

The endpoint count above **excludes** API Gateway `OPTIONS /{proxy+}` mock preflight. Invalid routes return 404 `Unknown endpoint`. The old 8 November document omitted sequence, retry, diagnostics and assembly endpoints.

## Key request / response examples (synthetic — not live values)

### 1. Presigned selfie upload

```http
POST /uploads
x-api-key: <developer-key>
Content-Type: application/json

{"contentType":"image/jpeg","sizeBytes":1250000}
```

Response shape:
```json
{"uploadKey":"uploads/<32hex>.jpg","upload":{"url":"https://<s3-host>/","fields":{"...":"S3 presigned POST fields"}},"expiresInSeconds":300}
```

Browser then POSTs `multipart/form-data` to the **returned S3 URL** using **every returned field** plus `file`. Signature expires in 300 seconds; size 1 byte–10 MiB, MIME type JPEG/PNG/WebP. This direct S3 request has separate HTTP errors and CORS conditions from API Gateway.

### 2. Idempotent job creation

```http
POST /jobs
x-api-key: <developer-key>
Idempotency-Key: example-unique-key-20261115
Content-Type: application/json

{"uploadKey":"uploads/<32hex>.jpg","scenarioId":"afl-grand-final-winning-kick","team":"st-kilda","firstName":"Example","lastName":"Customer","confirmPaidSequence":true}
```

`201 {job:{jobId,status:"AWAITING_IMAGE_PIPELINE",...}}`. On matching replay, `200` returns the previously created job. Different input with same idempotency key gives `409`. Upload must be present in S3 and match its type/size. The current frontend sends `confirmPaidSequence:true`, stored internally as `autoVideoConsent:true`, to authorise automatic video progression after verified image completion. This **does not verify Stripe payment**.

### 3. Image status

```json
{
  "jobId": "<32hex>",
  "status": "GENERATING_IMAGES",
  "scenes": {
    "scene1": {"status":"COMPLETED"},
    "scene2": {"status":"RETRY_WAIT","retryCount":1,"maxRetries":7,"nextRetryAt":1790000000},
    "scene3": {"status":"PENDING"}
  },
  "images": [{"sceneId":"scene1","url":"<short-lived-S3-GET>"}]
}
```

The example reflects a valid response **shape**, not a real job. `GET /jobs/{jobId}` reports **image pipeline overall status**, not final video delivery status.

### 4. Video sequence admission

```http
POST /jobs/<32hex>/real-videos/sequence
Idempotency-Key: example-video-sequence-key-20261115
x-api-key: <developer-key>
Content-Type: application/json

{"confirmPaidSequence":true}
```

Response: `202 {status:"SEQUENCE_APPROVED",jobId:"...",sequence:{status:"APPROVED",mode:"AWS_EVENT_DRIVEN"}}`. No Stripe payment validation exists yet: the existing word *paid* refers to billable Google model invocation and explicit developer consent, **not** an already paid A$5 Stripe order.

The **current browser no longer POSTs the sequence endpoint**: it records consent on the job at creation and starts image generation with the existing one-time `POST /jobs/{jobId}/generate`. Image SFN `SUCCEEDED` EventBridge event invokes `image_sequence_event()` to check recorded consent and three completed images before internally calling `start_video_sequence()`. The public sequence endpoint remains for explicitly authorised developer/manual uses. Successful video executions emit EventBridge events for subsequent scenes; **Scene 3 triggers assembly**. All normal status GETs are read-only.

### 5. Video status

```json
{
  "jobId": "<32hex>",
  "sequence": {"status":"APPROVED","mode":"AWS_EVENT_DRIVEN"},
  "videos": {
    "scene1": {"sceneId":"scene1","status":"COMPLETED","mode":"REAL","attemptNumber":1,"maxAttempts":10,"retryAvailable":false,"url":"<short-lived-S3-GET>"},
    "scene2": {"sceneId":"scene2","status":"PROCESSING","mode":"REAL","attemptNumber":1,"maxAttempts":10,"retryAvailable":false,"interactionId":"<provider-id>"},
    "scene3": {"sceneId":"scene3","status":"NOT_REQUESTED","attemptNumber":1,"maxAttempts":10,"retryAvailable":false}
  },
  "assembly": {"status":"NOT_READY","ready":false,"enabled":true}
}
```

This example omits several top-level response fields for readability. Actual `video_scene_public()` may include `workflowStatus`, `failureCode`, `errorCode`, `retryBlockedReason`, `requestId`, timestamps, `providerFinalStatus`, `providerErrorCode`, `providerErrorCategory`, and `downloadUrl`. `REVIEW_REQUIRED` can be derived from Step Functions failure even when the underlying DDB state remains nonterminal. Full status dictionary: [JOB_STATUSES.md](JOB_STATUSES.md).

### 6. Final stitching

```http
POST /jobs/<32hex>/assembly
x-api-key: <developer-key>
```

`202 {"status":"QUEUED","jobId":"..."}`; completed `GET /jobs/{jobId}/assembly` returns `assembly.status:"COMPLETED"`, `assembly.url`, and `assembly.downloadUrl` (signed for five minutes). S3 object `videos/{jobId}/final.mp4`; download filename `afl-grand-final.mp4`.

## Auth, safety and idempotency observations

- **Security blocker:** shared API key is sufficient to call current developer API methods; it does **not** authenticate a paying customer or prove their entitlement to retrieve a specific job. Job ID must not be the sole authorisation proof in a customer system.
- Idempotent `POST /jobs`: job ID deterministically derived from Idempotency-Key and payload fingerprint conditionally verified.
- Image start: named execution `jobId`; no second image workflow should start under same name. **Potential handoff ambiguity:** a failed StartExecution acknowledgement requires reconciliation.
- Original Google scene start: per-scene DynamoDB conditional admission; stable execution name `real-{jobId}-{scene}`. No duplicate billable POST on response uncertainty.
- Retry: only confirmed terminal provider failures, old workflow SUCCEEDED, still-matching image source, and fewer than 10 attempts. Current retries are separately confirmed by developer UI.
- Assembly: successful verified Scene 3 AWS completion automatically calls existing `start_assembly()` via EventBridge; this verifies all three real S3 MP4 files, conditionally creates `assembly`, and starts named `final-{jobId}`. Frontend no longer POSTs assembly. Existing `POST /assembly` route remains for controlled/manual use; lost-event, ambiguous workflow-start and assembly failure still require safe reconciliation.
- A 300-second presigned media URL expiry does **not** mean generation output expired; authenticated status GET obtains another URL. Actual S3 lifecycle and DDB TTL are separate.
- **Source compatibility note:** `parallelScenes:true` in `/scenarios` is now consistent with the image Map concurrency of 3. The existing start-images response still contains legacy Replicate wording; this is a wording defect only, recorded for later code cleanup, **not changed** in this documentation audit.

## Integration details

| Interface | Caller / backend |
|---|---|
| Frontend | `frontend/app.js` uses `api()` for application GET/POST requests and direct presigned S3 selfie POST; polls images and videos |
| API Gateway | Source `infrastructure/app/backend.yaml` REST `ANY /{proxy+}` → `ApiLambda` `handler.lambda_handler` |
| API → image | `start_images()` starts `afl-app-dev-scene1-pilot` Standard SFN |
| API → video | `start_real_scene()` starts `afl-app-dev-google-omni` Standard SFN |
| EventBridge → API | Successful image SFN execution invokes `image_sequence_event()` for consent-gated Video 1; successful Google video SFN execution invokes `video_sequence_event()` for Video 2/3 and Scene 3 → final assembly. These are Lambda events, not public HTTP calls. |
| API → assembly | `start_assembly()` starts `afl-app-dev-assembly` Standard SFN |
| Google image outbound | `POST https://aiplatform.googleapis.com/v1/projects/afl-video-platform/locations/global/publishers/google/models/gemini-3-pro-image:generateContent` |
| Google video outbound | `POST https://aiplatform.googleapis.com/v1beta1/projects/afl-video-platform/locations/global/interactions`; `GET .../interactions/{interactionId}` |
| Google video output | Inline MP4 or configured GCS location; private final per-scene S3 objects |
| S3 media | `uploads/{uuid}.{ext}`, `results/{jobId}/sceneN.png`, `videos/{jobId}/sceneN.mp4`, `videos/{jobId}/final.mp4` |

## Source-configured timeout and retry summary

- API Lambda: **15s**, 256MB.
- Image worker: **600s**, 1024MB; outbound Google image POST **240s**; retry explicit 429 only up to **8 total attempts**, 600s retry window.
- Image state machine: **1800s** overall, parallel Map `MaxConcurrency:3`; dedicated Wait for 429 Retry-After/backoff.
- Google worker: **300s**, 1024MB; Google Interactions POST/GET `urlopen(... timeout=150)`; video workflow **3600s**, 20s poll wait, PollVideo TaskFailed 3 retries at 10/20/40s.
- Assembly: **600s**, 2048MB, no Step Functions retry; FFmpeg normalisation up to **150s per clip**, concat up to **60s**.
- Frontend job status polling: **5s**, or **15s** after a polling failure. Uploaded S3 POST has up to **2 extra retries** only on browser `TypeError`.
- **Caution:** AWS SDK underlying retry settings, API Gateway integration timeout, live concurrency, and EventBridge retry policies require deployed/configured verification. See [RETRIES_AND_TIMEOUTS.md](RETRIES_AND_TIMEOUTS.md).

## Implemented read-only overall progress on job GET

`GET /jobs/{jobId}` now returns the original `{"job": {...}}` object with one additional `job.progress` member. Legacy `job.status` **continues to describe image-pipeline status** and is not renamed. The progress object is calculated from existing DynamoDB image/video/assembly records, plus the API's existing safe read-only video/assembly status projections. It does not write state, start generation, retry or stitch.

Example (synthetic):

```json
{
  "job": {
    "jobId": "<32hex>",
    "status": "COMPLETED",
    "progress": {
      "currentStage": "VIDEO_2",
      "stageStatus": "PROCESSING",
      "imagesCompleted": 3,
      "imagesTotal": 3,
      "videosCompleted": 1,
      "videosTotal": 3,
      "finalVideoReady": false
    }
  }
}
```

Stages: `IMAGES`, `VIDEO_1`, `VIDEO_2`, `VIDEO_3`, `STITCHING`, `COMPLETED`. Stage status: `PENDING`, `PROCESSING`, `RETRYING`, `WAITING_FOR_ACTION`, `FAILED`, `REVIEW_REQUIRED`, `COMPLETED`. Each image retains its independent record in existing `job.scenes`. If all three images are complete but the image workflow has not finalised its overall state, progress stays at `IMAGES / PROCESSING`. A stopped/uncertain video workflow can surface `REVIEW_REQUIRED`. `WAITING_FOR_ACTION` may now reflect a missing backend reservation/handoff, not necessarily a required browser click. This GET reads state only; AWS EventBridge completion events drive stage advancement independently.

**Deployment status:** existing overall status API and diagnostics deployed in [run 43678843432](https://github.com/rorygleeson/afl-video-platform/actions/runs/43678843432); subsequent guarded backend handoff deployments succeeded in [Stage 1 run 43689396745](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689396745) and [Stage 2 run 43689754297](https://github.com/rorygleeson/afl-video-platform/actions/runs/43689754297). Frontend read-only cleanup deployed in [run 43690121205](https://github.com/rorygleeson/afl-video-platform/actions/runs/43690121205). Live unattended full-job proof and full API Gateway drift comparison remain outstanding.

## Frontend developer diagnostics — 2-second overall status display

The Developer diagnostics panel now contains an **Overall job** status line. While that panel is expanded, a valid `currentJobId` exists, and the browser tab is visible, `frontend/app.js` reads `GET /jobs/{jobId}` every **2 seconds after the prior request completes**. It displays `job.progress.currentStage`, `job.progress.stageStatus`, `imagesCompleted/imagesTotal`, and `videosCompleted/videosTotal`.

- Polls are **read-only**; they never initiate generation, videos, retries or assembly.
- At most one dedicated overall-status request is in flight; polling pauses when the panel closes or the tab becomes hidden. Existing image/video polling schedules are unchanged.
- If no job exists, show `Overall job: No job started.`. If the deployed API lacks `job.progress`, show `Status not available from API yet.`, not a fabricated stage.
- **Release dependency:** both the frontend change and the API `job.progress` addition must be deployed for live status to display. GitHub commits are not proof of deployment.

### Live deployment verification — 2026-11-15

The new read-only `job.progress` projection and two-second Developer diagnostics status line were deployed in [GitHub Actions release run 43678843432](https://github.com/rorygleeson/afl-video-platform/actions/runs/43678843432). Evidence: 17 API contract tests and 60 frontend tests passed, API Lambda CodeSha256 verified after CloudFormation update, frontend `app.js` and `index.html` S3 byte-for-byte readback verified, CloudFront invalidation submitted. No actual customer job request has been used as end-to-end smoke proof; deployment verification is limited to AWS Lambda/S3 and offline contract tests.

## Open verification items

- [ ] Compare all API routes/flags and timeouts against deployed API Gateway and Lambda (source is verified; live drift unknown).
- [ ] Add isolated mock contract tests verifying every response shape and error code.
- [ ] Verify `frontend/app.js` callers including hidden developer controls and browser reload behaviour.
- [ ] Implement future customer recovery authorisation and paid entitlement only after audit completion and approval.
- [x] Document implemented read-only `job.progress` response with actual schema. [ ] Full end-to-end live status transition test and backend missed-event recovery remain open.

## Additive reference-image diagnostic fields (15 November 2026)

`GET /jobs/{jobId}` and `GET /jobs/{jobId}/images` retain their existing
response shapes. For an image scene whose `status` is `FAILED`, its
`scenes.sceneN` object can now additionally contain
`providerFinishReason`, `providerBlockReason`,
`providerBlockedCategories` (array of approved category names),
`providerCandidateCount`, `providerImageParts` and `providerTextOnly`.
These fields are optional: historic jobs, prep errors, HTTP errors and
non-provider failures may not have them. Only the existing status endpoints
are queried; there is **no new HTTP endpoint**, extra Google call, or paid
diagnostic POST. The developer UI reads them from the existing job-status GET.
The metadata is strictly allowlisted: no raw Google response body, safety
explanation strings or user media is returned.


## Definitive universal job terminal response (16 November 2026)

Both `GET /jobs/{jobId}` under `job.progress` and
`GET /jobs/{jobId}/videos` under `progress` expose the same backend-owned,
read-only definitive lifecycle result:

- `terminal: true`, `outcome: COMPLETED` means final MP4 is available.
- `terminal: true`, `outcome: FAILED` and `retryable: false` indicate
  `REFERENCE_IMAGES_FAILED`, `CONTENT_BLOCKED`,
  `VIDEO_ATTEMPTS_EXHAUSTED` or `STITCHING_FAILED`. Responses include
  `currentStage`, `stageStatus`, `failureCode`, safe `message`,
  `canStartNewJob: true`, and optionally `terminalAt` / `failedScene`.
- `terminal: false` means pending, processing, eligible for manual retry or
  review. It is **not** a payment/refund decision, even when stageStatus is
  `FAILED` or `REVIEW_REQUIRED`.

The lifecycle fields are a projection of existing persisted stage records.
`job.status` remains the image-job status; new job starts, retries, final
assembly and EventBridge handoffs reject known definitive terminal failures.
No new status POST, payment API or additional billable generation is added.
