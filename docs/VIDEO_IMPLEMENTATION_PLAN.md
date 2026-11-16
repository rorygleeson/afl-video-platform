# Stage 5A — Google Omni adapter (paid generation locked)

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

**Implemented 8 November 2026.** This stage prepares the real video integration **without making any paid generation calls**. Stage 5B (first paid Scene 1) is a separate approval gate.

## Exact delivery

- New isolated CloudFormation stack: `infrastructure/app/google-video.yaml`, stack `afl-app-dev-google-video`. It does not alter the working image or mock-video stacks.
- Separate Google Omni worker: `src/google_video_worker/handler.py`, with S3-only reference-image input, personalised versioned S3 prompt, asynchronous Google Interactions POST and GET, inline bounded MP4 parsing and private S3 output `videos/{jobId}/sceneN.mp4`.
- Separate Standard Step Functions state machine: `afl-app-dev-google-omni`; begins with an explicit paid gate, then claim, submit, wait, poll and finalise. **No automatic retries on ambiguous provider operations.** The global DynamoDB slot is shared with mock workflows. An unknown submission or interrupted execution leaves the slot locked until operator reconciliation rather than risking a double charge.
- Google WIF credential exchange follows existing project/pool/provider; the **new AWS IAM role has not yet been granted Google service-account impersonation**. Existing auth-probe role remains untouched.
- The adapter and its Step Functions are *not* connected to API Gateway or to the frontend; existing buttons remain mock-only.
- Hard-disabled by deployed Lambda environment `PAID_GENERATION_ENABLED=false` and `GOOGLE_VIDEO_MODEL=UNCONFIGURED`; the current S3 scenario configuration and prompt renderer independently reject video generation. An operator cannot start a paid request simply by using the current frontend or starting the new state machine.
- Offline contract tests: `tests/test_google_video_stage5a.py`; separate create-only GitHub Actions workflow `.github/workflows/deploy-google-video.yml`.

## Before separately approving a single paid Scene 1 test (Stage 5B)

1. Add the new AWS role `afl-app-dev-google-omni-role` as an authorised principal in the **existing** Google service-account WIF impersonation binding. Do not recreate the pool/provider or grant broad user IAM.
2. Verify Google project model entitlement, budget cap and per-model quotas; the earlier auth probe does not prove model access. Verify complete inline image + async inline MP4 contract in the actual Google project. Current Google docs explicitly support the component fields, but the combined request remains untested.
3. Add a **separate**, explicit paid Scene 1 control/API route with per-click confirmation and strict idempotency; never silently repurpose a mock button. Update scenario config and renderer only under that approval.
4. Plan a safe transition of `videos.scene1.status=MOCK_COMPLETED` to an approved paid attempt without deleting/replaying the original mock execution. Currently the real adapter's claim rejects any existing video state; this protects existing mock records by design.
5. Verify fail-closed recovery for unknown Google submission, failed parsing, oversize output and workflow timeout. The adapter does not yet include production-level manual reconciliation and should not be enabled without it.
6. Confirm model response structure, interaction ID and output MIME/content against the first authorised provider response. Don't declare real MP4 playback proven before that.

Google official docs: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image and https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/interactions-api .

---

# Video generation implementation plan (approved design, not yet deployed)

**Date:** 2026-11-08. **Stage:** 4 — mock-only scene API routes and frontend controls deployed. **Next gate:** Stage 5 approval for FFmpeg assembly infrastructure. **Repository:** `main`; CD uses the existing `deploy` marker branch and CloudFormation. No feature branch required.

**Stage 2 official API research (8 November 2026):** [Google Omni Stage 2 API contract](GOOGLE_OMNI_STAGE2_API_CONTRACT.md). The model ID, documented JSON contract and S3-only feasibility are confirmed; authenticated model entitlement, quota and actual inline MP4 retrieval are untested.\n\n**Stage 3 implementation:** [video CloudFormation](../infrastructure/app/video.yaml), [mock-only worker](../src/video_mock_worker/handler.py), [offline unit tests](../tests/test_video_mock_stage3.py), [isolated deployment workflow](../.github/workflows/deploy-video.yml). AWS stack `afl-app-dev-video` was created by GitHub Actions; mock-only execution and playback are not yet end-to-end tested.

