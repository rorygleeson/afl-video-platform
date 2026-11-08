# Video generation implementation plan (approved design, not yet deployed)

**Date:** 2026-11-08. **Stage:** 3 — isolated mock-only AWS video stack created; no real model integration, API routes or frontend video controls. **Next gate:** Stage 4 approval for additive API/frontend integration. **Repository:** `main`; CD uses the existing `deploy` marker branch and CloudFormation. No feature branch required.

**Stage 2 official API research (8 November 2026):** [Google Omni Stage 2 API contract](GOOGLE_OMNI_STAGE2_API_CONTRACT.md). The model ID, documented JSON contract and S3-only feasibility are confirmed; authenticated model entitlement, quota and actual inline MP4 retrieval are untested.\n\n**Stage 3 implementation:** [video CloudFormation](../infrastructure/app/video.yaml), [mock-only worker](../src/video_mock_worker/handler.py), [offline unit tests](../tests/test_video_mock_stage3.py), [isolated deployment workflow](../.github/workflows/deploy-video.yml). AWS stack `afl-app-dev-video` was created by GitHub Actions; mock-only execution and playback are not yet end-to-end tested.

## Goal and current reality

Keep the **existing** selfie + team + first/last name -> three parallel Replicate personalised reference images -> private S3 workflow operational and unchanged. Existing Google Workload Identity Federation **auth check** and three **free S3 prompt preview** buttons are deployed. **A separate mock-only video state machine now exists, but no paid video generation, real video storage, FFmpeg assembly or frontend playback has been implemented.**

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

**Stage 4 — API and UI:** additive routes, video statuses, three independently enabled buttons, preview preserved, playback, retries/refresh safe. Regression test image flow. Note existing packaging bug: deploy script copies `src/api/video_prompts.py` but zips only `handler.py`; fix and smoke-test import **before** updating existing API Lambda. No paid submits. **Stop for approval.**

**Stage 5 — FFmpeg assembly:** separate assembly worker, endpoint/button gated on three completed MP4s, test with sample MP4s including incompatible media settings; no paid Google calls. **Stop for approval.**

**Stage 6 — controlled real generation:** after explicit user authorization enable one paid Scene 1 run, inspect operation, output, costs and controls. Then approve Scene 2/3 separately and finally assemble. Concurrency remains 1; parallel-three mode requires later explicit approval.

## Regression & rollback checks

Run existing Python and browser tests on each stage, add mocks for concurrency, duplicate click, unknown provider outcome, expired image, missing source, oversize output, partial success, cancellation/recovery, AWS S3 upload, signed playback and FFmpeg. Deploy via the current GitHub `main` -> `deploy` marker workflow; confirm CI deploy sequence, changesets and nonreplacement of existing resources. Each stage is reversible without deleting existing images, image state machine or jobs. Do not misstate GitHub merges as AWS deployments; record actual deployed commit and successful Actions run when deploying.

**Decisions deferred:** customer payments/auth, automated visual consistency or regeneration, automated final assembly, cross-solution multi-level hierarchy, increasing to three concurrent Google generations.
