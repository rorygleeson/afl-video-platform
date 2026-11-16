# Paid MP4 preflight code audit — 8 November 2026

> **DOCUMENT STATUS — HISTORICAL / STAGE-SPECIFIC (reviewed 16 November 2026).** This records the design, release or resource assumptions at its original date; some model providers, payment gates, retries, waits, deployment commands and UI behaviour have since changed. **Do not use this file as today's operational contract or run its historical deployment instructions without a new live-state review.** Current references: [documentation index](README.md), [HTTP API](API_ENDPOINTS.md), [retry and timeout contract](RETRIES_AND_TIMEOUTS.md), [job statuses](JOB_STATUSES.md), [architecture](ARCHITECTURE.md), and [resilience audit](RESILIENCE_AUDIT_2026-11-16.md).

Scope: `frontend/index.html`, `src/api/handler.py`, `src/api/video_prompts.py`, `src/google_video_worker/handler.py`, `infrastructure/app/{backend,google-video,video}.yaml`, the paid and standard deployment workflows, versioned scenario prompts/configuration, and regression tests. This is a developer-pilot review, **not** a production security certification.

## Required behaviours

- Three separate paid actions: Scene 1 (8 s), Scene 2 (8 s), Scene 3 (10 s).
- Clicking one button must never submit another scene.
- Only the active app Job ID is used. No job IDs in code, environment variables, or CloudFormation.
- Each scene requires a confirmation and `confirmPaidScene` matching its requested scene, plus a valid idempotency key.
- One irreversible conditional DynamoDB approval per job/scene before Step Functions begins; uncertain handoffs must not be retried automatically.
- A single DynamoDB global admission slot guards the actual Google submission. Another scene cannot be approved while a different scene from the same job is still pending/processing.
- Reference images must exist and be complete; prompts are read from private versioned S3 objects.
- Google worker independently verifies the corresponding DynamoDB approval for **every** paid operation, not just API calls.
- Model payload uses Interactions API, background asynchronous, reference image and personalised scene prompt, portrait 720p inline MP4.
- MP4 bytes are validated and stored privately under `videos/<jobId>/sceneN.mp4`, and users receive short-lived S3 download/playback URLs.
- No workflow automatically retries an ambiguous billable POST. A failed Step Functions execution is surfaced as `REVIEW_REQUIRED` rather than an implicit permission to regenerate.

## Issues fixed in this audit

1. **Second-scene race:** API and UI now block a second paid scene while another is not terminal.
2. **Invisible orchestration failure:** API can inspect the exact Step Functions execution and reports `REVIEW_REQUIRED` for FAILED/TIMED_OUT/ABORTED executions, with no paid retry.
3. **Pre-charge lock leak:** when prompt/image validation fails before the Google POST, worker records FAILED and releases its global slot. A charged or ambiguous submission still retains the slot until reconciled.
4. **Unstable player:** read-only status polling no longer tears down/reloads the playing MP4 because presigned URLs refresh.
5. **Polling failure recovery:** failed read-only GET status polls retry without submitting additional paid POSTs.
6. **Frontend race:** a completed paid POST now queues a fresh status request when an older status GET is already active.
7. **Browser double-click:** clicked scene is hidden and the other paid controls are hidden while the server confirms the reservation.
8. **Deployment drift:** Google worker changes trigger the reviewed Stage 5C paid workflow; live `auth_check` is executed without requesting a video.
9. **Test coverage:** verified per-scene consent/locking, no cross-scene POST, worker precharge failure, status review state, and mock-vs-real separation.

## Known limitations — important for first pilot

- **No billable end-to-end test yet.** Google authentication and model configuration can pass while actual video generation fails due to model quota, provider errors, or output constraints. This must be assessed from the very first explicitly approved Scene 1 attempt.
- **Inline 720p video delivery:** Google's documentation recommends Google Cloud Storage URI output for larger videos (particularly over 4 MB). Current private-S3 architecture uses inline video; worker enforces a 50-MB MP4 and 75-MB JSON response cap. A large result can require manual recovery after a charge. GCS integration is a separate architecture task, not silently available.
- **Developer-only auth:** API Gateway `x-api-key` is a shared test key, **not** per-customer identity, checkout, or billing authorisation. Do not make this paid API public for customers before dedicated authentication, per-user entitlements, quotas/budget limits and payment verification.
- **Ambiguous submission:** a timeout after a Google POST leaves state `SUBMISSION_UNKNOWN`, keeps admission reserved and prevents an automatic new attempt. Operator reconciliation against Google interactions/billing is required. An indefinite fail-closed lock is intentional rather than risking duplicate charges.
- **TTL:** source reference images and jobs currently have seven-day retention; previously generated MP4s are stored separately. The source image must still exist when the user starts a scene.
- **No final three-clip assembly yet.** All three independent MP4 outputs are supported; stitching the final video remains separate.
- **No verified face/ball/commentary quality:** content quality and native audio are only measurable after the first successful provider video.

## Deployment/go-no-go

The application should only be described as ready for a **single controlled pilot** after the latest push has both full CI and all live deployment workflows green. This audit did not trigger Google paid generation. Test **Scene 1 only** first; do not press Scenes 2 or 3 until the first result and its charges are understood.

Emergency stop: set `PaidVideoEnabled=false` on **both** application CloudFormation stacks (`afl-app-dev-backend` and `afl-app-dev-google-video`); the S3 scenario `videoGenerationEnabled=false` is an additional separate fail-closed guard. Never assume hiding browser buttons alone disables billable endpoints.