## Goal and current reality

Keep the **existing** selfie + team + first/last name -> three parallel Replicate personalised reference images -> private S3 workflow operational and unchanged. Existing Google Workload Identity Federation **auth check** and three **free S3 prompt preview** buttons are deployed. **The mock-only video state machine now has independent API endpoints and three Scene N mock test buttons. No paid video generation, real MP4 storage, FFmpeg assembly or functional video playback has been implemented.**

Add three explicit **Generate Scene N Video** buttons (one per completed image), each using exactly that scene's `scenes.sceneN.resultKey` in the existing DynamoDB item, plus the scene-specific prompt from versioned S3 configuration. Each completion creates an MP4 in AWS S3. When all three complete, expose an **Assemble Final Video** button that invokes FFmpeg in a separate Lambda and stores `final.mp4`. No automatic quality-based reruns or assembly while testing. Later support up to three parallel provider generations and simplified consumer UX. Defer Solution -> Use Case hierarchy and payments.

## Resource boundaries

- **No changes to the Replicate worker or existing image Step Functions definition.** Existing image state map `scenes` stays independent from new `videos` state.
- Existing REST API Gateway and API Lambda: add additive endpoints, DynamoDB conditional claims and signed playback URLs. Existing proxy routes and dev API-key throttling retained.
- **New separate `afl-app-dev-video` CloudFormation stack** with a new video worker Lambda and **new Standard video state machine**, its execution role/log groups and a concurrency/admission mechanism. Keep existing auth-check Lambda and its WIF config intact; new worker must have its own least-privilege AWS IAM role and reuse keyless Google federation through a correctly authorised federated AWS role.
- Existing `afl-app-dev-jobs` DynamoDB table can use additional `videos` attributes without schema replacement. A separate small lock/lease or request ledger table is allowed if needed for durable cross-job admission and idempotency.
- **Reuse existing private AWS S3 uploads/results bucket**, with `videos/{jobId}/scene1.mp4`, `scene2.mp4`, `scene3.mp4`, `final.mp4`. No new AWS bucket or GCS bucket for v1 **unless API-contract/real output-size verification proves necessary**. The artifact bucket continues to store versioned prompts. Never make a bucket public or grant Google access to S3.
- Input: worker reads the **personalised generated image**, not the selfie or generic master, and submits it using an API-supported image representation (preferred bounded inline bytes). Output: supported downloadable URI streamed into S3 or bounded inline decoding into S3. Verify which forms work for the **existing Vertex/Google Cloud WIF endpoint**; Gemini Developer API behaviour cannot be assumed identical to Vertex AI. If the Google endpoint requires GCS for output URIs, explicitly revisit storage decision before deployment.
- Never carry MP4 or base64 through API Gateway, Step Functions state payloads or DynamoDB. Their contracts carry only scene IDs, operation handles, statuses and S3 object keys. Video worker should reject oversize uploads, verify media type and use bounded download memory/disk.

## Target sequence (one scene)

