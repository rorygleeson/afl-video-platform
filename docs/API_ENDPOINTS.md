# HTTP API reference — developer pilot (8 November 2026)

**Base:** `https://ue36acwvrc.execute-api.ap-southeast-2.amazonaws.com/dev`

Every endpoint below uses the AWS API Gateway `x-api-key` header (except unauthenticated CORS `OPTIONS`). This key is **not customer authentication**. `{jobId}` is a 32-character lowercase hexadecimal identifier; `{scene}` is `scene1`, `scene2`, or `scene3`.

| Method | Path | Purpose / notable input |
|---|---|---|
| GET | `/scenarios` | List available scenarios and supported image pipeline |
| POST | `/uploads` | Create a 5-minute, size/type-limited **presigned S3 POST** for selfie. JSON `contentType`, `sizeBytes` |
| POST | `/jobs` | Verify uploaded selfie exists and create job. JSON `uploadKey`, `scenarioId`, `team`, `firstName`, `lastName`; **Idempotency-Key** header |
| GET | `/jobs/{jobId}` | Read overall image job status and per-scene states |
| POST | `/jobs/{jobId}/generate` | Explicitly start three paid Replicate image edits; duplicate job execution is guarded |
| GET | `/jobs/{jobId}/images` | Read reference image states and temporary **presigned S3 GET URLs** |
| GET | `/jobs/{jobId}/video/{scene}/preview` | Read personalised video prompt, commentary and duration from S3 templates; free |
| GET | `/jobs/{jobId}/video/scene1/preflight` | Free contract check using the actual Google worker, with no model call |
| GET | `/video/paid-google-auth-check` | Free AWS-to-Google WIF authentication check using the paid worker |
| GET | `/video/auth-check` | Historical, separate video-worker Google authentication diagnostic |
| POST | `/jobs/{jobId}/real-videos/{scene}` | **Billable** Google request for exactly one scene; JSON `{"confirmPaidScene":"scene1"}` (matching scene) and **Idempotency-Key** header; approved at most once per job/scene |
| GET | `/jobs/{jobId}/videos` | Read all three real/mock video states, paid availability and assembly-not-implemented flag |
| GET | `/jobs/{jobId}/videos/{scene}` | Read one scene status and any short-lived MP4 playback/download URLs |
| POST | `/jobs/{jobId}/videos/{scene}` | **Legacy/mock-only** video execution — NOT the paid Google video endpoint; requires **Idempotency-Key** |

`OPTIONS /{proxy+}` is API Gateway's CORS preflight, not a Lambda business endpoint. Paths not listed return a 404 from the API handler.

## Typical client workflow

1. GET scenarios → POST uploads → **browser POSTs file directly to presigned S3 URL** → POST jobs.
2. POST job generate → GET job and images until images complete.
3. GET per-scene video preview → user confirms → POST `real-videos/{scene}` **once** for that scene.
4. GET `videos` repeatedly, never re-POST to check progress; once `COMPLETED`, play or download the MP4 using a newly issued signed URL.

Video responses include `status` and `mode`. Current video state examples: `REQUESTED`, `PREPARED`, `SUBMISSION_UNKNOWN` (may be transient just before Google POST), `PROCESSING`, `COMPLETED`, `FAILED`, `REVIEW_REQUIRED`. Never infer from an uncertain or failed handoff that it is safe to issue another paid request.

## Replacement frontend contract

Business logic should remain in API and workers, not the test page. A replacement frontend needs to implement the endpoint flow and receive browser-safe signed S3 media URLs; it **must not** access DynamoDB, Step Functions, provider API keys or Google WIF directly.

**Important security gap:** before offering these routes to customers, replace the shared `x-api-key` with authenticated per-customer access checks, paid order entitlements and abuse/cost limits. A job ID on its own must not authorise reading another customer's selfie, generated images or MP4s.

**Source of truth:** `src/api/handler.py` route dispatcher, `infrastructure/app/backend.yaml` API Gateway proxy, and `frontend/index.html`. This is the endpoint inventory of code, not a claim of separate live HTTP smoke testing for every endpoint.