1. UI shows reference thumbnail, **Preview Prompt** (free), **Generate Video** (paid and explicit) separately for each completed image. User confirms a single selected paid scene.
2. `POST /jobs/{jobId}/videos/{sceneId}` validates scene allowlist, job, image `COMPLETED` and existing S3 `resultKey` via HeadObject; validates server-side video enabled flag, paid request cap, and idempotency token.
3. Atomically create or recover one video request/scene record. Reserve a global provider slot using a conditional lease/queue mechanism (initial concurrency **1** across all jobs). When busy in dev, return a clear conflict/busy result rather than silently enqueueing additional paid calls. A **lease must remain occupied for the whole async generation**, not just Lambda invocation; reconcile expired leases safely.
4. Start a **named Standard Step Functions execution** for that scene. Track `requestId`, `executionArn`, `promptVersion`, `sourceImageKey`, `providerOperationId`, submission and completion times as appropriate. If execution start fails after a claim, reconcile the pending record without creating duplicate paid calls.
5. Worker loads S3 template and validated customer data, creates a deterministic resolved prompt and records prompt provenance; checks the source image and sends it to the verified model endpoint.
6. After provider submission, persist operation handle durably. **Do not retry ambiguous submit failures**: transition to `UNKNOWN_SUBMISSION`, preserve evidence for manual/provider reconciliation. Retrying a *known rejected*, confirmed nonbillable submission is a different policy. Polling known operation handles may retry with backoff.
7. Step Functions `Wait -> Poll -> Choice` until success/failure/time ceiling. On success stream/download/decode an MP4 within configured limits, save private S3 object (Content-Type video/mp4, SSE), verify object and update DynamoDB `videos.sceneN` to `COMPLETED`; release slot. On failure, save safe error code and release slot only when generation definitely ended. Fail-safe periodic reconciliation handles stalled executions/leases.
8. `GET /jobs/{jobId}/videos` and `GET /jobs/{jobId}/videos/{sceneId}` return states and, if completed, short-lived signed MP4 URLs. Frontend polls without paid side effects and plays each clip.
9. When all 3 independently stored MP4 objects are verified, offer `POST /jobs/{jobId}/assemble` (explicit, idempotent). Separate FFmpeg assembly Lambda uses /tmp or streaming disk; concatenate in scene order with stream copy where compatible, otherwise normalise transcode after testing audio/video codecs, frame rates and time bases. Save and verify `videos/{jobId}/final.mp4`. Provide signed playback/download and assembly status. No paid model call during assembly.

## Proposed API contract (additive)

- Existing `GET /jobs/{jobId}/video/{sceneId}/preview` stays free and unchanged.
- `POST /jobs/{jobId}/videos/{sceneId}`: explicit one-scene generation; validate `Idempotency-Key` and cost feature flag; 202 for accepted, 200 for exact replay/known state, 409 on busy/conflict, 4xx on invalid/missing inputs.
- `GET /jobs/{jobId}/videos`: all scene states; includes assembly state.
- `GET /jobs/{jobId}/videos/{sceneId}`: single scene state, signed playback URL when ready.
- `POST /jobs/{jobId}/assemble`: explicit assemble only after all 3 scene videos complete; idempotent.
- `GET /jobs/{jobId}/assembly`: assembly status and signed final URL.
- Do not accept arbitrary source S3 URL or prompt from the browser; derive both from trusted job and versioned configuration.

## DynamoDB model

Preserve existing `status`, `scenes` and image-worker writes. Add independent `videos.scene1/2/3` objects, with lifecycle `NOT_REQUESTED -> QUEUED/CLAIMED -> SUBMITTING -> GENERATING -> SAVING -> COMPLETED`, or `FAILED` / `UNKNOWN_SUBMISSION`. Track scene-specific status and identifiers; never overwrite the entire `scenes` map during video updates. Separate `assembly` object (`NOT_REQUESTED`, `ASSEMBLING`, `COMPLETED`, `FAILED`). Design conditional updates so duplicate clicks, concurrent callers, failed handoffs and partial retries cannot cause multiple paid provider submissions. Future generation attempts after a terminal provider failure require a new, explicitly approved request and attempt ID, never an automatic visually motivated retry.

## Limits, costs, retention

- `videoGenerationEnabled=false` on first deployments. No Google paid generation during tests/mocks. Paid calls require an explicit user action and feature-flag enablement for an approved test.
- `videoConcurrency=1` **enforced across async operations**; later `3` without replacing per-scene workflow. Distinguish concurrent operations from `maxPaidScenesPerRequest=1`. A future Generate All action requires a separate explicit policy decision.
- Scene durations currently **8/8/10 seconds** per existing JSON. Confirm the actual model identity, permitted durations, image payload sizes, output types, billing, quota and IAM via current official docs and controlled validation **before paid rollout**.
- Known test example: final stitched video about **35 MB**, individual clips roughly 12 MB; not a guaranteed maximum. Set an adjustable bounded output limit (initial engineering target 50 MB per clip) and memory/ephemeral storage capacity; measure real output sizes. Never return binary clips via Lambda's synchronous response.
- Existing image results and original jobs have **7-day S3 expiration / DynamoDB TTL**. For development, block scene generation when inputs have expired; before production, define job/result retention and configure `videos/` separately (existing S3 lifecycle only targets `uploads/` and `results/`). Avoid orphaned video states when TTL removes a parent job. Define cost/transfer budget and log safe metadata, not private image/video contents.

## Deployment gates (ASK USER TO PROCEED AFTER EACH)

**Stage 1 — docs (now):** update architecture, prompt, Google WIF, deployment, README and this plan. **No infrastructure, API or paid calls.**

**Stage 2 — Google API contract:** verify real provider endpoint for current WIF credentials, model ID, request JSON, accepted image inputs, async polling, output retrieval, durations, size limits, pricing and optional GCS requirement. Produce sample requests/tests with no paid submit. **Stop for approval.**

**Stage 3 — infrastructure:** Created separate `afl-app-dev-video` CloudFormation stack, mock-only worker, Standard Step Functions state machine, IAM roles and a DynamoDB global slot. Existing image resources untouched. Six offline tests pass. Google WIF permissions for a real new role, execution-level smoke testing, and real provider code are deliberately deferred. No paid submissions. **Stage complete; stop for approval before Stage 4.**

**Stage 4 — API and UI:** Added `POST /jobs/{jobId}/videos/{sceneId}`, `GET /jobs/{jobId}/videos`, `GET /jobs/{jobId}/videos/{sceneId}` to existing API with named single-execution-per-scene mock admission, scene-specific statuses, S3-reference readiness check and dev API-key routing. Added three independent mock Generate Video buttons, status polling, mock-only labels and disabled Assemble Final Video button. Preserved existing prompt previews/image controls. Fixed API ZIP packaging in the isolated Stage 4 deployment flow to include `video_prompts.py`; original broad `deploy.yml` packaging still needs correction before it is next used. No paid submits. **Stage complete; stop for Stage 5 approval.**

**Stage 5 — FFmpeg assembly:** separate assembly worker, endpoint/button gated on three completed MP4s, test with sample MP4s including incompatible media settings; no paid Google calls. **Stop for approval.**

**Stage 6 — controlled real generation:** after explicit user authorization enable one paid Scene 1 run, inspect operation, output, costs and controls. Then approve Scene 2/3 separately and finally assemble. Concurrency remains 1; parallel-three mode requires later explicit approval.

## Regression & rollback checks

Run existing Python and browser tests on each stage, add mocks for concurrency, duplicate click, unknown provider outcome, expired image, missing source, oversize output, partial success, cancellation/recovery, AWS S3 upload, signed playback and FFmpeg. Deploy via the current GitHub `main` -> `deploy` marker workflow; confirm CI deploy sequence, changesets and nonreplacement of existing resources. Each stage is reversible without deleting existing images, image state machine or jobs. Do not misstate GitHub merges as AWS deployments; record actual deployed commit and successful Actions run when deploying.

**Decisions deferred:** customer payments/auth, automated visual consistency or regeneration, automated final assembly, cross-solution multi-level hierarchy, increasing to three concurrent Google generations.

## Stage 4 implementation notes — mock-only UI

- Deployed Stage 4 API and UI via the isolated [GitHub Actions workflow](../.github/workflows/deploy-stage4.yml), keeping the existing image worker ZIP parameter and existing Replicate state machine unchanged.
- Three independent mock buttons only appear on a created job, and become available after their corresponding personalised reference image reaches `COMPLETED`.
- Each button explicitly confirms a **nonbillable mock** workflow; the existing free Preview Prompt actions are retained.
- Status API reads `videos.sceneN` independently from image `scenes.sceneN`. Only a genuine `COMPLETED` entry with exact S3 `videos/{jobId}/sceneN.mp4` key may expose playback; `MOCK_COMPLETED` never returns a video URL.
- `POST` accepts an idempotency key but uses a deterministic Standard Step Functions execution name per job + scene, so duplicate clicks cannot start new mock executions. For production, refine request ownership, retry and unknown-submission reconciliation before enabling paid calls.
- Global admission lock remains worker-enforced; API checks the slot before starting for friendlier busy feedback. An empty slot before submission is not a guaranteed reservation: the worker remains the source of truth.
- Assembly is deliberately disabled; Stage 5 will add real FFmpeg assembly and mock/test-compatible sample MP4 integration. The Stage 4 mock worker writes JSON markers, **not** MP4s, so assembly cannot be exercised with these markers.
